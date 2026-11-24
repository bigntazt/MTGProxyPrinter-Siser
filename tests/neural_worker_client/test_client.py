"""Standalone client integration contracts; load the module without app __init__."""
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('m34_client_under_test',ROOT/'mtg_proxy_printer/neural_worker_client.py')
c=importlib.util.module_from_spec(SPEC);sys.modules[SPEC.name]=c;SPEC.loader.exec_module(c)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.bundle=self.root/'bundle';self.bundle.mkdir()
        self.parent=self.root/'private';self.parent.mkdir()
        self.neighbor=self.parent/'preserve.txt';self.neighbor.write_bytes(b'neighbor')
        (self.bundle/'worker.py').write_bytes(Path(__file__).with_name('worker_fixture.py').read_bytes())
        for name in ('protocol.py','backend.py','reference-model.json'): (self.bundle/name).write_bytes(b'test-only bundle bytes')
        self.checkpoint=self.root/'checkpoint';self.checkpoint.write_bytes(b'unused by inert fixture')
        self.config=c.NeuralWorkerConfig(Path(sys.executable),self.bundle/'worker.py',self.checkpoint,c.REFERENCE_SHA256,
                                        'nomos_compact_2x','cuda:0',self.parent,startup_timeout=3,inference_timeout=2,
                                        shutdown_timeout=1,terminate_timeout=.2,kill_timeout=2)
        self.session=None

    def make(self,scenario=None,config=None,event=None):
        (self.bundle/'scenario.json').write_text(json.dumps(scenario or {}))
        self.session=c.NeuralWorkerSession(config or self.config,event)
        self.addCleanup(self.session.close)
        return self.session

    def cleaned(self,session):
        report=session.cleanup_report
        self.assertIsNotNone(report)
        self.assertTrue(report.reaped);self.assertTrue(report.threads_stopped)
        self.assertTrue(report.temporary_tree_removed);self.assertEqual(report.limitations,())
        self.assertFalse(any(t.is_alive() for t in session._readers))
        if session._writer is not None:self.assertFalse(session._writer.is_alive())
        self.assertEqual(self.neighbor.read_bytes(),b'neighbor')
        self.assertEqual(self.checkpoint.read_bytes(),b'unused by inert fixture')
        self.assertEqual(list(self.parent.iterdir()),[self.neighbor])

    def test_construction_two_jobs_one_process_immutable_and_normal_close(self):
        session=self.make()
        self.assertIsNone(session._process);self.assertEqual(list(self.parent.iterdir()),[self.neighbor])
        ready=session.start();pid=session._process.pid
        self.assertIs(session.start(),ready)
        results=[session.infer(b'abc',1,1),session.infer(b'abcdef',2,1)]
        self.assertEqual(session._process.pid,pid)
        for result in results:
            self.assertEqual(result.fingerprint,ready.fingerprint);self.assertEqual(result.session_token,ready.session_token)
            self.assertEqual(c._hash(result.rgb_bytes),result.output_sha256)
            self.assertEqual(result.provenance_json,ready.provenance_json)
            with self.assertRaises(FrozenInstanceError):result.width=0
        with self.assertRaises(FrozenInstanceError):ready.fingerprint='bad'
        copy=json.loads(ready.provenance_json);copy['model']['scale']=99
        self.assertEqual(json.loads(ready.provenance_json)['model']['scale'],2)
        self.assertEqual(list((session._session/'input').iterdir()),[])
        self.assertEqual(list((session._session/'output').iterdir()),[])
        session.close();session.close();self.cleaned(session)
        self.assertEqual(session.cleanup_report.returncode,0)
        requests=[json.loads(x) for x in (self.bundle/'requests.jsonl').read_text().splitlines()]
        ids=[r['id'] for r in requests]
        self.assertEqual(len(ids),3);self.assertEqual(len(set(ids)),3)
        self.assertTrue(all(re.fullmatch('[0-9a-f]{32}',v) for v in ids))

    def test_config_rejects_invalid_deadlines_and_configuration(self):
        for value in (True,0,-1,float('inf'),float('nan'),10**1000,'1',None):
            for field in ('startup_timeout','inference_timeout','shutdown_timeout','terminate_timeout','kill_timeout'):
                with self.subTest(value=value,field=field),self.assertRaises(c.NeuralWorkerError):replace(self.config,**{field:value})
        for changes in ({'device':'cpu'},{'expected_sha256':'0'*64},{'model_id':'other'},{'private_parent':Path('relative')}):
            with self.assertRaises(c.NeuralWorkerError):replace(self.config,**changes)

    def test_invalid_input_does_not_submit_and_remains_usable(self):
        session=self.make();session.start()
        for data,w,h in ((bytearray(b'abc'),1,1),(b'abc',True,1),(b'abc',1.0,1),(b'abc',0,1),
                         (b'ab',1,1),(b'abcd',1,1),(b'abc',4194305,1)):
            with self.subTest(data=data,w=w,h=h),self.assertRaises(c.NeuralWorkerError) as raised:session.infer(data,w,h)
            self.assertEqual(raised.exception.reason,'invalid_input')
        self.assertFalse((self.bundle/'requests.jsonl').exists())
        session.infer(b'abc',1,1);session.close();self.cleaned(session)

    def test_request_budget_reserves_shutdown(self):
        session=self.make({'max_requests':2});session.start();session.infer(b'abc',1,1)
        with self.assertRaises(c.NeuralWorkerError) as raised:session.infer(b'abc',1,1)
        self.assertEqual(raised.exception.reason,'request_budget')
        session.close();self.cleaned(session)
        self.assertEqual(len((self.bundle/'requests.jsonl').read_text().splitlines()),2)

    def test_ready_identity_capabilities_and_provenance(self):
        changes=[(['protocol'],True),(['type'],'result'),(['session'],'BAD'),
                 (['fingerprint'],'0'*64),(['provenance','model','sha256'],'0'*64),
                 (['provenance','model','bytes'],True),(['provenance','model','architecture'],'SPAN'),
                 (['provenance','model','scale'],4),(['provenance','model','input_channels'],4),
                 (['provenance','source_sha256','worker.py'],'0'*64),(['provenance','runtime','device'],'cpu'),
                 (['provenance','runtime','settings','precision'],'float16'),
                 (['provenance','runtime','settings','matmul_tf32'],True),
                 (['provenance','runtime','settings','passes'],True),(['provenance','runtime','packages'],{}),
                 (['capabilities','cancel'],True),(['capabilities','sequential'],False),
                 (['capabilities','max_requests'],True),(['capabilities','max_input_pixels'],4194305),
                 (['capabilities','max_line_bytes'],999999)]
        for keys,value in changes:
            with self.subTest(keys=keys):
                session=self.make({'ready_changes':[(keys,value)],'rehash':keys!=['fingerprint']})
                with self.assertRaises(c.NeuralWorkerError) as raised:session.start()
                self.assertEqual(raised.exception.reason,'protocol_error');self.cleaned(session);session.close()

    def test_result_identity_dimensions_digest_and_path(self):
        for keys,value in [(['session'],'b'*32),(['id'],'bad'),(['fingerprint'],'0'*64),(['input','sha256'],'0'*64),
                           (['input','width'],True),(['output','width'],4),(['output','height'],False),
                           (['output','scale'],4),(['output','format'],'RGBA8'),(['output','bytes'],99),
                           (['output','file'],'../neighbor'),(['output','sha256'],'0'*64)]:
            with self.subTest(keys=keys):
                session=self.make({'result_changes':[(keys,value)]});session.start()
                with self.assertRaises(c.NeuralWorkerError) as raised:session.infer(b'abc',1,1)
                self.assertEqual(raised.exception.reason,'protocol_error');self.cleaned(session);session.close()

    def test_missing_partial_and_corrupt_result_files(self):
        for mode in ('missing_file','partial_file','corrupt_file'):
            with self.subTest(mode=mode):
                session=self.make({'mode':mode});session.start()
                with self.assertRaises(c.NeuralWorkerError) as raised:session.infer(b'abc',1,1)
                self.assertIn(raised.exception.reason,('protocol_error','result_integrity'))
                self.cleaned(session);session.close()

    def test_stdout_bad_traffic_and_eof(self):
        for mode in ('partial_eof','malformed','duplicate_keys','nonfinite','utf16','utf32','invalid_utf8','oversized','early_exit'):
            with self.subTest(mode=mode):
                session=self.make({'mode':mode})
                with self.assertRaises(c.NeuralWorkerError) as raised:session.start()
                self.assertIn(raised.exception.reason,('protocol_error','worker_unavailable'))
                self.cleaned(session);session.close()

    def test_overflow_is_controlled_even_when_first_ready_wins_race(self):
        session=self.make({'mode':'overflow'})
        with self.assertRaises(c.NeuralWorkerError) as raised:
            session.start()
            session.infer(b'abc',1,1)
        self.assertEqual(raised.exception.reason,'protocol_error')
        self.cleaned(session);session.close()

    def test_stderr_flood_is_drained_and_bounded(self):
        session=self.make({'mode':'stderr_flood'});session.start();session.infer(b'abc',1,1);session.close()
        self.assertLessEqual(len(session.diagnostics),65536);self.assertTrue(session.diagnostics.endswith('END-TAIL'))
        self.cleaned(session)

    def test_absolute_startup_timeout_despite_partial_data_or_diagnostics(self):
        for mode in ('startup_silent','startup_partial','startup_stderr'):
            with self.subTest(mode=mode):
                session=self.make({'mode':mode},replace(self.config,startup_timeout=.25))
                started=time.monotonic()
                with self.assertRaises(c.NeuralWorkerError) as raised:session.start()
                self.assertEqual(raised.exception.reason,'timeout');self.assertLess(time.monotonic()-started,4)
                self.cleaned(session);session.close()

    def test_job_timeout_file_without_record_and_eof(self):
        for mode,reason in (('job_hang','timeout'),('file_only','timeout'),('job_eof','worker_unavailable')):
            with self.subTest(mode=mode):
                session=self.make({'mode':mode},replace(self.config,inference_timeout=.2));session.start()
                with self.assertRaises(c.NeuralWorkerError) as raised:session.infer(b'abc',1,1)
                self.assertEqual(raised.exception.reason,reason);self.cleaned(session);session.close()

    def test_cancellation_during_startup_and_inference(self):
        for active in (False,True):
            with self.subTest(active=active):
                event=threading.Event();session=self.make({'mode':'job_hang' if active else 'startup_silent'},event=event)
                if active:session.start()
                timer=threading.Timer(.15,event.set);timer.start()
                try:
                    with self.assertRaises(c.NeuralWorkerCancelled):
                        if active:session.infer(b'abc',1,1)
                        else:session.start()
                finally:timer.join()
                self.cleaned(session);session.close()
                with self.assertRaises(c.NeuralWorkerError):session.start()

    def test_recoverable_request_error_then_valid_job(self):
        session=self.make({'mode':'request_error'});session.start()
        with self.assertRaises(c.NeuralWorkerError) as raised:session.infer(b'abc',1,1)
        self.assertEqual(raised.exception.reason,'worker_rejected')
        session.infer(b'abc',1,1);session.close();self.cleaned(session)

    def test_fatal_and_duplicate_result_cannot_satisfy_next_job(self):
        session=self.make({'mode':'fatal'});session.start()
        with self.assertRaises(c.NeuralWorkerError) as raised:session.infer(b'abc',1,1)
        self.assertEqual(raised.exception.reason,'worker_unavailable');self.cleaned(session);session.close()
        session=self.make({'mode':'duplicate_result'});session.start()
        try:session.infer(b'abc',1,1)
        except c.NeuralWorkerError:pass
        with self.assertRaises(c.NeuralWorkerError):session.infer(b'abc',1,1)
        session.close();self.cleaned(session)

    def test_partial_startup_repeated_close_and_owner_thread(self):
        session=self.make(config=replace(self.config,interpreter=self.root/'missing.exe'))
        with self.assertRaises(c.NeuralWorkerError):session.start()
        self.cleaned(session);session.close();session.close()
        session=self.make();errors=[]
        def wrong_thread():
            try:session.start()
            except c.NeuralWorkerError as error:errors.append(error.reason)
        thread=threading.Thread(target=wrong_thread);thread.start();thread.join()
        self.assertEqual(errors,['wrong_thread']);self.assertIsNone(session._process)
        session.close();self.cleaned(session)

    def test_bad_shutdown_and_escalated_kill_preserve_primary(self):
        session=self.make({'mode':'bad_bye'});session.start()
        with self.assertRaises(c.NeuralWorkerError) as raised:session.close()
        self.assertEqual(raised.exception.reason,'protocol_error');self.cleaned(session)
        session=self.make({'mode':'shutdown_hang'},replace(self.config,shutdown_timeout=.15,terminate_timeout=.1));session.start()
        with patch.object(session._process,'terminate'),patch.object(session._process,'kill',wraps=session._process.kill) as kill:
            with self.assertRaises(c.NeuralWorkerError) as raised:session.close()
        self.assertEqual(raised.exception.reason,'timeout');kill.assert_called_once();self.cleaned(session)

    def test_context_manager_preserves_body_exception(self):
        session=self.make({'mode':'bad_bye'})
        with self.assertRaisesRegex(RuntimeError,'primary') as raised:
            with session:raise RuntimeError('primary')
        self.assertTrue(any('Neural worker close' in note for note in raised.exception.__notes__))
        self.cleaned(session)

    def test_blocked_request_write_has_a_deadline_and_writer_is_joined(self):
        session=self.make({'mode':'no_stdin'},replace(self.config,inference_timeout=.2));session.start()
        def blocked(data):
            while session._process.poll() is None:time.sleep(.01)
            raise BrokenPipeError('child terminated')
        with patch.object(session._process.stdin,'write',side_effect=blocked):
            with self.assertRaises(c.NeuralWorkerError) as raised:session.infer(b'abc',1,1)
        self.assertEqual(raised.exception.reason,'timeout');self.cleaned(session)

    def test_host_module_has_no_optional_imports(self):
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('import_probe.py')),
                               str(ROOT/'mtg_proxy_printer/neural_worker_client.py')],shell=False,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode());self.assertEqual(result.stdout.strip(),b'host-only')

    def test_cancellation_at_validated_result_boundary_discards_success(self):
        event=threading.Event();session=self.make(event=event);session.start()
        original=c._hash
        def hash_and_cancel(data):
            if data==b'\x10\x20\x30'*4:event.set()
            return original(data)
        with patch.object(c,'_hash',side_effect=hash_and_cancel):
            with self.assertRaises(c.NeuralWorkerCancelled):session.infer(b'abc',1,1)
        self.cleaned(session)

    def test_cleanup_limitation_keeps_cancellation_primary_and_can_retry_close(self):
        event=threading.Event();session=self.make({'mode':'job_hang'},event=event);session.start();event.set()
        with patch.object(c.shutil,'rmtree',side_effect=PermissionError('injected cleanup denial')):
            with self.assertRaises(c.NeuralWorkerCancelled) as raised:session.infer(b'abc',1,1)
        report=raised.exception.cleanup_report
        self.assertTrue(report.reaped and report.threads_stopped)
        self.assertFalse(report.temporary_tree_removed);self.assertTrue(report.limitations)
        session.close();self.cleaned(session)

    def test_partial_reader_start_failure_reaps_started_child(self):
        session=self.make()
        with patch.object(c.threading.Thread,'start',side_effect=RuntimeError('injected thread start failure')):
            with self.assertRaises(c.NeuralWorkerError) as raised:session.start()
        self.assertEqual(raised.exception.reason,'worker_unavailable');self.cleaned(session)

    def test_validated_file_cleanup_failure_is_distinct(self):
        session=self.make();session.start()
        original=Path.unlink
        def reject_job_file(path,*args,**kwargs):
            if path.suffix=='.rgb8':raise PermissionError('injected job cleanup failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',reject_job_file):
            with self.assertRaises(c.NeuralWorkerError) as raised:session.infer(b'abc',1,1)
        self.assertEqual(raised.exception.reason,'cleanup_failed')
        session.close();self.cleaned(session)

    def test_redirected_output_directory_is_not_accepted(self):
        session=self.make();session.start()
        output_directory=session._session/'output'
        original=Path.resolve
        def redirected(path,*args,**kwargs):
            if path==output_directory:return self.parent/'outside'
            return original(path,*args,**kwargs)
        with patch.object(Path,'resolve',redirected):
            with self.assertRaises(c.NeuralWorkerError) as raised:session.infer(b'abc',1,1)
        self.assertEqual(raised.exception.reason,'protocol_error');self.cleaned(session)


if __name__=='__main__':unittest.main()

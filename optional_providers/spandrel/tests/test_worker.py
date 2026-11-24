"""Standalone contracts. No Torch, Spandrel, Qt or application imports."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backend
import protocol as p
import worker


class InertBackend:
    initialized = 0
    calls = 0
    facts = {"test_only": True}
    load_seconds = 0

    def __init__(self, payload, reference, device):
        type(self).initialized += 1
        self.payload = payload
        print("test initialization diagnostic")

    def infer(self, samples, width, height):
        type(self).calls += 1
        return b"\x01\x02\x03" * (width * height * 4), {"inference_seconds": 0}


def infer(identifier="one", width=1, height=1, samples=b"abc"):
    return {"protocol": 1, "type": "infer", "id": identifier, "width": width,
            "height": height, "sha256": p.sha256(samples)}


class Scalar:
    def __init__(self, value): self.value = value
    def item(self): return self.value
    def all(self): return self


class Tensor:
    shape = (1, 3, 2, 2)
    device = "cuda:0"
    dtype = "torch.float32"
    finite = True
    low, high = 0, 1
    def isfinite(self): return Scalar(self.finite)
    def amin(self): return Scalar(self.low)
    def amax(self): return Scalar(self.high)


class ProtocolTests(unittest.TestCase):
    def test_strict_json_version_and_types(self):
        for line in (b"[]", b"null", b"{", b'{}', b'{"protocol":true}', b'{"protocol":1.0}',
                     b'{"protocol":2}', b'{"protocol":1,"protocol":1}',
                     b'{"protocol":1,"value":NaN}', b'\xff'):
            with self.subTest(line=line), self.assertRaises(p.Rejected): p.parse_line(line)

    def test_utf16_be_request_is_invalid_json(self):
        with self.assertRaises(p.Rejected) as raised:
            p.parse_line('{"protocol":1,"type":"shutdown","id":"stop"}'.encode('utf-16-be'))
        self.assertEqual(raised.exception.code, 'invalid_json')

    def test_utf32_be_request_is_invalid_json(self):
        with self.assertRaises(p.Rejected) as raised:
            p.parse_line('{"protocol":1,"type":"shutdown","id":"stop"}'.encode('utf-32-be'))
        self.assertEqual(raised.exception.code, 'invalid_json')

    def test_id_grammar(self):
        for value in (None, True, 1, "", "../x", "/x", "a.b", "-a", "x"*65, "é"):
            with self.subTest(value=value), self.assertRaises(p.Rejected): p.request_id({"id": value})
        self.assertEqual(p.request_id({"id": "CON"}), "CON")
        self.assertEqual(p.sample_name("CON"), "job-CON.rgb8")

    def test_positive_integer_dimensions_and_limit(self):
        for value in (True, 1.0, "1", None, 0, -1):
            with self.subTest(value=value), self.assertRaises(p.Rejected): p.validate_infer(infer(width=value))
        with self.assertRaisesRegex(p.Rejected, "limit"): p.validate_infer(infer(width=p.MAX_PIXELS+1))
        self.assertEqual(p.validate_infer(infer(width=p.MAX_PIXELS)), (p.MAX_PIXELS,1))

    def test_exact_fields_and_digest(self):
        for field, value in (("sha256", "0"*63), ("sha256", "A"*64), ("destination", "/outside")):
            record = infer(); record[field] = value
            with self.assertRaises(p.Rejected): p.validate_infer(record)

    def test_input_length_digest_and_same_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"input"; path.mkdir(); file = path/p.sample_name("one")
            for data, digest, code in ((b"ab", p.sha256(b"ab"), "input_length"),
                                       (b"abcd",p.sha256(b"abcd"),"input_length"),
                                       (b"abc",p.sha256(b"xxx"),"input_digest")):
                file.write_bytes(data)
                with self.assertRaises(p.Rejected) as raised: p.read_samples(directory,"one",1,1,digest)
                self.assertEqual(raised.exception.code, code)
            file.write_bytes(b"abc")
            payload = p.read_samples(directory,"one",1,1,p.sha256(b"abc"))
            file.write_bytes(b"new")
            self.assertEqual(payload,b"abc")

    def test_checkpoint_pin_and_read_once_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"weights"; path.write_bytes(b"verified")
            reference={"bytes":8,"sha256":p.sha256(b"verified")}
            with self.assertRaises(backend.Fatal): worker.load_checkpoint(path,"0"*64,reference)
            path.write_bytes(b"tampered")
            with self.assertRaises(backend.Fatal): worker.load_checkpoint(path,reference["sha256"],reference)
            path.write_bytes(b"verified")
            original = Path.open; calls=[]
            def opening(file,*args,**kwargs):
                calls.append(file); return original(file,*args,**kwargs)
            with patch.object(Path,'open',opening): payload=worker.load_checkpoint(path,reference['sha256'],reference)
            self.assertEqual(calls,[path])
            path.write_bytes(b"replaced")
            fake=InertBackend(payload,reference,"cuda:0")
            self.assertIs(fake.payload,payload)
            self.assertEqual(fake.payload,b"verified")

    def test_descriptor_contract(self):
        ref={"architecture":"Compact","scale":2}
        descriptor=SimpleNamespace(architecture=SimpleNamespace(id="Compact"),scale=2,input_channels=3,output_channels=3)
        backend.validate_descriptor(descriptor,ref)
        for key,value in (("scale",4),("input_channels",4),("output_channels",1),
                          ("architecture",SimpleNamespace(id="SPAN"))):
            changed=SimpleNamespace(**vars(descriptor)); setattr(changed,key,value)
            with self.subTest(key=key), self.assertRaises(backend.Fatal): backend.validate_descriptor(changed,ref)

    def test_tensor_validation(self):
        backend.validate_tensor(Tensor(),(1,3,2,2),"cuda:0")
        for key,value,code in (("shape",(1,3,1,2),"tensor_shape"),("device","cpu","tensor_type"),
                               ("dtype","torch.float16","tensor_type"),("finite",False,"tensor_nonfinite"),
                               ("low",-.01,"tensor_range"),("high",1.01,"tensor_range")):
            tensor=Tensor(); setattr(tensor,key,value)
            with self.subTest(key=key), self.assertRaises(backend.Fatal) as raised:
                backend.validate_tensor(tensor,(1,3,2,2),"cuda:0")
            self.assertEqual(raised.exception.code,code)

    def test_one_quantization_nearest_ties_even(self):
        class Quantizer:
            values=[0,.5,1, .5/255,1.5/255,2.5/255]
            rounds=0
            def mul(self,n): self.values=[v*n for v in self.values]; return self
            def round(self): self.rounds+=1; self.values=[round(v) for v in self.values]; return self
            def to(self,**kwargs): return self
            def squeeze(self,*args): return self
            def permute(self,*args): return self
            def contiguous(self): return self
            def cpu(self): return self
            def numpy(self): return self
            def tobytes(self): return bytes(self.values)
        tensor=Quantizer()
        self.assertEqual(backend.quantize_native(tensor,SimpleNamespace(uint8='uint8')),bytes([0,128,255,0,2,2]))
        self.assertEqual(tensor.rounds,1)

    def test_publication_no_replace_or_partial_success(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/"output"; out.mkdir()
            self.assertEqual(worker.publish(directory,"one",b"first"),"output/job-one.rgb8")
            with self.assertRaises(OSError): worker.publish(directory,"one",b"new")
            self.assertEqual((out/'job-one.rgb8').read_bytes(),b"first")
            self.assertFalse((out/'job-one.rgb8.part').exists())
            with patch.object(worker.os,'link',side_effect=OSError('publication failure')):
                with self.assertRaises(OSError): worker.publish(directory,"two",b"new")
            self.assertFalse((out/'job-two.rgb8').exists())
            self.assertFalse((out/'job-two.rgb8.part').exists())

    def session(self,records,factory=InertBackend,files=True):
        directory=tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        session=Path(directory.name)/'session'
        config=SimpleNamespace(checkpoint='unused',expected_sha256='unused',model_id='nomos_compact_2x',device='cuda:0',session_dir=str(session))
        class Input:
            def __init__(self): self.lines=iter(records)
            def readline(self,size):
                # Input is published after ready, as a real host would do.
                if files:
                    for identifier in ('one','two'):
                        path=session/'input'/p.sample_name(identifier)
                        if not path.exists(): path.write_bytes(b'abc')
                try:
                    record=next(self.lines)
                    return record if isinstance(record,bytes) else p.canonical(record)+b'\n'
                except StopIteration: return b''
        out,diag=io.StringIO(),io.StringIO()
        with patch.object(worker,'load_checkpoint',return_value=b'verified'):
            code=worker.serve(config,Input(),out,diag,factory)
        return code,[json.loads(line) for line in out.getvalue().splitlines()],diag.getvalue(),session

    def test_two_sequential_requests_retain_one_backend_and_fingerprint(self):
        InertBackend.initialized=InertBackend.calls=0
        code,records,diag,session=self.session([infer(),infer('two'),{'protocol':1,'type':'shutdown','id':'stop'}])
        self.assertEqual(code,0); self.assertEqual(InertBackend.initialized,1); self.assertEqual(InertBackend.calls,2)
        self.assertEqual([r['type'] for r in records],['ready','result','result','bye'])
        self.assertIn('diagnostic',diag)
        for record in records[1:3]:
            self.assertEqual(record['fingerprint'],records[0]['fingerprint'])
            self.assertEqual(record['session'],records[0]['session'])
            output=record['output']; data=(session/output['file']).read_bytes()
            self.assertEqual(len(data),output['bytes']); self.assertEqual(p.sha256(data),output['sha256'])
            self.assertEqual((output['width'],output['height']),(2,2))

    def test_request_errors_do_not_run_inference(self):
        bad=infer('two',width=p.MAX_PIXELS+1)
        InertBackend.calls=0
        code,records,_,_=self.session([b'not json\n',infer(width=0),infer(),bad,{'protocol':1,'type':'cancel','id':'cancel'}])
        self.assertEqual(code,0)
        self.assertEqual([r['type'] for r in records],['ready','request_error','request_error','request_error','request_error','request_error','bye'])
        self.assertEqual(records[3]['code'],'duplicate_id'); self.assertEqual(InertBackend.calls,0)

    def test_oversize_rejected_without_opening_input(self):
        with patch.object(worker,'read_samples',side_effect=AssertionError('must not read')):
            code,records,_,_=self.session([infer(width=p.MAX_PIXELS+1)],files=False)
        self.assertEqual(code,0); self.assertEqual(records[1]['code'],'input_limit')

    def test_fatal_inference_does_not_emit_success(self):
        class Fails(InertBackend):
            def infer(self,*args): raise backend.Fatal('cuda_oom','injected')
        code,records,_,session=self.session([infer()],Fails)
        self.assertEqual(code,1); self.assertEqual([r['type'] for r in records],['ready','fatal'])
        self.assertEqual(records[-1]['code'],'cuda_oom'); self.assertEqual(list((session/'output').iterdir()),[])

    def test_wrong_output_length_is_fatal(self):
        class Fails(InertBackend):
            def infer(self,*args): return b'wrong',{}
        code,records,_,_=self.session([infer()],Fails)
        self.assertEqual(code,1); self.assertEqual(records[-1]['code'],'output_length')

    def test_eof_and_oversize_line(self):
        bye=self.session([])[1][-1]
        self.assertEqual((bye['protocol'],bye['type'],bye['reason']),(1,'bye','eof'))
        code,records,_,_=self.session([b'x'*(p.MAX_LINE_BYTES+1)])
        self.assertEqual(code,1); self.assertEqual(records[-1]['code'],'message_limit')

    def test_rejected_checkpoint_never_initializes_backend(self):
        config=SimpleNamespace(model_id='nomos_compact_2x',device='cuda:0',checkpoint='missing',expected_sha256='0'*64)
        factory=unittest.mock.Mock()
        out=io.StringIO()
        self.assertEqual(worker.serve(config,io.BytesIO(),out,io.StringIO(),factory),1)
        factory.assert_not_called(); self.assertEqual(json.loads(out.getvalue())['code'],'checkpoint_pin')

    def test_fingerprint_stable_and_material_change(self):
        ref=json.loads((ROOT/'reference-model.json').read_text())
        a,digest=worker.fingerprint(ref,{'device':'cuda:0'})
        self.assertEqual((a,digest),worker.fingerprint(ref,{'device':'cuda:0'}))
        self.assertNotEqual(digest,worker.fingerprint(ref,{'device':'cuda:1'})[1])
        self.assertEqual(p.sha256(p.canonical(a)),digest)
        for forbidden in ('pid','session_dir','timings','free_memory'): self.assertNotIn(forbidden,a)

    def test_real_process_protocol_and_reaping_without_optional_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            command=[sys.executable,'-B',str(Path(__file__).with_name('process_driver.py')),str(Path(directory)/'session')]
            child=subprocess.Popen(command,shell=False,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                out,err=child.communicate(p.canonical({'protocol':1,'type':'shutdown','id':'stop'})+b'\n',timeout=10)
            except subprocess.TimeoutExpired:
                child.kill(); child.communicate(timeout=5); self.fail('Worker did not exit')
            self.assertEqual(child.returncode,0)
            records=[json.loads(line) for line in out.splitlines()]
            self.assertEqual([r['type'] for r in records],['ready','bye'])
            self.assertIn(b'diagnostic',err)

    def test_verified_bytes_reach_safetensors_and_state_dict_loader(self):
        from types import ModuleType
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'weights'; path.write_bytes(b'verified')
            reference={'bytes':8,'sha256':p.sha256(b'verified')}
            payload=worker.load_checkpoint(path,reference['sha256'],reference)
            path.write_bytes(b'replaced')
            observed=[]; state={'sentinel':object()}
            torch=ModuleType('torch')
            torch.cuda=SimpleNamespace(is_available=lambda:True)
            torch.manual_seed=lambda seed:None
            torch.set_num_threads=lambda count:None
            torch.backends=SimpleNamespace(cudnn=SimpleNamespace(),cuda=SimpleNamespace(matmul=SimpleNamespace()))
            safe=ModuleType('safetensors'); safe.__path__=[]
            safe_torch=ModuleType('safetensors.torch')
            def load(data): observed.append(data); return state
            safe_torch.load=load
            spandrel=ModuleType('spandrel')
            class StopAfterLoad(Exception): pass
            class Loader:
                def load_from_state_dict(self,actual):
                    self_test.assertIs(actual,state)
                    raise StopAfterLoad()
            self_test=self
            spandrel.ModelLoader=Loader; spandrel.ImageModelDescriptor=object
            modules={'torch':torch,'numpy':ModuleType('numpy'),'safetensors':safe,
                     'safetensors.torch':safe_torch,'spandrel':spandrel}
            with patch.dict(sys.modules,modules), self.assertRaises(StopAfterLoad):
                backend.TorchBackend(payload,reference,'cuda:0')
            self.assertEqual(len(observed),1); self.assertIs(observed[0],payload)
            self.assertEqual(observed[0],b'verified')

    def test_session_is_fresh_and_startup_failures_are_structured(self):
        with tempfile.TemporaryDirectory() as directory:
            config=SimpleNamespace(model_id='nomos_compact_2x',device='cuda:0',session_dir=directory,
                                   checkpoint='unused',expected_sha256='unused')
            factory=unittest.mock.Mock(); out=io.StringIO()
            with patch.object(worker,'load_checkpoint',return_value=b'test'):
                code=worker.serve(config,io.BytesIO(),out,io.StringIO(),factory)
            self.assertEqual(code,1); factory.assert_not_called()
            self.assertEqual(json.loads(out.getvalue())['type'],'fatal')
        class Missing(InertBackend):
            def __init__(self,*args): raise ImportError('missing neural dependency')
        code,records,_,_=self.session([],Missing)
        self.assertEqual(code,1); self.assertEqual(records[0]['code'],'dependency_unavailable')

    def test_existing_result_is_not_success_or_overwritten(self):
        # Direct publication checks cover preserved bytes; this tests the record boundary.
        with patch.object(worker,'publish',side_effect=FileExistsError('existing result')):
            code,records,_,_=self.session([infer()])
        self.assertEqual(code,1); self.assertEqual([r['type'] for r in records],['ready','fatal'])

    def test_session_state_limit_and_no_inband_cancel(self):
        with patch.object(worker,'MAX_REQUESTS',1):
            code,records,_,_=self.session([{'protocol':1,'type':'cancel','id':'x'},infer()])
        self.assertEqual(code,1); self.assertEqual(records[1]['code'],'invalid_type')
        self.assertEqual(records[2]['code'],'session_limit')

    def test_subprocess_timeout_terminates_and_reaps_child(self):
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('process_driver.py')),'--hang'],
                               shell=False,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            with self.assertRaises(subprocess.TimeoutExpired): child.communicate(timeout=.2)
        finally:
            child.kill(); child.communicate(timeout=5)
        self.assertIsNotNone(child.returncode)

    def test_cli_help_and_missing_arguments_keep_stdout_structured(self):
        help_result=subprocess.run([sys.executable,'-B',str(ROOT/'worker.py'),'--help'],
                                   shell=False,capture_output=True,timeout=10)
        self.assertEqual(help_result.returncode,0); self.assertEqual(help_result.stdout,b'')
        self.assertIn(b'--checkpoint',help_result.stderr)
        missing=subprocess.run([sys.executable,'-B',str(ROOT/'worker.py')],
                               shell=False,capture_output=True,timeout=10)
        self.assertNotEqual(missing.returncode,0)
        self.assertEqual(json.loads(missing.stdout)['code'],'startup_arguments')


if __name__=='__main__': unittest.main()

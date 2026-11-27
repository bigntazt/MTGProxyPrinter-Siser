"""Inactive service lifecycle/cache/fallback/cancellation with public client doubles."""
from dataclasses import replace
import threading
from unittest.mock import Mock, patch

import pytest
from PySide6.QtGui import QImage

from mtg_proxy_printer import image_preparation_neural as service, image_processing_neural as processing
from mtg_proxy_printer.image_derivative_cache import DerivativeCache, CacheWriteFailure, CacheMiss
from mtg_proxy_printer.image_derivative import build_derivative_request
from mtg_proxy_printer.image_source import plan_target_resolution
from mtg_proxy_printer.neural_worker_client import (NeuralWorkerConfig, NeuralWorkerError, NeuralWorkerCancelled,
                                                  NeuralWorkerCleanup, REFERENCE_SHA256)
from tests.test_image_processing_neural import EchoSession, ready, source
from tests.test_image_processing_pillow import patterned_image


@pytest.fixture
def setup(tmp_path):
    config=NeuralWorkerConfig(tmp_path/'python.exe',tmp_path/'worker.py',tmp_path/'checkpoint',REFERENCE_SHA256,
                              'nomos_compact_2x','cuda:0',tmp_path)
    event=threading.Event();cache=DerivativeCache(tmp_path/'cache');sessions=[]
    class Session(EchoSession):
        def __init__(self,config,event):
            super().__init__();self.starts=0;self.closed=0;self.start_error=self.infer_error=self.close_error=None
            self.on_start=self.on_infer=None;sessions.append(self)
        def start(self):
            self.starts+=1
            if self.on_start:self.on_start()
            if self.start_error:raise self.start_error
            return self.ready
        def infer(self,*args):
            if self.on_infer:self.on_infer()
            if self.infer_error:raise self.infer_error
            return super().infer(*args)
        def close(self):
            self.closed+=1
            if self.close_error:raise self.close_error
            self.cleanup_report=NeuralWorkerCleanup(1,0,True,True,True)
    with patch.object(service,'NeuralWorkerSession',Session):
        yield config,cache,event,sessions


def preparer(setup):return service.NeuralImagePreparer(*setup[:3])
def prepare(p,snapshot=None,ppi=600):
    snapshot=source() if snapshot is None else snapshot
    return p.prepare(snapshot,plan_target_resolution(snapshot,ppi))
def assert_original(result,snapshot,reason,outcome='fallback_original'):
    assert result.encoded_bytes is snapshot.encoded_bytes and result.image_format==snapshot.image_format
    assert result.actual_dimensions==snapshot.native_dimensions and result.derivative_key is None
    assert result.reason==reason and result.outcome==outcome


def test_lazy_context_and_sufficient_shortcut_never_resolve_optional_work(setup):
    snapshot=source(patterned_image(8,12));event=setup[2]
    with patch.object(processing,'discover_neural_runtime',side_effect=AssertionError('no optional work')), \
         patch.object(DerivativeCache,'lookup',side_effect=AssertionError('no cache')):
        with preparer(setup) as p:
            assert setup[3]==[]
            result=prepare(p,snapshot)
            assert_original(result,snapshot,'source_sufficient','original')
            assert result.target_dimensions==(8,12) and result.target_ppi==600
        p.close()
    assert setup[3]==[]
    with pytest.raises(RuntimeError):prepare(p)


def test_invalid_source_plan_precedes_runtime_and_cancel_boundaries(setup):
    s=source();plan=plan_target_resolution(s,600)
    with preparer(setup) as p,patch.object(processing,'discover_neural_runtime',side_effect=AssertionError('no discovery')):
        with pytest.raises(ValueError):p.prepare(replace(s,sha256='0'*64),plan)
        with pytest.raises(ValueError):p.prepare(s,replace(plan,target_dimensions=(99,99)))
    event=setup[2];event.set()
    with preparer(setup) as p:
        with pytest.raises(NeuralWorkerCancelled):p.prepare(None,None)
    event.clear()
    original=service.validate_source_plan
    def validate(*args):original(*args);event.set()
    with preparer(setup) as p,patch.object(service,'validate_source_plan',side_effect=validate):
        with pytest.raises(NeuralWorkerCancelled):prepare(p,source(patterned_image(8,12)))
    assert setup[3]==[]


def test_generation_warm_and_fresh_persistent_reuse_after_current_ready(setup):
    with preparer(setup) as p:
        first=prepare(p);session=setup[3][0]
        assert first.outcome=='generated_cached' and QImage.fromData(first.encoded_bytes).size().toTuple()==(8,12)
        with patch.object(processing.NeuralRuntime,'produce_png',side_effect=AssertionError('no producer on hit')):
            second=prepare(p)
        assert second.outcome=='cache_hit' and second.derivative_key==first.derivative_key
        assert second.encoded_bytes==first.encoded_bytes and session.starts==2 and len(session.inputs)==1
        sufficient=source(patterned_image(8,12));prepare(p,sufficient)
        assert session.starts==2
    assert session.closed==1
    with preparer(setup) as p,patch.object(processing.NeuralRuntime,'produce_png',side_effect=AssertionError('persistent hit')):
        third=prepare(p)
    assert third.outcome=='cache_hit' and len(setup[3])==2
    assert setup[3][1].starts==1 and setup[3][1].inputs==[]


@pytest.mark.parametrize('reason',['worker_unavailable','timeout','protocol_error','result_integrity','request_budget'])
def test_fatal_or_exhausted_session_blocks_stale_hits_and_never_restarts(setup,reason):
    snapshot=source()
    with preparer(setup) as p:
        prepare(p,snapshot);session=setup[3][0];session.start_error=NeuralWorkerError(reason,'injected readiness failure')
        with patch.object(DerivativeCache,'lookup',side_effect=AssertionError('no stale acceptance')):
            assert_original(prepare(p,snapshot),snapshot,reason)
            assert_original(prepare(p,snapshot),snapshot,reason)
        assert session.starts==2 and len(setup[3])==1 and len(session.inputs)==1


@pytest.mark.parametrize('reason',['invalid_config','session_state','wrong_thread','invalid_input'])
def test_client_programming_errors_propagate(setup,reason):
    with preparer(setup) as p:
        prepare(p);setup[3][0].infer_error=NeuralWorkerError(reason,'defect')
        with pytest.raises(NeuralWorkerError) as raised:prepare(p,source(patterned_image(2,3)))
        assert raised.value.reason==reason


def test_recoverable_rejection_and_source_failure_do_not_poison_worker(setup):
    with preparer(setup) as p:
        prepare(p);session=setup[3][0]
        snapshot=source(patterned_image(2,3));session.infer_error=NeuralWorkerError('worker_rejected','per-image')
        assert_original(prepare(p,snapshot),snapshot,'worker_rejected')
        session.infer_error=None
        unsupported=source(patterned_image(3,5).convertToFormat(QImage.Format.Format_RGBA64))
        assert_original(prepare(p,unsupported),unsupported,'unsupported_source')
        assert prepare(p,snapshot).outcome=='generated_cached' and len(setup[3])==1


def test_optional_pillow_failure_and_input_limit_are_specific_originals(setup):
    snapshot=source()
    with preparer(setup) as p,patch.object(processing,'discover_neural_runtime',side_effect=service.ProcessingFailure('provider_unavailable','Pillow')):
        assert_original(prepare(p,snapshot),snapshot,'provider_unavailable')
    assert setup[3]==[]
    with preparer(setup) as p:
        prepare(p);session=setup[3][0];session.ready=replace(session.ready,max_input_pixels=1)
        with patch.object(processing.NeuralRuntime,'produce_png',side_effect=AssertionError('no decode')):
            assert_original(prepare(p,snapshot),snapshot,'worker_input_limit')


def test_valid_uncached_and_invalid_generated_output(setup):
    snapshot=source()
    with preparer(setup) as p,patch.object(DerivativeCache,'store',return_value=CacheWriteFailure('write_failed','disk denied')):
        result=prepare(p,snapshot)
        assert result.outcome=='generated_uncached' and result.derivative_key and result.reason=='cache_write_failed'
        assert QImage.fromData(result.encoded_bytes).size().toTuple()==(8,12)
    with preparer(setup) as p,patch.object(processing.NeuralRuntime,'produce_png',return_value=b'invalid PNG'):
        assert_original(prepare(p,snapshot),snapshot,'invalid_generated_png')


def test_readiness_cancellation_never_reuses_committed_entry(setup):
    with preparer(setup) as p:
        prepare(p);session=setup[3][0];session.start_error=NeuralWorkerCancelled()
        with patch.object(DerivativeCache,'lookup',side_effect=AssertionError('no stale hit')):
            with pytest.raises(NeuralWorkerCancelled):prepare(p)
        assert session.closed>=1


@pytest.mark.parametrize('stage',['lookup','inference','before_store','during_store'])
def test_cancellation_suppresses_result_and_preserves_valid_publication(setup,stage):
    event=setup[2];cache=setup[1];snapshot=source();requests=[]
    original_lookup=DerivativeCache.lookup;original_store=DerivativeCache.store
    def lookup(cache,request):
        result=original_lookup(cache,request)
        if stage=='lookup':event.set()
        return result
    def store(cache,request,data):
        requests.append(request);result=original_store(cache,request,data)
        if stage=='during_store':event.set()
        return result
    with preparer(setup) as p:
        # Establish an owned session, then use a different source for the cancellation call.
        prepare(p);session=setup[3][0];snapshot=source(patterned_image(2,3))
        if stage=='inference':session.on_infer=event.set
        original_produce=processing.NeuralRuntime.produce_png
        def produce(*args):
            data=original_produce(*args)
            if stage=='before_store':event.set()
            return data
        with patch.object(DerivativeCache,'lookup',lookup),patch.object(DerivativeCache,'store',store), \
             patch.object(processing.NeuralRuntime,'produce_png',produce):
            with pytest.raises(NeuralWorkerCancelled):prepare(p,snapshot)
        assert session.closed>=1
    if stage=='during_store':
        assert len(requests)==1 and not isinstance(cache.lookup(requests[0]),CacheMiss)
        event.clear()
        with preparer(setup) as fresh:assert prepare(fresh,snapshot).outcome=='cache_hit'
    else:assert requests==[]


def test_cancel_cache_hit_and_fallback_return_boundaries(setup):
    event=setup[2]
    with preparer(setup) as p:
        prepare(p);original=DerivativeCache.lookup
        def hit(*args):result=original(*args);event.set();return result
        with patch.object(DerivativeCache,'lookup',hit):
            with pytest.raises(NeuralWorkerCancelled):prepare(p)
    event.clear()
    def unavailable():event.set();raise service.ProcessingFailure('provider_unavailable','Pillow')
    with preparer(setup) as p,patch.object(processing,'discover_neural_runtime',side_effect=unavailable):
        with pytest.raises(NeuralWorkerCancelled):prepare(p)


def test_cleanup_limitations_propagate_and_context_preserves_primary(setup):
    with preparer(setup) as p:
        prepare(p);session=setup[3][0]
        failure=NeuralWorkerError('worker_unavailable','fatal');failure.cleanup_report=NeuralWorkerCleanup(1,None,False,False,False,('unresolved',))
        session.start_error=failure
        with pytest.raises(NeuralWorkerError) as raised:prepare(p)
        assert raised.value is failure
    p=preparer(setup)
    with pytest.raises(ValueError,match='primary') as raised:
        with p:
            prepare(p);session=setup[3][-1];session.close_error=NeuralWorkerError('cleanup_failed','injected')
            raise ValueError('primary')
    assert 'cleanup' in raised.value.__notes__[0]
    session.close_error=None;p.close()


def test_owner_thread_and_close_after_unused_preparer(setup):
    p=preparer(setup);errors=[]
    def other():
        for action in (lambda:prepare(p),p.close):
            try:action()
            except RuntimeError:errors.append(True)
    t=threading.Thread(target=other);t.start();t.join()
    assert errors==[True,True] and setup[3]==[]
    p.close();p.close()
    with pytest.raises(RuntimeError):prepare(p)


def test_cancellation_after_runtime_and_decode_and_cleanup_information(setup):
    event=setup[2];original=processing.discover_neural_runtime
    def discovered():runtime=original();event.set();return runtime
    with preparer(setup) as p,patch.object(processing,'discover_neural_runtime',side_effect=discovered):
        with pytest.raises(NeuralWorkerCancelled):prepare(p)
    assert setup[3]==[]
    event.clear()
    from mtg_proxy_printer import image_processing_pillow as pillow
    decode=pillow.decode_source_rgba
    def decoded(*args):result=decode(*args);event.set();return result
    p=preparer(setup)
    with patch.object(pillow,'decode_source_rgba',side_effect=decoded):
        with pytest.raises(NeuralWorkerCancelled) as raised:prepare(p)
    assert raised.value.cleanup_report.reaped and setup[3][0].closed>=1
    p.close();event.clear()
    p=preparer(setup);prepare(p);session=setup[3][-1]
    cleanup=NeuralWorkerError('cleanup_failed','injected unresolved cleanup')
    cleanup.cleanup_report=NeuralWorkerCleanup(1,None,False,False,False,('unresolved',))
    session.close_error=cleanup;event.set()
    with pytest.raises(NeuralWorkerCancelled) as raised:prepare(p)
    assert raised.value.cleanup_report is cleanup.cleanup_report and raised.value.cleanup_error is cleanup
    session.close_error=None;p.close()


def test_disabled_provider_does_not_retain_per_image_traceback_buffers(setup):
    import gc,weakref
    refs=[]
    class BufferOwner:pass
    def fail(*args):
        image_buffer=BufferOwner();refs.append(weakref.ref(image_buffer))
        raise NeuralWorkerError('timeout','injected per-image failure')
    with preparer(setup) as p:
        prepare(p);setup[3][0].infer=fail
        result=prepare(p,source(patterned_image(2,3)))
        assert result.reason=='timeout'
        gc.collect();assert refs[0]() is None


@pytest.mark.parametrize('report_on_error',[True,False])
def test_disposed_rejection_records_fallback_without_reusing_session(setup,report_on_error):
    p=preparer(setup)
    try:
        prepare(p);session=setup[3][0];snapshot=source(patterned_image(2,3))
        rejection=NeuralWorkerError('worker_rejected','rejected job cleanup failed; session disposed '+('x'*5000))
        report=NeuralWorkerCleanup(1,0,True,True,True)
        def rejected(*args):
            session.cleanup_report=report
            if report_on_error:rejection.cleanup_report=report
            raise rejection
        with patch.object(session,'infer',side_effect=rejected) as inference:
            first=prepare(p,snapshot)
            assert_original(first,snapshot,'worker_rejected')
            assert first.detail==rejection.detail and len(first.detail)<=4096
            sufficient=source(patterned_image(8,12))
            with patch.object(session,'start',side_effect=AssertionError('disposed worker must not restart')) as start, \
                 patch.object(DerivativeCache,'lookup',side_effect=AssertionError('disabled provider must not lookup')) as lookup, \
                 patch.object(service,'NeuralWorkerSession',side_effect=AssertionError('no replacement session')):
                assert_original(prepare(p,snapshot),snapshot,'worker_rejected')
                assert_original(prepare(p,sufficient),sufficient,'source_sufficient','original')
                start.assert_not_called();lookup.assert_not_called()
            assert inference.call_count==1 and len(setup[3])==1
    finally:p.close()


@pytest.mark.parametrize('report_on_error',[True,False])
def test_rejected_session_with_unresolved_cleanup_propagates_without_retrying(setup,report_on_error):
    p=preparer(setup)
    try:
        prepare(p);session=setup[3][0];snapshot=source(patterned_image(2,3))
        report=NeuralWorkerCleanup(1,None,False,False,False,('unresolved rejected-session cleanup',))
        rejection=NeuralWorkerError('worker_rejected','rejection plus incomplete disposal')
        def rejected(*args):
            session.cleanup_report=report
            if report_on_error:rejection.cleanup_report=report
            raise rejection
        with patch.object(session,'infer',side_effect=rejected):
            with pytest.raises(NeuralWorkerError) as raised:prepare(p,snapshot)
        assert raised.value is rejection and raised.value.cleanup_report is report
        with patch.object(session,'start',side_effect=AssertionError('disposed worker must not restart')) as start, \
             patch.object(DerivativeCache,'lookup',side_effect=AssertionError('no cache access')) as lookup, \
             patch.object(service,'NeuralWorkerSession',side_effect=AssertionError('no replacement')):
            with pytest.raises(NeuralWorkerError) as raised:prepare(p,snapshot)
            assert raised.value.reason=='worker_rejected' and raised.value.cleanup_report is report
            start.assert_not_called();lookup.assert_not_called()
        assert len(setup[3])==1
    finally:p.close()


@pytest.mark.parametrize('retry_cleanup_fails',[False,True])
def test_budget_failure_cleanup_cancellation_is_terminal_after_event_clear(setup,retry_cleanup_fails):
    p=preparer(setup);event=setup[2]
    try:
        prepare(p);session=setup[3][0];snapshot=source(patterned_image(2,3))
        report=NeuralWorkerCleanup(1,0,True,True,not retry_cleanup_fails,
                                   ('temporary cleanup denied',) if retry_cleanup_fails else ())
        cancellation=NeuralWorkerCancelled();cancellation.cleanup_report=report
        retry_error=NeuralWorkerError('cleanup_failed','retry denied');retry_error.cleanup_report=report
        closes=[]
        def budget_guard(*args):
            # Match M34: the idle budget guard precedes its operation cancellation check.
            event.set()
            raise NeuralWorkerError('request_budget','shutdown capacity reserved')
        def close():
            closes.append(True);session.cleanup_report=report
            if len(closes)==1:raise cancellation
            if retry_cleanup_fails:raise retry_error
        with patch.object(session,'infer',side_effect=budget_guard) as inference,patch.object(session,'close',side_effect=close):
            with pytest.raises(NeuralWorkerCancelled) as raised:prepare(p,snapshot)
            assert raised.value is cancellation and raised.value.cleanup_report is report
            if retry_cleanup_fails:assert raised.value.cleanup_error is retry_error
            event.clear()
            with patch.object(session,'start',side_effect=AssertionError('cancelled preparer cannot restart')) as start, \
                 patch.object(DerivativeCache,'lookup',side_effect=AssertionError('cancelled preparer cannot lookup')) as lookup, \
                 patch.object(processing,'discover_neural_runtime',side_effect=AssertionError('no optional discovery')), \
                 patch.object(service,'NeuralWorkerSession',side_effect=AssertionError('no replacement worker')):
                with pytest.raises(NeuralWorkerCancelled):prepare(p,source(patterned_image(8,12)))
                with pytest.raises(NeuralWorkerCancelled):prepare(p,snapshot)
                start.assert_not_called();lookup.assert_not_called()
            assert inference.call_count==1 and len(closes)>=2 and len(setup[3])==1
        p.close();p.close()
        with pytest.raises(RuntimeError,match='closed'):prepare(p,snapshot)
    finally:
        event.clear();p.close()


@pytest.mark.parametrize('canonical,expected',[((745,1040),[(1490,2080),(2235,3120),(2980,4160)]),
                                             ((1040,1490),[(2080,2980),(3120,4470),(4160,5960)])])
def test_existing_physical_plans_are_exact_without_full_size_rendering(canonical,expected):
    snapshot=source(canonical=canonical)
    assert [plan_target_resolution(snapshot,ppi).target_dimensions for ppi in (600,900,1200)]==expected

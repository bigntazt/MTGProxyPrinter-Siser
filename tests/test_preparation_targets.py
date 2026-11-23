"""Three-target validation and existing derivative identity compatibility."""
from dataclasses import replace
import json
import pytest
import shiboken6

from mtg_proxy_printer.image_source import SUPPORTED_TARGET_PPI, plan_target_resolution
from mtg_proxy_printer.image_derivative import DerivativeRequest
from mtg_proxy_printer.image_derivative_cache import DerivativeCache
from mtg_proxy_printer.async_tasks.image_preparation import PreparationBatchTask
from mtg_proxy_printer.page_scene.prepared_raster_session import PreparedPageSession
from mtg_proxy_printer.page_scene.prepared_pixmap import decode_prepared_pixmap, RasterDecodeFailure
from tests.test_image_derivative import make_request, make_snapshot
from tests.page_scene.test_prepared_pixmap import prepared
from tests.test_image_derivative_cache import encoded


@pytest.mark.parametrize('canonical,target',[((745,1040),(2235,3120)),((1040,1490),(3120,4470))])
def test_middle_target_planning(canonical,target):
    assert SUPPORTED_TARGET_PPI == (600,900,1200)
    assert plan_target_resolution(make_snapshot(canonical_dimensions=canonical),900).target_dimensions == target


@pytest.mark.parametrize('ppi',[True,False,900.0,'900',300,750,901,1500,None])
def test_all_five_boundaries_reject_invalid_targets(document_light,tmp_path,ppi):
    with pytest.raises(ValueError): plan_target_resolution(make_snapshot(),ppi)
    spec=make_request().to_spec()
    spec['geometry']['target_ppi']=ppi
    with pytest.raises(ValueError):
        DerivativeRequest(json.dumps(spec,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode())
    with pytest.raises(ValueError):
        PreparationBatchTask(1,(),mode='original',target_ppi=ppi,cache=DerivativeCache(tmp_path))
    assert isinstance(decode_prepared_pixmap(replace(prepared(),target_ppi=ppi)),RasterDecodeFailure)
    session=PreparedPageSession(document_light,lambda task: None,tmp_path,DerivativeCache(tmp_path/'derivatives'))
    try:
        with pytest.raises(ValueError): session.enable(mode='original',target_ppi=ppi)
    finally:
        session.close()
        shiboken6.delete(session)


def test_deterministic_distinct_middle_identity_and_existing_golden_keys(tmp_path):
    requests=[make_request(ppi=ppi) for ppi in SUPPORTED_TARGET_PPI]
    assert len({request.key for request in requests}) == 3
    assert requests[1] == make_request(ppi=900)
    assert requests[0].key == 'e817a0b7d4c3225d533726714604db6a1af679f57301302c64028be48746d089'
    # Golden 600 descriptor fixes the accepted canonical structure; only target
    # fields differ for 1200. Schema, source and recipe stay byte-compatible.
    spec=requests[0].to_spec()
    spec['geometry'].update(target_ppi=1200,target_dimensions=[2980,4160])
    assert requests[2].canonical_bytes == json.dumps(spec,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    cache=DerivativeCache(tmp_path)
    for request in (requests[0],requests[2]):
        data=encoded(request.target_dimensions)
        cache.store(request,data)
        assert cache.lookup(make_request(ppi=request.to_spec()['geometry']['target_ppi'])).png_bytes == data

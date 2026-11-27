"""Small decoded-pixel checks for straight-RGB inference and explicit alpha policy."""
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from unittest.mock import patch

import pytest
from PySide6.QtGui import QColor, QColorSpace, QImage

from mtg_proxy_printer import image_processing_neural as neural, image_processing_pillow as pillow
from mtg_proxy_printer.image_derivative import build_derivative_request
from mtg_proxy_printer.image_source import plan_target_resolution
from mtg_proxy_printer.neural_worker_client import NeuralWorkerReady, NativeRGBResult, REFERENCE_SHA256
from tests.test_image_processing_pillow import (patterned_image, encoded_image, snapshot_from_bytes, oriented_jpeg)


def ready(**changes):
    spec={"model":{"id":"nomos_compact_2x","sha256":REFERENCE_SHA256},
          "runtime":{"precision":"float32","package":"test-runtime"},"implementation":"test-public-ready"}
    data=json.dumps(spec,sort_keys=True,separators=(',',':')).encode()
    value=NeuralWorkerReady('a'*32,hashlib.sha256(data).hexdigest(),data,4194304,4096,8192,())
    return replace(value,**changes)


class EchoSession:
    """Public client-shaped double; nearest native 2x only for deterministic tests."""
    def __init__(self,info=None):
        self.ready=info or ready();self.inputs=[];self.cleanup_report=None
    def infer(self,data,width,height):
        from PIL import Image
        self.inputs.append((data,width,height))
        with Image.frombytes('RGB',(width,height),data) as source:
            with source.resize((width*2,height*2),Image.Resampling.NEAREST) as output:
                raw=output.tobytes()
        return NativeRGBResult(raw,width*2,height*2,hashlib.sha256(data).hexdigest(),hashlib.sha256(raw).hexdigest(),
                               self.ready.fingerprint,self.ready.provenance_json,self.ready.session_token,'b'*32,())


def source(image=None,canonical=(4,6)):
    return snapshot_from_bytes(encoded_image(patterned_image(3,5) if image is None else image,metadata=True),canonical=canonical)


@pytest.fixture
def runtime():return neural.discover_neural_runtime()


@pytest.mark.parametrize('target',[(6,10),(9,13),(4,8)])
def test_straight_rgb_alpha_and_exact_resampling_pixels(runtime,target):
    from PIL import Image
    image=QImage(3,5,QImage.Format.Format_RGBA8888)
    for y in range(5):
        for x,alpha in enumerate((255,96,0)):image.setPixelColor(x,y,QColor(40+x*70,20+y*25,200-x*40,alpha))
    snapshot=source(image);session=EchoSession()
    data=runtime.produce_png(snapshot,target,session,lambda:None)
    rgba,_=pillow.decode_source_rgba(snapshot)
    with Image.frombytes('RGBA',(3,5),bytes(rgba.constBits()),'raw','RGBA',rgba.bytesPerLine(),1) as original:
        rgb=original.convert('RGB');alpha=original.getchannel('A')
        assert session.inputs==[(rgb.tobytes(),3,5)]  # Includes the hidden straight color at alpha zero.
        native=rgb.resize((6,10),Image.Resampling.NEAREST)
        native.putalpha(alpha.resize((6,10),Image.Resampling.LANCZOS,reducing_gap=None))
        expected=native if target==(6,10) else native.resize(target,Image.Resampling.LANCZOS,reducing_gap=None)
        with Image.open(BytesIO(data)) as actual:
            assert actual.mode=='RGBA' and actual.size==target and actual.tobytes()==expected.tobytes()
            assert 'icc_profile' not in actual.info and 'dpi' not in actual.info and 'exif' not in actual.info
            assert 'M26' not in actual.info and actual.getextrema()[3][0]==0
        if expected is not native:expected.close()
        native.close();rgb.close();alpha.close()
    assert data[24:26]==bytes((8,6))


def test_stride_and_equal_native_size_skip_final_interpolation(runtime):
    from PIL import Image
    raw=bytearray(32*3);image=QImage(raw,5,3,32,QImage.Format.Format_RGBA8888);image.fill(QColor('white'))
    for row,color in enumerate(('red','green','blue')):image.setPixelColor(0,row,QColor(color))
    assert image.bytesPerLine()==32
    session=EchoSession();operations=[];original=Image.Image.resize
    def resize(image,size,*args,**kwargs):
        operations.append((image.mode,size,kwargs.get('reducing_gap')))
        return original(image,size,*args,**kwargs)
    with patch.object(pillow,'_decode_source',return_value=image),patch.object(Image.Image,'resize',resize):
        output=runtime.produce_png(source(),(10,6),session,lambda:None)
    assert [call for call in operations if call[0]=='RGBA']==[]
    assert [call for call in operations if call[0]=='L']==[('L',(10,6),None)]
    decoded=QImage.fromData(output)
    assert [decoded.pixelColor(0,y*2) for y in range(3)]==[QColor(c) for c in ('red','green','blue')]


def test_orientation_profile_and_no_pillow_source_decode(runtime):
    from PIL import Image
    snapshot=snapshot_from_bytes(oriented_jpeg())
    session=EchoSession()
    with patch.object(Image,'open',side_effect=AssertionError('No Pillow source decoding')):
        data=runtime.produce_png(snapshot,(62,94),session,lambda:None)
    decoded=QImage.fromData(data)
    assert decoded.pixelColor(4,4).red()>200 and decoded.pixelColor(4,89).blue()>200
    tagged=patterned_image(3,5);tagged.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.DisplayP3))
    snapshot=source(tagged);expected=bytes(pillow._decode_source(snapshot).colorSpace().iccProfile())
    data=runtime.produce_png(snapshot,(6,10),EchoSession(),lambda:None)
    with Image.open(BytesIO(data)) as actual:
        assert actual.info['icc_profile']==expected and 'dpi' not in actual.info and 'exif' not in actual.info


def test_new_recipe_material_identity_and_transient_exclusion(runtime):
    r=ready();spec=runtime.recipe(r).to_spec()
    assert spec['provider']=={'id':'spandrel-neural-rgb8','version':'1'}
    assert spec['pipeline_version']=='m35-neural-preparation-v1'
    assert spec['parameters']['worker']['provenance']==json.loads(r.provenance_json)
    assert runtime.recipe(replace(r,session_token='b'*32,timings=(('startup_seconds',99),)))==runtime.recipe(r)
    snapshot=source();plan=plan_target_resolution(snapshot,600)
    base=build_derivative_request(snapshot,plan,runtime.recipe(r)).key
    variations=[replace(r,fingerprint='f'*64),replace(r,provenance_json=r.provenance_json.replace(b'test-runtime',b'other-runtime')),
                replace(r,provenance_json=r.provenance_json.replace(REFERENCE_SHA256.encode(),b'0'*64))]
    assert all(build_derivative_request(snapshot,plan,runtime.recipe(v)).key!=base for v in variations)
    changed=neural.NeuralRuntime(replace(runtime.host,pillow_version='different'))
    assert build_derivative_request(snapshot,plan,changed.recipe(r)).key!=base
    for ppi in (900,1200):assert build_derivative_request(snapshot,plan_target_resolution(snapshot,ppi),runtime.recipe(r)).key!=base
    from mtg_proxy_printer.model.imagedb_files import ImageKey
    back=replace(snapshot,image_key=ImageKey(snapshot.image_key.scryfall_id,False,snapshot.image_key.image_quality))
    assert build_derivative_request(back,plan_target_resolution(back,600),runtime.recipe(r)).key!=base
    changed_image=patterned_image(3,5);changed_image.setPixelColor(0,0,QColor('purple'))
    altered=source(changed_image)
    assert build_derivative_request(altered,plan_target_resolution(altered,600),runtime.recipe(r)).key!=base
    policy=spec;policy['parameters']['encoding']['compress_level']=5
    from mtg_proxy_printer.image_derivative import ProcessingRecipe
    assert build_derivative_request(snapshot,plan,ProcessingRecipe.from_spec(policy)).key!=base


def test_unsupported_precision_and_encoding_failure(runtime):
    snapshot=source(patterned_image(3,5).convertToFormat(QImage.Format.Format_RGBA64))
    session=EchoSession()
    with pytest.raises(pillow.ProcessingFailure,match='Unsupported'):runtime.produce_png(snapshot,(6,10),session,lambda:None)
    assert session.inputs==[]
    with patch.object(runtime.host.image_module.Image,'save',side_effect=OSError('encode failure')):
        with pytest.raises(pillow.ProcessingFailure):runtime.produce_png(source(),(6,10),session,lambda:None)

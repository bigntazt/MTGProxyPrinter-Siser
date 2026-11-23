"""Prepared pixels on independently sized RGB page canvases and bounded failure paths."""
from dataclasses import replace
import json
import math
import struct
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zlib

import pytest
import shiboken6
from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QIODevice
from PySide6.QtGui import QColor, QImage, QPainter

from mtg_proxy_printer import prepared_page_png as png, prepared_page_output as output, image_processing_pillow
from mtg_proxy_printer.siser_registration import resolve_siser_page_marks
from mtg_proxy_printer.units_and_sizes import CardSizes, unit_registry
from tests.test_prepared_page_pdf import preview, start
from tests.test_siser_artwork_output import configure, transformed


def capture_png(h, ppi=600, rotate=False):
    return png.capture_prepared_page_png(h.document,h.session,mode=h.dock.mode,target_ppi=h.dock.target_ppi,
                                         output_ppi=ppi,landscape_workaround=rotate)


def pattern():
    image=QImage(83,118,QImage.Format.Format_RGB888)
    image.fill(QColor('magenta'))
    painter=QPainter(image)
    painter.fillRect(0,59,83,59,QColor('cyan'))
    painter.setPen(QColor('yellow')); painter.drawLine(5,5,75,112); painter.end()
    data=QByteArray(); buffer=QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer,'PNG')
    return bytes(data)


def png_info(data):
    assert data[:8]==b'\x89PNG\r\n\x1a\n'
    offset=8; info={}; kinds=[]
    while offset<len(data):
        length=struct.unpack('>I',data[offset:offset+4])[0]
        kind=data[offset+4:offset+8]; body=data[offset+8:offset+8+length]
        crc=struct.unpack('>I',data[offset+8+length:offset+12+length])[0]
        assert zlib.crc32(kind+body)&0xffffffff==crc
        kinds.append(kind)
        if kind==b'IHDR': info['header']=struct.unpack('>IIBBBBB',body)
        if kind==b'pHYs': info['density']=struct.unpack('>IIB',body)
        offset+=length+12
    assert offset==len(data) and kinds[-1]==b'IEND' and b'IDAT' in kinds
    return info


def decode_isolated(path,samples=(),regions=(),red_bounds=False):
    # A separate Pillow-only process can decode >Qt's ordinary reader allocation
    # limit without changing any application-wide Qt or Pillow policy.
    script='''import json,sys,warnings
from PIL import Image,ImageChops
warnings.simplefilter('ignore',Image.DecompressionBombWarning)
request=json.loads(sys.argv[2])
with Image.open(sys.argv[1]) as image:
 image.load()
 result={'size':image.size,'mode':image.mode,'samples':[image.getpixel(tuple(p)) for p in request['samples']]}
 result['bars']=[]
 for box in request['regions']:
  crop=image.crop(tuple(box)).convert('L').point(lambda v:255 if v==0 else 0)
  b=crop.getbbox()
  result['bars'].append(None if b is None else [b[0]+box[0],b[1]+box[1],b[2]+box[0],b[3]+box[1]])
 if request['red']:
  mask=image.getchannel('R').point(lambda v:255 if v==255 else 0)
  for channel in ('G','B'):
   mask=ImageChops.multiply(mask,image.getchannel(channel).point(lambda v:255 if v==0 else 0))
  result['red']=mask.getbbox()
 print(json.dumps(result))
'''
    result=subprocess.run([sys.executable,'-B','-c',script,str(path),json.dumps({'samples':samples,'regions':regions,'red':red_bounds})],
                          capture_output=True,text=True,timeout=45,check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('ppi,paper,rotate,mode,target',[
    (600,'Letter',False,'lanczos',900),(900,'Letter',False,'original',600),
    (1200,'Letter',False,'original',900),(600,'A4',False,'original',600),
    (600,'A4',True,'original',900)])
def test_ready_pages_use_prepared_pixels_density_and_shared_marks(preview,ppi,paper,rotate,mode,target):
    h=preview
    configure(h.document,paper,card_bleed=1*unit_registry.mm,row_spacing=2*unit_registry.mm,column_spacing=3*unit_registry.mm,
              margin_top=25*unit_registry.mm,margin_left=25*unit_registry.mm)
    start(h,mode=mode,ppi=target,source=pattern())
    before=h.session.status(); tasks=len(h.tasks); target_choice=h.dock.target_ppi
    with patch.object(QCoreApplication,'processEvents',side_effect=AssertionError('No event boundary')):
        result=capture_png(h,ppi,rotate)
    assert h.session.status()==before and len(h.tasks)==tasks and h.dock.target_ppi==target_choice
    assert result.target_ppi==target and result.output_ppi==ppi
    geometry=h.scene.geometry
    paper_mm=(geometry.sheet_width_mm,geometry.sheet_height_mm)
    if rotate: paper_mm=paper_mm[::-1]
    expected=tuple(round(axis*ppi/25.4) for axis in paper_mm)
    assert (result.pixel_width,result.pixel_height)==expected
    info=png_info(result.data)
    assert info['header'][:2]==expected and info['header'][2:4]==(8,2)  # RGB8, no alpha.
    assert info['density']==(round(ppi/.0254),round(ppi/.0254),1)
    placement=geometry.placements[0].trim_mm
    x,y,w,height=transformed(placement,geometry,rotate)
    # Two source colors absent from the legacy canonical pattern prove adoption.
    points=[(placement.x+placement.width*.25,placement.y+placement.height*.2),
            (placement.x+placement.width*.25,placement.y+placement.height*.8)]
    if rotate: points=[(geometry.sheet_height_mm-y,x) for x,y in points]
    samples=[(0,0)]+[(round(x*ppi/25.4),round(y*ppi/25.4)) for x,y in points]
    rectangles=[transformed(bar.bounds_mm,geometry,rotate) for bar in resolve_siser_page_marks(geometry).bars]
    # Each corner's two bars touch, so raster measurement observes their union.
    edges=[]
    for first,second in zip(rectangles[::2],rectangles[1::2]):
        x=min(first[0],second[0]); y=min(first[1],second[1])
        right=max(first[0]+first[2],second[0]+second[2])
        bottom=max(first[1]+first[3],second[1]+second[3])
        edges.append(tuple(axis*ppi/25.4 for axis in (x,y,right,bottom)))
    regions=[(max(0,math.floor(x)-2),max(0,math.floor(y)-2),math.ceil(r)+2,math.ceil(b)+2) for x,y,r,b in edges]
    path=h.root/'decoded.png'; path.write_bytes(result.data)
    measured=decode_isolated(path,samples,regions)
    assert tuple(measured['size'])==expected and measured['mode']=='RGB'
    assert measured['samples']==[[255,255,255],[255,0,255],[0,255,255]]
    for observed,wanted in zip(measured['bars'],edges):
        assert observed==pytest.approx(wanted,abs=1,rel=0)


def test_oversized_partial_red_artwork_bounds(preview):
    from tests.test_image_source import encoded
    h=preview
    start(h,mode='original',size=CardSizes.OVERSIZED,source=encoded(104,149,color='red'))
    result=capture_png(h)
    path=h.root/'oversized.png'; path.write_bytes(result.data)
    measured=decode_isolated(path,red_bounds=True)
    rect=h.scene.geometry.placements[0].trim_mm
    assert measured['red']==pytest.approx(tuple(axis*600/25.4 for axis in (rect.x,rect.y,rect.right,rect.bottom)),abs=1,rel=0)


def test_genuine_fallback_and_immutable_output(preview,monkeypatch):
    h=preview
    def unavailable():
        raise image_processing_pillow.ProcessingFailure('provider_unavailable','Pillow unavailable fixture')
    monkeypatch.setattr(image_processing_pillow,'discover_pillow_runtime',unavailable)
    start(h,mode='lanczos',ppi=900,source=pattern())
    result=capture_png(h,600)
    assert dict(result.outcome_counts)=={'fallback_original':2} and result.target_ppi==900 and result.output_ppi==600
    before=result.data
    h.dock.set_off(); h.document.page_layout.row_spacing+=2*unit_registry.mm
    assert result.data==before and png_info(result.data)['header'][:2]==(4961,7016)
    assert not any(hasattr(value,'isValid') or isinstance(value,QImage) for value in vars(result).values())


@pytest.mark.parametrize('ppi',[True,False,600.0,'600',None,300,601,2400])
def test_output_ppi_is_strict_before_capture(ppi):
    with patch.object(png,'_capture_prepared_page_output',side_effect=AssertionError('No capture')):
        with pytest.raises(ValueError):
            png.capture_prepared_page_png(None,None,mode='original',target_ppi=600,output_ppi=ppi)


@pytest.mark.parametrize('paper',[(215.9,279.4),(210,297)])
@pytest.mark.parametrize('ppi',[600,900,1200])
def test_standard_canvas_budget(paper,ppi):
    width,height,charge=png._canvas_plan(SimpleNamespace(sheet_width_mm=paper[0],sheet_height_mm=paper[1]),ppi,False)
    assert charge==((width*3+3)//4)*4*height<=512*1024*1024


@pytest.mark.parametrize('dimensions',[(1000,1000),(0,297),(float('inf'),297),(10**12,297),(1e308,297)])
def test_canvas_rejection_precedes_allocation(dimensions):
    geometry=SimpleNamespace(sheet_width_mm=dimensions[0],sheet_height_mm=dimensions[1])
    with patch.object(png,'_allocate_page',side_effect=AssertionError('No allocation')):
        with pytest.raises((ValueError,RuntimeError)): png._render_png(None,geometry,1200,False)


@pytest.mark.parametrize('failure',['null','actual-budget','memory','begin','end','encode','stale'])
def test_failures_release_shared_scene_and_no_result(preview,monkeypatch,failure):
    h=preview; start(h,mode='original')
    scenes=[]; cls=output.PageScene
    def make(*args,**kwargs):
        scene=cls(*args,**kwargs); scenes.append(scene); return scene
    monkeypatch.setattr(output,'PageScene',make)
    if failure in ('null','actual-budget','memory'):
        image=Mock(); image.isNull.return_value=failure=='null'; image.sizeInBytes.return_value=png.MAX_PAGE_RASTER_BYTES+1
        monkeypatch.setattr(png,'_allocate_page',Mock(side_effect=MemoryError() if failure=='memory' else None,return_value=image))
    elif failure in ('begin','end'):
        painter=Mock(); painter.begin.return_value=failure!='begin'; painter.end.return_value=False; painter.isActive.return_value=False
        monkeypatch.setattr(png,'QPainter',Mock(return_value=painter,RenderHint=png.QPainter.RenderHint))
        monkeypatch.setattr(png,'_render_export_page',lambda *args:None)
    elif failure=='encode': monkeypatch.setattr(png,'_encode_png',lambda *args:False)
    else:
        encode=png._encode_png
        def stale(*args):
            result=encode(*args); h.session.refresh(); return result
        monkeypatch.setattr(png,'_encode_png',stale)
    with pytest.raises(RuntimeError): capture_png(h)
    assert scenes and not shiboken6.isValid(scenes[0])


def test_aligned_page_budget_exact_boundary(monkeypatch):
    geometry=SimpleNamespace(sheet_width_mm=3*25.4/600,sheet_height_mm=2*25.4/600)
    monkeypatch.setattr(png,'MAX_PAGE_RASTER_BYTES',24)
    assert png._canvas_plan(geometry,600,False)==(3,2,24)
    monkeypatch.setattr(png,'MAX_PAGE_RASTER_BYTES',23)
    with pytest.raises(RuntimeError,match='lower PNG resolution'):
        png._canvas_plan(geometry,600,False)


def test_existing_artwork_mark_overlap_remains_permitted(preview):
    h=preview
    configure(h.document,'Letter',card_bleed=1*unit_registry.mm,row_spacing=2*unit_registry.mm,column_spacing=3*unit_registry.mm)
    start(h,mode='original',source=pattern())
    result=capture_png(h)
    assert png_info(result.data)['header'][:2]==(5100,6600)

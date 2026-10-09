"""Measure actual stroked calibration PDF paths independently of Qt scene items."""

from dataclasses import replace
from io import BytesIO
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsLineItem, QGraphicsRectItem, QGraphicsSimpleTextItem
from pypdf import PdfReader

from mtg_proxy_printer import calibration
from mtg_proxy_printer.calibration import render_calibration_pdf, _build_calibration_scene
from mtg_proxy_printer.cut_template_svg import serialize_cut_template_svg
from mtg_proxy_printer.model.page_geometry import build_page_geometry
from mtg_proxy_printer.model.page_layout import PageLayoutSettings
from mtg_proxy_printer.page_scene.registration_profiles import get_registration_profile
from mtg_proxy_printer.units_and_sizes import PageType, unit_registry, distance_to_px

mm = unit_registry.mm


def stroked_paths(page):
    """Only straight synthetic Qt paths; discard clip/fill/text operations."""
    result, current = [], []
    closed = False
    height = float(page.mediabox.height)
    def visit(operator, args, cm, tm):
        nonlocal current, closed
        def point(x, y):
            a,b,c,d,e,f = cm
            return ((a*x+c*y+e)*25.4/72, (height-b*x-d*y-f)*25.4/72)
        if operator == b'm':
            current.append(point(*map(float, args)))
        elif operator == b'l':
            current.append(point(*map(float, args)))
        elif operator == b're':
            x,y,w,h = map(float,args)
            current.extend(point(x1,y1) for x1,y1 in [(x,y),(x+w,y),(x+w,y+h),(x,y+h)])
            closed = True
        elif operator == b'h':
            closed = True
        elif operator in {b'S',b's'}:
            result.append((current,closed or operator == b's'))
            current,closed = [],False
        elif operator in {b'n',b'f',b'f*',b'B',b'B*'}:
            current,closed = [],False
    page.extract_text(visitor_operand_before=visit)
    return result


def bounds(points):
    xs,ys=zip(*points)
    return min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)


def svg_bounds(geometry):
    result=[]
    for path in ElementTree.fromstring(serialize_cut_template_svg(geometry)):
        t=path.attrib['d'].split()
        result.append(bounds([(float(t[i]),float(t[i+1])) for i in (1,4,7,10)]))
    return result


@pytest.mark.parametrize('dpi',[300,600])
@pytest.mark.parametrize('layout,page_type,count,box',[
    (PageLayoutSettings(paper_size='A4'),PageType.REGULAR,4,(595,842)),
    (PageLayoutSettings(paper_size='A4',paper_orientation='Landscape'),PageType.REGULAR,3,(842,595)),
    (PageLayoutSettings(paper_size='Custom',custom_page_width=12*unit_registry.inch,
                        custom_page_height=8*unit_registry.inch,paper_orientation='Portrait'),
     PageType.OVERSIZED,2,(864,576)),
])
def test_measured_pdf_outlines_and_references(qtbot,dpi,layout,page_type,count,box):
    geometry=build_page_geometry(layout,page_type,count)
    data=render_calibration_pdf(geometry,'None',output_dpi=dpi)
    reader=PdfReader(BytesIO(data))
    assert len(reader.pages)==1
    page=reader.pages[0]
    assert (float(page.mediabox.width),float(page.mediabox.height))==box
    paths=stroked_paths(page)
    outlines=[bounds(points) for points,closed in paths if closed]
    assert len(outlines)==count
    for observed,wanted in zip(outlines,svg_bounds(geometry)):
        assert observed==pytest.approx(wanted,abs=.01,rel=0)
    lines=[points for points,closed in paths if not closed and len(points)==2]
    rulers=[points for points in lines if max(bounds(points)[2:])>49]
    first=geometry.placements[0].trim_mm
    x0,y0=first.x+6,first.bottom-8
    expected=[[(x0,y0),(x0+50,y0)],[(x0,y0-50),(x0,y0)]]
    assert len(rulers)==2
    for actual,wanted in zip(rulers,expected):
        for point,target in zip(actual,wanted):
            assert point==pytest.approx(target,abs=.01,rel=0)
        assert max(bounds(actual)[2:])==pytest.approx(50,abs=.01,rel=0)
    if layout.paper_size=='A4' and layout.paper_orientation=='Portrait':
        assert outlines[0]==pytest.approx((10.3716666667,16.4253333333,63.0766666667,88.0533333333),abs=.01,rel=0)
    assert not page.get('/Resources',{}).get('/XObject')  # No artwork in disabled-profile output.


def test_static_scene_contract(qtbot):
    g=build_page_geometry(PageLayoutSettings(paper_size='A4'),PageType.REGULAR,1)
    scene,roots=_build_calibration_scene(g,'None')
    try:
        assert not roots
        rectangles=[i for i in scene.items() if isinstance(i,QGraphicsRectItem)]
        assert len(rectangles)==1
        r=g.placements[0].trim_px
        rect=rectangles[0].rect()
        assert (rect.x(),rect.y(),rect.width(),rect.height())==(r.x,r.y,r.width,r.height)
        assert rectangles[0].brush().style()==Qt.BrushStyle.NoBrush
        assert not rectangles[0].pen().isCosmetic()
        assert rectangles[0].pen().widthF()==pytest.approx(distance_to_px(.1*mm))
        labels=[i for i in scene.items() if isinstance(i,QGraphicsSimpleTextItem)]
        assert sorted(i.text() for i in labels)==sorted([
            'Slot 1','X 10.371667 mm','Y 16.425333 mm','W 63.076667 mm','H 88.053333 mm','50 mm','50 mm'])
        for label in labels:
            assert label.font().pixelSize()==25
            assert rect.contains(label.sceneBoundingRect())
        lines=[i for i in scene.items() if isinstance(i,QGraphicsLineItem)]
        assert len(lines)==8  # Two crosses, two rulers, four endpoint ticks.
        cx,cy=r.x+r.width/2,r.y+r.height/2
        cross=[i.line() for i in lines if i.line().length()==pytest.approx(distance_to_px(5*mm))]
        assert len(cross)==2
        for line in cross:
            assert ((line.x1()+line.x2())/2,(line.y1()+line.y2())/2)==pytest.approx((cx,cy))
        assert all(i.pen().capStyle()==Qt.PenCapStyle.FlatCap for i in lines)
        assert all(type(i).__name__!='CardItem' for i in scene.items())
    finally:
        scene.clear()


@pytest.mark.parametrize('style',['None','Bullseye','Cut marker'])
def test_fresh_existing_registration_and_empty_pdf(qtbot,style):
    g=build_page_geometry(PageLayoutSettings(paper_size='Letter',margin_left=8*mm,margin_top=9*mm),PageType.UNDETERMINED,0)
    profile=get_registration_profile(style)
    with patch.object(profile,'place_items',wraps=profile.place_items) as place:
        scene,roots=_build_calibration_scene(g,style)
        other,second=_build_calibration_scene(g,style)
    try:
        assert len(roots)==(0 if style=='None' else 3)
        assert len(place.call_args_list)==2
        assert place.call_args_list[0].args==(roots,g)
        assert place.call_args_list[0].kwargs=={'legacy_x_offset_px':0}
        assert not set(map(id,roots)) & set(map(id,second))
        assert all(i.scene() is scene for i in roots)
        assert not any(isinstance(i,QGraphicsSimpleTextItem) for i in scene.items())
        data=render_calibration_pdf(g,style)
        page=PdfReader(BytesIO(data)).pages[0]
        if style == 'None':
            assert not stroked_paths(page)
        else:
            content = page.get_contents().get_data()
            assert b'Do' in content or b'f' in content
    finally:
        scene.clear()
        other.clear()


@pytest.mark.parametrize('dpi',[0,-1,1.5,True])
def test_invalid_resolution(qtbot,dpi):
    g=build_page_geometry(PageLayoutSettings(paper_size='A4'),PageType.REGULAR,0)
    with pytest.raises(ValueError,match='positive integer'):
        render_calibration_pdf(g,'None',output_dpi=dpi)


@pytest.mark.parametrize('value',[0,-1,float('nan'),float('inf'),.000001])
def test_invalid_paper(qtbot,value):
    g=build_page_geometry(PageLayoutSettings(paper_size='A4'),PageType.REGULAR,0)
    with pytest.raises(ValueError):
        render_calibration_pdf(replace(g,sheet_width_mm=value),'None')


@pytest.mark.parametrize('failure',['paper','startup','render'])
def test_failure_cleanup(qtbot,failure):
    g=build_page_geometry(PageLayoutSettings(paper_size='A4'),PageType.REGULAR,1)
    painters=[]
    buffers=[]
    scenes=[]
    original_buffer=calibration.QBuffer
    original_scene=calibration._build_calibration_scene
    def buffer():
        item=original_buffer()
        buffers.append(item)
        return item
    def scene(*args):
        item,roots=original_scene(*args)
        scenes.append(item)
        return item,roots
    def painter():
        item=QPainter()
        painters.append(item)
        return item
    painter.RenderHint = QPainter.RenderHint
    target={'paper':(calibration.QPdfWriter,'setPageLayout'),
            'startup':(QPainter,'begin'),'render':(calibration,'_render_graphics_scene')}[failure]
    kwargs={'side_effect':RuntimeError('primary render failure')} if failure=='render' else {'return_value':False}
    with patch.object(calibration,'QBuffer',side_effect=buffer), \
            patch.object(calibration,'QPainter',new=painter), \
            patch.object(calibration,'_build_calibration_scene',side_effect=scene),patch.object(*target,**kwargs):
        with pytest.raises(RuntimeError):
            render_calibration_pdf(g,'None')
    assert all(not p.isActive() for p in painters)
    assert all(not b.isOpen() for b in buffers)
    assert all(not s.items() for s in scenes)


def test_primary_failure_survives_cleanup_failure(qtbot):
    g=build_page_geometry(PageLayoutSettings(paper_size='A4'),PageType.REGULAR,1)
    with patch.object(calibration,'_render_graphics_scene',side_effect=RuntimeError('primary')), \
            patch.object(calibration.QBuffer,'close',side_effect=RuntimeError('cleanup')):
        with pytest.raises(RuntimeError,match='primary'):
            render_calibration_pdf(g,'None')

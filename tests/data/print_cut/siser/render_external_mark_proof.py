"""M08B development proof ONLY: one fixed Letter, nine-card, zero-spacing job."""
import argparse
import json
import sys
from pathlib import Path

# Direct script invocation from the repository root, without installing a package.
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice, QMarginsF, QSizeF, Qt
from PySide6.QtGui import QBrush, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen
from PySide6.QtWidgets import QApplication, QGraphicsScene

from mtg_proxy_printer.model.page_geometry import build_page_geometry
from mtg_proxy_printer.model.page_layout import PageLayoutSettings
from mtg_proxy_printer.print import _render_graphics_scene
from mtg_proxy_printer.units_and_sizes import PageType, RESOLUTION, distance_to_px, unit_registry

RECORD = Path(__file__).resolve().parent / 'references/external-marks/nine-card-baseline/reference.json'


def render(record):
    """Reads numeric bars/style only; card layout always comes from PageGeometry."""
    if record['fixture_id'] != 'm08b-letter-nine-card-baseline' or record['requested_paper_mm'] != [215.9,279.4]:
        raise ValueError('This renderer is restricted to the measured M08B Letter fixture')
    measurement = record['measurement']
    if len(measurement['bars']) != 8 or len(measurement['contours']) != 9:
        raise ValueError('Expected fixed eight-bar/nine-card record')
    style = measurement['contours'][0]
    if not all(c['cap'] == c['join'] == 0 and c['color'] == 'black' and c['fill'] == 'none'
               and not c['dash'] and abs(c['stroke_width_mm']-style['stroke_width_mm']) < 1e-12
               for c in measurement['contours']):
        raise ValueError('Unsupported reference stroke appearance')
    if abs(style['stroke_width_mm']-.176389) > .00001:
        raise ValueError('Unsupported reference stroke width')
    mm = unit_registry.mm
    layout = PageLayoutSettings(paper_size='Letter',paper_orientation='Portrait',
        margin_left=0*mm,margin_top=0*mm,margin_right=0*mm,margin_bottom=0*mm,
        row_spacing=0*mm,column_spacing=0*mm,card_bleed=0*mm,
        print_registration_marks_style='None')
    geometry = build_page_geometry(layout,PageType.REGULAR,9)
    scene = QGraphicsScene()
    scene.setSceneRect(0,0,geometry.scene_width_px,geometry.scene_height_px)
    pen = QPen(Qt.GlobalColor.black)
    pen.setWidthF(distance_to_px(style['stroke_width_mm']*mm))
    pen.setCosmetic(False)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    pen.setMiterLimit(style['miter_limit'])
    for placement in geometry.placements:
        r = placement.trim_px
        scene.addRect(r.x,r.y,r.width,r.height,pen,QBrush(Qt.BrushStyle.NoBrush))
    for bar in measurement['bars']:
        if bar['color'] != 'black' or bar['stroke'] != 'none':
            raise ValueError('Unsupported reference bar appearance')
        values = [distance_to_px(v*mm) for v in bar['bounds_xywh_mm']]
        scene.addRect(*values,QPen(Qt.PenStyle.NoPen),QBrush(Qt.GlobalColor.black))
    buffer = QBuffer()
    writer = painter = None
    try:
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise RuntimeError('Cannot open proof buffer')
        writer = QPdfWriter(buffer)
        writer.setCreator('MTGProxyPrinter-Siser M08B development-only external-mark proof')
        dpi = round(RESOLUTION.magnitude)
        writer.setResolution(dpi)
        paper = QPageSize(QSizeF(geometry.sheet_width_mm,geometry.sheet_height_mm),
            QPageSize.Unit.Millimeter,'',QPageSize.SizeMatchPolicy.ExactMatch)
        layout = QPageLayout(paper,QPageLayout.Orientation.Portrait,QMarginsF(),QPageLayout.Unit.Millimeter)
        if not paper.isValid() or not writer.setPageLayout(layout):
            raise RuntimeError('Proof paper was rejected')
        full = writer.pageLayout().fullRect(QPageLayout.Unit.Point)
        if (full.width(),full.height()) != (612,792):
            raise RuntimeError('Proof paper is not Letter')
        painter = QPainter()
        if not painter.begin(writer):
            raise RuntimeError('Cannot start proof painter')
        painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering)
        _render_graphics_scene(scene,geometry,painter,dpi,dpi)
        if not painter.end():
            raise RuntimeError('Cannot finalize proof painter')
        painter = writer = None  # Finalize writer while buffer remains alive.
        return bytes(buffer.data())
    finally:
        primary_failure = sys.exc_info()[0] is not None
        cleanup_error = None
        try:
            if painter is not None and painter.isActive() and not painter.end():
                raise RuntimeError('Cannot end proof painter during cleanup')
        except Exception as error:
            cleanup_error = error
        painter = writer = None
        for cleanup in (buffer.close, scene.clear):
            try:
                cleanup()
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None and not primary_failure:
            raise cleanup_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite an existing proof; choose a new evidence run')
    app = QApplication([])
    data = render(json.loads(RECORD.read_text(encoding='utf-8')))
    with args.output.open('xb') as output:
        if output.write(data) != len(data):
            raise RuntimeError('Incomplete proof write')
    print(f'Wrote {args.output.resolve()} ({len(data)} bytes; Qt platform {app.platformName()})')


if __name__ == '__main__':
    main()

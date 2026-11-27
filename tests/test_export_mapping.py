"""Measure files written by the exporters, independently of their mapping helper."""
from unittest.mock import Mock, patch

import pytest
from pypdf import PdfReader
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QImage, QPixmap, QPainter

import mtg_proxy_printer.print as printing
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.document_controller.page_actions import ActionNewPage
from mtg_proxy_printer.model.page_layout import PageLayoutSettings
from mtg_proxy_printer.page_scene.page_scene import PageScene, RenderMode
from mtg_proxy_printer.settings import settings
from mtg_proxy_printer.units_and_sizes import CardSizes, RESOLUTION, unit_registry
from tests.helpers import create_card

mm = unit_registry.mm

class Progress(QObject):
    advance = Signal()

@pytest.fixture
def export_document(document_light):
    document_light.page_layout = PageLayoutSettings(paper_size='A4', draw_sharp_corners=True)
    return document_light


def card(color='red', size=CardSizes.REGULAR):
    image = QImage(size.as_qsize_px(), QImage.Format.Format_RGB888)
    image.fill(QColor(color))
    return create_card(color, size, pixmap=QPixmap.fromImage(image))


def pdf(document, path, dpi=300, rotate=False, index=0, limit=None):
    progress = Progress()
    with patch.dict(settings['export'], {'landscape-compatibility-workaround': str(rotate)}):
        writer = printing.PDFPrinter(document, str(path), progress.advance, document_index=index, pages_to_print=limit)
        writer.setResolution(dpi)
        writer.run()
    assert not writer.painter.isActive()
    return writer


def image_placements(page):
    """Only synthetic Qt image Do operations; pypdf resolves the surrounding CTM."""
    result = []
    height = float(page.mediabox.height)
    def visit(operator, operands, cm, tm):
        if operator != b'Do':
            return
        obj = page['/Resources']['/XObject'][operands[0]].get_object()
        if obj['/Subtype'] != '/Image':
            return
        a,b,c,d,e,f = cm
        points = [(e, f), (a+e,b+f), (c+e,d+f), (a+c+e,b+d+f)]
        result.append([(x*25.4/72, (height-y)*25.4/72) for x,y in points])
    page.extract_text(visitor_operand_before=visit)
    return result


def bounds(points):
    xs,ys=zip(*points)
    return min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)


def red_bounds(image, rgb=b"\xff\x00\x00"):
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    raw = bytes(image.constBits())
    stride = image.bytesPerLine()
    rows = []
    for y in range(image.height()):
        row = raw[y*stride:y*stride+image.width()*3]
        start,end = row.find(rgb),row.rfind(rgb)
        if start >= 0:
            rows.append((y,start//3,end//3))
    if not rows:
        return None
    return min(r[1] for r in rows),rows[0][0],max(r[2] for r in rows)+1,rows[-1][0]+1

PAPERS = [
    ('A4','Portrait',None,(210,297),(2480,3508),(595,842)),
    ('A4','Landscape',None,(297,210),(3508,2480),(842,595)),
    ('Letter','Portrait',None,(215.9,279.4),(2550,3300),(612,792)),
    ('Custom','Portrait',(12*unit_registry.inch,8*unit_registry.inch),(304.8,203.2),(3600,2400),(864,576)),
    ('Custom','Landscape',(304.8*mm,203.2*mm),(304.8,203.2),(3600,2400),(864,576)),
    ('Custom','Landscape',(210.6*mm,297.6*mm),(210.6,297.6),(2487,3515),(597,844)),
]

@pytest.mark.parametrize('name,orientation,custom,physical,pixels,points', PAPERS)
def test_paper_files(export_document,tmp_path,name,orientation,custom,physical,pixels,points):
    d=export_document
    d.page_layout.paper_size=name
    d.page_layout.paper_orientation=orientation
    if custom:
        d.page_layout.custom_page_width,d.page_layout.custom_page_height=custom
    d.apply(ActionAddCard(card(),1))
    pdf(d,tmp_path/'paper.pdf')
    page=PdfReader(tmp_path/'paper.pdf').pages[0]
    assert (float(page.mediabox.width),float(page.mediabox.height)) == points
    scene=PageScene(d,RenderMode.ON_PAPER|RenderMode.FILE_EXPORT)
    assert (scene.geometry.sheet_width_mm,scene.geometry.sheet_height_mm) == pytest.approx(physical)
    trim = scene.geometry.placements[0].trim_mm
    assert bounds(image_placements(page)[0]) == pytest.approx(
        (trim.x, trim.y, trim.width, trim.height), abs=.01)
    task=printing.PNGRenderer(None,d,str(tmp_path/'paper.png'))
    task.run()
    image=QImage(str(tmp_path/'paper-1.png'))
    assert (image.width(),image.height()) == pixels
    assert image.dotsPerMeterX() == image.dotsPerMeterY() == 11811
    assert image.devicePixelRatio() == 1

@pytest.mark.parametrize('dpi',[300,600])
def test_independent_a4_card_coordinates(export_document,tmp_path,dpi):
    d=export_document
    d.apply(ActionAddCard(card(),1))
    pdf(d,tmp_path/'card.pdf',dpi)
    page=PdfReader(tmp_path/'card.pdf').pages[0]
    assert bounds(image_placements(page)[0]) == pytest.approx((10.3716667,16.4253333,63.0766667,88.0533333),abs=.01)
    task=printing.PNGRenderer(None,d,str(tmp_path/'card.png'))
    task._output_dpi=dpi
    task.run()
    image=QImage(str(tmp_path/'card-1.png'))
    expected=(122.5,194,867.5,1234)
    assert red_bounds(image) == pytest.approx(tuple(v*dpi/300 for v in expected),abs=1)
    assert image.dotsPerMeterX() == image.dotsPerMeterY() == round(dpi/.0254)
    assert RESOLUTION.magnitude == 300

@pytest.mark.parametrize('rotate',[False,True])
@pytest.mark.parametrize('custom',[False,True])
def test_rotation_and_target_pages(export_document,tmp_path,rotate,custom):
    d=export_document
    d.page_layout.paper_orientation='Landscape'
    if custom:
        d.page_layout.paper_size='Custom'
        d.page_layout.custom_page_width=12*unit_registry.inch
        d.page_layout.custom_page_height=8*unit_registry.inch
    d.page_layout.margin_left=13*mm
    d.page_layout.margin_top=7*mm
    d.apply(ActionAddCard(card(),1))
    d.apply(ActionNewPage(count=3,content=[[card('blue',CardSizes.OVERSIZED)]*2,[card()]*3,[]]))
    selected=d.currently_edited_page
    writer=pdf(d,tmp_path/'pages.pdf',rotate=rotate)
    reader=PdfReader(tmp_path/'pages.pdf')
    assert len(reader.pages)==4
    assert d.currently_edited_page is selected
    assert [len(image_placements(p)) for p in reader.pages] == [1,2,3,0]
    for number,page in enumerate(reader.pages):
        writer._switch_to_page(number)
        g=writer.scene.geometry
        box=((576,864) if rotate else (864,576)) if custom else ((595,842) if rotate else (842,595))
        assert (float(page.mediabox.width),float(page.mediabox.height)) == box
        for actual,placement in zip(image_placements(page),g.placements):
            r=placement.trim_mm
            expected=(g.sheet_height_mm-r.y-r.height,r.x,r.height,r.width) if rotate else (r.x,r.y,r.width,r.height)
            assert bounds(actual) == pytest.approx(expected,abs=.01)
            # Image unit-square x edge runs down after a clockwise turn, right without it.
            dx,dy=actual[1][0]-actual[0][0],actual[1][1]-actual[0][1]
            assert (dx,dy) == pytest.approx((0,r.width) if rotate else (r.width,0),abs=.01)
            dx,dy=actual[2][0]-actual[0][0],actual[2][1]-actual[0][1]
            # Qt maps the PDF image unit-square Y edge from bottom to top.
            assert (dx,dy) == pytest.approx((r.height,0) if rotate else (0,-r.height),abs=.01)
    task=printing.PNGRenderer(None,d,str(tmp_path/'pages.png'))
    task.run()
    assert sorted(p.name for p in tmp_path.glob('pages-*.png')) == [f'pages-{i}.png' for i in range(1,5)]
    assert red_bounds(QImage(str(tmp_path/'pages-4.png'))) is None
    assert red_bounds(QImage(str(tmp_path/'pages-3.png'))) is not None
    assert QImage(str(tmp_path/'pages-1.png')).size().width()==(3600 if custom else 3508)
    for number in range(3):
        writer._switch_to_page(number)
        trims=[p.trim_px for p in writer.scene.geometry.placements]
        expected=(min(r.x for r in trims),min(r.y for r in trims),
                  max(r.right for r in trims),max(r.bottom for r in trims))
        image=QImage(str(tmp_path/f'pages-{number+1}.png'))
        rgb=b'\x00\x00\xff' if number==1 else b'\xff\x00\x00'
        assert red_bounds(image,rgb) == pytest.approx(expected,abs=1)

@pytest.mark.parametrize('transparent',[False,True])
def test_preferences_alpha_and_split(export_document,tmp_path,transparent):
    d=export_document
    d.apply(ActionAddCard(card(),1))
    d.apply(ActionNewPage(count=2,content=[[card('blue')],[]]))
    with patch.dict(settings['printer'],{'horizontal-offset':'10 mm','borderless-printing':'False','landscape-compatibility-workaround':'True'}), patch.dict(settings['export'],{'png-background-color':'#00000000' if transparent else '#ffffffff'}):
        assert PageScene(d,RenderMode.ON_PAPER).x_offset == 118
        assert PageScene(d,RenderMode.ON_PAPER|RenderMode.FILE_EXPORT).x_offset == 0
        pdf(d,tmp_path/'split.pdf',index=0,limit=2)
        pdf(d,tmp_path/'split.pdf',index=1,limit=2)
        assert [len(PdfReader(p).pages) for p in sorted(tmp_path.glob('split-*.pdf'))] == [2,1]
        page=PdfReader(tmp_path/'split-1.pdf').pages[0]
        assert bounds(image_placements(page)[0])[0] == pytest.approx(10.3716667,abs=.01)
        task=printing.PNGRenderer(None,d,str(tmp_path/'alpha.png'))
        task.run()
    image=QImage(str(tmp_path/'alpha-1.png'))
    assert image.hasAlphaChannel() == transparent
    assert image.pixelColor(0,0).alpha() == (0 if transparent else 255)
    assert red_bounds(image) == pytest.approx((122.5,194,867.5,1234),abs=1)


def test_failure_cleanup(export_document,tmp_path,monkeypatch):
    d=export_document
    d.apply(ActionAddCard(card(),1))
    progress=Progress()
    advanced=Mock()
    progress.advance.connect(advanced)
    writer=printing.PDFPrinter(d,str(tmp_path/'fail.pdf'),progress.advance)
    with patch.object(PageScene,'render',side_effect=RuntimeError('synthetic rejected geometry')):
        with pytest.raises(RuntimeError,match='page 1.*rejected geometry'):
            writer.run()
    assert not writer.painter.isActive()
    advanced.assert_not_called()
    task=printing.PNGRenderer(None,d,str(tmp_path/'fail.png'))
    acquired,released,completed,error=Mock(),Mock(),Mock(),Mock()
    task.ui_lock_acquire.connect(acquired)
    task.ui_lock_release.connect(released)
    task.task_completed.connect(completed)
    task.error_occurred.connect(error)
    painters=[]
    def make_painter():
        painter=QPainter()
        painters.append(painter)
        return painter
    with patch.object(printing,'QPainter',side_effect=make_painter), patch.object(PageScene,'render',side_effect=RuntimeError('synthetic rejected geometry')),patch.object(task,'_compress_single_image') as compress:
        task.run()
    acquired.assert_called_once()
    released.assert_called_once()
    completed.assert_called_once()
    assert 'rejected geometry' in error.call_args.args[0]
    compress.assert_not_called()
    assert not task._running
    assert painters and all(not painter.isActive() for painter in painters)
    assert not list(tmp_path.glob('fail-*.png'))


def test_helper_restores_active_painter(export_document):
    export_document.page_layout.paper_orientation='Landscape'
    scene=PageScene(export_document,RenderMode.ON_PAPER|RenderMode.FILE_EXPORT)
    image=QImage(100,100,QImage.Format.Format_RGB888)
    painter=QPainter(image)
    painter.translate(3,4)
    original=painter.transform()
    try:
        with patch.object(scene,'render',side_effect=RuntimeError('failed')):
            with pytest.raises(RuntimeError):
                printing._render_export_page(scene,painter,600,True)
        assert painter.transform()==original
    finally:
        painter.end()


def test_unresolved_geometry_and_started_encoder_cleanup(export_document,tmp_path):
    d=export_document
    d.apply(ActionAddCard(card(),1))
    progress=Progress()
    advanced=Mock()
    progress.advance.connect(advanced)
    writer=printing.PDFPrinter(d,str(tmp_path/'invalid.pdf'),progress.advance)
    writer.scene.require_geometry_ready=Mock(side_effect=RuntimeError('unresolved geometry'))
    with pytest.raises(RuntimeError,match='unresolved geometry'):
        writer.run()
    assert not writer.painter.isActive()
    advanced.assert_not_called()
    # A previously submitted page must finish; a rejected second page must never be submitted.
    d.apply(ActionNewPage(content=[[card()]]))
    task=printing.PNGRenderer(None,d,str(tmp_path/'partial.png'))
    errors=[]
    success=[]
    task.error_occurred.connect(errors.append)
    task.advance_progress.connect(lambda: success.append(True))
    original=printing._render_export_page
    calls=0
    def fail_second(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:
            raise RuntimeError('second page rejected')
        return original(*args,**kwargs)
    with patch.object(printing,'_render_export_page',side_effect=fail_second):
        task.run()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert len(errors)==1 and 'second page rejected' in errors[0]
    assert success==[True]
    assert (tmp_path/'partial-1.png').exists()
    assert not (tmp_path/'partial-2.png').exists()


def test_pdf_progress_and_dialog_failure(export_document,tmp_path):
    from types import SimpleNamespace
    from mtg_proxy_printer.ui.dialogs import SavePDFDialog
    tasks=[]
    main=SimpleNamespace(progress_bar_manager=SimpleNamespace(add_task=tasks.append),on_error_occurred=Mock())
    class ExportParent(QObject):
        def parent(self):
            return main
    parent=ExportParent()
    with patch.object(printing.PDFPrinter,'run',side_effect=RuntimeError('render rejected')):
        with pytest.raises(RuntimeError,match='render rejected'):
            printing.export_pdf(export_document,str(tmp_path/'fail.pdf'),parent)
    assert len(tasks)==1 and not tasks[0]._running
    fake=SimpleNamespace(document=export_document,selectedFiles=lambda:[str(tmp_path/'dialog.pdf')],
                         parent=lambda:main,request_run_async_task=Mock())
    with patch.object(printing,'export_pdf',side_effect=RuntimeError('render rejected')):
        SavePDFDialog.on_accept(fake)
    main.on_error_occurred.assert_called_once_with('render rejected')
    fake.request_run_async_task.emit.assert_not_called()


def test_portrait_unchanged_with_rotation_enabled(export_document,tmp_path):
    export_document.apply(ActionAddCard(card(),1))
    pdf(export_document,tmp_path/'portrait.pdf',600,True)
    page=PdfReader(tmp_path/'portrait.pdf').pages[0]
    assert (float(page.mediabox.width),float(page.mediabox.height)) == (595,842)
    assert bounds(image_placements(page)[0]) == pytest.approx((10.3716667,16.4253333,63.0766667,88.0533333),abs=.01)


def test_png_two_digit_numbering(export_document,tmp_path):
    d=export_document
    d.apply(ActionNewPage(count=9))
    task=printing.PNGRenderer(None,d,str(tmp_path/'number.png'))
    task._output_dpi=30  # Small blank files suffice to verify unchanged numbering.
    task.run()
    assert sorted(p.name for p in tmp_path.glob('number-*.png')) == [f'number-{i:02}.png' for i in range(1,11)]


def test_nominal_edge_clipping_and_guides(export_document,tmp_path):
    from PySide6.QtCore import Qt, QSize
    d=export_document
    d.page_layout.cut_marker_style='Solid'
    d.page_layout.cut_marker_width=.2*mm
    d.apply(ActionAddCard(card(),1))
    pdf(d,tmp_path/'guides.pdf')
    task=printing.PNGRenderer(None,d,str(tmp_path/'guides.png'))
    task.run()
    scene=PageScene(d,RenderMode.ON_PAPER|RenderMode.FILE_EXPORT)
    g=scene.geometry
    from mtg_proxy_printer.units_and_sizes import distance_to_px
    w,h=distance_to_px(g.sheet_width_mm*mm),distance_to_px(g.sheet_height_mm*mm)
    scene.addRect(w-10,h-10,20,20,Qt.PenStyle.NoPen,QColor('red'))
    image=printing.PNGRenderer._create_image(QSize(round(w),round(h)),QColor('white'),11811)
    painter=QPainter(image)
    try:
        printing._render_export_page(scene,painter,300)
    finally:
        painter.end()
    image.save(str(tmp_path/'clipped.png'))
    loaded=QImage(str(tmp_path/'clipped.png'))
    assert loaded.pixelColor(loaded.width()-1,loaded.height()-1)==QColor('red')
    assert loaded.pixelColor(loaded.width()-12,loaded.height()-12)==QColor('white')


def test_pdf_startup_and_paper_setup_failures(export_document,tmp_path):
    progress=Progress()
    success=Mock()
    progress.advance.connect(success)
    writer=printing.PDFPrinter(export_document,str(tmp_path/'startup.pdf'),progress.advance)
    writer.painter=Mock()
    writer.painter.begin.return_value=False
    writer.painter.isActive.return_value=False
    with pytest.raises(RuntimeError,match='Cannot start PDF painter'):
        writer.run()
    success.assert_not_called()
    with patch.object(printing.PDFPrinter,'setPageLayout',return_value=False):
        with pytest.raises(RuntimeError,match='rejected the requested paper'):
            printing.PDFPrinter(export_document,str(tmp_path/'paper-failure.pdf'),progress.advance)


@pytest.mark.parametrize("failure", ["second-page", "finalize"])
def test_pdf_replacement_is_atomic(export_document, tmp_path, failure):
    d = export_document
    destination = tmp_path / "existing.pdf"
    pdf(d, destination)
    original = destination.read_bytes()
    d.apply(ActionNewPage(content=[[card()]]))
    progress = Progress()
    writer = printing.PDFPrinter(d, str(destination), progress.advance)
    if failure == "second-page":
        fault = patch.object(printing, "_render_export_page", side_effect=[None, RuntimeError("page failure")])
    else:
        end = writer.painter.end
        def failed_end():
            end()
            return False
        fault = patch.object(writer.painter, "end", side_effect=failed_end)
    with fault, pytest.raises(RuntimeError):
        writer.run()
    assert destination.read_bytes() == original
    assert list(tmp_path.iterdir()) == [destination]
    pdf(d, destination)
    assert len(PdfReader(destination).pages) == 2
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("blocked", [True, False])
def test_png_dialog_accounts_only_complete_snapshot(export_document, tmp_path, blocked):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication, QWidget
    from mtg_proxy_printer.ui.dialogs import SavePNGDialog
    from mtg_proxy_printer.async_tasks.print_count_updater import PrintCountUpdater

    d = export_document
    from threading import Event
    from tests.page_scene.test_prepared_scene import ordinary
    from tests.helpers import fill_card_database_with_json_cards, load_json
    from mtg_proxy_printer.model.carddb import CardDatabase

    catalog = CardDatabase(tmp_path / "counts.sqlite", register_exit_hooks=False)
    original = load_json("regular_english_card")
    changed_id = "00000000-0000-0000-0000-000000000002"
    fill_card_database_with_json_cards(catalog, [original, dict(original, id=changed_id)])
    d.card_db.db_path = catalog.db_path
    selected = ordinary()
    selected.scryfall_id = original["id"]
    d.apply(ActionAddCard(selected, 1))
    d.apply(ActionNewPage())
    second = tmp_path / "out-2.png"
    if blocked:
        second.mkdir()
    parent = QWidget()
    tasks = []
    submit = Mock()
    submit.emit.side_effect = tasks.append
    parent.request_run_async_task = submit
    dialog = SimpleNamespace(document=d, selectedFiles=lambda: [str(tmp_path/'out.png')],
                             parent=lambda: parent, request_run_async_task=submit)
    SavePNGDialog.on_accept(dialog)
    renderer = tasks.pop(0)
    renderer._output_dpi = 30
    errors, advances = [], []
    renderer.error_occurred.connect(errors.append)
    renderer.advance_progress.connect(lambda: advances.append(1))
    expected = {(key.scryfall_id, key.is_front) for key in d.get_all_image_keys_in_document()}
    assert expected
    entered, release = Event(), Event()
    encode = renderer._compress_single_image

    def held_encode(image, path):
        entered.set()
        assert release.wait(5)
        return encode(image, path)

    def edit_document():
        try:
            assert entered.wait(5)
            selected.scryfall_id = changed_id
        finally:
            release.set()

    renderer.ui_lock_release.connect(edit_document)
    with patch.object(renderer, "_compress_single_image", side_effect=held_encode):
        renderer.run()
    QApplication.processEvents()
    assert (tmp_path / 'out-1.png').is_file()
    if blocked:
        assert len(errors) == 1 and second.name in errors[0]
        assert advances == [1] and not tasks
    else:
        assert not errors and advances == [1, 1]
        assert len(tasks) == 1 and isinstance(tasks[0], PrintCountUpdater)
        assert set(tasks[0].data) == expected
        tasks[0].run()
    counts = dict(catalog.db.execute('SELECT scryfall_id,usage_count FROM Printing JOIN PrintingFace USING(printing_id)'))
    assert counts == {original['id']: int(not blocked), changed_id: 0}
    catalog.db.close()


def test_queued_png_rejects_edit_after_save(export_document, tmp_path, qtbot):
    from threading import Event
    from types import SimpleNamespace
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QWidget
    from mtg_proxy_printer.ui.dialogs import SavePNGDialog
    from tests.page_scene.test_prepared_scene import ordinary

    document = export_document
    document.apply(ActionAddCard(ordinary(), 1))
    pool = QThreadPool(maxThreadCount=1)
    started, release, done = Event(), Event(), Event()
    def busy():
        started.set()
        release.wait(5)
    pool.start(busy)
    assert started.wait(3)
    parent = QWidget()
    usage = Mock()
    parent.request_run_async_task = usage
    errors = []
    def enqueue(renderer):
        renderer.error_occurred.connect(errors.append)
        renderer.task_completed.connect(done.set)
        renderer._output_dpi = 30
        pool.start(renderer.run)
    dialog = SimpleNamespace(document=document, parent=lambda: parent, selectedFiles=lambda: [str(tmp_path/"out.png")],
                             request_run_async_task=SimpleNamespace(emit=enqueue))
    try:
        SavePNGDialog.on_accept(dialog)
        second = ordinary()
        second.scryfall_id = "00000000-0000-0000-0000-000000000002"
        document.apply(ActionAddCard(second, 1))
    finally:
        release.set()
    qtbot.waitUntil(done.is_set, timeout=5000)
    assert pool.waitForDone(3000)
    assert len(errors) == 1 and "changed" in errors[0].lower()
    assert not list(tmp_path.glob("out-*.png"))
    usage.emit.assert_not_called()


@pytest.mark.parametrize("when", ["between-pages", "during-second-capture"])
def test_pending_add_during_png_capture(export_document, tmp_path, qtbot, monkeypatch, when):
    from threading import Event, Thread
    from types import SimpleNamespace
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget
    from mtg_proxy_printer.async_tasks.image_downloader import SingleDownloadTask
    from mtg_proxy_printer.model.carddb import CardDatabase
    from mtg_proxy_printer.ui.dialogs import SavePNGDialog
    from tests.helpers import fill_card_database_with_json_cards, load_json

    document = export_document
    original = load_json("regular_english_card")
    changed_id = "00000000-0000-0000-0000-000000000002"
    catalog = CardDatabase(tmp_path / "usage.sqlite", register_exit_hooks=False)
    fill_card_database_with_json_cards(catalog, [original, dict(original, id=changed_id)])
    document.card_db.db_path = catalog.db_path
    document.image_db.db_path = tmp_path / "images"
    red, green = card("red"), card("lime")
    red.oracle_id = green.oracle_id = original["oracle_id"]
    red.scryfall_id, green.scryfall_id = original["id"], changed_id
    document.apply(ActionAddCard(red, 1))
    document.apply(ActionNewPage())
    pending = SingleDownloadTask(document.image_db, ActionAddCard(green, target_page=1))
    pending.request_action.connect(document.apply, Qt.ConnectionType.BlockingQueuedConnection)
    entered, release = Event(), Event()
    def cached_image(key):
        entered.set()
        assert release.wait(8)
        return green.image_file
    monkeypatch.setattr(pending, "_load_from_memory", cached_image)
    worker = Thread(target=pending.run, daemon=True)
    worker.start()
    assert entered.wait(3)

    parent = QWidget()
    tasks, errors = [], []
    parent.request_run_async_task = SimpleNamespace(emit=tasks.append)
    dialog = SimpleNamespace(document=document, parent=lambda: parent,
                             selectedFiles=lambda: [str(tmp_path/"sheet.png")],
                             request_run_async_task=parent.request_run_async_task)
    SavePNGDialog.on_accept(dialog)
    renderer = tasks.pop()
    renderer.error_occurred.connect(errors.append)
    renderer._output_dpi = 48
    def finish_add():
        release.set()
        qtbot.waitUntil(lambda: not worker.is_alive(), timeout=4000)
        assert document.pages[1][0].card is green

    start_encoder = printing.QThreadPool.start
    def submit_page(pool, job):
        start_encoder(pool, job)
        if when == "between-pages" and not release.is_set():
            finish_add()
    render_page = printing._render_export_page
    pages = 0
    def paint(*args):
        nonlocal pages
        pages += 1
        if when == "during-second-capture" and pages == 2:
            finish_add()
        render_page(*args)

    with patch.object(printing.QThreadPool, "start", submit_page), patch.object(printing, "_render_export_page", paint):
        renderer.run()
    QApplication.processEvents()
    assert not worker.is_alive()
    for usage in tasks:
        usage.run()
    counts = dict(catalog.db.execute("SELECT scryfall_id,usage_count FROM Printing JOIN PrintingFace USING(printing_id)"))
    catalog.db.close()
    first = QImage(str(tmp_path/"sheet-1.png"))
    assert not first.isNull() and first.pixelColor(50, 70) == QColor("red")
    second = tmp_path / "sheet-2.png"
    second_color = QImage(str(second)).pixelColor(50, 70).name() if second.exists() else None
    assert (second.exists(), second_color, counts, len(tasks)) == (
        False, None, {original["id"]: 0, changed_id: 0}, 0)
    assert len(errors) == 1 and "changed" in errors[0].lower()

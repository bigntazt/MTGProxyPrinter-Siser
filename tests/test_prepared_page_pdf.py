"""Real prepared current-page PDF capture, freshness and measured output."""
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication
from pypdf import PdfReader

from mtg_proxy_printer import prepared_page_pdf as pdf
from mtg_proxy_printer import prepared_page_output as output
from mtg_proxy_printer import image_processing_pillow
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.document_controller.page_actions import ActionNewPage
from mtg_proxy_printer.model.page_geometry import build_page_geometry
from mtg_proxy_printer.settings import settings
from mtg_proxy_printer.units_and_sizes import CardSizes, unit_registry
from tests.ui.test_image_preparation_preview_adoption import PreviewHarness
from tests.page_scene.test_prepared_scene import ordinary
from tests.test_image_source import encoded, write_original
from tests.test_export_mapping import image_placements, bounds
from tests.test_siser_artwork_output import assert_bars, configure, transformed


@pytest.fixture
def preview(tmp_path, qtbot, monkeypatch):
    from mtg_proxy_printer.ui import main_window
    monkeypatch.setattr(main_window, "UI_LOCK_SEMAPHORE", 0)
    h = PreviewHarness(tmp_path, qtbot)
    try:
        yield h
    finally:
        h.cleanup()


def capture(h, **kwargs):
    return pdf.capture_prepared_page_pdf(h.document, h.session, mode=h.dock.mode,
                                       target_ppi=h.dock.target_ppi, **kwargs)


def start(h, mode="lanczos", ppi=600, source=None, size=CardSizes.REGULAR):
    if size == CardSizes.REGULAR:
        card = h.add(source)
    else:
        card = ordinary(size)
        h.document.apply(ActionAddCard(card, 1))
        write_original(h.image_db.db_path, card, source or encoded(104, 149))
    h.open()
    h.dock.target_combo.setCurrentIndex(h.dock.target_combo.findData(ppi))
    h.dock.mode_combo.setCurrentIndex(1 if mode == "original" else 2)
    h.ready()
    return card


def image_sizes(page):
    return {(int(obj.get_object()['/Width']), int(obj.get_object()['/Height']))
            for obj in page['/Resources'].get('/XObject', {}).values()
            if obj.get_object().get('/Subtype') == '/Image'}


@pytest.mark.parametrize("ppi,paper,rotate", [(600,"Letter",False),(900,"Letter",False),
                                             (1200,"Letter",False),(600,"A4",False),(600,"A4",True)])
def test_real_targets_native_pdf_and_shared_physical_mapping(preview, ppi, paper, rotate, monkeypatch):
    h = preview
    configure(h.document, paper, card_bleed=1*unit_registry.mm, row_spacing=2*unit_registry.mm,
              column_spacing=3*unit_registry.mm)
    start(h, ppi=ppi)
    geometry = h.scene.geometry
    tasks = len(h.tasks)
    scenes = []
    cls = output.PageScene
    def observed(*args, **kwargs):
        scene = cls(*args, **kwargs)
        scenes.append(scene)
        return scene
    with patch.object(output, 'PageScene', side_effect=observed), \
            patch.object(QCoreApplication, 'processEvents', side_effect=AssertionError('No event boundary')):
        result = capture(h, landscape_workaround=rotate)
    assert scenes and not shiboken6.isValid(scenes[0])
    assert len(h.tasks) == tasks and h.scene.geometry == geometry
    assert result.target_ppi == ppi and dict(result.outcome_counts) == {'generated_cached': 2}
    assert len(result.image_keys) == 1
    reader = PdfReader(BytesIO(result.data))
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert (745*(ppi//300), 1040*(ppi//300)) in image_sizes(page)
    expected_paper = (612,792) if paper == 'Letter' else ((595,842) if rotate else (842,595))
    assert (float(page.mediabox.width), float(page.mediabox.height)) == expected_paper
    artwork = [points for points in image_placements(page) if min(bounds(points)[2:]) > 50]
    assert len(artwork) == len(geometry.placements)
    for observed, placement in zip(artwork, geometry.placements):
        assert bounds(observed) == pytest.approx(transformed(placement.trim_mm, geometry, rotate), abs=.01, rel=0)
    assert_bars(page, geometry, rotate, tolerance=.001)
    if ppi == 900:
        with patch.object(image_processing_pillow.PillowRuntime, 'produce_png', side_effect=AssertionError('Warm reuse')):
            h.dock.refresh_button.click()
            assert dict(h.ready().completion.outcome_counts) == {'cache_hit': 2}
        warm = capture(h)
        assert warm.target_ppi == 900 and dict(warm.outcome_counts) == {'cache_hit': 2}
        assert (2235,3120) in image_sizes(PdfReader(BytesIO(warm.data)).pages[0])


@pytest.mark.parametrize('fallback',[False,True])
def test_native_original_and_fallback_at_middle_target(preview, fallback, monkeypatch):
    h = preview
    if fallback:
        def unavailable():
            raise image_processing_pillow.ProcessingFailure('provider_unavailable','Pillow unavailable fixture')
        monkeypatch.setattr(image_processing_pillow,'discover_pillow_runtime',unavailable)
    start(h, mode='lanczos' if fallback else 'original', ppi=900, source=encoded(73,109))
    result = capture(h)
    assert (73,109) in image_sizes(PdfReader(BytesIO(result.data)).pages[0])
    assert dict(result.outcome_counts) == {('fallback_original' if fallback else 'original'):2}
    assert result.target_ppi == 900


def test_oversized_partial_page(preview):
    h = preview
    start(h, ppi=900, size=CardSizes.OVERSIZED)
    result = capture(h)
    page = PdfReader(BytesIO(result.data)).pages[0]
    assert (3120,4470) in image_sizes(page)
    assert len(image_placements(page)) == 1
    assert bounds(image_placements(page)[0]) == pytest.approx(transformed(h.scene.geometry.placements[0].trim_mm,h.scene.geometry), abs=.01,rel=0)


@pytest.mark.parametrize('phase',['off','queued','preparing','applying','cancelled','failed','closed'])
def test_nonready_states_reject(preview, phase, monkeypatch):
    h = preview
    start(h, mode='original')
    status = replace(h.session.status(), phase=phase)
    monkeypatch.setattr(h.session, 'status', lambda: status)
    with pytest.raises(RuntimeError, match='Ready'):
        capture(h)


@pytest.mark.parametrize('fault',['missing','null','generation','page','geometry','completion'])
def test_capture_changes_abort(preview, fault, monkeypatch):
    h = preview
    start(h, mode='original')
    resolver = h.session.resolve
    if fault == 'missing':
        monkeypatch.setattr(h.session, 'resolve', lambda index: None)
    elif fault == 'null':
        from PySide6.QtGui import QPixmap
        monkeypatch.setattr(h.session, 'resolve', lambda index: replace(resolver(index),pixmap=QPixmap()))
    elif fault == 'completion':
        status = h.session.status()
        monkeypatch.setattr(h.session, 'status',lambda:replace(status,completion=replace(status.completion,generation=status.generation-1)))
    else:
        render = pdf._render_export_page
        def mutate(*args):
            render(*args)
            if fault == 'generation': h.session.refresh()
            elif fault == 'geometry': h.document.page_layout.row_spacing += 1*unit_registry.mm
            else:
                h.document.apply(ActionNewPage(content=[[]]))
                h.document.set_currently_edited_page(h.document.pages[-1])
        monkeypatch.setattr(pdf, '_render_export_page',mutate)
    with pytest.raises(RuntimeError):
        capture(h)


def test_source_freshness_and_completed_bytes_survive_later_edits(preview):
    h = preview
    card = start(h,mode='original',source=encoded(73,109))
    first = capture(h)
    write_original(h.image_db.db_path,card,encoded(81,117))
    assert (73,109) in image_sizes(PdfReader(BytesIO(capture(h).data)).pages[0])
    h.dock.refresh_button.click()
    h.ready()
    second = capture(h)
    assert (81,117) in image_sizes(PdfReader(BytesIO(second.data)).pages[0])
    h.dock.set_off()
    h.document.page_layout.row_spacing += 5*unit_registry.mm
    assert (73,109) in image_sizes(PdfReader(BytesIO(first.data)).pages[0])
    assert not any(hasattr(value, 'isValid') for value in vars(first).values())


@pytest.mark.parametrize('failure',['begin','end','render'])
def test_render_failures_release_scene_and_keep_primary_error(preview, failure, monkeypatch):
    h=preview
    start(h,mode='original')
    scenes=[]
    cls=output.PageScene
    def make(*args,**kwargs):
        scene=cls(*args,**kwargs); scenes.append(scene); return scene
    monkeypatch.setattr(output,'PageScene',make)
    if failure == 'render':
        monkeypatch.setattr(pdf,'_render_export_page',Mock(side_effect=RuntimeError('primary rendering error')))
    else:
        painter=Mock()
        painter.begin.return_value=failure != 'begin'
        painter.end.return_value=False
        painter.isActive.return_value=False
        monkeypatch.setattr(pdf,'QPainter',Mock(return_value=painter,RenderHint=pdf.QPainter.RenderHint))
        monkeypatch.setattr(pdf,'_render_export_page',lambda *args: None)
    with pytest.raises(RuntimeError): capture(h)
    assert scenes and not shiboken6.isValid(scenes[0])


def test_recorded_standard_only_page_can_be_exported(preview):
    h = preview
    card = h.add()
    write_original(h.image_db.db_path, card, encoded(73,109)).unlink()
    h.open()
    h.dock.mode_combo.setCurrentIndex(1)
    status = h.ready()
    assert dict(status.completion.outcome_counts) == {'standard':2}
    result = capture(h)
    assert dict(result.outcome_counts) == {'standard':2}
    assert (745,1040) in image_sizes(PdfReader(BytesIO(result.data)).pages[0])


def test_state_change_during_lookup_aborts_before_output_scene(preview,monkeypatch):
    h = preview
    start(h,mode='original')
    resolve = h.session.resolve
    def changed(index):
        value = resolve(index)
        h.session.refresh()
        return value
    monkeypatch.setattr(h.session,'resolve',changed)
    with patch.object(output,'PageScene') as scene:
        with pytest.raises(RuntimeError): capture(h)
        scene.assert_not_called()

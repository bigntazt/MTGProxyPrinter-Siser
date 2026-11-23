"""PNG action data, source/output distinction and shared review/save ownership."""
from unittest.mock import Mock, patch

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QFileDialog

from mtg_proxy_printer.ui import main_window, dialogs
from mtg_proxy_printer.ui.prepared_page_png_dialog import PreparedPagePNGDialog
from mtg_proxy_printer.prepared_page_png import PreparedPagePNG
from tests.test_prepared_page_pdf import preview, start, capture
from tests.ui.test_prepared_page_pdf_dialog import choose
from tests.test_image_source import encoded


def small_result(h,ppi=600):
    common=vars(capture(h)).copy()
    common['data']=encoded(1,1)
    return PreparedPagePNG(**common,output_ppi=ppi,pixel_width=round(210*ppi/25.4),pixel_height=round(297*ppi/25.4))


@pytest.mark.parametrize('ppi',[600,900,1200])
def test_numeric_actions_available_for_original_without_changing_source_target(preview,ppi):
    h=preview; window=h.window
    actions=window._prepared_png_actions
    assert [action.data() for action in actions]==[600,900,1200]
    assert [action.text() for action in actions]==['600 PPI','900 PPI','1200 PPI']
    assert window.ui.menu_export_prepared_png.menuAction() in window.ui.menu_file.actions()
    assert not any(action.isEnabled() for action in actions)
    start(h,mode='original',ppi=900)
    assert all(action.isEnabled() for action in actions) and not h.dock.target_combo.isEnabled()
    assert window.ui.menu_export_prepared_png.isEnabled()
    assert window.ui.menu_export_prepared_png.menuAction().isEnabled()
    result=small_result(h,ppi)
    task_count=len(h.tasks)
    with patch.object(main_window,'capture_prepared_page_png',return_value=result) as prepare:
        actions[[a.data() for a in actions].index(ppi)].trigger()
        assert prepare.call_args.kwargs['output_ppi']==ppi
        assert prepare.call_args.kwargs['target_ppi']==900
    dialog=window.current_dialog
    assert isinstance(dialog,PreparedPagePNGDialog) and h.dock.target_ppi==900 and len(h.tasks)==task_count
    assert not any(action.isEnabled() for action in actions)
    assert 'Original — native source pixels' in dialog.ui.summary_text.toPlainText()
    assert f'PNG page canvas: {ppi} PPI' in dialog.ui.summary_text.toPlainText()
    assert 'Opaque white paper' in dialog.ui.summary_text.toPlainText()
    child=choose(dialog,h.root/'ignored.png')
    assert child.defaultSuffix()=='png' and child.nameFilters()==['PNG documents (*.png)']
    child.reject()
    assert window.current_dialog is dialog and dialog._payload['result'] is result
    dialog.reject()
    assert all(action.isEnabled() for action in actions)
    window.ui_lock_acquire()
    assert not any(action.isEnabled() for action in actions)
    window.ui_lock_release()
    assert not any(action.isEnabled() for action in actions)
    h.ready()
    assert all(action.isEnabled() for action in actions)
    h.dock.set_off()
    with patch.object(main_window,'capture_prepared_page_png') as prepare:
        window._export_prepared_page_png(ppi)
        prepare.assert_not_called()


def test_png_retry_writes_exact_snapshot_counts_once_and_releases(preview,tmp_path):
    h=preview; card=start(h,mode='original')
    result=small_result(h,900)
    h.window.request_run_async_task.disconnect(h.submit)
    tasks=[]; h.window.request_run_async_task.connect(tasks.append)
    with patch.object(main_window,'capture_prepared_page_png',return_value=result):
        h.window._prepared_png_actions[1].trigger()
    dialog=h.window.current_dialog
    path=tmp_path/'snapshot.png'; path.write_bytes(b'old destination')
    child=choose(dialog,path)
    with patch.object(dialogs,'_write_prepared_file',side_effect=OSError('commit failure')):
        dialog._save_selected()
    assert path.read_bytes()==b'old destination' and not tasks
    assert 'Could not save PNG' in dialog.ui.error_label.text()
    assert dialog._payload['result'] is result
    child.reject()
    child=choose(dialog,path)
    h.dock.set_off()
    h.document.page_layout.document_name='Later document'
    dialog._save_selected(); dialog._save_selected()
    assert path.read_bytes()==result.data
    assert len(tasks)==1 and tasks[0].data==((card.scryfall_id,True),)
    assert not dialog._payload and h.window.current_dialog is None
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(dialog) and not shiboken6.isValid(child)


def test_png_allocation_error_opens_no_review(preview):
    h=preview; start(h,mode='original')
    with patch.object(main_window,'capture_prepared_page_png',side_effect=MemoryError()), \
            patch.object(h.window,'on_error_occurred') as error, \
            patch.object(main_window,'PreparedPagePNGDialog') as constructor:
        h.window._prepared_png_actions[0].trigger()
        constructor.assert_not_called()
        assert 'lower PNG resolution' in error.call_args.args[0]
    assert h.window.current_dialog is None


def test_destroyed_png_review_releases_payload(preview):
    h=preview; start(h,mode='original')
    dialog=PreparedPagePNGDialog(small_result(h),h.window)
    holder=dialog._payload
    shiboken6.delete(dialog)
    assert not holder


def test_real_menu_capture_review_and_saved_png(preview,tmp_path):
    from tests.test_prepared_page_png import png_info,pattern
    h=preview
    card=start(h,mode='original',ppi=900,source=pattern())
    h.window.request_run_async_task.disconnect(h.submit)
    tasks=[];h.window.request_run_async_task.connect(tasks.append)
    h.window._prepared_png_actions[0].trigger()
    dialog=h.window.current_dialog
    result=dialog._payload['result']
    assert isinstance(result,PreparedPagePNG) and result.output_ppi==600 and result.target_ppi==900
    assert png_info(result.data)['header'][:2]==(4961,7016)
    path=tmp_path/'real-page.png'
    child=choose(dialog,path);child.reject()
    assert h.window.current_dialog is dialog and dialog._payload['result'] is result
    choose(dialog,path)
    dialog._save_selected()
    assert path.read_bytes()==result.data and len(tasks)==1
    assert tasks[0].data==((card.scryfall_id,True),)

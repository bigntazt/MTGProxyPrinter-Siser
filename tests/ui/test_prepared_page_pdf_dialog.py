"""Normal action ownership, immutable safe saving and captured usage accounting."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sqlite3

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QSaveFile
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog

from mtg_proxy_printer.async_tasks import print_count_updater as updater
from mtg_proxy_printer.async_tasks.print_count_updater import PrintCountUpdater
from mtg_proxy_printer.ui import dialogs, main_window
from mtg_proxy_printer.ui.prepared_page_pdf_dialog import PreparedPagePDFDialog
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.document_controller.page_actions import ActionNewPage
from mtg_proxy_printer.model.card import CustomCard, CheckCard
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from tests.test_prepared_page_pdf import preview, start, capture
from tests.page_scene.test_prepared_scene import ordinary
from tests.test_image_source import encoded, write_original


def open_flow(h):
    h.window.ui.action_export_prepared_page_pdf.trigger()
    dialog=h.window.current_dialog
    assert isinstance(dialog,PreparedPagePDFDialog)
    return dialog


def choose(dialog,path):
    with patch.object(QFileDialog,'open'):
        dialog.choose_path()
    child=dialog._filename_dialog
    child.selectFile(str(path))
    return child


def test_action_runtime_checks_locks_and_dialog_ownership(preview,monkeypatch):
    h=preview
    action=h.window.ui.action_export_prepared_page_pdf
    assert action in h.window.ui.menu_file.actions() and not action.isEnabled()
    assert 'Image Preparation Preview' in action.toolTip()
    start(h,mode='original')
    assert action.isEnabled()
    h.window.ui_lock_acquire()
    h.window.ui_lock_acquire()
    assert not action.isEnabled()
    h.window.ui_lock_release()
    assert not action.isEnabled()
    h.window.ui_lock_release()
    assert not action.isEnabled()
    h.ready()
    dialog=open_flow(h)
    assert not action.isEnabled()
    retained=dialog._payload['result']
    assert retained.data.startswith(b'%PDF-')
    with patch.object(main_window,'capture_prepared_page_pdf') as prepare:
        h.window.on_action_export_prepared_page_pdf_triggered()
        prepare.assert_not_called()
    child=choose(dialog,h.root/'cancelled.pdf')
    assert h.window.current_dialog is dialog
    child.reject()
    assert h.window.current_dialog is dialog and dialog._payload['result'] is retained
    assert dialog._filename_dialog is None
    dialog.reject()
    assert not dialog._payload and h.window.current_dialog is None and action.isEnabled()
    h.dock.set_off()
    assert not action.isEnabled()
    with patch.object(main_window,'capture_prepared_page_pdf') as prepare:
        h.window.on_action_export_prepared_page_pdf_triggered()
        prepare.assert_not_called()


@pytest.mark.parametrize('outcome',['fallback_original','generated_uncached','standard'])
def test_summary_is_captured_truthful_and_freshness_notice(preview,outcome):
    h=preview
    start(h,mode='original')
    result=capture(h)
    result=replace(result,mode='lanczos',target_ppi=900,outcome_counts=((outcome,2),))
    dialog=PreparedPagePDFDialog(result,h.window)
    dialog.show()
    text=dialog.ui.summary_text.toPlainText()
    assert 'Captured page 1' in text and 'requested target: 900 PPI' in text
    assert {'fallback_original':'Original fallback: 2','generated_uncached':'not cached: 2',
            'standard':'Standard rendering: 2'}[outcome] in text
    if outcome=='standard': assert 'placeholders' in text
    assert 'Refresh preparation before exporting' in dialog.ui.freshness_label.text()
    dialog.reject()
    assert not dialog._payload


def test_success_saves_exact_bytes_and_submits_captured_faces_once(preview,tmp_path):
    h=preview
    front=start(h,mode='original')
    # Freeze usage at the accepted page. Later changes cannot enter this update.
    h.window.request_run_async_task.disconnect(h.submit)
    tasks=[]
    h.window.request_run_async_task.connect(tasks.append)
    dialog=open_flow(h)
    result=dialog._payload['result']
    h.dock.set_off()
    other=ordinary(name='Later')
    other.scryfall_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    h.document.apply(ActionNewPage(content=[[other]]))
    h.document.set_currently_edited_page(h.document.pages[-1])
    path=tmp_path/'captured.pdf'
    child=choose(dialog,path)
    with patch.object(dialogs,'_write_prepared_file',wraps=dialogs._write_prepared_file) as write:
        dialog._save_selected()
        dialog._save_selected()
    assert path.read_bytes()==result.data and write.call_count==1
    assert len(tasks)==1 and isinstance(tasks[0],PrintCountUpdater)
    assert tasks[0].data==((front.scryfall_id,True),)
    assert tasks[0].db_path==result.db_path
    assert not dialog._payload and h.window.current_dialog is None
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(child) and not shiboken6.isValid(dialog)


@pytest.mark.parametrize('failure',['open','short','commit'])
def test_failed_atomic_save_preserves_destination_and_allows_retry(preview,tmp_path,failure):
    h=preview
    start(h,mode='original')
    dialog=open_flow(h)
    result=dialog._payload['result']
    path=tmp_path/'existing.pdf'
    path.write_bytes(b'original destination')
    choose(dialog,path)
    tasks=[]
    dialog.request_run_async_task.connect(tasks.append)
    calls=[]
    class FaultySaveFile:
        def __init__(self,destination): self.real=QSaveFile(destination)
        def setDirectWriteFallback(self,enabled):
            calls.append(enabled); self.real.setDirectWriteFallback(enabled)
        def open(self,mode): return False if failure=='open' else self.real.open(mode)
        def write(self,data): return self.real.write(data[:-1] if failure=='short' else data)
        def commit(self): return False
        def errorString(self): return 'injected '+failure
        def cancelWriting(self): self.real.cancelWriting(); self.real=None
    with patch.object(dialogs,'QSaveFile',FaultySaveFile):
        dialog._save_selected()
    assert path.read_bytes()==b'original destination' and not tasks
    assert calls==[False] and not dialog._saved
    assert dialog._payload['result'] is result and 'retry' in dialog.ui.error_label.text()
    child=dialog._filename_dialog
    child.reject()
    choose(dialog,path)
    # Keep usage submission visible but don't run a real database task in this test.
    h.window.request_run_async_task.disconnect(h.submit)
    h.window.request_run_async_task.connect(lambda task: None)
    dialog._save_selected()
    assert path.read_bytes()==result.data and len(tasks)==1


def test_page_usage_snapshot_includes_prepared_blank_and_both_faces_excludes_others(preview):
    h=preview
    front=h.add(count=2)
    front.image_file=h.image_db.get_blank(front.size)
    back=ordinary(name='Back')
    back.is_front=False
    h.document.apply(ActionAddCard(back,2))
    write_original(h.image_db.db_path,back,encoded(43,61))
    custom=CustomCard('Custom',front.set,'C1','en',True,'',True,front.size,False,encoded())
    h.document.apply(ActionAddCard(custom,1))
    h.document.apply(ActionAddCard(CheckCard(front,back),1))
    missing=ordinary(name='Missing')
    missing.scryfall_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
    missing.image_file=None
    h.document.apply(ActionAddCard(missing,1))
    current=h.document.currently_edited_page
    other=ordinary(name='Another page')
    other.scryfall_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    h.document.apply(ActionNewPage(content=[[other]]))
    h.document.set_currently_edited_page(current)
    h.open(); h.dock.mode_combo.setCurrentIndex(1); h.ready()
    result=capture(h)
    assert {(key.scryfall_id,key.is_front) for key in result.image_keys}=={(front.scryfall_id,True),(front.scryfall_id,False)}
    assert dict(result.outcome_counts)['original']==4
    assert dict(result.outcome_counts)['standard']==1
    assert dict(result.outcome_counts)['skipped']==2


def test_explicit_updater_freezes_deduplicates_and_writes_only_snapshot(tmp_path,monkeypatch):
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE Printing(printing_id INTEGER,scryfall_id TEXT);
        CREATE TABLE PrintingFace(printing_id INTEGER,is_front INTEGER,usage_count INTEGER,last_use_timestamp INTEGER);
        INSERT INTO Printing VALUES(1,'first'),(2,'other');
        INSERT INTO PrintingFace VALUES(1,1,0,NULL),(1,0,0,NULL),(2,1,0,NULL);''')
    keys=[ImageKey('first',True,ImageQuality.high_resolution),ImageKey('first',False,ImageQuality.high_resolution)]
    task=PrintCountUpdater(image_keys=keys+keys,db_path=tmp_path/'captured.db',db=db)
    keys.clear()
    with patch.object(updater,'open_database',side_effect=AssertionError('Provided connection')):
        task.run()
    assert db.execute('SELECT printing_id,is_front,usage_count FROM PrintingFace ORDER BY printing_id,is_front').fetchall()==[(1,0,1),(1,1,1),(2,1,0)]
    db.close()
    connection=Mock()
    with patch.object(updater,'open_database',return_value=connection) as open_db:
        delayed=PrintCountUpdater(image_keys=(),db_path=tmp_path/'captured.db')
        open_db.assert_not_called()
        delayed.run()
        open_db.assert_called_once_with(str(tmp_path/'captured.db'),updater.SCHEMA_NAME)
        connection.close.assert_called_once()


def test_document_updater_compatibility_and_ambiguous_inputs():
    key=ImageKey('fixture',True,ImageQuality.high_resolution)
    document=SimpleNamespace(card_db=SimpleNamespace(db_path='original.db'),get_all_image_keys_in_document=lambda:{key})
    task=PrintCountUpdater(document)
    assert task.data==[('fixture',True)] and task.db_path=='original.db'
    for kwargs in ({'document':document,'image_keys':()}, {'document':document,'db_path':'x'}, {}, {'image_keys':()}, {'db_path':'x'}):
        with pytest.raises(ValueError): PrintCountUpdater(**kwargs)

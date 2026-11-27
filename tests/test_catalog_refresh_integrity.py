import io
import json
import sqlite3
import threading
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from mtg_proxy_printer.async_tasks import card_info_downloader as download
from mtg_proxy_printer.carddb_migrations import DatabaseMigrationTask, MIGRATION_SCRIPTS
from mtg_proxy_printer.model.carddb import CardDatabase
from tests.helpers import fill_card_database_with_json_card, fill_card_database_with_json_cards, load_json
from tests.test_database_migration_code import old_db


def rows(db, query):
    return list(map(tuple, db.execute(query)))


@pytest.mark.parametrize("cancel", [True, False])
def test_stream_completion_and_cancel(card_db, monkeypatch, cancel):
    card = load_json("regular_english_card")
    db = card_db.db
    db.row_factory = None
    db.commit()
    before = list(db.iterdump())
    producer = download.ApiStreamTask("fixture")
    monkeypatch.setattr(producer, "read_from_url", lambda url: (io.BytesIO(json.dumps(card).encode()), None))
    monkeypatch.setattr(producer, "get_available_card_count", lambda: 1)
    monkeypatch.setattr(download.SetDataImportTask, "run", lambda self: None)
    consumer = download.DatabaseImportTask(producer, db, ":memory:")
    errors = []
    consumer.error_occurred.connect(errors.append)
    producer.run()
    if cancel:
        consumer.cancel()
    consumer.run()
    assert not errors and not db.in_transaction
    if cancel:
        assert list(db.iterdump()) == before
    else:
        assert rows(db, "SELECT reported_card_count FROM LastDatabaseUpdate") == [(1,)]
        assert rows(db, "SELECT scryfall_id FROM Printing") == [(card["id"],)]


def test_malformed_download_rolls_back_complete_batch(card_db, monkeypatch):
    card = load_json("regular_english_card")
    fill_card_database_with_json_card(card_db, card)
    db = card_db.db
    db.row_factory = None
    before = list(db.iterdump())
    data = b"".join(json.dumps(dict(card, id=str(UUID(int=i+1)))).encode() + b"\n" for i in range(5000))
    producer = download.ApiStreamTask("fixture")
    monkeypatch.setattr(producer, "read_from_url", lambda url: (io.BytesIO(data + b"{broken"), None))
    monkeypatch.setattr(producer, "get_available_card_count", lambda: 5001)
    monkeypatch.setattr(download.SetDataImportTask, "run", lambda self: None)
    consumer = download.DatabaseImportTask(producer, db, ":memory:")
    errors = []
    consumer.error_occurred.connect(errors.append)
    thread = threading.Thread(target=producer.run, daemon=True)
    thread.start()
    consumer.run()
    thread.join(5)
    assert not thread.is_alive()
    assert errors and not db.in_transaction
    assert list(db.iterdump()) == before


def test_refresh_after_schema_34_migration(old_db):
    card = load_json("regular_english_card")
    catalog = CardDatabase(old_db)
    DatabaseMigrationTask(catalog, {v: s for v, s in MIGRATION_SCRIPTS.items() if v < 34}).run()
    db = sqlite3.connect(old_db)
    assert db.execute("pragma user_version").fetchone()[0] == 34
    db.execute("INSERT INTO MTGSet VALUES (41, ?, ?, '', ?, 0)", (card["set"], card["set_name"], card["released_at"]))
    db.execute("INSERT INTO Card(card_id,oracle_id) VALUES(42,?)", (card["oracle_id"],))
    db.execute("INSERT INTO PrintLanguage(language_id,language) VALUES(43,'en')")
    db.execute("INSERT INTO FaceName(face_name_id,card_name,language_id) VALUES(44,?,43)", (card["name"],))
    db.execute("INSERT INTO Printing(printing_id,card_id,set_id,collector_number,scryfall_id,is_oversized,highres_image) VALUES(45,42,41,'157',?,0,1)", (card["id"],))
    db.execute("INSERT INTO CardFace VALUES(46,45,44,1,?,0)", (card["image_uris"]["png"],))
    db.commit()
    db.close()
    DatabaseMigrationTask(catalog).run()
    catalog.reopen_database()
    db = catalog.db
    assert rows(db, "SELECT set_id,set_scryfall_id FROM MTGSet") == [(41, card["set"])]
    for _ in range(2):
        fill_card_database_with_json_card(catalog, card)
        assert rows(db, "SELECT set_id,set_scryfall_id FROM MTGSet") == [(41, card["set_id"])]
        assert rows(db, "SELECT printing_id,card_id,set_id FROM Printing") == [(45,42,41)]
        assert rows(db, "SELECT printing_id FROM PrintingFace") == [(45,)]
        assert not rows(db, "PRAGMA foreign_key_check")


@pytest.mark.parametrize("reverse", [False, True])
def test_code_reassignment(card_db, reverse):
    a = load_json("regular_english_card")
    b = dict(a, id=str(UUID(int=1)), set_id=str(UUID(int=2)), set="bee")
    fill_card_database_with_json_cards(card_db, [a,b])
    db = card_db.db
    identities = rows(db, "SELECT printing_id,set_id FROM Printing ORDER BY printing_id")
    cards = [dict(a,set="new"), dict(b,set=a["set"])]
    fill_card_database_with_json_cards(card_db, cards[::-1] if reverse else cards)
    assert rows(db, "SELECT set_code FROM MTGSet ORDER BY set_id") == [("new",),(a["set"],)]
    assert rows(db, "SELECT printing_id,set_id FROM Printing ORDER BY printing_id") == identities


def test_real_uuid_conflict_rolls_back(card_db):
    a = load_json("regular_english_card")
    fill_card_database_with_json_card(card_db, a)
    db = card_db.db
    db.row_factory = None
    before = list(db.iterdump())
    task = download.DatabaseImportTask(MagicMock(), db, ":memory:")
    errors = []
    task.error_occurred.connect(errors.append)
    task.populate_database([dict(a, set_id=str(UUID(int=2))), a])
    assert errors and list(db.iterdump()) == before


@pytest.mark.parametrize("reverse", [True, False])
def test_earliest_date_across_refresh_and_cache(card_db, reverse):
    a = load_json("regular_english_card")
    fill_card_database_with_json_card(card_db, a)
    changed = dict(a, set="new", set_name="Renamed", released_at="2020-01-01")
    other = dict(changed, id=str(UUID(int=1)), released_at="1999-01-01")
    for cards, expected in (([changed], a["released_at"]), ([changed,other], other["released_at"])):
        fill_card_database_with_json_cards(card_db, cards[::-1] if reverse else cards)
        assert rows(card_db.db, "SELECT date(release_date,'unixepoch','localtime') FROM MTGSet") == [(expected,)]


def test_parent_rename_reuses_icon(card_db, monkeypatch):
    a = load_json("regular_english_card")
    b = dict(a, id=str(UUID(int=1)), set_id=str(UUID(int=2)), set="bee")
    fill_card_database_with_json_cards(card_db,[a,b])
    db = card_db.db
    db.execute("UPDATE MTGSet SET icon_file_name='shared.svg',icon_svg=?", (b'x'*128,))
    db.execute("UPDATE MTGSet SET parent_set_code=? WHERE set_code='bee'", (a["set"],))
    db.execute("UPDATE MTGSet SET set_code='new' WHERE set_code=?", (a["set"],))
    task = download.SetDataImportTask(db)
    items = [dict(id=c["set_id"], icon_svg_uri="https://fixture/shared.svg") for c in (a,b)]
    items[1]["parent_set_code"] = "new"
    monkeypatch.setattr(task, "_fetch_sets_list_from_scryfall_api", lambda: json.dumps({"data":items}))
    monkeypatch.setattr(task, "_download_svg", lambda uri: pytest.fail("Unnecessary icon download"))
    task.run()
    assert rows(db, "SELECT parent_set_code FROM MTGSet WHERE set_code='bee'") == [("new",)]
    assert len(list(card_db.get_all_sets())) == 2


@pytest.mark.parametrize("truncated", [True, False])
def test_http_eof_at_record_boundary(card_db, monkeypatch, truncated):
    import http.client
    import urllib.request

    seed = load_json("regular_english_card")
    fill_card_database_with_json_card(card_db, seed)
    db = card_db.db
    db.row_factory = None
    before = list(db.iterdump())
    card = dict(seed, id=str(UUID(int=123)))
    line = json.dumps(card).encode() + b"\n"
    length = len(line) * (2 if truncated else 1)
    wire = f"HTTP/1.1 200 OK\r\nContent-Encoding: identity\r\nContent-Length: {length}\r\n\r\n".encode() + line
    sock = MagicMock()
    sock.makefile.return_value = io.BytesIO(wire)
    response = http.client.HTTPResponse(sock)
    response.begin()
    monkeypatch.setattr(urllib.request, "urlopen", lambda request: response)
    producer = download.ApiStreamTask("https://fixture/bulk")
    producer._batch_size = 1
    monkeypatch.setattr(producer, "get_available_card_count", lambda: 999)
    monkeypatch.setattr(download.SetDataImportTask, "run", lambda self: None)
    consumer = download.DatabaseImportTask(producer, db, ":memory:")
    errors = []
    consumer.error_occurred.connect(errors.append)
    producer.run()
    consumer.run()
    if truncated:
        assert errors and "bytes" in errors[0]
        assert list(db.iterdump()) == before
    else:
        assert not errors
        assert db.execute("SELECT COUNT(*) FROM Printing").fetchone() == (2,)
        assert db.execute("SELECT COUNT(*) FROM LastDatabaseUpdate").fetchone() == (2,)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_first_migrated_refresh_uses_printing_links(old_db, monkeypatch, reverse, ambiguous):
    a = load_json("regular_english_card")
    b = dict(a, set="bee", set_id=str(UUID(int=2)), id=str(UUID(int=3)))
    catalog = CardDatabase(old_db, register_exit_hooks=False)
    DatabaseMigrationTask(catalog, {v: s for v, s in MIGRATION_SCRIPTS.items() if v < 34}).run()
    with sqlite3.connect(old_db) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (34,)
        db.execute("INSERT INTO Card(card_id,oracle_id) VALUES(42,?)", (a["oracle_id"],))
        db.execute("INSERT INTO PrintLanguage(language_id,language) VALUES(43,'en')")
        db.execute("INSERT INTO FaceName(face_name_id,card_name,language_id) VALUES(44,?,43)", (a["name"],))
        for i, card in enumerate((a, b)):
            db.execute("INSERT INTO MTGSet VALUES (?, ?, ?, '', ?, 0)",
                       (41+i, card["set"], card["set_name"], card["released_at"]))
            db.execute("""INSERT INTO Printing(printing_id,card_id,set_id,collector_number,scryfall_id,is_oversized,highres_image)
                          VALUES(?,42,?,'157',?,0,1)""", (45+i, 41+i, card["id"]))
            db.execute("INSERT INTO CardFace VALUES(?,?,44,1,?,0)", (50+i, 45+i, card["image_uris"]["png"]))
    db.close()
    DatabaseMigrationTask(catalog).run()
    catalog.reopen_database()
    db = catalog.db
    db.row_factory = None
    db.execute("UPDATE MTGSet SET set_preference_weight=set_id")
    db.commit()
    before = list(db.iterdump())
    cards = [dict(a, set=b["set"]), dict(b, set=a["set"])]
    if ambiguous:
        cards[1].update(set_id=a["set_id"], set=b["set"])
    if reverse:
        cards.reverse()
    # The first incoming printing for each UUID need not already be in the catalog.
    new_cards = [dict(card, id=str(UUID(int=100+i))) for i, card in enumerate(cards)]
    monkeypatch.setattr(download.SetDataImportTask, "run", lambda self: None)
    for _ in range(2):
        importer = download.DatabaseImportTask(MagicMock(), db, ":memory:")
        errors = []
        importer.error_occurred.connect(errors.append)
        importer.populate_database(new_cards + cards)
        if ambiguous:
            assert errors and list(db.iterdump()) == before
            break
        assert not errors
        assert rows(db, "SELECT set_id,set_code,set_scryfall_id,set_preference_weight FROM MTGSet ORDER BY set_id") == [
            (41, b["set"], a["set_id"], 41), (42, a["set"], b["set_id"], 42)]
        assert rows(db, "SELECT printing_id,set_id FROM Printing WHERE printing_id IN(45,46) ORDER BY printing_id") == [
            (45, 41), (46, 42)]
        assert not rows(db, "PRAGMA foreign_key_check")


@pytest.mark.parametrize("failure", ["count", "missing-file", "consumer", "cancel"])
def test_stream_startup_and_abandonment(card_db, tmp_path, monkeypatch, failure):
    from PySide6.QtCore import Qt

    db = card_db.db
    db.row_factory = None
    db.commit()
    before = list(db.iterdump())
    card = load_json("regular_english_card")
    line = json.dumps(card).encode() + b"\n"
    if failure == "missing-file":
        path = tmp_path / "bulk.jsonl"
        path.write_bytes(line)
        source = download.FileStreamTask(path)
        path.unlink()
    else:
        source = download.ApiStreamTask("fixture")
        source._batch_size = 1
        payload = line * 20
        if failure == "consumer":
            payload = b'{"invalid":"printing"}\n' + payload
        monkeypatch.setattr(source, "read_from_url", lambda url: (io.BytesIO(payload), None))
        count = {} if failure == "count" else {"total_cards": 20}
        monkeypatch.setattr(source, "read_json_from_url", lambda url: count)
    task = download.DatabaseImportTask(source, db, ":memory:")
    waiting_to_put = threading.Event()
    wait_for_space = source.queue.not_full.wait

    def blocked_put(timeout=None):
        waiting_to_put.set()
        return wait_for_space(timeout)

    monkeypatch.setattr(source.queue.not_full, "wait", blocked_put)
    errors, finished, escaped = [], [], []
    task.error_occurred.connect(errors.append, Qt.ConnectionType.DirectConnection)
    for name, job in (("source", source), ("import", task)):
        job.task_completed.connect(lambda n=name: finished.append(n), Qt.ConnectionType.DirectConnection)

    def run(job):
        try:
            job.run()
        except Exception as error:
            escaped.append(error)

    sender = threading.Thread(target=run, args=(source,), daemon=True)
    reader = threading.Thread(target=run, args=(task,), daemon=True)
    sender.start()
    try:
        if failure != "missing-file":
            assert waiting_to_put.wait(3) and source.queue.full()
        if failure == "cancel":
            task.cancel()
        reader.start()
        reader.join(3)
        sender.join(3)
        assert not reader.is_alive() and not sender.is_alive()
        assert not escaped and sorted(finished) == ["import", "source"]
        assert source.queue.empty()
        assert bool(errors) == (failure != "cancel")
        assert list(db.iterdump()) == before
    finally:
        source.cancel()
        sender.join(2)

    retry = download.ApiStreamTask("fixture")
    monkeypatch.setattr(retry, "get_available_card_count", lambda: 1)
    monkeypatch.setattr(retry, "read_from_url", lambda url: (io.BytesIO(line), None))
    monkeypatch.setattr(download.SetDataImportTask, "run", lambda self: None)
    retry_task = download.DatabaseImportTask(retry, db, ":memory:")
    retry.run()
    retry_task.run()
    assert rows(db, "SELECT scryfall_id FROM Printing") == [(card["id"],)]

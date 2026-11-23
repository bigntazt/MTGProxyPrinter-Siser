"""Completed-PNG validation and deterministic publication sequencing on local disk."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QSaveFile
from PySide6.QtGui import QColor, QColorSpace, QImage, QImageReader

from mtg_proxy_printer import image_derivative_cache as backend
from mtg_proxy_printer.image_derivative_cache import DerivativeCache, CachedDerivative, CacheMiss, CacheWriteFailure
from tests.test_image_derivative import make_request, make_snapshot


def encoded(dimensions=(1490, 2080), color="#4080bf40", image_format="PNG"):
    image = QImage(*dimensions, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    image.setText("M25", "synthetic byte-preservation fixture")
    image.setDotsPerMeterX(11811)
    image.setDotsPerMeterY(23622)
    image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.DisplayP3))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, image_format)
    return bytes(buffer.data())


@pytest.fixture(scope="module")
def pngs():
    return encoded(), encoded(color="#80bf4080")


def entry(root, request):
    return root / "v1" / request.key[:2] / request.key


def read_manifest(root, request):
    return json.loads((entry(root, request) / "manifest.json").read_bytes())


def write_manifest(root, request, manifest):
    (entry(root, request) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_construction_and_missing_lookup_are_read_only(tmp_path):
    root = tmp_path / "not-created"
    cache = DerivativeCache(root)
    assert not root.exists()
    result = cache.lookup(make_request())
    assert isinstance(result, CacheMiss) and result.reason == "not_found"
    assert not root.exists()
    with pytest.raises(ValueError, match="absolute"):
        DerivativeCache(Path("relative"))
    with pytest.raises(TypeError):
        cache.lookup(object())


def test_fresh_cache_returns_same_immutable_bytes_on_worker(tmp_path, pngs):
    request = make_request()
    limit = QImageReader.allocationLimit()
    with patch("PySide6.QtGui.QPixmap", side_effect=AssertionError("No worker QPixmap")), \
            ThreadPoolExecutor(max_workers=1) as executor:
        stored = executor.submit(DerivativeCache(tmp_path).store, request, pngs[0]).result(timeout=10)
        found = executor.submit(DerivativeCache(tmp_path).lookup, request).result(timeout=10)
    assert isinstance(stored, CachedDerivative) and found == stored
    assert found.png_bytes == pngs[0] and type(found.png_bytes) is bytes
    assert found.png_sha256 == hashlib.sha256(pngs[0]).hexdigest()
    assert found.request_key == request.key and found.dimensions == (1490, 2080)
    assert "png_bytes=" not in repr(found)
    assert QImageReader.allocationLimit() == limit
    image = QImage.fromData(found.png_bytes)
    assert image.pixelColor(0, 0).alpha() == 0x40
    assert image.text("M25") == "synthetic byte-preservation fixture"
    assert image.colorSpace() == QColorSpace(QColorSpace.NamedColorSpace.DisplayP3)
    assert (image.dotsPerMeterX(), image.dotsPerMeterY()) == (11811, 23622)
    manifest = read_manifest(tmp_path, request)
    assert manifest["request"] == request.to_spec()
    assert sorted(path.name for path in entry(tmp_path, request).iterdir()) == [found.png_sha256+".png", "manifest.json"]


@pytest.mark.parametrize("canonical,ppi,target", [
    ((745, 1040), 600, (1490, 2080)), ((745, 1040), 1200, (2980, 4160)),
    ((1040, 1490), 600, (2080, 2980)), ((1040, 1490), 1200, (4160, 5960)),
])
def test_all_supported_exact_targets(tmp_path, canonical, ppi, target):
    request = make_request(snapshot=make_snapshot(canonical_dimensions=canonical), ppi=ppi)
    png = encoded(target)
    stored = DerivativeCache(tmp_path).store(request, png)
    assert isinstance(stored, CachedDerivative) and stored.dimensions == target
    assert DerivativeCache(tmp_path).lookup(request).png_bytes == png


@pytest.mark.parametrize("kind", ["bad-bytes", "header-only", "wrong-dimensions", "jpeg"])
def test_invalid_output_creates_nothing_and_preserves_prior_entry(tmp_path, pngs, kind):
    request = make_request()
    bad = {"bad-bytes": b"not an image", "header-only": pngs[0][:33],
           "wrong-dimensions": encoded((31, 47)), "jpeg": encoded(image_format="JPEG")}[kind]
    root = tmp_path / "cache"
    cache = DerivativeCache(root)
    assert cache.store(request, bad).reason == "invalid_output"
    assert not root.exists()
    cache.store(request, pngs[0])
    previous = (entry(root, request) / "manifest.json").read_bytes()
    assert cache.store(request, bad).reason == "invalid_output"
    assert (entry(root, request) / "manifest.json").read_bytes() == previous
    assert cache.lookup(request).png_bytes == pngs[0]
    with pytest.raises(TypeError):
        cache.store(request, bytearray(pngs[0]))


@pytest.mark.parametrize("damage", [
    "bad-json", "duplicate-root", "duplicate-nested", "unsupported-schema", "boolean-schema", "float-schema",
    "wrong-request-key", "request-mismatch", "request-int-float", "missing-request-field",
    "unknown-root-field", "arbitrary-output-path", "uppercase-digest", "path-digest",
    "boolean-size", "float-size", "negative-size", "wrong-size", "boolean-dimensions", "float-dimensions",
    "wrong-dimensions-metadata", "wrong-format", "missing-blob", "changed-blob", "nonfinite-json", "overflow-json",
    "invalid-utf8", "self-consistent-invalid-image", "self-consistent-header", "self-consistent-wrong-decoded-size",
])
def test_damaged_entries_are_nonfatal_and_never_repaired(tmp_path, pngs, damage):
    request = make_request()
    cache = DerivativeCache(tmp_path)
    cache.store(request, pngs[0])
    manifest = read_manifest(tmp_path, request)
    output = manifest["output"]
    manifest_path = entry(tmp_path, request) / "manifest.json"
    raw = None
    if damage == "bad-json": raw = b"{"
    elif damage == "duplicate-root": raw = b'{"schema_version":1,' + manifest_path.read_bytes()[1:]
    elif damage == "duplicate-nested":
        raw = manifest_path.read_bytes().replace(b'"output":{', b'"output":{"format":"png",')
    elif damage == "unsupported-schema": manifest["schema_version"] = 2
    elif damage == "boolean-schema": manifest["schema_version"] = True
    elif damage == "float-schema": manifest["schema_version"] = 1.0
    elif damage == "wrong-request-key": manifest["request_key"] = "a"*64
    elif damage == "request-mismatch": manifest["request"]["processing"]["provider"]["version"] = "other"
    elif damage == "request-int-float": manifest["request"]["schema_version"] = 1.0
    elif damage == "missing-request-field": del manifest["request"]["inspection"]
    elif damage == "unknown-root-field": manifest["extra"] = 1
    elif damage == "arbitrary-output-path": output["path"] = "../../other.png"
    elif damage == "uppercase-digest": output["sha256"] = output["sha256"].upper()
    elif damage == "path-digest": output["sha256"] = "../../other"
    elif damage == "boolean-size": output["size_bytes"] = True
    elif damage == "float-size": output["size_bytes"] = float(output["size_bytes"])
    elif damage == "negative-size": output["size_bytes"] = -1
    elif damage == "wrong-size": output["size_bytes"] += 1
    elif damage == "boolean-dimensions": output["dimensions"][0] = True
    elif damage == "float-dimensions": output["dimensions"][0] = float(output["dimensions"][0])
    elif damage == "wrong-dimensions-metadata": output["dimensions"] = [31, 47]
    elif damage == "wrong-format": output["format"] = "jpeg"
    elif damage == "missing-blob": (entry(tmp_path, request) / (output["sha256"] + ".png")).unlink()
    elif damage == "changed-blob": (entry(tmp_path, request) / (output["sha256"] + ".png")).write_bytes(pngs[1])
    elif damage == "nonfinite-json": raw = manifest_path.read_bytes().replace(b'"schema_version":1', b'"schema_version":NaN', 1)
    elif damage == "overflow-json": raw = manifest_path.read_bytes().replace(b'"size_bytes":', b'"size_bytes":1e999,"ignored":', 1)
    elif damage == "invalid-utf8": raw = b"\xff"
    elif damage.startswith("self-consistent-"):
        bad = {"self-consistent-invalid-image": b"not an image",
               "self-consistent-header": pngs[0][:33],
               "self-consistent-wrong-decoded-size": encoded((31, 47))}[damage]
        output["sha256"] = hashlib.sha256(bad).hexdigest()
        output["size_bytes"] = len(bad)
        (entry(tmp_path, request) / (output["sha256"] + ".png")).write_bytes(bad)
    else: raise AssertionError(damage)
    if raw is not None:
        manifest_path.write_bytes(raw)
    else:
        write_manifest(tmp_path, request, manifest)
    before = {path.name: path.read_bytes() for path in entry(tmp_path, request).iterdir()}
    result = cache.lookup(request)
    assert isinstance(result, CacheMiss) and result.reason == "invalid_entry"
    assert {path.name: path.read_bytes() for path in entry(tmp_path, request).iterdir()} == before


def test_new_request_cannot_hit_old_entry_and_orphans_are_not_commits(tmp_path, pngs):
    request = make_request()
    cache = DerivativeCache(tmp_path)
    cache.store(request, pngs[0])
    assert cache.lookup(make_request(ppi=1200)).reason == "not_found"
    (entry(tmp_path, request) / "manifest.json").unlink()
    assert cache.lookup(request).reason == "not_found"
    assert list(entry(tmp_path, request).glob("*.png"))


def test_lookup_io_errors_and_captured_return_buffer(tmp_path, pngs, monkeypatch):
    request = make_request()
    cache = DerivativeCache(tmp_path)
    cache.store(request, pngs[0])
    read = backend._read_bytes
    for fail_manifest in (True, False):
        def denied(path):
            if (path.name == "manifest.json") == fail_manifest:
                raise PermissionError("Synthetic Windows sharing denial")
            return read(path)
        monkeypatch.setattr(backend, "_read_bytes", denied)
        assert cache.lookup(request).reason == "io_error"
    def replace_after_read(path):
        captured = read(path)
        if path.suffix == ".png": path.write_bytes(pngs[1])
        return captured
    monkeypatch.setattr(backend, "_read_bytes", replace_after_read)
    assert cache.lookup(request).png_bytes == pngs[0]
    monkeypatch.setattr(backend, "_read_bytes", read)
    assert cache.lookup(request).reason == "invalid_entry"


@pytest.mark.parametrize("stage", ["png-open", "png-short-write", "png-commit", "manifest-commit"])
def test_actual_qsavefile_failure_preserves_previous_commit(tmp_path, pngs, monkeypatch, stage):
    request = make_request()
    cache = DerivativeCache(tmp_path)
    old = cache.store(request, pngs[0])
    previous_manifest = (entry(tmp_path, request) / "manifest.json").read_bytes()
    events = []
    class FailingSaveFile:
        def __init__(self, path):
            assert type(path) is str
            self.path = Path(path)
            self.file = QSaveFile(path)
        def setDirectWriteFallback(self, enabled):
            assert enabled is False
            self.file.setDirectWriteFallback(enabled)
        def open(self, mode):
            opened = self.file.open(mode)
            return False if stage == "png-open" and self.path.suffix == ".png" else opened
        def write(self, data):
            if stage == "png-short-write" and self.path.suffix == ".png":
                return self.file.write(data[:len(data)//2])
            return self.file.write(data)
        def commit(self):
            fail = (stage == "png-commit" and self.path.suffix == ".png") or (stage == "manifest-commit" and self.path.name == "manifest.json")
            return False if fail else self.file.commit()
        def errorString(self): return "Synthetic interrupted " + stage
        def cancelWriting(self):
            events.append("cancel-" + self.path.name)
            self.file.cancelWriting()
    monkeypatch.setattr(backend, "QSaveFile", FailingSaveFile)
    failed = cache.store(request, pngs[1])
    assert isinstance(failed, CacheWriteFailure) and failed.reason == "write_failed"
    assert len(events) == 1
    assert (entry(tmp_path, request) / "manifest.json").read_bytes() == previous_manifest
    assert cache.lookup(request).png_bytes == old.png_bytes
    new_blob = entry(tmp_path, request) / (hashlib.sha256(pngs[1]).hexdigest() + ".png")
    if stage == "manifest-commit":
        assert new_blob.read_bytes() == pngs[1]  # Completed orphan intentionally retained.
    else:
        assert not new_blob.exists()
    assert sorted(path.name for path in entry(tmp_path, request).iterdir()) == sorted(
        [old.png_sha256 + ".png", "manifest.json"] + ([new_blob.name] if stage == "manifest-commit" else []))
    print("failure-preserved:", stage, "prior manifest unchanged; completed orphan", new_blob.exists())


def test_directory_and_write_failures_are_nonfatal_and_same_hash_blob_is_republished(tmp_path, pngs, monkeypatch):
    blocked = tmp_path / "file-instead-of-directory"
    blocked.write_bytes(b"unrelated")
    request = make_request()
    assert DerivativeCache(blocked).store(request, pngs[0]).reason == "write_failed"
    assert blocked.read_bytes() == b"unrelated"
    cache = DerivativeCache(tmp_path / "cache")
    cached = cache.store(request, pngs[0])
    blob = entry(cache.root, request) / (cached.png_sha256 + ".png")
    blob.write_bytes(b"damaged despite hash filename")
    assert cache.store(request, pngs[0]).png_bytes == pngs[0]
    assert blob.read_bytes() == pngs[0]
    with patch.object(backend, "_write_atomic", side_effect=PermissionError("Sharing violation")):
        assert cache.store(request, pngs[1]).reason == "write_failed"
    assert cache.lookup(request).png_bytes == pngs[0]
    with patch.object(backend, "_read_bytes", side_effect=RuntimeError("programming defect")):
        with pytest.raises(RuntimeError, match="programming defect"):
            cache.lookup(request)


def test_reader_manifest_a_survives_publication_b(tmp_path, pngs, monkeypatch):
    request = make_request()
    cache = DerivativeCache(tmp_path)
    first = cache.store(request, pngs[0])
    read = backend._read_bytes
    events = []
    def interleaved(path):
        captured = read(path)  # File is closed, then B commits.
        if path.name == "manifest.json" and not events:
            events.append("reader-captured-A")
            assert isinstance(DerivativeCache(tmp_path).store(request, pngs[1]), CachedDerivative)
            events.append("writer-committed-B")
        return captured
    monkeypatch.setattr(backend, "_read_bytes", interleaved)
    assert cache.lookup(request).png_bytes == pngs[0]
    events.append("reader-returned-A")
    assert DerivativeCache(tmp_path).lookup(request).png_bytes == pngs[1]
    events.append("next-reader-returned-B")
    assert (entry(tmp_path, request) / (first.png_sha256 + ".png")).read_bytes() == pngs[0]
    print("reader/writer:", events)
    assert events == ["reader-captured-A", "writer-committed-B", "reader-returned-A", "next-reader-returned-B"]


def test_two_writers_last_manifest_commit_wins(tmp_path, pngs, monkeypatch):
    request = make_request()
    cache = DerivativeCache(tmp_path)
    write = backend._write_atomic
    digest_a, digest_b = (hashlib.sha256(png).hexdigest() for png in pngs)
    events = []
    started_b = False
    def interleaved(path, data):
        nonlocal started_b
        if path.name == "manifest.json" and not started_b:
            started_b = True
            assert cache.store(request, pngs[1]).png_sha256 == digest_b
        write(path, data)
        if path.suffix == ".png":
            events.append("png-A" if path.stem == digest_a else "png-B")
        else:
            events.append("manifest-A" if json.loads(data)["output"]["sha256"] == digest_a else "manifest-B")
    monkeypatch.setattr(backend, "_write_atomic", interleaved)
    assert cache.store(request, pngs[0]).png_sha256 == digest_a
    assert cache.lookup(request).png_bytes == pngs[0]
    assert (entry(tmp_path, request) / (digest_b + ".png")).read_bytes() == pngs[1]
    assert events == ["png-A", "png-B", "manifest-B", "manifest-A"]
    print("two-writer commit order:", events, "lookup returned A")

"""M28 decoding is a GUI presentation operation, never another preparation pass."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
import gc
import struct
import weakref
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QObject, QThread
from PySide6.QtGui import QImageReader, QPixmap

from mtg_proxy_printer.image_preparation import PreparedImage
from mtg_proxy_printer.image_source import SourceIdentity
from mtg_proxy_printer.page_scene import prepared_pixmap as presentation
from mtg_proxy_printer.page_scene.prepared_pixmap import PreparedPixmap, RasterDecodeFailure, decode_prepared_pixmap
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from tests.test_image_source import encoded, PRINTING


def prepared(data=None, dimensions=(73, 109), canonical=(745, 1040), ppi=600, **changes):
    result = PreparedImage(
        SourceIdentity(ImageKey(PRINTING, True, ImageQuality.high_resolution), "a"*64),
        canonical, encoded(*dimensions) if data is None else data, "png", dimensions,
        ppi, tuple(axis*(ppi//300) for axis in canonical), "original")
    return replace(result, **changes)


def reference(data):
    buffer = QBuffer()
    buffer.setData(data)
    assert buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoDetectImageFormat(True)
    reader.setAutoTransform(False)
    pixmap = QPixmap.fromImage(reader.read())
    pixmap.setDevicePixelRatio(1)
    return pixmap


@pytest.mark.parametrize("canonical", [(745, 1040), (1040, 1490)])
@pytest.mark.parametrize("ppi", [600, 1200])
def test_native_target_pixels_retained(qtbot, canonical, ppi):
    dimensions = tuple(axis*(ppi//300) for axis in canonical)
    source = prepared(dimensions=dimensions, canonical=canonical, ppi=ppi,
                      outcome="generated_cached", derivative_key="b"*64)
    result = decode_prepared_pixmap(source)
    assert isinstance(result, PreparedPixmap)
    assert result.pixmap.size().toTuple() == dimensions == result.actual_dimensions
    assert result.pixmap.devicePixelRatio() == 1
    assert result.pixmap.toImage() == reference(source.encoded_bytes).toImage()
    assert result.target_ppi == ppi and result.derivative_key == "b"*64


@pytest.mark.parametrize("outcome", ["original", "fallback_original"])
def test_original_keeps_native_pixels_and_outcome(qtbot, outcome):
    source = prepared(ppi=1200, outcome=outcome, reason="unsupported_source", detail="Retained reason")
    result = decode_prepared_pixmap(source)
    assert result.actual_dimensions == (73, 109) != result.target_dimensions
    assert result.pixmap.size().toTuple() == (73, 109)
    assert (result.outcome, result.reason, result.detail) == (outcome, source.reason, source.detail)


@pytest.mark.parametrize("changes,reason", [
    ({"encoded_bytes": b"not an image"}, "decode_failed"),
    ({"image_format": "jpeg"}, "format_mismatch"),
    ({"actual_dimensions": (109, 73)}, "dimension_mismatch"),
    ({"actual_dimensions": (0, 109)}, "invalid_metadata"),
    ({"actual_dimensions": (True, 109)}, "invalid_metadata"),
    ({"canonical_dimensions": (745,)}, "invalid_metadata"),
    ({"target_dimensions": (2980, 4160)}, "invalid_metadata"),
    ({"target_ppi": 300}, "invalid_metadata"),
    ({"reason": []}, "invalid_metadata"),
])
def test_bad_bytes_and_contradictory_metadata(qtbot, changes, reason):
    result = decode_prepared_pixmap(replace(prepared(), **changes))
    assert isinstance(result, RasterDecodeFailure) and result.reason == reason and result.detail


def test_null_pixmap_conversion_is_explicit_but_programming_error_propagates(qtbot):
    with patch.object(presentation.QPixmap, "fromImage", return_value=QPixmap()):
        assert decode_prepared_pixmap(prepared()).reason == "pixmap_failed"
    with patch.object(presentation.QPixmap, "fromImage", side_effect=ValueError("programming bug")):
        with pytest.raises(ValueError, match="programming bug"):
            decode_prepared_pixmap(prepared())


def test_orientation_policy_reuses_m24_exif_case(qtbot):
    # Same minimal orientation-6 EXIF fixture as the M24 source inspection case.
    jpeg = encoded(31, 47, image_format="JPEG")
    tiff = b"II\x2a\x00\x08\x00\x00\x00" + struct.pack("<H", 1)
    tiff += struct.pack("<HHI", 0x112, 3, 1) + struct.pack("<H", 6) + b"\x00\x00" + b"\x00"*4
    exif = b"Exif\x00\x00" + tiff
    data = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif)+2) + exif + jpeg[2:]
    result = decode_prepared_pixmap(prepared(data, (31, 47), image_format="jpeg"))
    assert result.pixmap.size().toTuple() == (31, 47)
    assert result.pixmap.toImage() == reference(data).toImage()


def test_alpha_and_metadata_use_qt_presentation_reference(qtbot):
    data = encoded(73, 109, color="#4080bf40", metadata=True)
    result = decode_prepared_pixmap(prepared(data))
    expected = reference(data)
    assert result.pixmap.hasAlphaChannel()
    assert result.pixmap.toImage() == expected.toImage()
    assert result.pixmap.toImage().pixelColor(0, 0).alpha() == 0x40


def test_thread_and_argument_errors_precede_pixmap_construction(qtbot):
    source = prepared()
    with pytest.raises(TypeError, match="PreparedImage"):
        decode_prepared_pixmap(None)
    with patch.object(presentation.QGuiApplication, "instance", return_value=None), \
            patch.object(presentation.QPixmap, "fromImage", side_effect=AssertionError("Constructed pixmap")):
        with pytest.raises(RuntimeError, match="existing GUI application"):
            decode_prepared_pixmap(source)
    def wrong_owner():
        owner = QObject()  # Its owning thread is not the application's GUI thread.
        assert owner.thread() == QThread.currentThread()
        return decode_prepared_pixmap(source)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="GUI application's thread"):
            pool.submit(wrong_owner).result(timeout=10)


def test_no_processing_storage_or_payload_retention(qtbot, monkeypatch):
    import builtins
    from mtg_proxy_printer.image_derivative_cache import DerivativeCache
    from mtg_proxy_printer import image_processing_pillow
    from mtg_proxy_printer.model import card
    source = prepared(canonical=[745, 1040])
    source_ref = weakref.ref(source)
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise AssertionError("Presentation imported Pillow")
        return original_import(name, *args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError("Presentation performed source/cache/processing I/O")
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(DerivativeCache, "lookup", forbidden)
    monkeypatch.setattr(DerivativeCache, "store", forbidden)
    monkeypatch.setattr(image_processing_pillow, "discover_pillow_runtime", forbidden)
    monkeypatch.setattr(card, "post_process_image", forbidden)
    result = decode_prepared_pixmap(source)
    source.canonical_dimensions[0] = 1
    assert result.canonical_dimensions == (745, 1040)
    del source
    gc.collect()
    assert source_ref() is None
    assert not any(isinstance(getattr(result, field.name), (bytes, PreparedImage, list, dict)) for field in fields(result))
    with pytest.raises(FrozenInstanceError):
        result.target_ppi = 1200
    result.pixmap.setDevicePixelRatio(3)  # Qt value mutability is explicitly not deep immutability.
    assert result.pixmap.devicePixelRatio() == 3

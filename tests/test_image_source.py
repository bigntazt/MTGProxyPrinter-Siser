"""M24 contracts, using only synthetic bytes and temporary original-cache roots."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import os
from pathlib import Path
import struct
from unittest.mock import PropertyMock, patch

import pytest
from PySide6.QtCore import QBuffer, QIODevice, qVersion
from PySide6.QtGui import QColor, QColorSpace, QImage, QImageIOHandler, QImageReader, QPixmap

from mtg_proxy_printer.image_source import (
    DECODE_POLICY, SourceIdentity, SourceRequest, SourceSnapshot, SourceUnavailable,
    capture_source_request, load_source_snapshot, plan_target_resolution,
)
from mtg_proxy_printer.model.card import Card, CheckCard, CustomCard, MTGSet
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from mtg_proxy_printer.units_and_sizes import CardSizes


PRINTING = "12345678-1234-1234-1234-123456789abc"
REGULAR = (745, 1040)


def official(**changes):
    card = Card("Synthetic", MTGSet("m24", "Synthetic"), "1", "en", PRINTING,
                True, PRINTING, "https://invalid.example/synthetic", True,
                CardSizes.REGULAR, False)
    return replace(card, **changes)


def custom(data, **changes):
    card = CustomCard("Synthetic", MTGSet("m24", "Synthetic"), "C1", "en", True,
                      "", True, CardSizes.REGULAR, False, data)
    return replace(card, **changes)


def encoded(width=73, height=109, *, color="#bf4971", image_format="PNG", metadata=False):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    if metadata:
        image.setDotsPerMeterX(11811)  # Approximately 300 PPI; deliberately unrelated to physical size.
        image.setDotsPerMeterY(23622)
        image.setText("M24", "encoded metadata must survive")
        image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.DisplayP3))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, image_format)
    return bytes(buffer.data())


def write_original(root, card, data):
    key = ImageKey(card.scryfall_id, card.is_front, ImageQuality.of_card(card))
    path = root / key.format_relative_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def snapshot(data=None, size=CardSizes.REGULAR):
    request = capture_source_request(custom(data if data is not None else encoded(), size=size), cache_root=None)
    result = load_source_snapshot(request)
    assert isinstance(result, SourceSnapshot)
    return result


def test_official_reads_native_file_without_using_normalized_pixmap(tmp_path):
    card = official(image_file=QPixmap(745, 1040))
    data = encoded(1490, 2080)
    path = write_original(tmp_path, card, data)
    with patch.object(Path, "read_bytes", side_effect=AssertionError("Capture must not read")), \
            patch.object(Card, "image_file", new_callable=PropertyMock, side_effect=AssertionError("No pixmap access")), \
            patch("mtg_proxy_printer.model.imagedb.ImageDatabase", side_effect=AssertionError("No cache init")):
        request = capture_source_request(card, cache_root=tmp_path)
    assert request.original_path == path
    with patch.object(Path, "read_bytes", autospec=True, return_value=data) as read:
        result = load_source_snapshot(request)
    read.assert_called_once_with(path)
    assert result.native_dimensions == (1490, 2080)
    assert result.canonical_dimensions == REGULAR
    assert result.encoded_bytes == data
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert card.image_file.size().toTuple() == REGULAR


@pytest.mark.parametrize("front,highres,bucket", [
    (True, True, "highres_front"), (False, True, "highres_back"),
    (True, False, "lowres_front"), (False, False, "lowres_back"),
])
def test_exact_face_and_quality_paths_without_open_or_fallback(tmp_path, front, highres, bucket):
    card = official(is_front=front, highres_image=highres)
    request = capture_source_request(card, cache_root=tmp_path)
    assert request.original_path == tmp_path / bucket / "12" / (PRINTING + ".png")
    assert request.image_key == ImageKey(PRINTING, front, ImageQuality.of_card(card))
    write_original(tmp_path, replace(card, is_front=not front), encoded())
    write_original(tmp_path, replace(card, highres_image=not highres), encoded())
    assert load_source_snapshot(request).reason == "missing_file"
    assert not request.original_path.exists()


def test_custom_capture_uses_bytes_and_custom_identity_without_image_processing():
    data = encoded()
    card = custom(data, is_front=False)
    with patch.object(CustomCard, "image_file", new_callable=PropertyMock,
                      side_effect=AssertionError("No normalized pixmap")), \
            patch("mtg_proxy_printer.model.card.post_process_image", side_effect=AssertionError("No normalization")), \
            patch.object(ImageQuality, "of_card", side_effect=AssertionError("Custom quality is explicit")):
        request = capture_source_request(card, cache_root=None)
        result = load_source_snapshot(request)
    assert request.original_path is None and request.custom_bytes == data
    assert request.image_key == ImageKey(str(card.scryfall_id), False, ImageQuality.custom_card)
    assert "image_file" not in card.__dict__
    assert result.identity == SourceIdentity(request.image_key, hashlib.sha256(data).hexdigest())


def test_composite_rejected_before_any_component_access():
    composite = CheckCard(official(), official(is_front=False))
    with patch.object(CheckCard, "image_file", new_callable=PropertyMock, side_effect=AssertionError("No composition")), \
            patch.object(CheckCard, "scryfall_id", new_callable=PropertyMock, side_effect=AssertionError("No delegation")), \
            patch.object(CheckCard, "size", new_callable=PropertyMock, side_effect=AssertionError("No delegation")):
        assert capture_source_request(composite, cache_root=None).reason == "unsupported_composite"
    assert isinstance(capture_source_request(composite.front, cache_root=Path.cwd()), SourceRequest)
    assert isinstance(capture_source_request(composite.back, cache_root=Path.cwd()), SourceRequest)


@pytest.mark.parametrize("identity,uri,reason", [
    ("", "", "empty_placeholder"), ("", "image", "invalid_source_identity"),
    ("../bad", "image", "invalid_source_identity"),
    ("12345678-1234-1234-1234-123456789abc/../../bad", "image", "invalid_source_identity"),
    (None, "image", "invalid_source_identity"),
])
def test_placeholder_and_invalid_identity_never_construct_a_path(identity, uri, reason):
    card = official(scryfall_id=identity, image_uri=uri)
    with patch.object(ImageKey, "format_relative_path", side_effect=AssertionError("Must reject before path")), \
            patch.object(Path, "read_bytes", side_effect=AssertionError("No source access")):
        assert capture_source_request(card, cache_root=None).reason == reason


def test_capture_freezes_metadata_and_explicit_root(tmp_path, monkeypatch):
    card = official()
    request = capture_source_request(card, cache_root=tmp_path)
    card.scryfall_id = "abcdefab-1234-1234-1234-123456789abc"
    card.is_front = False
    card.highres_image = False
    card.size = CardSizes.OVERSIZED
    assert request.image_key == ImageKey(PRINTING, True, ImageQuality.high_resolution)
    assert request.canonical_dimensions == REGULAR
    assert request.original_path == tmp_path / "highres_front" / "12" / (PRINTING + ".png")
    monkeypatch.chdir(tmp_path)
    assert capture_source_request(official(), cache_root="relative").original_path.is_absolute()
    with pytest.raises(ValueError, match="explicit"):
        capture_source_request(official(), cache_root=None)
    with pytest.raises(TypeError):
        capture_source_request(object(), cache_root=tmp_path)
    with pytest.raises(FrozenInstanceError):
        request.image_key = ImageKey("changed", True, ImageQuality.low_resolution)


def test_snapshot_reads_once_and_survives_file_replacement(tmp_path):
    card = official()
    old, new = encoded(91, 137), encoded(103, 151, color="blue")
    path = write_original(tmp_path, card, old)
    request = capture_source_request(card, cache_root=tmp_path)
    read_bytes = Path.read_bytes
    reads = []
    def read_then_replace(file):
        data = read_bytes(file)
        reads.append(file)
        file.write_bytes(new)
        return data
    with patch.object(Path, "read_bytes", read_then_replace):
        first = load_source_snapshot(request)
    assert reads == [path]
    assert first.encoded_bytes == old and first.native_dimensions == (91, 137)
    assert first.sha256 == hashlib.sha256(old).hexdigest()
    second = load_source_snapshot(request)
    assert second.encoded_bytes == new and second.native_dimensions == (103, 151)
    assert first.identity != second.identity


def test_identity_ignores_path_mtime_copy_name_and_size_but_distinguishes_face_quality_content(tmp_path):
    data = encoded()
    card = official()
    path = write_original(tmp_path / "a", card, data)
    duplicate = replace(card, name="Other display name", size=CardSizes.OVERSIZED)
    write_original(tmp_path / "b", duplicate, data)
    first = load_source_snapshot(capture_source_request(card, cache_root=tmp_path / "a"))
    second = load_source_snapshot(capture_source_request(duplicate, cache_root=tmp_path / "b"))
    os.utime(path, (1000000000, 1000000000))
    assert first.identity == second.identity == load_source_snapshot(capture_source_request(card, cache_root=tmp_path / "a")).identity
    assert hash(first.identity) == hash(second.identity)  # In-memory equality only, never persisted.
    for changed in (replace(first.image_key, is_front=False),
                    replace(first.image_key, image_quality=ImageQuality.low_resolution)):
        assert replace(first, image_key=changed).identity != first.identity
    assert replace(first, sha256="0"*64).identity != first.identity
    assert set(field.name for field in fields(first.identity)) == {"image_key", "sha256"}


def test_transport_payload_is_excluded_from_repr_comparison_and_hash():
    request = capture_source_request(custom(b"private encoded payload"), cache_root=None)
    assert "private encoded payload" not in repr(request)
    assert request == replace(request, custom_bytes=b"another payload")
    result = snapshot()
    assert "encoded_bytes=" not in repr(result)
    assert result == replace(result, encoded_bytes=b"not compared automatically")
    for value in (request, result):
        with pytest.raises(TypeError):
            hash(value)
        assert all(not isinstance(getattr(value, f.name), (Card, CustomCard, CheckCard, QImage, QPixmap))
                   for f in fields(value))
    with pytest.raises(ValueError):
        SourceRequest(request.image_key, REGULAR)
    with pytest.raises(ValueError):
        replace(request, original_path=Path.cwd())
    with pytest.raises(ValueError):
        SourceRequest(request.image_key, REGULAR, original_path=Path("relative"))
    with pytest.raises(TypeError):
        replace(request, custom_bytes=bytearray(b"mutable"))


def test_expected_failures_are_read_only_and_programming_errors_propagate(tmp_path):
    request = capture_source_request(official(), cache_root=tmp_path)
    assert load_source_snapshot(request).reason == "missing_file"
    assert not (tmp_path / "highres_front").exists()
    with patch.object(Path, "read_bytes", side_effect=PermissionError(13, "Access denied")):
        assert load_source_snapshot(request).reason == "unreadable_file"
    corrupt = b"undecodable synthetic original"
    path = write_original(tmp_path, official(), corrupt)
    before = path.stat()
    assert load_source_snapshot(request).reason == "undecodable_image"
    assert path.read_bytes() == corrupt and path.stat().st_mtime_ns == before.st_mtime_ns
    with patch.object(Path, "read_bytes", side_effect=RuntimeError("programming defect")):
        with pytest.raises(RuntimeError, match="programming defect"):
            load_source_snapshot(request)
    assert load_source_snapshot(capture_source_request(custom(b""), cache_root=None)).reason == "undecodable_image"


def test_content_format_and_actual_decode_not_filename_or_header(tmp_path):
    data = encoded(image_format="JPEG")
    path = write_original(tmp_path, official(), data)  # Existing cache uses .png even for JPEG bytes.
    assert path.suffix == ".png"
    result = load_source_snapshot(capture_source_request(official(), cache_root=tmp_path))
    assert result.image_format in ("jpeg", "jpg")
    assert result.encoded_bytes == data
    # A valid PNG signature/IHDR alone is not a successfully decoded image.
    request = capture_source_request(custom(encoded()[:33]), cache_root=None)
    assert load_source_snapshot(request).reason == "undecodable_image"


@pytest.mark.parametrize("size,ppi,target", [
    (CardSizes.REGULAR, 600, (1490, 2080)), (CardSizes.REGULAR, 1200, (2980, 4160)),
    (CardSizes.OVERSIZED, 600, (2080, 2980)), (CardSizes.OVERSIZED, 1200, (4160, 5960)),
])
def test_exact_targets_derive_from_canonical_physical_size(size, ppi, target):
    original = snapshot(size=size)
    plan = plan_target_resolution(original, ppi)
    assert plan.target_dimensions == target and plan.target_ppi == ppi
    assert plan.source_dimensions == (73, 109)
    assert plan.effective_source_ppi == pytest.approx(tuple(n*300/c for n, c in zip((73, 109), size.as_qsize_px().toTuple())))
    assert not plan.sufficient


@pytest.mark.parametrize("dimensions,sufficient", [
    ((745, 1040), False), ((1490, 2080), True), ((2980, 4160), True),
    ((1490, 2079), False), ((1489, 4160), False),
])
def test_both_axes_control_sufficiency_and_target_is_always_exact(dimensions, sufficient):
    original = snapshot(encoded(*dimensions))
    plan = plan_target_resolution(original, 600)
    assert plan.source_dimensions == dimensions
    assert plan.sufficient is sufficient
    assert plan.target_dimensions == (1490, 2080)


@pytest.mark.parametrize("target", [300, 601, 2400, 600.0, True, "600", None])
def test_target_must_be_explicit_supported_integer(target):
    with pytest.raises(ValueError, match="integer 600 or 1200"):
        plan_target_resolution(snapshot(), target)


def test_same_custom_source_identity_at_different_physical_sizes_has_different_plans():
    data = encoded(1490, 2080)
    regular = snapshot(data)
    oversized = snapshot(data, CardSizes.OVERSIZED)
    assert regular.identity == oversized.identity
    small = plan_target_resolution(regular, 600)
    large = plan_target_resolution(oversized, 600)
    assert small != large and small.sufficient and not large.sufficient
    assert small.target_dimensions == (1490, 2080) and large.target_dimensions == (2080, 2980)


def test_metadata_policy_preserves_bytes_alpha_color_and_ignores_embedded_dpi():
    data = encoded(91, 137, color="#4080bf40", metadata=True)
    original = QImage.fromData(data)
    assert original.dotsPerMeterX() == 11811 and original.dotsPerMeterY() == 23622
    result = snapshot(data)
    assert result.encoded_bytes == data and result.native_dimensions == (91, 137)
    assert result.decode_policy == DECODE_POLICY and result.qt_decoder_version == qVersion()
    assert plan_target_resolution(result, 600).effective_source_ppi == pytest.approx((91*300/745, 137*300/1040))
    image = QImage.fromData(result.encoded_bytes)
    assert image.pixelColor(0, 0).alpha() == 0x40
    assert image.colorSpace() == original.colorSpace()
    assert image.text("M24") == original.text("M24")


def test_orientation_is_explicitly_disabled_and_allocation_limit_unchanged():
    jpeg = encoded(31, 47, image_format="JPEG")
    # Minimal EXIF TIFF IFD containing orientation 6 (90 degrees clockwise).
    tiff = b"II\x2a\x00\x08\x00\x00\x00" + struct.pack("<H", 1)
    tiff += struct.pack("<HHI", 0x112, 3, 1) + struct.pack("<H", 6) + b"\x00\x00" + b"\x00"*4
    exif = b"Exif\x00\x00" + tiff
    data = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif)+2) + exif + jpeg[2:]
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    assert reader.transformation() == QImageIOHandler.Transformation.TransformationRotate90
    reader.setAutoTransform(True)
    assert reader.read().size().toTuple() == (47, 31)
    limit = QImageReader.allocationLimit()
    result = snapshot(data)
    assert result.native_dimensions == (31, 47) and result.encoded_bytes == data
    assert QImageReader.allocationLimit() == limit


def test_first_frame_only_and_loader_can_run_on_worker_without_model_or_pixmap():
    # Two-frame 1x1 GIF: global red/blue palette, red first frame and blue second.
    header = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\x00\x00\x00\x00\xff"
    descriptor = b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00"
    data = header + descriptor + b"\x02\x02\x44\x01\x00" + descriptor + b"\x02\x02\x4c\x01\x00\x3b"
    request = capture_source_request(custom(data), cache_root=None)
    decoded_colors = []
    class RecordingReader(QImageReader):
        def read(self):
            image = super().read()
            decoded_colors.append(image.pixelColor(0, 0))
            return image
    with patch("PySide6.QtGui.QPixmap", side_effect=AssertionError("No worker pixmaps")), \
            patch("mtg_proxy_printer.image_source.QImageReader", RecordingReader), \
            patch.object(CustomCard, "image_file", new_callable=PropertyMock, side_effect=AssertionError("No model access")), \
            patch.object(CustomCard, "scryfall_id", new_callable=PropertyMock, side_effect=AssertionError("No model access")), \
            ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(load_source_snapshot, request).result(timeout=10)
    assert result.native_dimensions == (1, 1) and result.image_format == "gif"
    assert result.encoded_bytes == data
    assert decoded_colors == [QColor("red")]
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    assert reader.read().pixelColor(0, 0) == QColor("red")
    assert reader.read().pixelColor(0, 0) == QColor("blue")

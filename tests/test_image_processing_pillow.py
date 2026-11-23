"""Synthetic processor policy checks; Pillow never decodes an original source."""
from dataclasses import replace
from io import BytesIO
import builtins
import struct
from unittest.mock import patch

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QPointF
from PySide6.QtGui import QColor, QColorSpace, QImage, QPainter

from mtg_proxy_printer import image_processing_pillow as processor
from mtg_proxy_printer.image_source import SourceRequest, load_source_snapshot, SourceSnapshot
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality


def patterned_image(width=37, height=53):
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor("#00000000"))
    painter = QPainter(image)
    painter.fillRect(0, 0, width//2, height//3, QColor("red"))
    painter.fillRect(width//2, 0, width-width//2, height//3, QColor("green"))
    painter.fillRect(0, height//3, width//3, height-height//3, QColor("blue"))
    painter.fillRect(width//3, height//3, width-width//3, height-height//3, QColor("#80ffaa00"))
    painter.end()
    return image


def encoded_image(image=None, image_format="PNG", metadata=False):
    image = patterned_image() if image is None else image
    if metadata:
        image.setDotsPerMeterX(11811)
        image.setDotsPerMeterY(23622)
        image.setText("M26", "source text must not leak")
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, image_format)
    return bytes(buffer.data())


def snapshot_from_bytes(data, *, canonical=(745, 1040), front=True):
    key = ImageKey("12345678-1234-1234-1234-123456789abc", front, ImageQuality.custom_card)
    result = load_source_snapshot(SourceRequest(key, canonical, custom_bytes=data))
    assert isinstance(result, SourceSnapshot)
    return result


def make_source(**kwargs):
    return snapshot_from_bytes(encoded_image(**kwargs))


def oriented_jpeg():
    jpeg = encoded_image(patterned_image(31, 47).convertToFormat(QImage.Format.Format_RGB32), "JPEG", True)
    tiff = b"II\x2a\x00\x08\x00\x00\x00" + struct.pack("<H", 1)
    tiff += struct.pack("<HHI", 0x112, 3, 1) + struct.pack("<H", 6) + b"\x00\x00" + b"\x00"*4
    exif = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif)+2) + exif + jpeg[2:]


@pytest.fixture(scope="module")
def runtime():
    return processor.discover_pillow_runtime()


def test_runtime_recipe_records_actual_versions_and_policies(runtime):
    from PIL import __version__, features
    from PySide6 import __version__ as pyside
    from PySide6.QtCore import qVersion
    spec = runtime.recipe().to_spec()
    assert spec["provider"] == {"id": "pillow-lanczos", "version": __version__}
    assert spec["model"] is None
    assert spec["parameters"]["runtime"] == {"qt": qVersion(), "pyside6": pyside, "pillow_zlib": features.version_codec("zlib")}
    assert spec["parameters"]["decode"]["auto_transform"] is False
    assert spec["parameters"]["resize"]["reducing_gap"] is None
    assert spec["parameters"]["encoding"]["compress_level"] == 4
    assert spec["parameters"]["encoding"]["optimize"] is False


def test_missing_png_codec_and_broken_import_are_unavailable():
    from PIL import features
    with patch.object(features, "check_codec", return_value=False):
        with pytest.raises(processor.ProcessingFailure, match="PNG/zlib") as error:
            processor.discover_pillow_runtime()
    assert error.value.reason == "provider_unavailable"
    original_import = builtins.__import__
    def broken_import(name, *args, **kwargs):
        if name == "PIL":
            raise OSError("Synthetic broken optional DLL")
        return original_import(name, *args, **kwargs)
    with patch.object(builtins, "__import__", side_effect=broken_import):
        with pytest.raises(processor.ProcessingFailure) as error:
            processor.discover_pillow_runtime()
    assert error.value.reason == "provider_unavailable"
    with patch.object(features, "version_codec", return_value=None):
        with pytest.raises(processor.ProcessingFailure) as error:
            processor.discover_pillow_runtime()
    assert error.value.reason == "provider_unavailable"


@pytest.mark.parametrize("image_format", processor.SUPPORTED_FORMATS)
def test_supported_qt_formats_normalize_without_color_change(runtime, image_format):
    image = patterned_image(9, 11).convertToFormat(image_format)
    source = make_source()
    with patch.object(processor, "_decode_source", return_value=image):
        output = runtime.produce_png(source, (9, 11))
    actual = QImage.fromData(output).convertToFormat(QImage.Format.Format_RGBA8888)
    expected = image.convertToFormat(QImage.Format.Format_RGBA8888)
    assert actual == expected
    assert output[24:26] == bytes((8, 6))  # PNG RGBA8 IHDR fields.


def test_real_rgb_indexed_grayscale_sources_and_byte_determinism(runtime):
    for image_format in (QImage.Format.Format_RGB888, QImage.Format.Format_Indexed8, QImage.Format.Format_Grayscale8):
        source = make_source(image=patterned_image(13, 17).convertToFormat(image_format))
        first = runtime.produce_png(source, (29, 31))
        assert first == runtime.produce_png(source, (29, 31))
        assert QImage.fromData(first).size().toTuple() == (29, 31)


def test_equal_size_skips_resize_and_source_decoding_is_qt_only(runtime):
    from PIL import Image
    source = make_source(metadata=True)
    with patch.object(Image, "open", side_effect=AssertionError("No Pillow source decode")), \
            patch.object(Image.Image, "resize", side_effect=AssertionError("No interpolation at equal dimensions")), \
            patch("PySide6.QtGui.QPixmap", side_effect=AssertionError("No QPixmap")):
        output = runtime.produce_png(source, source.native_dimensions)
    assert QImage.fromData(output).size().toTuple() == source.native_dimensions


@pytest.mark.parametrize("source_dimensions,target", [((61, 79), (17, 23)), ((13, 29), (31, 11))])
def test_downsampling_and_mixed_axes_use_full_exact_target(runtime, source_dimensions, target):
    source = make_source(image=patterned_image(*source_dimensions))
    output = QImage.fromData(runtime.produce_png(source, target))
    assert output.size().toTuple() == target
    assert output.pixelColor(1, 1).red() > 200
    assert output.pixelColor(1, target[1]-2).blue() > 200


def test_actual_qt_row_stride_is_used(runtime):
    raw = bytearray(32*3)
    image = QImage(raw, 5, 3, 32, QImage.Format.Format_RGBA8888)
    image.fill(QColor("white"))
    for row, color in enumerate(("red", "green", "blue")):
        image.setPixelColor(0, row, QColor(color))
    assert image.bytesPerLine() == 32
    with patch.object(processor, "_decode_source", return_value=image):
        output = QImage.fromData(runtime.produce_png(make_source(), (5, 3)))
    assert [output.pixelColor(0, row) for row in range(3)] == [QColor(c) for c in ("red", "green", "blue")]


def test_transparent_colored_edges_use_associated_alpha(runtime):
    image = QImage(4, 2, QImage.Format.Format_RGBA8888)
    for row in range(2):
        for column, color in enumerate((QColor(255, 0, 0, 255), QColor(255, 0, 0, 128), QColor(0, 0, 255, 0), QColor(0, 0, 255, 0))):
            image.setPixelColor(column, row, color)
    source = make_source(image=image)
    output = QImage.fromData(runtime.produce_png(source, (29, 7)))
    transparent = []
    partially_opaque = []
    for x in range(output.width()):
        color = output.pixelColor(x, 3)
        if color.alpha() == 0: transparent.append(color)
        if 0 < color.alpha() < 255: partially_opaque.append(color)
    assert transparent and partially_opaque
    assert all(color.red() >= 250 and color.blue() == 0 and color.green() == 0 for color in partially_opaque)


def test_supported_rgb_profile_is_preserved_without_conversion_and_untagged_stays_untagged(runtime):
    from PIL import Image
    tagged = patterned_image(17, 23)
    tagged.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.DisplayP3))
    source = make_source(image=tagged, metadata=True)
    decoded = processor._decode_source(source)
    expected_icc = bytes(decoded.colorSpace().iccProfile())
    output = runtime.produce_png(source, source.native_dimensions)
    with Image.open(BytesIO(output)) as image:
        assert image.info["icc_profile"] == expected_icc
        assert "dpi" not in image.info and "exif" not in image.info and "M26" not in image.info
    assert QImage.fromData(output).pixelColor(1, 1) == decoded.pixelColor(1, 1)
    untagged = runtime.produce_png(make_source(), (19, 29))
    with Image.open(BytesIO(untagged)) as image:
        assert "icc_profile" not in image.info and "srgb" not in image.info
    assert not QImage.fromData(untagged).colorSpace().isValid()


def test_orientation_and_dpi_metadata_do_not_leak_or_rotate(runtime):
    from PIL import Image
    source = snapshot_from_bytes(oriented_jpeg())
    assert source.native_dimensions == (31, 47)
    output = runtime.produce_png(source, (62, 94))
    image = QImage.fromData(output)
    assert image.size().toTuple() == (62, 94)
    assert image.pixelColor(4, 4).red() > 200 and image.pixelColor(4, 89).blue() > 200
    with Image.open(BytesIO(output)) as png:
        assert not png.getexif() and "exif" not in png.info and "dpi" not in png.info


def test_real_high_depth_source_is_unsupported(runtime):
    source = make_source(image=patterned_image().convertToFormat(QImage.Format.Format_RGBA64))
    assert processor._decode_source(source).format() not in processor.SUPPORTED_FORMATS
    with pytest.raises(processor.ProcessingFailure) as error:
        runtime.produce_png(source, (19, 29))
    assert error.value.reason == "unsupported_source"


def test_gray_profile_and_invalid_exposed_profile_are_not_attached_to_rgba(runtime):
    gray = patterned_image().convertToFormat(QImage.Format.Format_Grayscale8)
    space = QColorSpace(QPointF(0.3127, 0.3290), QColorSpace.TransferFunction.Gamma, 2.2)
    assert space.isValid() and space.colorModel() == QColorSpace.ColorModel.Gray
    gray.setColorSpace(space)
    source = make_source(image=gray)
    with pytest.raises(processor.ProcessingFailure) as error:
        runtime.produce_png(source, (19, 29))
    assert error.value.reason == "unsupported_source"
    class InvalidWithProfile:
        def isValid(self): return False
        def iccProfile(self): return b"exposed but invalid profile"
    with patch.object(QImage, "colorSpace", return_value=InvalidWithProfile()):
        with pytest.raises(processor.ProcessingFailure) as error:
            processor._rgb_profile(patterned_image())
    assert error.value.reason == "unsupported_source"
    class ValidRgbWithoutProfile:
        def isValid(self): return True
        def iccProfile(self): return b""
        def colorModel(self): return QColorSpace.ColorModel.Rgb
    with patch.object(QImage, "colorSpace", return_value=ValidRgbWithoutProfile()):
        with pytest.raises(processor.ProcessingFailure) as error:
            processor._rgb_profile(patterned_image())
    assert error.value.reason == "processing_failed"


def test_decode_disagreement_and_expected_encoding_failure(runtime):
    source = make_source()
    with pytest.raises(processor.ProcessingFailure) as error:
        runtime.produce_png(replace(source, native_dimensions=(1, 1)), (17, 19))
    assert error.value.reason == "source_decode_failed"
    with pytest.raises(processor.ProcessingFailure) as error:
        runtime.produce_png(replace(source, encoded_bytes=b"undecodable"), (17, 19))
    assert error.value.reason == "source_decode_failed"
    with patch.object(runtime.image_module.Image, "save", side_effect=OSError("Synthetic encoding failure")):
        with pytest.raises(processor.ProcessingFailure) as error:
            runtime.produce_png(source, (17, 19))
    assert error.value.reason == "processing_failed"
    with patch.object(runtime.image_module.Image, "save", side_effect=RuntimeError("programming defect")):
        with pytest.raises(RuntimeError, match="programming defect"):
            runtime.produce_png(source, (17, 19))

"""Optional, synchronous Lanczos processor. Importing this module never imports Pillow."""
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QBuffer, QIODevice, qVersion
from PySide6.QtGui import QColorSpace, QImage, QImageReader

from mtg_proxy_printer.image_derivative import ProcessingRecipe
from mtg_proxy_printer.image_source import SourceSnapshot


SUPPORTED_FORMATS = (
    QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32, QImage.Format.Format_ARGB32_Premultiplied,
    QImage.Format.Format_RGB888, QImage.Format.Format_RGBX8888, QImage.Format.Format_RGBA8888,
    QImage.Format.Format_RGBA8888_Premultiplied, QImage.Format.Format_Indexed8,
    QImage.Format.Format_Grayscale8, QImage.Format.Format_Mono, QImage.Format.Format_MonoLSB,
)


class ProcessingFailure(Exception):
    """Expected optional-provider/source/processing failure, not input validation."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _decode_source(snapshot: SourceSnapshot) -> QImage:
    buffer = QBuffer()
    buffer.setData(snapshot.encoded_bytes)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        raise ProcessingFailure("source_decode_failed", "Cannot open the captured source buffer")
    reader = QImageReader(buffer)
    reader.setAutoDetectImageFormat(True)
    reader.setAutoTransform(False)
    detected_format = bytes(reader.format()).decode("ascii")
    image = reader.read()  # First frame, no scaled decode or orientation transform.
    if image.isNull():
        raise ProcessingFailure("source_decode_failed", reader.errorString())
    if ((image.width(), image.height()) != tuple(snapshot.native_dimensions)
            or detected_format != snapshot.image_format):
        raise ProcessingFailure("source_decode_failed", "Decoded source disagrees with the snapshot")
    return image


def _rgb_profile(image: QImage) -> bytes | None:
    space = image.colorSpace()
    profile = bytes(space.iccProfile())
    if not space.isValid():
        if profile:
            raise ProcessingFailure("unsupported_source", "Invalid color space exposes an ICC profile")
        return None
    if space.colorModel() != QColorSpace.ColorModel.Rgb:
        raise ProcessingFailure("unsupported_source", "Only RGB color profiles are supported")
    if not profile:
        raise ProcessingFailure("processing_failed", "Valid RGB color space provides no encodable ICC profile")
    return profile


def decode_source_rgba(snapshot: SourceSnapshot) -> tuple[QImage, bytes | None]:
    """Shared production decode policy, without Pillow discovery or source reopening."""
    image = _decode_source(snapshot)
    if image.format() not in SUPPORTED_FORMATS:
        raise ProcessingFailure("unsupported_source", "Unsupported Qt decoded format: " + image.format().name)
    profile = _rgb_profile(image)
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    if rgba.isNull():
        raise ProcessingFailure("processing_failed", "Straight RGBA8888 normalization failed")
    return rgba, profile


@dataclass(frozen=True)
class PillowRuntime:
    """One resolved runtime used for both identity and production, never in results."""

    image_module: Any = field(repr=False, compare=False)
    pillow_version: str
    qt_version: str
    pyside_version: str
    zlib_version: str

    def recipe(self) -> ProcessingRecipe:
        return ProcessingRecipe.from_spec({
            "schema_version": 1, "pipeline_version": "m26-image-preparation-v1",
            "provider": {"id": "pillow-lanczos", "version": self.pillow_version},
            "operation": "whole-source-exact-canonical-target-v1", "model": None,
            "policies": {
                "decode": "qt-first-frame-straight-rgba8888-v1", "orientation": "no-auto-transform-v1",
                "color": "qt-exposed-rgb-icc-or-untagged-no-conversion-v1",
                "alpha": "straight-input-pillow-associated-alpha-resize-v1", "mask": "none",
                "resize": "pillow-full-image-lanczos-v1",
                "encoding": "rgba8-png-level4-no-metadata-except-rgb-icc-v1",
            },
            "parameters": {
                "runtime": {"qt": self.qt_version, "pyside6": self.pyside_version, "pillow_zlib": self.zlib_version},
                "decode": {"frame": 0, "auto_transform": False, "scaled_decode": False,
                           "normalization": "straight-rgba8888",
                           "supported_qt_formats": [value.name for value in SUPPORTED_FORMATS]},
                "color": {"conversion": "none", "sample_space": "qt-decoded",
                          "rgb_profile": "preserve-qt-exposed-icc", "untagged": "leave-untagged",
                          "non_rgb": "unsupported", "invalid_with_icc": "unsupported"},
                "alpha": {"preserve": True, "resampling": "pillow-rgba-associated-alpha"},
                "resize": {"filter": "LANCZOS", "reducing_gap": None, "crop": None, "padding": None,
                           "whole_image": True, "equal_size": "skip-interpolation"},
                "mask": "none",
                "encoding": {"format": "PNG", "mode": "RGBA", "compress_level": 4, "optimize": False,
                             "metadata": "only-preserved-qt-rgb-icc", "dpi": None, "exif": None},
            },
        })

    def produce_png(self, snapshot: SourceSnapshot, target_dimensions: tuple[int, int]) -> bytes:
        rgba, profile = decode_source_rgba(snapshot)
        try:
            # frombytes copies the straight samples using Qt's actual row stride.
            with self.image_module.frombytes("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()),
                                             "raw", "RGBA", rgba.bytesPerLine(), 1) as source:
                resized = None
                try:
                    if source.size == target_dimensions:
                        output = source
                    else:
                        resized = source.resize(target_dimensions, resample=self.image_module.Resampling.LANCZOS,
                                                reducing_gap=None)
                        output = resized
                    output.info.clear()  # Raw samples carry no source EXIF/DPI/text; preserve only declared RGB ICC.
                    options = {"compress_level": 4, "optimize": False}
                    if profile is not None:
                        options["icc_profile"] = profile
                    with BytesIO() as stream:
                        output.save(stream, format="PNG", **options)
                        return stream.getvalue()
                finally:
                    if resized is not None:
                        resized.close()
        except (OSError, ValueError, MemoryError) as error:
            raise ProcessingFailure("processing_failed", "Pillow processing/encoding failed: " + str(error)) from error


def discover_pillow_runtime() -> PillowRuntime:
    """Resolve current optional runtime once; never install, download or probe old manifests."""
    try:
        from PIL import Image, PngImagePlugin, features, __version__
    except (ImportError, OSError) as error:
        raise ProcessingFailure("provider_unavailable", "Optional Pillow runtime cannot be imported: " + str(error)) from error
    try:
        # Import registers the PNG writer without initializing every unrelated format plugin.
        if not PngImagePlugin or "PNG" not in Image.SAVE or not features.check_codec("zlib"):
            raise ProcessingFailure("provider_unavailable", "Pillow PNG/zlib encoding is unavailable")
        zlib = features.version_codec("zlib")
        if not __version__ or not zlib:
            raise ProcessingFailure("provider_unavailable", "Pillow runtime versions cannot be resolved")
        return PillowRuntime(Image, __version__, qVersion(), pyside_version, zlib)
    except (ImportError, OSError, AttributeError, ValueError) as error:
        raise ProcessingFailure("provider_unavailable", "Optional PNG runtime is unusable: " + str(error)) from error

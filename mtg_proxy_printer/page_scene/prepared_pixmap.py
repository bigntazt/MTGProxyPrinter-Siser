"""GUI-thread presentation of already prepared bytes, without processing or storage access."""
from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QIODevice, QThread
from PySide6.QtGui import QGuiApplication, QImageReader, QPixmap

from mtg_proxy_printer.image_preparation import PreparedImage
from mtg_proxy_printer.image_source import SUPPORTED_TARGET_PPI, SourceIdentity
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality


@dataclass(frozen=True)
class PreparedPixmap:
    """Frozen metadata bindings; pixmap is an owned, mutable Qt value, not deeply immutable."""

    source_identity: SourceIdentity
    canonical_dimensions: tuple[int, int]
    actual_dimensions: tuple[int, int]
    target_ppi: int
    target_dimensions: tuple[int, int]
    image_format: str
    outcome: str
    pixmap: QPixmap
    derivative_key: str | None = None
    reason: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class RasterDecodeFailure:
    reason: str
    detail: str


def require_gui_thread():
    """QPixmap work requires the application GUI thread, not an arbitrary QObject owner."""
    application = QGuiApplication.instance()
    if not isinstance(application, QGuiApplication):
        raise RuntimeError("Prepared raster presentation requires an existing GUI application")
    if QThread.currentThread() != application.thread():
        raise RuntimeError("Prepared raster presentation requires the GUI application's thread")


def _dimensions_valid(value):
    return (isinstance(value, (tuple, list)) and len(value) == 2
            and all(type(axis) is int and axis > 0 for axis in value))


def decode_prepared_pixmap(prepared: PreparedImage) -> PreparedPixmap | RasterDecodeFailure:
    """Decode the exact first frame; never resize, transform, mask or reopen a source."""
    if not isinstance(prepared, PreparedImage):
        raise TypeError("Expected a PreparedImage")
    require_gui_thread()  # Check before any QPixmap construction, even on invalid data.
    if not all(_dimensions_valid(value) for value in (
            prepared.canonical_dimensions, prepared.actual_dimensions, prepared.target_dimensions)):
        return RasterDecodeFailure("invalid_metadata", "Declared dimensions must contain two positive integers")
    if (type(prepared.target_ppi) is not int or prepared.target_ppi not in SUPPORTED_TARGET_PPI
            or tuple(prepared.target_dimensions) != tuple(
                axis * (prepared.target_ppi // 300) for axis in prepared.canonical_dimensions)):
        return RasterDecodeFailure("invalid_metadata", "Target dimensions must agree with canonical dimensions and PPI")
    if not isinstance(prepared.source_identity, SourceIdentity) or not isinstance(
            prepared.source_identity.image_key, ImageKey):
        return RasterDecodeFailure("invalid_metadata", "A source identity and ImageKey are required")
    identity, key = prepared.source_identity, prepared.source_identity.image_key
    if (type(identity.sha256) is not str or not isinstance(key.scryfall_id, str)
            or type(key.is_front) is not bool or type(key.image_quality) is not ImageQuality
            or type(prepared.outcome) is not str
            or prepared.outcome not in ("original", "fallback_original", "cache_hit", "generated_cached", "generated_uncached")
            or any(value is not None and type(value) is not str for value in (
                prepared.derivative_key, prepared.reason)) or type(prepared.detail) is not str):
        return RasterDecodeFailure("invalid_metadata", "Presentation metadata must contain immutable identity and outcome values")
    if (type(prepared.encoded_bytes) is not bytes or type(prepared.image_format) is not str
            or not prepared.image_format):
        return RasterDecodeFailure("invalid_metadata", "Encoded bytes and a declared Qt image format are required")
    buffer = QBuffer()
    buffer.setData(prepared.encoded_bytes)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("Could not open the in-memory prepared-image buffer")
    reader = QImageReader(buffer)
    reader.setAutoDetectImageFormat(True)
    reader.setAutoTransform(False)
    # Some Qt handlers clear format after reading. M24 uses this same convention.
    detected_format = bytes(reader.format()).decode("ascii")
    image = reader.read()
    if image.isNull():
        return RasterDecodeFailure("decode_failed", reader.errorString())
    if detected_format != prepared.image_format:
        return RasterDecodeFailure("format_mismatch", "Detected Qt format differs from the declared format")
    actual = tuple(prepared.actual_dimensions)
    if image.size().toTuple() != actual:
        return RasterDecodeFailure("dimension_mismatch", "Decoded dimensions differ from declared actual dimensions")
    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull() or pixmap.size().toTuple() != actual:
        return RasterDecodeFailure("pixmap_failed", "Qt could not create a pixmap with the decoded dimensions")
    pixmap.setDevicePixelRatio(1.0)
    # Copy only immutable metadata. Do not retain prepared or its encoded payload.
    return PreparedPixmap(
        SourceIdentity(ImageKey(str(key.scryfall_id), key.is_front, key.image_quality), identity.sha256),
        tuple(prepared.canonical_dimensions), actual, prepared.target_ppi,
        tuple(prepared.target_dimensions), prepared.image_format, prepared.outcome, pixmap,
        prepared.derivative_key, prepared.reason, prepared.detail)

"""Read-only original-image snapshots and explicit target planning, without rendering adoption."""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import QBuffer, QIODevice, qVersion
from PySide6.QtGui import QImageReader

from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from mtg_proxy_printer.units_and_sizes import RESOLUTION, UUID

if TYPE_CHECKING:
    from mtg_proxy_printer.model.card import AnyCardType


SUPPORTED_TARGET_PPI = (600, 900, 1200)
DECODE_POLICY = "qt-first-frame-no-auto-transform-v1"
Dimensions = tuple[int, int]
UnavailableReason = Literal[
    "unsupported_composite", "empty_placeholder", "invalid_source_identity",
    "missing_file", "unreadable_file", "undecodable_image",
]


@dataclass(frozen=True)
class SourceUnavailable:
    reason: UnavailableReason
    detail: str


@dataclass(frozen=True)
class SourceIdentity:
    """Raw source identity, not a derivative recipe or persistent Python hash."""

    image_key: ImageKey
    sha256: str


@dataclass(frozen=True)
class SourceRequest:
    """Captured metadata, not a promise about file contents at capture time."""

    image_key: ImageKey
    canonical_dimensions: Dimensions
    original_path: Path | None = None
    custom_bytes: bytes | None = field(default=None, repr=False, compare=False)
    __hash__ = None

    def __post_init__(self):
        if (self.original_path is None) == (self.custom_bytes is None):
            raise ValueError("A source request requires exactly one path or byte payload")
        if self.original_path is not None and not self.original_path.is_absolute():
            raise ValueError("An original source path must be absolute")
        if self.custom_bytes is not None and not isinstance(self.custom_bytes, bytes):
            raise TypeError("Captured custom sources must be immutable bytes")


@dataclass(frozen=True)
class SourceSnapshot:
    """Validated encoded bytes; no decoded image, card, document or cache reference."""

    image_key: ImageKey
    sha256: str
    encoded_bytes: bytes = field(repr=False, compare=False)
    native_dimensions: Dimensions
    canonical_dimensions: Dimensions
    image_format: str
    decode_policy: str
    qt_decoder_version: str
    __hash__ = None

    @property
    def identity(self) -> SourceIdentity:
        return SourceIdentity(self.image_key, self.sha256)


@dataclass(frozen=True)
class ResolutionPlan:
    source_identity: SourceIdentity
    canonical_dimensions: Dimensions
    target_ppi: int
    target_dimensions: Dimensions
    source_dimensions: Dimensions
    effective_source_ppi: tuple[float, float]
    sufficient: bool


def capture_source_request(card: "AnyCardType", *, cache_root: str | Path | None
                           ) -> SourceRequest | SourceUnavailable:
    """Capture on the model thread; never open/decode images or initialize a cache.

    cache_root is required explicitly, but may be None for a custom/composite
    source, where it is unused. Ordinary cards require an explicit filesystem root.
    """
    # Keep model access/imports on the capture path, outside the worker-facing loader.
    from mtg_proxy_printer.model.card import Card, CheckCard, CustomCard

    if isinstance(card, CheckCard):
        return SourceUnavailable("unsupported_composite", "Composed CheckCard sources are not supported")
    if isinstance(card, CustomCard):
        key = ImageKey(str(card.scryfall_id), card.is_front, ImageQuality.custom_card)
        dimensions = card.size.as_qsize_px().toTuple()
        return SourceRequest(key, dimensions, custom_bytes=bytes(card.source_image_file))
    if not isinstance(card, Card):
        raise TypeError("Source capture requires a Card, CustomCard or CheckCard")
    if not card.scryfall_id and not card.image_uri:
        return SourceUnavailable("empty_placeholder", "Empty placeholders have no original image")
    if not isinstance(card.scryfall_id, str) or not UUID.uuid_re.fullmatch(card.scryfall_id):
        return SourceUnavailable("invalid_source_identity", "Printing identity must be a canonical UUID")
    if cache_root is None:
        raise ValueError("An explicit original cache root is required for ordinary cards")
    key = ImageKey(card.scryfall_id, card.is_front, ImageQuality.of_card(card))
    dimensions = card.size.as_qsize_px().toTuple()
    # absolute() freezes relative-root interpretation without reading/cache discovery.
    path = Path(cache_root).absolute() / key.format_relative_path()
    return SourceRequest(key, dimensions, original_path=path)


def load_source_snapshot(request: SourceRequest) -> SourceSnapshot | SourceUnavailable:
    """Read once, then hash and decode that same immutable byte buffer.

    First frame only, orientation transforms disabled, no resize/crop/mask,
    color conversion or alpha flattening. Embedded DPI is not physical geometry.
    Qt allocation limits are left unchanged; a rejected decode is unavailable.
    """
    if request.original_path is not None:
        try:
            data = request.original_path.read_bytes()
        except FileNotFoundError:
            return SourceUnavailable("missing_file", "Requested original image file is missing")
        except OSError as error:
            return SourceUnavailable("unreadable_file", f"Cannot read original image: {error.strerror or error}")
    else:
        data = request.custom_bytes
    digest = hashlib.sha256(data).hexdigest()
    buffer = QBuffer()
    buffer.setData(data)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("Could not open the in-memory source buffer")
    reader = QImageReader(buffer)
    reader.setAutoDetectImageFormat(True)
    reader.setAutoTransform(False)
    # Some handlers clear format() after read(); inspect this same buffer first.
    image_format = bytes(reader.format()).decode("ascii")
    image = reader.read()
    if image.isNull():
        return SourceUnavailable("undecodable_image", reader.errorString())
    dimensions = image.width(), image.height()
    return SourceSnapshot(request.image_key, digest, data, dimensions,
                          request.canonical_dimensions, image_format, DECODE_POLICY, qVersion())


def plan_target_resolution(snapshot: SourceSnapshot, target_ppi: int) -> ResolutionPlan:
    """Plan only. Sufficiency requires both axes; never resize or select a provider."""
    if type(target_ppi) is not int or target_ppi not in SUPPORTED_TARGET_PPI:
        raise ValueError("Target PPI must be the integer 600, 900 or 1200")
    base_ppi = RESOLUTION.to("dots/inch").magnitude
    target = tuple(round(dimension*target_ppi/base_ppi) for dimension in snapshot.canonical_dimensions)
    effective = tuple(native*base_ppi/canonical for native, canonical in
                      zip(snapshot.native_dimensions, snapshot.canonical_dimensions))
    sufficient = all(native >= needed for native, needed in zip(snapshot.native_dimensions, target))
    return ResolutionPlan(snapshot.identity, snapshot.canonical_dimensions, target_ppi,
                          target, snapshot.native_dimensions, effective, sufficient)

"""Inactive derivative identity contract. No processing or application adoption."""
from dataclasses import dataclass
import hashlib
import json
import math
import re

from mtg_proxy_printer.image_source import SUPPORTED_TARGET_PPI, SourceIdentity, SourceSnapshot, ResolutionPlan, plan_target_resolution
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from mtg_proxy_printer.units_and_sizes import RESOLUTION, UUID


def _validate_json(value, ancestors=None):
    """Only ordinary JSON values, with cycles and nonfinite numbers rejected."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if not isinstance(value, (dict, list)):
        raise TypeError("Only JSON objects, arrays and scalars are supported")
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        raise ValueError("Cyclic JSON structures are not supported")
    ancestors.add(id(value))
    try:
        if isinstance(value, dict):
            if any(type(key) is not str for key in value):
                raise TypeError("JSON object keys must be strings")
            values = value.values()
        else:
            values = value
        for child in values:
            _validate_json(child, ancestors)
    finally:
        ancestors.remove(id(value))


def _canonical_json(value) -> bytes:
    _validate_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _load_json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("Invalid JSON numeric constant: " + value)
    value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    _validate_json(value)
    return value


def _fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("Object fields do not match schema version 1")


def _text(value):
    if type(value) is not str or not value.strip():
        raise ValueError("Expected a nonempty string")


def _digest(value):
    if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError("Expected a lowercase SHA-256 digest")


def _dimensions(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2 or any(
            type(axis) is not int or axis <= 0 for axis in value):
        raise ValueError("Dimensions must contain two positive integers")
    return tuple(value)


def _version(value):
    if type(value) is not int or value != 1:
        raise ValueError("Unsupported schema version")


def _image_key(value):
    _fields(value, ("scryfall_id", "is_front", "image_quality"))
    if not isinstance(value["scryfall_id"], str) or not UUID.uuid_re.fullmatch(value["scryfall_id"]):
        raise ValueError("Invalid printing/custom identity")
    if type(value["is_front"]) is not bool:
        raise ValueError("Face flag must be boolean")
    quality = value["image_quality"]
    if type(quality) is not int or quality not in (1, 2, 4):
        raise ValueError("Expected one exact image-quality category")


def _key_spec(key):
    if not isinstance(key, ImageKey) or type(key.image_quality) is not ImageQuality:
        raise ValueError("Expected an ImageKey with an ImageQuality category")
    result = {"scryfall_id": key.scryfall_id, "is_front": key.is_front, "image_quality": int(key.image_quality)}
    _image_key(result)
    # The application's validated UUID is a str subclass; serialize its string value.
    result["scryfall_id"] = str(result["scryfall_id"])
    return result


def _validate_recipe(spec):
    _fields(spec, ("schema_version", "pipeline_version", "provider", "operation", "model", "policies", "parameters"))
    _version(spec["schema_version"])
    for value in (spec["pipeline_version"], spec["operation"]):
        _text(value)
    _fields(spec["provider"], ("id", "version"))
    for value in spec["provider"].values():
        _text(value)
    if spec["model"] is not None:
        _fields(spec["model"], ("id", "checkpoint_sha256"))
        _text(spec["model"]["id"])
        _digest(spec["model"]["checkpoint_sha256"])
    _fields(spec["policies"], ("decode", "orientation", "color", "alpha", "mask", "resize", "encoding"))
    for value in spec["policies"].values():
        _text(value)
    if not isinstance(spec["parameters"], dict):
        raise ValueError("Recipe parameters must be a JSON object")


@dataclass(frozen=True)
class ProcessingRecipe:
    canonical_bytes: bytes

    def __post_init__(self):
        if type(self.canonical_bytes) is not bytes:
            raise TypeError("Recipe storage must be immutable canonical JSON bytes")
        spec = _load_json(self.canonical_bytes)
        _validate_recipe(spec)
        if _canonical_json(spec) != self.canonical_bytes:
            raise ValueError("Recipe bytes must be canonical")

    @classmethod
    def from_spec(cls, spec) -> "ProcessingRecipe":
        return cls(_canonical_json(spec))

    def to_spec(self) -> dict:
        return _load_json(self.canonical_bytes)


def _validate_descriptor(spec):
    _fields(spec, ("schema_version", "source", "inspection", "geometry", "processing"))
    _version(spec["schema_version"])
    source = spec["source"]
    _fields(source, ("image_key", "sha256", "native_dimensions", "format"))
    _image_key(source["image_key"])
    _digest(source["sha256"])
    _dimensions(source["native_dimensions"])
    _text(source["format"])
    _fields(spec["inspection"], ("decode_policy", "qt_decoder_version"))
    for value in spec["inspection"].values():
        _text(value)
    geometry = spec["geometry"]
    _fields(geometry, ("canonical_dimensions", "target_ppi", "target_dimensions"))
    canonical = _dimensions(geometry["canonical_dimensions"])
    target = _dimensions(geometry["target_dimensions"])
    ppi = geometry["target_ppi"]
    if type(ppi) is not int or ppi not in SUPPORTED_TARGET_PPI:
        raise ValueError("Target PPI must be the integer 600, 900 or 1200")
    base = RESOLUTION.to("dots/inch").magnitude
    if target != tuple(round(axis*ppi/base) for axis in canonical):
        raise ValueError("Target dimensions disagree with physical geometry")
    _validate_recipe(spec["processing"])


@dataclass(frozen=True)
class DerivativeRequest:
    """Only the small descriptor is retained; key and targets are derived from it."""

    canonical_bytes: bytes

    def __post_init__(self):
        if type(self.canonical_bytes) is not bytes:
            raise TypeError("Request storage must be immutable canonical JSON bytes")
        spec = _load_json(self.canonical_bytes)
        _validate_descriptor(spec)
        if _canonical_json(spec) != self.canonical_bytes:
            raise ValueError("Request bytes must be canonical")

    @property
    def key(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @property
    def target_dimensions(self) -> tuple[int, int]:
        return tuple(self.to_spec()["geometry"]["target_dimensions"])

    def to_spec(self) -> dict:
        return _load_json(self.canonical_bytes)


def validate_source_plan(snapshot: SourceSnapshot, plan: ResolutionPlan) -> None:
    if not isinstance(snapshot, SourceSnapshot) or not isinstance(plan, ResolutionPlan):
        raise TypeError("Expected an M24 source snapshot and resolution plan")
    if type(snapshot.encoded_bytes) is not bytes:
        raise TypeError("Source payload must be immutable bytes")
    _digest(snapshot.sha256)
    if hashlib.sha256(snapshot.encoded_bytes).hexdigest() != snapshot.sha256:
        raise ValueError("Source digest does not match its encoded bytes")
    _key_spec(snapshot.image_key)
    _dimensions(snapshot.native_dimensions)
    _dimensions(snapshot.canonical_dimensions)
    for value in (snapshot.image_format, snapshot.decode_policy, snapshot.qt_decoder_version):
        _text(value)
    if not isinstance(plan.source_identity, SourceIdentity):
        raise ValueError("Resolution plan requires a SourceIdentity")
    _key_spec(plan.source_identity.image_key)
    _digest(plan.source_identity.sha256)
    for dimensions in (plan.canonical_dimensions, plan.source_dimensions, plan.target_dimensions):
        _dimensions(dimensions)
    if type(plan.sufficient) is not bool or not isinstance(plan.effective_source_ppi, (tuple, list)) or len(plan.effective_source_ppi) != 2:
        raise ValueError("Invalid resolution-plan derived fields")
    if any(type(value) not in (int, float) or (type(value) is float and not math.isfinite(value))
           for value in plan.effective_source_ppi):
        raise ValueError("Invalid effective source PPI")
    if plan != plan_target_resolution(snapshot, plan.target_ppi):
        raise ValueError("Resolution plan does not match the source snapshot")


def build_derivative_request(snapshot: SourceSnapshot, plan: ResolutionPlan,
                             recipe: ProcessingRecipe) -> DerivativeRequest:
    validate_source_plan(snapshot, plan)
    if not isinstance(recipe, ProcessingRecipe):
        raise TypeError("Expected a frozen ProcessingRecipe")
    key = _key_spec(snapshot.image_key)
    spec = {
        "schema_version": 1,
        "source": {"image_key": key, "sha256": snapshot.sha256,
                   "native_dimensions": list(snapshot.native_dimensions), "format": snapshot.image_format},
        "inspection": {"decode_policy": snapshot.decode_policy, "qt_decoder_version": snapshot.qt_decoder_version},
        "geometry": {"canonical_dimensions": list(snapshot.canonical_dimensions), "target_ppi": plan.target_ppi,
                     "target_dimensions": list(plan.target_dimensions)},
        "processing": recipe.to_spec(),
    }
    return DerivativeRequest(_canonical_json(spec))

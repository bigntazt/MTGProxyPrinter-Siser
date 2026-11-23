"""Synthetic identity contracts; no provider, source reopening or full decoding."""
from dataclasses import fields, replace
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mtg_proxy_printer.image_derivative import ProcessingRecipe, DerivativeRequest, build_derivative_request
from mtg_proxy_printer.image_source import DECODE_POLICY, SourceSnapshot, plan_target_resolution
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from mtg_proxy_printer.units_and_sizes import UUID


# Fixed, valid 1x1 red GIF, so the serialization contract is independent of encoders.
SOURCE = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\x00\x00\x00\x00\xff"
          b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b")


def make_snapshot(**changes):
    snapshot = SourceSnapshot(ImageKey("12345678-1234-1234-1234-123456789abc", True, ImageQuality.high_resolution),
                              hashlib.sha256(SOURCE).hexdigest(), SOURCE, (1, 1), (745, 1040),
                              "gif", DECODE_POLICY, "6.11.2")
    return replace(snapshot, **changes)


def recipe_spec():
    return {"schema_version": 1, "pipeline_version": "synthetic-v1",
            "provider": {"id": "synthetic", "version": "1"}, "operation": "fixture",
            "model": None,
            "policies": {"decode": "declared-v1", "orientation": "none", "color": "preserve",
                         "alpha": "preserve", "mask": "none", "resize": "synthetic-target", "encoding": "png-v1"},
            "parameters": {"nested": {"options": [1, 2], "enabled": True}}}


def make_request(*, snapshot=None, ppi=600, spec=None):
    snapshot = make_snapshot() if snapshot is None else snapshot
    recipe = ProcessingRecipe.from_spec(recipe_spec() if spec is None else spec)
    return build_derivative_request(snapshot, plan_target_resolution(snapshot, ppi), recipe)


def set_path(spec, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        spec = spec[part]
    spec[parts[-1]] = value


def test_canonical_order_and_frozen_nested_values():
    original = recipe_spec()
    def reversed_objects(value):
        if isinstance(value, dict):
            return {key: reversed_objects(value[key]) for key in reversed(value)}
        if isinstance(value, list):
            return [reversed_objects(child) for child in value]
        return value
    recipe = ProcessingRecipe.from_spec(original)
    request = make_request(spec=original)
    assert recipe == ProcessingRecipe.from_spec(reversed_objects(original))
    assert request == make_request(spec=reversed_objects(original))
    assert request.key == make_request(spec=reversed_objects(original)).key
    original["parameters"]["nested"]["options"].append(99)
    assert recipe.to_spec()["parameters"]["nested"]["options"] == [1, 2]
    inspected = request.to_spec()
    inspected["processing"]["parameters"]["nested"]["options"].append(77)
    recipe.to_spec()["provider"]["version"] = "mutated"
    assert request == make_request() and recipe == ProcessingRecipe.from_spec(recipe_spec())
    assert [field.name for field in fields(request)] == ["canonical_bytes"]
    assert type(request.canonical_bytes) is bytes and len(request.canonical_bytes) < 4096
    assert SOURCE not in request.canonical_bytes
    with pytest.raises(TypeError):
        replace(request, key="0"*64)
    with pytest.raises(ValueError):
        DerivativeRequest(b"{}")
    with pytest.raises(ValueError):
        ProcessingRecipe(json.dumps(recipe_spec()).encode())  # Noncanonical spacing.


@pytest.mark.parametrize("path,value", [
    ("pipeline_version", "synthetic-v2"), ("provider.id", "other"), ("provider.version", "2"),
    ("operation", "other-operation"), ("model", {"id": "synthetic-model", "checkpoint_sha256": "a"*64}),
    *[("policies." + policy, "changed-v2") for policy in ("decode", "orientation", "color", "alpha", "mask", "resize", "encoding")],
    ("parameters.nested.options", [2, 1]), ("parameters.nested.enabled", False),
    ("parameters.nested.options", [1.0, 2]),
])
def test_each_declared_processing_change_changes_key(path, value):
    spec = recipe_spec()
    set_path(spec, path, value)
    assert make_request(spec=spec).key != make_request().key


def test_model_digest_and_source_provenance_changes_affect_identity():
    spec = recipe_spec()
    spec["model"] = {"id": "synthetic-model", "checkpoint_sha256": "a"*64}
    first = make_request(spec=spec)
    spec["model"]["checkpoint_sha256"] = "b"*64
    assert make_request(spec=spec).key != first.key
    source = make_snapshot()
    changes = [
        {"encoded_bytes": SOURCE+b"\x00", "sha256": hashlib.sha256(SOURCE+b"\x00").hexdigest()},
        {"image_key": replace(source.image_key, is_front=False)},
        {"image_key": replace(source.image_key, image_quality=ImageQuality.low_resolution)},
        {"image_key": replace(source.image_key, scryfall_id="abcdefab-1234-1234-1234-123456789abc")},
        {"canonical_dimensions": (1040, 1490)}, {"native_dimensions": (2, 1)},
        {"image_format": "png"}, {"decode_policy": "other-inspection-policy"}, {"qt_decoder_version": "other-qt"},
    ]
    baseline = make_request(snapshot=source).key
    assert all(make_request(snapshot=replace(source, **change)).key != baseline for change in changes)
    assert make_request(ppi=1200).key != baseline


def test_existing_validated_uuid_type_and_plain_string_have_same_identity():
    source = make_snapshot()
    typed_key = replace(source.image_key, scryfall_id=UUID(source.image_key.scryfall_id))
    assert make_request(snapshot=replace(source, image_key=typed_key)) == make_request(snapshot=source)


def test_no_path_identity_no_source_reopening_or_decoding(tmp_path):
    from mtg_proxy_printer.image_source import SourceRequest, load_source_snapshot
    source = make_snapshot()
    paths = [tmp_path / "one.gif", tmp_path / "two.gif"]
    snapshots = []
    for path in paths:
        path.write_bytes(SOURCE)
        snapshots.append(load_source_snapshot(SourceRequest(source.image_key, source.canonical_dimensions, original_path=path)))
    assert make_request(snapshot=snapshots[0]) == make_request(snapshot=snapshots[1])
    sha256 = hashlib.sha256
    with patch.object(Path, "read_bytes", side_effect=AssertionError("No reopening")), \
            patch("PySide6.QtGui.QImageReader", side_effect=AssertionError("No redecoding")), \
            patch("mtg_proxy_printer.image_derivative.hashlib.sha256", wraps=sha256) as digest:
        request = build_derivative_request(source, plan_target_resolution(source, 600), ProcessingRecipe.from_spec(recipe_spec()))
        digest.assert_called_once_with(SOURCE)
    assert request == make_request()


@pytest.mark.parametrize("path,value", [
    ("schema_version", True), ("schema_version", 2), ("schema_version", 1.0),
    ("pipeline_version", ""), ("provider", {"id": "synthetic", "version": "1", "extra": 1}),
    ("provider.version", 1), ("operation", None),
    ("model", {}), ("model", {"id": "x", "checkpoint_sha256": "A"*64}),
    ("model", {"id": "x", "checkpoint_sha256": "b"*63}),
    ("model", {"id": "x", "checkpoint_sha256": "b"*64, "path": "forbidden"}),
    ("policies.decode", ""), ("policies", {}), ("parameters", []),
    ("parameters", {1: "non-string"}), ("parameters", {"bad": float("nan")}),
    ("parameters", {"bad": float("inf")}), ("parameters", {"bad": -float("inf")}),
    ("parameters", {"bad": {1, 2}}), ("parameters", {"bad": (1, 2)}),
    ("parameters", {"bad": b"bytes"}),
])
def test_invalid_recipes_are_rejected(path, value):
    spec = recipe_spec()
    set_path(spec, path, value)
    with pytest.raises((ValueError, TypeError)):
        ProcessingRecipe.from_spec(spec)


def test_recipe_rejects_unknown_missing_fields_and_cycles_and_escapes_unicode():
    envelope = recipe_spec()
    envelope["unknown"] = 1
    with pytest.raises(ValueError):
        ProcessingRecipe.from_spec(envelope)
    spec = recipe_spec()
    del spec["model"]
    with pytest.raises(ValueError):
        ProcessingRecipe.from_spec(spec)
    spec = recipe_spec()
    spec["parameters"]["cycle"] = spec
    with pytest.raises(ValueError, match="Cyclic"):
        ProcessingRecipe.from_spec(spec)
    spec = recipe_spec()
    spec["parameters"] = {"label": "café", "array": [None, 1.0, False]}
    recipe = ProcessingRecipe.from_spec(spec)
    assert b"caf\\u00e9" in recipe.canonical_bytes and b"1.0" in recipe.canonical_bytes
    assert recipe.to_spec() == spec


@pytest.mark.parametrize("field,value", [
    ("encoded_bytes", bytearray(SOURCE)), ("sha256", "0"*64), ("sha256", "A"*64),
    ("native_dimensions", (True, 1)), ("native_dimensions", (1.0, 1)),
    ("canonical_dimensions", (0, 1040)), ("canonical_dimensions", (745,)),
    ("image_format", ""), ("decode_policy", None), ("qt_decoder_version", ""),
    ("image_key", ImageKey("../bad", True, ImageQuality.high_resolution)),
    ("image_key", ImageKey("12345678-1234-1234-1234-123456789abc", 1, ImageQuality.high_resolution)),
    ("image_key", ImageKey("12345678-1234-1234-1234-123456789abc", True, ImageQuality(3))),
])
def test_inconsistent_public_snapshots_are_rejected(field, value):
    source = make_snapshot()
    plan = plan_target_resolution(source, 600)
    with pytest.raises((TypeError, ValueError)):
        build_derivative_request(replace(source, **{field: value}), plan, ProcessingRecipe.from_spec(recipe_spec()))


@pytest.mark.parametrize("field,value", [
    ("target_ppi", True), ("target_ppi", 600.0), ("target_ppi", 1200),
    ("canonical_dimensions", (1040, 1490)), ("source_dimensions", (2, 1)),
    ("target_dimensions", (1490.0, 2080)), ("target_dimensions", (1490, 2079)),
    ("sufficient", True), ("sufficient", 0), ("effective_source_ppi", (float("nan"), 1)),
    ("effective_source_ppi", (1, 1)),
    ("source_identity", None),
])
def test_inconsistent_public_plans_are_rejected(field, value):
    source = make_snapshot()
    plan = plan_target_resolution(source, 600)
    with pytest.raises((TypeError, ValueError)):
        build_derivative_request(source, replace(plan, **{field: value}), ProcessingRecipe.from_spec(recipe_spec()))
    changed = replace(plan, source_identity=replace(source.identity, sha256="a"*64))
    with pytest.raises(ValueError):
        build_derivative_request(source, changed, ProcessingRecipe.from_spec(recipe_spec()))


def test_serialization_contract():
    request = make_request()
    # The complete golden descriptor and digest are checked independently below.
    expected = {
        "schema_version": 1,
        "source": {"image_key": {"scryfall_id": "12345678-1234-1234-1234-123456789abc", "is_front": True, "image_quality": 2},
                   "sha256": hashlib.sha256(SOURCE).hexdigest(), "native_dimensions": [1, 1], "format": "gif"},
        "inspection": {"decode_policy": "qt-first-frame-no-auto-transform-v1", "qt_decoder_version": "6.11.2"},
        "geometry": {"canonical_dimensions": [745, 1040], "target_ppi": 600, "target_dimensions": [1490, 2080]},
        "processing": recipe_spec(),
    }
    assert request.canonical_bytes == json.dumps(expected, sort_keys=True, separators=(",", ":"),
                                                ensure_ascii=True, allow_nan=False).encode("utf-8")
    assert request.key == hashlib.sha256(request.canonical_bytes).hexdigest()
    assert len(request.canonical_bytes) == 805
    assert request.key == "e817a0b7d4c3225d533726714604db6a1af679f57301302c64028be48746d089"
    assert "sufficient" not in request.to_spec()["geometry"]
    assert "effective_source_ppi" not in request.to_spec()["geometry"]

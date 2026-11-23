"""Preparation outcomes with real synthetic sources and an isolated derivative cache."""
from dataclasses import fields, replace
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColorSpace, QImage

from mtg_proxy_printer import image_preparation as preparation
from mtg_proxy_printer import image_processing_pillow as processor
from mtg_proxy_printer.image_derivative import build_derivative_request
from mtg_proxy_printer.image_derivative_cache import DerivativeCache, CacheMiss
from mtg_proxy_printer.image_source import plan_target_resolution, SourceSnapshot
from tests.test_image_processing_pillow import make_source, patterned_image, snapshot_from_bytes, oriented_jpeg


def test_original_is_exact_without_pillow_cache_or_decode(tmp_path):
    source = make_source(metadata=True)
    plan = plan_target_resolution(source, 1200)
    cache = DerivativeCache(tmp_path / "not-created")
    with patch.object(processor, "discover_pillow_runtime", side_effect=AssertionError("No optional discovery")), \
            patch.object(DerivativeCache, "lookup", side_effect=AssertionError("No Original cache lookup")), \
            patch.object(DerivativeCache, "store", side_effect=AssertionError("No Original cache store")), \
            patch.object(processor, "_decode_source", side_effect=AssertionError("No Original source decode")):
        result = preparation.prepare_image(source, plan, mode="original", cache=cache)
    assert result.outcome == "original" and result.encoded_bytes is source.encoded_bytes
    assert result.image_format == source.image_format and result.actual_dimensions == source.native_dimensions
    assert result.target_ppi == 1200 and result.target_dimensions == (2980, 4160)
    assert result.derivative_key is None and result.reason is None
    assert "encoded_bytes=" not in repr(result)
    assert not cache.root.exists()
    assert all(not isinstance(getattr(result, field.name), (SourceSnapshot, QImage)) for field in fields(result))
    assert type(result.canonical_dimensions) is tuple and type(result.actual_dimensions) is tuple


def test_preparation_import_and_original_work_with_pillow_blocked_in_fresh_process(tmp_path):
    script = tmp_path / "without_pillow.py"
    script.write_text('''
import importlib.abc
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import platformdirs
root = Path(sys.argv[1]).resolve()
store = Path(__file__).parent / "child-isolation"
class IsolatedDirs(platformdirs.PlatformDirs):
    @property
    def user_data_dir(self): return str(store / "data")
    @property
    def user_config_dir(self): return str(store / "config")
    @property
    def user_cache_dir(self): return str(store / "cache")
    @property
    def user_log_dir(self): return str(store / "logs")
    @property
    def user_state_dir(self): return str(store / "state")
    @property
    def site_data_dir(self): return str(store / "site-data")
    @property
    def site_config_dir(self): return str(store / "site-config")
platformdirs.PlatformDirs = IsolatedDirs
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(root))
os.chdir(root)
protected = []
if "LOCALAPPDATA" in os.environ:
    local = Path(os.environ["LOCALAPPDATA"])
    for name in ("MTGProxyPrinter", "MTGProxyPrinter-Siser"):
        protected.extend((local / name / name,
                          local / "Packages/PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0/LocalCache/Local" / name / name))
protected = [str(path.resolve()).casefold() for path in protected]
def audit(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "sqlite3.connect"):
        raise RuntimeError("Missing-Pillow subprocess forbids network/database operations")
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = str(Path(os.fsdecode(args[0])).resolve()).casefold()
        if any(path == prefix or path.startswith(prefix + os.sep) for prefix in protected):
            raise RuntimeError("Missing-Pillow subprocess blocked protected storage")
sys.addaudithook(audit)
class BlockPillow(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PIL" or fullname.startswith("PIL."):
            raise ModuleNotFoundError("Pillow deliberately unavailable")
sys.meta_path.insert(0, BlockPillow())
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(["M26 missing-Pillow isolation"])
from mtg_proxy_printer.image_preparation import prepare_image
from mtg_proxy_printer.image_derivative_cache import DerivativeCache
from mtg_proxy_printer.image_source import plan_target_resolution
from tests.test_image_derivative import make_snapshot
source = make_snapshot()
plan = plan_target_resolution(source, 600)
cache = DerivativeCache(Path(__file__).parent / "no-cache")
with patch.object(DerivativeCache, "lookup", side_effect=AssertionError("No cache")), patch.object(DerivativeCache, "store", side_effect=AssertionError("No cache")):
    original = prepare_image(source, plan, mode="original", cache=cache)
    assert original.outcome == "original" and original.encoded_bytes == source.encoded_bytes
    fallback = prepare_image(source, plan, mode="lanczos", cache=cache)
    assert fallback.outcome == "fallback_original" and fallback.reason == "provider_unavailable"
    assert fallback.encoded_bytes == source.encoded_bytes and fallback.derivative_key is None
assert not any(name == "PIL" or name.startswith("PIL.") for name in sys.modules)
assert not cache.root.exists()
import mtg_proxy_printer
from mtg_proxy_printer.app_dirs import data_directories
assert Path(mtg_proxy_printer.__file__).resolve().is_relative_to(root)
locations = {name: str(getattr(data_directories, name)) for name in
             ("user_data_path", "user_config_path", "user_cache_path", "user_log_path", "user_state_path")}
assert all(Path(path).is_relative_to(store) for path in locations.values())
(Path(__file__).parent / "without-pillow-proof.json").write_text(json.dumps(
    {"package": mtg_proxy_printer.__file__, "locations": locations, "pillow_imports": [],
     "original": original.outcome, "unavailable": fallback.reason}, indent=2), encoding="utf-8")
print("Original and explicit provider-unavailable fallback passed with Pillow blocked")
''', encoding="utf-8")
    env = os.environ.copy()
    root = Path(preparation.__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-B", str(script), str(root)],
                            capture_output=True, text=True, env=env, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Pillow blocked" in result.stdout


@pytest.mark.parametrize("path", ["original", "unavailable", "available"])
@pytest.mark.parametrize("bad", ["digest", "plan"])
def test_invalid_source_or_plan_is_never_a_fallback(tmp_path, path, bad):
    source = make_source()
    plan = plan_target_resolution(source, 600)
    if bad == "digest": source = replace(source, sha256="0"*64)
    else: plan = replace(plan, target_dimensions=(1490, 2079))
    with patch.object(DerivativeCache, "lookup", side_effect=AssertionError("Reject before cache")):
        if path == "unavailable":
            with patch.object(processor, "discover_pillow_runtime", side_effect=processor.ProcessingFailure("provider_unavailable", "missing")):
                with pytest.raises(ValueError):
                    preparation.prepare_image(source, plan, mode="lanczos", cache=DerivativeCache(tmp_path))
        else:
            with pytest.raises(ValueError):
                preparation.prepare_image(source, plan, mode="original" if path == "original" else "lanczos", cache=DerivativeCache(tmp_path))


def test_invalid_programmer_arguments_raise(tmp_path):
    source = make_source()
    plan = plan_target_resolution(source, 600)
    for mode in (None, "Original", "neural", True):
        with pytest.raises(ValueError):
            preparation.prepare_image(source, plan, mode=mode, cache=DerivativeCache(tmp_path))
    with pytest.raises(TypeError):
        preparation.prepare_image(source, plan, mode="original", cache=None)
    with pytest.raises(TypeError):
        preparation.prepare_image(object(), plan, mode="original", cache=DerivativeCache(tmp_path))


def test_first_generation_then_cache_hit_skips_producer_and_source_decode(tmp_path):
    source = make_source()
    plan = plan_target_resolution(source, 600)
    cache = DerivativeCache(tmp_path)
    first = preparation.prepare_image(source, plan, mode="lanczos", cache=cache)
    assert first.outcome == "generated_cached" and first.actual_dimensions == (1490, 2080)
    assert first.derivative_key and first.actual_dimensions != source.native_dimensions
    with patch.object(processor.PillowRuntime, "produce_png", side_effect=AssertionError("No warm producer")), \
            patch.object(processor, "_decode_source", side_effect=AssertionError("No warm source decode")):
        second = preparation.prepare_image(source, plan, mode="lanczos", cache=DerivativeCache(tmp_path))
    assert second.outcome == "cache_hit" and second.encoded_bytes == first.encoded_bytes
    assert second.derivative_key == first.derivative_key


def test_sufficient_source_still_honors_explicit_lanczos_and_equal_size_encoding(tmp_path):
    source = make_source(image=patterned_image(1490, 2080), metadata=True)
    plan = plan_target_resolution(source, 600)
    assert plan.sufficient
    from PIL import Image
    with patch.object(Image.Image, "resize", side_effect=AssertionError("Equal-size input needs no interpolation")):
        result = preparation.prepare_image(source, plan, mode="lanczos", cache=DerivativeCache(tmp_path))
    assert result.outcome == "generated_cached" and result.derivative_key
    assert result.actual_dimensions == (1490, 2080)
    assert result.encoded_bytes != source.encoded_bytes  # Declared normalization/metadata/encoding still applies.


def test_source_face_target_runtime_and_policy_changes_do_not_reuse_old_key(tmp_path):
    source = make_source()
    runtime = processor.discover_pillow_runtime()
    baseline = build_derivative_request(source, plan_target_resolution(source, 600), runtime.recipe())
    changes = [make_source(image=patterned_image(41, 59)), replace(source, image_key=replace(source.image_key, is_front=False))]
    for changed in changes:
        assert build_derivative_request(changed, plan_target_resolution(changed, 600), runtime.recipe()).key != baseline.key
    assert build_derivative_request(source, plan_target_resolution(source, 1200), runtime.recipe()).key != baseline.key
    changed_runtime = replace(runtime, qt_version=runtime.qt_version + "-synthetic-test")
    assert build_derivative_request(source, plan_target_resolution(source, 600), changed_runtime.recipe()).key != baseline.key
    cache = DerivativeCache(tmp_path)
    first = preparation.prepare_image(source, plan_target_resolution(source, 600), mode="lanczos", cache=cache)
    with patch.object(processor, "discover_pillow_runtime", return_value=changed_runtime):
        changed = preparation.prepare_image(source, plan_target_resolution(source, 600), mode="lanczos", cache=cache)
    assert changed.outcome == "generated_cached" and changed.derivative_key != first.derivative_key
    spec = runtime.recipe().to_spec()
    spec["policies"]["resize"] += "-synthetic-test"
    from mtg_proxy_printer.image_derivative import ProcessingRecipe
    assert build_derivative_request(source, plan_target_resolution(source, 600), ProcessingRecipe.from_spec(spec)).key != baseline.key


@pytest.mark.parametrize("canonical,ppi,target", [
    ((745, 1040), 600, (1490, 2080)), ((745, 1040), 1200, (2980, 4160)),
    ((1040, 1490), 600, (2080, 2980)), ((1040, 1490), 1200, (4160, 5960)),
])
def test_actual_exact_target_generation(tmp_path, canonical, ppi, target):
    source = replace(make_source(), canonical_dimensions=canonical)
    plan = plan_target_resolution(source, ppi)
    result = preparation.prepare_image(source, plan, mode="lanczos", cache=DerivativeCache(tmp_path))
    assert result.outcome == "generated_cached" and result.actual_dimensions == target == result.target_dimensions
    assert QImage.fromData(result.encoded_bytes).size().toTuple() == target


@pytest.mark.parametrize("reason", ["not_found", "invalid_entry", "io_error"])
def test_cache_miss_or_lookup_failure_still_prepares(tmp_path, reason):
    source = make_source()
    with patch.object(DerivativeCache, "lookup", return_value=CacheMiss(reason, "synthetic lookup outcome")):
        result = preparation.prepare_image(source, plan_target_resolution(source, 600), mode="lanczos", cache=DerivativeCache(tmp_path))
    assert result.outcome == "generated_cached"


def test_write_failure_keeps_valid_generated_bytes_and_repeat_generation_is_deterministic(tmp_path):
    from mtg_proxy_printer import image_derivative_cache
    source = make_source()
    plan = plan_target_resolution(source, 600)
    cache = DerivativeCache(tmp_path)
    with patch.object(image_derivative_cache, "_write_atomic", side_effect=PermissionError("Synthetic persistence denial")):
        first = preparation.prepare_image(source, plan, mode="lanczos", cache=cache)
        second = preparation.prepare_image(source, plan, mode="lanczos", cache=cache)
    assert first.outcome == second.outcome == "generated_uncached"
    assert first.reason == "cache_write_failed" and first.derivative_key
    assert first.encoded_bytes == second.encoded_bytes
    assert first.actual_dimensions == QImage.fromData(first.encoded_bytes).size().toTuple() == (1490, 2080)
    assert cache.lookup(build_derivative_request(source, plan, processor.discover_pillow_runtime().recipe())).reason == "not_found"


@pytest.mark.parametrize("bad_output", [b"not a PNG", b""])
def test_invalid_producer_bytes_are_never_returned(tmp_path, bad_output):
    source = make_source()
    with patch.object(processor.PillowRuntime, "produce_png", return_value=bad_output):
        result = preparation.prepare_image(source, plan_target_resolution(source, 600), mode="lanczos", cache=DerivativeCache(tmp_path))
    assert result.outcome == "fallback_original" and result.reason == "invalid_generated_png"
    assert result.encoded_bytes == source.encoded_bytes and result.actual_dimensions == source.native_dimensions
    assert result.derivative_key is None


@pytest.mark.parametrize("kind", ["high-depth", "gray-profile", "decode-disagreement", "processing-failure"])
def test_expected_failures_return_exact_original_with_native_dimensions(tmp_path, kind):
    image = patterned_image().convertToFormat(QImage.Format.Format_RGBA64) if kind == "high-depth" else patterned_image()
    if kind == "gray-profile":
        image = image.convertToFormat(QImage.Format.Format_Grayscale8)
        image.setColorSpace(QColorSpace(QPointF(0.3127, 0.3290), QColorSpace.TransferFunction.Gamma, 2.2))
    source = make_source(image=image)
    if kind == "decode-disagreement": source = replace(source, native_dimensions=(99, 101))
    plan = plan_target_resolution(source, 1200)
    if kind == "processing-failure":
        with patch.object(processor.PillowRuntime, "produce_png", side_effect=processor.ProcessingFailure("processing_failed", "synthetic failure")):
            result = preparation.prepare_image(source, plan, mode="lanczos", cache=DerivativeCache(tmp_path))
    else:
        result = preparation.prepare_image(source, plan, mode="lanczos", cache=DerivativeCache(tmp_path))
    expected = {"high-depth": "unsupported_source", "gray-profile": "unsupported_source",
                "decode-disagreement": "source_decode_failed", "processing-failure": "processing_failed"}
    assert result.outcome == "fallback_original" and result.reason == expected[kind]
    assert result.encoded_bytes is source.encoded_bytes and result.actual_dimensions == source.native_dimensions
    assert result.derivative_key is None and result.target_dimensions == (2980, 4160)


def test_worker_uses_only_captured_bytes_and_no_qpixmap(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    source = snapshot_from_bytes(oriented_jpeg())
    plan = plan_target_resolution(source, 600)
    # PNG codec discovery was resolved before the filesystem-read spy.
    runtime = processor.discover_pillow_runtime()
    with patch.object(processor, "discover_pillow_runtime", return_value=runtime), \
            patch("PySide6.QtGui.QPixmap", side_effect=AssertionError("No worker QPixmap")), \
            patch.object(Path, "read_bytes", side_effect=FileNotFoundError("No source paths may be reopened")), \
            ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(preparation.prepare_image, source, plan, mode="lanczos", cache=DerivativeCache(tmp_path)).result(timeout=15)
    assert result.outcome == "generated_cached"
    assert result.actual_dimensions == (1490, 2080)

"""Standalone unittest checks; deliberately outside application conftest."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("enhancement_benchmark", ROOT / "scripts" / "enhancement_benchmark.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_exact_targets(self):
        expected = {("regular", 600): (1490, 2080), ("regular", 1200): (2980, 4160),
                    ("oversized", 600): (2080, 2980), ("oversized", 1200): (4160, 5960)}
        for (kind, ppi), size in expected.items():
            with self.subTest(kind=kind, ppi=ppi):
                self.assertEqual(bench.target_size(kind, ppi), size)

    def test_rejects_other_targets(self):
        for ppi in (300, 900, 2400):
            with self.assertRaises(ValueError):
                bench.target_size("regular", ppi)

    def test_sufficient_requires_both_dimensions(self):
        self.assertTrue(bench.sufficient((1490, 2080), (1490, 2080)))
        self.assertFalse(bench.sufficient((3000, 2079), (1490, 2080)))
        self.assertFalse(bench.sufficient((1489, 9000), (1490, 2080)))

    def test_skip_is_per_selected_target(self):
        self.assertEqual(bench.ai_decision((1490, 2080), (1490, 2080), False)[0], "skipped")
        self.assertIsNone(bench.ai_decision((1490, 2080), (2980, 4160), False))

    def test_explicit_alpha_rejection(self):
        self.assertEqual(bench.ai_decision((372, 520), (1490, 2080), True)[0], "unsupported")

    def test_native_scale_label(self):
        self.assertEqual(bench.neural_label(2), "2x neural upscale followed by conventional resizing")
        self.assertNotIn("4x", bench.neural_label(2))

    def test_explicit_neural_alpha_path(self):
        source = Image.new("RGBA", (10, 15), (20, 40, 80, 128))
        native = bench.restore_alpha(source, Image.new("RGB", (20, 30), "red"))
        self.assertEqual(native.mode, "RGBA")
        self.assertEqual(native.getchannel("A").getextrema(), (128, 128))
        self.assertEqual(native.getpixel((0, 0))[:3], (255, 0, 0))
        self.assertIsNone(bench.ai_decision((10, 15), (20, 30), True, allow_alpha=True))

    def test_srgb_profile_metadata_is_deterministic_and_loadable(self):
        self.assertEqual(bench.srgb_profile(), bench.srgb_profile())
        self.assertEqual(bench.SRGB[24:36], bytes.fromhex("07d000010001000000000000"))
        source = self.root / "profile.png"
        bench.encode_png(Image.new("RGB", (10, 15), (20, 40, 80)), source)
        image, info = bench.load_image(source)
        self.assertEqual(image.getpixel((0, 0)), (20, 40, 80))
        self.assertIn("default perceptual", info["color_conversion"])

    def test_exact_size_copy_is_explicit(self):
        recipe = {"target_dimensions": [20, 30]}
        record, _ = bench.final_result(self.root, recipe, Image.new("RGB", (20, 30)), {}, "lanczos", None)
        self.assertIn("identity copy", record["final_operation"])

    def test_recipe_material_changes_invalidate(self):
        base = {"source_sha256": "a", "checkpoint_sha256": "b", "target_ppi": 600,
                "precision": "FP32", "color_policy": "sRGB", "harness": "code1", "runtime": "torch1"}
        for key in base:
            changed = {**base, key: "changed"}
            self.assertNotEqual(bench.identity(base), bench.identity(changed), key)

    def test_identity_is_order_independent(self):
        self.assertEqual(bench.identity({"a": 1, "b": 2}), bench.identity({"b": 2, "a": 1}))

    def test_source_hash_tracks_content(self):
        source = self.root / "source"
        source.write_bytes(b"one")
        initial = bench.sha256(source)
        source.write_bytes(b"two")
        self.assertNotEqual(initial, bench.sha256(source))

    def successful_record(self):
        recipe = {"source": "hash", "target_dimensions": [20, 30]}
        image = Image.new("RGB", (20, 30), "red")
        output = self.root / "output.png"
        metadata = bench.encode_png(image, output)
        record = {"recipe": recipe, "recipe_id": bench.identity(recipe), "status": "success", **metadata}
        path = self.root / "record.json"
        bench.write_json(path, record)
        return recipe, path, output

    def test_valid_result_reuse(self):
        recipe, path, _ = self.successful_record()
        self.assertEqual(bench.reusable(path, recipe)["status"], "success")

    def test_corrupt_output_not_reused(self):
        recipe, path, output = self.successful_record()
        output.write_bytes(b"not an image")
        self.assertIsNone(bench.reusable(path, recipe))

    def test_wrong_recipe_not_reused(self):
        recipe, path, _ = self.successful_record()
        self.assertIsNone(bench.reusable(path, {**recipe, "source": "changed"}))

    def test_failed_record_not_reused(self):
        recipe, path, _ = self.successful_record()
        record = bench.read_json(path)
        record["status"] = "failure"
        bench.write_json(path, record)
        self.assertIsNone(bench.reusable(path, recipe))

    def test_wrong_dimensions_not_accepted(self):
        _, _, output = self.successful_record()
        self.assertFalse(bench.validate_output(output, (21, 30)))

    def test_alpha_preserved_by_control(self):
        source = self.root / "alpha.png"
        image = Image.new("RGBA", (10, 15), (20, 40, 80, 128))
        image.save(source)
        loaded, info = bench.load_image(source)
        self.assertTrue(info["nonopaque_alpha"])
        recipe = {"target_dimensions": [20, 30]}
        record, reused = bench.final_result(self.root, recipe, loaded, {}, "lanczos", None)
        self.assertFalse(reused)
        self.assertEqual(record["status"], "success")
        with Image.open(record["output"]) as output:
            self.assertEqual(output.mode, "RGBA")
            self.assertEqual(output.getchannel("A").getextrema(), (128, 128))

    def test_original_not_mislabeled_as_target_raster(self):
        image = Image.new("RGB", (10, 15))
        recipe = {"target_dimensions": [20, 30]}
        record, _ = bench.final_result(self.root, recipe, image, {}, "original", None)
        self.assertEqual(record["final_dimensions"], [10, 15])
        self.assertIn("target not applied", record["final_operation"])

    def test_final_dimensions_separate_from_native(self):
        native = Image.new("RGB", (40, 60))
        recipe = {"target_dimensions": [80, 120]}
        common = {"native_scale": 2, "native_output_dimensions": [40, 60],
                  "recipe_label": bench.neural_label(2)}
        record, _ = bench.final_result(self.root, recipe, native, common, "candidate", None)
        self.assertEqual(record["native_output_dimensions"], [40, 60])
        self.assertEqual(record["final_dimensions"], [80, 120])
        self.assertEqual(record["native_scale"], 2)

    def test_encoding_failure_not_success_and_no_partial(self):
        image = Image.new("RGB", (10, 15))
        recipe = {"target_dimensions": [20, 30]}
        with patch.object(bench, "validate_output", return_value=False):
            record, _ = bench.final_result(self.root, recipe, image, {}, "lanczos", None)
        self.assertEqual(record["status"], "failure")
        self.assertNotIn("output", record)
        self.assertEqual(list(self.root.rglob("*.part")), [])
        record_path = self.root / "records" / (bench.identity(recipe) + ".json")
        self.assertIsNone(bench.reusable(record_path, recipe))



class ReuseRegressionTests(unittest.TestCase):
    """End-to-end run() fixtures: real cache files, no weights/inference/GPU."""

    def setUp(self):
        from argparse import Namespace
        import io
        from contextlib import redirect_stdout
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.models = self.root / "models"
        self.models.mkdir()
        self.output = self.root / "results"
        self.source = self.root / "source.png"
        self.truth = self.root / "truth.png"
        Image.new("RGB", (10, 15), (50, 50, 50)).save(self.source)
        Image.new("RGB", (20, 30), (50, 50, 50)).save(self.truth)
        self.checkpoint = self.models / "fixture.pth"
        self.checkpoint.write_bytes(b"fixture checkpoint A; never loaded")
        self.pin = bench.sha256(self.checkpoint)
        self.sample = {"id": "fixture", "file": "source.png", "sha256": bench.sha256(self.source),
                       "face": "synthetic-front", "card_type": "regular",
                       "truth_file": "truth.png", "truth_sha256": bench.sha256(self.truth)}
        self.model = {"id": "fixture_2x", "filename": "fixture.pth", "sha256": self.pin, "scale": 2}
        self.corpus = self.root / "corpus.json"
        self.manifest = self.root / "models.json"
        self.save_manifests()
        self.args = Namespace(model_manifest=str(self.manifest), models_dir=str(self.models),
                              corpus=str(self.corpus), output=str(self.output), models="lanczos",
                              samples=None, targets=[600], repeats=2, alpha_policy="resize")
        self.env = {"harness_sha256": bench.sha256(bench.__file__), "packages": {"fixture": "CPU-only"}}
        self.enterContext(patch.object(bench, "environment", return_value=self.env))
        # Exercise run() on tiny files; exact physical target sizes have separate tests.
        self.enterContext(patch.object(bench, "target_size", return_value=(20, 30)))
        self.no_gpu = self.enterContext(patch.object(
            bench, "configure_torch", side_effect=AssertionError("Inference/GPU must not be reached")))
        self.enterContext(redirect_stdout(io.StringIO()))

    def save_manifests(self):
        bench.write_json(self.corpus, {"samples": [self.sample]})
        bench.write_json(self.manifest, {"models": [self.model]})

    def run_fixture(self, model="lanczos"):
        self.args.models = model
        bench.run(self.args)
        index = bench.read_json(self.output / "run-index.json")
        self.assertEqual(len(index["records"]), 1)
        path = Path(index["records"][0]["record"])
        return bench.read_json(path), path, index["records"][0]["reused"]

    def seed_neural_cache(self, native=False):
        # Derive common recipe settings from a genuine CPU control run, then
        # construct a pinned fixture success. Cache validation itself is not mocked.
        control, _, _ = self.run_fixture()
        recipe = {**control["recipe"], "model": "fixture_2x", "checkpoint_sha256": self.pin,
                  "device": "cuda:0", "precision": "FP32", "control": None}
        if native:
            for field in ("card_type", "target_ppi", "target_dimensions", "mapping", "control", "truth_sha256"):
                recipe.pop(field, None)
        image = Image.new("RGB", (20, 30), (50, 50, 50))
        directory = "native-records" if native else "records"
        path = self.output / directory / (bench.identity(recipe) + ".json")
        output = self.output / ("native-images" if native else "images") / (bench.identity(recipe) + ".png")
        record = {"method": "fixture_2x", "status": "success", "recipe": recipe,
                  "recipe_id": bench.identity(recipe), **bench.encode_png(image, output),
                  "native_scale": 2, "native_output_dimensions": [20, 30],
                  "load_seconds": 0, "capabilities": {"architecture": "CPU fixture"},
                  "first_call_for_shape_seconds": 0, "first_call_is_first_after_model_load": True,
                  "warm_inference_seconds": [0, 0], "warm_median_seconds": 0,
                  "peak_allocated_bytes": 0, "peak_reserved_bytes": 0,
                  "transfer_quantize_seconds": 0, "native_encoding_validation_seconds": 0}
        bench.write_json(path, record)
        self.assertIsNotNone(bench.reusable(path, recipe))
        return path, output

    def test_checkpoint_mismatch_rejected_before_cached_success(self):
        path, output = self.seed_neural_cache()
        before = path.read_bytes(), output.read_bytes()
        self.model["sha256"] = "0" * 64
        self.save_manifests()
        with self.assertRaisesRegex(ValueError, "Checkpoint hash mismatch.*expected.*got"):
            self.run_fixture("fixture_2x")
        self.assertEqual((path.read_bytes(), output.read_bytes()), before)
        self.no_gpu.assert_not_called()

    def test_valid_checkpoint_pin_resumes_cached_success(self):
        path, output = self.seed_neural_cache()
        before = path.read_bytes(), output.read_bytes()
        record, resumed_path, reused = self.run_fixture("fixture_2x")
        self.assertTrue(reused)
        self.assertEqual(resumed_path, path)
        self.assertEqual(record["status"], "success")
        self.assertEqual((path.read_bytes(), output.read_bytes()), before)
        self.no_gpu.assert_not_called()

    def test_sufficient_source_still_skips_with_mismatched_checkpoint(self):
        Image.new("RGB", (20, 30), (50, 50, 50)).save(self.source)
        self.sample["sha256"] = bench.sha256(self.source)
        self.model["sha256"] = "0" * 64
        self.save_manifests()
        record, _, reused = self.run_fixture("fixture_2x")
        self.assertEqual(record["status"], "skipped")
        self.assertNotIn("output", record)
        self.assertFalse(reused)
        self.no_gpu.assert_not_called()

    def test_truth_changed_without_manifest_digest_is_rejected(self):
        _, path, _ = self.run_fixture()
        before = path.read_bytes()
        Image.new("RGB", (20, 30), (80, 80, 80)).save(self.truth)
        with self.assertRaisesRegex(ValueError, "Truth hash mismatch"):
            self.run_fixture()
        self.assertEqual(path.read_bytes(), before)

    def test_updated_truth_digest_gets_new_evaluation_without_stale_metrics(self):
        first, old_path, _ = self.run_fixture()
        before = old_path.read_bytes()
        self.assertEqual(first["quality_against_synthetic_truth"]["rgb_mae"], 0)
        Image.new("RGB", (20, 30), (80, 80, 80)).save(self.truth)
        self.sample["truth_sha256"] = bench.sha256(self.truth)
        self.save_manifests()
        second, new_path, reused = self.run_fixture()
        self.assertFalse(reused)
        self.assertNotEqual(new_path, old_path)
        self.assertEqual(second["recipe"]["truth_sha256"], self.sample["truth_sha256"])
        self.assertEqual(second["quality_against_synthetic_truth"]["rgb_mae"], 30)
        self.assertEqual(old_path.read_bytes(), before)

    def test_unchanged_truth_source_and_recipe_resume(self):
        _, path, _ = self.run_fixture()
        before = path.read_bytes()
        record, resumed_path, reused = self.run_fixture()
        self.assertTrue(reused)
        self.assertEqual(resumed_path, path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(record["recipe"]["truth_sha256"], self.sample["truth_sha256"])
        self.assertEqual(record["recipe"]["runtime"]["harness_sha256"], bench.sha256(bench.__file__))

    def test_declared_truth_must_exist_decode_and_have_digest(self):
        for case, message in (("missing", "Missing declared truth image"),
                              ("corrupt", "Invalid declared truth image"),
                              ("no_digest", "Missing or invalid declared truth SHA-256")):
            with self.subTest(case=case):
                Image.new("RGB", (20, 30), (50, 50, 50)).save(self.truth)
                self.sample["truth_sha256"] = bench.sha256(self.truth)
                if case == "missing":
                    self.truth.unlink()
                elif case == "corrupt":
                    self.truth.write_bytes(b"corrupt bytes, matching declared digest")
                    self.sample["truth_sha256"] = bench.sha256(self.truth)
                else:
                    self.sample.pop("truth_sha256")
                self.save_manifests()
                with self.assertRaisesRegex(ValueError, message):
                    self.run_fixture()

    def test_truth_update_reuses_native_output_but_recomputes_evaluation(self):
        path, output = self.seed_neural_cache(native=True)
        native_before = path.read_bytes(), output.read_bytes()
        native_recipe = bench.read_json(path)["recipe"]
        self.assertNotIn("truth_sha256", native_recipe)
        first, first_path, _ = self.run_fixture("fixture_2x")
        first_before = first_path.read_bytes()
        self.assertEqual(first["quality_against_synthetic_truth"]["rgb_mae"], 0)
        Image.new("RGB", (20, 30), (80, 80, 80)).save(self.truth)
        self.sample["truth_sha256"] = bench.sha256(self.truth)
        self.save_manifests()
        second, second_path, reused = self.run_fixture("fixture_2x")
        self.assertFalse(reused)
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(second["quality_against_synthetic_truth"]["rgb_mae"], 30)
        self.assertEqual(first["native_record_id"], second["native_record_id"])
        self.assertEqual((path.read_bytes(), output.read_bytes()), native_before)
        self.assertEqual(first_path.read_bytes(), first_before)
        self.assertEqual(len(list((self.output / "native-records").glob("*.json"))), 1)
        self.no_gpu.assert_not_called()

    def test_replacement_between_hash_and_decode_cannot_poison_resume(self):
        original_source = self.source.read_bytes()
        original_truth = self.truth.read_bytes()
        decode = bench.load_image
        changed = set()

        def replace_before_decode(value):
            path = self.source if not changed else self.truth
            if path not in changed:
                changed.add(path)
                Image.new("RGB", (10, 15), "red" if path == self.source else "blue").save(path)
            return decode(value)

        with patch.object(bench, "load_image", side_effect=replace_before_decode):
            first, _, _ = self.run_fixture()
        self.assertEqual(changed, {self.source, self.truth})
        self.source.write_bytes(original_source)
        self.truth.write_bytes(original_truth)
        resumed, _, reused = self.run_fixture()
        self.assertTrue(reused)
        self.assertEqual(first, resumed)
        with Image.open(resumed["output"]) as image:
            self.assertEqual(image.getpixel((0, 0)), (50, 50, 50))
        self.assertEqual(resumed["quality_against_synthetic_truth"]["rgb_mae"], 0)

    def test_shared_content_keeps_both_report_labels(self):
        import csv
        import sys
        from argparse import Namespace
        spec = importlib.util.spec_from_file_location("review", ROOT / "scripts" / "enhancement_review.py")
        review = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, enhancement_benchmark=bench):
            spec.loader.exec_module(review)
        back = dict(self.sample, id="second", face="back")
        bench.write_json(self.corpus, {"samples": [self.sample, back]})
        bench.run(self.args)
        path = self.output / "run-index.json"
        entries = bench.read_json(path)["records"]
        self.assertEqual([(e["sample_id"], e["face"]) for e in entries],
                         [("fixture", "synthetic-front"), ("second", "back")])
        self.assertEqual(entries[0]["record"], entries[1]["record"])
        self.assertTrue(entries[1]["reused"])
        report = self.root / "report"
        review.report(Namespace(results=self.output, output=report, corpus=self.corpus,
                                index=path, samples="fixture,second"))
        with (report / "results.csv").open(newline="") as stream:
            self.assertEqual([(r["sample_id"], r["face"]) for r in csv.DictReader(stream)],
                             [("fixture", "synthetic-front"), ("second", "back")])
        html = (report / "index.html").read_text(encoding="utf-8")
        sheets = []
        for sample in (self.sample, back):
            self.assertIn(f"{sample['id']} / {sample['face']}", html)
            sheets.append((report / (sample["id"] + "-600-comparison.png")).read_bytes())
        self.assertNotEqual(*sheets)


if __name__ == "__main__":
    unittest.main(verbosity=2)

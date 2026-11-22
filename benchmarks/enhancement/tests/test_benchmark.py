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


if __name__ == "__main__":
    unittest.main(verbosity=2)

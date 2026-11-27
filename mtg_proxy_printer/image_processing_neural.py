"""Optional host pixel policy for native neural RGB, with explicit retained alpha."""
from contextlib import ExitStack
from dataclasses import dataclass
from io import BytesIO
import json

from mtg_proxy_printer import image_processing_pillow as pillow
from mtg_proxy_printer.image_derivative import ProcessingRecipe
from mtg_proxy_printer.neural_worker_client import NeuralWorkerReady


@dataclass(frozen=True)
class NeuralRuntime:
    host: pillow.PillowRuntime

    def recipe(self, ready: NeuralWorkerReady) -> ProcessingRecipe:
        if not isinstance(ready, NeuralWorkerReady):
            raise TypeError("Expected current verified NeuralWorkerReady")
        provenance = json.loads(ready.provenance_json)
        # A new independent spec; the existing Lanczos identity remains untouched.
        spec = self.host.recipe().to_spec()
        spec.update(pipeline_version="m35-neural-preparation-v1",
                    provider={"id": "spandrel-neural-rgb8", "version": "1"},
                    operation="one-native-neural-pass-then-exact-target-v1",
                    model={"id": provenance["model"]["id"], "checkpoint_sha256": provenance["model"]["sha256"]})
        spec["policies"].update(alpha="straight-rgb-independent-alpha-lanczos-then-rgba-v1",
                                resize="native-2x-then-pillow-full-image-lanczos-v1")
        parameters = spec["parameters"]
        parameters["runtime"]["pillow"] = self.host.pillow_version
        parameters["worker"] = {"fingerprint": ready.fingerprint, "provenance": provenance}
        parameters["inference"] = {"passes": 1, "native_scale": 2, "input": "straight-rgb8-no-matte",
                                   "output": "verified-native-rgb8", "input_resize": None, "tiling": None}
        parameters["alpha"] = {"preserve": True, "native_alpha": "independent-original-alpha-LANCZOS",
                               "reducing_gap": None, "matte": None, "premultiply_rgb_input": False,
                               "final_rgba": "pillow-associated-alpha-LANCZOS"}
        parameters["resize"]["stage"] = "native-neural-output-to-final-target"
        parameters["sufficient_source"] = "exact-original-no-optional-work-v1"
        return ProcessingRecipe.from_spec(spec)

    def produce_png(self, snapshot, target_dimensions, session, check_cancel):
        check_cancel()
        rgba, profile = pillow.decode_source_rgba(snapshot)
        check_cancel()
        image_module = self.host.image_module
        try:
            with ExitStack() as images:
                source = images.enter_context(image_module.frombytes(
                    "RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()),
                    "raw", "RGBA", rgba.bytesPerLine(), 1))
                rgb = images.enter_context(source.convert("RGB"))  # Straight samples; no matte.
                alpha = images.enter_context(source.getchannel("A"))
                check_cancel()
                result = session.infer(rgb.tobytes(), source.width, source.height)
                check_cancel()
                native = images.enter_context(image_module.frombytes(
                    "RGB", (result.width, result.height), result.rgb_bytes))
                native_alpha = images.enter_context(alpha.resize(
                    native.size, resample=image_module.Resampling.LANCZOS, reducing_gap=None))
                native.putalpha(native_alpha)
                check_cancel()
                output = native if native.size == target_dimensions else images.enter_context(native.resize(
                    target_dimensions, resample=image_module.Resampling.LANCZOS, reducing_gap=None))
                output.info.clear()
                options = {"compress_level": 4, "optimize": False}
                if profile is not None:
                    options["icc_profile"] = profile
                check_cancel()
                with BytesIO() as encoded:
                    output.save(encoded, format="PNG", **options)
                    data = encoded.getvalue()
                check_cancel()
                return data
        except (OSError, ValueError, MemoryError) as error:
            raise pillow.ProcessingFailure("processing_failed", "Neural host processing/encoding failed: " + str(error)) from error
        finally:
            rgba = None


def discover_neural_runtime() -> NeuralRuntime:
    return NeuralRuntime(pillow.discover_pillow_runtime())

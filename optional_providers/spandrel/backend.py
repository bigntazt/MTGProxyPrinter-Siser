"""Lazy, whole-image CUDA FP32 Spandrel implementation; no image decoding."""
import importlib.metadata
import platform
import subprocess
from time import perf_counter


class Fatal(RuntimeError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def validate_descriptor(descriptor, reference):
    actual = (str(descriptor.architecture.id), descriptor.scale,
              descriptor.input_channels, descriptor.output_channels)
    expected = (reference["architecture"], reference["scale"], 3, 3)
    if actual != expected:
        raise Fatal("descriptor_mismatch", f"Expected {expected}, detected {actual}")


def validate_tensor(tensor, shape, device):
    if tuple(tensor.shape) != tuple(shape):
        raise Fatal("tensor_shape", f"Expected tensor shape {shape}, got {tuple(tensor.shape)}")
    if str(tensor.device) != device or str(tensor.dtype) != "torch.float32":
        raise Fatal("tensor_type", "Tensor must be FP32 on the selected CUDA device")
    if not tensor.isfinite().all().item():
        raise Fatal("tensor_nonfinite", "Tensor contains nonfinite samples")
    if tensor.amin().item() < 0 or tensor.amax().item() > 1:
        raise Fatal("tensor_range", "Tensor samples must be within [0,1]")


def quantize_native(tensor, torch):
    # One rounding step: nearest integer, ties to even (torch.round), then uint8.
    return tensor.mul(255).round().to(dtype=torch.uint8).squeeze(0).permute(1, 2, 0).contiguous().cpu().numpy().tobytes()


class TorchBackend:
    def __init__(self, payload, reference, device):
        # These imports happen only after checkpoint bytes have passed the pin.
        import numpy as np
        import torch
        from safetensors.torch import load
        from spandrel import ImageModelDescriptor, ModelLoader

        if not torch.cuda.is_available():
            raise Fatal("cuda_unavailable", "cuda:0 is required; CPU fallback is not supported")
        self.torch, self.np, self.device = torch, np, device
        torch.manual_seed(2200)
        torch.set_num_threads(4)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        # M22 did not enable use_deterministic_algorithms; record its actual state.
        started = perf_counter()
        descriptor = ModelLoader().load_from_state_dict(load(payload))
        if not isinstance(descriptor, ImageModelDescriptor):
            raise Fatal("unsupported_descriptor", "Expected an ImageModelDescriptor")
        validate_descriptor(descriptor, reference)
        self.model = descriptor.eval().to(device=device, dtype=torch.float32)
        if str(self.model.device) != device or self.model.dtype != torch.float32:
            raise Fatal("model_type", "Model device/precision mismatch")
        torch.cuda.synchronize(device)
        self.load_seconds = perf_counter() - started
        driver = None
        try:
            driver = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                                    shell=False, capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        self.facts = {
            "packages": {p: importlib.metadata.version(p) for p in
                         ("torch", "torchvision", "spandrel", "numpy", "safetensors", "einops", "packaging")},
            "python": platform.python_version(), "device": device,
            "device_name": torch.cuda.get_device_name(device),
            "device_capability": list(torch.cuda.get_device_capability(device)),
            "cuda_runtime": torch.version.cuda, "cudnn": torch.backends.cudnn.version(), "driver": driver,
            "architecture": str(descriptor.architecture.id), "scale": descriptor.scale,
            "input_channels": descriptor.input_channels, "output_channels": descriptor.output_channels,
            "settings": {"precision": "float32", "seed": 2200, "torch_threads": torch.get_num_threads(),
                         "cudnn_benchmark": torch.backends.cudnn.benchmark,
                         "cudnn_deterministic": torch.backends.cudnn.deterministic,
                         "matmul_tf32": torch.backends.cuda.matmul.allow_tf32,
                         "cudnn_tf32": torch.backends.cudnn.allow_tf32,
                         "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
                         "eval": not descriptor.model.training, "inference_mode": True,
                         "passes": 1, "tiling": None, "precision_fallback": False,
                         "quantization": "round-nearest-ties-even-rgb8-v1"}}

    def infer(self, payload, width, height):
        torch, np = self.torch, self.np
        # copy owns writable samples and binds the tensor to the verified bytes.
        samples = np.frombuffer(payload, dtype=np.uint8).copy().reshape(height, width, 3)
        source = native = None
        try:
            with torch.inference_mode():
                source = torch.from_numpy(samples).permute(2, 0, 1).unsqueeze(0).to(
                    device=self.device, dtype=torch.float32).div_(255)
                validate_tensor(source, (1, 3, height, width), self.device)
                torch.cuda.synchronize(self.device)
                started = perf_counter()
                native = self.model(source)  # Descriptor manages its own padding/clamping.
                torch.cuda.synchronize(self.device)
                inference_seconds = perf_counter() - started
                validate_tensor(native, (1, 3, height * 2, width * 2), self.device)
                started = perf_counter()
                result = quantize_native(native, torch)
                return result, {"inference_seconds": inference_seconds,
                                "quantization_seconds": perf_counter() - started}
        finally:
            del native, source, samples

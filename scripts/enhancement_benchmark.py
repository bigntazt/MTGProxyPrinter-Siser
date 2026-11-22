#!/usr/bin/env python3
"""M22 standalone, local-only enhancement benchmark. Never imports the application."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps, features

SCHEMA = "m22-benchmark-v1"
CANONICAL = {"regular": (745, 1040), "oversized": (1040, 1490)}
COLOR_POLICY = "EXIF transpose; embedded ICC to sRGB; untagged RGB/L assumed sRGB; gamma-encoded Lanczos; PNG8"
def srgb_profile():
    """Normalize nonvisual ICC metadata so generated probe files are reproducible."""
    data = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    # ICC header creation date: 2000-01-01 00:00:00. An all-zero profile ID is unset.
    data[24:36] = bytes.fromhex("07d000010001000000000000")
    data[84:100] = bytes(16)
    return bytes(data)


SRGB = srgb_profile()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(recipe):
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def target_size(card_type, ppi):
    if ppi not in (600, 1200):
        raise ValueError("Only 600 and 1200 PPI are supported; 1200 is the maximum.")
    return tuple(n * (ppi // 300) for n in CANONICAL[card_type])


def sufficient(size, target):
    return all(a >= b for a, b in zip(size, target))


def ai_decision(size, target, nonopaque_alpha, allow_alpha=False):
    if sufficient(size, target):
        return ("skipped", "Source already meets both selected target dimensions; no AI")
    if nonopaque_alpha and not allow_alpha:
        return ("unsupported", "Nonopaque alpha: AI path intentionally unsupported; controls preserve alpha")
    return None


def neural_label(scale):
    return f"{scale}x neural upscale followed by conventional resizing"


def restore_alpha(source, native):
    """Infer straight RGB; resize the untouched source alpha with Lanczos independently."""
    if source.mode == "RGBA":
        native = native.copy()
        native.putalpha(source.getchannel("A").resize(native.size, Image.Resampling.LANCZOS))
    return native


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False),
                         encoding="utf-8")
    os.replace(temporary, path)


def load_image(path):
    with Image.open(path) as source:
        source.load()
        stored_size = source.size
        image = ImageOps.exif_transpose(source)
        image.load()
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        alpha = image.convert("RGBA").getchannel("A") if has_alpha else None
        nonopaque = alpha is not None and alpha.getextrema()[0] < 255
        icc = image.info.get("icc_profile")
        if image.mode == "CMYK" and not icc:
            raise ValueError("Untagged CMYK is unsupported; no implicit color assumption.")
        rgb = image.convert("RGB")
        conversion = "untagged assumed sRGB"
        if icc:
            # Use the original color space for profile conversion (not preconverted CMYK).
            profile_source = image.convert("RGB") if image.mode in ("RGBA", "P", "L", "LA") else image
            rgb = ImageCms.profileToProfile(profile_source, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                            ImageCms.createProfile("sRGB"), outputMode="RGB")
            conversion = "embedded ICC converted to sRGB, Pillow default perceptual intent"
        if alpha is not None:
            rgb.putalpha(alpha)
        return rgb.copy(), {"stored_dimensions": list(stored_size),
                            "source_dimensions": list(rgb.size),
                            "source_mode": source.mode, "has_alpha": has_alpha,
                            "nonopaque_alpha": bool(nonopaque), "color_conversion": conversion}


def validate_output(path, expected_size, expected_hash=None):
    try:
        path = Path(path)
        if not path.is_file() or (expected_hash and sha256(path) != expected_hash):
            return False
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            return image.format == "PNG" and image.size == tuple(expected_size) and image.mode in ("RGB", "RGBA")
    except (OSError, ValueError, SyntaxError):
        return False


def encode_png(image, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    try:
        image.save(temporary, format="PNG", compress_level=4, icc_profile=SRGB)
        if not validate_output(temporary, image.size):
            raise ValueError("Encoded output failed decode/dimensions validation")
        digest = sha256(temporary)
        os.replace(temporary, path)
        return {"output": str(path.resolve()), "output_sha256": digest,
                "output_bytes": path.stat().st_size, "final_dimensions": list(image.size),
                "output_mode": image.mode}
    finally:
        temporary.unlink(missing_ok=True)


def reusable(path, recipe):
    try:
        record = read_json(path)
        if record["status"] != "success" or record["recipe"] != recipe or record["recipe_id"] != identity(recipe):
            return None
        if not validate_output(record["output"], record["final_dimensions"], record["output_sha256"]):
            return None
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None


def environment(gpu=False):
    packages = {}
    for name in ("spandrel", "torch", "torchvision", "numpy", "Pillow", "safetensors", "packaging", "einops"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    result = {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
              "packages": packages, "pillow_lcms": features.version("littlecms2"),
              "harness_sha256": sha256(__file__)}
    if gpu:
        import torch
        result["cuda_runtime"] = torch.version.cuda
        result["cuda_available"] = torch.cuda.is_available()
        if not result["cuda_available"]:
            raise RuntimeError("CUDA is unavailable. CPU fallback is not automatic.")
        result["gpu"] = torch.cuda.get_device_name(0)
        result["compute_capability"] = list(torch.cuda.get_device_capability(0))
        result["gpu_total_bytes"] = torch.cuda.get_device_properties(0).total_memory
        result["cudnn"] = torch.backends.cudnn.version()
        query = subprocess.run(["nvidia-smi", "--query-gpu=uuid,name,driver_version,memory.total",
                                "--format=csv,noheader"], capture_output=True, text=True, check=True)
        result["nvidia_smi"] = query.stdout.strip()
    return result


def configure_torch():
    import torch
    torch.manual_seed(2200)
    torch.set_num_threads(4)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    return torch


@contextlib.contextmanager
def gpu_lock(models_dir):
    """One GPU benchmark process per model directory; no stale-lock auto deletion."""
    path = Path(models_dir) / ".m22-gpu-job.lock"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def infer_native(descriptor, image, repeats, first_after_load):
    torch = configure_torch()
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    tensor = torch.from_numpy(rgb.transpose(2, 0, 1).copy()).unsqueeze(0).to("cuda", torch.float32)
    expected = (1, 3, image.height * descriptor.scale, image.width * descriptor.scale)
    if tensor.shape != (1, 3, image.height, image.width) or tensor.device.type != "cuda":
        raise ValueError("Invalid input shape/device")
    if not torch.isfinite(tensor).all().item() or tensor.min().item() < 0 or tensor.max().item() > 1:
        raise ValueError("Invalid input range")
    if descriptor.device != tensor.device or descriptor.dtype != tensor.dtype:
        raise ValueError("Descriptor/tensor device or precision mismatch")
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode():
        start = time.perf_counter()
        output = descriptor(tensor)
        torch.cuda.synchronize()
        first_time = time.perf_counter() - start
        del output
        warm = []
        for _ in range(repeats):
            start = time.perf_counter()
            output = descriptor(tensor)
            torch.cuda.synchronize()
            warm.append(time.perf_counter() - start)
            if len(warm) < repeats:
                del output
        allocated = torch.cuda.max_memory_allocated()
        reserved = torch.cuda.max_memory_reserved()
        if tuple(output.shape) != expected or output.device != tensor.device or output.dtype != torch.float32:
            raise ValueError(f"Unexpected native output: {output.shape}, {output.device}, {output.dtype}; expected {expected}")
        if not torch.isfinite(output).all().item():
            raise ValueError("Nonfinite neural output")
        low, high = output.min().item(), output.max().item()
        if low < 0 or high > 1:
            raise ValueError(f"Out-of-range descriptor output: [{low}, {high}]")
        start = time.perf_counter()
        array = output[0].permute(1, 2, 0).mul(255).round().to(torch.uint8).cpu().numpy()
        native = Image.fromarray(array)
        transfer_quantize = time.perf_counter() - start
    return native, {"first_call_for_shape_seconds": first_time,
                    "first_call_is_first_after_model_load": first_after_load,
                    "warm_inference_seconds": warm, "warm_median_seconds": statistics.median(warm),
                    "peak_allocated_bytes": allocated, "peak_reserved_bytes": reserved,
                    "transfer_quantize_seconds": transfer_quantize, "tensor_range": [low, high]}


def metric_psnr(image, truth_path):
    if not truth_path or image.mode == "RGBA":
        return None
    truth, _ = load_image(truth_path)
    reference = truth.convert("RGB").resize(image.size, Image.Resampling.LANCZOS)
    a = np.asarray(image.convert("RGB"), dtype=np.float32)
    b = np.asarray(reference, dtype=np.float32)
    mse = float(np.mean((a - b) ** 2))
    # JSON cannot represent Infinity. Exact match gets an explicit separate flag.
    return {"rgb_psnr_db": 10 * math.log10(255**2 / mse) if mse else None,
            "exact_match": mse == 0, "rgb_mae": float(np.mean(np.abs(a - b))),
            "reference_resize": "sRGB PNG8 truth resized with Lanczos to measured dimensions"}


def final_result(out, recipe, image, common, method, truth):
    record_path = out / "records" / (identity(recipe) + ".json")
    record = reusable(record_path, recipe)
    if record:
        return record, True
    record = {**common, "recipe": recipe, "recipe_id": identity(recipe), "method": method}
    try:
        start = time.perf_counter()
        if method == "original":
            result = image.copy()
            operation = "Original normalized sRGB pixels; target not applied"
        else:
            size = tuple(recipe["target_dimensions"])
            result = image.resize(size, Image.Resampling.LANCZOS)
            operation = ("identity copy; input already equals the canonical target rectangle"
                         if image.size == size else
                         "complete face mapped to canonical rectangle with Lanczos; no crop or pad")
        record["final_resize_seconds"] = time.perf_counter() - start
        start = time.perf_counter()
        record.update(encode_png(result, out / "images" / (identity(recipe) + ".png")))
        record["encoding_validation_seconds"] = time.perf_counter() - start
        record["final_operation"] = operation
        record["quality_against_synthetic_truth"] = metric_psnr(result, truth)
        record["status"] = "success"
    except Exception as error:
        record.update(status="failure", error=f"{type(error).__name__}: {error}")
    write_json(record_path, record)
    return record, False


def run(args):
    manifest = read_json(args.model_manifest)["models"]
    model_map = {m["id"]: m for m in manifest}
    corpus_path = Path(args.corpus).resolve()
    corpus = read_json(corpus_path)
    samples = corpus["samples"]
    if args.samples:
        ids = args.samples.split(",")
        if set(ids) - {s["id"] for s in samples}:
            raise ValueError("Unknown requested sample")
        samples = [s for s in samples if s["id"] in ids]
    candidates = args.models.split(",")
    unknown = set(candidates) - {"original", "lanczos"} - set(model_map)
    if unknown:
        raise ValueError(f"Unknown models: {sorted(unknown)}")
    env = environment(any(m not in ("original", "lanczos") for m in candidates))
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "environment.json", env)
    run_index = []
    for candidate in candidates:
        neural = candidate not in ("original", "lanczos")
        descriptor = None
        load_error = None
        load_seconds = None
        model = model_map.get(candidate)
        checkpoint_hash = None
        checkpoint = None
        capabilities = None
        if neural:
            checkpoint = Path(args.models_dir) / model["filename"]
            if not checkpoint.is_file():
                load_error = f"Missing checkpoint {model['filename']}"
            else:
                checkpoint_hash = sha256(checkpoint)
                if checkpoint_hash != model["sha256"]:
                    load_error = "Checkpoint hash does not match manifest"
        first_after_load = True
        with gpu_lock(args.models_dir) if neural else contextlib.nullcontext():
            for sample in samples:
                source = (corpus_path.parent / sample["file"]).resolve()
                actual_hash = sha256(source)
                if actual_hash != sample["sha256"]:
                    raise ValueError(f"Source hash mismatch: {sample['id']}")
                image, image_info = load_image(source)
                truth = (corpus_path.parent / sample["truth_file"]).resolve() if sample.get("truth_file") else None
                common = {"sample_id": sample["id"], "face": sample["face"],
                          "card_type": sample["card_type"], "source_sha256": actual_hash,
                          **image_info}
                native_recipe = {"schema": SCHEMA, "source_sha256": actual_hash,
                                 "checkpoint_sha256": checkpoint_hash, "model": candidate,
                                 "color_policy": COLOR_POLICY, "alpha_policy": args.alpha_policy,
                                 "device": "cuda:0" if neural else "CPU", "precision": "FP32" if neural else "PNG8",
                                 "tiling": "none; descriptor size padding only", "repeats": args.repeats,
                                 "seed": 2200, "tf32": False, "cudnn_benchmark": False,
                                 "cudnn_deterministic": True, "runtime": env}
                native_record = None
                native_image = None
                for ppi in args.targets:
                    size = target_size(sample["card_type"], ppi)
                    recipe = {**native_recipe, "card_type": sample["card_type"], "target_ppi": ppi,
                              "target_dimensions": list(size), "mapping": "full face; Lanczos after inference",
                              "control": candidate if not neural else None}
                    common_target = {**common, "target_ppi": ppi, "target_dimensions": list(size),
                                     "source_aspect_ratio": image.width / image.height,
                                     "aspect_ratio_difference_percent": 100 * ((image.width / image.height) / (size[0] / size[1]) - 1),
                                     "native_scale": None, "native_output_dimensions": None,
                                     "load_seconds": load_seconds, "capabilities": capabilities}
                    final_path = out / "records" / (identity(recipe) + ".json")
                    cached_final = reusable(final_path, recipe)
                    if cached_final:
                        run_index.append({"record": str(final_path), "reused": True})
                        print(f"REUSED {candidate} {sample['id']} {ppi}", flush=True)
                        continue
                    decision = ai_decision(image.size, size, image_info["nonopaque_alpha"], args.alpha_policy == "resize") if neural else None
                    if neural and not decision and load_error:
                        decision = ("failure", load_error)
                    if decision:
                        write_json(final_path, {**common_target, "recipe": recipe, "recipe_id": identity(recipe),
                                                "method": candidate, "status": decision[0], "reason": decision[1]})
                    elif not neural:
                        _, _ = final_result(out, recipe, image, common_target, candidate, truth)
                    else:
                        native_path = out / "native-records" / (identity(native_recipe) + ".json")
                        if native_record is None:
                            native_record = reusable(native_path, native_recipe)
                            if native_record:
                                with Image.open(native_record["output"]) as decoded:
                                    native_image = decoded.copy()
                            else:
                                native_record = {**common, "recipe": native_recipe, "recipe_id": identity(native_recipe),
                                                 "method": candidate}
                                try:
                                    torch = configure_torch()
                                    if descriptor is None:
                                        from spandrel import ImageModelDescriptor, ModelLoader
                                        torch.cuda.empty_cache()
                                        torch.cuda.synchronize()
                                        start = time.perf_counter()
                                        # Keep Spandrel 0.4.2 restricted .pth unpickling unchanged.
                                        descriptor = ModelLoader(device="cpu").load_from_file(checkpoint)
                                        if not isinstance(descriptor, ImageModelDescriptor):
                                            raise ValueError("Not an ImageModelDescriptor")
                                        if descriptor.scale != model["scale"] or descriptor.input_channels != 3 or descriptor.output_channels != 3:
                                            raise ValueError("Unexpected descriptor scale/channels")
                                        descriptor.eval().to(device="cuda", dtype=torch.float32)
                                        torch.cuda.synchronize()
                                        load_seconds = time.perf_counter() - start
                                        capabilities = {"architecture": str(descriptor.architecture.id),
                                                        "scale": descriptor.scale, "supports_half": descriptor.supports_half,
                                                        "supports_bfloat16": descriptor.supports_bfloat16,
                                                        "tiling": str(descriptor.tiling), "size_requirements": str(descriptor.size_requirements),
                                                        "input_channels": descriptor.input_channels, "output_channels": descriptor.output_channels}
                                        write_json(out / "descriptors" / (candidate + ".json"),
                                                   {"model": candidate, "sha256": checkpoint_hash,
                                                    "load_seconds": load_seconds, **capabilities})
                                    native_image, timings = infer_native(descriptor, image, args.repeats, first_after_load)
                                    start_alpha = time.perf_counter()
                                    native_image = restore_alpha(image, native_image)
                                    timings["alpha_resize_seconds"] = time.perf_counter() - start_alpha
                                    first_after_load = False
                                    native_record.update(timings, status="success", load_seconds=load_seconds,
                                                         capabilities=capabilities, native_scale=descriptor.scale,
                                                         native_output_dimensions=list(native_image.size),
                                                         native_quantization="round to PNG8 once before shared final derivatives")
                                    start = time.perf_counter()
                                    native_record.update(encode_png(native_image, out / "native-images" / (identity(native_recipe) + ".png")))
                                    native_record["native_encoding_validation_seconds"] = time.perf_counter() - start
                                except Exception as error:
                                    import gc
                                    import torch
                                    status = "out-of-memory" if isinstance(error, torch.cuda.OutOfMemoryError) else "failure"
                                    native_record.update(status=status, error=f"{type(error).__name__}: {error}",
                                                         load_seconds=load_seconds, capabilities=capabilities)
                                    native_image = None
                                    gc.collect()
                                    torch.cuda.empty_cache()
                                write_json(native_path, native_record)
                        if native_record["status"] == "success":
                            common_target.update(native_record_id=native_record["recipe_id"],
                                                 native_output_dimensions=native_record["native_output_dimensions"],
                                                 native_scale=native_record["native_scale"],
                                                 load_seconds=native_record["load_seconds"],
                                                 capabilities=native_record["capabilities"],
                                                 native_timings={k: native_record[k] for k in (
                                                     "first_call_for_shape_seconds", "first_call_is_first_after_model_load",
                                                     "warm_inference_seconds", "warm_median_seconds", "peak_allocated_bytes",
                                                     "peak_reserved_bytes", "transfer_quantize_seconds", "native_encoding_validation_seconds")})
                            common_target["recipe_label"] = neural_label(native_record["native_scale"])
                            final_result(out, recipe, native_image, common_target, candidate, truth)
                        else:
                            write_json(final_path, {**common_target, "recipe": recipe, "recipe_id": identity(recipe),
                                                    "method": candidate, "status": native_record["status"],
                                                    "error": native_record["error"], "native_record_id": native_record["recipe_id"]})
                    run_index.append({"record": str(final_path), "reused": False})
                    record = read_json(final_path)
                    print(f"{record['status'].upper()} {candidate} {sample['id']} {ppi}", flush=True)
                del image, native_image
            if descriptor is not None:
                del descriptor
                import gc
                import torch
                gc.collect()
                torch.cuda.empty_cache()
    write_json(out / "run-index.json", {"schema": SCHEMA, "records": run_index})
    records = [read_json(row["record"]) for row in run_index]
    counts = {s: sum(r["status"] == s for r in records) for s in sorted({r["status"] for r in records})}
    write_json(out / "summary.json", {"counts": counts, "record_count": len(records)})
    print(json.dumps(counts), flush=True)


def synthesize(args):
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    samples = []
    cases = [("dense", (372, 520), "regular", False),
             ("symbols", (372, 520), "regular", False),
             ("texture_jpeg", (372, 520), "regular", False),
             ("clean", (745, 1040), "regular", False),
             ("alpha", (372, 520), "regular", True),
             ("sufficient", (2980, 4160), "regular", False),
             ("oversized", (520, 745), "oversized", False)]
    font_hash = sha256(args.font)
    for index, (kind, input_size, card_type, alpha) in enumerate(cases):
        size = target_size(card_type, 1200)
        width, height = size
        rng = np.random.default_rng(2200 + index)
        # Controlled gradients and textured central art rectangle, with white text/frame areas.
        array = np.full((height, width, 3), 244, np.uint8)
        y0, y1 = int(height * .17), int(height * .54)
        x0, x1 = int(width * .08), int(width * .92)
        xx = np.linspace(0, 1, x1-x0)[None, :]
        yy = np.linspace(0, 1, y1-y0)[:, None]
        noise = rng.normal(0, 4, (y1-y0, x1-x0))
        for channel, factor in enumerate((1.0, .7, .45)):
            array[y0:y1, x0:x1, channel] = np.clip(35 + 150 * (xx * factor + yy * .25) + noise, 0, 255).astype(np.uint8)
        image = Image.fromarray(array)
        draw = ImageDraw.Draw(image)
        scale = width / 745
        font = ImageFont.truetype(args.font, round(13 * scale))
        title_font = ImageFont.truetype(args.font, round(26 * scale))
        draw.rectangle((round(width*.025), round(height*.02), round(width*.975), round(height*.98)),
                       outline=(30, 36, 45), width=max(1, round(scale)))
        draw.text((round(width*.08), round(height*.06)), "M22 SYNTHETIC / 0123456789", font=title_font, fill="black")
        text = ["Dense text: 0O 1Il rn m; .,:!? - + = / (2/W)",
                "When this probe enters, draw 2 cards.",
                "Then choose one: {W} {U} {B} {R} {G}.",
                "Thin punctuation; quoted 'rules' and numbers.",
                "This is a technical probe, not real card artwork."]
        for row in range(15):
            draw.text((round(width*.08), round(height*.59 + row*height*.018)),
                      text[row % len(text)], font=font, fill=(12, 16, 24))
        for offset in range(8):
            y = round(height * .93 + offset * scale * 2)
            draw.line((round(width*.08), y, round(width*.92), y), fill=(20, 20, 20), width=max(1, round(scale / 2)))
        for i, color in enumerate(("white", "#4094d0", "#4b4750", "#e54c3c", "#52a061")):
            x, y, radius = round(width*(.13+.18*i)), round(height*.13), round(width*.025)
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color, outline="black", width=round(scale))
            draw.line((x-radius//2, y, x+radius//2, y), fill="black", width=round(scale))
        if alpha:
            image = image.convert("RGBA")
            mask = Image.new("L", size, 0)
            md = ImageDraw.Draw(mask)
            md.rounded_rectangle((10, 10, width-10, height-10), radius=round(width*.08), fill=255)
            # Explicit partial-alpha strip as well as rounded transparent corners.
            md.rectangle((width//3, 0, width//3+round(scale*4), height), fill=128)
            image.putalpha(mask)
        truth = root / "truth" / (kind + ".png")
        encode_png(image, truth)
        source = image.resize(input_size, Image.Resampling.LANCZOS)
        suffix = ".jpg" if kind == "texture_jpeg" else ".png"
        path = root / "sources" / (kind + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        if suffix == ".jpg":
            source.save(path, quality=40, subsampling=2, icc_profile=SRGB)
        else:
            encode_png(source, path)
        samples.append({"id": "synth_" + kind, "file": str(path.relative_to(root)),
                        "sha256": sha256(path), "face": "synthetic-front", "card_type": card_type,
                        "truth_file": str(truth.relative_to(root)), "truth_sha256": sha256(truth),
                        "kind": "synthetic", "seed": 2200+index,
                        "input_dimensions": list(input_size), "truth_dimensions": list(size),
                        "font_sha256": font_hash, "features": [kind, "dense text", "numbers", "thin lines",
                                                              "colored symbols", "gradient", "flat borders"],
                        "degradation": "Lanczos from known 1200-PPI original" + ("; JPEG quality 40, 4:2:0" if suffix == ".jpg" else "")})
    write_json(root / "manifest.json", {"schema": SCHEMA, "samples": samples,
                                       "limitations": "Synthetic probes do not qualify actual artwork or typography."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    synth = commands.add_parser("synth")
    synth.add_argument("--output", required=True)
    synth.add_argument("--font", required=True)
    synth.set_defaults(func=synthesize)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--model-manifest", required=True)
    run_parser.add_argument("--models-dir", required=True)
    run_parser.add_argument("--corpus", required=True)
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--models", required=True, help="Comma-separated shortlist IDs, original, lanczos")
    run_parser.add_argument("--samples", help="Explicit comma-separated sample IDs; default full corpus")
    run_parser.add_argument("--targets", type=int, nargs="+", choices=(600, 1200), default=[600, 1200])
    run_parser.add_argument("--repeats", type=int, choices=range(1, 6), default=2)
    run_parser.add_argument("--alpha-policy", choices=("reject", "resize"), default="reject",
                            help="resize explicitly infers straight RGB and conventionally resizes source alpha independently")
    run_parser.set_defaults(func=run)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

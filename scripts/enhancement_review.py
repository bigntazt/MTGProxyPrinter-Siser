#!/usr/bin/env python3
"""Prepare a limited private M22 corpus and produce local review artifacts."""
import argparse
import csv
import html
import json
from pathlib import Path
import shutil
import statistics

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import enhancement_benchmark as bench


def prepare(args):
    root = Path(args.output).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Generate deterministic synthetic corpus first")
    manifest = bench.read_json(manifest_path)
    if any(s.get("kind") == "real" for s in manifest["samples"]):
        raise ValueError("Real corpus already prepared; refusing to overwrite or silently change selection")
    cache = Path(args.cache).resolve()
    fronts = sorted((cache / "highres_front").glob("*/*.png"))
    backs = sorted((cache / "highres_back").glob("*/*.png"))
    lowres = sorted((cache / "lowres_front").glob("*/*.png"))
    selected = []
    for back in backs:
        front = cache / "highres_front" / back.parent.name / back.name
        if front.is_file():
            selected.extend([front, back])
            if len(selected) == 4:
                break
    selected.extend(p for p in lowres[:2] if p not in selected)
    for front in fronts:
        if len(selected) >= 10:
            break
        if front not in selected:
            selected.append(front)
    for i, source in enumerate(selected[:10], 1):
        sample_id = f"real_{i:02d}"
        dest = root / "sources" / (sample_id + source.suffix.lower())
        if dest.exists():
            raise ValueError(f"Will not overwrite {dest.name}")
        before_hash = bench.sha256(source)
        shutil.copy2(source, dest)
        if bench.sha256(dest) != before_hash or bench.sha256(source) != before_hash:
            raise ValueError("Source/copy hash changed during sampling")
        image, info = bench.load_image(dest)
        manifest["samples"].append({
            "id": sample_id, "file": str(dest.relative_to(root)), "sha256": before_hash,
            "kind": "real", "face": "front" if "front" in source.parent.parent.name else "back",
            "printing_id": source.stem, "source_path_private": str(source), "card_type": "regular",
            "size_assignment": "Regular benchmark face selected explicitly; verify against contact sheet",
            "features": ["existing normalized PNG cache source", source.parent.parent.name],
            **info})
    manifest["limitations"] = [
        "Real samples are existing normalized cache PNGs, not pristine upstream masters; Original means acquired source bytes.",
        "No catalog lookup, application initialization, original-cache modification, or image upload.",
        "Real sample names/layout features require visual annotation. Synthetic oversized and alpha cases supplement unavailable real cases.",
        "Synthetic truth metrics measure these technical probes only; not real artwork fidelity."]
    bench.write_json(root / "manifest.json", manifest)
    contact_sheet(manifest, root, root / "corpus-contact.png")
    print(f"Private corpus: {len(manifest['samples'])} samples; {len(selected[:10])} real faces")


def contact_sheet(manifest, root, output):
    samples = manifest["samples"]
    tw, th, cols = 248, 400, 5
    sheet = Image.new("RGB", (tw * cols, th * ((len(samples) + cols - 1) // cols)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, sample in enumerate(samples):
        image, _ = bench.load_image(root / sample["file"])
        image.thumbnail((tw - 12, th - 55))
        tile = Image.new("RGB", image.size, "#ddd")
        if image.mode == "RGBA":
            tile.paste(image, mask=image.getchannel("A"))
        else:
            tile.paste(image)
        x, y = (i % cols) * tw, (i // cols) * th
        sheet.paste(tile, (x + 6, y + 45))
        draw.text((x + 6, y + 4), sample["id"] + " / " + sample["face"], fill="black")
        draw.text((x + 6, y + 20), sample["card_type"], fill="black")
    sheet.save(output)


def report(args):
    results = Path(args.results).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    corpus_path = Path(args.corpus).resolve()
    corpus = bench.read_json(corpus_path)
    samples = {s["id"]: s for s in corpus["samples"]}
    index = Path(args.index) if args.index else results / "run-index.json"
    entries = bench.read_json(index)["records"] if index.is_file() or args.index else []
    if not args.index:
        indexed = {Path(entry["record"]).resolve() for entry in entries}
        entries.extend({"record": path} for path in sorted((results / "records").glob("*.json"))
                       if path.resolve() not in indexed)
    records = []
    for entry in entries:
        record = bench.read_json(entry["record"])
        if "sample_id" in entry:
            record.update(sample_id=entry["sample_id"], face=entry["face"])
        records.append(record)
    # Every successful result must still decode and match its hash before inclusion.
    for record in records:
        if record["status"] == "success" and not bench.validate_output(
                record["output"], record["final_dimensions"], record["output_sha256"]):
            raise ValueError("A success record no longer validates; refusing misleading report")
    fields = ["sample_id", "face", "method", "target_ppi", "status", "source_dimensions",
              "native_scale", "native_output_dimensions", "final_dimensions", "load_seconds",
              "warm_median_seconds", "peak_allocated_mib", "peak_reserved_mib",
              "final_resize_seconds", "encoding_validation_seconds", "output_bytes", "rgb_psnr_db", "reason"]
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for record in records:
            row = {k: record.get(k) for k in fields}
            timing = record.get("native_timings", {})
            row["warm_median_seconds"] = timing.get("warm_median_seconds")
            for memory in ("allocated", "reserved"):
                value = timing.get(f"peak_{memory}_bytes")
                row[f"peak_{memory}_mib"] = value / 1024**2 if value is not None else None
            row["rgb_psnr_db"] = (record.get("quality_against_synthetic_truth") or {}).get("rgb_psnr_db")
            row["reason"] = record.get("reason", record.get("error"))
            writer.writerow(row)
    groups = {}
    for model in sorted({r["method"] for r in records}):
        subset = [r for r in records if r["method"] == model]
        native = [bench.read_json(p) for p in (results / "native-records").glob("*.json")]
        native_ids = {r.get("native_record_id") for r in subset}
        successes = [r for r in native if r["method"] == model and r["status"] == "success"
                     and r["recipe_id"] in native_ids]
        groups[model] = {
            "counts": {s: sum(r["status"] == s for r in subset) for s in sorted({r["status"] for r in subset})},
            "unique_successful_native_recipes": len(successes),
            "warm_median_seconds_range": [min(r["warm_median_seconds"] for r in successes),
                                          max(r["warm_median_seconds"] for r in successes)] if successes else None,
            "peak_allocated_mib": max((r["peak_allocated_bytes"]/1024**2 for r in successes), default=None),
            "peak_reserved_mib": max((r["peak_reserved_bytes"]/1024**2 for r in successes), default=None)}
    bench.write_json(output / "aggregate.json", groups)
    # Contact sheets use identical normalized crop coordinates. The cropped 320x240
    # target-pixel rectangles are kept 1:1; Original is nearest-expanded ONLY for display.
    preview_rows = []
    font = ImageFont.load_default(size=15)
    for ppi in (600, 1200):
        for sample_id in args.samples.split(","):
            selected = [r for r in records if r["sample_id"] == sample_id and
                        r["target_ppi"] == ppi and r["status"] == "success"]
            if not selected:
                continue
            selected.sort(key=lambda r: (r["method"] != "original", r["method"] != "lanczos", r["method"]))
            sample = samples[sample_id]
            target = bench.target_size(sample["card_type"], ppi)
            cols, cw, ch = len(selected), 340, 1220
            sheet = Image.new("RGB", (cols*cw, ch), "white")
            draw = ImageDraw.Draw(sheet)
            for i, record in enumerate(selected):
                x = i*cw
                draw.text((x+5, ch-28), f"{sample_id} / {record['face']}", fill="black", font=font)
                label = record["method"]
                draw.text((x+5, 4), label, fill="black", font=font)
                draw.text((x+5, 26), str(ppi) + " PPI / " + record.get("recipe_label", "control")[:30],
                          fill="black", font=font)
                image, _ = bench.load_image(record["output"])
                thumbnail = image.copy()
                thumbnail.thumbnail((320, 390), Image.Resampling.LANCZOS)
                sheet.paste(flatten(thumbnail), (x+5, 55))
                # Original comparison is a nearest display expansion, never an enhanced result.
                if image.size != target:
                    image = image.resize(target, Image.Resampling.NEAREST)
                for row, (name, rx, ry) in enumerate((("Rules text: 1:1 target pixels", .10, .66),
                                                      ("Title / symbols: 1:1 target pixels", .35, .06),
                                                      ("Artwork / texture: 1:1 pixels", .20, .30),
                                                      ("Border / alpha edge: 1:1 pixels", .00, .90))):
                    left, top = int(target[0]*rx), int(target[1]*ry)
                    crop = image.crop((left, top, left+320, top+160))
                    y = 470 + row*185
                    draw.text((x+5, y-22), name, fill="black", font=font)
                    sheet.paste(flatten(crop), (x+5, y))
            name = f"{sample_id}-{ppi}-comparison.png"
            sheet.save(output / name)
            preview_rows.append((sample_id, selected[0]['face'], ppi, name))
    body = ["<html><head><meta charset='utf-8'><title>M22 local review</title></head><body>",
            "<h1>M22 private local comparisons</h1>",
            "<p>Not a print proof. Whole cards are thumbnails; crop panels retain 1:1 target pixels. "
            "Original crop display uses nearest expansion only. Full-resolution PNGs remain in results/images. "
            "No physical print quality is claimed.</p>",
            "<p><a href='results.csv'>Detailed results</a> | <a href='aggregate.json'>Aggregate</a></p>"]
    for sample_id, face, ppi, name in preview_rows:
        body.append(f"<h2>{html.escape(sample_id)} / {html.escape(face)} / {ppi} PPI</h2><a href='{name}'><img src='{name}' style='max-width:100%'></a>")
    body.append("</body></html>")
    (output / "index.html").write_text("\n".join(body), encoding="utf-8")
    print(json.dumps(groups, indent=2))


def flatten(image):
    if image.mode != "RGBA":
        return image.convert("RGB")
    result = Image.new("RGB", image.size, "#ddd")
    result.paste(image, mask=image.getchannel("A"))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--cache", required=True)
    prep.add_argument("--output", required=True)
    prep.set_defaults(func=prepare)
    rep = commands.add_parser("report")
    rep.add_argument("--results", required=True)
    rep.add_argument("--corpus", required=True)
    rep.add_argument("--output", required=True)
    rep.add_argument("--index", help="Optional preserved run-index JSON to summarize one stage only")
    rep.add_argument("--samples", default="synth_dense,synth_texture_jpeg,real_01")
    rep.set_defaults(func=report)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

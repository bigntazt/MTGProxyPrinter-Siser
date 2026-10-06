"""Measure the initial M02 PDF captures without changing their bytes.

Requires the bundled PDF runtime's pypdf, pdfplumber, Pillow, and numpy.
This deliberately handles only these simple one-page, unrotated captures.
Raster shape bounds are approximate and do not measure native cut contours.
"""

import hashlib
import importlib.metadata
import json
from pathlib import Path

import numpy as np
import pdfplumber
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import ContentStream


ROOT = Path(__file__).resolve().parent


def measure():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    result = {"tools": {name: importlib.metadata.version(name) for name in ("pypdf", "pdfplumber", "Pillow", "numpy")}, "outputs": []}
    for reference in manifest["initial_reference_outputs"]:
        artifact = reference["print_output"]
        path = ROOT / artifact["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
        reader = PdfReader(path)
        assert len(reader.pages) == 1
        page = reader.pages[0]
        assert page.get("/Rotate", 0) == 0 and page.get("/UserUnit", 1) == 1
        assert list(page.mediabox) == list(page.cropbox)
        record = {
            "id": reference["id"], "sha256": artifact["sha256"],
            "producer": reader.metadata.get("/Producer"), "creator": reader.metadata.get("/Creator"),
            "page_boxes_pt": {name: list(getattr(page, name)) for name in ("mediabox", "cropbox", "bleedbox", "trimbox", "artbox")},
            "explicit_page_boxes": [name for name in ("/MediaBox", "/CropBox", "/BleedBox", "/TrimBox", "/ArtBox") if name in page],
            "rotation_degrees": 0, "rotation_explicit": "/Rotate" in page,
            "user_unit": 1, "user_unit_explicit": "/UserUnit" in page,
            "page_mm": [float(page.mediabox.width) * 25.4 / 72, float(page.mediabox.height) * 25.4 / 72],
            "content_cm_operations": [[float(v) for v in args] for args, op in ContentStream(page.get_contents(), reader).operations if op == b"cm"],
            "vector_rectangles_mm": [], "raster_images": [],
        }
        with pdfplumber.open(path) as pdf:
            parsed = pdf.pages[0]
            assert not parsed.curves and not parsed.lines
            for rect in parsed.rects:
                assert rect["fill"] and not rect["stroke"]
                record["vector_rectangles_mm"].append({
                    "bounds": [round(rect[k] * 25.4 / 72, 8) for k in ("x0", "top", "x1", "bottom")],
                    "size": [round(rect[k] * 25.4 / 72, 8) for k in ("width", "height")],
                    "filled": True, "stroked": False,
                })
            assert len(parsed.rects) == 8
            decoded_by_name = {image.name.removesuffix(".png"): image for image in page.images}
            for placed in parsed.images:
                decoded = decoded_by_name[placed["name"]].image.convert("RGBA")
                pixels = np.asarray(Image.alpha_composite(Image.new("RGBA", decoded.size, "white"), decoded).convert("L"))
                mask = pixels < 128
                rows = np.flatnonzero(mask.any(axis=1))
                bands = np.split(rows, np.where(np.diff(rows) > 1)[0] + 1)
                raster = {
                    "name": placed["name"], "pixel_size": list(decoded.size),
                    "placed_bounds_mm": [round(placed[k] * 25.4 / 72, 8) for k in ("x0", "top", "x1", "bottom")],
                    "pixel_pitch_mm": [placed["width"] / decoded.width * 25.4 / 72, placed["height"] / decoded.height * 25.4 / 72],
                    "dark_row_bands": [],
                }
                for band in bands:
                    if len(band) == 0:
                        continue
                    y0, y1 = int(band[0]), int(band[-1]) + 1
                    cols = np.flatnonzero(mask[y0:y1].any(axis=0))
                    x0, x1 = int(cols[0]), int(cols[-1]) + 1
                    bounds = [
                        placed["x0"] + x0 / decoded.width * placed["width"],
                        placed["top"] + y0 / decoded.height * placed["height"],
                        placed["x0"] + x1 / decoded.width * placed["width"],
                        placed["top"] + y1 / decoded.height * placed["height"],
                    ]
                    raster["dark_row_bands"].append({"pixel_bounds": [x0, y0, x1, y1], "bounds_mm": [round(v * 25.4 / 72, 8) for v in bounds]})
                record["raster_images"].append(raster)
        result["outputs"].append(record)
        print(f"{reference['id']}: {record['page_mm']} mm, 8 filled mark bars, {len(record['raster_images'])} raster artwork image(s)")
    output = ROOT / "initial-measurements.json"
    output.write_bytes((json.dumps(result, indent=2) + "\n").encode("utf-8"))
    print("Recorded output geometry; row bands may combine shapes that overlap vertically.")


if __name__ == "__main__":
    measure()

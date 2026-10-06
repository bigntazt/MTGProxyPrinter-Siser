"""Verify M02 probe geometry and the hashes of any acquired reference files.

Run from any directory with the existing development Python. This does not import
the application, generate registration marks, or prove Leonardo import behavior.
"""

import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
SVG = "{http://www.w3.org/2000/svg}"


def check_artifact(artifact):
    path, digest = artifact["path"], artifact["sha256"]
    assert (path is None) == (digest is None), "Path and hash must be provided together"
    if path is None:
        return 0
    resolved = (ROOT / path).resolve()
    assert resolved.is_relative_to(ROOT), "Reference paths must stay inside this fixture directory"
    assert resolved.is_file(), f"Missing artifact: {path}"
    assert hashlib.sha256(resolved.read_bytes()).hexdigest() == digest, f"Hash mismatch: {path}"
    return 1


def verify():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    all_shapes = {}
    reference_count = 0
    for reference in manifest.get("initial_reference_outputs", []):
        reference_count += check_artifact(reference["print_output"])
        reference_count += check_artifact(reference["native_project"])
        reference_count += check_artifact(reference["settings_record"])
        assert reference["status"] == "measured_output_settings_pending"
        assert reference["observed_settings"] is None
    for case in manifest["cases"]:
        check_artifact(case["probe"])
        root = ET.parse(ROOT / case["probe"]["path"]).getroot()
        page = case["expected"]["page_mm"]
        assert root.tag == SVG + "svg"
        assert root.attrib["width"] == f"{page[0]:g}mm"
        assert root.attrib["height"] == f"{page[1]:g}mm"
        assert [float(n) for n in root.attrib["viewBox"].split()] == [0, 0, *page]
        assert set(root.attrib) == {"width", "height", "viewBox"}
        shapes = {}
        points = []
        for element in root:
            assert element.tag in {SVG + tag for tag in ("title", "desc", "rect", "circle", "polygon")}
            if element.tag in {SVG + "title", SVG + "desc"}:
                continue
            assert element.attrib["fill"] == "black"
            assert not list(element), "No nested assets or transforms"
            kind = element.tag.removeprefix(SVG)
            if kind == "rect":
                assert set(element.attrib) == {"id", "x", "y", "width", "height", "fill"}
                x, y, width, height = (float(element.attrib[k]) for k in ("x", "y", "width", "height"))
                assert width > 0 and height > 0
                geometry = {"kind": kind, "xywh_mm": [x, y, width, height]}
                bounds = [(x, y), (x + width, y + height)]
            elif kind == "circle":
                assert set(element.attrib) == {"id", "cx", "cy", "r", "fill"}
                cx, cy, radius = (float(element.attrib[k]) for k in ("cx", "cy", "r"))
                assert radius > 0
                geometry = {"kind": kind, "center_mm": [cx, cy], "radius_mm": radius}
                bounds = [(cx - radius, cy - radius), (cx + radius, cy + radius)]
            else:
                assert set(element.attrib) == {"id", "points", "fill"}
                bounds = [tuple(map(float, point.split(","))) for point in element.attrib["points"].split()]
                assert len(bounds) >= 3 and all(len(point) == 2 for point in bounds)
                geometry = {"kind": kind, "vertices_mm": [list(point) for point in bounds]}
            assert element.attrib["id"] not in shapes
            shapes[element.attrib["id"]] = geometry
            points.extend(bounds)
        assert shapes == case["expected"]["shapes"]
        assert len(shapes) == 4
        assert all(0 < x < page[0] and 0 < y < page[1] for x, y in points)
        xs, ys = zip(*points)
        assert [min(xs), min(ys), max(xs), max(ys)] == case["expected"]["occupied_bounds_mm"]
        all_shapes[case["id"]] = shapes
        for run in case["reference_runs"]:
            for artifact in (run["native_project"], run["print_output"], run["settings_record"], *run["screenshots"]):
                reference_count += check_artifact(artifact)
            assert run["status"] in {"pending_acquisition", "acquired_unmeasured", "measured"}
            if run["status"] == "pending_acquisition":
                assert run["native_project"]["path"] is None and run["print_output"]["path"] is None
                assert all(value is None for value in run["import_checks"].values())
                assert all(value is None for value in run["measurements"].values())
            else:
                assert run["print_output"]["path"] is not None
        print(f"PASS {case['id']}: page {page}, 4 shapes, declared geometry and SHA-256")

    original = all_shapes["letter-portrait"]
    shifted = all_shapes["letter-portrait-shifted"]
    for name, shape in original.items():
        other = shifted[name]
        assert shape["kind"] == other["kind"]
        if shape["kind"] == "rect":
            a, b = shape["xywh_mm"], other["xywh_mm"]
            assert [b[0] - a[0], b[1] - a[1]] == [12, 9] and a[2:] == b[2:]
        elif shape["kind"] == "circle":
            a, b = shape["center_mm"], other["center_mm"]
            assert [b[0] - a[0], b[1] - a[1]] == [12, 9]
            assert shape["radius_mm"] == other["radius_mm"]
        else:
            assert len(shape["vertices_mm"]) == len(other["vertices_mm"])
            for a, b in zip(shape["vertices_mm"], other["vertices_mm"]):
                assert [b[0] - a[0], b[1] - a[1]] == [12, 9]

    # Exact unit relations used by the planned PDF measurements; no output is being measured here.
    assert math.isclose(215.9 * 72 / 25.4, 612)
    assert math.isclose(279.4 * 72 / 25.4, 792)
    for mm in (0, 7, 12, 25, 210, 297):
        assert math.isclose(mm * 72 / 25.4 * 25.4 / 72, mm)
    print(f"PASS shifted case: (+12, +9) mm; unit round-trips; {reference_count} acquired artifacts")
    print("Output measurements do not prove SVG import or print/cut alignment without native/settings evidence.")


if __name__ == "__main__":
    verify()

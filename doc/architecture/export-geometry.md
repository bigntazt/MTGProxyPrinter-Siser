# M05: PDF and PNG physical export mapping

Recorded 2026-10-07 (America/Los_Angeles). Accepted M04
`acc43c65ae8c7f940be12358bc561e65f02d1ad5` was fast-forwarded into `siser-dev`
and pushed before creating `milestone/m05-export-mapping`. `trunk` remains
`00202f988bef071c7070967e86c1490cb19114ba`. M05 stops on its pushed milestone
branch for architect review, without merging.

## Four representations

1. `PageGeometry.sheet_width_mm` / `sheet_height_mm` are nominal physical paper.
2. Canonical scene geometry uses the existing RESOLUTION print context, 300 DPI.
   A4 is exactly 210 × 297 mm but has a rounded 2480 × 3508 scene extent.
3. Output resolution D determines device units per canonical unit: D / canonical DPI.
4. Representable boundaries are integral PNG pixels or Qt PDF whole points.

Card layout, capacity, bleed, margins, spacing, centering, corners, and canonical
rounding are unchanged. Exporters do not calculate placements. Public export DPI
remains the existing 300 default. A private PNG field and QPdfWriter resolution
allow internal 600-DPI tests; global RESOLUTION remains 300.

## Scene mode and shared helper

Both exporters construct `ON_PAPER | FILE_EXPORT` scenes without IMPLICIT_MARGINS.
FILE_EXPORT makes x_offset zero regardless of native printer preferences. Paper
colors, transparent scene background, registration classes/style strings, and
label behavior remain. ON_SCREEN retains zero offset. Native ON_PAPER offset and
implicit-margin behavior remain M04 behavior for M06.

`print._render_export_page` requires ready geometry for the actual selected output
page. An empty builder snapshot serves only PDF job paper metadata. Per page,
`distance_to_px(sheet_width_mm * mm)` and the corresponding height define a
floating QRectF at (0, 0). The rectangle is never rounded to scene/device extent.

The painter saves state, uniformly scales by D / RESOLUTION.magnitude, optionally
quarter-turns, and restores in finally. Scene.render receives identical explicit
source and target rectangles and IgnoreAspectRatio. Qt contributes no fitting
scale. LosslessImageRendering remains enabled. Device dimensions, scene ratios,
view zoom, PDF box ratios, and image enhancement do not enter content mapping.

## PNG representation

Dimensions are Python round(sheet_mm × D / 25.4), directly from actual geometry.
Named landscape orientation is already in physical metadata. Custom dimensions
are already oriented and are never transposed again. No printer layout conversion,
borderless setting, native landscape workaround, or PDF rotation setting affects
PNG coordinates.

Device pixel ratio is explicitly 1. X/Y density both equal round(D / 0.0254):
11811 dots/metre at 300 DPI and 23622 at 600. Integral pixels and density entail
a small physical metadata approximation; unequal density is not used to force
paper size. Opaque RGB / transparent RGBA backgrounds, filename numbering, and
concurrent background encoding are preserved.

## PDF paper and rotation

QPdfWriter receives explicit millimetre dimensions through QPageSize Millimeter
and ExactMatch, zero margins, and QPageLayout Portrait applied to already oriented
width/height. This avoids a second orientation swap and interpreting inch quantity
magnitudes as millimetres. Failed setPageLayout raises RuntimeError; returned full
dimensions are checked against the request with a one-point boundary allowance.

Qt 6.11.2 represents PDF boxes in whole points. ExactMatch avoids broad fuzzy
snapping but may identify standard sizes after point conversion. Arbitrary custom
identity and sub-point page-box identity are not promised. No PDF rewriting or
replacement backend is used. Actual generated-file measurements at 300 DPI:

| Paper | Nominal sheet mm | Emitted PDF pt | Represented PDF mm | PNG px |
| --- | --- | --- | --- | --- |
| A4 portrait | 210 × 297 | 595 × 842 | 209.902778 × 297.038889 | 2480 × 3508 |
| A4 landscape, normal | 297 × 210 | 842 × 595 | 297.038889 × 209.902778 | 3508 × 2480 |
| Letter portrait | 215.9 × 279.4 | 612 × 792 | 215.9 × 279.4 | 2550 × 3300 |
| Custom 12 × 8 inches | 304.8 × 203.2 | 864 × 576 | 304.8 × 203.2 | 3600 × 2400 |
| Near-standard custom | 210.6 × 297.6 | 597 × 844 | 210.608333 × 297.744444 | 2487 × 3515 |
| A4 landscape, workaround | 297 × 210 | 595 × 842 | 209.902778 × 297.038889 | 3508 × 2480 |
| Custom landscape, workaround | 304.8 × 203.2 | 576 × 864 | 203.2 × 304.8 | 3600 × 2400 |

The existing PDF landscape setting/default is preserved. Nominal W > H swaps PDF
dimensions and turns the entire content clockwise 90 degrees: x' = H - y, y' = x.
After DPI scaling: translate(source.height(), 0), then rotate(90). Translation
uses floating nominal height, never rounded scene height or quantized PDF width.
No aspect correction is added. Save/restore per page prevents accumulating
rotation. Portrait stays unchanged; custom landscape is detected from physical
dimensions. PNG retains configured orientation. The rotated workaround is not
qualified for Print & Cut; normal coordinates remain the future validation basis.

## Clipping and local failures

The explicit rectangle defines nominal paper. The device additionally clips at
its integral pixel/point boundary. No recentering or shrink absorbs the difference.
Edge-touching content can be clipped by a sub-point PDF boundary discrepancy or
raster pixel edge, separately from interior card-position accuracy.

PDF checks painter startup, ends active painters in finally, and wraps rejected
page rendering with the page number and original reason. Progress advances only
after successful rendering; success logging remains on the success path.
export_pdf closes started progress in finally and propagates RuntimeError.
SavePDFDialog uses MainWindow.on_error_occurred and returns before success logging
or print-count scheduling for a failed export.

PNG ends active painters in finally, releases acquired UI locks, waits for queued
encoders, and closes progress. Expected errors remain observable through
error_occurred; completion does not erase them. Failed renders are not queued or
reported as successful pages. Earlier successful files remain: no atomic export,
rollback, or asynchronous job-framework redesign is introduced.

## Numeric validation

The independent zero-margin/spacing A4 first regular trim is canonical
(122.5, 194, 745, 1040). Actual PDF measurements at both resolutions:

| DPI | x mm | y mm | width mm | height mm |
| --- | --- | --- | --- | --- |
| 300 | 10.3716666667 | 16.4253333333 | 63.0766666667 | 88.0533333333 |
| 600 | 10.3716666667 | 16.4253333333 | 63.0766666667 | 88.0533333333 |

pypdf resolves Qt image Do-operation CTMs, with PDF bottom-left converted to
top-left millimetres. The helper is limited to synthetic image PDFs, not a generic
parser. Content assertions allow 0.01 mm; these measured values coincide to
floating precision. Whole-point page boundaries are asserted separately. Reloaded
PNG bounds match scaled canonical trims within one pixel at both resolutions.
Density is checked independently with its integral representation.

Synthetic cards use local filled pixmaps, sharp corners, zero bleed, and explicit
paper settings. No Scryfall/network fixture, stored preference, OS locale, schema,
or default change is involved. pypdf>=6,<7 is declared only in the tests group,
not runtime dependencies or the application bundle. Critical tests import it
directly, without optional skips.

Coverage: six paper/unit/orientation cases; near-standard paper; 300/600 physical
invariance; named/custom landscape rotation with asymmetric anchors and multiple
pages; regular/oversized/partial/empty targets while UI stays elsewhere; printer
preference isolation/native offset retention; alpha; one/two-digit PNG numbering;
PDF splitting/page counts/order/no trailing blank; helper state restoration;
rejected geometry/rendering; PDF startup/paper failures; PNG painter/lock cleanup,
completed earlier encoding, failed-page suppression; PDF progress/dialog failure;
edge clipping; and guide output.

Tools: Windows CPython 3.13.14, PySide6/Qt 6.11.2, Pint 0.24.4, pytest 9.1.1,
pypdf 6.19.0, Poppler pdfinfo/pdftoppm 26.07.0. Install the declared tests group in
the documented development environment. Run from repository root; in-memory A4
normalization is for older fixtures:

```powershell
$env:PATH = "$PWD\venv\Scripts;$env:PATH"
@'
import mtg_proxy_printer.settings as s
s.DEFAULT_SETTINGS['documents']['paper-size'] = 'A4'
s.settings.read_dict(s.DEFAULT_SETTINGS)
import pytest
raise SystemExit(pytest.main([
    'tests/model/test_page_geometry.py',
    'tests/page_scene/test_page_geometry_adoption.py',
    'tests/test_pdf_export.py',
    'tests/test_print.py',
    'tests/test_export_mapping.py',
    '-q', '--timeout=30', '--tb=short',
]))
'@ | & .\venv\Scripts\python.exe
```

Final combined result: **71 passed in 18.61 seconds** (20 geometry, 21 adoption
including FILE_EXPORT, 2 PDF smoke, 6 print/UI fixtures, 22 focused export cases).
Earlier 67-case combined run also passed before final coverage additions.
No full-suite or network loader rerun was needed.

## Visual/manual evidence and follow-ups

Poppler independently rendered Letter portrait, A4 landscape, 12 × 8-inch custom,
oversized/partial pages, rotated PDF, and guides. Those renders and actual
Qt-written Letter/landscape/custom/oversized PNGs were inspected in the local
image viewer. Orientation, occupancy, guides, and in-page content matched numeric
results. Adjacent zero-spacing filled cards form continuous color regions;
numeric image-operation counts distinguish them. Artifacts remain ignored/local
under `.m01-output/m05-review`; fixtures are in `m05-validated`, `m05-extra`, and
`m05-final` alongside it.

Native interactive export was not performed: native controls are disabled in the
enabled UI automation surface; the older native inventory API was not exposed.
Offscreen tests/image review do not establish interactive behavior. A brief native
export remains an optional architect/user check. No physical print/cut validation
is required or claimed.

Pre-existing follow-ups, deliberately not implemented:

- PNG retains its existing worker/scene threading pattern; PDF event processing
  can permit reentrancy. No general async infrastructure or job-state redesign.
- SavePNGDialog schedules print-count updates separately from export success.
  Broader count timing and encoder-save result accounting remain follow-ups.
- Configured guide width is converted to point magnitude and supplied to a
  scene-unit QPen, so physical guide width does not equal its configured length.
  M05 preserves that existing style; guide-width units need a separate correction.

M06 owns native mapping, create_printer, to_page_layout, offsets/implicit margins,
and native landscape behavior; these paths are unchanged. SVG, Siser registration,
profiles, Leonardo numeric placement, and M08 remain pending. No reference
acquisition/reinterpretation or hardware qualification is included. User incoming
references and unrelated Memtrace state are preserved.

## M06 adoption

Accepted M05 was subsequently integrated into siser-dev. Its export wrapper now
delegates to the narrowly generalized _render_page with equal DPI and zero native
origin/correction, preserving all measured PDF/PNG invariants. Active native
scenes use NATIVE_PRINT and the accepted printer mapping described in
[native-print-geometry.md](native-print-geometry.md). Legacy bare ON_PAPER and
IMPLICIT_MARGINS still retain their earlier behavior. M07 is not started;
Leonardo numeric placement and M08 remain pending.

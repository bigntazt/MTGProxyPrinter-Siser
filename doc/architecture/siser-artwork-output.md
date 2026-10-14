# M15 — Ordinary Siser artwork output

## Integration and scope

Accepted M14 `c17435e2ac164a91e69b7b59e1a5199be54908d9` was fast-forwarded
into `siser-dev` and pushed before creating `milestone/m15-siser-artwork-output`.
M15 removes the historical calibration-only prohibition and enables ordinary
PDF, PNG, native printing and native preview through their existing routes.
Calibration and standalone SVG remain available. M14's original handoff records
what was available then; this document describes the new software behavior.

The stored identity remains `Siser`. Reference resolution and graphics are unchanged:

| Nominal sheet (mm) | Resolved frame (mm) |
|---|---|
| Letter portrait 215.9 × 279.4 | 215.9 × 279.4 |
| A4 landscape 297 × 210 | 296.96832909999995 × 209.97333756666666 |

Equivalent custom nominal dimensions use those entries; other configurations do
not gain support. Identification remains absolute 1e-6 mm, relative zero. The A4
entry is the observed Leonardo 1.1.31 frame, not a universal A4 correction.
Eight filled no-pen bars retain their accepted geometry and order.

## Readiness and fidelity

The shared policy now accepts explicit style and geometry and resolves Siser's
frame. Unsupported dimensions become actionable RuntimeErrors. Empty builder
snapshots validate sheet/profile compatibility before devices or work scheduling;
actual selected-page snapshots govern each render. No global printer state or
clearance assessment is consulted.

`PageScene.require_output_ready()` retains geometry recovery, then rejects a
remaining registration-unavailable reason. Editable card geometry remains valid
on an unsupported frame; changing to a supported sheet restores output. Both
explicit scene rendering and ordinary physical-output wrappers require readiness.
The low-level static calibration mapping is unchanged.

PDF validates before constructing QPdfWriter and again at execution. Actual nominal
sheet dimensions must match those captured for paper setup within 1e-6 mm; otherwise
restart the export. This is distinct from Qt's whole-point page-box quantization.
Per-page selection, splitting, numbering, progress and painter cleanup remain.
PNG checks before locking/encoders and at each page, retains per-page sizing,
density, background/alpha, numbering, lock release and encoder completion on failure.
Failed renders are not queued. Native checks precede painting and each page;
accepted printer clipping, paper validation and cleanup remain.

There is no mandatory clearance enforcement. `assess_siser_clearance()` remains
available as M13's unchanged API but is not an output prerequisite. Card/bleed,
guide, title, page-number and watermark composition uses ordinary PageScene layers.
No marks are moved above artwork and no exclusion patch is added. Overlap can
obscure marks; page/printer clipping is preserved without fitting or reflow.
Siser's white preview background is not forced into file output.

The existing PDF clockwise rotation, native landscape rotation and configured
native horizontal correction apply once to the whole scene, including marks.
Native correction is a local printer preference, not Siser geometry. File output
has zero native correction. Nominal SVG coordinates remain independent of output
preferences. Artwork corner presentation remains separate from M12's rounded
regular 2.5 mm cut contours and square oversized contours.

## UI and remaining lifecycle limits

All four Siser ordinary-output actions validate before acquisition and bypass
export-time compaction; explicit separate compaction remains available. Other
profiles retain the existing prompt. Missing artwork acquisition remains, while
loading/retained-dialog protection prevents replacement of an active dialog.
Acceptance/execution rechecks support. Export preparation adds no document edit,
undo action or current-page selection change.

Synchronous PNG rejection schedules neither renderer nor count task. PDF failure
avoids its success/count path. Native counts follow successful rendering; preview
never counts. This does not make asynchronous PNG accounting transactional:
existing encoder-save accounting, background print-count timing, output-job
threading/reentrancy and mutable job state remain follow-ups. Configured guide
width interpretation is an existing issue, unchanged here. No general job or
safe-save redesign was performed.

## Focused validation

Windows child process: CPython 3.13.14, PySide6/Qt 6.11.2, pytest 9.1.1,
pypdf 6.19.0, Pint 0.24.4, Poppler 26.07.0. Child-only `QT_QPA_PLATFORM=windows`
and venv Scripts PATH; no package, font, persistent setting or locale change.

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests/test_siser_artwork_output.py `
  tests/test_siser_registration.py::test_direct_output_guards_and_png_lifecycle `
  tests/test_siser_registration.py::test_operation_time_guards_and_existing_pdf_success `
  tests/test_siser_registration.py::test_ui_guards_and_calibration_svg_access `
  tests/test_siser_registration.py::test_png_dialog_rechecks_before_tasks_and_print_count `
  tests/test_export_mapping.py::test_preferences_alpha_and_split `
  tests/test_native_print_mapping.py::test_print_and_preview_reporting_recovery_and_count `
  -q --timeout=30 --tb=short
```

Result: **25 passed in 10.58 seconds** (18 new parameterized cases, four retargeted
M14 cases, two existing alpha/split cases and one native dialog case).
`git diff --check` passed. No passing application tests were repeated after docs.

Actual pypdf measurements resolve emitted transforms and distinguish opaque black
fills from colored images, bleed, transparent fills and text. All eight marks
pass 0.001 mm absolute, relative-zero comparison; image trim bounds and corresponding
SVG outer bounds pass 0.01 mm. Regular, oversized, partial and empty pages use
actual snapshots without changing UI selection. Rotated multipage output does not
accumulate transforms. Native PdfFormat cases cover Letter, A4 landscape, Standard
margins and A4 rotation with 2 mm configured correction. Standard origin residual
is independently accounted from accepted paint rectangles, without new compensation.

Additional disposable measurements from ordinary output:

| Output | DPI | Max bar difference (mm) | Max image bound difference (mm) | PDF box (pt) |
|---|---:|---:|---:|---|
| Letter | 300 | 0.000000546667 | 0.0000000000000213 | 612 × 792 |
| A4 landscape | 300 | 0.000000546667 | 0.0000000000000213 | 842 × 595 |
| A4 landscape | 600 | 0.000000123333 | 0.0000000000000213 | 842 × 595 |

A4 content retains physical scale across resolutions; nominal A4, reference frame
and emitted box remain different representations. These numerical tolerances are
software representation checks, not physical accuracy specifications.

PNG Letter/A4 dimensions are 2550 × 3300 / 3508 × 2480 at 300 DPI, density 11811
on both axes, DPR 1; measured simple raster edges are within one pixel. Transparent
and colored backgrounds remain configured. Nine-card Letter with 1.5 mm bleed,
blue guides and white watermarks exports while a patched assessment would raise
if called. PDF contains artwork plus per-edge bleed and text; PNG shows guide ink
over a mark and opaque red bleed obscuring another portion. Geometry is unchanged.
Unsupported frames reject before writer/startup/jobs; failed PNG pages release the
lock and report failure without encoding. Changed nominal PDF paper requires restart.

## Visual review and qualification

Ignored `.m01-output/m15-review/` holds generation script, measurements, ordinary
Letter/A4 PDFs, spaced/bleed and crowded proofs, transparent/colored PNGs, software
native PDF and selector capture. Independent Poppler full-page renders and PNGs
were inspected: black bars, correct orientation, ordinary artwork, guides,
watermarks/title/page number, preserved overlap and normal clipping. The spaced
proof uses four red regular cards, 2 mm row/column spacing and 1 mm bleed; crowded
proof uses nine red cards, 1.5 mm bleed and 0.3 mm configured blue guides. Synthetic
image-bound proofs deliberately use sharp artwork presentation; cut contours are
verified independently against unchanged SVG outer bounds. M12/calibration corner
matrices were not rerun or changed.

Programmatic Windows selector capture and tooltip text were reviewed. Unsupported
frame error/recovery and dialog flows were exercised by tests. Interactive native
printer/device controls were unavailable; programmatic widgets, PdfFormat and file
inspection are not physical or interactive printer validation.

No hardware job, acquisition, M08B repetition, clearance matrix, full scene/UI suite,
network loader or unrelated suite ran. Historical evidence, layout/formulas,
calibration, SVG, contours, schemas and dependencies are unchanged. M08B acceptance
remains scoped to its tested job. Optical recognition, equipment equivalence,
rounded physical cuts, Romeo and transformed Leonardo workflows remain unqualified.
No hardware-derived correction or automatic adjustment was introduced. M15 remains
unmerged for architect review; subsequent milestones were not started.

## M16 subsequent operator workflow evidence

The M15 software checks and qualification limits above are its historical handoff.
M16 records later operator-reported successful Letter nine-card and complete A4
landscape-to-portrait-feed trials, with the exact counterclockwise A4 sequence and
saved contour-only LDS preview. See [M16 evidence and scope](siser-reference.md#m16--tested-artwork-workflows-and-offline-guide-2026-10-14)
and the canonical [Siser Print & Cut Guide](../../resources/help/siser-print-cut.md).
No printing, registration, calibration or layout behavior changes accompany that
record. The trials are visual operator evidence, not numerical accuracy or universal
optical/equipment qualification; borderless and automatic rotation remain deferred.

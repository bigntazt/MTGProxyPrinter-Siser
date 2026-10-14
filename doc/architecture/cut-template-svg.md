# M09: generic single-page SVG cut templates

Recorded 2026-10-08. Corrected M08A
`f1ab8f720f8b904e27beb8949b952258dc84dfec` was pushed on
`milestone/m08a-leonardo-placement`, fast-forwarded into `siser-dev`, and pushed
before creating `milestone/m09-svg-engine`. M09 was left unmerged for architect
review at that handoff; its subsequent acceptance/integration is recorded below.
`trunk` remains `00202f988bef071c7070967e86c1490cb19114ba`.

## Callable API

`mtg_proxy_printer.cut_template_svg.serialize_cut_template_svg(geometry:
PageGeometry) -> str` returns a complete SVG document. The serializer uses only
standard-library XML generation and numeric formatting at runtime; it does no
I/O, settings lookup, snapshot construction, or GUI/output-device work. It
requires no QApplication, document, scene, painter, or printer. Caller code owns
snapshot construction, page selection, and saving UTF-8:

```python
from pathlib import Path
from mtg_proxy_printer.cut_template_svg import serialize_cut_template_svg
from mtg_proxy_printer.model.page_geometry import build_page_geometry
from mtg_proxy_printer.model.page_layout import PageLayoutSettings
from mtg_proxy_printer.units_and_sizes import PageType

layout = PageLayoutSettings(paper_size="Letter", paper_orientation="Portrait")
geometry = build_page_geometry(layout, PageType.REGULAR, 9)
Path("letter-trims.svg").write_text(
    serialize_cut_template_svg(geometry), encoding="utf-8"
)
```

No existing production module, dependency, UI action, setting, or document field
changes in M09. Its only application addition is this callable engine.

## Coordinates and contours

The SVG 1.1 root uses `http://www.w3.org/2000/svg`, explicit `width`/`height` in
millimetres, and `viewBox="0 0 W H"` with identical numerical dimensions. One user
unit is one millimetre. The origin is the sheet's top-left, X increases rightward,
and Y increases downward. Already-oriented `sheet_width_mm`/`sheet_height_mm`
are used directly, including custom dimensions. Neither rounded scene extents
nor PDF page boxes define the SVG sheet.

Iterating occupied `geometry.placements` in supplied order emits one path with
ID `card-{slot_index}` for each `placement.trim_mm`. The historical M09 square
paths had absolute commands
`M x y L right y L right bottom L x bottom Z`, `fill="none"`, `stroke="black"`,
and `stroke-width="0.1"`. Right and bottom are computed before formatting. There
are no group transforms, artwork, bleed envelopes, guides, marks, labels,
backgrounds, or page borders. The path centreline defines trim; its 0.1 mm display
stroke is neither kerf compensation nor a cutting parameter.

Partial pages retain full-grid slot positions without filling empty slots or
recentering. Oversized cards retain their supplied trims. Positive-size empty
snapshots, including zero-capacity sheets, produce a root with no paths. Grid-edge
collections are not consulted because they can include empty slots.

M12 now selects a nominal 2.5 mm radius for regular trim contours; oversized
contours remain square. The shared definition is described below. Artwork
transparency and `draw_sharp_corners` do not select the cutting radius.
Separate closed paths remain separate even with zero or tiny spacing
and coincident edges. Cutting these edges independently may repeat a cut;
common-line optimization is deferred.

## Formatting and validation

One private formatting policy uses six fixed decimal places in millimetres,
strips trailing zeroes/the trailing decimal point, normalizes negative zero to
`0`, and never emits scientific notation. Decimal points are locale independent.
Formatting occurs only at serialization, without new layout rounding or DPI
conversion. Repeated output is deterministic and the frozen snapshot is unchanged.

Nonfinite serialized coordinates/dimensions (including derived right/bottom)
raise `ValueError`. Sheet and trim dimensions must be positive and remain
nonzero at six-decimal precision. Supported-page, occupancy, and slot invariants
belong to the existing builder, rather than another validation framework.

Coordinates are preserved without clamping, scaling, recentering, or dropping
paths. A supported near-edge layout can extend slightly beyond nominal paper
because canonical margins and sheet coordinates were rounded independently.
The serializer writes that geometry. Viewer/importer clipping outside the SVG
viewport is not promised.

## Nominal geometry and workflow limits

The SVG represents nominal page geometry. Native printer corrections/offsets and
the PDF landscape compatibility rotation are not applied. Matching such
transformed output needs a later explicit output-adapter decision. The normal,
unrotated PDF comparison below checks content coordinates, independently of Qt's
whole-point page boundary. A4 SVG remains exactly 210 × 297 mm; its comparison
PDF has a 595 × 842 pt box (209.902778 × 297.038889 mm).

M08A demonstrated numeric placement using Artwork Only with retained printing
artwork and duplicate cut paths. A contours-only SVG can have different occupied
bounds and import behavior. M09 has no new Leonardo acquisition or native
interaction, and is not qualified for independent printed-page alignment or
Siser cutting. Production M08, external-mark/camera semantics, active cutter
qualification, physical print scale/skew, alignment/repeatability, and contours-only
import remain gated. No physical printer/cutter operation or M10 implementation
was started.

## Focused validation

Existing Windows environment: CPython 3.13.14, PySide6/Qt 6.11.2, Pint 0.24.4,
pytest 9.1.1, pypdf 6.19.0. No dependency installation or saved preference/default/
locale changes. From the repository root, bootstrap executable lookup only:

```powershell
$env:PATH = "$PWD\venv\Scripts;$env:PATH"
.\venv\Scripts\python.exe -m pytest tests/test_cut_template_svg.py -q --timeout=30 --tb=short
```

Final result: **38 passed in 1.24 seconds**. Coverage includes parsed namespace,
physical dimensions/viewBox, explicit commands/closure, IDs/order/presentation,
absence of other objects, Letter nine cards, A4 landscape eight cards, custom
inch dimensions without another orientation swap, asymmetric margins/fractional
spacing, partial/oversized/empty/zero-capacity pages, coincident edges, actual
rounding-induced overhang, bleed independence, square corners, determinism and
snapshot preservation. It also checks numeric rejection, derived overflow,
precision-zero dimensions, negative zero, endpoints computed before formatting,
and serialization in a subprocess without QApplication or document/scene imports.

The independent A4 first trim reference is
`(10.3716666667, 16.4253333333, 63.0766666667, 88.0533333333)` mm, asserted with
absolute tolerance 0.000001 mm and relative tolerance zero. One explicit A4,
zero-bleed, sharp-corner, local-image PDF at 600 DPI with workaround disabled
was actually written and read with pypdf. Reused synthetic helpers from
`tests/test_export_mapping.py` resolve its image placement into top-left mm;
parsed SVG path bounds agree within 0.01 mm. Page-box quantization is asserted
separately. Only the new module was selected, not the imported helpers' suite.
Unrelated application suites, earlier milestone matrices, and network loaders
were skipped. `git diff --check` passed.

Qt QSvgRenderer 6.11.2 accepted and rendered Letter portrait (nine paths, 3 × 3)
and A4 landscape (eight paths, 4 × 2). Both raster reviews were inspected in the
local image viewer: orientation, placement, contour counts and absence of extra
objects matched expectations. Coincident edges appear as shared grid lines;
XML counts establish independent closed paths. Disposable SVG/PNG examples are
ignored under `.m01-output/m09-review`. This software review is supplementary to
numeric measurements and is not native Leonardo or hardware validation.

The M08A prerequisite was checked separately with its documented bundled CPython
3.12.14, pypdf 6.10.0 and pdfplumber 0.11.9:

```powershell
& 'C:\Users\mitch\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tests/data/print_cut/siser/measure_numeric_placement_outputs.py --check
.\venv\Scripts\python.exe tests/data/print_cut/siser/verify_fixtures.py
git diff --check
```

All passed. Removing explicit-box lists from old/regenerated JSON yielded exact
equality; all six original lists now correctly contain only `/MediaBox`.
The reported `.984252` inch conversion is 25.0000008 mm. Original acquired files,
literal entries, effective boxes, measured geometry and the unexplained X-entry
difference were preserved. No application pytest was run for those corrections.

## M10: current-page export UI (2026-10-08)

The architect accepted M09 at `a248c483890f404cff592f24a28e244591ec6f66`.
That exact commit was fast-forwarded into `siser-dev` and pushed before creating
`milestone/m10-svg-export-ui`. M10 is pushed and left unmerged for review.
Local/remote `trunk` remain `00202f988bef071c7070967e86c1490cb19114ba`.

Use **File → Export → Export current page cut template (SVG)…**. The new
`action_export_cut_template` uses the existing export icon and Qt slot
auto-connection, without an extra manual connection, shortcut, or toolbar button.
Its tooltip now explains rounded regular and square oversized outlines at document paper size and the exclusion of
registration marks/printer corrections. The existing loading lock disables it;
the slot also returns when that lock is active or another dialog is retained.

Invocation synchronously obtains the selected model page, one-based page number,
and source document path, builds the shared snapshot using `Page.page_type()` and
`len(page)`, and serializes/encodes UTF-8 before opening the destination dialog.
`SaveCutTemplateDialog` retains only prepared bytes and captured naming metadata,
with no document reference for later rereading. Selection, layout, and document
path changes after invocation cannot change that prepared export. The next
invocation captures the new state. Expected preparation `ValueError` failures use
the existing main-window error display before any save dialog is opened.

Regular, oversized and partial pages keep the accepted serializer behavior.
Valid empty pages produce the empty physical-sheet SVG. Explicit placeholders
remain occupied slots even without artwork; no image-availability filter is used.
There is no compaction prompt, document action/mutation, missing-image acquisition,
background task, print-count update, current-page change, or save-status/history
change. Settings, document format, serializer, geometry, PDF/PNG/native rendering,
and dependencies are unchanged.

The asynchronous file dialog uses AcceptSave/AnyFile, the translated SVG filter,
default suffix `svg`, and the existing `export-path` initial directory. Normal
overwrite confirmation remains enabled in production. Suggestions always include
the extension: `Deck-page-2-cut.svg`, `Deck.v2-page-2-cut.svg`, or `page-1-cut.svg`
for an unsaved document. Its title identifies the captured page. The existing
`current_dialog`/`finished` lifecycle releases the reference on Save or Cancel.

Saving uses QSaveFile with direct-write fallback disabled and binary WriteOnly.
Only a complete byte count permits commit. Open, short-write and commit failures
cancel incomplete writing and release the temporary-file owner before reporting
an error containing destination and underlying reason. Success is logged only
after commit. Existing output is preserved on unsuccessful saves, and cancellation
does no file I/O or error reporting. No general export service or worker was added.

### M10 focused validation

Windows CPython 3.13.14, PySide6/Qt 6.11.2, pytest 9.1.1, pytest-qt 4.5.0,
pytest-timeout 2.4.0 and Pint 0.24.4; the existing environment was reused.
Authoritative UI edits are in `resources/ui/main_window.ui`. Actual QAction
validation loaded the changed source successfully; no stale generated module
blocked it, so no regeneration or packaging cycle was needed. Generated UI and
translation catalogs were neither edited nor committed.

```powershell
$env:PATH = "$PWD\venv\Scripts;$env:PATH"
.\venv\Scripts\python.exe -m pytest tests/ui/test_cut_template_export.py -q --timeout=30 --tb=short
git diff --check
```

The final focused run passed **17 tests in 6.47 seconds**. It covers one controlled real main-window
arrangement with QAction menu order, tooltip, auto-connection opening exactly one
dialog, retained-dialog protection/cleanup, and real loading-lock disable/release.
Light document/dialog cases cover later partial regular and oversized pages,
placeholder occupancy, capture despite later selection/layout/path changes,
subsequent recapture, valid empty output, exact UTF-8 bytes, Unicode destinations,
real replacement of existing output, cancellation, and open/short/commit failure
injections with destination preservation. Naming, dialog options, export-path
lookup, expected preparation failures, unchanged document/history/layout/path,
and absence of unrelated export side effects are checked. Test-only non-native
dialogs and overwrite-confirmation bypasses do not change production options.

Only this module was selected. M09 geometry/formatting matrices, PDF coordinate
tests, M08A acquisition verification, and unrelated application/database/network/
cache/serialization suites were skipped. No default, saved preference, OS locale,
or dependency changes were made. Diff checks passed.

Offscreen Qt controls confirmed menu text/tooltip and captured page-2 title and
`Deck.v2-page-2-cut.svg` suggestion. Visible non-native Qt widget Save/Cancel were
driven locally, with exact saved bytes and no Cancel write. The saved four-card
A4 partial-page SVG was rendered with QSvgRenderer and visually inspected: the
full-grid positions and empty remaining slots appeared correctly, without extra
objects. Review artifacts remain ignored under `.m01-output/m10-review`. Menu
and dialog screenshots were inspected but have the previously documented
missing-font boxes, limiting text appearance review; control properties establish
the strings. The controlled main-window visual arrangement used an inert preview
scene. Native interactive controls remain unavailable/unverified; these checks
do not establish native desktop appearance or Leonardo behavior.

Fixture setup exposed a pre-existing `Document.get_empty_card_for_size()`
constructor mismatch (an extra positional argument to `Card`). It is outside M10
and was not repaired. Tests construct equivalent valid local placeholders with
the existing `create_card` helper; exporting occupied placeholders works. This
follow-up affects the application's add-empty-card path, rather than SVG saving.

The template remains generic nominal document geometry, excluding registration
marks, native printer corrections and PDF compatibility rotation. M08A Artwork
Only findings do not qualify contours-only alignment. Production M08, Leonardo
contours-only behavior and physical qualification remain pending. Calibration
implementation, paired print/cut orchestration, and subsequent milestones were
not started. No new Leonardo evidence or physical printer/cutter operation was
requested or performed.

### M10 acceptance and integration (2026-10-08)

The architect accepted M10 at `57aa3f0d875a8354b217bffa5b9593a870a5d078`
without corrective work or another acceptance test run. That commit was
fast-forwarded into `siser-dev` and pushed before M11. M10's preceding unmerged
statement records its historical review handoff. M11 narrowly shares the
prepared-byte QSaveFile helper while preserving the SVG dialog contract;
see [calibration-sheet.md](calibration-sheet.md). No SVG geometry, serializer,
document schema or cutter qualification changed.

## M12 — Shared rounded trim contours

Starting integration: `e77722c995c24cba7d97c56811b225c81d390c64`, including
accepted M08B software and operator-reported physical comparison. M12 is on
`milestone/m12-rounded-corners`, left unmerged for architect review. Local/remote
trunk remain `00202f988bef071c7070967e86c1490cb19114ba`.

`mtg_proxy_printer.trim_contour.trim_contour()` owns the project radius policy
and effective radius: regular cards use min(2.5 mm, width/2, height/2); oversized
cards remain square. The selected 2.5 mm is a project contour specification, not
an independent measurement of every manufactured MTG card. Current regular trims
are approximately 63.076667 × 88.053333 mm, so the clamp does not occur in supported
layouts. Synthetic 3 × 8, 8 × 2 and 5 × 5 mm bounds exercise it explicitly.

PageGeometry trim rectangles retain their existing meaning as outer bounds.
The helper adds corner shape without changing layout, dimensions, placement,
spacing, bleed, page size or registration marks. Its immutable contour stores the
mm bounds and effective radius; SVG commands and the Qt painter path consume
that definition. SVG still works without QApplication or settings access: the
helper imports Qt lazily only for painter-path construction. There is no persisted
layout field, document migration, public radius control, artwork-sharp-corners
coupling, printer/cutter preference or hardware-derived correction.

Regular SVG paths now use M/L plus four circular A commands with equal radius
2.5, zero rotation, small arc and clockwise sweep, then Z. Tangents are inset
2.5 mm from each bound corner. Oversized paths retain M/L/L/L/Z. IDs, order,
six-decimal mm formatting, page metadata, independent closed paths, empty/partial
behavior and nominal overhang remain intact. Common-line optimization is not
introduced. Calibration uses the same contour helper via QPainterPath; Qt emits
four cubic quarter curves for regular outlines, retaining tangent locations and
outer bounds. This cubic circle representation is an approximation between
exact tangents, not byte-for-byte or mathematically exact SVG/PDF arc identity.
The existing physical mapping, pen, labels, crosses and 50 mm rulers are unchanged.

The SVG action tooltip now states rounded regular and square oversized outlines.
Only that resource string and its directly affected assertion changed; action
identity/menu position, filenames, capture-at-invocation and safe saving remain
unchanged. Development UI loading uses the resource dynamically; the affected
assertion passed without generated UI regeneration or hand editing.

### Focused verification

Existing Python 3.13.14, PySide6/Qt 6.11.2, pypdf 6.19.0, pytest 9.1.1 and
Poppler 26.07.0. One process-scoped Windows-platform test run used a copied
environment and the existing venv Scripts on PATH; no persistent settings changed.
The exact test selection was:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_cut_template_svg.py tests/test_calibration_pdf.py tests/ui/test_cut_template_export.py::test_real_action_autoconnection_menu_and_loading_lock -q
```

Result: **66 passed in 14.40 seconds**. New checks inspect all four SVG arcs,
requested radii and tangent positions, deterministic output and unchanged bounds.
Actual generated PDFs at 300/600 DPI must contain four cubic quarters per regular
contour, with start/end tangents agreeing with independently parsed SVG arcs
within 0.01 mm and quarter-circle control points within 0.001 mm. A square,
mirrored curve or incorrect tangent/control arrangement fails these checks.
Oversized PDFs/SVGs contain no curved corners. The existing ruler, label,
registration, cleanup and SVG independence cases pass in the selected modules.
No full UI suite, M08B experiment, acquisition checks or unrelated application
matrices were run. Application tests were not repeated after documentation edits.

Separate ignored outputs are in
`D:\Code\MTGProxyPrinter-Siser\.m01-output\m12-review\`: Letter regular SVG/PDF
at 300 and 600 DPI, custom 12 × 8 inch oversized square SVG/PDF, measurements and
independent Poppler full-page/corner renders. Historical M08B PDF/LDS/package,
reference data/hashes and square calibration/SVG proofs were neither modified
nor regenerated.

| Actual review output | Page box (pt) | First outer bounds (x,y,w,h mm) | Cubic quarters per card |
|---|---|---|---:|
| Letter regular, 300 DPI | 612 × 792 | 13.335, 7.620, 63.076667, 88.053333 | 4 |
| Letter regular, 600 DPI | 612 × 792 | 13.335, 7.620, 63.076667, 88.053333 | 4 |
| Custom oversized, 300 DPI | 864 × 576 | 20.320, 38.523333, 88.053333, 126.153333 | 0 |

The Letter rulers measured 49.999999991333326 mm horizontal and
49.99999999133332 mm vertical at both resolutions. Full measurements and SHA-256
values are retained in `measurements.json`. Letter SVG bytes are identical across
the two PDF resolutions. Numeric file checks supplement visual inspection, not
screenshot measurements.

Independent visual commands:

```powershell
pdftoppm -r 180 -png -singlefile .m01-output/m12-review/letter-rounded-300.pdf .m01-output/m12-review/letter-full
pdftoppm -r 600 -x 290 -y 150 -W 250 -H 250 -png -singlefile .m01-output/m12-review/letter-rounded-600.pdf .m01-output/m12-review/letter-corner
pdftoppm -r 150 -png -singlefile .m01-output/m12-review/custom-oversized-square.pdf .m01-output/m12-review/oversized-full
git diff --check
```

Visual inspection confirmed nine rounded regular outlines, a smooth first-corner
curve, readable unchanged labels/rulers/crosses, and two square oversized outlines.
No interactive native export, Leonardo import or physical rounded-corner cut was
performed. Software verification does not qualify those later trials or a
production Siser profile. M12 stops at the pushed review handoff.

### M12 acceptance and integration for M13

The architect accepted M12 at `d5a49fa2cba3d246d445b210c6ca88d982517042`.
It was fast-forwarded into `siser-dev` and pushed before M13, without repeating
accepted M12 tests. The preceding unmerged statement is the historical M12
review handoff. M13's explicit-frame calculation and clearance diagnostics do
not alter SVG export or the contour policy; see [siser-page-marks.md](siser-page-marks.md).

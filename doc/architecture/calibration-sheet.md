# M11: current-page calibration PDF

Recorded 2026-10-08. Accepted M10
`57aa3f0d875a8354b217bffa5b9593a870a5d078` was fast-forwarded into `siser-dev`
and pushed before creating `milestone/m11-calibration-pdf`. M11 is pushed and
left unmerged for architect review. Local/remote `trunk` remain
`00202f988bef071c7070967e86c1490cb19114ba`.

## API and static rendering

```python
def render_calibration_pdf(
    geometry: PageGeometry,
    registration_style: str,
    *,
    output_dpi: int | None = None,
) -> bytes:
    ...
```

`mtg_proxy_printer.calibration` requires an existing QApplication and its GUI
thread. It accepts an immutable snapshot and registration-style string, uses the
canonical RESOLUTION by default (300 DPI), and returns a complete one-page PDF
as immutable bytes. Explicit positive integer DPI is for internal validation;
there is no new user-facing resolution option. The renderer consults no live
document/preferences, processes no application events, creates no background
task, and performs no filesystem I/O. Invalid resolution and unusable/nonfinite/
nonpositive paper dimensions fail before drawing.

The private builder creates a fresh QGraphicsScene using `trim_px` for drawing
and `trim_mm` for measurement labels. It does not instantiate PageScene, CardItem,
a temporary document, placeholder cards, or card artwork. Partial-page positions,
oversized dimensions, occupied-slot order and coincident contours are preserved.
The existing geometry model and SVG serializer remain unchanged.

One unfilled square rectangle per placement uses a black noncosmetic 0.1 mm pen.
Its centreline agrees with the M09 SVG. Each centre cross has two perpendicular
5 mm segments (2.5 mm each side of the centre). Five labels, inset 2 mm from
top-left with 3 mm pitch, show slot index plus one and actual X/Y/W/H in mm to
six decimal places. Drawing values are not changed by label formatting. A regular
card remains approximately 63.076667 × 88.053333 mm. The ordinary sans-serif font
has a 25 canonical-pixel size, representing 6 printed points at 300 DPI, rather
than a screen point-size assumption. Text is black and contained inside the trim.

The first occupied trim holds the L-shaped references. With intentional mm
distances converted through `distance_to_px` without integer rounding:

```text
x0 = first.x + 6 mm
y0 = first.bottom - 8 mm
d  = 50 mm
horizontal: (x0, y0) to (x0 + d, y0)
vertical:   (x0, y0 - d) to (x0, y0)
```

Both have flat caps, short perpendicular endpoint ticks (1 mm either side), and
an in-card “50 mm” label. Measure line centrelines/tick centres, rather than pen
outer edges. The 50 mm length retains fractional canonical pixels. The vertical
label rotates within the card; the page does not rotate. Empty snapshots have no
cards, labels, crosses or references; registration-disabled output is blank.
Printer-scale checks require a populated page.

## Registration and shared output mapping

`get_registration_profile` resolves the captured string. Fresh roots are added
once to the static scene and placed with `legacy_x_offset_px=0`. The roots and
scene remain alive until painting finishes. There is no parent/stacking traversal
or new ownership framework. Disabled (`None`), Bullseye (`Bullseye`) and Silhouette
(`Cut marker`) retain their original item geometry, resources, transforms, Z
values and resolver behavior. Existing overlaps/clipping remain visible; marks
and cards are not moved to conceal them. A Bullseye resource can be represented
by Qt as an image; this is not card artwork or a new registration implementation.

The arithmetic and painter operations formerly inside `print._render_page` now
live in `_render_graphics_scene(scene, geometry, painter, dpi_x, dpi_y, ...)`.
The original wrapper retains its signature and calls `require_geometry_ready`
before delegating with actual `scene.geometry`. Source-rectangle construction,
save, native clipping, DPI scaling, origin/offset translation, optional rotation,
explicit identical source/target rendering and finally restore retain their
original order. Existing PDF/PNG/native callers use that wrapper.

Calibration uses equal DPI, no native paint rectangle, zero correction and no
compatibility rotation. Nominal millimetre paper dimensions are already oriented.
QPdfWriter receives QPageSize Millimeter/ExactMatch, Portrait applied to those
width/height values, and zero margins. Paper acceptance and painter startup are
checked. QBuffer backs the writer; painting and writer finalization complete
before bytes are read while the buffer is alive. Failure cleanup ends the painter,
releases writer/scene/buffer resources and preserves a primary failure if cleanup
also raises. No destination is touched during preparation.

Qt's whole-point PDF page boundary remains separate from content coordinates:

| Requested paper mm | Emitted box pt | Represented paper mm |
| --- | --- | --- |
| Letter portrait 215.9 × 279.4 | 612 × 792 | 215.9 × 279.4 |
| A4 portrait 210 × 297 | 595 × 842 | 209.902778 × 297.038889 |
| A4 landscape 297 × 210 | 842 × 595 | 297.038889 × 209.902778 |
| Custom 12 × 8 inches, 304.8 × 203.2 | 864 × 576 | 304.8 × 203.2 |

No stretching, fitting, recentering or compensation absorbs boundary quantization.
Nominal edge geometry can therefore be clipped by the representable PDF boundary.
Separate renders may differ in metadata bytes; coordinate agreement is the
contract, and each save preserves its own invocation's exact prepared bytes.

## Current-page UI and safe saving

Use **File → Export → Export current page calibration sheet (PDF)…**. The
`action_export_calibration` follows the ordinary PDF action, before PNG; the SVG
action remains immediately after PNG. It uses the existing icon, translation
hooks, Qt slot auto-connection, loading lock and retained-dialog guard.

Invocation captures current geometry, registration string, source path and
one-based page number, then renders complete PDF bytes synchronously before
opening `SaveCalibrationPDFDialog`. Expected ValueError/RuntimeError preparation
failures use the existing error display and open no destination dialog. No
compaction, image acquisition, print-count update, document action/history/status
change, event processing, preference change or selection change occurs.

The dialog retains bytes and naming metadata, not a document. Later page/layout/
profile/path changes cannot change its PDF. Suggestions include the suffix even
with dotted stems: `Deck.v2-page-2-calibration.pdf` or unsaved
`page-1-calibration.pdf`. The title identifies the captured page. AcceptSave,
AnyFile, translated PDF filter, default suffix, existing export-path initial
directory, normal overwrite confirmation and asynchronous `open()` follow M10.
Existing error/finished handling releases the retained reference.

Only the two prepared-byte dialogs use the small private `_write_prepared_file`
helper in `dialogs.py`. It retains QSaveFile's disabled direct-write fallback,
binary WriteOnly, complete-count/commit checks, cancellation and temporary-owner
release before propagating OSError. Each dialog formats its translated destination/
reason error and logs success only after commit. Cancel performs no writing.
SaveCutTemplateDialog's constructor and behavior are preserved; unrelated dialogs
and the async export infrastructure are unchanged.

## Numeric and automated validation

Windows CPython 3.13.14, PySide6/Qt 6.11.2, pytest 9.1.1, pytest-qt 4.5.0,
pytest-timeout 2.4.0, Pint 0.24.4, pypdf 6.19.0, Poppler pdftoppm 26.07.0.
The existing environment was reused without installations. From the repository root:

```powershell
$env:PATH = "$PWD\venv\Scripts;$env:PATH"
.\venv\Scripts\python.exe -m pytest tests/test_calibration_pdf.py tests/ui/test_calibration_export.py tests/test_export_mapping.py::test_independent_a4_card_coordinates tests/test_export_mapping.py::test_helper_restores_active_painter tests/test_native_print_mapping.py::test_observable_anisotropic_mapping_and_clip tests/ui/test_cut_template_export.py::test_success_replaces_existing_file_with_exact_bytes tests/ui/test_cut_template_export.py::test_write_failure_preserves_destination_and_reports_error tests/ui/test_cut_template_export.py::test_valid_empty_page_exports_and_cancellation_does_no_io tests/ui/test_cut_template_export.py::test_real_action_autoconnection_menu_and_loading_lock -q --timeout=30 --tb=short
git diff --check
```

Final result: **45 passed in 8.21 seconds** (24 rendering, eight new UI, thirteen
selected regression cases). An earlier 44-case run passed before adding the
GUI-thread/no-artwork/no-events case. Diff checks passed. No application tests
were repeated after documentation-only edits.

Actual in-memory PDFs were reopened with pypdf. A helper limited to synthetic Qt
straight stroked paths resolves PDF CTMs and bottom-left coordinates into
top-left mm, rejecting clip/fill/text bounds as card outlines. Partial regular,
landscape and custom oversized cases at 300/600 DPI agree with parsed M09 SVG
centrelines within 0.01 mm. Page boxes are asserted separately. The independent
zero-margin/spacing A4 first-card reference and both ruler lengths measured:

| DPI | X mm | Y mm | W mm | H mm | Horizontal/vertical reference mm |
| --- | --- | --- | --- | --- | --- |
| 300 | 10.3716666667 | 16.4253333333 | 63.0766666667 | 88.0533333333 | 49.9999999913 / 49.9999999913 |
| 600 | 10.3716666667 | 16.4253333333 | 63.0766666667 | 88.0533333333 | 49.9999999913 / 49.9999999913 |

The negligible reference difference is emitted floating-point transform precision;
the intentional length is exactly 50 mm before PDF serialization. No tolerance
was broadened to hide a scale or translation error. Scene checks independently
verify labels, containment, pen width/caps, crosses, ticks, fresh registration
roots and profile placement with zero legacy offset. Disabled PDFs contain no
card image objects. Empty pages retain selected existing marks. Failure cases
cover invalid paper/resolution, rejected layout, painter startup/render rejection,
resource cleanup and primary-error preservation.

New UI cases verify one real action/dialog invocation, fixed capture after later
changes, actual exact-byte saves to Unicode destinations without rerendering,
filenames/options, errors, Save/Cancel, lock/retained reference and unchanged
document/history/layout/path with no image or background side effects. Selected
M10 tests recheck replacement, short/open/commit failures and cancellation through
the shared writer. Mapping regressions exercise existing PDF/PNG coordinates,
painter restoration, anisotropic native scaling and clipping.

Unrelated suites, acquisition hashes, M09's serializer matrix and the remainder
of native/PDF/PNG/database/network/cache/serialization tests were skipped. No
default, schema, saved preference, OS locale, font installation or dependency
change was made. The placeholder-factory bug remains a separate follow-up;
calibration does not call it or manufacture placeholder cards.

## Visual and native status

Poppler independently rendered Letter portrait with nine occupied cards and no
registration, plus A4 landscape with three occupied cards and the existing
Silhouette profile. Their orientation, full-grid placement, trim outlines, centre
crosses, both rulers/ticks, existing marks and absence of artwork were inspected.
Disposable PDF/PNG outputs remain ignored under `.m01-output/m11-review`.

**Label legibility is not verified in this environment.** QFontDatabase reports
zero font families under the offscreen Qt platform. Actual review PDFs display
fallback boxes in place of text; menu/dialog screenshots have the same known
limitation. The tests verify the exact label strings, scene positions and size,
but do not establish readable PDF glyphs here. No font asset, system-font change
or dependency workaround was added. Native font availability and resulting label
appearance need an ordinary application/viewer check before using labels for
physical measurement. This is a concrete environment limitation, not a successful
typographic qualification.

Offscreen controls confirmed the menu text/tooltip, page-2 title and
`Deck.v2-page-2-calibration.pdf` suggestion. Visible non-native Qt widget Save/Cancel
were exercised with exact prepared bytes and retained-dialog cleanup. The
controlled main-window review used an inert preview scene. Native interactive
controls remain unavailable; offscreen checks do not establish native appearance
or Leonardo operation. No physical print/cut operation was performed or required
for code acceptance.

## Later physical handoff

Printer scale/placement checks should use a populated Letter calibration page
with registration disabled, matching paper and actual-size/100% viewer printing.
After confirming readable labels, measure both 50 mm references at tick centres,
then several card positions from physical sheet edges. Repeat the sheet to
separate consistent offset from variable feed/skew. Record raw measurements,
printer, driver, PDF viewer, paper and all scaling settings. Do not derive
automatic compensation from a single result. Viewer printing qualifies that
route only; retain the separate M06 native-print physical procedure.

Bullseye/Silhouette marks do not establish Juliet compatibility. A later initial
Siser physical check can use the already preserved Leonardo-generated Letter
PDF/LDS pair without repeating numeric-placement acquisition. Explicitly verify
operational cutting settings: loading an LDS does not establish a cutting preset.
Externally generated Siser marks, camera-anchor interpretation, contours-only
alignment and production M08 remain gated.

No production Siser implementation, live PageScene calibration, native calibration
printing, PNG/batch/multi-page calibration, paired job packaging, corner/kerf/
common-line optimization, settings/document fields, artwork enhancement or
placeholder repair is included. Production M08, physical qualification and later
milestones were not started.

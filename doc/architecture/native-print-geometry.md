# M06: Native printer mapping and preview

Recorded 2026-10-07 (America/Los_Angeles). Accepted M05
`f48978bea121d202b9d0a4ff692ec6ddd09be1f5` was fast-forwarded into `siser-dev`
and pushed before creating `milestone/m06-printer-mapping`. Local and remote
`trunk` remain `00202f988bef071c7070967e86c1490cb19114ba`. M06 is submitted
on its milestone branch. Accepted M06 has since been integrated into `siser-dev`
for M07; registration adoption is documented in [registration-profiles.md](registration-profiles.md).

## Coordinate spaces and transformation

Application margins already constrain shared page geometry. Active native scenes
use `ON_PAPER | NATIVE_PRINT`, without IMPLICIT_MARGINS. NATIVE_PRINT gives zero
scene x_offset, just like ON_SCREEN and FILE_EXPORT. Cards, bleed, guides,
registration items, title, and page labels retain full nominal sheet coordinates.
Legacy bare ON_PAPER/IMPLICIT_MARGINS behavior remains for other callers/tests.
No geometry, card-size, packing, rounding, serialization, or settings changes.

The shared private `_render_page` extends M05's helper narrowly; the existing
`_render_export_page` remains an equal-DPI, zero-native-correction wrapper.
It requires actual selected-page ready geometry and a floating nominal source
rectangle from sheet millimetres through distance_to_px. Source and target are
identical, with IgnoreAspectRatio; there is no device/scene/paint-area fitting.
LosslessImageRendering remains. Painter state is saved and restored in finally.

Let C be canonical DPI (300), Dx/Dy accepted logical printer DPI, (x,y) a canonical
scene point, H nominal sheet height in floating canonical units, k the configured
horizontal correction converted once by distance_to_rounded_px, and (ox,oy) the
accepted paintRect top-left in millimetres. Normal mapping is:

```text
device_x = (x + k) × Dx/C − ox × Dx/25.4
device_y =  y      × Dy/C − oy × Dy/25.4
```

With the native workaround enabled for physically landscape input:

```text
device_x = (H − y + k) × Dx/C − ox × Dx/25.4
device_y =  x          × Dy/C − oy × Dy/25.4
```

Operations: save, establish clip in initial printer coordinates, scale Dx/C and
Dy/C, translate by (k − canonical(ox), −canonical(oy)), optionally translate by H
and rotate clockwise 90 degrees, render, restore. No aspect correction and no
accumulating per-page transforms. Physical width > height determines rotation,
including custom dimensions; stored custom orientation does not transpose them.

Positive correction means rightward on selected output paper after rotation.
The entire scene moves exactly once, including the title. This intentionally fixes
the legacy title omission and makes the rotated correction axis explicit. The
existing setting/unit/default and canonical correction rounding are retained.

## Origin and clipping policies

Read accepted `printer.pageLayout().paintRect(Millimeter)` as floating values.
FullPage mode has the paper origin; Standard mode has the accepted margin-defined
paint origin. Before nominal transforms, intersect the existing painter clip with
(0,0, paint_width_mm × Dx/25.4, paint_height_mm × Dy/25.4).

Four quantities remain distinct: application layout margins, accepted Qt paint
margins, hardware unprintable margins, and user horizontal correction. Application
margins are not subtracted again. Hardware offsets are not separately subtracted:
Qt's Windows backend owns that handling. Device/hardware boundaries can clip
content; no fitting, reflow, invented calibration constant, or driver-borderless
instruction is introduced.

The stored `printer.borderless-printing` key/default and widget binding remain,
but its label is now “Use full-page printer coordinates.” The tooltip explains
paper/printable-area origins, possible hardware clipping, and driver ownership of
borderless printing/enlargement. Horizontal-offset wording specifies its direction
after rotation. Development uses the existing loadUiType/UI compiler path;
the test compiles and instantiates the edited `.ui`. No generated UI source or
translation/catalog refresh was performed.

## Paper conversion and initial setup

to_page_layout retains its signature and legacy implicit application margins.
Both named and custom layouts explicitly use millimetres. Named paper IDs remain;
custom QPageSize uses ExactMatch. Intended dimensions come from page_width/height.
The workaround swaps intended landscape output dimensions for either paper kind.
Orientation is chosen relative to intrinsic QPageSize dimensions: Portrait means
intrinsic orientation, which can itself be landscape. Ledger is tested explicitly.
Device minima and painter origin stay outside this model conversion.

Initial create_printer is best-effort: HighResolution, NativeFormat first, and a
warning/return if no valid initial device. For a valid device it requests the
existing default resolution and simplex, temporarily uses full-page coordinates,
requests orientation before paper size, and reads the actual layout. A copied
layout in millimetres requests FullPage with zero configured margins, or Standard
with the refreshed device minimum margins. Setters/readback discrepancies are
logged with requested/actual dimensions, mode, and margins; rejection does not
make the dialog unconstructible. Invalid Standard margins are not forced to zero.

Orientation precedes paper size because the reviewed Qt 6.11.2 Windows size setter
refreshes device minimum margins using current orientation. A rejected layout
setter alone is not classified as unsupported paper. Later accepted choices are
authoritative and can recover from unsuitable defaults.

## Accepted state, resolution, and lifecycle

Every print callback/preview request uses the supplied printer, explicitly selects
the output page, requires geometry, and validates accepted physical paper, paint
rectangle, coordinate mode, and resolution. The paper must match nominal dimensions
or swapped nominal dimensions for the active workaround. The absolute maximum
representation allowance is **25.4/72 mm per dimension**, with no relative
tolerance or fit permission. Invalid/nonfinite/unusable metrics and mismatched
paper are rejected before painter.begin where possible, with required/selected
dimensions and an instruction to choose matching paper/orientation or change the
document. No accepted dialog choice is silently overwritten.

Before starting an inactive painter, read positive accepted printer.resolution and
reapply that same value exactly once. This retains the dialog's logical resolution
while normalizing the reviewed Windows logical-to-physical stretch factors.
No paper, margin, coordinate-mode, or other setting is changed afterward. Check
begin, then reread accepted layout and positive logicalDpiX/Y; use those DPI values
for scaling, without an additional physical-DPI scale. Incompatible backend
changes stop before content. These checks repeat for actual output pages.

newPage is checked and no trailing blank is generated. Empty models start no job;
a legitimate empty document page is rendered. Active painters end reliably;
cleanup failures preserve the primary error, and a failed final end is reported.
Page failures include the output page number. Success is logged only after
completion. Renderer returns True for software completion, False for no pages.

Guarded PrintDialog callbacks report RuntimeError through error_occurred, connected
to MainWindow.on_error_occurred, and schedule PrintCountUpdater only after successful
renderer return. Preview reports failures through the same route and never schedules
counts. MainWindow also guards genuine constructor RuntimeError. Spool/physical
completion, copy/range accounting, and cancellation semantics remain deferred.

## Measurements and backend precision

These tests use real QPrinter **PdfFormat**, never NativeFormat hardware jobs.
They exercise the native application adapter but not Windows GDI or a real driver.
With zero correction, zero application margins/spacing, A4 first-card output is:

| Mode / DPI | x mm | y mm | width mm | height mm |
| --- | --- | --- | --- | --- |
| FullPage / 300 | 10.371666667 | 16.425333333 | 63.076666667 | 88.053333333 |
| FullPage / 600 | 10.371666667 | 16.425333333 | 63.076666667 | 88.053333333 |
| Standard / 300 | 10.334333328 | 16.444666628 | 63.076666667 | 88.053333333 |
| Standard / 600 | 10.376666657 | 16.444666628 | 63.076666667 | 88.053333333 |

Standard accepted margins are asymmetric (3.17, 5.23, 7.41, 9.67) mm, distinct from
application margins. Qt 6.11.2's QPdfEngine pageMatrix independently translates by
the integral paintRectPixels origin. Against floating application compensation,
its residual is `paint_origin_pixels × 25.4/D − paint_origin_mm`: at 300 DPI,
(-0.037333333, +0.019333333) mm; at 600, (+0.005, +0.019333333) mm. Each is within
half a device pixel. Tests retain 0.01 mm content tolerance after independently
accounting for this backend residual; they do not loosen tolerance to hide fitting.
Card dimensions remain unchanged. No extra compensation is added to the contract.

Native software boxes: A4 portrait 595 × 842 pt; A4 landscape 842 × 595 pt;
12 × 8-inch custom 864 × 576 pt; workaround versions 595 × 842 and 576 × 864 pt.
Whole-point PDF page-box precision remains separate, as documented for M05.
Observable painter mapping at separate 600 × 300 DPI checks independent A4
positions/dimensions, both correction signs, rotated direction, all scene items,
initial clip intersection, and state restoration. Real drivers can report/round
different device geometry; physical accuracy requires measurement.

Primary references examined, pinned where implementation-specific:

- [QPrinter full-page coordinates](https://doc.qt.io/qt-6/qprinter.html#setFullPage)
- [QPageLayout modes and intrinsic orientation](https://doc.qt.io/qt-6/qpagelayout.html)
- [Qt 6.11.2 Windows printer engine](https://github.com/qt/qtbase/blob/v6.11.2/src/printsupport/platform/windows/qprintengine_win.cpp)
- [Qt 6.11.2 PDF pageMatrix](https://github.com/qt/qtbase/blob/v6.11.2/src/gui/painting/qpdf.cpp#L3557)
- [Qt 6.11.2 layout rectangles](https://github.com/qt/qtbase/blob/v6.11.2/src/gui/painting/qpagelayout.cpp)
- [Qt 6.11.2 device layout validation](https://github.com/qt/qtbase/blob/v6.11.2/src/printsupport/kernel/qplatformprintdevice.cpp)

## Reproduction and results

Environment reused without upgrades: CPython 3.13.14, PySide6/Qt 6.11.2, Pint 0.24.4,
pytest 9.1.1, pypdf 6.19.0; independent renderer Poppler 26.07.0. No runtime
dependency, saved preference, document schema, OS locale, or reference change.

Exact final command from repository root (normalization affects older fixtures
in memory only; new fixtures configure paper explicitly):

```powershell
$env:PATH = "$PWD\venv\Scripts;$env:PATH"
@'
import mtg_proxy_printer.settings as s
s.DEFAULT_SETTINGS['documents']['paper-size'] = 'A4'
s.settings.read_dict(s.DEFAULT_SETTINGS)
import pytest
raise SystemExit(pytest.main([
    'tests/model/test_page_geometry.py',
    'tests/model/test_page_layout_settings.py',
    'tests/page_scene/test_page_geometry_adoption.py',
    'tests/test_pdf_export.py',
    'tests/test_print.py',
    'tests/test_export_mapping.py',
    'tests/test_native_print_mapping.py',
    '-q', '--timeout=30', '--tb=short', '--show-capture=no',
]))
'@ | & .\venv\Scripts\python.exe
```

**169 passed in 24.53 seconds**: 20 geometry, 55 layout, 22 adoption, 2 PDF smoke,
6 print/UI fixtures, 22 M05 measured exports, and 42 native cases. Coverage includes
full/Standard at 300/600; fractional margins; named/custom rotation and hidden
regular/oversized/partial/empty pages; Ledger orientation and units; unequal DPI;
correction signs/title/labels/guides/marks; clipping; setup order/refreshed minima;
rejected defaults and later recovery; preserved accepted resolution/choices;
one-point mismatch boundary; begin/transition/geometry/DPI/backend-paper/end
failures and primary-error preservation; real Qt dialog callbacks/count suppression;
constructor reporting; zero-page job suppression; and dynamic settings UI wording.
After strengthening the existing-clip assertion, the four anisotropic mapping
cases passed again in 0.37 seconds; no production code changed after the combined
run. No full-suite or network loader rerun was needed.

## Visual and native interactive checks

Poppler independently rendered portrait, named/custom landscape, rotated output,
nonzero Standard margins, and corrected pages with guides, existing Bullseye marks,
title, and page labels. Those images were inspected in the local image viewer.
Orientation, occupancy, correction direction, and clipping at Standard boundaries
appeared consistent. Existing mark/title overlap in the portrait fixture and
edge-clipped marks are retained presentation, not a new Siser layout. Review images
are local/ignored under `.m01-output/m06-review`; synthetic fixtures remain in
`m06-probe`, `m06-validated`, and `m06-final` alongside it.

Native computer controls are disabled in the enabled automation surface. Native
interactive preview/printer selection was therefore unavailable and not performed.
Offscreen real Qt dialog callbacks and software-output inspection are not native
interactive validation. No physical jobs were submitted.

## Later low-cost physical qualification

Use blank-paper outlines, rulers, and asymmetric anchors, not real artwork. Record
printer/driver identity and version, paper and feed orientation, selected logical
resolution, driver scaling, and borderless enlargement settings. Start with zero
correction and measure horizontal/vertical distances and anchor positions from
paper edges. Repeat with a known correction to verify rightward output-paper
direction and unchanged size. Repeat portrait/landscape and the workaround if it
will be used, and FullPage/Standard where supported. Print the same simple sheet
again to observe feed variation. Keep raw measurements and settings together.
This qualifies printer behavior; cutter-camera alignment remains separate.

## Remaining boundaries and M07 handoff

M07 adopted registration profiles after accepted M06 was integrated. Its contract
is in [registration-profiles.md](registration-profiles.md). Leonardo numeric
placement remains pending and M08 stays gated;
the direct Print & Cut evidence and newer Artwork Only evidence remain distinct.
M06 itself added no registration classes, Siser marks, profiles, SVG/calibration UI,
import compensation, physical constants, image enhancement, or packaging work.

Deferred: physical GDI/driver qualification, native interactive checks, spool/copy/
range/cancellation accounting, general reentrancy/threading, guide-width units,
PNG encoder-save accounting, and broader count timing. User incoming references
and unrelated local files were preserved. No implementation deviation or blocker
remains; backend-origin quantization and unperformed physical/native checks are
explicit validation limitations.

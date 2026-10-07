# M03 nominal page geometry and M04 handoff

`model.page_geometry.build_page_geometry(layout, page_type, card_count)` is a
synchronous, uncached calculation. It reads explicit inputs without modifying
the layout, consulting a document/selected page, or accessing printer preferences.
It requires no QApplication, card images, database, or graphics scene.

## Immutable API

The builder returns a frozen `PageGeometry`. Its nested `Rectangle`,
`SideDistances`, and `CardPlacement` records are also frozen; placements and
grid edges are tuples. Only numbers, the PageType enum, immutable records,
tuples, and None are retained. No Pint quantities or Qt geometry objects escape
the calculation. The layout is imported for type checking only.

- `sheet_width_mm`, `sheet_height_mm`, `margins_mm`: copied physical values.
- `scene_width_px`, `scene_height_px`: rounded nominal scene extent.
- `page_type`, `card_width_px`, `card_height_px`, `rows`, `columns`, `capacity`:
  explicit type, legacy trim dimensions, and physical capacity results.
- `margin_frame_px`: rectangle between independently rounded margin anchors.
- `grid_bounds_px`: full capacity-grid rectangle, independent of occupancy;
  None for zero capacity.
- `placements`: sequential entries with original `slot_index`, row, column,
  `trim_px`, `bleed_px`, and derived `bleed_envelope_px`.
- `grid_x_edges_px`, `grid_y_edges_px`: ordered unique full-grid trim edges.

Rectangles store x, y, width, height; right and bottom are derived. Side distances
are named left, top, right, bottom. Containing fields/properties declare units;
Rectangle itself is a numeric record. The bleed envelope measures geometry,
not opaque pixels or a rounded cutter contour.

## Coordinates and rounding

Origin is the physical sheet's top-left; x increases rightward and y downward.
Canonical `_px` values are the existing 300-DPI logical coordinates, independent
of source-image resolution or future output DPI. All derived `_mm` views use
`logical_px_to_mm`, which delegates to the existing Pint print-context conversion.
No separately calculated millimetre layout or display rounding exists.

Capacity uses PageLayoutSettings' existing physical-unit methods first. Page
dimensions, margins and spacing then use `distance_to_rounded_px`. Grid origins
use those rounded values and retain half pixels. Regular trims remain 745 ×
1040; oversized trims remain 1040 × 1490 logical pixels.

Exact physical size is separate: A4 portrait is 210 × 297 mm, while its scene
is 2480 × 3508 logical pixels. Named paper sizes use the layout's orientation
properties; custom dimensions are not swapped again.

The full grid is centered on the rounded sheet, with nonnegative remainder
clamping followed by left/top margin clamping. Right/bottom margins affect
capacity, not a new margin-frame centering rule. Partial pages and short final
rows keep their full-grid slot positions. No packing or rotation is introduced.

## Bleed, edges, and input policy

Outer edges receive rounded full configured bleed. Edges with an actual
sequentially occupied neighbor receive the rounded physical minimum of half
spacing and full bleed. Spacing is halved before rounding, not after. A partial
final row changes neighbor bleed without moving trims or changing capacity.

Grid edges include empty slots. Zero rounded spacing shares boundaries; positive
rounded spacing includes both sides of each gap. Edges are unique and do not
extend beyond the grid. This intentionally differs from the legacy guide
generator for tiny positive spacing rounded to zero, which mixes rounded spacing
with original-quantity truthiness and emits duplicate and extra guides. Active
guide rendering is unchanged in M03; M04 must account for this approved difference.

REGULAR and OVERSIZED are supported. Empty UNDETERMINED retains its explicit type
and uses the existing regular-grid fallback. MIXED, negative counts, over-capacity
counts, and nonempty UNDETERMINED raise ValueError. A nonempty zero-capacity page
also raises ValueError. An empty zero-capacity page has no placements, no edges,
and no grid bound; positive-capacity empty pages retain the full grid. The old
exact-one-card-fit capacity rule returning zero is preserved.

## Output exclusions and M04 adoption

No printer horizontal offset, IMPLICIT_MARGINS subtraction, landscape printer
workaround, output-resolution scaling, PDF fitting, or Leonardo compensation is
applied. `to_page_layout()` is not used. Output adapters own those transformations.
No registration dimensions or corner-radius policy enter this calculation.

M03 leaves the active PageScene calculation unchanged. M04 should replace nominal
calculations in `_compute_position_for_image`, `update_card_bleeds` /
`_has_neighbors`, and `_compute_cut_marker_positions` with snapshot consumption.
Margin anchor consumers can use `margin_frame_px`. Pass the actual target page's
type/count explicitly, map placements back through slot indexes, and handle
invalid/transient inputs deliberately. Apply render-mode and printer transforms
after nominal geometry. Consolidate the consumers rather than maintain two
continuing layout implementations.

No geometry field is added to PageLayoutSettings; dataclass-based document
serialization is unchanged. Production modules do not yet import this builder.

## Validation

Focused tests characterize A4 anchors, Letter, landscape/custom orientation,
asymmetric clamps, both card types, partial occupancy, neighbor bleed and rounding,
unique edges (including tiny spacing), empty/invalid inputs, frozen snapshots,
rebuilding after source changes, and exact-versus-rounded units. A fresh-process
check builds geometry with no QApplication or document/scene imports.

Existing layout tests use the documented in-memory A4 normalization; application
defaults and saved preferences are not changed. No full-suite, manual visual,
physical printer/cutter, or network-loader validation is required. These checks
do not establish PDF/printer/cutter alignment; those remain later milestones.

Recorded results: 20 new geometry cases pass; 55 existing layout cases and 224
existing x/y position characterization cases pass with in-memory A4 normalization.
The initial combined run passed 297 cases (18 geometry cases at that point);
after adding two final edge cases, the focused geometry run passed all 20.
The M02 verifier also passes all 11 acquired artifact hashes.

Reproduce from the repository root in PowerShell:

```powershell
$env:PATH = "$PWD\venv\Scripts;$env:PATH"
@'
import mtg_proxy_printer.settings as settings
settings.DEFAULT_SETTINGS['documents']['paper-size'] = 'A4'
settings.settings.read_dict(settings.DEFAULT_SETTINGS)
import pytest
raise SystemExit(pytest.main([
    'tests/model/test_page_geometry.py',
    'tests/model/test_page_layout_settings.py',
    'tests/page_scene/test_page_scene.py::test__compute_position_for_image_x',
    'tests/page_scene/test_page_scene.py::test__compute_position_for_image_y',
    '-q', '--timeout=30',
]))
'@ | & .\venv\Scripts\python.exe
```

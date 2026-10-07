# Shared nominal page geometry: M03 contract and M04 adoption

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

M03 introduced the builder without changing active rendering. M04 now makes
PageScene consume it for occupied card placements, bleed distances, full-grid
manual guides, registration margin anchors, nominal full scene extent, and label
positioning. No independent placement/bleed/guide arithmetic remains in the scene.
No geometry field is added to PageLayoutSettings; document serialization and
settings migrations remain unchanged.

## M04 scene ownership and reconciliation

Each scene owns `geometry` for its actual persistent `selected_page`, plus
`full_grid_geometry` REGULAR/OVERSIZED snapshots populated to capacity for guides
and compatibility queries. Full grids rebuild on layout changes; actual snapshots
refresh for page selection, insertion/removal/movement, replacement, page type,
and layout notifications. Row/column fields derive from the actual snapshot;
unavailable geometry uses zero counts. No global/persistent geometry cache exists.
Output scenes may select a different page without changing the UI selection.

Production item positions and bleeds resolve `geometry.placements[item.index.row()]`.
The position-sorted card item list is not a model slot array. Missing pixmaps leave
holes; Image-column data changes can create a newly available item in its proper
slot. Ordinary updates remain incremental, including retention of unaffected
items. Count-only changes with unchanged grid edges do not redraw guides.

Strict-builder ValueError (mixed, over-capacity, nonempty zero-capacity) becomes
an explicit scene-unavailable state: `geometry=None`, `geometry_pending=True`,
and `geometry_unavailable_reason`. Cards/guides are removed and labels hidden.
No count is clamped and no mixed page is reinterpreted. About-to-remove handlers
remove items while their persistent indices are still safe. Later valid model
notifications rebuild all cards if changes were deferred, restore positions,
bleed/guides/labels, and clear the pending state/reason. Root changes preserve
selected-page identity and page numbering; an invalid removed selection may use
the document's existing current selection as its replacement.

`action_applied` and `action_undone` provide a final pending retry; redo uses
`action_applied`. Normal model signals also recover direct `action.apply()` calls.
No signal-order change, timer, sleep, or transaction/coalescing framework is added.
`require_geometry_ready()` retries pending reconciliation and raises RuntimeError
with the reason if unresolved. The common `render(*args, **kwargs)` boundary calls
it before forwarding unchanged Qt arguments. Onscreen transient states remain safe;
explicit output cannot silently succeed with stale/blank invalid content. Empty
zero-capacity pages are valid blank output, with no grid or grid-dependent labels.

`_compute_position_for_image` remains an uncached full-capacity lookup plus the
presentation translation; UNDETERMINED maps to the regular grid. Actual rendering
uses occupied placements instead. `_has_neighbors` is compatibility-only and
reads adjacency from occupied row/column coordinates, never bleed magnitudes.
Production bleed updates use named top/bottom/left/right arguments.
`_compute_cut_marker_positions` and unused `CutMarkerParameters` are removed.
Tiny positive spacing rounded to zero now produces the approved unique shared
edges without the old extra/duplicate guide.

## Presentation after M06; legacy modes retained

Cards and guides use zero X offset for ON_SCREEN, FILE_EXPORT, or NATIVE_PRINT.
Active native scenes use ON_PAPER | NATIVE_PRINT without implicit margins; PDF/PNG
use ON_PAPER | FILE_EXPORT. Legacy bare ON_PAPER retains its configured scene
offset and IMPLICIT_MARGINS retains rounded application-margin subtraction.
Vertical guide caches omit X offset; drawing adds it exactly once. Full scene
extent uses snapshot rounded dimensions. Implicit extent still subtracts physical
margins before rounding; it is not the difference of rounded pixel quantities.

Registration anchors use snapshot `margin_frame_px`, use mode-specific X offset, and retain
all existing style strings, classes, visibility, item scaling/rotation/anchor
adjustments. They deliberately retain the old absence of implicit-margin
subtraction. No Siser marks or profiles are added.

Labels retain the greater final regular/oversized horizontal guide edge plus
rounded full bleed and two units. Title/page-number X uses regular guides; the
title has no legacy scene X offset, while the page number does. M06 applies its
printer correction once to the entire native scene, including title and labels.
Wrapping is unchanged.
Unavailable grids hide dependent labels. M05 PDF/PNG now map the actual snapshot
physical sheet rectangle with explicit DPI scaling and a pure optional PDF
quarter-turn. See [export-geometry.md](export-geometry.md) for measurements and
rounding/clipping boundaries. M06 native origin, correction, clipping, DPI, and
rotation are documented in [native-print-geometry.md](native-print-geometry.md).
Registration profiles and physical alignment remain later work; M07 is not started.

## Historical M03 validation

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

## M04 validation and limitations

Recorded 2026-10-07 (America/Los_Angeles). The combined run passed 856 cases:
20 M03 geometry, 55 legacy layout, 756 legacy scene, 19 new adoption, and six
existing PDF export smoke cases. After adding the last empty-zero-capacity output
case, all 20 adoption cases pass (857 unique cases validated across these runs).
The fixture verifier passes 11 historical plus seven Artwork Only acquisition
hashes; all three source SVGs remain unchanged.

Focused adoption coverage includes partial/oversized/empty hidden output targets
while UI selection stays elsewhere, fractional margins, X offsets and marker
adjustments, implicit rounding order, missing-image holes/new images, incremental
move/remove/replace/undo/redo, three-page shrinking reflow with temporary destination
overflow, empty-size transitions, sole-card size replacement, root page numbering,
selected-page removal, new-document replacement, invalid render rejection/recovery,
valid zero-capacity blank output, and tiny-spacing unique guides.

Run from the root using the existing development environment and in-memory A4
normalization shown above, with this pytest selection:

```python
[
    'tests/model/test_page_geometry.py',
    'tests/model/test_page_layout_settings.py',
    'tests/page_scene/test_page_scene.py',
    'tests/page_scene/test_page_geometry_adoption.py',
    'tests/test_print.py::test_export_pdf_creates_a_pdf_file',
    '-q', '--timeout=30', '--tb=short',
]
```

No saved defaults, printer preferences, locale, dependencies, document schema,
or model-action signal ordering were changed. No full application suite or
network loader rerun was performed. Two synthetic offscreen images (five regular
cards and three oversized cards) were rendered and visually inspected: full-grid
guides and expected partial occupancy/bleed presentation appeared. Review PNGs
remain local in ignored `.m01-output/m04-regular.png` and `m04-oversized.png`.
Native window inventory found no running MTGProxyPrinter target; a native
interactive UI check was not performed. Offscreen review does not establish it.
No physical print/cut validation was performed or required.

Leonardo numeric placement restoration/persistence/repeatability remains a later
validation task described in `siser-reference.md`. M08 registration stays gated;
no Leonardo compensation is included. M04 stops at the pushed milestone branch
for architect review. M05 subsequently implements file mapping as documented in
`export-geometry.md`; the preceding results describe the historical M04 state.

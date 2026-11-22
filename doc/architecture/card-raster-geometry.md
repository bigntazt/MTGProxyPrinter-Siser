# Card raster and physical geometry (M23)

M23 adds an item-local raster seam on the accepted M22 base
`2afe6cb70d30eb6c4bb12eda079a4a678f8275f6`. It does not enable enhancement
or a complete 600/1200-PPI export workflow. Original/Lanczos remains the baseline.

## Physical contract

`Card.requested_page_type()` and `CustomCard.requested_page_type()` use
`is_oversized`. Missing, undecoded, unusually shaped or high-resolution images
cannot change page classification. CheckCard still delegates to its front card.
This intentionally corrects earlier bitmap/metadata disagreements: declared
physical size wins. Identity, equality, hashing, constructors, custom source
bytes, persistence formats and page-size serialization are unchanged.

The existing card-size API supplies the canonical 300-DPI scene dimensions:

| Card type | Canonical | Synthetic 2× (600 PPI) | Synthetic 4× (1200 PPI) |
|---|---|---|---|
| Regular | 745 × 1040 | 1490 × 2080 | 2980 × 4160 |
| Oversized | 1040 × 1490 | 2080 × 2980 | 4160 × 5960 |

1200 PPI is a future maximum target, not a minimum. No selector is introduced.
Page geometry, layout rounding, unit conversions and print-device DPI remain
unchanged. The group bounding rectangle includes decorations and is not a trim
rectangle.

## Raster preparation and ownership

The compatible constructor is
`CardItem(index, document, parent=None, *, raster: QPixmap | None = None)`.
The positional parent remains supported. PageScene continues its default
construction and existing missing-image/replacement lifecycle.

Internal `PreparedCardRaster.from_card()` selects the explicit raster or obtains
the default image once. It copies the QPixmap value before normalizing its DPR
to 1. Native pixel dimensions and decoded pixels remain intact; neither the
caller, model nor original cache is mutated. An explicit null pixmap raises
ValueError. An unavailable default image raises RuntimeError if preparation is
called directly; PageScene's existing availability gate remains unchanged.

Canonical inputs avoid resampling. Other inputs get a canonical style proxy
using Qt SmoothTransformation and IgnoreAspectRatio. The full image maps to the
canonical rectangle, including nonuniform inputs. Only the artwork child gets
the independent width/native-width and height/native-height transform, after
grouping. The artwork's mapped rectangle is `(0, 0, canonical_width,
canonical_height)` within numerical tolerance. The group is not scaled.

Each child retains its intended local position/transform after addToGroup;
otherwise Qt's preservation of scene-relative transforms would counteract a
transformed positional parent. Ownership remains the existing single group-add
and single scene-add approach. No global raster cache is added. The frozen
dataclass freezes field bindings, not the mutable Qt values/dictionary inside.

Default corner colors still come from `card.corner_color()`, preserving
CheckCard's component-face mapping. Explicit overrides sample their style proxy
with the existing corner fractions, 10 × 10 region and smooth averaging. The
same prepared colors feed sharp fills and bleed corners. Preparation does not
compose or inspect CheckCard components on the explicit path.

Bleed strips keep the existing one-pixel selections and top/left offsets.
Expansion, zero-bleed opacity, stacking and positions remain canonical. Sharp
fills remain 50 pixels, bleed-corner lengths remain 32 regular/50 oversized, and
watermark font, anchor, rotation and layout signals are unchanged. Existing
artwork masks are untouched; the 2.5 mm cutting radius is a separate contract.

Acquisition, post_process_image, custom lazy processing, CheckCard composition,
cache keys, serializers, settings and print code are unchanged. Higher-resolution
CheckCard composition, provider orchestration, derivative storage, UI, export
preparation and enhancement integration remain deferred.

## Focused validation and reproduction

Use a separate environment and the private review package's `run_isolated.py`.
Before application imports it redirects platformdirs, blocks real application
storage and network access, restricts SQLite to memory/review locations, checks
the package's worktree, adds its runtime Scripts to PATH and selects offscreen
Qt. A4 is process-only. Do not run these tests against normal application stores.
The retained wrapper and resolved requirements are review artifacts, not
production startup changes.

Actual versions: Python 3.13.14, PySide6 Essentials/shiboken/Qt 6.11.2,
pytest 9.1.1, pytest-qt 4.5.0, pytest-timeout 2.4.0, Pint 0.24.4,
setuptools 84.0.0, build 1.6.1, pypdf 6.19.0. Current bootstrap requirements
were followed; pip check passed. Source-mode resource loading needed no separate
resource compilation. The stable and benchmark environments were not used.

PowerShell, with `$Review` set to the private M23 review directory and `$Python`
to its `runtime\Scripts\python.exe`, from the M23 worktree:

```powershell
$env:PYTHONPATH = $null
$env:M23_RUN_LABEL = 'focused-final'
& $Python -B "$Review\run_isolated.py" pytest `
  tests/model/test_card.py `
  tests/page_scene/test_card_raster.py `
  tests/page_scene/test_page_scene.py::test_sharp_corners `
  tests/page_scene/test_page_scene.py::test_card_item_origin_equals_pixmap_origin `
  tests/page_scene/test_page_scene.py::test_card_bleed_with_single_card `
  tests/page_scene/test_page_scene.py::test_card_bleed_with_two_cards `
  tests/page_scene/test_page_geometry_adoption.py::test_missing_pixmap_slot_can_become_available_and_replaced `
  tests/page_scene/test_page_geometry_adoption.py::test_incremental_moves_removal_undo_and_redo `
  tests/page_scene/test_page_geometry_adoption.py::test_empty_size_transition_and_sole_replacement `
  -q --basetemp "$Review\pytest-focused-final" --timeout=30
```

Recorded run: **158 passed in 7.33 s**. One subsequently added test was run
separately (**1 passed in 0.16 s**):

```powershell
$env:M23_RUN_LABEL = 'check-override'
& $Python -B "$Review\run_isolated.py" pytest `
  tests/page_scene/test_card_raster.py::test_explicit_check_raster_uses_proxy_without_composition_or_component_colors `
  -q --basetemp "$Review\pytest-check-override" --timeout=30
```

Thus 159 distinct focused cases passed; a fresh combined invocation now includes
that additional case. Metadata-only development validation passed 57 cases;
the accepted-base model file passed 49 before editing. No old expectation was
weakened. Coverage includes physical grouping, offline save/load of canonical
page sizes and unchanged custom bytes, 1×/2×/4× pixels and bounds for both sizes,
DPR and transformed parents, nonuniform mapping, override/default corner policy,
exact strip samples, decoration signals/layers, caller release, reconstruction,
missing-image lifecycle and nonzero scene spacing/bleed.

Private `reference_scenes.py` captured two small accepted-base scenes before
production edits, then default-final and explicit-4× variants. It includes Card,
CustomCard and CheckCard, 1.5 mm row/2.5 mm column spacing, 1.2 mm bleed,
watermarks, sharp fills, guides and bullseye marks. `compare_references.py`
asserts identical default pixels and recorded geometry for both sizes. The 4×
variants retain 2980 × 4160 and 4160 × 5960 native pixmaps while mapping to
745 × 1040 and 1040 × 1490 respectively. Item placement, guides, marks and
neighboring default CustomCard/CheckCard pixels remain unchanged. Both 2× sizes
are covered by the automated mapping tests.

```powershell
# Run accepted-base BEFORE edits in the isolated accepted-base worktree.
& $Python -B "$Review\run_isolated.py" script "$Review\reference_scenes.py" accepted-base
# Final worktree:
& $Python -B "$Review\run_isolated.py" script "$Review\reference_scenes.py" default-final
& $Python -B "$Review\run_isolated.py" script "$Review\reference_scenes.py" high-resolution
& $Python -B "$Review\run_isolated.py" script "$Review\compare_references.py"
```

The recorded baseline was captured in this worktree before edits, not recreated
afterward. To recapture it independently, adapt the private wrapper's worktree
constant to a separate checkout of the accepted base; do not use the stable
installation. Comparison labels load Arial process-locally for readability,
without regenerating captured pages.

Agent visual inspection of the offscreen comparison sheet found the same card
placement, quadrant/corner colors, split CheckCard composition, spacing, guides
and registration alignment. Explicit 4× detail stays within the same trim area.
Scene text used fallback glyph boxes in this isolated runtime, consistently in
the baseline and final pages. This limits typography assessment; no claim of
native interactive verification, enhancement quality, complete target-PPI
export, or physical print/cut quality is made. No normal application launch,
catalog operation, inference or hardware job was performed.

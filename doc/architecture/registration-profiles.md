# M07: Registration profiles with preserved existing output

Recorded 2026-10-07 (America/Los_Angeles). Accepted M06
`37665618874bacfdb5483efda2e78a137899ae76` was fast-forwarded into `siser-dev`
and pushed before creating `milestone/m07-registration-profiles`. Local and remote
`trunk` remain `00202f988bef071c7070967e86c1490cb19114ba`. M07 is submitted
for architect review on its separate milestone branch; it is not merged.

## Stable identities and dependencies

| UI label (existing translation context retained) | Stored string | Enum member |
|---|---|---|
| Disabled | `"None"` | `RegistrationProfileId.NONE` |
| Bullseye | `"Bullseye"` | `RegistrationProfileId.BULLSEYE` |
| Silhouette cutter (Cameo-compatible) | `"Cut marker"` | `RegistrationProfileId.SILHOUETTE` |

`registration_profile_ids.py` imports only standard-library `enum` and defines a
plain Enum. Settings and UI import that lightweight identity module, using `.value`
for the public set of strings and combo-box item data. Literal translated labels,
order, contexts, settings key/default, document annotations/schema and migrations
are unchanged. `PageLayoutSettings.print_registration_marks_style` remains a string;
no runtime profile object is persisted and no aliases are accepted.

Only scene rendering imports `page_scene.registration_profiles`. That module uses
shared `PageGeometry` and the existing graphics classes. It neither queries settings
nor retains a scene/document, inspects `RenderMode`, or calculates layout. Keeping
runtime profiles out of settings initialization avoids the geometry/layout/settings
import cycle. Shared singleton profile instances are stateless; items are not shared.

## Runtime contract

```python
class RegistrationProfile(Protocol):
    profile_id: RegistrationProfileId

    def create_items(self) -> list[QGraphicsItem]: ...

    def place_items(
        self, items: Sequence[QGraphicsItem], geometry: PageGeometry,
        *, legacy_x_offset_px: int = 0,
    ) -> None: ...

def get_registration_profile(style: str) -> RegistrationProfile: ...
```

`create_items()` returns fresh, initially unparented roots. Descendants belong to
their root and must not also be returned. Profiles must not create model-backed
`CardItem` objects. No dynamic registration, plugin or exporter framework is added.

Disabled returns no roots and placement is a no-op. Bullseye creates, in order,
`BullseyeMarkItem(False, False)`, `(True, False)`, `(False, True)`. Silhouette creates
`CutMarkSquareItem()`, `CutMarkAngleItem(False)`, `(True)`. Their anchors are the ready
actual page's `margin_frame_px` top-left, top-right and bottom-left, respectively.
The explicit legacy X offset is added before invoking each item's normal overridden
`setPos(QPointF(...))`; no implicit-margin subtraction is introduced.

The SVG resource, pens/colors, geometry, Z, scale, transform origins and rotation
are retained. Only unused `update_visibility(style)` methods were removed after
checking their callers. Active roots have their existing default opacity of one;
inactive profiles have no scene items. Bullseye retains
`RESOLUTION.magnitude / 100 + 285 / 256000` and `256105 / 256000`. Silhouette retains
its canonical rounded 65-unit square, 213-unit angle and 12-unit thickness. These
are compatibility behavior, not specifications for another cutter.

## Ownership, selectors and stacking

`PageScene.print_markers` records only active roots and `_registration_profile`
records the active stateless profile. Both are initialized before geometry/selectors
can run. A replacement removes old roots while ownership is still recorded, clears
the old list/reference, publishes fresh roots before adding them, places them using
`self.geometry`, then restores stacking. Geometry-only refreshes reuse existing roots
and call placement on every successful actual-page refresh.

`_is_registration_item` tests roots and traverses their descendants' parent chain.
An ancestry precheck avoids walking unrelated card trees. Guide and label selectors
keep their existing Qt type classification and ascending order while excluding all
registration-owned roots/descendants. Global classifiers and `card_items` are unchanged.
Guide deletion, palette updates and footer updates therefore leave owned lines/text
alone, including nested items.

For each root, the first actual guide in ascending order with the same parent and Z
receives `root.stackBefore(guide)`. This runs after installation/placement and after
guide drawing. Existing layers are retained; profiles are not wrapped in a new group.
Guides remain above same-Z registration roots after replacement and redraw.

An observed PySide6 6.11.2 ownership pitfall required a small local safeguard:
`parentItem()` on a parentless scene-owned item changes `shiboken6.ownedByPython`
from false to true. Dropping that temporary wrapper can delete the item. This was
reproduced with a scene containing a single line and also caused missing cards/guides
in initial regressions. The ancestry precheck avoids unrelated parentless card roots;
stacking reasserts scene ownership with `addItem()` after parentless parent lookups.
Adding an item already in the same scene preserves its placement/stacking. Regression
checks assert card and guide retention, and independent pixel comparisons verify the
final same-Z order. No general Qt ownership framework or card-lifetime redesign is added.

## Geometry availability and unknown values

Unavailable geometry removes active roots and their complete subtrees, clears the
active profile reference, and retains the existing readiness failure. Recovery
creates exactly one fresh set for the selected style. This intentionally prevents
stale marks during invalid/mixed/over-capacity transient states. A valid empty page,
including a zero-capacity empty page, retains its selected profile at the margin frame.

| Boundary | Unknown value behavior |
|---|---|
| Global preference validation | Existing default reset |
| Document settings loader | Existing assertion/rejection |
| Runtime in-memory style lookup | Disabled profile; original string unchanged |

Screen, FILE_EXPORT and active NATIVE_PRINT scenes pass zero scene X offset. Legacy
bare ON_PAPER (including its IMPLICIT_MARGINS path) retains its earlier scene offset,
applied before Bullseye's multiplier. M05/M06 render transformations are unchanged.

## Automated validation

Actual tools: CPython 3.13.14 (64-bit Windows), PySide6/Qt 6.11.2, pytest 9.1.1,
Pint 0.24.4, pypdf 6.19.0, Poppler 26.07.0. No dependency or persisted preference,
default, OS locale, schema, migration or reference-fixture changes were made.

Exact final command from repository root (older fixtures use in-memory A4
normalization; new profile fixtures configure paper explicitly):

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
    'tests/page_scene/test_registration_profiles.py',
    'tests/test_registration_profile_compatibility.py',
    'tests/test_pdf_export.py',
    'tests/test_print.py',
    'tests/test_export_mapping.py',
    'tests/test_native_print_mapping.py',
    'tests/page_scene/test_page_scene.py',
    'tests/ui/test_page_config_widget.py',
    '-q', '--timeout=30', '--tb=short', '--show-capture=no',
    '--basetemp=.m01-output/m07-final',
]))
'@ | & .\venv\Scripts\python.exe
```

**1,065 passed in 50.06 seconds.** This includes 17 new profile cases, 9 compatibility
cases (current-schema SQLite with both query-order modes), 28 adoption cases,
20 geometry, 55 layout, 2 PDF smoke, 6 print/UI, 22 measured export, 42 native,
756 existing scene and 108 page-configuration cases. Tests use local synthetic images;
no Scryfall, network loader matrix or hardware job was needed.

New checks cover exact identities/types/order, fresh roots, scale/origins/polygon/
rotation/anchors, portrait/landscape/inch custom paper, fractional margins, all scene
offset modes, actual regular/oversized/partial/empty target pages, repeated style/
layout/page/undo/redo/reflow/recovery changes, and unknown values. An injected test-only
four-root profile uses line/text/rectangle/group roots plus nested line/text; ownership,
unaltered presentation, bounded recovery, complete removal and same-Z stacking pass.
Three independent QImage comparisons reproduce M06's six-item construction with
inactive opacity zero and a contrasting guide, verifying output for every style.

Actual settings persist via `ActionSaveDocument.save_settings()` and load via
`DocumentLoader._load_document_settings()` against current document-v7 in-memory
SQLite databases. All three strings round-trip unchanged, stay strings in UI/model,
and retain invalid-global reset and invalid-document rejection. No serialization code
was modified. M03–M06 numeric output tests retain their original content tolerances,
including M06 Standard-mode backend-origin residual accounting.

## Output and visual inspection

Synthetic two-page A4 PDF/PNG jobs for Disabled, Bullseye and Silhouette were generated
with local colored cards, cyan guides and an empty second page. Each PDF has two pages;
each first-page PNG is 2480 × 3508. PDFs were independently rendered with:

```powershell
pdftoppm -f 1 -singlefile -scale-to 900 -png <input.pdf> <output-stem>
```

The local image viewer was used to inspect the PDF/PNG comparison and portrait/rotated
marked native examples. Card orientation, enabled/disabled marks and guide layering
agree with the preserved presentation. Very thin guides can disappear in reduced PNG
thumbnails; configured guide-width units remain the existing separate follow-up.
The native portrait title/mark overlap and edge clipping are also retained.

Preserved M06 and final M07 `test_marked_native_software_example` PDFs were independently
rendered with Poppler at 900-pixel maximum extent. Both portrait and clockwise-rotated
images are **pixel-for-pixel equal**. pypdf-resolved image placement arrays are also
exactly equal in both cases (five image operations: two cards and three Bullseye marks).
Current Bullseye output can be image objects in this PDF path; registration output is
not universally vector. Review artifacts are ignored/local under
`.m01-output/m07-review`; test artifacts are under `.m01-output/m07-final`.

Native computer controls are disabled in the available automation surface. Interactive
native preview/export selection was unavailable and was not performed. Real offscreen
Qt widget tests and software output checks do not constitute native interactive or
physical qualification. No printer/cutter job was submitted.

## Architect handoff and M08 gate

The only implementation accommodation is the demonstrated local Qt ownership safeguard;
there is no unresolved implementation blocker. The M06 pinned `pageMatrix()` source link
was corrected to `qpdf.cpp#L3557`; its measurements and acceptance are unchanged.

`RegistrationProfile` is currently a registration-rendering interface. If validated
Siser workflows later require identical registration geometry in SVG, that profile
may consume a shared measured geometry module. M07 does not invent that format, add
Siser identities/marks or imply optical recognition, cutter alignment or physical scale.

M07 remains unmerged for architect review. M08 was not started. Leonardo numeric
placement remains pending; older direct Print & Cut and newer Artwork Only evidence
stay distinct, unchanged acquisitions. The prerequisites and measured Siser gate in
[siser-reference.md](siser-reference.md) remain open. Deferred follow-ups retain their
existing scope: native/physical qualification, output-job threading/reentrancy,
print-count timing and guide-width units. No SVG/import compensation, calibration UI,
layout, output-renderer or image-enhancement change is included.

# M13 — Explicit-frame Siser Page Marks and geometric clearance

Accepted M12 `d5a49fa2cba3d246d445b210c6ca88d982517042` was fast-forwarded
into `siser-dev` and pushed before creating `milestone/m13-siser-mark-geometry`.
M13 is left unmerged for architect review; local/remote trunk remain
`00202f988bef071c7070967e86c1490cb19114ba`. Historical square/rounded proofs,
acquisitions, comparison records and operator observations remain unchanged.

## API and coordinate spaces

`mtg_proxy_printer.siser_page_marks` adds these frozen records:

- `RegistrationBar(bar_id: str, bounds_mm: Rectangle)`.
- `SiserPageMarks(frame_width_mm: float, frame_height_mm: float, bars: tuple[RegistrationBar, ...])`.
- `MarkClearanceIssue(code: str, slot_index: int | None, bar_id: str | None)`.

The existing frozen Rectangle is reused; nested bars/issues are tuples and numeric
records, not mutable graphics objects. Functions:

```python
build_siser_page_marks(frame_width_mm: float, frame_height_mm: float) -> SiserPageMarks
assess_siser_clearance(geometry: PageGeometry, marks: SiserPageMarks,
                       *, extra_clearance_mm: float = 0.0) -> tuple[MarkClearanceIssue, ...]
```

Nominal sheet dimensions come from PageGeometry. The registration frame is
explicit caller input, anchored at the nominal sheet's top-left (X right/Y down).
An output-format boundary, such as Qt's quantized PDF box, is a third concept.
The builder does not derive its frame from artwork, occupancy/grid, margins,
rounded canonical pixels, output-device dimensions or cutter observations.
Neither function shifts, resizes, recenters or reflows cards. Automatic resolution
of named/custom paper settings to supported Leonardo frames is deferred.

No QApplication, scene, painter, document, image database, printer, settings
lookup, file I/O or background task is involved in these calculations. Runtime
code never reads the test fixtures or copies a reference page. Rendering and
existing registration profiles/IDs, settings and serialization are unchanged.

## Measured convention and mathematical constraint

Named constants are construction inset 6 mm, length 13 mm and thickness 1 mm.
For supplied W/H, the stable tuple order and coordinates are:

| Bar ID | x | y | width | height |
|---|---:|---:|---:|---:|
| top-left-horizontal | 6 | 5.5 | 13 | 1 |
| top-left-vertical | 5.5 | 6 | 1 | 13 |
| top-right-horizontal | W − 19 | 5.5 | 13 | 1 |
| top-right-vertical | W − 6.5 | 6 | 1 | 13 |
| bottom-left-horizontal | 6 | H − 6.5 | 13 | 1 |
| bottom-left-vertical | 5.5 | H − 19 | 1 | 13 |
| bottom-right-horizontal | W − 19 | H − 6.5 | 13 | 1 |
| bottom-right-vertical | W − 6.5 | H − 19 | 1 | 13 |

Values stay floating-point mm, without pixel rounding or display formatting.
Each dimension must exceed 2 × (6 + 13) = 38 mm: at 38, opposing groups touch.
This is a geometric separation constraint, not a minimum cutter media size.
Nonfinite/invalid dimensions and derived nonfinite rectangles fail with ValueError.
The API also rejects nonnumeric/bool values and derived padding overflow.

This convention has provenance in M08B's nine-card Letter baseline and M08A's
numeric-placement records. It is neither a definition of camera anchors nor a
manufacturer optical clearance specification. Custom-spacing/artwork-relative
mark modes are not extrapolated.

## Conservative clearance diagnostics

The checker uses each **occupied** placement's existing `bleed_envelope_mm`.
It does not recalculate bleed, fill empty slots, inspect opaque image pixels,
consult corner-radius policy or relax the envelope for M12's rounded cuts.
Rounded trimming does not prove the printed corner or bleed is empty.

| Code | Attribution and condition |
|---|---|
| registration_bar_outside_sheet | Bar ID; an actual bar extends beyond the nominal sheet |
| printed_envelope_outside_sheet | Slot index; an occupied envelope extends beyond the nominal sheet |
| printed_envelope_intersects_mark | Slot and bar; overlap or contact with actual bar ink rectangle |
| printed_envelope_within_requested_clearance | Slot and bar; no actual contact, but overlap/contact with that bar expanded by requested padding |

Checks use individual horizontal/vertical rectangles, not an L-mark's bounding
box. Additional padding is finite/nonnegative, axis-aligned expansion on all four
sides of each bar; it is not a radial optical search area. Actual contact wins
over a duplicate padded issue for the same pair. Padding does not move bars or
alter the envelope. Sheet-edge contact is contained; extension beyond the edge
is outside. A declared 1e-9 mm comparison epsilon handles floating-point boundary
noise; it is not a hardware allowance or physical measurement tolerance.

Issue order is deterministic: out-of-sheet bars in supplied order, then occupied
placements in snapshot order, each with its sheet issue followed by bar-pair
issues in supplied bar order. Slot/bar associations are retained; nothing is
silently clamped, dropped, compensated or reconfigured.

An empty tuple means only **no conflicts detected within the modeled geometry
and requested padding**. It does not qualify camera clearance or guarantee a safe
cut. Full-page manual guides, titles/page-number text, watermarks/other graphics,
actual device clipping and hardware margins are outside this model. The next
integration milestone must handle those scene objects before presenting a
production output-readiness result; M13 neither traverses nor disables them.

## Reference agreement and focused verification

Direct corresponding-bar comparisons use existing JSON records, without fitting
translation, rotation, scale or a correction. Full available frame precision is
retained. All eight (x,y,width,height) tuples agree within 0.001 mm absolute,
relative tolerance disabled:

| Evidence | Explicit registration frame (mm) | Maximum absolute difference (mm) |
|---|---|---:|
| M08B nine-card Letter reference | 215.9 × 279.4 | 0.000025838332646799245 |
| M08A letter-base-initial | 215.89999999999998 × 279.4 | 0.000025838332646799245 |
| M08A a4-page-before-import | 296.96832909999995 × 209.97333756666666 | 0.00002778797107794162 |

A4's nominal landscape document sheet remains **297 × 210 mm**. The recorded
emitted frame supports the observed bar-construction rule, not a universal
Leonardo conversion for every version, named paper or custom size. These tests
read vector-measurement evidence; no acquisition/PDF was regenerated or remeasured.

Mathematical tests cover portrait, landscape and custom frames, stable IDs/order,
changing W only moving right bars and H only moving bottom bars, immutability,
invalid inputs and too-small frames. Layout count/spacing/margins/bleed and corner
policy do not influence marks for the same frame. Their envelope consequences
are tested separately, including empty/partial occupancy and outside attribution.

The dense Letter nine-card layout has no direct collision with zero bleed and
zero padding. Configured 1.5 mm bleed has actual rounded exterior bleed **1.524 mm**
(18 canonical pixels), reporting horizontal-bar contact at slots 0, 2, 6 and 8.
A four-card layout with 12 mm margins and 1.5 mm bleed passes without moving marks.
Synthetic tests cover overlap, exact contact, numerical just-outside cases,
requested padding, actual-contact precedence, the L interior and diagonal
axis-aligned padding. No numerical physical cutter tolerance is inferred.

Exact selection from repository root:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_siser_page_marks.py -q --timeout=30 --tb=short
git diff --check
```

Final result: **51 passed in 0.20 seconds**. Initial 48 cases passed; three overflow
input cases were added and the focused module rerun before documentation edits.
Versions: Python 3.13.14, pytest 9.1.1, PySide6/Qt 6.11.2, Pint 0.24.4. A separate
numeric summary confirmed the reported maxima and no QApplication instance was
created. Installed versions were read from module attributes because PySide6
distribution metadata was unavailable; no dependency changes were made.
Whitespace and final changed-file review passed. Unrelated suites, M12/M08B
matrices, acquisition scans, PDF generation, Leonardo runs and hardware jobs
were skipped. Passing tests were not repeated after documentation-only edits.

## Next integration boundary

Next work must explicitly resolve supported Leonardo frames, consume this one
bar calculation in a selectable profile/output path, and combine envelope
diagnostics with scene objects and hardware/output limitations. M13 adds no
profile identity, UI controls, settings/migrations, exporter or printer changes,
automatic layout adjustments or machine-specific offsets/scaling.

Mathematical support for arbitrary valid frames is distinct from Letter/A4
software-reference evidence and M08B's accepted operator-reported square Letter
physical comparison. Other layouts, optical clear zones, bleed configurations,
rounded physical cutting, equipment and Romeo are not automatically qualified.
The milestone stops at its unmerged review handoff.

# M14 — Experimental Siser profile and faithful calibration export

Accepted M13 `a503c0ca3ae97c40b98024ae8af68218528d9995` was fast-forwarded
into `siser-dev` and pushed before creating `milestone/m14-siser-profile-calibration`.
No M13 acceptance tests were rerun for integration. M14 remains unmerged for
architect review. Local/remote trunk remain
`00202f988bef071c7070967e86c1490cb19114ba`.

The replacement M14 prompt explicitly **does not enforce registration clearance**.
It supersedes the earlier M14 clearance-blocking proposal. Faithful software
output does not certify optical recognition or physical cutting accuracy.

## Identity, persistence and dependency boundary

`RegistrationProfileId.SISER.value` is the plain string `Siser`. The translated
selector appends `Siser Page Marks (experimental)` after Disabled (`None`),
Bullseye (`Bullseye`) and Silhouette (`Cut marker`). Their order, translation
contexts, defaults and unknown-value behavior remain unchanged. Settings validation
already derives its set from the enum, so settings.py needs no change. The actual
existing document settings save/load and UI boundaries round-trip the new string;
there is no new field, document migration or schema change. An older application
that predates this identity may reject a document containing `Siser`.

`siser_registration.py` depends only on geometry, the identity, M13 calculation,
math and Qt's translation hook. It imports no calibration, printing, UI or live
scene constructors. The resolver reads no fixtures, preferences, PDF boxes or
physical error observations. Existing M13 calculation/assessment APIs, M12
contours and SVG serialization are unchanged.

## Explicit supported frames

`resolve_siser_page_marks(geometry)` identifies nominal sheet dimensions with
1e-6 mm absolute tolerance, zero relative tolerance, then calls the one accepted
M13 builder with these frames:

| Nominal sheet (mm) | Registration frame (mm) |
|---|---|
| Letter portrait: 215.9 × 279.4 | 215.9 × 279.4 |
| A4 landscape: 297 × 210 | 296.96832909999995 × 209.97333756666666 |

Equivalent custom nominal dimensions resolve to the same entries. Other sizes,
orientations and nearby dimensions reject with requested dimensions and supported
configurations in the ValueError. No transposition or aspect-ratio inference is
used. The A4 entry is the observed Leonardo 1.1.31 frame, not a universal A4 rule
or equipment correction. The identification tolerance is floating-point identity
housekeeping, not fitting or a physical accuracy allowance.

Nominal A4 remains 297 × 210 mm. Its registration frame stays separate from Qt's
842 × 595 point output boundary (approximately 297.038889 × 209.902778 mm).
Neither the cards nor marks are resized/recentered to reconcile these values.
M11's unrotated physical mapping and normal page clip remain in effect.

## Graphics, preview and recovery

The stateless adapter creates eight fresh, unparented QGraphicsRectItem roots in
M13 bar order. Each has black fill, no pen, the existing CUT_LINES_BELOW Z layer,
unit scale and zero rotation. Placement uses resolved bars and the existing
nonrounding mm-to-canonical conversion for positions, 13 mm lengths and 1 mm
thicknesses. No independent mark formula or legacy item-specific anchor adjustment
exists. A nonzero legacy horizontal offset rejects explicitly.

PageScene retains generic root ownership, registration selector exclusions and
equal-Z guide stacking. Supported geometry refreshes reuse/reposition roots.
An expected Siser placement error clears stale/partial roots and records
`registration_unavailable_reason`, separately from `geometry_unavailable_reason`.
Valid page/cards remain editable with no hidden fallback to Disabled or paper
change. Supported-frame/style selection recovers; empty valid pages retain marks,
and invalid page geometry clears roots until normal recovery. Overlap is not
registration unavailability.

Siser screen preview uses white paper and black marks. Palette changes preserve
that paper choice; switching away restores the existing theme background. No
white patches, masks or overlap-specific stacking changes are added. The selector
tooltip explains the experimental Leonardo 1.1.31 scope, calibration-only stage,
unchanged layout and the need for visible/unobstructed marks without claiming
optical clearance. Loading and retained-dialog behavior are unchanged.

## Calibration without mandatory clearance

The existing current-page action captures geometry/style and prepares a static
calibration PDF before opening the existing safe-save dialog. Siser frame resolution
occurs before static scene construction; the adapter places its eight bars in that
scene. Regular contours retain the shared 2.5 mm radius; oversized contours stay
square. Labels, centre crosses, 50 mm rulers, empty-page behavior, pen and physical
mapping retain accepted behavior. Bleed is not painted onto calibration sheets.

`assess_siser_clearance()` remains available unchanged as a possible future
optional diagnostic. Preview/export do not call it. There is no mandatory overlap
warning, acknowledgement, clearance preference, padding, containment inspection,
white exclusion zone or layout adjustment. Known bleed-envelope conflicts, an
additional line contacting a mark, and valid graphics extending beyond the page
can export. Normal clipping may hide off-page ink without fitting the design.

Genuine errors remain observable: unsupported frames, invalid/nonfinite usable
paper, invalid resolution, rejected paper setup, painter/render/finalization and
file write/commit failures. Existing cleanup and prepared-byte saving are unchanged.
Non-Siser calibration gains no new restrictions.

## Temporary artwork-output restriction

`require_artwork_output_supported(style)` is the one shared, explicit-style policy
helper. Siser ordinary PDF/PNG/native printing/native preview report:

> Siser Page Marks currently supports calibration PDF export only.
> Use "Export current page calibration sheet (PDF)" or choose another registration style for ordinary printing/export.

Main-window checks precede compaction, missing-image acquisition and task dispatch.
PDF checks precede destination-writer construction and run/page painting; PNG checks
precede locking/encoder submission and report error plus task completion; native
checks precede scene construction/painter startup and run/page submission. Current
style is rechecked at execution rather than relying only on construction-time state.
Existing profiles remain enabled. Calibration, editing and mark-free SVG export
are not gated.

One extra production file, `ui/dialogs.py`, has a narrow SavePNGDialog acceptance
guard: an operation that has become Siser cannot submit its renderer or print-count
task. The safe file-writing helper is untouched. Generic asynchronous output
reentrancy and pre-existing print-count timing are not redesigned in M14.

M15 must integrate ordinary artwork output before removing this restriction,
including its guides, labels, watermarks, backgrounds, clipping and job lifecycle.
M15 must also preserve the no-mandatory-clearance-enforcement decision; this
temporary capability guard is not a geometric safety policy.

## Focused tests and measured results

Existing Python 3.13.14, PySide6/Qt 6.11.2, pytest 9.1.1, pypdf 6.19.0,
Pint 0.24.4, Poppler 26.07.0. A copied child environment set
`QT_QPA_PLATFORM=windows` and prepended venv/Scripts to PATH, leaving persistent
configuration and dependencies unchanged. Exact test selection:

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests/test_siser_registration.py `
  tests/test_registration_profile_compatibility.py `
  tests/page_scene/test_registration_profiles.py::test_composite_ownership_stacking_recovery_and_switching `
  tests/page_scene/test_registration_profiles.py::test_render_matches_accepted_six_item_construction `
  tests/test_calibration_pdf.py::test_fresh_existing_registration_and_empty_pdf `
  -q --timeout=30 --tb=short
git diff --check
```

Final selection: **36 passed in 7.00 seconds**. The initial 35-case run passed;
actual empty/invalid model-notification recovery and global-setting validation
were strengthened before the final run. No tests were repeated after docs edits.

Tests cover both/custom frame mappings and unsupported orientations, immutable
snapshots, eight fresh fractional bars, no legacy offset, palette/style recovery,
actual model-notification recovery, all persisted identities and unknown values,
ordinary-output side-effect guards and a successful existing-profile ordinary PDF.
They exercise calibration/SVG access through the real action flows, PNG dialog
task/count rejection, and current-style rechecks after renderer creation.

Actual calibration PDFs, independently read with pypdf, have eight filled bars
agreeing with the resolved calculation within 0.001 mm absolute (relative zero):

| Output | DPI | Maximum bar difference (mm) |
|---|---:|---:|
| Letter | 300 | 0.00000054666668347636 |
| Letter | 600 | 0.0000001233333932759706 |
| A4 landscape | 300 | 0.0000005466666550546506 |
| A4 landscape | 600 | 0.00000012333336485426116 |

Each regular trim has four cubic quarters; measured bounds preserve the snapshot
within 0.01 mm. Letter's first trim is (13.335, 7.620, 63.0766666667,
88.0533333333) mm at both DPIs, and rulers measure 49.999999991333326/
49.99999999133332 mm. A4's first trim is (22.352, 16.9333333333, 63.0766666667,
88.0533333333) mm; rulers remain 50 mm within the existing 0.01 mm tolerance.
Oversized Letter retains square contours with no cubic corners.

A valid nine-card layout with configured 1.5 mm bleed exports, unchanged. A
synthetic 0.1 mm line at y=6 mm from x=-2 to x=20 contacts the top-left bar and
extends past the nominal page; it also exports unchanged and clips normally.
A patched assessment function would raise if called, but export succeeds. Direct
file measurements confirm unchanged trim coordinates rather than just comparing
two helper invocations. These are software representation tolerances, not cutter
accuracy claims.

## Visual review and qualification limits

Ignored `.m01-output/m14-review/` contains Letter/A4 PDFs at 300/600 DPI, an
overlapping Letter proof, an oversized proof, SHA-256/measurement JSON and preview/
selector PNGs. Historical acquisitions and square/rounded proofs were untouched.
Independent Poppler full-page renders of Letter and A4 and an overlap crop were
inspected. Black bars, readable labels/rulers, rounded regular and square oversized
contours are present. The added overlapping line remains visible through the mark
and stops at the page edge by normal clipping; no design correction was applied.

Windows Qt programmatic widget captures show the selected experimental option,
white-paper supported/recovered preview, and marks absent on unsupported A4
portrait while valid page geometry remains available. Synthetic preview images
are transparent; card item presence/positions are verified by the lifecycle tests.
The temporary route error was inspected as text and tested through the handlers.
These captures are noninteractive checks, not native Save/Cancel/device-dialog
verification. Native interactive controls were unavailable and remain unverified.

No physical job, Leonardo acquisition, M08B repetition, M13 matrix, full scene/UI
suite, network loader or unrelated application tests ran. M08B's accepted square
Letter comparison remains accepted for its tested job. This milestone does not
qualify universal optical clear zones, rounded physical cutting, equipment or
Romeo. No hardware-derived correction, reflow, new schema or hidden fallback
exists. M15 and new hardware qualification were not started.

## M15 current-status update

The preceding M14 calibration-only restrictions and "M15 not started" statements
are historical handoff records. M15 now enables supported Siser ordinary PDF,
PNG, native printing and native preview with explicit registration output readiness.
The reference frames and no-mandatory-clearance decision remain unchanged.
See [Siser artwork output](siser-artwork-output.md) for implementation, focused
measurements, UI compaction policy, lifecycle limits and qualification scope.

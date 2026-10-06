# M02: Leonardo reference and SVG import evidence

**Status: Complete — negative workflow finding; M08 registration gate remains.** Recorded 2026-10-05,
America/Los_Angeles. Six user-exported PDFs have been preserved and measured.
Three native .lds projects and two settings screenshots are preserved. These
measurements do not establish a general registration specification.
Measured Siser registration support remains gated before M08.

## Repository and scope

Accepted M01 `014156d9c4dfc16fe67e443220443c1db239d12b` was verified against
`origin/milestone/m01-bootstrap`, fast-forwarded into `siser-dev`, and pushed.
M02 is on `milestone/m02-leonardo-reference`, based on that accepted commit.
`trunk` remains at `00202f988bef071c7070967e86c1490cb19114ba`.
M02 is not merged into `siser-dev`.

The baseline now says logger, user agent, selected UI text, and PDF creator
metadata; the main window title remains unchanged. Its A4 diagnostic now has
repository-path instructions and failed-selection regeneration steps. Historical
test results remain unchanged. No full suite was rerun, dependencies changed,
application modules modified, or application settings imported during M02.
The existing Python environment and fork storage isolation were preserved.

## Availability actually established

| Item | Evidence/result |
| --- | --- |
| Installed software | Windows uninstall record: Leonardo Design Studio 1.1.31, publisher Future Corporation |
| Executable metadata | File/product version 1.1.0.31; not an About-dialog observation |
| OS | Windows NT 10.0.22631.0 |
| Agent interaction | Computer-use app inventory found the installed app; launch returned “launched app did not expose a targetable window”; one follow-up window inventory found no Leonardo window |
| User-reported version | 1.1.31 |
| Edition / selected software profile | Unknown; user owns a Siser Juliet |
| Juliet/Romeo profile selection | Neither observed; availability/equivalence unknown |
| Custom spacing | User reports .492 inches (12.4968 mm), smallest accepted in their setup; which outputs used it is unknown |
| Output route | User reports Leonardo Export tool; native save remains pending |
| Small Page Margins | User reports disabled on printer; Leonardo preference remains unconfirmed |

This establishes installed software and an interaction limitation, not that the
software is broken. No installation, trial, account change, purchase, or support
contact was attempted. Reference acquisition therefore requires manual operation.

## Authoritative documented evidence

Sources accessed 2026-10-05. The English manual is **1.1.8 / 2023**, older than
the installed version; its behavior must be checked in the user's actual edition.

| Classification | Finding | Source |
| --- | --- | --- |
| Documented | SVG import offers Print & Cut, Cut only, or Editable Artwork. | [Developer/Siser manual, pp. 20–22](https://fcl.software/lds/manual/LDS_Manual_EN_1.1.8.pdf#page=20) |
| Documented | Page Marks use the selected page boundary; custom mark spacing surrounds artwork. Red marks warn of insufficient space. | [Manual, pp. 50–51](https://fcl.software/lds/manual/LDS_Manual_EN_1.1.8.pdf#page=50) |
| Documented | Small Page Margins changes edge proximity; no numeric minimum is established here. | [Manual, pp. 15–16](https://fcl.software/lds/manual/LDS_Manual_EN_1.1.8.pdf#page=15) |
| Documented | Printing exposes size, orientation, scale, and recenter controls; avoid automatic fitting during comparison. | [Manual, pp. 52–53](https://fcl.software/lds/manual/LDS_Manual_EN_1.1.8.pdf#page=52) |
| Documented | Layer Marks belongs to Cut-only positioning, not a demonstrated substitute for optical Print & Cut registration. | [Manual, pp. 53–54](https://fcl.software/lds/manual/LDS_Manual_EN_1.1.8.pdf#page=53) |
| Documented | Siser lists SVG import for Basic and Pro; artwork export including PDF is listed under Pro. Installed availability remains unknown. | [Siser feature comparison](https://www.siser.com/leonardo-design-studio/) |
| Documented | Siser describes Romeo's registration camera and Leonardo pairing with both cutters. This does not prove equal registration geometry. | [Siser Romeo](https://www.siser.com/cutter/romeo/) |

The PDFs provide output observations below; direct native import observations remain pending. The sources reviewed do not
establish a numeric registration specification usable for this implementation.
Illustrations are not calibrated measurements. No recommended margin is promoted
to a minimum; no marketing accuracy specification is treated as finished-cut accuracy.

## Diagnostic fixtures and known values

Files and acquisition instructions are in
[`tests/data/print_cut/siser`](../../tests/data/print_cut/siser/README.md).
The manifest records probe SHA-256 hashes, expected geometry, and ten pending
reference runs: five per requested cutter profile. A requested profile/mode is
an acquisition target, **not** an observed setting. Unavailable measurements,
settings, native files, and import results are explicit nulls.

| Probe | Declared page, mm | Occupied geometric bounds, mm `(left, top, right, bottom)` |
| --- | --- | --- |
| Letter portrait | 215.9 × 279.4 | `(25, 35, 169, 208)` |
| Letter portrait shifted | 215.9 × 279.4 | `(37, 44, 181, 217)` |
| A4 landscape | 297 × 210 | `(35, 25, 254, 163)` |

Each contains a 30 × 20 mm rectangle, 14 mm diameter circle, 25 × 25 mm
right-triangle bounds, and a 24 × 18 mm L-shaped polygon. Vertex/center
coordinates are in the manifest. Shapes are filled black without strokes, making
their geometric bounds unambiguous. Their unequal distribution detects rotation
or mirroring. There is no page-border contour, artwork, font, script, external
asset, or proposed Siser mark.

Both Letter probes have identical shapes and page extents; every vertex/center
in the variation moves **+12 mm right, +9 mm down**, with unchanged distances
and occupied width/height. They isolate translation from page size and scaling.
These positions are diagnostic inputs, not a claim about usable cutter margins.

The matching physical viewport and `viewBox="0 0 W H"` intend one user unit per
millimetre, top-left origin, x right and y down. This follows
[SVG coordinate mapping](https://www.w3.org/TR/SVG2/coords.html); it does not prove
that Leonardo preserves the viewport. The [CSS unit relations](https://www.w3.org/TR/css-values-4/#absolute-lengths)
give `1 in = 25.4 mm = 96 px = 72 pt`. Letter corresponds to 612 × 792 pt.
An unintended 72/96 interpretation would yield 0.75 or 4/3 scale; that is a
diagnostic hypothesis, not observed behavior.

## Reference measurements to make after acquisition

Preserve the originals before measuring; hash the exact native files, PDFs, and
settings screenshots/record. Store them under `references/<run-id>/` and update
manifest paths relative to the fixture directory. `references/**` is marked
binary for Git to prevent newline conversion in text-based native files.

For each PDF, record MediaBox, CropBox, any other page boxes, `/Rotate`, `/UserUnit`
(including an absent/default value), and relevant content/Form XObject matrices.
Resolve those matrices before comparing vertices. Establish the displayed page's
origin and axes explicitly; do not assume a landscape page is an unrotated wide box.

For an unrotated page box `[x0, y0, x1, y1]` in PDF user space with UserUnit `u`,
after applying content transforms, the planned top-left millimetre mapping is:

```text
x_mm = (x - x0) * u * 25.4 / 72
y_mm = (y1 - y) * u * 25.4 / 72
page_width_mm  = (x1 - x0) * u * 25.4 / 72
page_height_mm = (y1 - y0) * u * 25.4 / 72
```

Rotation/cropping require their own explicitly recorded transform before this
comparison. The validator checks scalar conversion round-trips; **no acquired
PDF transformation or geometry has been checked**, because no output exists yet.
Do not use screenshots to assert physical measurements without calibration.

Record every mark's primitive type/count, vertices or circle center/radius,
filled/stroked appearance, stroke width, geometric and painted bounds, distances
to page edges, and positions relative to the probe shapes. Describe any extra
orientation symbol separately. A measured bounding box is not an established
cutter anchor: anchor semantics stay unknown until supported by evidence.
Record exclusion regions and custom-size control constraints exactly as exposed.

Compare Page Marks and fixed-spacing Letter pairs separately. If shapes and marks
both shift by `(12, 9)`, that supports artwork-relative placement. If shapes shift
but marks remain fixed, that supports page-relative placement. If neither shifts,
investigate recentering/cropping. These are **inferred diagnostic interpretations**,
not current findings; screenshots alone cannot settle the physical coordinates.

## Initial output observations (partial evidence)

Six byte-identical snapshots are preserved under `references/`; their
SHA-256 digests and received filenames are in `manifest.json`. Reproduce the
measurements with `measure_initial_outputs.py` using a PDF runtime containing
pypdf, pdfplumber, Pillow, and numpy. `initial-measurements.json` records tool
versions, hashes, page boxes, transforms, every vector bar, and raster bounds.
Original incoming files are preserved separately and are not rewritten.

**Observed in exported PDFs:** Letter pages measure 215.9 × 279.4 mm. A4 is
landscape, approximately 296.9683 × 209.9733 mm, reflecting its PDF point dimensions.
All three are unrotated with default UserUnit 1. Each has eight filled, unstroked
rectangles forming four corner L marks. Letter bars measure approximately
13 × 1 mm; the top-left union spans (5.5, 5.5) to (19, 19) mm. Geometric bar
intersections are not proven camera anchors. The JSON records A4 bars individually.
No separate orientation symbol appears among these vector objects.

The probe artwork in these PDFs is raster image content. In the base Letter
capture, the intended 30 × 20 mm rectangle measures approximately 26.46 × 17.72 mm
at (44.58, 63.48) mm, rather than its source position (25, 35) mm. Raster bounds
use dark pixels below grayscale 128, with roughly 0.123 mm pixel pitch; they are
approximate painted bounds, not measurements of native cut contours. A4 artwork
also differs from the declared source size and placement.

The shifted Letter capture contains two raster images, including the original
artwork and the shifted artwork. It therefore cannot establish how a single
import's translation affects registration. Acquire each comparison in a fresh
blank project. The observed discrepancies warrant investigating import, resizing,
placement, and export settings; their cause is **unknown**, not an established
Leonardo import rule. The user reports custom spacing was changed at one point,
so spacing cannot yet be assigned to a particular captured PDF.

**Unknown:** actual import mode, native shape dimensions/positions, selected
software profile, edition, per-output mark setting, save/reopen preservation,
contour correspondence, exclusion limits, and camera anchors. The reported .492
inch value is an observed user setting, not a universal minimum. No conclusion
about Juliet/Romeo equivalence or printed-sheet alignment follows from these PDFs.

## Revised shifted and custom-off captures

The user supplied a replacement shifted Letter export and two files labelled
“Custom Mark Off”. These are preserved as separate hashed snapshots; the earlier
mixed-artwork capture remains available as historical evidence.

**Observed:** the revised shifted export contains one raster artwork image.
Its rectangle begins at approximately (44.5835, 63.5380) mm, compared with
(44.5766, 63.4773) mm in the base export: only (+0.0068, +0.0607) mm, within
raster measurement uncertainty, instead of the source translation (+12, +9) mm.
Page-relative marks match between these two captures. This provides evidence
that this export sequence did not preserve absolute source placement; the
responsible import/placement/export step remains unknown without native projects.

Both custom-off captures still contain eight filled rectangular mark bars.
Their artwork image placements match the corresponding page-mark captures.
The base top-left horizontal mark spans approximately x=32.0712–45.0712 mm,
y=50.5000–51.5000 mm; the shifted capture spans x=32.0096–45.0096 mm at the
same y coordinates. Thus the marks move inward relative to the page-mark
captures, and barely move between the base and shifted exports. This is
consistent with marks surrounding nearly coincident artwork, but does not prove
translation behavior when Leonardo preserves the intended source coordinates.
The user identifies “Custom Mark Off” as custom spacing disabled; the exact
per-file numerical spacing remains unconfirmed. Do not interpret these as registration-free PDFs.

## Native projects, settings, and confirmed acquisition workflow

Three .lds files are preserved beside their corresponding base Letter, revised
shifted Letter, and A4 PDFs. SHA-256 digests are in the manifest. Each is a ZIP
container with CDOC binary data and a PREVIEW entry. The internal geometry was
not decoded, and these files were not opened/reopened by the agent. No project
files were supplied for the two custom-spacing-disabled outputs.

Two preserved screenshots show Page Marks enabled, Print and Cut enabled, and a
disabled Mark Spacing field displaying 0.492 in. Letter settings show 8.500 ×
11.000 in and printer portrait; A4 settings show 11.692 × 8.267 in and printer
landscape. The printer shown is Epson ET-8550. The screenshots do not establish
edition, selected cutter profile, Small Page Margins preference, export scaling,
or native object dimensions.

The user confirms dragging and dropping each SVG, selecting Print and Cut,
and neither resizing nor repositioning. The user also clarifies that the toggle
changes custom spacing, while registration marks remain required for Print and
Cut. Accordingly, the two “Custom Mark Off” outputs are interpreted as custom
spacing disabled, not registration disabled. Their filenames remain unchanged.

**Decision supported by available evidence:** the supplied drag-and-drop Print
and Cut → Leonardo Export sequence does not preserve the intended physical size
and absolute page placement in its exported PDFs. This is a reproducible negative
result across the provided probes, not a claim that every Leonardo workflow fails.
The responsible stage remains unknown; no compensation constants are justified.
Native saves and settings are preserved for a later targeted investigation.

## Import proof and workflow decision

| Required proof | Current state |
| --- | --- |
| Declared page extent / empty margins | Unknown |
| Shape sizes and relative distances | Known only in the source SVG; Leonardo preservation unknown |
| Absolute placement, rotation, mirroring | Unknown |
| Generated mark dimensions, anchors, constraints | Initial PDF bar dimensions measured; anchors and constraints unknown |
| Mark behavior after translation and save/reopen | Unknown |
| Juliet/Romeo equivalence | Unknown; requires separate software references |
| Externally supplied marks: recognized, ignored, cut, or repositioned | Unknown; not included in these probes |
| Contours corresponding to an already printed page | Unproven |

**Supported conclusion: insufficient evidence for a standalone SVG workflow.**
Contours-only import with Leonardo managing registration is a candidate to test,
not a validated recommendation for pages printed by MTGProxyPrinter. A cut-only
job preserving relative contours still does not establish their alignment to a
separate printed sheet.

If import discards margins or recenters, record the exact numeric restoration
setting and demonstrate it on the shifted case. Approximate dragging, page-border
cut paths, cropping the PDF, or invented offsets are not alignment proof.
If Page Marks establishes repeatable coordinates, later work may evaluate a
native-project or documented-setting workflow. If fixed placement cannot be
reproduced, document a negative result rather than compensating with guessed marks.

External registration vectors must not be added until actual Leonardo output is
measured. A subsequent controlled test must distinguish ordinary cut geometry
from Leonardo-managed registration and capture print/cut layer assignments.
Matching appearance does not establish recognized marks or cutter anchor behavior.

**Requires hardware validation:** printed scale/skew, camera recognition and
anchor acquisition, feed/orientation, material stability, registration repeatability,
and finished-cut alignment. Software references alone prove none of these.
No physical printer/cutter operation is required or was performed in M02.

## Validation and next handoff

`venv\Scripts\python.exe tests/data/print_cut/siser/verify_fixtures.py` passes:
three parsed SVGs, page/shape coordinates, fixed translation, manifest hashes,
safe paths, and unit round-trips. It verifies **six acquired PDF reference artifacts**.
Probe line endings are pinned to LF to preserve hashes across Windows checkouts.
No full application suite was run. The diff is limited to documentation and
small reference fixtures/validation; production layout/export/settings are unchanged.

Follow the fixture README's five-sheet procedure for the available profile;
repeat for the other profile if selectable. Return native saves, uncropped PDFs,
settings records/screenshots, and import/save-reopen observations. Edition, selected software profile,
custom-size constraints, and import/save-reopen behavior need confirmation. Output
geometry is recorded separately from the still-null controlled-run observations. M02 is complete as an evidence-backed negative workflow decision. M08 geometry
remains gated on an independently demonstrated size/placement-preserving workflow.
Save/reopen checks, native dimension inspection, Romeo comparison, and external
mark handling were not performed. No further user files are required for this
M02 handoff. Do not begin M03.

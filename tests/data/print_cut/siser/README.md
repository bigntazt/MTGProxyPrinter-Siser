# M02 Leonardo reference acquisition

These are diagnostic probes, not a production cut template. There are no Siser
marks or page-border paths. Six PDFs, including the revised shifted and custom-off captures, are preserved in `references/`, with measurements in
`initial-measurements.json`. Three native projects and two settings screenshots are also preserved. M02 is
complete with a negative workflow finding; unperformed planned-run observations
remain null. The acquisition procedure below is retained for future investigation. See the
[evidence report](../../../../doc/architecture/siser-reference.md) for sources,
classifications, measurement plans, and the workflow gate.

## Five controlled sheets per available cutter profile

Start with whichever profile is available. Record the selected name exactly;
repeat with the other Juliet/Romeo profile if selectable without a purchase,
trial, account change, or physical cutter operation. Do not send a cut job.

| Run suffix | Probe | Page | Mark setting |
| --- | --- | --- | --- |
| `letter-portrait-<profile>-page_marks` | `probes/letter-portrait.svg` | Letter portrait, 215.9 × 279.4 mm | Page Marks enabled |
| `letter-portrait-shifted-<profile>-page_marks` | `probes/letter-portrait-shifted.svg` | Same | Same as preceding sheet |
| `letter-portrait-<profile>-artwork_spacing` | `probes/letter-portrait.svg` | Same | Page Marks disabled; record fixed numeric spacing |
| `letter-portrait-shifted-<profile>-artwork_spacing` | `probes/letter-portrait-shifted.svg` | Same | Exactly the same fixed spacing |
| `a4-landscape-<profile>-page_marks` | `probes/a4-landscape.svg` | A4 landscape, 297 × 210 mm | Page Marks enabled |

Use `juliet` or `romeo` in filenames. Do not alter spacing between a base/shifted
pair; otherwise movement and spacing effects become confounded. If that mode is
unavailable, report it and preserve the other outputs rather than imitating marks.

## Acquisition procedure

1. Open a **new blank project** for each run. Preserve existing user projects.
   Record About version/edition, selected profile, units, page size/orientation,
   mat/roll selection and size, Small Page Margins state, and mark settings.
   Keep every setting other than the stated probe/mode the same within each pair.
2. Import the SVG without resizing, rotation, mirroring, recentering, or dragging.
   Record the import route/options actually offered. Prefer an editable-vector
   route for inspecting dimensions; if unavailable, preserve that limitation.
   Immediately record the selected-group and individual-shape dimensions and
   numeric positions. Record whether page extent survives independently of artwork bounds.
3. Enable a Print & Cut job for the requested mark mode. If contours initially
   belong only to a cut layer, record that and the exact assignment used to make
   their filled shapes visible in print output while keeping cut contours.
   Do not create offsets, trace raster substitutes, or weld/repack the geometry.
4. Save a native project using the application's actual native suffix. Record
   the native format. Save an unmodified PDF showing the marks **and** probe
   shapes; record whether this uses printing or artwork export. If export is
   edition-restricted, use an already installed PDF printer if available; no trial.
   Confirm marks are present in that route rather than assuming exported artwork includes them.
5. For print-to-PDF, record the virtual printer and relevant driver options.
   Match the page size/orientation, use 100% scaling, disable fit/shrink/enlarge,
   and record any automatic positioning/borderless option. Do not crop, flatten,
   resize, or redraw the resulting reference. If any requirement is unavailable,
   record what actually happened instead of calling it a dimension-preserving output.
6. Save screenshots of import dimensions/positions, page/mat/mark settings,
   print preview/settings, and cut preview (without sending). Save/reopen the
   native project and record which numeric positions and settings persist.
   Open the cut preview again and note any rearrangement, mirroring, or mark changes.
7. Inspect custom-size fields once: record accepted ranges/precision and whether
   limits depend on the profile or printer. Do not infer limits from a preset list.
   Keep this as a settings record; an additional output is needed only for a specific question.

Before any correction, preserve the raw import/native/PDF result. If an exact
numeric setting is needed to restore absolute placement, save a separate
`corrected` project/output and repeat that exact method on the shifted case.
Do not accept visual hand-positioning as proof.

## Expected source measurements

Coordinates are millimetres from the page's top-left (x right, y down), not
Leonardo or cutter anchor conventions. All source geometry is black fill, no stroke.

| Shape | Letter base | Letter shifted | A4 landscape |
| --- | --- | --- | --- |
| Rectangle `(x,y,w,h)` | `(25,35,30,20)` | `(37,44,30,20)` | `(35,25,30,20)` |
| Circle `(cx,cy,r)` | `(110,80,7)` | `(122,89,7)` | `(160,70,7)` |
| Triangle vertices | `(65,150) (90,150) (65,175)` | `(77,159) (102,159) (77,184)` | `(95,120) (120,120) (95,145)` |
| L-shape bounds `(x,y,w,h)` | `(145,190,24,18)` | `(157,199,24,18)` | `(230,145,24,18)` |
| All-shape bounds `(x,y,w,h)` | `(25,35,144,173)` | `(37,44,144,173)` | `(35,25,219,138)` |

The rectangle's top-left to circle-center displacement is `(85,45)` in both
Letter cases and `(125,45)` in A4. The L's unequal arms plus the group's unequal
positions distinguish rotation/mirroring. Compare both sizes and these distances;
a successful import alone is insufficient.

## Return package and observations

For each of the five runs, provide the native save, original mark-bearing PDF,
and settings screenshots or a precise text record. Use run IDs above so files
can be paired. Also report:

- About version/edition, selected profile, whether the other profile is available,
  native suffix, output route, and custom-size constraints.
- Whether each declared page, shape size, distance, absolute position, and
  orientation was preserved; include numeric readings, not just “looks right.”
- Whether the shifted designs moved by `(12,9)` and whether marks moved with
  them; whether switching mark modes or reopening changed anything.
- Print/cut assignments and whether any imported contour is omitted, rearranged,
  or treated as additional cut geometry.

Do not include personal projects, machine serial numbers, account details,
installers, or artwork. No physical printing/cutting is needed. These probes do
not test recognition of external registration vectors, which remains a separate
unknown until Leonardo mark geometry is available.

## Preservation and local verification

Once received, keep originals under `references/<run-id>/` with their native
extensions. Add relative paths and SHA-256 hashes to `manifest.json`; retain
requested settings separately from actual observed settings. Record capture
procedure and version/profile in `settings_record`, plus screenshot hashes.
Do not change a pending status to measured just because files open. Record PDF
page boxes/transforms and shape/mark measurements first. The original probes
must remain unchanged; hashes are for file identity, not proof of alignment.

From the repository root:

```powershell
.\venv\Scripts\python.exe tests/data/print_cut/siser/verify_fixtures.py
Get-FileHash -Algorithm SHA256 -LiteralPath 'path-to-original-reference'
```

The verifier parses all three SVGs, checks their intended coordinates and fixed
translation, checks manifest/file hashes, and checks scalar PDF unit conversions.
It imports no application settings and measures no nonexistent native output.
The `.gitattributes` rules keep SVG hashes stable and prevent Git altering
acquired originals. No dependencies or application-test rerun are needed.

## Artwork Only follow-up and original M08A acquisition plan

This records the pre-closeout evidence and requested procedure. The accepted M08A
numeric-placement result is recorded in the closeout subsection below.

The newer Artwork Only route has seven separately preserved artifacts under
`references/artwork-only/`: three PDF/LDS pairs and Wayne's Position Records.txt.
It preserves the tested 30 × 20 mm print rectangles, while base/shifted imports
normalize to identical positions. See the report's Artwork Only section for
reproduced PDF values and supplied native observations. Historical direct Print
& Cut evidence remains separate. The verifier covers 11 historical plus seven
follow-up acquisitions; source SVGs remain unchanged.

The **next targeted validation (M08A)** is the report's five-step procedure: verify
coordinate origin/anchor and units; restore the complete four-shape group using
source-derived page targets; duplicate and verify print/cut geometry; save/reopen;
and repeat from fresh imports, proving the corrected +12/+9 mm Letter shift.
Include the initially off-page L object. Do not resize, guess offsets, or assume
X/Y is page-relative. Record numeric-entry versus display precision. This is a
manual acquisition; it was not an M04 dependency. Start with Letter base and shifted
only; defer A4 landscape until that numeric method succeeds. Return four PDF/LDS pairs
(`letter-base-initial`, `letter-shifted-initial`, `letter-base-repeat`,
`letter-shifted-repeat`) and before/after placement, Page Marks, reopen and repeat
records. Include actual settings, coordinate conventions, input/display precision,
both groups' print/cut assignments and complete cut preview. Do not send a job.

## Historical M08A awaiting stage (2026-10-07)

This subsection records the earlier pending acquisition; the closeout below supersedes it.

Accepted M07 `308eb22e8739e5817c1285b4343603944770b968` has been integrated
and pushed to `siser-dev`. New work is on the unmerged
`milestone/m08a-leonardo-placement` branch. All 17 incoming files in
`probes/Probe Marks Enabled/` and `probes/Position Records.txt` match existing
preserved hashes; no corrected numeric-placement acquisition is present. Native
Leonardo interaction is unavailable because the enabled automation surface has no
native computer controls. Numeric restoration, print/cut correspondence and persistence
remain unverified; M08A is not complete.

Use the existing five-step checklist in the
[report](../../../../doc/architecture/siser-reference.md#next-targeted-leonardo-validation-m08a-acquisition-checklist).
For a verified page-relative top-left anchor, Letter group targets are `(25,35)`
and `(37,44)` mm, with unchanged `144 × 173` mm bounds. Those bounds are checks,
not resize instructions. Center-anchor targets and alternate-origin requirements
are in that same procedure. Preserve new original files under
`references/numeric-placement/<run-id>/`; the manifest's separate
`numeric_placement_evidence` section currently has no acquisitions or observations.
The verifier needs no extension until records are acquired. Historical findings,
source probes and acquisition bytes/digests are unchanged. No application test
matrix was rerun. Production M08 and physical alignment remain gated.

## M08A closeout: software numeric placement demonstrated

On 2026-10-08 the architect accepted the Letter initial/repeat and both A4 setup-order
findings. **No further Leonardo runs or exports are required.** Three incoming archive
hashes were verified; twelve original PDF/LDS members are preserved byte-for-byte in
six `references/numeric-placement/<run-id>/` directories with received filenames and
individual hashes. Source probes and all eighteen earlier acquisitions are unchanged.

The manifest's `numeric_placement_evidence` section records the accepted software result,
operator observations/limitations and the earlier awaiting-stage snapshot. Full-precision
local measurements are in `numeric-placement-measurements.json`, generated by the narrow
`measure_numeric_placement_outputs.py` using existing bundled pypdf/pdfplumber.

Both Letter initial/repeat pairs preserve all four vectors including the L, source
dimensions/relative geometry and the corrected (+12,+9) mm translation, with fixed
registration bars. Corresponding initial/repeat PDFs match byte-for-byte; base and
shifted PDFs remain different. Both corrected A4 setup-order PDFs are byte-identical,
with rectangle left/top approximately 35/25 mm and complete bounds 219 × 138 mm.
The emitted A4 boundary is approximately 296.968329 × 209.973338 mm, separate from
nominal 297 × 210; no compensation is applied.

Native print/cut agreement, preview and initial Letter reopening are operator-reported,
not decoded LDS geometry or agent native observations. Operational cutting parameters
follow the last-used settings according to Wayne; loading an LDS is not a complete
reproducible cutting preset. One pass is an observed setting, not a universal default.
Leonardo is reported as 1.1.31; edition/selected software cutter profile and the exact
settings storage scope remain unconfirmed. See the
[closeout report](../../../../doc/architecture/siser-reference.md#m08a-closeout--software-placement-demonstrated-2026-10-08)
for literal A4 entries, separate measured results, provenance and scope.

Applicable closeout commands from repository root:

```powershell
.\venv\Scripts\python.exe tests/data/print_cut/siser/verify_fixtures.py
& 'C:\Users\mitch\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  tests/data/print_cut/siser/measure_numeric_placement_outputs.py --check
git diff --check
```

Checks cover all thirty acquisition hashes, source probes, the recorded measurement
digest, six PDF measurements, independent pdfplumber bounds and identity/translation
comparisons with a 0.001 mm software tolerance. No application pytest suite, physical
job, new visual matrix or LDS reverse engineering is needed. Production M08 registration,
external marks/camera/clearance, active profile and physical qualification remain gated.

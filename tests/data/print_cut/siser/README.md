# M02 Leonardo reference acquisition

These are diagnostic probes, not a production cut template. There are no Siser
marks or page-border paths. Three initial PDFs are preserved in `references/initial-*`, with measurements in
`initial-measurements.json`. Native projects and controlled import results remain
pending; planned-run observations in `manifest.json` are null. See the
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

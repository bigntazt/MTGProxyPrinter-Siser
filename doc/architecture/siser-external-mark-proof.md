# M08B — controlled external-registration-mark proof

Historical software-preparation record: 2026-10-13 (America/Los_Angeles).
Starting integration: `243ac2f09aea058483f260f973ea8e1ed39d97aa` (accepted M11A,
already on local/remote siser-dev). Branch `milestone/m08b-external-mark-proof`
was initially left unmerged for architect review. The authorized documentation
closeout below completes M08B for fast-forward integration. Local/remote trunk remain
`00202f988bef071c7070967e86c1490cb19114ba`.

## Preserved reference and operator evidence

Incoming files were found in `tests/data/print_cut/siser/probes/Probe Marks Enabled/Baseline Cut/`.
Byte-identical copies, retaining filenames, are committed under
`tests/data/print_cut/siser/references/external-marks/nine-card-baseline/`.
The LDS is an opaque native-project reference, neither decoded nor rewritten.

| Input | SHA-256 |
|---|---|
| Nine Card Baseline Cut.pdf | `7023f253a4e29caad51e25c8a7b0176c7be179b062cbb77cb49a9789d1b9bc68` |
| Nine Card Baseline Cut.lds | `48abbd086c354eff398b2d54d864e4f5174a0220defd634f82318219a6e63eca` |

`reference.json` retains full measured precision for nine trim contours, eight
filled bars, stroke appearance, paper metadata, source hashes and observations.
The manifest indexes this evidence separately; all prior sections are unchanged.

| Stage | Operator-reported observation |
|---|---|
| Pre-recalibration first cut | Approximately 0.33 mm left; bottom edges approximately 0.9 mm high across rows; pieces approximately 63 × 88 mm |
| Pre-recalibration second cut | Top row approximately 0.4 mm left; bottom row visually aligned; approximately 0.5 mm high, middle row approximately 0.7 mm high |
| After Siser recalibration | Unchanged baseline printed/cut again; cuts visually coincided with printed lines; small deviations could not reliably be quantified |

Caliper alignment was visual; measurement repeatability was not established.
Calibration contributed materially to the earlier displacement, but this does
not identify every residual error source or establish universal machine accuracy.
No constants, averages, offsets or numerical physical acceptance tolerances were
derived. Exact calibration procedure/date and active tool settings were not
supplied and remain unknown. Earlier observations remain pre-recalibration evidence.

## Independent generation and bounded measurement

`render_external_mark_proof.py` builds the explicit Letter portrait snapshot:
regular cards, nine occupied slots, zero margins, row/column spacing and bleed,
registration style stored as `None`. Card coordinates come exclusively from
`PageGeometry.trim_px`. Nine separate unfilled rectangles preserve shared edges.
Only bar measurements and contour appearance come from the numeric reference.
The renderer never reads/embeds/stamps/rasterizes the Leonardo PDF, copies its
stream, loads the LDS, or uses physical observations as a correction.

Reference physical stroke width is **0.17638902057871667 mm**, solid black,
unfilled, PDF cap 0 (flat), join 0 (miter), default PDF miter limit 10. The local
proof sets these explicitly on a noncosmetic pen, preserving square corners.
Qt defaults are square caps and bevel joins, so they are unsuitable here:
[QPen documentation](https://doc.qt.io/qt-6/qpen.html).
Eight black, unstroked bar rectangles use full measured mm coordinates converted
with `distance_to_px`, retaining fractional canonical pixels. This appearance
choice does not change production SVG or calibration rendering.

The fresh static scene uses unchanged `_render_graphics_scene`, default 300 DPI,
equal explicit physical-sheet source/target rectangles, zero correction and no
rotation/fitting. QPdfWriter requests already oriented Letter mm with ExactMatch,
Portrait arrangement and zero margins; paper acceptance, Letter full rectangle,
painter startup/end and cleanup are checked. A QBuffer remains alive until writer
finalization; output uses exclusive creation. No labels, artwork, crosses,
watermarks, rulers or extra borders are included. The script has no layout options
and rejects unsupported fixture identity/paper/style/count rather than extrapolating.
See [QGraphicsScene rendering documentation](https://doc.qt.io/qt-6/qgraphicsscene.html).

`measure_external_mark_proof.py` independently reads both actual PDFs with pypdf.
Its straight-path parser resolves nested q/Q and cm transforms, normalizes cyclic
rectangle starts/directions, measures stroke width after uniform transforms,
checks black/opaque vector appearance, rejects unsupported painted operators,
artwork, annotations, dashed paint, nonrectangles and unexpected clipping.
Only nominal-page-or-larger rectangular clips are accepted. Explicit page-box
keys are captured before accessing pypdf box properties. Qt's indirect empty
annotation array and BooleanObject false are explicitly resolved; named color
spaces are resolved to DeviceRGB/Gray. Qt's opaque `/SA` stroke-adjustment and
`/SM` smoothness defaults are accepted; rasterization remains independently checked.
This is a checker for these straight-vector fixtures, not a general PDF framework.
Historical `measure_numeric_placement_outputs.py` is untouched.

## Commands and actual results

Tools: existing CPython 3.13.14, PySide6/Qt 6.11.2, pypdf 6.19.0, Poppler 26.07.0.
No installations/upgrades. Normal Windows Qt platform in a copied child environment;
no persistent environment or preferences changed. From repository root, initial
reference capture used:

```powershell
.\venv\Scripts\python.exe tests/data/print_cut/siser/measure_external_mark_proof.py --record-reference
```

This refuses to overwrite the retained record. Generation reproduction (output
directory must exist; select a new run directory to preserve existing evidence):

```powershell
@'
import os, subprocess, sys
env = os.environ.copy(); env['QT_QPA_PLATFORM'] = 'windows'
sys.exit(subprocess.run([sys.executable,
    'tests/data/print_cut/siser/render_external_mark_proof.py',
    '--output', '.m01-output/m08b-review/B-generated-marks.pdf'], env=env).returncode)
'@ | .\venv\Scripts\python.exe -
```

Comparison command (also refuses to overwrite existing evidence):

```powershell
.\venv\Scripts\python.exe tests/data/print_cut/siser/measure_external_mark_proof.py --proof .m01-output/m08b-review/B-generated-marks.pdf --output .m01-output/m08b-review/comparison-measurements.json
```

The final detailed report retains each contour/bar comparison. The initial
summary report is preserved as `comparison-measurements-initial.json`.

| Property | Reference | Generated proof |
|---|---|---|
| Pages / MediaBox | 1 / 612 × 792 pt | 1 / 612 × 792 pt |
| Requested / measured sheet | 215.9 × 279.4 mm | 215.9 × 279.4 mm |
| Explicit page boxes | MediaBox only | MediaBox only |
| Effective boxes | All Letter, uncropped | All Letter, uncropped |
| Rotate / UserUnit | absent; effective 0 / 1 | absent; effective 0 / 1 |
| First trim x/y, mm | 13.335000555750 / 7.619995888999 | 13.335 / 7.620 |
| First trim w/h, mm | 63.076675358945 / 88.053340272888 | 63.076666666667 / 88.053333333333 |
| Stroke width, mm | 0.176389020578717 | 0.176389019840000 |
| Objects | 9 contours, 8 bars | 9 contours, 8 bars |

Maximum absolute resolved coordinate difference: **0.00003070766649670986 mm**,
against 0.01 mm. Maximum stroke-width difference:
**0.0000000007387166989225591 mm**, against the separate 0.00001 mm tolerance.
No fitted translation, rotation or computational alignment was used. A deliberately
perturbed 0.02 mm bar corner fails the comparator. No additional placement
translation, scale, rotation or restrictive clipping was found.

Independent Poppler renders at 180 DPI (full page) and 300 DPI (top-left and
bottom-right crops) were inspected. Four corner marks, nine unfilled contours,
orientation and stroke appearance match, with no missing/clipped bars or extra
objects. Full-page PNGs are byte-identical at 180 DPI; corresponding top-left
crops are byte-identical at 300 DPI. Numeric measurements remain primary; this
raster equality is specific to those resolutions, not an assertion of exact
PDF bytes or universal rasterizer behavior.

```powershell
pdftoppm -r 180 -png -singlefile .m01-output/m08b-review/A-leonardo-control.pdf .m01-output/m08b-review/A-full
pdftoppm -r 180 -png -singlefile .m01-output/m08b-review/B-generated-marks.pdf .m01-output/m08b-review/B-full
pdftoppm -r 300 -x 30 -y 30 -W 290 -H 290 -png -singlefile .m01-output/m08b-review/A-leonardo-control.pdf .m01-output/m08b-review/A-top-left
pdftoppm -r 300 -x 30 -y 30 -W 290 -H 290 -png -singlefile .m01-output/m08b-review/B-generated-marks.pdf .m01-output/m08b-review/B-top-left
pdftoppm -r 300 -x 2220 -y 2990 -W 290 -H 290 -png -singlefile .m01-output/m08b-review/A-leonardo-control.pdf .m01-output/m08b-review/A-bottom-right
pdftoppm -r 300 -x 2220 -y 2990 -W 290 -H 290 -png -singlefile .m01-output/m08b-review/B-generated-marks.pdf .m01-output/m08b-review/B-bottom-right
git diff --check
```

Input working-tree/staged-byte hashes, manifest/reference-record consistency and
unchanged prior manifest sections were checked. No unrelated application suites
or historical acquisition matrices were rerun. Production modules and existing
preserved files are unchanged. Memtrace decision IPC was unavailable and its
index stale; incremental refresh/source-window inspection was used, while the
explicit architect contract governed this new development-only code.

## A/B package and physical comparison

The instructions and pending status in this section describe the historical
software-preparation stage. The subsequent accepted physical result is recorded
in the closeout below; instructed conditions are not independently confirmed by
the operator's brief report.

Exact package: `D:\Code\MTGProxyPrinter-Siser\.m01-output\m08b-review\`.
Control PDF and LDS are byte-identical copies of preserved originals. Output
hashes are recorded below and in `package-hashes.json` in that directory.
Generated PDF SHA-256:
`6126973d23c7bb49e89ca2b3575312503ec08c47b781acad37f674499f12b4c8`.
Control PDF/LDS hashes are the preserved input hashes above.

| Other package output | SHA-256 |
|---|---|
| comparison-measurements.json | `86980f6766acd5171bdee1a47be921abc2f2031ea2120aa744e1d432b90beb93` |
| comparison-instructions.txt | `e2a0a7aab49d64227baebc3109e486104f2de7fea00e223bb8b7a623e4b7c004` |

Instructions are committed with the reference fixture and copied unchanged into
the package as `comparison-instructions.txt`. After architect review, Wayne
prints one fresh A and one fresh B from the same viewer at actual-size/100%,
identical paper/feed/driver settings; cuts both with the unchanged LDS and newly
calibrated Siser; records active tool/blade/pressure/speed/pass settings; retains
calibration and placement throughout. Loading LDS does not establish a preset.
Observe normal acquisition and top/middle/bottom alignment. Record acquisition
failure before workarounds. Do not reimport B into Leonardo, nudge marks/cuts,
or apply a per-job offset. If A does not reproduce the calibrated baseline,
the comparison is inconclusive until the control is restored; do not fix B.

**Historical preparation status: software proof verified; physical comparison pending.**
No physical job was submitted by the agent, and no hardware-derived correction
was applied. Success would qualify
external-mark equivalence only for this fixture/workflow, not other paper/layouts,
printers or Romeo. Close corner-card/mark proximity remains a constraint, not an
approved optical clear zone. Production integration requires configuration-driven
geometry and explicit mark/bleed clearance; this fixed renderer is not the feature.
No production Siser profile, settings, menu, dependencies or later milestone began.

## Accepted physical A/B comparison and integration closeout

The software proof was previously accepted at
`ae3df7e530b29fd2181c1ea76910f82d904f254b`. Wayne has now supplied this exact
operator report:

> Both pages: Alignment = to baseline page, all cards identical.

Wayne reports that both the Leonardo control and independently generated
proof matched the baseline’s alignment, with all cards appearing identical.
No distinguishable additional cutting error was observed for the generated
proof. This is an operator-reported visual comparison, not a numerical
accuracy specification.

**M08B accepted: software proof verified and physical A/B alignment
equivalent by operator observation for the tested Letter nine-card job.**

The architect accepts this physical result and authorizes documentation-only
closeout followed by fast-forward integration into `siser-dev`, without another
review solely for this closeout. Previously verified digital PDF geometry remains
the numerical software evidence; the newly reported physical alignment is visual
operator evidence. No new dimensions, tolerances, acquisition log, equipment/tool
settings or confirmation of every instructed condition are inferred. No further
print is required solely to fill missing equipment metadata.

No hardware-derived application correction was introduced. The external-mark
feasibility prerequisite is satisfied for this job, so configuration-driven
Siser integration can now be planned. Other paper sizes/orientations, optical
clear-zone limits, bleed conflicts, other equipment and Romeo equivalence are
not automatically qualified. The fixed proof renderer remains a development
fixture, not a production profile.

The original PDF/LDS, A/B package, reference measurements/hashes, manifest,
proof/checker scripts and historical instructions remain unchanged. Closeout
validation is limited to `git diff --check`, changed-file review and documentation
consistency. No application tests, regeneration, PDF remeasurement, acquisition
matrix or further physical job was performed. Production implementation awaits
the architect's separate prompt; the milestone branch is retained after integration.

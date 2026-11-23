# Prepared raster presentation (M28)

M28 supplies an explicit presentation seam over accepted M24–M27 services. No
normal application, document session, live view or export entry point adopts it.
There is no automatic preparation, new setting, provider, cache or persistent
schema. Original/Lanczos and explicit 600/1200 targets remain the accepted choices.

## Decoder and value ownership

`page_scene.prepared_pixmap.decode_prepared_pixmap(prepared)` accepts a
`PreparedImage` and returns `PreparedPixmap | RasterDecodeFailure`. A GUI
application must already exist and execution must be on that application's GUI
thread. Missing GUI application, wrong thread and wrong argument type are
programming errors (`RuntimeError`/`TypeError`), checked before pixmap construction.
An arbitrary QObject's owner thread is insufficient.

PreparedPixmap has frozen bindings for source_identity, canonical_dimensions,
actual_dimensions, target_ppi, target_dimensions, image_format, outcome, pixmap,
derivative_key, reason and detail. Metadata uses immutable tuples, strings, enums
and frozen identity records. It retains no encoded bytes, PreparedImage, source
snapshot, model, document, widget, cache or provider. The decoder creates its own
QPixmap value; CardItem's existing M23 seam makes another value copy. QPixmap is
itself mutable and implicitly shared, so this is not deep immutability. Caller
mutation/dropping the presentation value does not invalidate an item's copy.

The helper reads exactly the supplied encoded bytes through QBuffer/QImageReader.
Format detection is enabled and automatic transforms explicitly disabled. It
reads the first frame once and requests no scaled size. It checks structurally
valid declared dimensions, the target/canonical/PPI relationship, immutable
metadata, detected Qt format, actual decoded dimensions and pixmap conversion.
The detected format convention is the same as M24 (for example `jpeg`, not an
invented filename-extension alias). The local pixmap DPR is normalized to 1.

Expected failures return a reason/detail record: invalid_metadata, decode_failed,
format_mismatch, dimension_mismatch or pixmap_failed. Programming exceptions are
not swallowed by a broad fallback handler. Failure of the in-memory buffer to
open is an internal RuntimeError. Qt's existing allocation limits are unchanged.
Successful presentation copies the existing M26 outcome, reason and detail
unchanged; a presentation failure does not invent a new processing outcome.

The decoder never reopens sources/cache files, imports Pillow, calls processing
or custom post-processing, crops, pads, masks, flattens alpha, re-encodes or adds
application color conversion. Original/fallback may have actual dimensions far
below or above the requested target; these native dimensions are preserved.
Qt/platform pixmap conversion is a presentation representation, not a promise of
byte-for-byte high-bit-depth or CMYK sample preservation. Tests use an explicit
Qt reader-to-pixmap reference when platform representation matters.

## Optional scene lookup

The compatible constructor is:

```python
PageScene(document, render_mode, parent=None, *, raster_resolver=None)
```

The optional callable is installed before initial drawing. Its contract is a
synchronous GUI-thread **in-memory lookup**:
`raster_resolver(image_index) -> PreparedPixmap | None`. It must not decode, hash,
read files/cache, download, process, start/wait for workers or mutate the document.
The caller prepares records before exposing them. Wrong return types raise
TypeError; resolver programming exceptions propagate rather than becoming misses.

Only ordinary Card faces with a supported physical size and valid printing/face/
quality metadata are eligible. CustomCard, CheckCard, empty placeholders and
unsupported/invalid identities bypass lookup entirely. Matching checks compare
the current ImageKey (printing ID, face, image-quality category), canonical size,
non-null pixmap and current pixmap dimensions against the record's actual size.
Mismatch logs one concise diagnostic and uses existing rendering, without a
popup. There is no hashing or source inspection in this check.

A matching prepared raster is resolved before DisplayRole availability is tested.
It can fill an ordinary card slot with no legacy in-memory pixmap. Otherwise the
available legacy image is used, or the existing missing-image hole remains.
Accepted native pixels go directly to `CardItem(index, document, raster=...)`.
No model image or ImageDatabase entry is assigned and no acquisition normalization
is involved. With no resolver, the existing construction and availability behavior
remain unchanged. No new GUI-thread assertion is applied to default PageScene
construction (including the unchanged task-created PNG export scene).

## Refresh and physical geometry

`scene.refresh_card_rasters()` is an explicit GUI-thread operation. It uses the
established geometry recovery, clear/draw and bleed methods to reconstruct the
selected page. It supports adoption and removal of a lookup result; merely
clearing a dictionary cannot change pixels already owned by an item. Repeated
refreshes leave one item per available model slot. Existing image-change,
insertion and page-change construction paths all go through draw_card and use
the same resolver checks. No document dataChanged, action or undo entry is emitted
to force a redraw. Missing/pending geometry is retained/recovered using existing
logic, without independent physical layout calculations.

CardItem, its constructor-time Qt group-bounds correction and all artwork/style
transforms are unchanged. Only the artwork child maps native pixels into canonical
trim; the group, margins, spacing, bleed expansion, registration marks, guides,
watermarks and shared physical output transform are unchanged. Refresh does not
unnecessarily update existing footer text. Later bleed updates keep the existing
group-bounds policy; this milestone does not alter it.

| Card type | Canonical at 300 DPI | 600-PPI artwork | 1200-PPI artwork |
|---|---:|---:|---:|
| Regular | 745 × 1040 | 1490 × 2080 | 2980 × 4160 |
| Oversized | 1040 × 1490 | 2080 × 2980 | 4160 × 5960 |

These accepted physical dimensions are not nominal 63 × 88 mm. Regular artwork
occupies 178.8 × 249.6 PDF points; oversized occupies 249.6 × 357.6 points,
independently of requested raster target. M23's canonical style proxy still
supplies the existing corner/edge sampling policy. Different prepared pixels can
therefore change enabled-path bleed colors without changing geometry. Default
scenes remain pixel-identical.

## Freshness and deferred adoption

ImageKey/size checks reject obvious substitutions; they do not establish current
source bytes or processing settings. Row numbers and valid persistent indices
are insufficient: a card container can retain its index while its card changes.
An M27 batch token identifies work, not document freshness.

A future document/session binding must own generation, source-capture validity,
cancellation/invalidation on relevant document/settings changes, occurrence-to-
result mappings, representation lifetimes and scene refresh on adoption or
invalidation. It must arrange all pixmap decoding on the GUI thread and obey the
M27 synchronous acceptance/lifecycle contract. M28 does not implement that binding.

Custom source bytes omit the application's forced-corner presentation mask.
Supporting that mask is required before future prepared adoption for custom
cards; custom cards are not permanently excluded. Existing artwork masks use
canonical pixel radii, separate from the 2.5 mm cutting contour. CheckCard needs
its own higher-resolution face composition and component-corner policy. Neither
masking nor composition is implemented here.

Live view, PDF, PNG and native printing construct separate scenes. A resolver on
one scene does not adopt prepared images for another. Normal export adoption,
readiness, UI/settings, PNG threading changes, neural providers and subsequent
milestones remain deferred.

## Focused evidence

The two new presentation test modules cover native dimensions for both sizes and
targets; Original/fallback; corrupt/mismatched data; the M24 orientation-6 case;
alpha/Qt reference pixels; GUI-thread errors; no I/O/processing or retained encoded
payload; lookup eligibility/mismatch/fallback; missing legacy slots; reconstructed
refresh; Qt value lifetime; current identity after persistent-slot replacement;
and real M24–M27 preparation, accepted-result decoding and alias reuse.

Selected M23 raster and scene-adoption regressions additionally cover placement,
group bounds, decorations, missing images, movement and invalid-geometry recovery.
Private accepted-M27 PageScene source comparisons verify exact populated regular
and oversized default scene pixels and decoration geometry, including visible
watermark settings. All sources/stores/caches are synthetic and isolated.

The end-to-end software exercise renders an explicit scene once after each batch,
and uses the unchanged shared physical renderer for a synthetic PDF. PDF inspection
checks native artwork XObject dimensions (and alpha masks), and composes the image
placement matrices to compare positions/rectangles against accepted geometry.
Large cases run sequentially. Private four-case proof covers both targets and both
card types; removing records and refreshing restores default pixels exactly.
This qualifies only the explicit software path, not normal export actions,
interactive controls, hardware or enhancement quality.

Reproduction uses the private review package's isolated interpreter and wrapper.
With `$Review` pointing there, from the milestone worktree:

```powershell
$Python = "$Review\runtime\Scripts\python.exe"
$env:PYTHONPATH = $null
$env:PYTHONHOME = $null
$env:M28_RUN_LABEL = 'focused-final'
& $Python -B "$Review\run_isolated.py" pytest `
  tests/page_scene/test_prepared_pixmap.py `
  tests/page_scene/test_prepared_scene.py `
  tests/page_scene/test_card_raster.py `
  tests/page_scene/test_page_geometry_adoption.py::test_missing_pixmap_slot_can_become_available_and_replaced `
  tests/page_scene/test_page_geometry_adoption.py::test_incremental_moves_removal_undo_and_redo `
  tests/page_scene/test_page_geometry_adoption.py::test_invalid_geometry_rejects_render_then_recovers `
  tests/page_scene/test_page_geometry_adoption.py::test_reflow_temporary_destination_overflow_recovers `
  -q -s --timeout=40 --tb=short -o "cache_dir=$Review\pytest-cache" `
  --basetemp "$Review\pytest-final"
```

Runtime: Python 3.13.14, Qt/PySide6 6.11.2, Pillow 13.0.0. Exact commands,
versions, final check counts, visual observations and preservation evidence are
retained in the private handoff. Offscreen tests may lack default fonts; the
visual proof loads an existing Arial font process-locally. This does not qualify
native interactive UI. No catalog, inference, printer or cutter operation ran.

Qt reference: [image reader transformation controls](https://doc.qt.io/qt-6/qimagereader.html#autoTransform),
[pixmap value representation](https://doc.qt.io/qt-6/qpixmap.html#details),
[PDF device resolution](https://doc.qt.io/qt-6/qpdfwriter.html#setResolution).

# Prepared current-page PNG (M32)

**File → Export prepared current page as PNG → 600 / 900 / 1200 PPI** exports one
completed prepared page. Enable Original or Lanczos in Image Preparation Preview,
wait for Ready, choose the PNG canvas resolution, review the captured results and
save. Choices are ephemeral numeric action data from SUPPORTED_TARGET_PPI; 600 is
first. No extra preparation pass or derivative-cache identity change is requested.

## Two independent resolutions

The preparation target chooses accepted card pixels. Original/fallback preserve
native source dimensions, even when the disabled Lanczos target says 900. PNG page
PPI instead chooses the entire output grid. All three PNG choices work in Original
mode, without enabling/changing the Lanczos target. A 1200-PPI page does not mean
every source contains 1200-PPI detail or that interpolation recovered artwork detail.

| Nominal paper | 600-PPI canvas | 900-PPI canvas | 1200-PPI canvas |
|---|---|---|---|
| Letter portrait | 5100 × 6600 | 7650 × 9900 | 10200 × 13200 |
| A4 portrait | 4961 × 7016 | 7441 × 10524 | 9921 × 14031 |

Landscape transposes the appropriate nominal dimensions. The captured landscape
compatibility workaround transposes the emitted canvas and applies the existing
whole-page rotation together. Existing artwork/Siser configuration checks still
apply (for example, the Siser reference supports A4 landscape, not portrait).

## Shared one-turn ownership

prepared_page_output._capture_prepared_page_output extracts M31's capture and face
accounting without duplicating its state machine. Public session status/resolve,
Ready/current-generation checks, persistent page index and actual identity, geometry,
registration, completion/diagnostics/counts and captured printing faces remain shared.
An export-local lookup owns QPixmap value copies; painting never consults the live
session. Missing published bindings fail, while recorded standard/skipped holes
remain allowed and disclosed. No source refresh, download or document mutation is
introduced. External prepared-source edits still require Refresh.

The private helper creates one temporary ON_PAPER|FILE_EXPORT PageScene and invokes
a synchronous format callback. It validates state before scene construction, before
rendering and after completed encoding, then disposes the document-connected scene
and clears raster references on every exit, preserving a primary error. Returned
PNG/PDF records contain only bytes and immutable metadata. The PDF public interface,
300-coordinate-DPI writer, ExactMatch/zero-margin/whole-point boundary policy,
lossless images and writer/buffer lifetime remain intact.

## Direct PNG rendering and memory

capture_prepared_page_png takes separate target_ppi and output_ppi. Output PPI must
be exactly integer 600, 900 or 1200, excluding bool/coercible values. It directly
paints the captured scene into opaque white QImage.Format_RGB888 at DPR 1. Width and
height are round(emitted_mm*output_ppi/25.4); both densities are
round(output_ppi/0.0254) dots/metre. Shared print.py physical mapping preserves
geometry, bleed, unequal spacing, guides, labels, watermarks and marks, including
permitted artwork/mark overlap. There is no PDF rasterization, completed-page
enlargement, new layout calculation or native-printer horizontal correction.

A separate **512 MiB output-page raster limit** preflights aligned RGB rows:
`stride=((width*3+3)//4)*4; estimated_bytes=stride*height`.
Positive finite/Qt-int-compatible dimensions are checked before allocation, then
isNull and actual sizeInBytes are checked. Standard Letter/A4 fit at every choice;
Letter 1200 needs 403920000 bytes (about 385 MiB). An over-budget custom canvas is
rejected with its requested dimensions and guidance to choose lower PNG PPI or
prepared PDF; PPI is never reduced silently. Painter begin/end, PNG encoder success
and MemoryError are handled through the normal export-error path. Encoding occurs
once into an owned buffer; temporary image/painter/buffer objects are released.
See [Qt's image API](https://doc.qt.io/qt-6/qimage.html) for the underlying operations;
the isolated runtime used Qt 6.11.2, without upgrading it.

This limit is independent of the unchanged 512 MiB retained prepared-card budget.
Neither is a total-process-memory promise: prepared images, source/processing buffers,
PDF/PNG encoding and completed bytes require additional memory.

Capture through encoding remains on one GUI turn: no processEvents, dialogs, waits,
workers or new scheduler. The UI pauses temporarily. Representative synthetic
observations were 3.812 s total for Letter 1200 (0.011 s painting, 3.654 s encoding)
and 1.031 s for rotated A4 600 (0.009 s painting, 0.969 s encoding). These are
observations, not timing guarantees or assertions. A background design is deferred.

## Review, safe save and accounting

PreparedPageOutputDialog shares M31's layout/lifetime; compatible PDF and explicit
PNG wrappers supply format wording/names. The PNG summary distinguishes Original or
requested Lanczos preparation from page PPI/pixel dimensions, shows actual outcomes
and diagnostics, and states white paper and freshness limitations. Suggested names
are document-page-number-prepared-outputppi.png; the suffix describes the canvas.

The outer flow remains MainWindow.current_dialog. Child Cancel returns to the same
captured result; failed atomic save preserves the destination and permits retry.
The existing _write_prepared_file writes exactly those completed bytes. Only a
successful commit submits one PrintCountUpdater snapshot, guarded against repeated
or reentrant acceptance. No live document is reread for saving/accounting. Finished
or destroyed flows release payloads and child dialogs. Existing explicit face
selection, deduplication/front-back distinction and delayed locked database writes
are unchanged. Both formats' actions refresh eligibility on status/dialog/loading
transitions and repeat checks in their runtime handlers.

## Focused software evidence and boundaries

The final focused run passed 80 cases: retained M31 PDF measurements/capture/save,
PNG output/preflight/failures and both formats' UI. Compiled UI passed 18 overlapping
dialog/action cases. Independent Pillow-only child processes decode the large PNGs
sequentially, without relaxing application-wide reader limits. CRC/IHDR/pHYs checks
verify RGB8 dimensions and rounded density. Magenta/cyan source pixels absent from
the legacy pattern prove prepared adoption. Oversized artwork and touching Siser
corner-group bounds agree within one output pixel; PDF's existing 0.01 mm artwork
and 0.001 mm vector-mark comparisons remain intact. A permitted-overlap case still
exports; raster measurement fixtures keep bars visible rather than imposing policy.

New Letter 1200 and rotated A4 600 visuals show white paper, consistent artwork,
marks and spacing. Native interaction availability is recorded in the private
handoff separately from automated/offscreen coverage. No hardware or physical-quality
claim is made. Regular Print/print preview/PDF/PNG, multipage output, neural providers,
custom masks/CheckCard composition, settings/serialization, cache policy, registration
geometry and calibration are unchanged. Prepared multipage/native printing remain
separate work; this milestone adds only the explicit current-page PNG path.

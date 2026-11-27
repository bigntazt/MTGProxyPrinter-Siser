# Prepared current-page PDF (M31)

File → **Export prepared current page as PDF…** is an explicit single-page artwork
export. Open Image Preparation Preview, choose Original or Lanczos, wait for Ready,
invoke the command, review the captured outcomes, then choose Save PDF. Original
keeps native pixels. Lanczos offers exactly integer 600 (default), 900 and 1200 PPI
(maximum). These image targets do not change canonical 300-DPI layout geometry or
the PDF writer's coordinate resolution. Regular Print, print preview, PDF and PNG
commands retain their existing paths.

## Eligibility and capture

The action requires a running MainWindow, no loading lock or current dialog, an
enabled session with Ready/current-generation completed metadata, and a valid
current page. General UI unlocking is followed by specific eligibility. Status and
dialog ownership changes refresh it. The handler repeats the checks and preserves
existing artwork/Siser readiness validation. It never compacts, changes selection,
downloads missing images or starts another preparation batch.

prepared_page_pdf.capture_prepared_page_pdf executes on the GUI thread in one turn.
M32 moves format-independent capture, accounting and scene lifetime to the private
prepared_page_output helper, reused by [prepared PNG](prepared-page-png.md). The
public PDF result/capture interfaces and output policy remain unchanged.
Before any dialog it captures public status(), generation, completion, diagnostics,
the persistent page index and actual identity, page number/source path, shared
PageGeometry, preview options and landscape-workaround choice. It resolves each
current-page image through public session.resolve into an export-local lookup,
owning Qt pixmap value copies without a deep pixel copy. It checks occurrence and
published counts, actual outcome counts, usable native dimensions and current
generation/page/geometry. It neither inspects private publication state nor
duplicates ordinary-face preparation eligibility.

A fresh paper/FileExport PageScene receives only that local lookup; painting never
calls the live session. Its geometry and readiness are checked, then one page is
rendered into QBuffer/QPdfWriter with shared print.py physical mapping. No events,
dialogs, queued wait, worker handoff or asynchronous boundary occurs through final
byte creation. State is revalidated before scene construction and after rendering.
Stale, missing or unusable published bindings fail rather than silently becoming
standard images. Recorded standard/skipped occurrences are permitted and disclosed.
The document-connected temporary scene is explicitly disposed and raster references
released on success/failure, preserving the primary failure during cleanup.

## Output policy

Nominal paper uses ExactMatch, zero margins and the existing whole-point PDF
boundary allowance. The writer uses round(RESOLUTION.magnitude), currently 300;
LosslessImageRendering preserves native artwork dimensions independently of that
coordinate DPI. The captured landscape workaround transposes paper and rotates the
whole page together. No native-printer horizontal correction applies. Existing
scene bleed, spacing, guides, watermarks, labels and registration marks are reused,
with no new artwork/mark overlap restriction or layout calculation.

| Type | Canonical trim | 600 | 900 | 1200 |
|---|---|---|---|---|
| Regular | 745 × 1040 | 1490 × 2080 | 2235 × 3120 | 2980 × 4160 |
| Oversized | 1040 × 1490 | 2080 × 2980 | 3120 × 4470 | 4160 × 5960 |

SUPPORTED_TARGET_PPI in image_source is shared by planning, descriptor validation,
batch tasks, prepared-pixmap validation, session enable, UI numeric item data and
capture. Strict types remain required. The descriptor schema/serialization, recipes,
source identities and cache publication are unchanged. Existing 600/1200 identities
stay compatible; 900 has its own deterministic target identity. The 600 golden key
remains e817a0b7d4c3225d533726714604db6a1af679f57301302c64028be48746d089.
Original and fallback keep their native dimensions even with 900 target metadata.

## Review, freshness, saving and usage

The returned frozen PreparedPagePDF contains completed bytes and metadata only:
no live session, model, scene, index or QPixmap. The outer review dialog owns one
active result. It shows captured page, Original/native or requested Lanczos target,
actual Original/cache/generated/uncached/fallback/standard/skipped counts and useful
diagnostics. A target is not presented as achieved by fallback images. Standard
holes explain that existing images are used and missing images may be placeholders.
All-fallback or all-recorded-standard completed batches can be saved.

External prepared-source edits without Refresh deliberately retain the accepted
pixels. Refresh is required for a new prepared revision. Later page/layout/mode
changes cannot alter already completed bytes. Child filename selection operates on
those bytes only, using the existing QSaveFile safe writer with direct-write fallback
disabled. Child Cancel returns to review; failed save preserves the destination and
allows retry with the same bytes. Suggested names contain document/page/prepared,
not an achieved PPI claim. Successful Save finishes the flow. Finished/destroyed
outer dialogs release payloads, and finished child dialogs are disposed. A hidden
MainWindow-owned result is not retained indefinitely after completion.

Capture freezes the database path and unique exported face identities. Accepted
prepared bindings count even when legacy images are blank/absent. Standard-only
faces use the existing selected-page key helper plus absent/null/blank exclusion;
custom cards and CheckCards are excluded. Front/back remain distinct and duplicate
copies count once. PrintCountUpdater supports explicit captured inputs without a
Document, rejecting ambiguous combinations; document-based construction retains
its behavior. Connections remain delayed and the existing database write lock is
unchanged. Only successful atomic commit submits one task through normal handling,
guarded against reentrant/repeated acceptance. A later accounting failure cannot
roll back or resave the completed PDF.

## Evidence and remaining boundaries

Focused checks cover real Ready control-to-PDF capture, nonready/stale rejection,
missing/null published bindings, injected generation/page/geometry changes, accepted
source freshness, native Original and fallback, all three targets, warm/duplicate
reuse, atomic save failures/retry, dialog resource release and captured usage.
Actual PDF resources retain regular 1490×2080, 2235×3120 and 2980×4160, oversized
3120×4470, and nonstandard Original dimensions. Existing measurement helpers compare
artwork at 0.01 mm and Siser marks at 0.001 mm, including spaced/bleeding Letter and
A4 landscape with whole-page rotation. Development and compiled UI are exercised.
Exact commands, durations, visual/native observations and any process limitations
belong to the private handoff; this is software evidence, not printed-quality proof.

512 MiB still bounds retained prepared-raster payload only. Source/temporary/Qt
buffers, PDF encoding and completed bytes need additional memory. The cosmetic
first-image Queued label timing is deferred. Later prepared multipage, native print,
print preview need separate ownership/validation and inherit the same
600/900/1200 policy. No neural provider, persistence, custom-mask/composite support,
new scheduler or layout changes are introduced here. M32 separately adds the explicit
current-page PNG path; regular PNG output stays unchanged.

## Experimental neural preparation (M37)

A configured Neural 2× choice can supply this prepared output. Its completed
generation fixes mode and card-target PPI; capture rejects mismatching caller
values before rendering. Review shows neural counts/policy rather than a Lanczos
label. See [setup and qualification](experimental-neural-preview.md). No inference
is started by capture/save, and ordinary output commands remain unchanged.

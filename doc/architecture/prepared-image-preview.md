# Main-page image preparation preview (M30)

M30 adopts the [current-page session](prepared-raster-session.md) in the normal
MainWindow. Its central on-screen PageScene receives the resolver. M31 additionally captures
completed results for an explicit [current-page PDF command](prepared-page-pdf.md).
M32 adds [current-page PNG](prepared-page-png.md) with separate 600/900/1200 page
output PPI, including in Original mode.
Regular Print, print preview, PDF and PNG keep their existing image path. Settings
layout previews also retain the default, unprepared scene. There are no document,
settings, geometry, provider recipe or derivative identity changes.

## Operation

Open **Application → Image Preparation Preview**. The reusable dock starts hidden
and Off on each launch. Choose **Original — native source pixels** to preserve
the captured source dimensions, or **Lanczos resize** for conventional resizing
at 600 PPI (initial), 900 PPI or 1200 PPI (maximum offered target). Lanczos and Neural enable the target selector. M37 adds an explicit
[experimental neural setup and preview](experimental-neural-preview.md); it remains
Off until selected and configured. Interpolation does not recover missing image detail.

Refresh explicitly rereads sources through the session. External file changes
without application notifications require Refresh. Off, closing or hiding the dock
restores standard rendering; reopening starts Off. Choices are not saved. Ordinary
card faces are eligible; custom cards and composed CheckCards keep their existing
preview. The scope notices remain visible above the controls.

## Ownership and storage

MainWindow creates one disabled PreparedPageSession after assigning its document
and image database, before central scene construction. CentralWidget.set_data and
PageRenderer.set_document accept a keyword-only raster_resolver defaulting to None.
Only this owner supplies session.resolve. Task submission uses the existing
request_run_async_task signal, Application submission, progress display and Cancel
control. No scheduler, pool or shutdown implementation changes are introduced.

The source root is the actual supplied image_db.db_path. The derivative root is
`Path(image_db.db_path).absolute().parent / "ImageDerivatives"`. Resolved trees
must be disjoint in both directions, including through symbolic links. An invalid
configuration disables preview controls with an explanation and leaves ordinary
startup intact. Construction checks path relationships, without creating, scanning
or cleaning the derivative directory. Off does no preparation; Original retains
its no-Pillow/no-derivative-I/O contract. Lanczos discovers Pillow lazily and uses
the existing derivative service. Original-cache cleanup does not delete siblings.

## Status and readiness

The UI reads a fresh immutable session.status() snapshot on every metadata or
raster notification. It does not inspect staging, rediscover outcomes or retain
event payloads. Metadata belongs to one generation and clears on invalidation.
Completion publishes the registry before the queued scene rebuild; during this
interval status is **Updating preview…**. The owner rebuilds central scene rasters
before reading and displaying **Ready**.

Details count page occurrences, including shared aliases, and distinguish explicit
Original, Lanczos cache hits, generated cached/uncached results, Original fallback,
standard rendering and unsupported/skipped cards. Ready means publication has been
applied, not that Lanczos succeeded for every card. For example, unavailable Pillow
shows **Original fallback: 2** with the reason, rather than a Lanczos-success claim.
A cache-write failure can still show **Lanczos generated, not cached: 2**. Failure
and fallback reasons are deferred metadata, never synchronous UI notifications from
result consumption. Cancelled work remains cancelled until an explicit choice,
Refresh or legitimate page/source change requests work again.

## Retained pixel payload

The normal owner supplies max_raster_bytes=512*1024*1024. This is a 512 MiB retained
prepared-raster payload budget, **not a process-memory cap**. Source decoding,
processing, temporary buffers and Qt scene presentation can require extra memory.
Other session callers remain unlimited unless they opt in.

Each unique successful result is charged once; aliases share it. Valid declared
dimensions give a four-byte-per-pixel predecode estimate. Over-budget results do
not call the GUI decoder. Actual decoded dimensions/depth then determine a
conservative aligned charge of at least four bytes per pixel, without toImage or
a deep pixel copy. Excessive actual charge discards only that new raster. Rejected
results remain addressable failure sentinels, aliases stay valid and those cards
use standard rendering while accepted cards publish atomically. Invalid metadata
remains invalid. No silent PPI reduction, Original resizing, eviction or retry is
introduced. Accounting follows batch lifetime, preserving M29's terminal and
genuinely draining teardown rules.

## Loading, cleanup and shutdown

The existing outer loading-lock acquisition suspends preparation and controls.
The dock retains desired options. Only the real final lock release queues one
resume for the latest options/current page, if the window is running, visible and
active. General unlocking is followed by mode-specific control enablement. Hiding
or Off prevents later resume. Ordinary preparation does not acquire this lock.

CacheCleanupWizard acceptance is connected with a queued notification: its
synchronous original deletion finishes before the owner requests refresh. No
source reread occurs on the wizard's acceptance stack. MainWindow closes the
session before the existing application quit/task-wait path, both from its close
event and menu Quit. Session close stays nonblocking.

## Focused evidence and remaining scope

The isolated development-UI run passed 106 cases: new status/budget tests, directly
affected M29 session/lifecycle guards, dock controls, owner lifecycle, and real
MainWindow adoption. Compiled-UI controls/owner/adoption passed 35 cases. Small
rasters exercise numeric memory boundaries; real controls drive 600/1200 targets,
duplicates, warm reuse, native Original, fallback, uncached success, cancellation,
all layouts, empty/custom-only pages and export-construction isolation.

Native Windows interaction with synthetic sources checked opening, choices,
Refresh/Cancel, close/reopen Off and normal window close. Private screenshots cover
Off, Original, both targets and forced provider fallback. Scope notices and controls
were readable. This is functional UI evidence, not a physical-quality claim or
daily-use M21 qualification. The automation log recorded a Windows COM diagnostic
while interaction continued; that limitation and exact commands/versions are in
the private handoff. Runtime: Python 3.13.14, Qt/PySide6 6.11.2, Pillow 13.0.0.

M31 adds File → Export prepared current page as PDF after Ready, with a captured
outcome review and immutable atomic save. Later multipage/native/PNG adoption needs
separately authorized ownership, freshness and presentation checks; M32 now covers
only the explicit current-page PNG case. The shared
600/900/1200 target policy applies to those future consumers. The cosmetic first-image
Queued timing remains deferred. Custom masks/composition, neural
providers, persisted controls and derivative retention/cleanup remain separate
work. This milestone introduces none of them.

M37 prepared exports use the immutable completed mode/card-target PPI, not live
dock choices. Neural setup is session-only; normal output paths remain unchanged.

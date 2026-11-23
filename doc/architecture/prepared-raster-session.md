# Current-page preparation session (M29)

M29 adds an optional GUI-thread `PreparedPageSession` over M24–M28. An explicit
caller can capture the selected page, run the existing background controller,
decode accepted results and publish them together to a PageScene resolver.
No normal application, live renderer or output caller constructs this session.
There are no new controls, persisted settings, dependencies, processing recipes,
custom masks, composite rendering, neural providers or physical-layout changes.

## API and explicit ownership

```python
session = PreparedPageSession(document, submit_task, source_cache_root,
                              derivative_cache, parent=None)
session.enable(mode="lanczos", target_ppi=600)
session.refresh()
session.disable()
session.close()
prepared = session.resolve(image_index)
```

Construction requires an existing GUI application and its GUI thread, as do all
operations and decoding. The document must belong to that thread. Construction
starts disabled, performs no capture and submits nothing. An explicit source root
and DerivativeCache are required; no storage defaults, ImageDatabase or pool are
discovered/created. A real source root comes from the existing ImageDatabase.db_path.
Relative source-root interpretation is frozen at construction without file access.
Submission must use the existing asynchronous task mechanism and M27's submission
contract; it must not run a task synchronously or schedule it and then report failure.

Mode is exactly original or lanczos; target_ppi is explicitly integer 600 or 1200,
not bool. Invalid options raise before changing an existing valid state. enable
always requests a fresh generation, including unchanged options. refresh forces
recapture and source rereading even for unchanged ImageKey/path/options. refresh
while disabled is a no-op. enable/refresh on a closed session raise RuntimeError;
disable/close are harmless after close. Wrong-thread calls fail before model or
pixmap access.

Disabled means existing rendering; explicit Original instead uses the full accepted
source/preparation/presentation path and preserves the source's native dimensions.
No target size silently bounds or rescales Original/fallback native dimensions.

## Capture and binding identity

Capture uses get_current_page_index, rowCount, index at PageColumns.Image and
UserRole. It does not request DisplayRole/image_file or inspect source contents.
Exact ordinary Card type, valid printing/face/quality metadata and supported
canonical dimensions use the same eligibility as M28. CustomCard, CheckCard,
placeholders and unsupported identities stay on their existing path.

GUI-only occurrences retain generation, page reference, persistent image index,
the actual Card reference, expected ImageKey/canonical dimensions, frozen M24
SourceRequest locator and a unique batch-local occurrence ID. Card/page references
are compared with `is`, not mutable equality, row numbers or raw IDs. In particular,
Page is a list subclass: a Qt QVariant data-role round-trip may produce a copied
list. The model index's internal pointer supplies the original page identity.

Only immutable BatchEntry/SourceRequest values go to M27. No document, card, index,
scene, widget or QPixmap goes to the worker. Each actual new request recaptures
metadata and M27 rereads/hashes the original bytes through the unchanged services.

## Generations, structural boundaries and scheduling

Session generation is monotonic and distinct from M27 batch ID. Each enabled
invalidation immediately advances generation, clears the published registry,
cancels active work and replaces the single pending request with the latest
desired options. An owned single-shot zero-delay QTimer coalesces fallback refresh
and reconciliation. Disabled sessions still track structural boundaries without
starting preparation.

Observed signals are current_page_changed, dataChanged, page_type_changed,
page_layout_changed, paired row insert/remove/move notifications, paired reset
and layout notifications, and document destruction. Invalidation is conservative
for the entire page. A nesting counter blocks capture, publication and public
refreshes between structural about-to/completion boundaries; about-to handlers
never capture or rebuild scenes. Completion only queues reconciliation. This also
coalesces settings reflow and new/open-document sequences. Page-removal completion
may precede replacement selection; invalid selections produce no capture.

There is one session-owned ImagePreparationController, one active/draining stage
and one latest pending request. Cancellation does not make M27 idle. A successor
starts only after the existing controller's terminal boundary and prior stage
cleanup. If A is draining while B then C are requested, only C starts afterward.
The pending request is consumed once when reconciled. Empty/wholly ineligible
pages emit idle_no_eligible_sources diagnostic metadata and submit no batch.

Terminal completion/failure/cancellation does not create a request. In particular,
the existing task Cancel action leaves fallback rendering with no automatic retry.
A scheduling exception consumes the failed request, reports scheduling_failed,
and does not loop or fabricate a BatchSummary for an unsubmitted task. Explicit
enable/refresh or a later relevant model change can request work again.

## Acceptance, atomic publication and notifications

The M27 acceptance callback validates active batch/generation, current selected
page and captured occurrences before decoding. It performs only short synchronous
validation and staging: no scene rebuild, session-owned public notification,
document mutation, event processing, nested loop, modal UI or worker wait.

PreparedResult is decoded once through M28 and stored by result ID. Decode failure
is retained as an explicit RasterDecodeFailure sentinel under that ID. Aliases
reuse either the successful value or failure sentinel; unknown IDs or inconsistent
bindings are programming errors, handled as consumer failure by M27. Unavailable
sources record fallback. No PreparedImage or encoded-payload collection is retained.
Captured sources are ordinary file locators, not another raw-source cache.

Invalidation hides published values immediately but retains active result-ID
representations until terminal cleanup, so obsolete staging does not break alias
ownership. Completed current-generation work is reconciled only after structural
stability. Page, options, count, persistent slots, Card references, ImageKeys and
canonical dimensions are rechecked. The whole successful stage is then swapped
into one published registry and bookkeeping released. Expected missing/decode
failures leave legacy holes while other valid occurrences publish. Failed,
cancelled or obsolete batches publish nothing.

Signals carry no image payloads:

- rasters_changed(): coalesced fallback/publication notification, suitable for
  scene.refresh_card_rasters. One publication refresh follows the completed swap;
  per-result acceptance does not refresh the page.
- progress(BatchProgress): the existing current-batch accepted occurrence counts,
  not published raster counts. Obsolete progress is suppressed.
- completed(SessionCompletion): generation, the real submitted BatchSummary and
  a separate published_occurrences count. Obsolete completion is suppressed.
- diagnostic(SessionDiagnostic): generation, reason/detail, occurrence IDs and
  optional batch ID. Expected fallback diagnostics are deferred until reconciliation.

After public notifications the session rechecks generation, closed/native-lifetime
state and structural depth before proceeding. A handler can disable, replace the
request, close or synchronously destroy the session. Native destruction marks plain
lifetime state and clears presentation storage without dereferencing a dead QObject.

## Resolver, wiring and teardown

resolve is a GUI-thread in-memory lookup. It checks enabled/open state, published
generation, document/current page, image-column index and the captured Card object,
ImageKey and canonical dimensions. Normal misses/stale bindings return None.
There is no reading, stat, hashing, decoding, processing, cache lookup, submission
or document mutation. Index validity alone cannot identify a card: replacement can
retain the same container and persistent index. These checks also protect calls
made before an invalidation observer has handled a model notification.

Explicit wiring (not normal application adoption):

```python
scene = PageScene(document, render_mode, raster_resolver=session.resolve)
session.rasters_changed.connect(scene.refresh_card_rasters)
scene.destroyed.connect(session.close)
```

The owner must arrange session closure and dispose the attached scene during
permanent teardown. disable is the operation for a *living* scene to regain legacy
rendering: lookup clears immediately, then a queued refresh reconstructs CardItems.
Existing items otherwise retain their Qt pixmap value copies.

close marks permanent closure first, discards pending/publication, stops the timer,
disconnects model observations, releases the document reference and closes/cancels
the controller without waiting. Active staging releases at terminal cleanup or
native destruction. Later model access, publication and refresh notifications are
suppressed, including after document destruction. Native child-controller destruction
uses the accepted M27 cancellation wakeup. No thread termination, global cancellation
or accepted runner/shutdown change is made. As in M27, a queued runnable discarded
before run entry cannot promise terminal callbacks; owner/native teardown releases
the session's storage in that boundary.

## Freshness and memory limits

Known document/image notifications invalidate automatically. Arbitrary external
file changes without notification or explicit refresh are not watched. The resolver
does not prove file contents are unchanged. No watcher, GUI hashing or additional
raw-source cache is added.

Presentation retention is scoped to the selected page and the single draining/
staging batch. Duplicates share a value. This is not a fixed RAM guarantee: custom
paper can contain many cards, Original/fallback can exceed targets, scenes retain
Qt copies until refresh/disposal, and processing/decoding require working memory.
A hard memory/readiness policy remains for normal adoption. No silent downsampling
or maximum-native-size interpretation of 1200 PPI is introduced.

M29 does not implement custom forced-corner masks, CheckCard high-resolution
composition, normal PageRenderer/live application or PDF/PNG/native-print adoption,
persisted controls, neural providers, cache retention or later milestones. The
accepted M28 native raster mapping and all physical layout/decorations stay unchanged.

## Focused evidence

The two new test modules plus the five explicitly required M27/M28 boundary guards
passed **47 cases in 3.61 seconds** in the isolated review runtime. Real M24–M29
tests cover shared-path duplicates (no artificial alias roots), sequential 600/1200
targets, Original native size, atomic staging/publication, changed bytes under the
same ImageKey after refresh, disable restoration, worker/consumer/scheduling failure,
task cancellation, document/native teardown and resolver/capture I/O boundaries.
Controlled tests cover successful/failed aliases, partial fallback, A→B→C draining,
same-slot replacement, structural terminal deferral, reentrant notifications and
representation release. Pools/barriers are test-owned, bounded and drained in cleanup.

Runtime: Python 3.13.14, Qt/PySide6 6.11.2, Pillow 13.0.0. Exact reproduction commands,
versions, logs, private scene comparisons and preservation evidence are in the
private architect handoff. The small explicit visual scene exercise confirms native
1490×2080 and 2980×4160 pixels map to the same 745×1040 trim and original positions;
disable restores exact default pixels. Arial is loaded process-locally for review;
offscreen default-font limitations are not native interactive qualification.
No new PDF proof, normal application launch, catalog, inference or hardware operation
is part of this milestone.

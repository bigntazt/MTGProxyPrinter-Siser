# Background image preparation (M27, internal neural adoption M36)

M27 introduced one sequential PreparationBatchTask and one owner-thread
ImagePreparationController. M29/M30 later adopted it for current-page preview.
M36 adds an explicitly configured internal neural path; the live dock and normal
output callers retain their existing modes. The runner, shutdown contract and
accepted processing policies remain unchanged.

## Public API and ownership

```python
controller = ImagePreparationController(submit_task, consume_result, parent=None)
batch_id = controller.start(entries, mode="original", target_ppi=600, cache=cache)
controller.cancel()
controller.close()
```

Mode, target_ppi and an existing explicit-root DerivativeCache are required
keyword arguments (the example supplies values, not defaults). Entries are
BatchEntry(occurrence_id, source) or two-element tuple/list pairs. Each source is
an M24 SourceRequest or SourceUnavailable. Occurrence IDs are unique opaque
integers; bool is rejected. Negative IDs are not given positional meaning.

start/cancel/close/is_busy belong to the controller's owning Qt thread and check
that ownership. Only one batch may be active. Starting while busy raises, and
cancel does not immediately make it idle. A new start is allowed after terminal
finalization; close permanently rejects new starts. Repeated cancel/close are
harmless. Source capture from cards/models remains a future caller's owner-thread
responsibility; this service accepts no Card, document, model index, ImageDatabase,
scene item, widget or QPixmap.

The submit callable must schedule asynchronously through the existing runner,
as Application.run_async_task does. A standalone test adapter uses an isolated
QThreadPool with `pool.start(AsyncTaskRunner(task))`. Do not synchronously call
task.run on the owner thread. An owner Qt event loop is required for acceptance.
No new pool, executor, queue, progress widget or global scheduler is constructed
by the production service.

Task construction is parentless. The controller retains its active task and the
existing AsyncTaskRunner retains it during execution. Task/control state retains
no controller, consumer callback, model or UI references. The controller's
destroyed signal connects directly to the standalone control object's cancel/wake
method without capturing the controller. Native controller destruction therefore
releases a worker waiting for acknowledgment. Per-batch connections and active
references are released during terminal handling.

If submission raises, the control is cancelled, input/task connections and active
state are released, and start raises a clear scheduling RuntimeError. There is
no finished notification for a task that was never submitted. The callable must
not schedule a task and then claim it was not submitted.

## Captured input and worker responsibilities

Before scheduling, cheap structural checks validate settings/IDs/request metadata
and freeze the entry sequence, ImageKey fields and canonical dimensions. Immutable
custom bytes are retained without copying. There are no owner-thread file reads,
source hashes, image decodes, planning, cache operations or preparation.

The worker sequentially loads M24 snapshots, creates existing M24 plans, calls M26
prepare_image, and uses the existing M25 persistent cache through M26. It creates
no QPixmap. PreparationBatchTask is an AsyncTask, with can_cancel=True and its
cancel method reaching the same control used by the controller. Existing progress
buttons and AsyncTaskRunner.cancel_all_tasks therefore have the same protection.

## Two levels of batch deduplication

Locator groups are built in first-seen order using:

| Source | Locator key |
|---|---|
| File | ImageKey, canonical dimensions, exact path string |
| Custom bytes | ImageKey, canonical dimensions, identity of the immutable bytes object |

There is no path resolution, case folding, timestamp inspection or file access
while building keys. SourceRequest equality is deliberately not used: its custom
payload is excluded from equality, so different custom images can compare equal.
ImageKey alone is also insufficient. Captured SourceUnavailable values are
delivered explicitly without loading or placeholder/download substitution.

Each locator group is read once. All of its occurrence IDs use that captured
snapshot, including copies appearing later in the input sequence. The same path
is not reread halfway through the batch. A later batch reads it anew.

After loading and planning, the content key contains SourceIdentity (ImageKey and
SHA-256), canonical/native dimensions, image format, decode policy, inspection Qt
version, mode, target PPI and target dimensions. Equal content from distinct
locators can therefore converge after inspection. Face, quality, geometry,
inspection and content differences remain distinct.

The first key calls M26. After successful owner acceptance, the worker retains
only content-key-to-result-ID metadata. A later matching locator sends an alias,
not another preparation. This also works for generated_uncached. No aggregate of
snapshots or prepared PNGs is retained. The group helper releases its working
references before beginning the next group.

This is a batch metadata key, not a new persistent identity. M25 still owns the
full recipe/runtime-aware persistent key. One batch assumes the current fixed
processing policy; provider installation or hot reload during a batch is outside
scope.

## Events, bounded mailbox and acceptance

Immutable delivery events are:

- PreparedResult(result_id, occurrence_ids, prepared_image).
- ResultAlias(result_id, occurrence_ids), referencing an accepted result.
- UnavailableResult(occurrence_ids, unavailable).

Result IDs are batch-local. The consumer must retain an addressable accepted
representation for each result ID until that batch ends. It may be a lightweight
renderable handle rather than the original encoded payload. Alias events do not
carry duplicate images.

One mailbox slot holds one result event. The worker publishes it, emits only
batch/delivery IDs through a queued wakeup, then waits for acknowledgment or
cancellation. Delivery IDs increase monotonically within the fresh batch control.
Stale/wrong IDs cannot take or acknowledge another slot. Worker-to-controller
notifications use explicit @Slot methods and QueuedConnection, never
BlockingQueuedConnection. Batch and delivery checks remain necessary even after
disconnecting connections, because queued notifications may already exist.

The decorated owner slot synchronously invokes consume_result(batch_id, event).
Normal return is acceptance; ordinary exception is rejection. Successful signal
emission is not acceptance. No mailbox lock is held during callbacks or public
notifications. Only after acceptance/acknowledgment is a new prepared result
eligible for aliases. The slot drops its payload reference before acknowledgment
or deferred terminal forwarding.

The callback must be short and synchronous. It must not start a nested event
loop, call processEvents, show modal UI, or wait for the worker. Consumer exceptions
record failure, cancel/wake the worker and stop later delivery. Arbitrary partial
consumer mutations before an exception cannot be undone by this service.

Memory is bounded by one pending result plus current source/processing working
memory, rather than a batch's worth of queued PNGs. Captured custom input bytes,
input/identity metadata, and representations deliberately retained by the consumer
are outside that payload bound. The controller stores counters, not all images.

## Cancellation and terminal handling

A shared threading.Event and Condition-backed cancellable mailbox are used by
both task.cancel and controller.cancel. Checks occur before/after source loading,
before/after preparation, before publishing/acceptance and while waiting. Cancel
wakes the worker and discards pending unaccepted payloads; no UI-thread completion
wait is added. Closed or stale batches do not invoke the consumer.

Cancellation is cooperative. It cannot interrupt a synchronous Qt decoder,
Pillow operation or atomic M25 publication halfway through. A running operation
may finish and commit a valid cache entry; retain it and suppress later delivery.
There is no rollback, thread termination or cache cleanup.

An already executing callback cannot be rolled back. If it calls cancel or close
and returns normally, its current event is counted as accepted and no later event
is delivered. If it raises, it is not counted and final status is failed, even
after cancellation. Cancellation need not wait for that callback to return; the
owner reconciles accepted counts afterward. A delivery-in-progress flag defers
terminal metadata, preventing reentrant finalization within the callback.

Every task that enters run emits terminal(batch_id, status, detail) once and
task_completed once from cleanup. AsyncTaskRunner remains responsible for
task_deleted. Status is completed, cancelled or failed. Unexpected worker or
consumer exceptions are logged and reported as failed; expected M24 unavailability
and M26 fallback cause no per-image error_occurred popup.

A living controller emits one final BatchSummary and clears active state before
finished, permitting a completion handler to start another batch. Explicit
failure takes precedence over cancellation. Cancellation observed before owner
terminal finalization takes precedence over an otherwise completed status.

The existing application shutdown cancels tasks and clears queued runnables.
A runnable discarded before entering run cannot promise terminal callbacks during
process teardown. M27 does not change that global shutdown boundary.

## Progress and results

Task task_begins(total_occurrences, description) uses the existing progress API.
The owner emits task.set_progress(resolved_occurrences) only after acceptance.
Nine duplicate occurrences accepted in one event advance progress by nine.
Unavailable occurrences count as resolved only when accepted. Aliases advance
resolved count but not unique count. No completion percentage is manufactured
after cancellation.

progress carries only BatchProgress: batch_id, total_occurrences,
resolved_occurrences, unique_prepared_results and unavailable_occurrences.
finished carries only BatchSummary: the same counts plus status/error_detail.
Unique prepared results include Original, cached, generated, uncached and fallback
outcomes; this is not an inference-operation counter. No image/snapshot appears
in progress or terminal summaries.

## Focused validation

Separate runtime: Python 3.13.14, Qt/PySide6/shiboken 6.11.2, Pillow 13.0.0,
pytest 9.1.1 and pytest-qt 4.5.0. Accepted M26 pins were installed only into the
new M27 environment; no dependency or earlier environment changed. Pip check passed.
The established private wrapper redirects storage before application imports,
blocks real stores/network, restricts SQLite, checks the M27 package location,
and uses offscreen Qt with process-only A4. Final proof shows only M27 writable
locations and no database connections.

From the M27 worktree, with `$Review` the private review directory and `$Python`
its runtime\Scripts\python.exe:

```powershell
$env:PYTHONPATH = $null
$env:M27_RUN_LABEL = 'batch-tests-final'
& $Python -B "$Review\run_isolated.py" pytest `
  tests/async_tasks/test_image_preparation_batch.py `
  tests/async_tasks/test_image_preparation_lifecycle.py `
  tests/test_image_derivative.py::test_serialization_contract `
  -q -s --timeout=30 --tb=short --basetemp "$Review\pytest-batch-final"
```

**43 passed in 2.07 s.** Tests use isolated QThreadPool/AsyncTaskRunner instances,
deterministic events and bounded waits; blockers are released and those pools
drained in cleanup without cancelling unrelated global tasks. Most cases use
controlled source/preparation doubles. Real M24–M26 cases demonstrate Original
with Pillow import blocked/no cache access, cold then warm Lanczos at 600 and
1200, explicit high-depth fallback, validated uncached success with content alias,
and a cache entry remaining usable after cancelled delivery.

Other evidence covers frozen input, invalid settings/IDs, empty batches,
locator/content dedup and equality traps, later-batch rereads, inspection metadata,
off-owner work/owner callbacks, event-loop responsiveness, bounded pending delivery,
stale IDs, accepted-only aliases, previous-payload release via weak references,
cancel before execution/after loading/during preparation/in a mailbox wait, direct
task cancel, native controller destruction, busy/reuse/close, deferred terminal
handling, consumer/worker failure precedence and scheduling failure. The M25
805-byte descriptor/golden key remains unchanged:
`e817a0b7d4c3225d533726714604db6a1af679f57301302c64028be48746d089`.

No manual visual, native interactive or physical qualification is claimed. No
full application, catalog, downloader, print/cut, benchmark, source/processor or
cache fault matrix was rerun. There is no inference, application launch, new UI
or hardware job.

## Next integration boundary

A future owner must capture sources safely, maintain occurrence/result mappings,
keep result-ID representations until terminal, and cancel/invalidate batches when
document or relevant settings state changes. It must respect the acceptance
callback contract and dispose/close controllers on teardown. M27 observes no
documents and does not automatically solve stale document integration. Custom
forced-corner masking, high-resolution CheckCard composition, settings/UI,
rendering/export adoption, retention and M28 remain deferred.

Qt background: [signals across threads](https://doc.qt.io/qt-6/threads-qobject.html#signals-and-slots-across-threads),
[QObject connections/disconnection](https://doc.qt.io/qt-6/qobject.html#disconnect).

## Internal neural batches (M36)

`start` and `PreparationBatchTask` additionally accept `mode="neural"` with a
keyword-only `worker_config`. Only that mode accepts a frozen M34
`NeuralWorkerConfig`; it is required, with no discovery/default provider. All
three integer targets (600/900/1200) are supported. Invalid mode/config/target
metadata is rejected before submission. Capture the config, immutable values and
explicit absolute derivative root; do not probe interpreter/checkpoint paths or
verify readiness on the GUI thread. Existing calls omit the new keyword.

On explicit neural execution, the task imports M35 and constructs one lazy
NeuralImagePreparer on its executing thread with the batch cache and the exact
batch cancellation Event. Only the existing post-content-deduplication site calls
its prepare method. The same executing thread closes it before terminal and
task_completed on every exit. Empty, unavailable-only and sufficient-only batches
start zero workers. Original/Lanczos retain M26 dispatch and never import neural
host modules. Torch/CUDA/Spandrel remain in the child. M35 owns readiness before
warm lookup, recipes, pixels, provider failure policy and final-target caching.
There is no replacement, automatic retry or provider substitution.

Grouping, one-slot acknowledged delivery, accepted-only aliases and metadata-only
progress retain their existing semantics. Original fallback is a unique prepared
result with its original reason/detail. Aliases count occurrences without another
unique result or inference. Cache persistence failure retains generated_uncached.

NeuralWorkerCancelled from preparation or final close is classified before generic
failure handling. Clean teardown yields cancelled and discards pending delivery;
a completed synchronous store may remain valid. Independent processing/consumer
failure remains failed even when cancellation also occurs. Final public cleanup
information supersedes an earlier report after owned cleanup retry. Incomplete
teardown yields failed with bounded primary-cause and cleanup detail (at most 4096
characters). The executing task logs cleanup limitations even for stale or destroyed
owners; session presentation still suppresses stale notifications.

BatchSummary adds default-false metadata `neural_relaunch_blocked`. If public
cleanup reports a child not reaped or I/O threads not stopped, the controller
latches that condition before emitting finished, including stale generations.
Unknown cleanup exceptions are conservatively latched too. The same controller
rejects later neural submissions without creating another task; baseline modes
remain available. This is an owner-lifetime safety latch, not a process recovery
service. No global runner or shutdown contract changed. A pre-run discarded task
still has the established runner boundary.

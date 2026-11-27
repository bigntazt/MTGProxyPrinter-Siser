# Inactive neural image preparation (M35)

M35 connects immutable source snapshots/plans, the explicit M34 worker client,
current processing provenance and the existing M25 final-target cache. It returns
the existing PreparedImage type. M36 adopts it only through explicitly configured
internal [background batches](image-preparation-batch.md) and
[page sessions](prepared-raster-session.md). **No normal application/preview or
output caller enables neural mode.** Original/Lanczos remain the baseline; the
experimental Compact reference is not a recommended faithful neural default.

## Explicit lazy ownership

```python
with NeuralImagePreparer(worker_config, cache, cancel_event) as preparer:
    result = preparer.prepare(snapshot, resolution_plan)
```

The constructor requires explicit NeuralWorkerConfig, DerivativeCache and
threading.Event. Construction/context entry do not discover Pillow, access cache
or application storage, or create/start a worker. Calls belong to one owner
thread; wrong-thread, closed-object, invalid source/plan and configuration/state
errors propagate as programming errors. Closing unused preparers starts nothing.

The optional host runtime is resolved only for insufficient sources. One M34
session is created at most once and reused for successive eligible images.
Close is idempotent, retains cleanup information via cleanup_report, and permits
retry after a cleanup failure. Context cleanup preserves a primary exception with
notes rather than hiding it; cancellation errors carry cleanup details. Torch,
Spandrel and CUDA libraries stay in the explicit external worker environment.

## Sufficient source and current cache identity

Each request checks cancellation, validates the source/plan through the existing
contract, checks cancellation again, then inspects both-axis sufficiency.
A sufficient source returns the exact captured bytes, original format/native
dimensions, outcome original, reason source_sufficient and no derivative key.
Requested target PPI/dimensions remain informational. That call performs no Pillow
discovery, readiness check, inference or cache access, even when a worker is already
owned. There is no force-enhance option.

For an insufficient source the service resolves the host runtime, calls the owned
client's public start() for **current verified readiness**, builds the recipe and
request, then performs lookup. The current worker input-pixel limit is checked
before lookup/decode/intermediates; unsupported size returns worker_input_limit.
A cached ready object or historical manifest never substitutes for start(). Each
eligible cache lookup repeats that check, including warm hits. A fresh preparer
may therefore load the model just to establish identity before finding a persistent
hit; this startup cost is intentional and was observed in the proof.

A valid hit skips source decode, inference, final resizing and encoding. M25 still
performs its own final PNG integrity validation. A miss performs one native neural
pass and stores only the completed final-target derivative. There is no second
persistent native-output cache or cross-target native-result reuse in M35.

The new recipe uses provider spandrel-neural-rgb8, adapter version 1, pipeline
m35-neural-preparation-v1. build_derivative_request retains the existing schemas and
source/face/quality/hash/inspection/canonical/target identity. Recipe parameters
bind the verified model/checkpoint, worker fingerprint and canonical stable
provenance, actual host Qt/PySide6/Pillow/zlib versions, decode/orientation/color/
alpha/mask policies, one native 2x pass/RGB8 quantization, final interpolation and
PNG metadata/encoding policy, plus the sufficient-source rule. They exclude
session/request IDs, PIDs, paths, timing and unrelated diagnostics. No normalized
RGB hash or inference result is needed to construct a cache request.

## Exact pixel policy

decode_source_rgba is a narrow extraction from the existing Pillow processor,
shared with Lanczos. It decodes only captured encoded bytes through Qt's existing
first-frame, no-auto-transform, no-scaled-decode policy, checks native dimensions
and format, retains the supported-format restriction and normalizes to straight
RGBA8888. It preserves only a valid Qt-exposed RGB ICC profile; untagged samples
stay untagged. Unsupported precision/color/profile cases remain explicit failures.
Lanczos recipe strings, error messages and pixels are unchanged.

On a neural miss:

1. Copy straight RGBA samples to Pillow with the actual Qt row stride.
2. Separate straight RGB and the original alpha; send RGB8 to M34 without a matte,
   alpha flattening or premultiplication.
3. Perform exactly one whole-image Compact native 2x CUDA FP32 pass.
4. Resize original alpha independently to native dimensions using Pillow Lanczos,
   reducing_gap=None, then attach it to native RGB.
5. Skip final interpolation when native dimensions equal the plan target;
   otherwise resize complete RGBA with Pillow Lanczos, reducing_gap=None.
6. Encode RGBA8 PNG, compression level 4, optimize=False, keeping only the declared
   RGB ICC profile. No EXIF/DPI/text, assumed profile, crop, padding or corner masks.

Temporary image buffers close per call. A disabled-provider record retains only
bounded failure metadata, not exception tracebacks that could retain source buffers.
Hidden straight RGB can influence neural edges near transparency; explicit alpha
preservation is not edge-fidelity qualification. Custom snapshots can use the
existing source contract, without adding custom presentation or CheckCard composition.

Native scale and final target size remain separate. Targets come exclusively from
the existing plan, not new physical-size calculations:

| Canonical geometry | 600 PPI | 900 PPI | 1200 PPI |
|---|---|---|---|
| 745 x 1040 regular | 1490 x 2080 | 2235 x 3120 | 2980 x 4160 |
| 1040 x 1490 oversized | 2080 x 2980 | 3120 x 4470 | 4160 x 5960 |

A 1200-PPI final is still a **native 2x neural upscale followed by conventional
resizing**, not native 4x enhancement or proof of recovered detail. There is no
input reduction, repeated neural pass, tiling, CPU/half-precision fallback, model
substitution, downloader or environment installer.

## Results, expected failures and cancellation

Outcomes reuse original, cache_hit, generated_cached, generated_uncached and
fallback_original. Valid generated PNGs with cache write failure retain their
derivative key and cache_write_failed reason; invalid generated PNGs return exact
source bytes with invalid_generated_png. Expected Pillow/source/processing failures
and optional worker unavailability, timeout, protocol/result failure, rejection or
budget exhaustion preserve specific reasons/details (bounded to 4096 characters).
Every fallback retains exact encoded source bytes/native format/dimensions and no
derivative key; it is never stored as neural success.

Worker rejection is recoverable only while the session remains usable; it and
source-specific unsupported formats do not poison a healthy worker. A rejection
accompanied by a public session-disposal report disables neural use for the rest
of the preparer lifetime, preserving the rejection fallback reason. Unresolved
rejection cleanup still propagates rather than returning a fallback. Fatal provider
failure or request-budget exhaustion
disables neural use for that preparer's remaining lifetime, with no replacement
session. Later insufficient requests fall back with the recorded reason; a new
explicit preparer can retry. Sufficient-source calls still bypass optional work.
Invalid adapter input, configuration, thread/state errors and unexpected programming
errors propagate. Cleanup failures/unresolved reports also propagate rather than
becoming successful-looking Original fallback.

Cancellation is NeuralWorkerCancelled, never a PreparedImage. The shared Event is
checked before/after validation and runtime/readiness, after lookup, around decode/
inference/final processing, immediately before/after store and before every return
(including shortcut/hit/fallback). Cancellation outside an active M34 call closes
the owned session and preserves cancellation as primary with cleanup details.
Cancellation raised during failure cleanup follows the same terminal cancellation
path, preserving the primary cancellation and cleanup information. A cancelled
preparer does not restart, even if the Event is later cleared; both sufficient
and insufficient requests remain cancelled. Explicit close retains the normal
closed-object behavior.

Known cancellation before store prevents that store. Cancellation during synchronous
publication can leave a complete valid cache entry: return no result, preserve the
entry, and never delete/roll it back. Qt/Pillow/OS calls are not forcibly interrupted;
this is cooperative boundary checking, not a hard real-time cancellation promise.
The direct-child/I/O-thread and transient nvidia-smi limits documented in
[M34](neural-worker-client.md) remain unchanged.

## Evidence and remaining boundary

Focused tests use small asymmetric/transparent fixtures and public client-shaped
doubles, inspect actual pixels/PNG metadata and exercise readiness/cache identity,
fallback, cancellation/publication and lifecycle. The narrow decoder extraction
retains the Lanczos recipe/encoded-pixel hashes and the M25 805-byte golden descriptor
key. Exact results and commands are recorded in the private handoff.

The bounded Windows proof used the existing host Python 3.13.14, Qt/PySide6 6.11.2,
Pillow 13.0.0 and zlib 1.3.1.zlib-ng; external Torch 2.9.1+cu128, torchvision
0.24.1+cu128, Spandrel 0.4.2, NumPy 2.2.6 and safetensors 0.6.2 on RTX 4090.
It generated three derivatives at 600/900/1200, observed three warm hits and three
fresh-preparer persistent hits after current readiness, and verified normal child
shutdown/reaping/thread termination/temporary cleanup. No neural libraries were
imported in the host. These are technical software-pipeline checks, not typography,
colorimetric, artwork or physical print-quality qualification.

M35 remains an inactive source/recipe/final-target/cache bridge. M36 will own
existing batch/session adoption; M37 experimental UI/output qualification. No
neural processing is enabled in the application and no new default is selected.

# Standalone optional neural worker (M33)

M33 implements an inactive external process boundary, not application neural
adoption. Original/Lanczos remain the application baseline. There is no new UI,
normal startup dependency, cache producer, preparation-session mode, decoder,
alpha policy, layout change or PDF/PNG behavior change.

The process and exact JSON-lines v1 contract are in
[optional worker README](../../optional_providers/spandrel/README.md).
The reference is explicitly experimental `nomos_compact_2x`: Philip Hofmann's
NomosUni Compact multijpg LDL, pinned safetensors bytes, detected Compact/2×/3→3.
Attribution and separate code/weight license notes are in
[reference-model.json](../../optional_providers/spandrel/reference-model.json).
No weights are bundled and no faithful/default model is selected.

## Boundary and ownership

A host provides an explicit interpreter, checkpoint/pin/model/device and new
private session path. After one ready record it submits normalized RGB8 files
with dimensions and a digest. The worker verifies the exact samples, performs
one native inference, validates output and publishes native RGB8 samples plus
provenance. It neither decodes source files nor knows face identity, physical
geometry, PPI, documents or derivative-cache keys.

One model stays loaded for sequential requests. The process owns the fresh fixed
input/output directory layout; host request IDs derive filenames rather than
accepting arbitrary destinations. Positive strict dimensions and 4,194,304 input
pixels bound input reads; 8192-byte messages and 4096 consumed IDs bound protocol
state. These are worker capabilities, not universal application restrictions.
Immutable bytes bind checkpoint hashing to safetensors loading and source hashing
to tensor construction. CPU/precision/model/size/tiling substitutions are absent.

The fingerprint binds source implementation and actual provider/model/runtime
settings. A separate token identifies the live session without contaminating
stable identity with paths, PID or timing. Complete output is published without
replacement; only a matching result makes it usable. Request validation errors
do not infer; fatal runtime failures terminate without automatic recovery. There
is no live-job cancel command. Shutdown and EOF apply between jobs.

## Qualification evidence

The existing independently installed M22 environment and Compact acquisition were
reused read-only. Actual runtime: Python 3.13.14; Torch 2.9.1+cu128; torchvision
0.24.1+cu128; Spandrel 0.4.2; NumPy 2.2.6; safetensors 0.6.2; einops 0.8.2;
packaging 25.0. Device: RTX 4090, capability 8.9, CUDA runtime 12.8, cuDNN 91002,
driver 616.92. Runtime discovery, not just requirements, supplies the fingerprint.

Settings follow the qualified benchmark: FP32, eval/inference mode, seed 2200,
four Torch threads, TF32 disabled for matmul/cuDNN, cuDNN benchmark disabled and
deterministic enabled. Global deterministic algorithms were false. This does not
promise cross-machine bitwise reproducibility. Spandrel's descriptor (rather than
the raw model) is called; its padding must yield exact native dimensions.

Standalone tests cover strict protocol/types/IDs, resource/read limits, same-byte
verification/loading, descriptors/tensors/quantization, no-replace publication,
errors without success, sequential reuse, EOF/shutdown and process cleanup. They
run with an inert backend and no application fixtures; a child driver explicitly
blocks neural, Qt and application imports. The private handoff records counts
and commands. No unrelated application or historical benchmark suites ran.

The real final-code proof launched one child with an explicit argument list and
`shell=False`. It observed one ready record, two results, matching session/model
fingerprints and a normal shutdown/reap. Synthetic RGB inputs 186×260 and odd
61×83 produced exactly 372×520 (580320 bytes) and 122×166 (60756 bytes).
The parent independently verified every output byte length and SHA-256.
Startup/model load and per-request timings are private observations, separated
from stable identity; first inference includes first-use GPU initialization and
is not a steady-state benchmark.

Labelled native-2× PNG views show smoothed text, altered thin-line edges and
small border/color-edge changes. These establish local technical compatibility,
not preserved typography, recovered detail, human preference or physical print
quality. No real card corpus or M22 outputs were regenerated, and no printing or
cutting occurred.

## Precise M34 consumption contract (not implemented here)

The later host must launch this explicit optional child, validate ready protocol,
capabilities, stable fingerprint and session token, and own process timeout,
termination and reaping. It must maintain one outstanding request, unique IDs and
an exclusive private session. It writes closed RGB8 samples before submission;
on success it independently checks session/request/fingerprint, expected native
dimensions, RGB8 byte length, digest and fixed session-relative filename. Neither
a file without a record nor a mismatched/stale record is a usable derivative.

The host owns source orientation/color/alpha handling, any supported transparency
strategy, card/face identities, native-versus-final resizing, application target
planning, existing derivative schema and preparation lifecycle. It must describe
the actual complete recipe rather than inventing missing provenance. A 2× neural
output subsequently resized conventionally must stay labelled accordingly. Worker
input limits/OOM/unavailability must become explicit optional-provider outcomes.
Cancellation requires terminating/reaping the child and discarding the session,
not pretending an in-band message interrupts CUDA. Cleanup/retention, UI selection,
installation/distribution and model quality decisions require separate work.

No M34 caller, adapter or cache adoption is included in M33.

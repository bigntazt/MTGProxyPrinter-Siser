# Explicit neural worker client (M34)

M34 adds a synchronous host API for the optional M33 worker. It has **no normal
application caller**. Original/Lanczos remain the baseline, including the existing
600/900/1200-PPI targets. No decoding, alpha/ICC policy, final resize, derivative
cache, preparation mode, Qt task/session, settings, UI or output path changes.

## API and ownership

`mtg_proxy_printer.neural_worker_client` uses only the standard library. Importing
it does not import Torch, Spandrel, Pillow or Qt. Standalone tests load this file
directly to avoid the existing application package initializer; that initializer
was not changed. Construction of a session launches nothing.

```python
config = NeuralWorkerConfig(
    interpreter=worker_python, worker_script=worker_py, checkpoint=checkpoint,
    expected_sha256=compact_digest, model_id="nomos_compact_2x", device="cuda:0",
    private_parent=user_private_existing_directory)
cancel = threading.Event()
with NeuralWorkerSession(config, cancel) as session:
    ready = session.ready
    result = session.infer(immutable_rgb8_bytes, width, height)
    # result.rgb_bytes are verified native samples; no encoded image or PreparedImage.
```

Configuration is frozen, captures explicit absolute paths/pin/model/device and
finite positive timeouts, and supports only the declared Compact/cuda:0 worker.
`start()`, `infer()` and idempotent `close()` belong to the constructing owner
thread. Another thread may set the cancellation Event, matching the existing
batch's cancellation signal type without adopting/changing that batch.

`start()` returns frozen NeuralWorkerReady and may be repeated while idle without
launching another process. One worker/model serves multiple sequential calls;
there is at most one outstanding request. A closed, failed or cancelled session
cannot restart. There is no retry, restart, model substitution or fallback.

Ready includes a session token, accepted fingerprint, canonical provenance JSON
**bytes**, capability limits and immutable timing pairs. NativeRGBResult contains
immutable verified RGB bytes, actual dimensions, input/output digests, fingerprint,
the same canonical provenance bytes, session/request identity and timing pairs.
It contains no mutable internal dictionaries. Decode provenance JSON into a fresh
caller-owned value when needed; changing that copy cannot change accepted identity.

## Private process and bounded communication

The client creates one uniquely named owned directory under the explicit private
parent and passes a nonexistent `session` child to the worker. Binary pipes stay
separate and Popen uses an explicit argument list with `shell=False`. PYTHONPATH
and PYTHONHOME are removed in the child environment; bytecode writes are disabled
and Torch/CUDA cache paths point into the owned tree. Existing interpreters,
checkpoints and environments are neither changed nor installed by the client.

On Windows the caller must supply a user-private parent with appropriate existing
permissions. This is trusted local-process transport, not a filesystem sandbox
against another equally privileged process. Cleanup checks the owned root's
identity and parent before recursive removal; neighboring files and source/model
data are never cleanup targets.

One non-daemon stdout reader accumulates records up to **64 KiB**, with a
**four-record queue** and at most one bounded partial record. Queue insertion is
nonblocking; overflow sets a controlled fault rather than stranding the reader.
A separate non-daemon stderr drainer retains only the last **64 KiB of bytes**.
These limits are host policy, separate from the worker's advertised request-line
limit. A small per-request non-daemon writer handles bounded request messages so
a blocked/failed stdin write cannot block the owner's wait indefinitely. Completed
writer references are replaced, not accumulated across jobs.

Owner waits use monotonic absolute deadlines. Partial bytes and diagnostic traffic
do not renew them; cancellation is observed at most every 50 ms during waits.
There are no owner-path blocking pipe reads or select() assumptions for Windows
pipes. This polling interval is not a bound on OS creation, file I/O or teardown.

| Operation | Default seconds |
|---|---:|
| Startup | 120 |
| One complete inference operation | 300 |
| Graceful shutdown/exit | 5 |
| Terminate wait | 2 |
| Final kill/reap wait; separate I/O join budget of the same duration | 5 |

Tests may supply shorter positive finite deadlines. Runtime line, pixel and
request-count capabilities are validated and bounded by M33's supported limits.
The host generates lowercase UUID hex IDs internally for infer and shutdown,
avoiding case-variant collisions on case-insensitive filesystems. Callers choose
neither request IDs nor output destinations. Capacity for one shutdown ID is
reserved; exhaustion is reported before submitting another job, with no new worker.

## Readiness and result acceptance

Both sides explicitly decode UTF-8 before JSON parsing. Duplicate keys, nonfinite
numbers, unsupported protocol versions, bool-as-integer values and unexpected
record types/stages are rejected. M34's separate worker correction adds focused
UTF-16-BE/UTF-32-BE rejection without changing protocol version 1.

Before launching, the host hashes the **actual local file bytes** of the explicitly
selected worker.py, its protocol/backend and reference JSON. Ready must match those
hashes, the pinned model digest/size/ID/Compact/2x/3-channel identity, supported
RGB8/tightly-packed/sequential limits and no in-band cancel. It also validates the
actual runtime/settings structure and declared CUDA FP32 one-pass recipe, then
recomputes SHA-256 of canonical provenance and checks the advertised fingerprint.
Runtime versions remain reported facts; validation does not fabricate them.
The fingerprint is not an attestation against a malicious trusted local worker.

The ready session/fingerprint/provenance is frozen. Timings and session tokens stay
outside stable provenance. Runtime file-byte changes, including line-ending
changes, conservatively change source hashes; Git blob hashes are not substituted.
Historical M33 fingerprints are preserved separately.

Input must be exactly immutable bytes and positive integer dimensions, within the
accepted pixel limit, length width*height*3. The client hashes those samples and
writes/closes `input/job-<generated-id>.rgb8` before sending the request.

Result acceptance checks current protocol/session/ID/fingerprint and input echo,
native scale 2, exact 2W x 2H RGB8 dimensions, W*H*12 bytes and exactly the expected
`output/job-<generated-id>.rgb8` string. It constructs the output path itself;
an arbitrary response path is never joined. The ordinary owned-session file is
checked with type/length metadata, bounded read, exact byte length and independent
SHA-256. The very same immutable bytes are returned. Per-job input/output files
are released after verification; cancellation is checked again after validation
and immediately before return. A file alone or an unmatched/partial/stale record
is never a usable result. Duplicates cannot satisfy a later unique request.

## Errors and cleanup

NeuralWorkerError exposes stable `reason`, bounded `detail` (4096 characters),
bounded diagnostic tail and an immutable cleanup report when applicable. Reasons
distinguish invalid_config, wrong_thread, session_state, invalid_input,
request_budget, worker_rejected, worker_unavailable, protocol_error,
result_integrity, timeout, cancelled and cleanup_failed. NeuralWorkerCancelled
is a distinct subclass. Local pre-submission invalid input/budget failures leave
an idle session usable; a valid matching request_error may likewise be reported
without destroying recoverable state. A failed file cleanup after rejection
aborts the session while preserving the primary rejection.

Fatal, integrity/protocol, timeout and cancellation failures terminate the direct
worker, escalate to kill if needed, wait/reap, close pipes, join owned I/O threads
and only then remove the owned tree. Normal close sends a unique shutdown request,
validates the matching bye and normal exit, and performs the same final cleanup.
Close handles new/partial/failed/repeated flows. A cleanup limitation is recorded
without replacing a primary failure; cleanup can be retried by close after a
retained-tree failure. Context-manager exit preserves a body exception and adds a
close note rather than masking it. Cleanup reports pid/returncode, reap/thread/
temporary-tree status and bounded limitations, separately from stable provenance.

The guarantee covers the **direct worker and owned I/O threads** under normal OS
teardown. Failure to reap/stop threads retains the temporary tree and is reported;
daemon-thread exit is not cleanup. There is no generic descendant-tree isolation.
M33 still runs a transient nvidia-smi query during startup; M34 does not redesign
that probe or introduce Windows Job Objects. OS-level denial or inherited handles
in another process can limit teardown, so those limitations must remain visible.

## Evidence and future boundary

Qualified on Windows with Python 3.13.14: 25 client tests and three affected worker
parser tests passed. The focused standard-library suite checks
real test-child processes, protocol/provenance/file faults, absolute deadlines,
cancellation at startup/job/result acceptance, bounded diagnostics/traffic, blocked
request writing, reserved budgets, primary-error preservation and teardown/reaping.
The private handoff records exact commands/counts; application pytest fixtures and
unrelated suites are not used.

The real client used the approved existing M22 environment (Torch 2.9.1+cu128,
torchvision 0.24.1+cu128, Spandrel 0.4.2, NumPy 2.2.6, safetensors 0.6.2) and pinned
Compact acquisition read-only. One session returned the preserved 186x260 and
61x83 fixtures as 372x520/580320 bytes and 122x166/60756 bytes. Independently checked
output hashes match M33 exactly. Normal shutdown/reap, ended I/O threads and owned
session removal passed. The host imported no neural or Qt libraries. This is
client/worker compatibility, not typography fidelity or print-quality evidence.

Roadmap remains explicit:

- **M34:** inactive host client, implemented here.
- **M35:** source/recipe/final-target/cache bridge, not implemented. It owns source
  decoding/orientation, alpha/ICC policy, sufficient-source decisions, final
  conventional resize and Original fallback. A later recipe must preserve original
  image identity and declare the full current processing/runtime/model policy.
  Never infer current neural identity from an old cache manifest when the current
  worker has not established readiness.
- **M36:** existing batch/session adoption, not implemented.
- **M37:** experimental UI and output qualification, not implemented.

The client returns native samples only, with no PNG/physical-PPI/derivative policy.
No neural default is selected and the optional provider must not displace the
existing Original/Lanczos baseline.

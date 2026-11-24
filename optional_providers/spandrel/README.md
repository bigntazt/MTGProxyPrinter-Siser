# Optional Spandrel worker (M33)

This separately invoked experimental worker has **no application caller**. Normal
Original/Lanczos preview and exports do not import or launch it. It only accepts
normalized RGB8 samples and returns native 2× RGB8 samples. It knows nothing about
image formats, alpha, profiles, card identity, physical geometry or target PPI.

## Runtime and reference

Use an independently installed optional environment. The qualified baseline is in
[requirements.txt](requirements.txt); no automatic installation or downloading is
performed. The requirements are not application startup dependencies. M33 reused
the existing approved M22 runtime read-only; do not rebuild it to reproduce this.

The sole supported reference is **NomosUni Compact multijpg LDL**, model ID
`nomos_compact_2x`, by **Philip Hofmann**. Its acquisition pin, byte size,
Compact/2×/3-channel expectations, release and license links are in
[reference-model.json](reference-model.json). The
[release](https://github.com/Phhofm/models/releases/tag/2xNomosUni_compact_multijpg_ldl)
states [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The pin identifies locally acquired bytes, not a publisher-verified checksum.
No weights are bundled or modified. Retain attribution/source/license and review
pretrained-model lineage before any redistribution. Spandrel implementation code
is MIT; upstream SRVGG/Real-ESRGAN code is BSD-3-Clause. These are separate from
weight licensing. This experimental reference is not a faithful/default selection.

## Launch and sample exchange

Launch with an explicit interpreter and argument list, `shell=False`, separate
stdin/stdout/stderr pipes, and a new absolute session path under a private existing
parent directory. The worker creates the session and fixed `input`/`output`
subdirectories; an existing session is rejected. On Windows the host must supply
a parent with appropriate user-private permissions. This is local trusted-host
transport, not a filesystem sandbox against another process with the same access.

```python
command = [python_executable, "-B", worker_script,
           "--checkpoint", checkpoint_path,
           "--expected-sha256", "dec25af2672783ce2214c38d6a70caf8c3d8a640d6f4d87b543b264cafd74110",
           "--model-id", "nomos_compact_2x", "--device", "cuda:0",
           "--session-dir", fresh_absolute_session_path]
child = subprocess.Popen(command, shell=False, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
```

After `ready`, the host writes complete tightly packed row-major bytes to
`input/job-<id>.rgb8`, closes that file, then sends the request. There is no image
decoder or metadata in the transport. Byte length must be width × height × 3;
samples normalize as `float32(sample)/255`. Never silently drop alpha to satisfy
this contract: a later host owns supported alpha/color/orientation handling.

Protocol v1 uses one UTF-8 JSON object per line, flushing every response. stdout
is protocol-only; diagnostics (including CLI help) go to stderr. Images are files,
never JSON/base64. Example requests:

```json
{"protocol":1,"type":"infer","id":"probe_1","width":61,"height":83,"sha256":"<64 lowercase hex>"}
{"protocol":1,"type":"shutdown","id":"stop"}
```

All fields are exact, without coercions; bool is not an integer. IDs are unique
for the session: 1–64 ASCII alphanumeric/underscore/hyphen characters, beginning
alphanumeric. Prefixing filenames with `job-` also avoids Windows reserved stems.
Unknown fields/types, duplicate JSON keys, nonfinite JSON and malformed versions
are rejected. A syntactically valid ID is consumed even if the rest of its request
is invalid. Invalid IDs are not echoed. Dimensions must be positive integers;
the initial **worker** cap is 4,194,304 input pixels, checked before a complete
sample read/allocation. This is not an application-wide image-size policy.
Line length is capped at 8192 bytes; session state at 4096 valid IDs (including
rejected jobs and shutdown). Start a new worker after that bound.

## Records and lifecycle

- `ready`: protocol, unique `session` token, stable `fingerprint`, full `provenance`,
  capabilities and separate startup/model-load timing. One model initialization.
- `result`: protocol/session/id/fingerprint; verified input digest/dimensions;
  output digest, width/height, native scale 2, format RGB8, exact byte length and
  fixed session-relative `output/job-<id>.rgb8`; inference, quantization and
  publication timing observations. Only published complete output earns a result.
- `request_error`: protocol/session/id (or null), code/detail; no inference for
  this rejected request. The worker can accept another ID.
- `fatal`: protocol/session/id (or null), code/detail; exit status 1, no recovery.
  Argument-parser failures occur before a session token exists. Missing optional
  dependencies, rejected checkpoints, unavailable CUDA, descriptor/tensor failures
  and CUDA OOM are explicit fatal outcomes.
- `bye`: protocol/session, reason `shutdown` (and its ID) or `eof`; exit status 0.

There is one job at a time. EOF/shutdown is handled **between** jobs. There is no
in-band cancellation. A future host must terminate and reap the child to cancel
active inference, discard unmatched/partial outputs and use a fresh session.
No daemon, listener, pool, downloader, environment installer or cache is present.

The checkpoint is read once into bounded immutable bytes, checked against the
explicit pin and length, and that same payload goes to `safetensors.torch.load`
then `ModelLoader.load_from_state_dict`. No reopening for loading, pickle widening,
TorchScript, downloaded model code or extra architecture registry. The descriptor
must be ImageModelDescriptor/Compact/2×/RGB→RGB and is called in eval/inference mode.

The qualified recipe is CUDA FP32, one whole-image pass, seed 2200, four Torch
threads, cuDNN benchmark off/deterministic on, both TF32 flags off. M22 did not
enable `use_deterministic_algorithms`; actual state is recorded rather than
claiming cross-machine bitwise determinism. Descriptor-managed padding must end
at exactly `(2*width,2*height)`. No tiling/crop/reduction/repeated pass, CPU/FP16
fallback or final target resize. Tensor batch/channels/dimensions, CUDA device,
FP32 dtype, finite values and [0,1] range are validated. Quantization occurs once:
multiply by 255, nearest integer with ties to even (`torch.round`), uint8, tightly
packed HWC RGB. No extra clipping outside the descriptor's normal behavior.

Fingerprint is SHA-256 of sorted compact UTF-8 JSON of implementation/protocol
version, relevant source hashes (including reference config), verified model
identity, actual runtime packages/Python/device/CUDA/cuDNN/driver and material
settings. Session token, paths, PID, timing and changing memory readings are
outside stable identity. Results must match the ready token and fingerprint.

Output is written/fsynced to an exclusively created `.part` file, linked atomically
without replacing a destination, then reread against the completed payload before
success. Local hard-link-capable storage (qualified NTFS) is required; unsupported
publication is fatal, with no copy/overwrite fallback. Owned temporary files are
removed on failure; externally killed workers can leave artifacts. A file alone,
a partial file or a stale file without its matching success record is unusable.
Per-request samples/tensors are released; only model and bounded ID state remain.

## Standalone checks

Run without the application's pytest fixtures or optional dependencies:

```text
python -B -m unittest discover -s optional_providers/spandrel/tests -p test_worker.py -v
```

Inert backends exist only in test support, with no production CLI substitution.
The child-process driver blocks neural/Qt/application imports and is bounded by
a timeout/kill/wait test. Actual CUDA compatibility additionally requires the
private two-request proof; inert tests alone do not qualify the runtime.

See [architecture and M34 boundary](../../doc/architecture/neural-worker.md).

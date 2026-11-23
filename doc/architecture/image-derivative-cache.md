# Processed-image identity and cache (M25)

M25 adds an inactive backend in `image_derivative.py` and
`image_derivative_cache.py`. No normal application caller adopts it. There is no
provider, resizing, inference, model loader, scheduling or rendering/export/UI
integration. Original/Lanczos remains the M22 baseline; no neural default is
selected. Original source storage, M24 source inspection, M23 geometry and its
historical dynamic-bounds limitation are unchanged.

## API and immutable inputs

- `ProcessingRecipe.from_spec(spec)` validates and captures a recipe as immutable
  canonical JSON bytes. `to_spec()` returns a fresh decoded object.
- `build_derivative_request(snapshot, plan, recipe)` validates current M24 inputs
  and returns a DerivativeRequest retaining only its small canonical descriptor.
  `key` is SHA-256 of those exact bytes; `target_dimensions` is derived from that
  descriptor, never independently supplied. `to_spec()` returns a fresh object.
- `DerivativeCache(explicit_absolute_root)` is inert until called. Construction
  and lookup never create directories, initialize ImageDatabase, migrate, repair
  or delete entries. Use a separate derivative root, not the original cache.
- `lookup(request)` returns CachedDerivative or CacheMiss. `store(request,
  png_bytes)` returns CachedDerivative or CacheWriteFailure. The caller supplies
  already completed exact-target PNG bytes; this backend produces no pixels.

CachedDerivative has request_key, png_sha256, dimensions and immutable png_bytes.
Payload bytes are excluded from repr and automatic payload comparison. A lookup
returns the validated bytes, not a path that might change before a later open.
No decoded QImage, QPixmap, source payload, Card, document, model index or
SourceSnapshot is retained by a request/cache result. No memory cache is added.

## Recipe schema 1

The fixed envelope requires exactly these fields:

```json
{
  "schema_version": 1,
  "pipeline_version": "synthetic-v1",
  "provider": {"id": "synthetic", "version": "1"},
  "operation": "fixture",
  "model": null,
  "policies": {
    "decode": "declared-v1", "orientation": "none", "color": "preserve",
    "alpha": "preserve", "mask": "none", "resize": "synthetic-target",
    "encoding": "png-v1"
  },
  "parameters": {"nested": {"enabled": true, "options": [1, 2]}}
}
```

Pipeline/provider versions, IDs, operation and every policy are nonempty strings.
`none` is a valid explicit policy. Model is either null or exactly
`{"id": "nonempty-id", "checkpoint_sha256": "64-lowercase-hex-digest"}`.
Fixed objects reject missing/unknown fields and incorrect types; parameters is
the extensible JSON object. Model digest declares selected contents without
locating a checkpoint or requiring a provider to retrieve an existing derivative.

Only ordinary JSON scalars, arrays and objects with string keys are accepted.
Unsupported objects, tuples, bytes, non-string keys, cycles, NaN and infinity
are rejected. Serialization uses UTF-8, sorted keys, compact separators,
ensure_ascii=True and allow_nan=False. Array order is preserved. Conservative
differences such as 1 versus 1.0 may invalidate reuse. Caller dictionaries/lists
are never retained. Python hash() is not persisted.

Real provider builders must declare every applicable output-affecting setting,
including processing libraries, native scale, precision, tiling/overlap, seeds,
color/alpha/mask arguments, resampling and encoding settings. Envelope validation
cannot prove that a producer declared everything or followed its claimed recipe.

## Descriptor schema 1

The complete request object has exactly:

| Object | Fields |
|---|---|
| Root | schema_version=1, source, inspection, geometry, processing |
| source | image_key, sha256, native_dimensions, format |
| source.image_key | scryfall_id, is_front, image_quality (exact integer 1, 2 or 4) |
| inspection | decode_policy, qt_decoder_version |
| geometry | canonical_dimensions, target_ppi, target_dimensions |
| processing | Complete recipe object described above |

Dimensions serialize as two positive integer arrays; booleans cannot pass integer
validation. Source identifiers use the established canonical lowercase UUID
grammar; face is boolean and SHA-256 is 64 lowercase hex characters. Target PPI
is strictly integer 600, 900 or 1200. Format and inspection strings are nonempty.
The source-inspection Qt version is separate from the processing runtime declared
in a recipe. Its inclusion conservatively invalidates reuse, without claiming Qt
produced the derivative.

Construction requires immutable source bytes, recomputes their SHA-256 once and
rejects disagreement. It validates snapshot metadata and the complete recipe,
then checks all supplied plan fields against M24 plan_target_resolution, including
identity, dimensions, effective PPI and sufficiency. Source files are not reopened
or decoded again: M24 is the accepted inspection boundary. Public frozen M24
dataclasses are not assumed internally consistent merely because they are frozen.

No source/executable/checkpoint paths, timestamps, names, document placement,
copy counts, timing, errors, effective-PPI floats or sufficiency flag are stored
in the descriptor. Effective PPI and sufficiency are validated derived plan
fields, not additional identity inputs. The expected request must be built from
current source/settings, never obtained by trusting a cached manifest.

Canonical 300-DPI geometry and exact targets remain:

| Type | Canonical | 600 PPI | 900 PPI | 1200 PPI |
|---|---|---|---|---|
| Regular | 745 × 1040 | 1490 × 2080 | 2235 × 3120 | 2980 × 4160 |
| Oversized | 1040 × 1490 | 2080 × 2980 | 3120 × 4470 | 4160 × 5960 |

1200 is an optional maximum. Original/disabled operation need not create an
entry. Decisions about sufficient sources, processing and native-raster adoption
belong to later integration. There is no native-model-intermediate cache.

## Disk layout and validation

```text
<absolute-root>/v1/<request-key[:2]>/<request-key>/
    manifest.json
    <png-sha256>.png
```

Manifest schema 1 requires exactly schema_version, request_key, request (the full
descriptor), and output. Output requires exactly sha256, size_bytes, dimensions
and format="png". Size is a positive integer, excluding bool; dimensions must
equal the request target. Hashes must be validated lowercase SHA-256 strings.
There is no artifact-path field; the filename is derived from the digest.

The UTF-8 JSON reader rejects duplicate keys at any depth, unsupported schemas,
nonfinite numbers, malformed fields and incorrect scalar types. The stored
request is compared with the independently supplied descriptor using canonical
bytes and its recomputed digest, not loose Python dict equality (so 1.0 cannot
stand in for 1). Lookup reads/closes the manifest, then reads/closes its PNG once.
The same captured PNG bytes are checked for length, SHA-256, detected PNG format,
successful full QImageReader decode and exact target pixel dimensions.

Output validation uses QBuffer/QImageReader with autoTransform=False and leaves
Qt's allocation limit unchanged. Header-only images fail. No resize, re-encode,
crop, mask, alpha flattening, color conversion or metadata edit occurs. The
temporary decoded validation image is discarded; returned bytes remain exact.

CacheMiss reasons: not_found (no committed manifest), invalid_entry (inconsistent
or damaged entry, including a missing referenced PNG), and io_error (ordinary
filesystem access failure). CacheWriteFailure reasons: invalid_output or
write_failed. Details are concise diagnostics. Invalid programmer arguments can
raise TypeError/ValueError; unrelated programming errors are not swallowed.

## Publication and concurrency

Store validates incoming immutable PNG bytes completely before directory
creation or replacement. It then prepares metadata, creates the request
directory, publishes the hash-named PNG, and publishes manifest.json last. Only
successful manifest publication returns success: the manifest is the commit point.

Each write uses a module-private local QSaveFile constructed with str(path),
direct-write fallback explicitly disabled, WriteOnly, a complete-byte-count
check and successful commit(). Unfinished writes are cancelled on failure.
This follows the accepted prepared-output helper without importing UI code.
[Qt QSaveFile documentation](https://doc.qt.io/qt-6/qsavefile.html) describes
the temporary-file replacement and direct-write fallback behavior.

An existing committed manifest survives a failed replacement. A failed manifest
write may leave a completed orphan PNG. **Published old/orphan PNGs are
intentionally retained**, including during replacement and failure cleanup,
because another reader/writer may still reference them. QSaveFile handles its
own unfinished temporary output. Same-hash blobs are republished from validated
bytes rather than trusting an existing filename. There are no retries, locks,
cleanup, eviction, quota settings or migrations.

**The last successfully committed manifest wins.** A reader that captures A's
manifest can finish with A's retained blob after B commits. Two writers may
publish distinct complete blobs for one request; the final manifest references
one matching complete output. This is coherent publication, not scheduling,
exactly-once processing or in-flight deduplication. No power-loss durability,
network-share qualification or protection against arbitrary external deletion
is claimed. Cache integrity does not prove image fidelity or recipe compliance.

## Focused evidence and reproduction

Tested locally on Microsoft Windows 10.0.22631 with actual QSaveFile publication
and replacement. Separate runtime: Python 3.13.14, PySide6 Essentials/shiboken/Qt
6.11.2, pytest 9.1.1, pytest-qt 4.5.0, pytest-timeout 2.4.0, Pint 0.24.4.
Accepted dependencies were pinned in the isolated runtime; no package was added
to the application or any prior environment. Pip check passed.

The private review wrapper redirects platformdirs before application imports,
blocks real stores and network access, restricts SQLite to memory/review paths,
verifies the M25 package path, uses offscreen Qt and supplies a process-only A4
default. Final proof reports contain only M25 writable locations and no database
connections. No normal application is launched.

With `$Review` set to the private M25 review directory and `$Python` to its
runtime\Scripts\python.exe, from the M25 worktree:

```powershell
$env:PYTHONPATH = $null
$env:M25_RUN_LABEL = 'derivative-tests-final'
& $Python -B "$Review\run_isolated.py" pytest `
  tests/test_image_derivative.py tests/test_image_derivative_cache.py `
  -q -s --basetemp "$Review\pytest-derivative-final" --timeout=30
```

**116 passed in 8.86 seconds.** This includes a fixed 805-byte canonical
descriptor/key contract, existing UUID subtype compatibility, nested mutation/order checks, all four targets, fresh
worker lookup, alpha/metadata preservation, strict manifest corruptions,
self-consistent hashes around undecodable/wrong-sized PNGs, interrupted writes,
and deterministic interleavings. Libpng Read Error messages are expected from
intentional truncated-image cases, not test failures.

Fault injection wraps real QSaveFile to simulate PNG open, short-write, PNG
commit and manifest commit failures. Each preserves the prior committed entry;
only the manifest-failure case leaves the completed new orphan. A reader test
records capture-A, commit-B, return-A, next-return-B. A two-writer test records
PNG-A, PNG-B, manifest-B, manifest-A and then retrieves A. No timing sleeps,
production callbacks or stress infrastructure are used.

Private example artifacts include the complete descriptor, manifest, PNG and
independent verification hashes. The synthetic serialization fixture key is
`e817a0b7d4c3225d533726714604db6a1af679f57301302c64028be48746d089`.
Generated bytes and detailed results remain outside Git. No full application,
scene, source, benchmark, network-loader or hardware suite was run. No inference,
app launch, image-fidelity review or physical qualification occurred.

## Future adoption boundary

Future processing integration must supply honest complete recipes, select a
separate root, produce target-sized PNGs and build expected requests from current
sources/settings before lookup. Provider/library policy changes must change the
recipe. High-resolution composition, job management, cache retention policy,
settings, rendering and export adoption all remain deferred. M22 research records
are not production entries. This milestone implements no M26 work.

# Original-image sources and resolution planning (M24)

M24 adds a read-only API in `mtg_proxy_printer/image_source.py`. No rendering,
export, settings or UI call site adopts it. Original/Lanczos remains the M22
baseline, and M23's raster/physical-geometry contract is unchanged.

## Existing originals, not another cache

The downloader already writes response bytes to a temporary file, validates the
image, then moves those bytes into the original disk cache. Its subsequent
canonical QPixmap normalization does not rewrite that disk file. The earlier
architecture implication that another preservation mechanism was needed was
incorrect. M24 reads those existing originals directly. CustomCard already
retains original encoded bytes in `source_image_file`; its normalized/masked
`image_file` is not source material. Historical M22 results remain unchanged.

No ImageDatabase is constructed to obtain a path: construction initializes and
migrates storage. No fallback to memory pixmaps, alternate qualities/faces or
custom overrides is attempted. No downloader or existing production module is
changed.

## Capture, load, plan

`capture_source_request(card, *, cache_root)` runs where model metadata is safe
to access. It returns a frozen SourceRequest or SourceUnavailable. The explicit
cache_root may be None for custom/composite sources, where it is unused;
ordinary cards require an explicit root. A relative root is made absolute at
capture time without opening image files. The request retains only ImageKey,
an immutable `(canonical_width, canonical_height)` integer tuple, and exactly
one of an absolute path or immutable custom bytes. It retains no card, model
index, document, ImageDatabase, pixmap or decoded image.

Selection order is deliberate:

1. CheckCard returns `unsupported_composite` before inspecting delegated metadata
   or composing its faces. Explicitly supplied front/back Card instances can be
   captured separately; high-resolution composition remains deferred.
2. CustomCard uses `str(card.scryfall_id)`, its face flag and
   ImageQuality.custom_card. It reads source_image_file, never image_file or
   post_process_image, and does not use ImageQuality.of_card. The existing UUID
   remains logical metadata; its MD5 derivation is not content verification.
3. Ordinary Card uses its existing printing UUID, exact face and existing
   high/low metadata category. Only `root / key.format_relative_path()` is
   selected. The established canonical lowercase UUID grammar is checked before
   path construction, so malformed identity cannot become a path fragment.
4. Empty printing identity together with empty image URI identifies an existing
   placeholder without looking at pixmaps. Other invalid identities return
   `invalid_source_identity`.

Metadata mutation after capture cannot change a request. Capture freezes the
intended path, not its contents. A later worker can call
`load_source_snapshot(request)` without any model access or QPixmap creation.
It reads the file once (or uses captured custom bytes), computes SHA-256 and
decodes the same buffer, then releases the decoded QImage. The frozen snapshot
retains ImageKey, digest, original encoded bytes, native decoded dimensions,
captured canonical dimensions, detected format, decode-policy ID and Qt version.
Replacing a disk file after that read does not alter an existing snapshot.

Both request and snapshot exclude encoded payloads from repr and automatic
equality comparisons, and are explicitly unhashable. Their general transport
equality is not a content/deduplication contract. Use `snapshot.identity`, a
small frozen SourceIdentity of ImageKey and SHA-256. Equal copies/locations and
modification times do not change that identity; face, quality or byte changes do.
Names, document/page/row placement, copy count, paths and physical size are not
raw-source identity. Python hash() is never persisted.

## Decode policy

`qt-first-frame-no-auto-transform-v1` uses QImageReader over a QBuffer containing
the exact hashed bytes, with format autodetection and autoTransform explicitly
false. A real read must succeed; a readable header alone is insufficient. The
format is captured before read because Qt's JPEG handler can clear format()
after successful decoding. Format comes from content, not the cache's .png
suffix. A first-frame read is the only image decode.

There is no resize, crop, rotation, mask, color-space conversion, alpha
flattening or global allocation-limit change. Native dimensions are decoded
pixel width/height, not device-independent dimensions. Embedded orientation is
not applied. Embedded DPI does not determine physical or target dimensions.
All encoded metadata remains byte-for-byte intact. This is an inspection policy,
not provider preprocessing or a future color-management choice. Qt's available
codecs and unchanged allocation limits can make a source undecodable; inspection
does not promise compatibility with every format/size. Only Qt APIs available
before 6.12 are used.

Expected outcomes have stable reason codes and concise detail:
`unsupported_composite`, `empty_placeholder`, `invalid_source_identity`,
`missing_file`, `unreadable_file`, and `undecodable_image`. File errors are caught
as FileNotFoundError/OSError; unrelated programming errors propagate. A missing
explicit root or unsupported caller type is an API usage error. Outcomes never
download, initialize/migrate directories, delete corrupt files, rewrite bytes,
repair storage or replace ordinary rendering.

## Explicit resolution planning

`plan_target_resolution(snapshot, target_ppi)` accepts only integer 600 or 1200
(including rejection of bool, floats and strings), with no default. It derives
target pixels from captured canonical dimensions and existing RESOLUTION at
300 dots/inch. It reports source identity, canonical and source dimensions,
requested PPI, exact target dimensions, effective source PPI on each axis and
`sufficient`. It does not select Original/Lanczos/neural processing or touch pixels.

| Physical type | Canonical | 600-PPI target | 1200-PPI target |
|---|---|---|---|
| Regular | 745 × 1040 | 1490 × 2080 | 2980 × 4160 |
| Oversized | 1040 × 1490 | 2080 × 2980 | 4160 × 5960 |

Each effective PPI is `source_axis * 300 / canonical_axis`. Sufficiency is exactly
`source_width >= target_width AND source_height >= target_height`. One sufficient
axis, total area or average PPI cannot substitute. Exact targets remain reported
when the source exceeds them. A sufficient source needs no enlargement; no
downsampling or restoration decision follows. Unusual aspect ratios remain
visible through actual dimensions and unequal per-axis PPI, with no correction.

For the same 1490 × 2080 custom bytes/logical identity:

| Captured physical size | 600 target | Effective source PPI | Sufficient |
|---|---|---|---|
| Regular | 1490 × 2080 | (600, 600) | yes |
| Oversized | 2080 × 2980 | (429.8076923, 418.7919463) | no |

The same identity therefore produces different plans. For regular 1490 × 2079,
the 600 target is still 1490 × 2080 and sufficiency is false.

## Focused validation

The private M24 review directory retains a separate runtime, resolved pinned
requirements, setup log, storage/network isolation wrapper, test logs and
synthetic source/plan records. The wrapper establishes platformdirs isolation
before application settings/logging imports, blocks real stores/network,
restricts SQLite to memory/review locations and verifies the imported package
comes from the M24 worktree. Runtime Scripts is on PATH; A4 is process-only.

Actual versions: Python 3.13.14, PySide6 Essentials/shiboken/Qt 6.11.2,
pytest 9.1.1, pytest-qt 4.5.0, pytest-timeout 2.4.0, Pint 0.24.4,
platformdirs 4.12.3, setuptools 84.0.0, build 1.6.1. No packaging workaround,
image-fixture dependency or enhancement runtime was added; pip check passed.

PowerShell from the M24 worktree, with `$Review` set to its private review
directory and `$Python` to `runtime\Scripts\python.exe` there:

```powershell
$env:PYTHONPATH = $null
$env:M24_RUN_LABEL = 'source-tests-final'
& $Python -B "$Review\run_isolated.py" pytest tests/test_image_source.py `
  -q --basetemp "$Review\pytest-source-final" --timeout=30
```

**38 passed in 1.72 s.** Coverage includes exact selection/no fallback,
no model-pixmap access/cache initialization, capture independence, a read-once
file-replacement race, identity separation, payload transport policy, read-only
failures, misleading suffix/header-only rejection, both sizes/targets,
per-axis thresholds, unchanged metadata/alpha/color bytes, EXIF orientation,
unchanged allocation limit, first GIF frame only and worker loading without
model/pixmap access. Initial development validation caught a JPEG format timing
issue (37 passed, 1 failed); recording format before read resolved it without
weakening the test. Isolation reports show no database connections and only
M24 writable locations.

No rendered-page, visual-quality, native interactive, printer/cutter or complete
export qualification is claimed. No scene, full application, network-loader,
benchmark/inference or hardware suite was run.

## Later integration boundaries

SourceIdentity is not a derivative-cache key. Such a key still needs physical
and target dimensions plus the full processing recipe: applicable model and
checkpoint, decoder/version, color, alpha and resizing policies. No derivative
key/cache, duplicate raw cache, provider, scheduler, output acquisition or UI is
implemented. Availability outcomes permit a later caller to keep ordinary
rendering working, but M24 introduces no caller or fallback integration.

Qt API reference: [QImageReader](https://doc.qt.io/qt-6/qimagereader.html).

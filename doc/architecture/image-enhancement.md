# Optional image enhancement: M22 architecture decision

M22 adds standalone research tooling only. It does not change application dependencies,
image loading, cache behavior, physical geometry, rendering, settings or output jobs.
The benchmark findings and unresolved selection questions are in
[image-enhancement-benchmark.md](image-enhancement-benchmark.md).

## Physical dimensions and raster detail are separate contracts

The accepted source is
[units_and_sizes.py at M21](https://github.com/bigntazt/MTGProxyPrinter-Siser/blob/c7daa581303276fab37ab435e4bcec1da85cf354/mtg_proxy_printer/units_and_sizes.py).
Its canonical layout resolution remains 300 DPI:

| Type | Canonical layout units | Physical size, inches | 600-PPI raster | 1200-PPI raster |
| --- | --- | --- | --- | --- |
| Regular | 745 × 1040 | 745/300 × 1040/300 | 1490 × 2080 | 2980 × 4160 |
| Oversized | 1040 × 1490 | 1040/300 × 1490/300 | 2080 × 2980 | 4160 × 5960 |

These dimensions must not be replaced with nominal 63 × 88 mm dimensions.
A higher-resolution image supplies more samples inside the same physical rectangle.
1200 is an optional maximum, not a minimum, an automatic default, or a reason to
enlarge an already sufficient source.

## Source inspection and later integration boundaries

The following observations concern accepted source read as text. The benchmark
does not import or initialize any of these modules.

| Existing source | Observed constraint | Later integration implication |
| --- | --- | --- |
| async_tasks/image_downloader.py, _fetch_and_set_image | A loaded/downloaded pixmap is scaled to the canonical blank size when dimensions differ. | Passing benchmark rasters through this path would discard target-resolution detail. Downloaded response bytes already remain in the existing disk cache; normalization changes only the pixmap. Read those originals directly; enhancement derivatives need a separate path, not another raw cache. |
| model/imagedb.py, ImageDatabase and on_image_obtained | The current cache tracks ImageKey/pixmap entries and initializes/migrates on construction. | Do not initialize it for research. Future derivatives must not replace the existing source cache. |
| model/imagedb_files.py, ImageKey | Keys distinguish printing identity, front/back and quality category, not enhancement recipes. | A future enhancement key needs source and checkpoint hashes plus a complete recipe identity; retain face identity. |
| model/card.py, post_process_image | Custom images are normalized to canonical dimensions and may have corner masking applied. | Use the existing source_image_file bytes directly. Do not route high-resolution comparisons through this normalization. |
| model/card.py, requested_page_type | When an image is present, some paths infer oversized status by comparing bitmap dimensions with the canonical oversized size. | Raster pixel count must cease being the authority for physical card size before any production high-resolution payload is introduced. |
| page_scene/items.py, _create_pixmap_item and corner/bleed construction | Graphics pixmaps and image dimensions participate in item/corner geometry. | A later design must map the high-resolution pixmap into an explicit canonical item rectangle while keeping bleed, marks, spacing and corners in existing layout units. |
| print.py, _render_graphics_scene | The painter maps canonical scene units using output DPI divided by the existing 300-DPI resolution. | Keep that physical transform. Increasing raster detail is not a global scene-resolution change. |

No changes to these paths are included in M22.

## Optional external provider, not a mandatory application dependency

If later authorized, keep a local enhancement worker and its PyTorch/Spandrel
environment optional and separate from the application runtime. The application
must remain usable with Original or conventional resizing when the provider,
weights, CUDA, or resources are unavailable. Do not import PyTorch into ordinary
application startup as a side effect of this benchmark.

Preserve original bytes. Store any future derivative separately and associate it
with source hash, face identity, checkpoint hash, loader/runtime versions, color
and alpha policy, native scale, precision, requested target and post-inference
resize. Never silently repeat AI passes or label a 2× output as native 4×.

The M22 resumable records are research artifacts, not a production cache design.
Production cancellation, job scheduling, UI selection, provider protocol,
environment lifecycle, cache migration and storage limits require later design
and authorization. M22 implements none of them.

## Limits and decisions still requiring review

Typography, punctuation, mana/set symbols, thin borders and artwork must be
assessed together. A visually attractive art region does not justify a faithful
whole-card default. Synthetic PSNR cannot qualify Magic artwork or guarantee text
correctness. A sharpened glyph is not necessarily the original glyph.

Model code and checkpoint licenses are separate. No checkpoint is bundled here.
The Nomos releases identify Philip Hofmann and state CC BY 4.0; attribution and
pretrained-model lineage need to accompany any later distribution decision.
SwinIR and Real-ESRGAN repository code licenses alone are not treated as an
independent grant of checkpoint redistribution rights.

Full-resolution PNGs and local preview sheets remain private. M22 generates no
printable PDF and performs no physical print/cut test. Any later proof must embed
the actual target pixels at the canonical physical size without application
normalization.

## M23: item-local raster geometry seam

M23 addresses the physical-size and scene-mapping prerequisites identified above.
Card and CustomCard now classify page type from existing size metadata; CheckCard
still delegates. CardItem accepts an optional keyword-only native QPixmap and
maps only its artwork child into the canonical rectangle. A canonical style
proxy supplies decorations; default component-based CheckCard corner colors
remain unchanged. Acquisition, cache entries, composition, persistence, page
geometry and printing are unchanged.

See [Card raster and physical geometry](card-raster-geometry.md) for contracts,
focused validation and offscreen reference limitations. This seam enables no
provider, target selector, derivative cache, high-resolution CheckCard composition
or complete 600/1200-PPI export workflow. Original/Lanczos remains the baseline;
no neural default has been selected. Later integration remains optional and
external as described above.

## M24: original source capture and resolution planning

The downloader already preserves encoded original response bytes in its disk
cache before normalizing memory pixmaps. No new raw-source preservation cache
is required. CustomCard likewise already retains encoded source_image_file.
This corrects the earlier preservation implication without changing M22 evidence.

[Original-image sources and resolution planning](image-source-planning.md)
describes the new read-only capture/load/plan API. Requests freeze face/quality
identity, canonical dimensions and one path or custom byte payload. Snapshots
hash and validate the same bytes with a versioned first-frame/no-auto-transform
Qt policy. Explicit 600/1200 planning reports exact targets, per-axis source PPI
and both-axis sufficiency. No production caller adopts this API in M24.

SourceIdentity is logical ImageKey plus SHA-256, not a derivative-cache key.
Physical/target dimensions and the complete decoder/color/alpha/model/resize
recipe remain later requirements. Rendering, export, acquisition, composition,
settings, UI and existing cache behavior are unchanged. No processing mode or
neural default is selected.

## M25: inactive processed-image identity and cache

[Processed-image identity and cache](image-derivative-cache.md) specifies a frozen
canonical recipe/request contract and a separate explicit-root PNG cache. The
request includes original content/face/quality identity, source-inspection
provenance, physical/target dimensions and the complete declared processing
recipe. Lookup checks the manifest against that independently built request and
returns the same immutable PNG bytes it validated.

Store validates completed target-sized PNG bytes before publishing a hash-named
blob and then an atomic manifest commit. Last successful manifest wins; old and
orphan blobs remain for coherent readers. Cache integrity does not establish
image fidelity or producer compliance. Real provider builders still must declare
all applicable settings.

No production caller adopts this backend. Originals, M24 inspection, M23 geometry,
settings, rendering, export and existing cache behavior are unchanged. There is
no provider execution, processing, in-flight deduplication, retention/cleanup,
neural default or M26 integration. M22 benchmark artifacts remain research only.

## M26: inactive Original/Lanczos preparation service

[Original and Lanczos image preparation](image-preparation.md) documents the new
synchronous, single-image coordinator. Original returns exact captured bytes
without Pillow or cache access. Optional Pillow Lanczos resolves a current-runtime
recipe, reuses M25 first, and generates only on misses. Outcomes distinguish
cached/new/uncached derivatives and explicit Original fallback. Shared source/plan
validation preserves the M25 descriptor/key contract.

Processing follows M24's first-frame/no-auto-orientation policy, not M22's EXIF
transpose/sRGB research policy. It normalizes supported samples to straight RGBA,
uses associated-alpha full-image Lanczos, preserves only Qt-exposed valid RGB ICC,
and encodes RGBA8 PNG without source EXIF/DPI/text. No color conversion, mask or
neural provider is introduced. An explicit Lanczos choice still normalizes/encodes
sufficient sources; Original retains its actual native dimensions.

The image-processing extra declares Pillow>=13.0,<14. No normal application caller
adopts the service. Custom forced-corner masking, CheckCard composition,
rendering/export adoption, scheduling, UI/settings and cache retention remain
later concerns. Existing behavior and physical geometry are unchanged. No neural
default has been selected; no recovered-detail or physical-quality claim is made.

## M27: inactive background preparation batches

[Background image preparation](image-preparation-batch.md) adds a sequential
AsyncTask and owner-thread controller over the accepted M24–M26 services. Frozen
captured requests are grouped by exact locator and then validated content metadata.
Only explicitly accepted results become alias targets, including uncached success.
A single-slot mailbox, lightweight queued wakeups, cooperative cancellation and
batch/delivery IDs bound payload buffering and suppress stale deliveries.

Progress counts accepted occurrences. Consumer failure takes precedence over
cancellation, and terminal handling releases active state before finished.
Existing runner/progress/shutdown infrastructure and processing/cache policies
are unchanged. Source unavailability and Original fallback remain explicit.

No normal application caller adopts this service. Future document/settings
integration must capture requests safely, retain result-ID representations and
cancel/invalidate work when relevant state changes. M27 does not observe documents,
provide a new global scheduler, or implement UI/export adoption, masking,
composition, neural providers or M28.

## M28: explicit native raster presentation

[Prepared raster presentation](prepared-raster-presentation.md) adds a GUI-thread
decoder from accepted PreparedImage bytes to an owned native-resolution QPixmap
with immutable identifying metadata. An optional PageScene in-memory resolver
validates ordinary-card identity/size and supplies M23's existing raster seam,
including when the legacy image is unavailable. Explicit refresh reconstructs
items for adoption or invalidation; model images and physical geometry stay intact.

Original/fallback pixels keep their native size. Both 600/1200 targets reach the
explicit scene and synthetic software PDF proof without canonical downsampling.
Default scenes remain pixel-identical. No normal application/export caller adopts
this interface. A later document/session binding must own source/settings freshness,
generation, result lifetimes and invalidation/refresh. Custom-card presentation
masks and high-resolution CheckCard composition remain required follow-ups before
their adoption. No UI/settings, neural provider or subsequent milestone is added.

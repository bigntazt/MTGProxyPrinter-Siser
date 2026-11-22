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
| async_tasks/image_downloader.py, _fetch_and_set_image | A loaded/downloaded pixmap is scaled to the canonical blank size when dimensions differ. | Passing benchmark rasters through this path would discard target-resolution detail. Preserve acquired originals before this normalization; enhancement derivatives need a separate path. |
| model/imagedb.py, ImageDatabase and on_image_obtained | The current cache tracks ImageKey/pixmap entries and initializes/migrates on construction. | Do not initialize it for research. Future derivatives must not replace the existing source cache. |
| model/imagedb_files.py, ImageKey | Keys distinguish printing identity, front/back and quality category, not enhancement recipes. | A future enhancement key needs source and checkpoint hashes plus a complete recipe identity; retain face identity. |
| model/card.py, post_process_image | Custom images are normalized to canonical dimensions and may have corner masking applied. | Preserve raw custom sources independently. Do not route high-resolution comparisons through this normalization. |
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

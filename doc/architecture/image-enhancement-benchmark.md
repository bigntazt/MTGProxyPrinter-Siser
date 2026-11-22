# M22 image-enhancement benchmark

M22 is standalone research, based on accepted M21
c7daa581303276fab37ab435e4bcec1da85cf354. It introduces no application
enhancement integration and no production dependency changes.

## Reproduce in a separate environment

Provide your own absolute review/output path, approved base Python interpreter,
read-only image-cache path, and font. No private corpus, machine paths, checkpoints,
or generated images are committed.

PowerShell inputs:

~~~powershell
$Review = $env:M22_REVIEW
$BasePython = $env:M22_BASE_PYTHON
$Cache = $env:M22_SOURCE_CACHE
$Font = $env:M22_FONT
# Run from the M22 worktree, never the stable application checkout.
& $BasePython -m venv "$Review\runtime"
$Python = "$Review\runtime\Scripts\python.exe"
& $Python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128
& $Python -m pip install -r benchmarks/enhancement/requirements.txt
& $Python -m pip check
~~~

The CUDA build follows the official
[PyTorch 2.9.1 instructions](https://pytorch.org/get-started/previous-versions/#v291).
Do not update drivers or install into the application venv. The measured host used
Python 3.13.14, torch 2.9.1+cu128, torchvision 0.24.1+cu128, Spandrel 0.4.2,
Pillow 11.3.0, numpy 2.2.6, safetensors 0.6.2, einops 0.8.2 and packaging 25.0.
The GPU was an RTX 4090, 24564 MiB reported memory, driver 616.92, CUDA runtime
12.8, cuDNN 91002. Complete resolved packages and environment JSON are private
review artifacts. Normal desktop GPU activity was not disabled; timing ranges
are observations on this host, not hardware guarantees.

Download only the six URLs in
[model-manifest.json](../../benchmarks/enhancement/model-manifest.json) into
the separate models directory and compare every SHA-256 with that manifest.
The hashes identify acquired bytes. No publisher asset digest was present in the
captured release metadata, so none is described as publisher-verified.

~~~powershell
New-Item -ItemType Directory -Force "$Review\models" | Out-Null
$Models = (Get-Content benchmarks/enhancement/model-manifest.json -Raw | ConvertFrom-Json).models
foreach ($Model in $Models) {
    $File = Join-Path "$Review\models" $Model.filename
    curl.exe -fL $Model.url -o "$File.part"
    if ($LASTEXITCODE -ne 0) { throw "Checkpoint download failed" }
    if ((Get-FileHash -LiteralPath "$File.part").Hash.ToLowerInvariant() -ne $Model.sha256) {
        throw "Checkpoint hash mismatch"
    }
    Move-Item -LiteralPath "$File.part" -Destination $File
}
& $Python -B scripts/enhancement_benchmark.py synth --output "$Review\corpus" --font $Font
& $Python -B scripts/enhancement_review.py prepare --cache $Cache --output "$Review\corpus"
& $Python -B -m unittest discover -s benchmarks/enhancement/tests -p 'test_*.py' -v
~~~

The sampler copies at most ten existing PNG faces, including available front/back
pairs and low-resolution-tagged sources. Originals are hashed before and after
copying. A private manifest records face identity, hashes, source dimensions and
paths. Existing cache PNGs may already have undergone application normalization;
they are not claimed to be pristine upstream downloads. The measured corpus
contained ten real faces and seven synthetic faces. Real oversized/custom-card
originals were unavailable in the selected cache; synthetic probes cover those
technical cases. Corpus selection is small and not statistically representative.

Synthetic cases include dense small text/numbers/punctuation, colored geometric
symbols, thin lines and borders, gradients/noise texture, JPEG quality-40
degradation, explicit alpha, clean digital content, an already-sufficient source,
and an oversized case. Each has a known 1200-PPI truth image and a recorded seed,
font hash and downsampling recipe. No synthetic result qualifies actual artwork.

## Resolution, color and inference contract

Canonical layout remains 745 × 1040 regular / 1040 × 1490 oversized at 300 DPI.
The standalone harness does not import the application.

| Type | 600 PPI | 1200 PPI maximum |
| --- | --- | --- |
| Regular | 1490 × 2080 | 2980 × 4160 |
| Oversized | 2080 × 2980 | 4160 × 5960 |

Every candidate maps the complete face into the same canonical target rectangle,
with no model-specific crop, padding or art extraction. Source aspect-ratio
difference is recorded; small 744-versus-745-pixel and synthetic ratio differences
are stretched consistently. Descriptor internal size padding is removed by the
descriptor and is not a tiling strategy.

Records keep source dimensions, native neural scale/dimensions, final dimensions,
and the conventional post-inference resize separate. A 2× neural result resized
to either target is labeled **2× neural upscale followed by conventional resizing**.
No repeated AI pass is used. Successful native inference is shared by both target
derivatives. Sources sufficient in both selected dimensions skip AI for that
target. Original remains at acquired source dimensions; it is not falsely labeled
as a target-resolution raster. Lanczos maps to the exact target.

Color processing transposes EXIF orientation, converts embedded ICC profiles to
sRGB through Pillow/LCMS using its default perceptual intent, assumes untagged RGB/grayscale is sRGB, and rejects
untagged CMYK. Resizing occurs in gamma-encoded sRGB, not linear light. Native
float output is rounded once to PNG8 before generating shared derivatives.
This quantization is explicit; the benchmark is not a float-output image archive.

The default alpha policy rejects nonopaque neural cases. The actual comparison
used explicit --alpha-policy resize: infer straight RGB without a matte, resize
the original source alpha independently with Lanczos, restore it to native
output, then resize the full RGBA image. Alpha is not reconstructed by AI.
Hidden RGB can affect visible edge neighborhoods; inspect corner/alpha previews.
Controls preserve alpha. This is an experimental supported path, not production
alpha qualification.

[Spandrel 0.4.2](https://github.com/chaiNNer-org/spandrel/tree/v0.4.2) supplies
ModelLoader and ImageModelDescriptor. The harness calls the descriptor in
eval/inference mode, validates RGB channels, input/output tensor shape, FP32,
CUDA device, finite [0,1] range and expected native dimensions. It keeps the
pinned loader's restricted .pth unpickler; it does not claim weights_only=True.
No arbitrary TorchScript, downloaded model code, extra architecture registry,
diffusion workflow, global FP16 or automatic fallback is used.

All neural cases use FP32 whole-image inference and one GPU job at a time.
SwinIR/HAT descriptors do not advertise FP16 support; SwinIR discourages tiling.
An OOM is a recorded failure. No CPU, precision, source-size or tiling fallback
was attempted in this benchmark.

## Two-stage execution

~~~powershell
$Harness = 'scripts/enhancement_benchmark.py'
$Manifest = 'benchmarks/enhancement/model-manifest.json'
$Common = @('run', '--model-manifest', $Manifest, '--models-dir', "$Review\models",
            '--corpus', "$Review\corpus\manifest.json", '--output', "$Review\results",
            '--targets', '600', '1200', '--alpha-policy', 'resize', '--repeats', '2')
$Screen = 'original,lanczos,nomos_span_2x,nomos_compact_2x,swinir_psnr_2x,realesrnet_4x,realesrgan_2x,nomos_hat_4x'
& $Python -B $Harness @Common --models $Screen --samples synth_dense,synth_texture_jpeg,real_01
Copy-Item "$Review\results\run-index.json" "$Review\screening-index.json"
$Finalists = 'original,lanczos,nomos_span_2x,nomos_compact_2x,realesrnet_4x,realesrgan_2x'
& $Python -B $Harness @Common --models $Finalists
Copy-Item "$Review\results\run-index.json" "$Review\full-corpus-index.json"
& $Python -B scripts/enhancement_review.py report --results "$Review\results" --corpus "$Review\corpus\manifest.json" --output "$Review\review-previews" --samples synth_dense,synth_texture_jpeg,synth_alpha,synth_oversized,real_01,real_02,real_05,real_09
~~~

The actual first setup attempt could not import Spandrel because its import path
needed packaging, not declared by the installed dependency metadata. That attempt
is preserved separately; packaging 25.0 was added to this benchmark environment
and requirements before the working comparison. pip check alone did not detect it.

Each native run records model load time (including descriptor construction and
device transfer), first descriptor call for the source shape, whether it was
first after model loading, two GPU-synchronized warm calls, peak PyTorch allocated
and reserved memory, and host transfer/quantization. Final resizing and PNG
encoding/decode validation are timed separately. Warm times do not include
encoding, model loading, or complete cold process startup. Reserved memory can
include allocator capacity retained from previous samples in the same model
session; desktop GPU memory is outside the PyTorch counters.

Resume validates recipe identity, output hash, decode and dimensions. Recipes
include source/checkpoint hashes, target, card type, actual package versions,
GPU/driver/CUDA, precision, seed, backend settings, alpha/color policy, repeat
count and executed harness hash. Temporary outputs and records are committed
atomically only after validation. Failure/skipped/unsupported records are not
treated as reusable successes. A shared models-directory lock prevents concurrent
benchmark jobs. A stale lock after an abnormal process kill requires deliberate
inspection, not automatic deletion.

## Measured results and decision

The screen used the same three sources for all six candidates at both targets.
It produced 46 successes and two OOM target rows. Those two rows refer to one
native HAT failure on a 744 × 1040 real face, not two independent inference
attempts. HAT requested another 10.07 GiB; no fallback was attempted.

SPAN, Compact, RealESRNet and RealESRGAN then ran on all 17 faces. Each produced
16 successes plus one sufficient-source skip **at each target**. Original and
Lanczos each produced 17 successful controls per target. The full-corpus stage
therefore contains 196 successes and eight skips. Combining stages without
double-counting reused records gives 216 records: 206 success, eight skipped,
two OOM; 69 successful native PNGs validate.

| Candidate | Coverage / target outcomes, both targets combined | Load seconds | Warm native seconds, observed range | Peak allocated / reserved MiB, successful cases |
| --- | --- | --- | --- | --- |
| Nomos SPAN 2× | Full corpus: 32 success, 2 skip | 0.041–0.046 | 0.016–0.100 | 2868 / 3806 |
| Nomos Compact 2× | Full corpus: 32 success, 2 skip | 0.011 | 0.018–0.097 | 392 / 500 |
| SwinIR PSNR 2× | Screen: 6 success | 0.285 | 1.160–4.361 | 7857 / 9646 |
| RealESRNet 4× | Full corpus: 32 success, 2 skip | 0.247–0.249 | 0.294–5.789 | 6127 / 10788 |
| RealESRGAN 2× | Full corpus: 32 success, 2 skip | 0.208–0.225 | 0.053–0.326 | 1611 / 2252 |
| Nomos HAT 4× | Screen: 4 success, 2 OOM | 0.584 | 2.950–3.000, small probes only | 7882 / 10998, successful cases only |

The inference time belongs to a native recipe and is shared by its two target
derivatives; it is not a separate 600/1200 GPU measurement. Failed cases have no
invented warm timing or output size. Finalist resize/copy steps ranged
0.003–0.300 seconds; final PNG encoding plus validation ranged 0.121–1.499 seconds.
Finalist target files ranged approximately 0.87–21.14 MB. Detailed CSV/JSON records
retain exact per-target dimensions, bytes, resize/encoding times and native
measurements. Original's source-grid PSNR is not comparable to target-grid PSNR.

Illustrative RGB PSNR against known synthetic truth, dB:

| Candidate | Dense 600 | Dense 1200 | JPEG probe 600 | JPEG probe 1200 |
| --- | ---: | ---: | ---: | ---: |
| Lanczos | 21.96 | 20.81 | 21.58 | 20.51 |
| SPAN | 22.30 | 21.08 | 21.97 | 20.83 |
| Compact | 22.72 | 21.40 | 22.21 | 21.02 |
| SwinIR | 22.52 | 21.26 | 22.21 | 21.02 |
| RealESRNet | 23.17 | 21.76 | 22.62 | 21.35 |
| RealESRGAN | 21.00 | 20.07 | 20.82 | 19.92 |
| HAT | 24.22 | 22.50 | 22.58 | 21.32 |

These are whole-image synthetic metrics dominated partly by flat/gradient
regions, not character-accuracy scores. HAT's high score coexists with visibly
distorted small glyphs. Metrics do not establish actual-card fidelity.

**Decision:** Spandrel/PyTorch is technically viable for an optional external
local provider on this CUDA host, but selecting a faithful neural default is
deferred. Keep Original and conventional resizing preferable unless users
explicitly choose enhancement after reviewing the face.

- Compact and SPAN are promising lightweight whole-face comparisons. Compact
  used much less peak memory here. Neither is qualified for exact typography.
- RealESRNet is the strongest restoration candidate from this shortlist for
  further human review: it improved these degraded-probe metrics and removed
  compression roughness, while also smoothing genuine fine detail.
- RealESRGAN remains a deliberately aggressive option for comparison, never the
  faithful default on this evidence.
- SwinIR was set aside after the screen: it worked, but had much higher latency
  and no clear inspected text advantage over the lighter candidates. This is a
  bounded selection judgment, not a claim that SwinIR is generally unsuitable.
- HAT was eliminated from full-corpus evaluation because of the real-face OOM
  and synthetic small-text distortion. No tiling framework or precision change
  was introduced to rescue it. The optional SwinIR 4× extension was unnecessary
  and was not downloaded.

## Visual review and limitations

Agent inspection covered whole-card composition and matched crops at both
targets, including a painterly front, an unusual saga back, a small-text
planeswalker face, synthetic degraded text/gradients, alpha edges and oversized
output. Specific observations:

- Full-face composition stayed in the same rectangle. Conventional stretching
  for source aspect differences was shared across candidates.
- On dense synthetic text, HAT produced hollow/outlined-looking strokes;
  RealESRGAN made markedly heavier and reshaped small glyphs. Tiny punctuation
  and similar forms such as 0/O, 1/I/l and rn/m remain unreliable after loss of
  source detail. Apparent sharpness did not recover a verified transcription.
- On real title/rules/collector crops, Compact and SPAN smoothed raster steps but
  altered some stroke terminals and small counters. RealESRNet looked cleaner
  while rounding/smoothing details. No exact glyph-preservation claim follows.
- Matched mana/set-symbol details showed smoother outlines but altered small negative spaces in the mana tree and reshaped fine set-icon strokes. Recognizability is not exact symbol preservation.
- The unusual back's thin red frame and collector text remained recognizable,
  but neural smoothing changed edge weight and the texture behind the text.
  Sharper frame edges alone do not establish better whole-card fidelity.
- In a painterly gold/red art crop, stronger models smoothed fine source grain
  into broader surfaces; RealESRGAN increased texture/edge contrast. Such detail
  is model interpretation, not known recovered art information.
- Synthetic flat borders, circle outlines and gradients expose ringing, softened
  thin lines and faint edge/color artifacts. Color observations are visual;
  no calibrated colorimetric or print assessment was performed.
- The explicit alpha path retained rounded boundaries and a partial-alpha strip.
  Transparent pixels were not silently discarded or replaced by a white matte.
  Hidden-RGB edge behavior and arbitrary real transparency still need human
  qualification.

Crop positions are fixed normalized coordinates across candidates. Labels name
intended review regions; unusual layouts can place rules text in an intended
artwork crop. No region detection is involved. Full files remain available for
mana/set-symbol and other face-specific inspection.

Human review must verify punctuation, mana/set symbols, collector numbers,
type-line details, borders and artwork together at the intended viewing/print
scale. No OCR accuracy, human preference study, calibrated color fidelity, or
physical print quality has been established.

## Focused validation and evidence integrity

The final standalone unittest suite passes **21 tests**. It covers all four exact
target rectangles, native/final labeling, per-target sufficient-source skips,
material recipe changes, source hashing, decode/hash/dimension reuse validation,
failure accounting, explicit alpha rejection/preservation, Original dimensions,
identity resize reporting, and deterministic/loadable sRGB profile metadata.

The result audit validates 69 native PNGs and target metadata for all 216 primary
records. Successful target pairs share one native recipe. All ten copied real
sources still match their original cache hashes. Fourteen regenerated synthetic
source/truth files are byte-identical across two final-code generations and
pixel-identical to the historical measured corpus.

After the full comparison, a metadata-only cleanup corrected the ICC intent
description, made equal-size copies explicit, and normalized ICC creation
metadata for deterministic future file generation. The image conversion and
inference calls were unchanged. The exact executed full-comparison source is
archived privately. All six candidates plus both controls then passed a final-code
16-output smoke: decoded pixels match the earlier screen exactly. Repeating that
unchanged recipe reused all 16 validated records without inference. The source
hash deliberately prevents automatic reuse across a harness edit, including
this conservative invalidation; the historical source/results remain preserved.

Whole-card thumbnails and 1:1 target-pixel crops are labeled by candidate and
target. Original is nearest-expanded only for crop display; its stored source
pixels remain unchanged. Detailed results, real sample manifest, full target
PNGs and review HTML remain outside Git. No PDF proof, output regeneration through
the application, printer/cutter job or physical print-quality measurement occurred.

## Licenses and later integration

The model manifest separates detected/expected architecture, scale, code-license
notes, checkpoint release terms and unresolved redistribution questions.
Nomos release pages state CC BY 4.0 and identify Philip Hofmann:
[SPAN](https://github.com/Phhofm/models/releases/tag/2xNomosUni_span_multijpg),
[Compact](https://github.com/Phhofm/models/releases/tag/2xNomosUni_compact_multijpg_ldl),
[HAT](https://github.com/Phhofm/models/releases/tag/4xNomos8kHAT-L_otf).
Retain author, model name, source/release link, license link and modification
information if later redistributing. Pretrained-model lineage merits separate
review. This milestone distributes no weights.

[SwinIR code](https://github.com/JingyunLiang/SwinIR/blob/main/LICENSE) and
[HAT code](https://github.com/XPixelGroup/HAT/blob/main/LICENSE) are Apache-2.0;
[Real-ESRGAN code](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE)
is BSD-3-Clause. These software licenses are not assumed to independently settle
all checkpoint redistribution rights. Spandrel is MIT; upstream architecture
license obligations also need review before shipping a provider. The acquired
release JSON and license evidence are retained privately.

Later integration must preserve original bytes, separate raster sample count from
the canonical physical rectangle, and keep enhancement optional and external.
See [image-enhancement.md](image-enhancement.md). Production cache, UI, jobs,
provider integration and geometry changes are explicitly absent from M22.

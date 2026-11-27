# Experimental neural preview and prepared output (M37)

Image Preparation Preview now offers **Neural 2× (experimental)** as an explicit,
session-only choice. Startup remains hidden and Off, with Original and Lanczos
unchanged. The initial card target is 600 PPI; 900 and 1200 are also available.
Canonical 300-DPI physical geometry and ordinary print/export/cut paths do not
change. Compact is an experimental comparison, not a faithful default.

## Local setup

Open Application → Image Preparation Preview → Set up local neural worker.
Select all four existing absolute locations:

1. The Python executable in the separately qualified worker environment.
2. The accepted bundle's worker.py.
3. The pinned NomosUni Compact multijpg LDL safetensors checkpoint.
4. An existing user-private working directory for this worker's temporary files.

The accepted bundle is the separate optional_providers/spandrel tree distributed
with the source repository; it is not part of the installed application package.
Keep worker.py beside protocol.py, backend.py and reference-model.json. Preserve
those accepted files together. The setup does not guess a checkout, search for
interpreters, download weights, install packages or change filesystem permissions.
The working directory must already exist and be private to the user. See the
[worker client contract](neural-worker-client.md) for its trusted-local-process
and direct-child cleanup boundaries.

The model is fixed to nomos_compact_2x, the accepted REFERENCE_SHA256, cuda:0 and
M34's existing timeout defaults. The qualified configuration is Windows / RTX 4090 /
CUDA 12.8 / FP32; a matching separate environment and accepted checkpoint must be
provided by the user. Setup saves metadata in memory only, retaining choices
across Off/hide and later explicit requests in that window. It does not persist
settings or redistribute model bytes. Cancel retains the last accepted choices.
Path existence, bundle/model hashing, runtime checks and inference occur in the
background task, not in the setup dialog.

## Explicit preparation and outcomes

Select Neural and a card target, then inspect the page and outcome details. Without
complete setup, this choice immediately disables the session, clears any Ready
publication, disables both prepared exports and names the four missing locations.
Original/Lanczos remain usable. A valid setup with an unavailable provider may
complete Ready using exact originals with explicit fallback reasons. Ready means
the results have been applied, not that every image received neural inference.

Counts distinguish source-sufficient originals, neural cache hits, generated
cached/uncached images, original fallback, standard rendering and skipped cards.
Alias occurrences count once each without another inference or retained-raster
charge. Terminal failure details are bounded and displayed. Configured worker
locations are replaced by descriptive placeholders in published/output diagnostics;
no worker configuration object or paths are added to captured output metadata.
Private task logs retain operational diagnostics.

The model performs one whole-image native 2× pass, then conventional resizing
when necessary to reach the card target. A 1200-PPI target is not native 4× detail.
Already-sufficient sources remain exact originals. Text, punctuation, symbols and
artwork can change. Compare against Original/Lanczos and inspect the saved result;
sharper edges do not establish typography preservation or recovered detail.

The accepted M34–M36 lifetime, cancellation, staleness, cleanup and cache contracts
remain in force. A same-PPI configuration change creates a fresh generation.
Refresh explicitly rereads sources. Hide/Off restores standard rendering; loading
locks suspend work and resume only the latest valid selection. Incomplete cleanup
with live resources blocks that owner from neural relaunch. A new explicit request
after verified cleanup is allowed; no automatic worker replacement or retry exists.

## Publication-bound PDF and PNG

Frozen SessionCompletion metadata now includes the submitted mode and card-target
PPI. Only a current Ready completion can supply either prepared export. MainWindow
uses that completion rather than live dock values. Public capture arguments remain
compatible but must match the completed generation exactly, before rendering.
Review dialogs label neural outcomes and the native-2×/conventional-resize policy.

Capture retains the existing page, geometry, binding, outcome and final encoding
freshness checks. Saving a Ready page does no new inference. Captured bytes remain
immutable after later document/source changes. The existing atomic save, retry and
once-only captured-face usage accounting remain unchanged. Worker configuration
is not serialized in output metadata.

Card target PPI and PNG canvas PPI are independent. For example, a 1200-PPI regular
card is 2980×4160 pixels (oversized 4160×5960), while a 600-PPI A4 landscape page is
7016×4961 pixels. PDF lossless image embedding retains the prepared card rasters;
PNG necessarily samples them onto its chosen whole-page grid. The MainWindow
512 MiB retained prepared-raster allowance and independent 512 MiB PNG canvas
limit remain in force; neither is a total process/GPU memory cap.

## Qualification boundary

Focused tests exercise actual dock/menu/setup/save entry points with controlled
providers and inert local test children. Separate fresh M37 qualification uses
the existing CUDA worker through MainWindow, saves both formats and independently
checks physical page/trim placement, embedded dimensions, PNG density and existing
registration/bleed behavior. Earlier M35/M36 evidence remains separately attributed.
Source and compiled UI forms are checked, and baseline startup/import guards
reject neural imports unless explicitly needed.

Private corpus comparisons show cleaner text/icon edges in several regions and
substantial smoothing/reinterpretation of painterly and low-resolution texture.
No pristine real-image masters exist for that comparison. Agent visual observations
are not human preference, exact typography fidelity, or physical print/cut evidence.
Physical qualification and human preference remain pending. Known sequence-sensitive
native Qt test-process crashes are documented in the private handoff; final suites
are run separately rather than claiming a successful combined process.

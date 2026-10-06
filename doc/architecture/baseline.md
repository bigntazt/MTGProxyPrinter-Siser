# M01 baseline

Recorded 2026-10-05 (America/Los_Angeles). Scope: fork, development environment,
storage isolation, and baseline only. No cutter features or packaging changes.

## Repository

- Local root: `D:\Code\MTGProxyPrinter-Siser`; initially empty, cloned into `.`.
  No existing files or user changes were overwritten. No on-disk `AGENTS.md` was
  found in the root or relevant parents; supplied chat instructions were followed.
- Authenticated/verified owner: `bigntazt`.
- Fork / `origin`: <https://github.com/bigntazt/MTGProxyPrinter-Siser>
  (`https://github.com/bigntazt/MTGProxyPrinter-Siser.git`). GitHub API verified
  `fork=true`, with parent and source both `luziferius/MTGProxyPrinter`.
- `upstream`: `https://github.com/luziferius/MTGProxyPrinter.git`.
- Canonical Fossil source:
  <https://chiselapp.com/user/luziferius/repository/MTGProxyPrinter>.
- Actual default branch: `trunk`. Starting upstream Git revision:
  `00202f988bef071c7070967e86c1490cb19114ba`.
- Commit metadata `FossilOrigin-Name`:
  `2453bafaad37bd2597dee0f4ff6897ce700c8280f6b587e9729d4b3546ed8321`.
- Application version: `0.36.0`. No drift from the architecture-review revision.
  Fossil metadata, copyright, license, source URLs, package name, and version remain intact.
- Local `trunk` tracks `upstream/trunk` and has no customization commits.
  `siser-dev` is the integration branch at the starting revision. M01 commits are
  on `milestone/m01-bootstrap`; M01 is not merged into `siser-dev`.
- Repository-local `remote.pushDefault=origin`, `pull.ff=only`; GitHub CLI default
  repository is the fork. Fetch upstream branches/tags; update `trunk` only by fast-forward.

## Environment and reproducible commands

Windows, installed CPython 3.13.14 (64 bit), PySide6/Qt 6.11.2,
Pint 0.24.4, platformdirs 4.12.3, pytest 9.1.1, pytest-qt 4.5.0,
pytest-timeout 2.4.0, tox 4.64.9, virtualenv 21.14.5.
Main-environment pip: 26.2.1. Git: 2.28.0.windows.1; GitHub CLI: 2.92.0.
No system Python installation or execution-policy changes were needed.

Run from the project root **after applying the isolation commit**:

```powershell
py -3.13 -m venv venv-tmp
.\venv-tmp\Scripts\python.exe -m pip install "tox>=4.41"
.\venv-tmp\Scripts\python.exe -m tox run -e generate_development_environment
```

The first tox run installed Pint 0.26.1 and failed during translation generation:
`pint.facets.plain.QuantityT` is absent. The checkout directly uses that type in
`units_and_sizes.py`. Resolve this environment mismatch with the project's declared
minimum Pint version, then rerun the existing generation process:

```powershell
.\venv\Scripts\python.exe -m pip install "Pint==0.24.4"
.\venv-tmp\Scripts\python.exe -m tox run -e generate_development_environment
$env:PATH = "$PWD\venv\Scripts;$env:PATH"
.\venv\Scripts\python.exe -m pytest tests
.\venv\Scripts\python.exe mtg-proxy-printer-runner.py --test-exit-on-launch
# Normal development launch:
.\venv\Scripts\python.exe mtg-proxy-printer-runner.py
```

For an existing suitable `venv`, reuse it. Adding its `Scripts` directory to PATH
is required even when invoking Python directly: Qt's `loadUiType` needs its UI
compiler. Without this, collection reported 17 UI compilation errors (exit 2).
No application repair was made for either setup problem.

The tox development environment generates translations and UI type stubs.
Development loads UI/resources from disk; `compile_resources.py` and importable UI
generation are for deliverables and were not needed. Generated files were not edited.
Tox emitted a nonfatal virtualenv cache self-migration warning, then completed
successfully after the Pint adjustment. The bootstrap environment was retained.
The desktop sandbox required approved execution for Windows Store Python and Git
metadata writes; this was an execution restriction, not an application failure.

## Isolation change and evidence

Commit `a90cb025`: `meta_data.PROGRAMNAME` changes from `MTGProxyPrinter` to
`MTGProxyPrinter-Siser`, before settings imports. This also identifies the fork's
logger, user agent, selected UI text, and PDF creator metadata. The main window
title remains unchanged. Document-format IDs,
MIME IDs, import package `mtg_proxy_printer`, and serialization remain unchanged.
Existing filenames within isolated storage remain unchanged.

Audit traced `app_dirs`, `settings`, `logger`, `application`, both image-storage
modules, `carddb`, `carddb_migrations`, `__main__`, and the runner:

- `PlatformDirs(PROGRAMNAME)` is the sole application-storage directory provider.
- Settings are read at module import; logger import creates a crash log. Isolation
  was applied before translation generation, tests, or application imports.
- Card database migration uses **both** old and new paths from the same fork
  directory provider. It cannot discover the upstream legacy database.
- Linux log migration operates only inside that same namespace and does not run
  on Windows. No migration suppression is necessary.
- No `QSettings` consumers or independent hardcoded upstream storage paths were found.
  `QStandardPaths` is used for user-facing file-picker/export defaults, not internal storage.
- Test card databases are memory/temporary databases; image fixtures use temporary
  paths. Existing test helpers reject settings writes. Import-time logs use fork storage.
- Smoke mode uses temporary databases, but still imports settings/logging first;
  its flag alone would not isolate those paths.

With the installed platformdirs version, let
`F = %LOCALAPPDATA%\MTGProxyPrinter-Siser\MTGProxyPrinter-Siser`:

| Storage | Resolved location |
| --- | --- |
| Configuration | `F\MTGProxyPrinter.ini` |
| Card data | `F\CardDatabase.sqlite3` |
| Image cache | `F\Cache\CardImages` |
| Legacy card migration source | `F\Cache\CardDataCache.sqlite3` |
| Logs | `F\Logs\MTGProxyPrinter-Siser.log` |
| Crash log | `F\Logs\MTGProxyPrinter-Siser-crashes.log` |

Runtime imports printed these exact paths and asserted the fork directories differ
from, and are outside, upstream storage. The existing upstream tree is
`%LOCALAPPDATA%\MTGProxyPrinter\MTGProxyPrinter`. Its file metadata was inventoried
for comparison around validation: all 4,148 files retained their names, sizes, and
nanosecond modification timestamps. This is metadata evidence, not a byte-for-byte
content audit. No card-image contents or personal settings were read.

## Validation

The tested state is the recorded upstream revision **plus the isolation change**.
It is not an untouched upstream baseline.

- `python -m pytest tests`: collected 4,098 cases after PATH correction; stalled
  in document loading and was interrupted (exit 1, no final pytest summary).
  A diagnostic `python -m pytest tests -vv --timeout=30` with
  `QT_QPA_PLATFORM=offscreen` recorded 1,332 passed and 3 skipped, then exited 1
  on timeout at
  `tests/model/test_document_loader.py::test_document_with_card_loads_correctly[False-False]`.
  The timeout stack is in the image downloader's HTTP retry/sleep path after a
  URL error. This is a network-dependent baseline exception; no unrelated
  downloader repair or catalog download was attempted.
- The unrun groups were checked separately, with the same offscreen/30-second
  settings: `tests/model/test_image_db.py`, `tests/model/test_page_layout_settings.py`,
  `tests/page_scene`, `tests/ui`, and every root `tests/test_*.py` file. This run
  finished with **2,002 passed, 419 failed, 22 skipped**, exit 1, in 72.98 seconds.
  The failed cases were 21 page-layout, 384 page-scene, 13 page-configuration,
  and 1 save-migration cases. The system Qt locale is `en_US`: defaults select
  Letter, while those tests require A4 geometry. All **419 failed cases passed**
  when rerun with A4 defaults set in memory, exit 0, in 10.65 seconds. No source,
  user locale, or saved settings were changed for this diagnostic.
- Across non-overlapping groups: **3,334 passed, 419 failed, 25 skipped** with
  the machine's defaults. The 419 failures are resolved for diagnostic purposes
  by A4 normalization, giving 3,753 distinct passing cases across those two
  configurations. **320 document-loader cases remain uncompleted**, including
  the timed-out case. These numbers do not represent a single successful full
  suite run. A focused diagnostic of `tests/model/test_document.py` separately
  passed all 41 cases; these are already included in the counts above.
- The historical A4 diagnostic selected 419 failed node IDs from the preceding
  diagnostic log. It can be reproduced without that ignored local argument file
  by selecting the four affected repository test files (this broader selection
  also includes cases that originally passed):

  ```powershell
  $env:PATH = "$PWD\venv\Scripts;$env:PATH"
  $env:QT_QPA_PLATFORM = 'offscreen'
  @'
  import mtg_proxy_printer.settings as settings
  settings.DEFAULT_SETTINGS['documents']['paper-size'] = 'A4'
  settings.settings.read_dict(settings.DEFAULT_SETTINGS)
  import pytest
  raise SystemExit(pytest.main([
      'tests/model/test_page_layout_settings.py',
      'tests/page_scene/test_page_scene.py',
      'tests/ui/test_page_config_widget.py',
      'tests/test_save_file_migrations.py',
      '--timeout=30',
  ]))
  '@ | .\venv\Scripts\python.exe -
  ```

  To reconstruct the exact failed-case selection from a newly captured verbose
  pytest log, use the lines beginning `FAILED tests/`, remove the leading
  `FAILED ` and everything from the first ` - ` onwards, and write one node ID
  per line. Pass that file as `@path/to/failed-nodeids.txt` to `pytest.main()`.
  Historical invocation (requires that generated argument file):

  ```python
  import mtg_proxy_printer.settings as settings
  settings.DEFAULT_SETTINGS['documents']['paper-size'] = 'A4'
  settings.settings.read_dict(settings.DEFAULT_SETTINGS)
  import pytest
  # One failed node ID per line in this local diagnostic argument file:
  raise SystemExit(pytest.main(['@.m01-output/failed-nodeids.txt', '--timeout=30']))
  ```

  The original baseline failures remain recorded; this is an environment-adjusted
  diagnostic. `python -m pip check` found no broken requirements (exit 0).
- Existing smoke command: exit 1, `AttributeError: 'NoneType' object has no
  attribute 'card_data_updated'` in `ui/add_card.py:69`. The unchanged temporary
  database branch in `Application._open_databases()` does not set
  `CardDatabase.main_instance`, unlike the normal branch. A comparison restored
  the original runtime identity while redirecting PlatformDirs to fork storage
  before application imports; it reproduced the same failure. This is a confirmed
  pre-existing smoke-path failure in this environment, not an isolation regression.
  No upstream issue was filed and no out-of-scope repair was made.
- Normal `Application(Namespace())` construction: exit 0 using fork databases.
  Main window and standalone page configuration were rendered with Qt offscreen;
  screenshots were inspected. Controls/layout appeared, but text rendered as
  missing-font boxes. This limits appearance validation; it does not establish
  successful native desktop appearance or interactive behavior.
- Live widget inspection confirmed registration choices/data: Disabled/`None`,
  Bullseye/`Bullseye`, Silhouette cutter (Cameo-compatible)/`Cut marker`.
- Native interactive control is unavailable in this session. Full normal startup
  tasks/dialogs and interactive main-window/page-configuration checks remain
  unverified. Existing automated PDF export checks passed: the two
  `test_pdf_export_does_not_raise_exception` cases and all six
  `test_export_pdf_creates_a_pdf_file` cases (PDF file/signature checks).
  No manual PDF/PNG export or physical validation was performed.
- Logs and render snapshots remain locally in ignored `.m01-output`; they are
  diagnostic artifacts, not source or deliverables.

## Git hygiene and handoff

The new `.gitignore` covers actual environment/tool output, bytecode/test caches,
build products, generated UI/translations/resources, and local validation/application
output. It does not blanket-ignore images, PDFs, or database fixtures. No tracked
files were removed. A local `.memdb` appeared during tooling initialization and was
preserved and ignored. No Memtrace connector tools were available in this session.

Status: **complete with documented baseline exceptions**. The environment and
fork namespace are usable; the full suite and native GUI appearance are not
claimed to pass. Architect review should account for the pre-existing smoke-path
failure, A4 test assumptions, network-blocked document-loader group, and manual
GUI/export gaps. There is no authentication/network blocker to repository pushes.

Runtime isolation is a separate commit (`a90cb025`). The second commit contains
only this baseline and Git hygiene. `siser-dev` was pushed at the starting upstream
revision; `milestone/m01-bootstrap` is the handoff branch. Neither `trunk` nor
`siser-dev` contains M01 customization commits. The source working tree is clean
after the two commits, with ignored environments, tooling data, translations/UI
stubs, and diagnostic output retained locally. No PR, release, upstream message,
M01 integration merge, or M02 work was performed.

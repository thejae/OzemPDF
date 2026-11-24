# Changelog

All notable changes to OzemPDF are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.3.2 — 2026-11-24

### Changed

- **Electron pinned to 38.0.0.** Electron 45 aborts in Chromium's sandbox on this Windows 11 build
  before the main process runs; Electron 38 launches directly, so `OzemPDF.exe` no longer needs the
  compatibility launcher on ordinary machines. The launcher stays for hosts that still block the
  sandbox.
- **Input PDFs are opened read-only.** The pipeline never writes back to the source (every result is
  a temp file promoted with an atomic replace), so requesting write access was unnecessary and
  failed for OneDrive/cloud placeholders, locked files and read-only media. Permission failures now
  say which path was denied and suggest closing the app or downloading the cloud file first.
- **Windows is now genuinely portable: one ZIP, no installer.** The NSIS installer and the
  NSIS-based portable self-extractor are gone (`npm run dist` builds only
  `OzemPDF-<version>-win.zip`). Nothing is extracted to the system temp directory and no uninstall
  entry, registry key or Program Files copy exists.
- **All app-owned files live in `data/` beside the executable**: settings, staging, Chromium caches,
  crash dumps, logs and the process temp directory. A packaged app sets Electron's `userData`,
  `sessionData` and `crashDumps` there and passes `OZEMPDF_DATA_DIR` to the engine; the frozen
  engine also derives `data/` from its own location. Moving or deleting the folder removes every
  trace. A pip-installed CLI keeps the platform-standard config location unless `OZEMPDF_DATA_DIR`
  is set.
- The browser launcher sets `OZEMPDF_DATA_DIR`, `TEMP` and `TMP` into `data/` as well, so the
  no-Electron fallback is equally self-contained.

## 0.3.1 — 2026-11-24

### Fixed

- **Packaged Windows build starts on hosts that block Chromium's sandbox.** On a managed or
  containerised host the sandbox fails before the Electron main process runs, so the app exited
  with no window and no log. The build now ships `Start OzemPDF (compatibility).cmd`, which starts
  the same application with `--no-sandbox`; `OzemPDF.exe` remains the normal entry point elsewhere.
- **Windows distribution includes a ZIP** (`npm run dist` builds ZIP + portable). Hosts where NSIS
  cannot write its temporary extraction directory can unzip and run the app; `npm run dist:installer`
  still builds the NSIS installer on hosts that can execute its uninstaller pass. The ZIP also
  contains `Start OzemPDF in browser.cmd`, which runs the bundled engine and opens the HTML
  interface in the default browser — no Electron, Python or installer required.
- **Desktop startup is diagnosable.** Electron, engine and window failures are appended to
  `%APPDATA%\OzemPDF\main.log` instead of disappearing with the GUI console.

## 0.3.0 — 2026-11-24

The PySide6 desktop application is replaced by an HTML/CSS/JS interface served by the Python
engine and wrapped in Electron. The engine, CLI and API were rebuilt against the technical
specification with every recorded discrepancy resolved.

### Added

- **Protection detection and automatic removal of permission-only restrictions.** Files that open
  with the empty user password (printing/copying/editing restrictions) are decrypted and written as
  plain PDFs with no prompt, even when a password was supplied for another file in the same batch.
  The result and JSON report now carry `protection_kind` (`none` / `permissions` / `password` /
  `unsupported`), `protection_removed` and `required_password`; batches report
  `protection_removed_count`; the queue shows a lock-open badge and the results band notes how many
  documents were unlocked.
- Open-password documents now fail with a message that says the password is genuinely required, and
  certificate-encrypted documents are reported as requiring their private key instead of a generic
  error.
- **Electron shell** (`app/`) for Windows, macOS and Linux: spawns the Python engine on a free
  loopback port, waits for its ready line, and hosts the same web interface the browser fallback
  uses. Native file/folder/certificate pickers, real paths for drag-and-drop, and a close-mid-run
  guard (`US-053`).
- **Local HTTP API** (`ozempdf/server.py`, stdlib only): session-cookie authentication, CSRF origin
  check, queue management, run control, thumbnails, page rendering, filesystem browser, upload
  staging, output opening, and a JSON report download.
- **Re-signing after slimming** (`engine/signing.py`) with a PKCS#12 certificate, optional RFC 3161
  timestamping, and passwords that never persist (`US-047` follow-up).
- **Password prompt in the interface** (`US-051`): a failed run identifies locked files and offers
  to unlock and retry them once per run.
- **Drop feedback** (`US-004`): skipped drops report what was ignored and why.
- **Already-optimal messaging** (`US-052`) in the row, the results band, the report and the
  inspector.
- **Explicit consent** before removing signature locks, and before enabling re-signing.
- **Logging** (`--verbose`, `OZEMPDF_LOG=1`) with per-file timings (D-10).
- **Real PNG Predictor 15** with all five filters — None, Sub, Up, Average and Paeth (D-02).
- **JSON guardrail flags** (`kept_original_guardrail`, `already_optimal`) and a separate
  `cmaps_optimized` counter (D-12, D-17).
- **Output collision handling**: colliding names get `-2`, `-3` instead of silently overwriting
  (D-18).
- `tests/` covering pixel identity, DPI math, palettes, predictor filters, encryption, signing,
  CLI exit codes, the HTTP API, settings, localization key coverage and orphan keys.
- `.github/workflows/ci.yml`, `LICENSE`, `AGENTS.md`, `PRODUCT.md`, `DESIGN.md`.

### Changed

- **Frontend** is plain HTML, CSS and JavaScript with no build step; the design is the
  "drafting table" system documented in `DESIGN.md` (paper/hairline neutrals, one vermilion accent,
  tabular mono for all measurements). Typography is bundled IBM Plex Sans with JetBrains Mono for
  every measurement.
- **Preset defaults**: signature flattening is off in all presets and requires explicit consent
  (D-14).
- **DPI and JPEG ranges** are aligned across UI, API and CLI: 72–600 DPI, Q50–98 (D-03, D-04).
- **`--help` is localized** through the catalog, including the `-h` option itself (D-11).
- **Unlock tries the empty user password first**, then the caller's password, so a single password
  for a mixed batch no longer breaks permission-restricted files. No dictionary is ever tried
  (D-15).
- **The completion workflow is now part of the repository contract** (`AGENTS.md`): keep
  `CHANGELOG.md` current, run the tests and lint, produce a Windows build, and make one atomic
  conventional commit per completed task.
- **Settings** are driven by `PERSISTED_KEYS` and include the language, restored at launch
  (D-07, D-21).
- **Reduced motion** is honoured through `prefers-reduced-motion` instead of a dead Qt probe
  (D-13).
- Locale catalog pruned to zero orphan keys, with tests for both missing and unused keys (D-08).
- Packaging declares `locales/`, `web/` and fonts as package data; the PyInstaller engine build
  passes them via `--add-data` (D-05).

### Fixed

- An optimized image stream could be written with a PDF *string* instead of a name for
  `/ColorSpace`, which blanked every page that contained it. Names are now coerced at the single
  write site.
- Strict Lossless no longer quantises gradients: an indexed palette is used only when the image
  genuinely has ≤256 colours, so decoded pixels remain bit-identical.
- The palette colour space is built with proper name objects, so renderers and `pikepdf` accept it.
- Signing failures report the real cause (wrong certificate password, missing certificate) and
  leave the output untouched.
- Stop requests in a sequential batch now stop dispatching remaining files instead of raising.
- The inspector no longer opens before the interface is ready; its dialog is hidden until shown.
- `--serve` works as a bare word as well as a flag.

### Removed

- The PySide6 GUI, its themes, SVG icon set, QSS generator and Qt worker threads.
- The automatic password dictionary (`" "`, `"1234"`, `"password"`, `"admin"`): only a
  user-supplied password is tried (D-15).
- The stale generated `OzemPDF.spec` with hard-coded paths.
- Orphan locale keys and the unused `PERSISTED_KEYS` indirection.

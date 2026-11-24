# Changelog

All notable changes to OzemPDF are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

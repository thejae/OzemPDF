# Changelog

All notable changes to OzemPDF are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.3.13 — 2026-11-25

### Changed

- **The advanced pane's section titles have breathing room.** A fieldset `<legend>` is placed in the
  border area, so the card's top padding never reached it and `Raster images`,
  `Stream & deflate engine` and `Fonts, metadata & locks` sat directly on the rule above the drawer.
  The legend now carries the card's 14 px top inset (15 px from the rule, border included), while the
  vertical card rules still meet that top rule; the narrow one-column layout inherits it.

## 0.3.12 — 2026-11-25

### Changed

- **Inspector zoom is about three times faster.** Page rasters were encoded with PNG
  `optimize=True`, which cost ~4.7× the default compression level for identical bytes at preview
  sizes (441 ms vs 92 ms for an 800 % page of the reported 5.4 MB CV), and every wheel notch started
  a fresh rasterisation of both pages. Encoding now uses the default level, and a wheel gesture
  coalesces into one render of the level the user stops at; the zoom pills still react immediately
  while the sharp raster arrives. Measured on that CV: the 800 % pair went from 1 029 ms to 346 ms,
  and a three-notch 100 % → 800 % gesture from six renders and ~2.5 s to two renders and ~0.7 s.

## 0.3.11 — 2026-11-25

### Changed

- **The queue's measured columns are centred.** Pages, Original, Savings and Action now centre both
  their header and their content in Configure & queue and in Slimmed results (the two tabs share the
  table), so page counts, sizes, savings chips and row actions line up under their labels instead of
  hanging off the right edge. The narrow two-line record layout is unchanged.

## 0.3.10 — 2026-11-25

### Added

- **Floating previous/next arrows in the inspector.** Multi-page documents get a pair of round page
  buttons at the bottom right of the canvas, so paging does not require reaching back to the header
  while zoomed in. They disable on the first and last page, hide for single-page documents, keep
  working inside the canvas, and carry localized labels and titles.

### Fixed

- **Dragging inside the inspector no longer looks like a drop onto the queue.** A drag that starts in
  the modal (the page image, a corner badge, the page number) is cancelled at the source and every
  drop is ignored while the inspector is open, so the queue can no longer light up or accept a
  dropped preview. Element and link drags carrying an image url are no longer read as path lists
  (only real files or plain path text are), and queue thumbnails are no longer draggable.

## 0.3.9 — 2026-11-25

### Added

- **Reset all in Configure & queue.** Once at least one file has been slimmed, the queue panel
  offers `Reset all` beside `Clear all`: every saved result is cleared and every finished row
  returns to `Queued — ready to slim`, so a whole batch can be retried with different settings
  without clicking each row. It stays disabled while there is no result and during a run, a toast
  reports how many results were cleared, and the same work is available over
  `POST /api/queue/reset`. Covered by `test_reset_all_returns_every_result_to_the_queue`.

### Changed

- **Zoom is reachable anywhere in the inspector, and survives a rebuilt header.** The wheel handler
  moved from the page canvas to the dialog, so scrolling over the header, the corner badges or the
  footer also steps the zoom; wheel over the page-number field is ignored so it cannot hijack typing.
  The zoom pills are handled by delegation instead of one listener per button, a pointer released
  outside the window clears the drag state, and a replaced header can no longer leave the zoom
  buttons dead.

## 0.3.8 — 2026-11-25

### Added

- **A slimmed row can be reset and slimmed again.** The Action cell of every completed row now
  offers `Reset` beside `Inspect`, in both Configure & queue and Slimmed results (the two tabs share
  the queue table). Reset clears the row's saved result and returns it to `Queued — ready to slim`
  with the dock reading `Slim 1 PDF`, so the same file can be retried with different settings; the
  written output stays on disk and the next run replaces it atomically. Reset is refused while a
  batch is already running, with the running message rather than a silent no-op, and a reset closes
  the inspector if it is showing that row. Covered by
  `test_reset_returns_a_slimmed_row_to_the_queue`.

## 0.3.7 — 2026-11-25

### Fixed

- **The inspector no longer goes dark after a compression run.** PDFium (through pypdfium2) is not
  thread-safe, and the inspector asks for the original and the slimmed page on two connections at
  once while the thumbnail pool renders in parallel. Two overlapping renders corrupted PDFium's
  global state, after which *every* later page render returned nothing: `/api/page` answered 404 for
  both halves and the wipe canvas stayed black for the rest of the session (the packaged app's
  `main.log` recorded `_serve_page`'s `render_failed` for both). Every PDFium call now runs under
  one module lock, and `test_page_renders_are_serialized_for_pdfium` pins the invariant rather than
  the timing of a race.
- **Page previews now render damaged files that qpdf can still reconstruct.** PDFium demands an
  intact cross-reference table; a half-written or badly finalised PDF (the reported signed form was
  truncated mid-object) was refused, so both halves of the inspector stayed dark even though the
  document compressed normally. The renderer re-saves such a document through qpdf into memory and
  renders that copy — nothing on disk is touched
  (`test_truncated_pdf_renders_from_a_repaired_copy`).
- **The preview uses the document password.** `/api/page` rendered the original with the empty
  password, so a password-protected PDF previewed as a dark empty stage even after a successful run.
  The row now keeps the credential in memory (never persisted, never in the queue JSON) and both the
  page endpoint and the thumbnail pool pass it
  (`test_inspector_renders_protected_pages_after_a_password`).
- **A page that truly cannot be rendered says so.** The inspector shows a localized notice over the
  canvas instead of a silent dark void, so a rendering gap cannot be mistaken for a damaged file.

## 0.3.6 — 2026-11-25

### Fixed

- **Every 1-bit image was recompressed into garbage, which erased text lines.** Word draws some
  text runs as a tiny colour image covered by a 1-bit bitmap `/SMask`; the 1-bit branch read the
  stream's *encoded* bytes, ran them through zopfli and stored the result as the new
  `FlateDecode` payload. The stream then decoded to a deflate blob instead of a raster, so the mask
  rendered as a noise strip and the sentence under it vanished. The raster is now decoded
  (`read_bytes()`), checked against the expected `ceil(width/8) × height` length, and only
  rewritten when the candidate is smaller. Indexed or colour 1-bit images keep their colour space
  (forcing `/DeviceGray` reinterpreted them), and streams with `/Decode` are left untouched.
  Covered by `test_bilevel_mask_keeps_every_pixel`,
  `test_bilevel_mask_with_decode_is_not_reencoded` and `test_text_mask_overlay_still_renders`; the
  reported CV now renders its heading and every sentence, with extracted text identical to the
  source.

## 0.3.5 — 2026-11-25

### Fixed

- **A denied write into the output folder now retries, then names the folder.** Reported from a
  OneDrive folder, the failure read `Permission denied for …\.ozempdf_288mw5gc.tmp.pdf` and advised
  closing whichever application held that file — a random temporary name the user could neither find
  nor act on. Two changes: temp-file creation, the pikepdf save and the atomic promotion now retry
  briefly (50/150/400 ms), because OneDrive, antivirus scanners and the indexer take short-lived
  handles on files they are syncing or inspecting; and a denial that persists is now reported against
  the destination folder with the OS reason, suggesting a sync check or another output folder. A
  denial while reading the input keeps the original download-the-cloud-copy advice.
- **Running the app from inside the development checkout is now documented as unsupported.** The
  OneDrive write failure reported against 0.3.5 was the checkout's sandbox, not OneDrive: the same
  packaged engine, extracted outside the checkout, compressed that file into that folder
  (5 407 573 → 1 014 067 bytes), while a scheduled-task launch of the in-checkout copy could write
  only inside the checkout and `python.exe` — installed outside it — could write anywhere. Sandboxing
  follows the executable's location, so it presented as a OneDrive permission problem. `README.md`
  now says to extract the ZIP outside the checkout, and `docs/DISCREPANCIES.md` records the evidence
  as N-11.

## 0.3.4 — 2026-11-25

### Added

- **Failed rows can hand over the complete error message.** A failed file in the queue now carries a
  `Copy` action that puts the whole diagnostic on the clipboard, not the ellipsized beginning the
  status column can show. The message stays selectable in place, and the column keeps its width.

### Changed

- **Repository restructured for a clear launch path and uncrowded root.** The Electron shell moved
  from `app/` to `electron/` (with `launcher/` → `launchers/` and `build/` → `assets/`), authored
  Python moved under `python/` (`src/`, `tests/`, `scripts/`), project documentation moved under
  `docs/` (`CHANGELOG.md`, `DESIGN.md`, `PRODUCT.md`), the portable ZIP is written to `release/`, and
  PyInstaller scratch/output moved to `artifacts/build/` and `artifacts/dist/`. The root now lists
  only the source groups, generated outputs and conventional project files. Command paths and the
  build script follow the move; the shipped engine and ZIP contents are unchanged. Third-party
  manifests are indexed in `docs/DEPENDENCIES.md`.

### Fixed

- **Multi-file slimming failed in the shipped engine.** The frozen sidecar is built with PyInstaller,
  and it never called `multiprocessing.freeze_support()`: Windows starts pool workers by re-running
  the executable with a `--multiprocessing-fork` payload, argparse consumed that flag, and every run
  with two or more files failed with `unrecognized arguments: --multiprocessing-fork` while
  single-file runs kept working. The entry point now consumes the spawn payload before the parser,
  and a guard test keeps the call ahead of `run_cli()`. Unpacked source runs were never affected,
  which is why the suite stayed green.

## 0.3.3 — 2026-11-24

### Changed

- **New interface accent colour: deep blue instead of vermilion.** The brand mark, primary buttons,
  active step, focus rings, slider thumbs, the wipe divider and the breakdown bars are now ink blue
  (`#1b4a80` light, `#63b3ed` dark). The drafting-table neutrals are unchanged, and the semantic
  colours keep their meaning: green still means saved/completed, red still means danger or a failed
  file, amber still means a partial run. Every pairing was re-measured for WCAG AA — accent on
  surface 8.6:1, white on accent 9.0:1, accent on its tint 7.6:1 in the light theme; 7.8:1, 8.0:1
  and 6.3:1 in the dark theme, all above the 4.5:1 floor.
- The application icon follows the accent, so the taskbar, window and ZIP read as the same product.
- `AGENTS.md` now records where the GitHub GPG passphrase lives, so agents can read it when signing
  commits instead of asking the user each time.
- **Documentation synchronised with the shipped application.** The technical specification is
  rewritten as version 2.0 for the HTML/CSS/Electron architecture (new interface chapter, real
  preset/range/config tables, a defects-and-limitations chapter, current test inventory); the user
  stories now cover the four behaviours that shipped without a written story (US-055 … US-058) and
  record the 0.3.x release slices; the discrepancy ledger lists the defects found after the rewrite.

### Fixed

- **The header showed a stale version.** The version chip was hardcoded as `v0.3.0` in the markup and
  never updated, so a 0.3.3 build still claimed 0.3.0. It now renders the running version, and a test
  fails if the static fallback drifts from `_version.py`.
- **`CompressionConfig.from_preset()` ignored preset enum members.** Because `PresetMode` is a string
  enum, `str(PresetMode.LOSSLESS)` is `"PresetMode.LOSSLESS"` on Python 3.11+, so passing a member
  raised `ValueError` and fell back silently to balanced. Enum members are now used directly, with a
  test covering every member.

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

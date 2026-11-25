# Discrepancy ledger — 0.3.3 architecture

The technical specification recorded 21 defects (`D-01` … `D-21`) in the PySide6 build, plus five
user-facing gaps (`US-004`, `US-036`, `US-051`, `US-052`, `US-053`). This page records what happened
to each one in the HTML/CSS + Python architecture, then the defects that surfaced while building it,
and the behaviours added since. Nothing here is aspirational: a "resolved" item has a test or a code
path in this repository.

## The original 21

| ID | Was | Now |
| --- | --- | --- |
| **D-01** | Copy claimed MozJPEG; the encoder was Pillow/libjpeg-turbo | UI, README and changelog say "progressive JPEG (Pillow/libjpeg-turbo)". No MozJPEG claim exists anywhere. |
| **D-02** | Predictor documented five filters but implemented None/Sub/Up; `_paeth_predictor` was dead code | All five filters are implemented and selected per row by the libpng heuristic. Round-trip and reference tests in `python/tests/test_visually_lossless.py` (`test_predictor_roundtrip_all_filters`, `test_paeth_filter_matches_reference`). |
| **D-03** | GUI DPI slider stopped at 300 while config allowed 600; Strict Lossless was a slider display hack | One range (72–600) in UI, API, CLI and config. Strict Lossless is a real `strict_lossless` flag that disables downsampling and force-converts nothing. |
| **D-04** | GUI JPEG max 95, CLI 98 | Single range 50–98 everywhere, clamped in `CompressionConfig.clamped()`. |
| **D-05** | PyInstaller spec had hard-coded `D:/dev/...` paths and never shipped fonts | No checked-in spec. `python/scripts/build_cross_platform.py` runs PyInstaller `--onedir` and passes `locales/`, `web/` (fonts included) via `--add-data`; `pyproject.toml` declares the same package data; `resources.py` resolves them from `sys._MEIPASS` in a frozen build. `--onefile` was dropped because its temp extraction fails on locked-down hosts, and NSIS was dropped for the same reason (`npm run dist` builds the portable ZIP only). |
| **D-06** | Changelog stated 32 icons; there were 37 | The icon count claim is gone. The UI ships one authored SVG sprite; nothing counts glyphs in prose. |
| **D-07** | `PERSISTED_KEYS` was dead and advertised a key that was never written | Persistence is driven by `PERSISTED_KEYS` / `PERSISTED_CONFIG_KEYS`; tests assert only whitelisted keys persist and that the password, certificate path and TSA URL never do. |
| **D-08** | Seven unused locale keys | Catalog pruned to 282 strings in 19 groups. Two tests: every referenced key exists, and no orphan key exists (it has since caught two more orphans). |
| **D-09** | README said v0.1.0 while the package was 0.2.0 | README, `docs/PRODUCT.md`, changelog and package version all read 0.3.3. The same class of bug reappeared in the new UI — a hardcoded `v0.3.0` version chip — and is now impossible: the chip renders `state.version`, and `test_static_version_label_matches_package_version` fails the build if the static fallback drifts. |
| **D-10** | No logging, no `--verbose`, no timings | `logging_setup.py` (`--verbose`, `OZEMPDF_LOG=1`), per-file timings in DEBUG, normal runs silent, plus `data/main.log` for Electron/engine startup on Windows. |
| **D-11** | `argparse` help strings were English literals | Every option (including `-h` itself) is translated through the catalog; tests assert the localized text appears and no raw key leaks. |
| **D-12** | `kept_original_guardrail` was absent from JSON | Present in `FileCompressionResult.to_dict()` with `already_optimal`; asserted by tests, the CLI report and the results band. |
| **D-13** | Reduced-motion probe called a non-existent Qt API | The web UI uses `@media (prefers-reduced-motion: reduce)`; every transition and the drawer animation are disabled under it. |
| **D-14** | Signature flattening was on by default and silently invalidated signatures | `unlock_and_flatten_signatures` is **False in all four presets**. Enabling it requires a consent dialog that states the signature is destroyed, and re-signing is offered as the remedy. |
| **D-15** | Common-password dictionary was tried automatically | No dictionary, no guessing. The engine tries the empty user password (`permissions` case, not decryption), then only the password the caller supplied. `test_no_password_guessing` proves `"1234"` fails and `test_unsupported_encryption_is_reported_not_unlocked` proves certificate encryption is reported. |
| **D-16** | `/Thumb` images were counted as images even though the role is metadata | The analyzer classifies by role first, so a page `/Thumb` counts as metadata even when it is an image stream. |
| **D-17** | `/ToUnicode` savings were credited without a counter | Separate `cmaps_optimized` counter in `FileCompressionResult` and the JSON schema. |
| **D-18** | Colliding output paths silently overwrote one another | `_unique_outputs` appends `-2`, `-3` before the extension; a test covers two same-named inputs targeting one output folder. |
| **D-19** | No CI, no lint config, no license, no contributing notes | `.github/workflows/ci.yml` (Windows/macOS/Linux, Python 3.10/3.14, ruff, `node --check`), ruff config in `pyproject.toml`, MIT `LICENSE`, `AGENTS.md` with invariants and the completion checklist. |
| **D-20** | `pdf` parameter threaded through image helpers unused | Helpers take only the stream they mutate; the pipeline-facing entry points take what they use. |
| **D-21** | Language was written but never restored | `language` is in `PERSISTED_KEYS` and restored by `AppState` at launch, before the first page is served. Tests cover the round-trip and the `/api/ui` switch. |

## Defects found while building 0.3.x

| ID | Symptom | Fix |
| --- | --- | --- |
| **N-01** | Compressed pages rendered blank | `/ColorSpace` was written as a PDF *string* because a plain `str` was passed to pikepdf. Now `pikepdf.Name(colorspace)`, and `/Indexed` spaces are built as `pikepdf.Array([Name("/Indexed"), …])`. Invariant recorded in `AGENTS.md` and spec §4.6. |
| **N-02** | Strict Lossless shifted gradient colours | The palette path quantised unconditionally (mean pixel difference 28.6). `_exact_palette_candidate` now only quantises when the image has ≤ 256 unique colours, so gradients stay photographic. Covered by `test_lossless` pixel comparison. |
| **N-03** | `[Errno 13] Permission denied` on OneDrive/cloud placeholders and read-only media | Inputs were opened with `allow_overwriting_input=True` although the pipeline never writes to the source. Removed; `test_input_is_opened_read_only` asserts it stays removed, and a `PermissionError` now produces an actionable message naming the path (`test_permission_denied_is_actionable`). |
| **N-04** | Header showed `v0.3.0` on a 0.3.3 build | Version chip rendered from `state.version`; static fallback asserted by a test (see D-09). |
| **N-05** | `from_preset(PresetMode.LOSSLESS)` silently produced the Balanced preset | `PresetMode` is a `str` enum, so `str(member)` is `"PresetMode.LOSSLESS"` on Python 3.11+ and the `ValueError` fallback fired. Enum members are now used directly; `test_from_preset_accepts_an_enum_member` covers every member. |
| **N-06** | Packaged app exited with no window and no log on some hosts | Electron 45's Chromium sandbox aborts before the main process runs on this Windows 11 build. Pinned to Electron 38 (launches directly); the compatibility launcher remains for hosts that still block the sandbox, and startup failures are appended to `main.log`. |
| **N-07** | `npm run dist` failed at the NSIS step | electron-builder executes the generated installer to capture the uninstaller, which locked-down hosts block. NSIS and the self-extracting portable target were removed; the deliverable is the portable ZIP. |
| **N-08** | A packaged run wrote to `%APPDATA%` and the system temp directory | All app-owned files now live in `data/` beside the executable (Electron `userData`/`sessionData`/`crashDumps`/temp redirect plus `OZEMPDF_DATA_DIR` for the engine). Verified by extracting the ZIP outside the workspace and checking `%APPDATA%`. |
| **N-09** | Every packaged batch of two or more files failed with `unrecognized arguments: --multiprocessing-fork`, reported only as a truncated status cell | The frozen sidecar never called `multiprocessing.freeze_support()`, so the pool's Windows spawn payload reached argparse. Single-file runs use the sequential path, which is why the symptom looked file-specific. `engine_entry.py` now consumes the payload before the parser; `test_packaging` keeps the call ahead of `run_cli()`, and the frozen engine was re-verified on a two-file batch. Failed rows also gained a `Copy` action so the next diagnostic is not trapped behind an ellipsis. |
| **N-10** | `Permission denied for …\.ozempdf_288mw5gc.tmp.pdf` when writing into a OneDrive folder | `_temp_path` denied meant an unwritable destination, but the message named our random temp file and told the user to close the application holding it. Output writes (temp creation, the pikepdf save, the atomic promotion) now retry brief cloud/AV locks; a persistent denial reports the destination folder and the OS reason (`errors.output_denied`), while input denials keep the download-the-cloud-copy advice. Covered by `test_denied_output_names_the_folder_not_the_temp_file` and `test_transient_lock_on_the_output_is_retried`. |
| **N-11** | The same denial persisted after N-10, for one OneDrive folder only | The app was being run from `release/win-unpacked` inside the development checkout, which this host sandboxes to the checkout. The identical packaged engine, extracted outside it, wrote the file (`5 407 573 → 1 014 067` bytes) into that OneDrive folder; a scheduled-task launch of the in-checkout copy could write only inside the checkout, while `python.exe` (installed outside it) wrote anywhere. Sandboxing follows the executable's location, not the destination, so this read as a OneDrive permission problem. `README.md` now says to extract the ZIP outside the checkout and why; N-10's retries and folder-named message remain as genuine improvements for real cloud locks. |

## Protection removal (new in v0.3.0)

The v0.1 build decrypted implicitly but never said what it could and could not remove. The engine now
classifies protection in `engine/unlock.py` and acts only where a credential is not required:

| Input | Classification | Behaviour |
| --- | --- | --- |
| No encryption | `none` | Normal compression. |
| Encrypted, opens with the empty user password | `permissions` | Decrypted, restrictions removed, output written as a plain PDF. **No password needed**; this covers the common "secured PDF" that readers display but forbid printing/copying. |
| Encrypted under an open (user) password | `password` | Required. Supplied once (`--password` or the interface prompt) and applied to the remaining rows; the output is plain. |
| Certificate / public-key encryption or an unsupported scheme | `unsupported` | Needs the private key, which libqpdf cannot use here, so it is reported instead of guessed at. |

The empty user password is tried first, then the caller's password — that ordering is why one password
for a mixed batch no longer fails the permission-only files. The report exposes `protection_kind`,
`protection_removed` and `required_password`; `BatchSummary` counts unlocked documents and the results
band names the count. No dictionary, brute force or cracking exists (D-15).

## Previously proposed stories

| Story | Status | Where |
| --- | --- | --- |
| **US-004** — explain ignored drops | Shipped | `/api/queue/add` returns `skipped[]` with a localized reason; the UI shows one notice with the count and reasons. |
| **US-036** — keep my setup (language gap) | Shipped | Language joins theme/preset/knobs in settings and is restored (see D-21). |
| **US-051** — enter a password in the GUI | Shipped | A run that reports "password required" opens a prompt and retries the affected rows with the entered password, once per run; nothing persists. |
| **US-052** — explain "already optimal" | Shipped | Row status, results-band note, inspector note, JSON `already_optimal` and a CLI line. |
| **US-053** — warn before closing mid-run | Shipped | The Electron close guard reads the live run state, asks for confirmation, stops at a file boundary and waits for the in-flight file; the browser fallback uses `beforeunload`. |
| **US-055** — remove restrictions without a password | Shipped (v0.3.0) | `inspect_protection()` + empty-password-first open; tests for all four classifications. |
| **US-056** — re-sign after slimming | Shipped (v0.3.0) | `engine/signing.py` (pyhanko, PKCS#12, optional TSA, verified field, atomic promote). |
| **US-057** — run from any folder, leave nothing behind | Shipped (v0.3.2) | Portable `data/` resolution; ZIP-only distribution. |
| **US-058** — browser fallback | Shipped (v0.3.1) | `--serve` + upload staging + loopback token; same UI files. |

## Decisions taken

| Question | Decision |
| --- | --- |
| Default preset on cold start | **Visually Lossless** remains the default; the results breakdown and the wipe-slider inspector make quality measurable, which was the missing counterweight for vector-heavy users. |
| Signature flattening default | **Off**, behind a consent dialog (D-14), with re-signing as the remedy. |
| Overwrite recoverability | Atomic replace after confirmation, no automatic backup or trash. Documented rather than silently added. |
| "100 % vectors preserved" claim | Rests on the construction invariant plus tests that compare decoded content bytes and pixels; no per-run content-stream diff is shown in the product. |
| Guardrail visibility | First-class: row status, results-band note, inspector note, JSON flag and CLI line. |
| Password scope | Once per run, applied to the remaining rows, never persisted or printed. |
| Ghostscript for locked PDFs | Rejected. Measured: `-sDEVICE=pdfwrite` exits 0 with a **blank page** and `This file requires a password for access` on a genuinely password-protected file, rewrites content operators (breaking the vector invariant), and grew a 1171-byte file to 2532 bytes. |
| Browser vs desktop | One implementation. The browser serves the same three files and API; upload staging is the only documented difference. |

## Deferred (documented, not implemented)

- Per-stage progress for a single very large file (the engine reports per file).
- Streaming/tiled image encoding to bound memory on hundreds-of-megapixel rasters.
- PDF/A and PDF/X conformance checks.
- A pixel-difference (MSE/PSNR) readout in the inspector.
- Automatic backup of overwritten originals.
- Certificate-encrypted PDFs (private key required).
- Code signing of the Windows ZIP (SmartScreen still warns on first run).
- End-to-end packaged verification on macOS and Linux (CI only).

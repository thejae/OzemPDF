# Discrepancy ledger — 0.3.0 architecture

The technical specification recorded 21 defects (`D-01` … `D-21`) in the PySide6 build, plus five
user-facing gaps (`US-004`, `US-036`, `US-051`, `US-052`, `US-053`). This page records what
happened to each one in the HTML/CSS + Python architecture. Nothing here is aspirational: a
"resolved" item has a test or a code path in this repository.

| ID | Was | Now |
| --- | --- | --- |
| **D-01** | Copy claimed MozJPEG; the encoder was Pillow/libjpeg-turbo | UI and docs say "progressive JPEG (Pillow/libjpeg-turbo)". No MozJPEG claim exists. |
| **D-02** | Predictor documented five filters but implemented None/Sub/Up; `_paeth_predictor` was dead code | All five filters are implemented and selected per row by the libpng heuristic. Round-trip and reference tests in `tests/test_visually_lossless.py`. |
| **D-03** | GUI DPI slider stopped at 300 while config allowed 600; Strict Lossless was a slider display hack | One range (72–600) in UI, API and CLI. Strict Lossless is a real `strict_lossless` flag that disables downsampling. |
| **D-04** | GUI JPEG max 95, CLI 98 | Single range 50–98 everywhere. |
| **D-05** | PyInstaller spec had hard-coded `D:/dev/...` paths and never shipped fonts | Generated spec removed. `build_cross_platform.py` passes `locales/`, `web/` (including fonts) via `--add-data`; `pyproject.toml` declares the same package data; `resources.py` resolves both in a frozen build. |
| **D-06** | Changelog stated 32 icons; there were 37 | The icon count claim is gone. The web UI ships one authored SVG sprite; no stale number is maintained. |
| **D-07** | `PERSISTED_KEYS` was dead and advertised a key that was never written | `save_settings()` is driven by `PERSISTED_KEYS`; a test asserts only whitelisted keys persist and that secrets never do. |
| **D-08** | Seven unused locale keys | Catalog pruned. Two tests: every referenced key exists, and no orphan key exists. |
| **D-09** | README said v0.1.0 while the package was 0.2.0 | README, changelog and version all say 0.3.0. |
| **D-10** | No logging, no `--verbose`, no timings | `logging_setup.py`; `--verbose` and `OZEMPDF_LOG=1` enable DEBUG with per-file timings; normal runs stay silent. |
| **D-11** | `argparse` help strings were English literals | Every option (including `-h` itself) is translated through the catalog; a test asserts the localized text appears and no raw key leaks. |
| **D-12** | `kept_original_guardrail` was absent from JSON | Present in `FileCompressionResult.to_dict()` along with `already_optimal`; asserted by tests and the CLI report. |
| **D-13** | Reduced-motion probe called a non-existent Qt API | The web UI uses `prefers-reduced-motion: reduce`; every animation is disabled under it. |
| **D-14** | Signature flattening was on by default and silently invalidated signatures | Off in all presets. Enabling it (or re-signing) requires a confirmation dialog that states the consequence. |
| **D-15** | Common-password dictionary was tried automatically | Only the user-supplied password is tried; empty only when the user supplied none (owner-restricted documents). A test proves `"1234"` fails. |
| **D-16** | `/Thumb` images were counted as images even though the role is metadata | The analyzer classifies by role first: a page `/Thumb` counts as metadata even when it is an image stream. |
| **D-17** | `/ToUnicode` savings were credited without a counter | Separate `cmaps_optimized` counter (alias `fonts_subsetted` preserved) in the result and JSON schema. |
| **D-18** | Colliding output paths silently overwrote one another | Collisions get `-2`, `-3` before the extension; a test covers two same-named inputs in an output folder. |
| **D-19** | No CI, no lint config, no license, no contributing notes | `.github/workflows/ci.yml` (Windows/macOS/Linux, Python 3.10/3.14), `ruff` config in `pyproject.toml`, MIT `LICENSE`, `AGENTS.md`. |
| **D-20** | `pdf` parameter threaded through image helpers unused | Helpers take only the stream they mutate; the pipeline-facing entry points take what they use. |
| **D-21** | Language was written but never restored | Language is stored and restored by `AppState` at launch; test covers a French catalog round-trip. |

## Protection removal (new in v0.3.0)

The v0.1 architecture decrypted implicitly but never said what it could and could not remove. The
0.3.0 engine classifies protection and acts only where a credential is not required:

| Input | Classification | Behaviour |
| --- | --- | --- |
| No encryption | `none` | Normal compression. |
| Encrypted, opens with the empty user password | `permissions` | Decrypted, restrictions removed, output written as a plain PDF. **No password needed**; this covers the common "secured PDF" that readers display but forbid printing/copying. |
| Encrypted under an open (user) password | `password` | The password is required. Supplied once with `--password` or the interface prompt; the output is plain. |
| Certificate / public-key encryption or an unsupported scheme | `unsupported` | Requires the private key; libqpdf cannot open it, so it is reported instead of guessed at. |

The report exposes `protection_kind`, `protection_removed` and `required_password`, and
`BatchSummary` counts unlocked documents. No password dictionary, brute force or cracking is
implemented (D-15); removing permission restrictions is not decryption, because the content is
already readable with the empty user password every PDF reader uses.

## Proposed user stories

| Story | Status | Where |
| --- | --- | --- |
| **US-004** — explain ignored drops | Done | `/api/queue/add` returns `skipped[]` with a localized reason; the UI shows one notice with the count and reasons. |
| **US-036** — keep my setup (language gap) | Done | Language joins theme/preset/knobs in settings and is restored (see D-21). |
| **US-051** — enter a password in the GUI | Done | A run that fails with "password required" opens a prompt and retries the failed rows with the entered password, once per run; nothing persists. |
| **US-052** — explain "already optimal" | Done | Row status, results band note, inspector note, JSON `already_optimal` and a CLI summary line. |
| **US-053** — warn before closing mid-run | Done | Electron close guard reads the live run state, asks for confirmation, then stops at a file boundary and waits for the in-flight file before quitting; the browser fallback uses `beforeunload`. |

## Open questions from the specification

| Question | Decision in 0.3.0 |
| --- | --- |
| Default preset on cold start | **Visually Lossless** remains the default; the results screen and the inspector make quality measurable, which was the missing counterweight for vector-heavy users. |
| Signature flattening default | **Off**, with explicit consent. |
| Overwrite recoverability | Atomic replace after confirmation, but no automatic backup/trash. Documented rather than silently added. |
| "100% vectors preserved" claim | The claim now rests on the construction invariant plus tests that compare decoded page content and pixels; a per-run content-stream diff is not implemented. |
| Guardrail visibility | First-class: row status, banner note, inspector note, JSON flag and CLI line. |
| GUI password scope | Once per run, applied to all files; the password lives only in the running process. |
| Per-file progress | Still per file. The queue shows an indeterminate bar while a file is inside Zopfli; stage-level progress is future work. |

## Deferred (documented, not implemented)

- Per-stage progress for a single very large file.
- Streaming/tiled image encoding to bound memory on hundreds-of-megapixel rasters.
- PDF/A and PDF/X conformance checks.
- A pixel-difference (MSE/PSNR) readout in the inspector.
- Automatic backup of overwritten originals.

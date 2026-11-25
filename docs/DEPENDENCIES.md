# Third-party dependencies

This page groups every third-party component the project relies on. It is a human-readable
inventory only: **the package manifests are authoritative** — `pyproject.toml` for Python and
`electron/package.json` (with `electron/package-lock.json`) for Node. Add or remove a dependency
there first, then update this page.

## Python runtime

Declared in `pyproject.toml` under `[project].dependencies`.

| Package | Constraint | Used for |
| --- | --- | --- |
| `pikepdf` | `>=8.0.0` | PDF object model and qpdf bindings — the only layer that reads/writes PDF objects |
| `Pillow` | `>=10.0.0` | Raster decode/encode, palette quantisation, JPEG handling |
| `zopfli` | `>=0.2.2` | Stronger deflate recompression of content streams |
| `fonttools` | `>=4.40.0` | TrueType subsetting and CMap recompression |
| `pypdfium2` | `>=4.20.0` | Page and thumbnail rendering for the inspector |
| `pyhanko` | `>=0.25.0` | PKCS#12 re-signing and incremental signature updates |

## Python development

Declared in `pyproject.toml` under `[project.optional-dependencies].dev`.

| Package | Constraint | Used for |
| --- | --- | --- |
| `pytest` | `>=7.4` | Test suite (`python -m pytest -q`) |
| `pyinstaller` | `>=6.0` | Frozen engine build (`python python/scripts/build_cross_platform.py`) |
| `ruff` | `>=0.6` | Lint gate (`ruff check python/src python/tests`); installed with the `dev` extra |

## Node / Electron

Declared in `electron/package.json` under `devDependencies`; exact versions are locked in
`electron/package-lock.json`.

| Package | Constraint | Used for |
| --- | --- | --- |
| `electron` | `^38.0.0` | Desktop shell. Electron stays on the 38.x line: 45's Chromium sandbox aborts on the reference Windows 11 build before the main process runs |
| `electron-builder` | `^26.0.12` | Portable Windows ZIP (and macOS/Linux) packaging |

## Bundled assets

Third-party assets committed to the repository and served by the local web UI.

| Asset | Licence | Location |
| --- | --- | --- |
| IBM Plex Sans (latin, latin-ext) `woff2` | SIL Open Font License 1.1 | `python/src/ozempdf/web/fonts/IBMPlexSans-*.woff2` with `IBMPlexSans-OFL.txt` |
| JetBrains Mono Regular/Bold `woff2` | SIL Open Font License 1.1 | `python/src/ozempdf/web/fonts/JetBrainsMono-*.woff2` with `JetBrainsMono-OFL.txt` |

The web interface is plain HTML/CSS/JavaScript with no framework, no vendored JavaScript library
and no CDN request; the fonts are the only third-party assets shipped in the UI.

## Deliberate exclusions

- No Ghostscript, Poppler, qpdf or external PDF binaries: `pikepdf` (qpdf), `pypdfium2` and `Pillow`
  are compiled wheels, so the application runs fully offline.
- No third-party Python dependency is vendored under `python/src/`; all come from the environment
  and are collected into the frozen engine by PyInstaller at build time.

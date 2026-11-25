# Contributing to OzemPDF

Thanks for helping. OzemPDF is a small, deliberately conservative project: the engine rewrites PDF
objects, so nearly every rule here exists to keep a document from being damaged.

## Before you start

Two invariants govern every change:

1. **Never rasterize or rewrite page content.** Page `/Contents` operators, vector paths and text
   are not regenerated.
2. **Never grow a file.** The only exceptions are the transformations a user explicitly asks for:
   removing locks/signatures, and re-signing.

If a proposal conflicts with either invariant it will not be merged, however good the compression
result looks.

## Development setup

Requirements: Python 3.10+ (the tests and CI run on 3.10 and 3.14) and, for the desktop shell,
Node 22+.

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"

# optional: the Electron desktop shell
cd electron
npm install
npm start
```

Node is optional for engine work: `ozempdf --serve` runs the complete interface in a browser from
the Python package alone.

## Gates every change must pass

```bash
python -m pytest -q                  # 90+ tests, ~20 s
ruff check python/src python/tests   # style and imports
```

UI changes additionally need a look in both themes (light and dark) at desktop and mobile widths,
with keyboard navigation and reduced motion. The design contract is
[`docs/DESIGN.md`](docs/DESIGN.md); product truth is [`docs/PRODUCT.md`](docs/PRODUCT.md).

Packaging changes must rebuild the portable Windows ZIP and keep its documented contents:

```bash
python python/scripts/build_cross_platform.py   # frozen engine -> artifacts/dist/engine/
cd electron && npm run dist                     # ZIP -> release/OzemPDF-<version>-win.zip
```

The ZIP must contain `OzemPDF.exe`, `Start OzemPDF (compatibility).cmd`,
`Start OzemPDF in browser.cmd` and `resources/engine/`. Verify that the browser launcher serves the
UI (`--serve --no-browser --port <port>`, then request `/` and `/api/bootstrap`) and that the desktop
app opens from a normal writable extraction folder outside the checkout.

## Changelog and versioning

Add a bullet under the in-progress version in [`docs/CHANGELOG.md`](docs/CHANGELOG.md), in the
correct section (`Added`, `Changed`, `Fixed`, `Removed`). Bump
[`python/src/ozempdf/_version.py`](python/src/ozempdf/_version.py) when behaviour or the release
changes; `electron/package.json` mirrors the version.

Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html); the changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Commits and pull requests

- One atomic change per commit, in the imperative mood, using conventional subjects (`feat:`,
  `fix:`, `docs:`, `chore:`, `test:`, `perf:`, `style:`, `refactor:`), with a short subject.
- Explain *why* in the body when the reason is not obvious from the diff.
- Keep `release/`, `artifacts/`, `electron/node_modules/` and caches out of commits — `.gitignore`
  covers them. Never commit secrets.
- A pull request should state the problem, the approach, the risk to the two invariants, and the
  gates you ran. UI changes should include a screenshot in both themes.
- Do not mix unrelated refactors into a fix.

## Reporting a bug

Use the issue form. Include the version from the app header (or `ozempdf --version`), the OS, how you
installed it (portable ZIP, CLI, Electron from source), and the affected document's characteristics —
scanned or text, images, fonts, signatures, encryption — **not** the document itself when it is
confidential. A synthetic PDF that reproduces the problem is ideal.

## Security

Do not open a public issue for a vulnerability. Follow [`SECURITY.md`](SECURITY.md).

## Code of conduct

Participation is covered by [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## License

By contributing you agree that your contribution is licensed under the MIT license; see
[`LICENSE`](LICENSE).

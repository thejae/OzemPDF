## What and why

<!-- The problem, the approach, and why the change is safe. -->

## Invariants

- [ ] Page content is not rasterized or rewritten.
- [ ] Result files never grow (except the explicit unlock/flatten/sign paths).

## Gates

- [ ] `python -m pytest -q`
- [ ] `ruff check python/src python/tests`
- [ ] `docs/CHANGELOG.md` updated under the in-progress version
- [ ] Version bumped in `python/src/ozempdf/_version.py` and `electron/package.json` when behaviour
      or the release changed
- [ ] UI checked in both themes at desktop and mobile widths (UI changes only)
- [ ] `release/OzemPDF-<version>-win.zip` rebuilt and verified (packaging changes only)

## Notes for reviewers

<!-- Screenshots, risk areas, follow-ups. -->

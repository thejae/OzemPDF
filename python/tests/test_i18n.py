"""Localization behaviour and static key coverage."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ozempdf import i18n

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "ozempdf" / "web"
SOURCE = ROOT / "src" / "ozempdf"


@pytest.fixture(autouse=True)
def english() -> None:
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def test_translation_and_fallback() -> None:
    assert i18n.t("header.step1") == "Add PDFs"
    assert i18n.t("does.not.exist") == "does.not.exist"
    assert i18n.t("notices.added", count=3) == "3 file(s) queued"
    assert i18n.t("notices.added") == "{count} file(s) queued"


def test_formatting_errors_return_the_template() -> None:
    template = i18n.t("notices.added")
    assert "{" in template
    assert i18n.t("notices.added", wrong_placeholder="x") == template


def test_unknown_language_keeps_current() -> None:
    i18n.set_language("en")
    assert i18n.set_language("zz") is False
    assert i18n.current_language() == "en"


def test_available_languages_and_listener() -> None:
    languages = i18n.available_languages()
    assert any(entry["code"] == "en" for entry in languages)
    seen = []
    i18n.register_language_listener(seen.append)
    assert i18n.set_language("en") is True
    assert seen[-1] == "en"


def _used_keys() -> set[str]:
    """Every key referenced statically by the UI, the server and the CLI."""
    keys: set[str] = set()
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for attribute in ("data-i18n", "data-i18n-title", "data-i18n-aria", "data-i18n-placeholder"):
        keys.update(re.findall(rf'{attribute}="([\w.]+)"', html))
    javascript = (WEB / "app.js").read_text(encoding="utf-8")
    keys.update(re.findall(r"\bt\(\s*'([\w.]+)'", javascript))
    # Keys chosen at runtime: preset tooltips, the picker's label table, and
    # the Advanced toggle's two labels.
    keys.update({f"mode.tip_{preset}" for preset in ("lossless", "balanced", "max")})
    keys.update(
        {
            "picker.title_files", "picker.title_folder", "picker.title_output",
            "picker.title_cert", "picker.hint_files", "picker.hint_folder",
            "picker.hint_output", "picker.hint_cert", "picker.select_files",
            "picker.select_folder", "picker.select_cert",
            "mode.advanced", "mode.advanced_hide",
        }
    )
    for path in SOURCE.rglob("*.py"):
        keys.update(re.findall(r"\bt\(\s*[\"']([\w.]+)[\"']", path.read_text(encoding="utf-8")))
    return keys


def test_static_keys_exist_in_catalog() -> None:
    catalog = json.loads((SOURCE / "locales" / "en.json").read_text(encoding="utf-8"))
    flat = set(i18n._iter_flat(catalog))
    missing = sorted(key for key in _used_keys() if key not in flat)
    assert not missing, f"missing locale keys: {missing}"


def test_catalog_has_no_orphan_keys() -> None:
    """D-08: a dead key is a translation nobody should be asked to maintain."""
    catalog = json.loads((SOURCE / "locales" / "en.json").read_text(encoding="utf-8"))
    flat = set(i18n._iter_flat(catalog))
    meta = {"_meta.code", "_meta.name", "_meta.native_name"}
    orphans = sorted(flat - _used_keys() - meta)
    assert not orphans, f"orphan locale keys: {orphans}"


def test_catalog_has_no_empty_values() -> None:
    catalog = json.loads((SOURCE / "locales" / "en.json").read_text(encoding="utf-8"))
    empties = [key for key, value in i18n._iter_flat(catalog).items() if not value.strip()]
    assert not empties

"""Settings persistence and the D-07 / D-21 fixes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ozempdf import i18n, settings


@pytest.fixture
def config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "config"
    monkeypatch.setattr(settings, "settings_dir", lambda: target)
    return target


def test_roundtrip_keeps_only_whitelisted_keys(config_dir: Path) -> None:
    saved = settings.save_settings(
        {
            "dark_mode": True,
            "language": "en",
            "preset": "max",
            "advanced_open": True,
            "config": {
                "target_dpi": 110,
                "jpeg_quality": 75,
                "password": "must-not-persist",
                "sign_pkcs12_password": "must-not-persist",
                "sign_after_slimming": False,
            },
            "junk": "drop me",
        }
    )
    assert saved is True
    data = settings.load_settings()
    assert data["dark_mode"] is True
    assert data["language"] == "en"
    assert data["preset"] == "max"
    assert data["advanced_open"] is True
    assert "junk" not in data
    # D-07: the payload is driven by PERSISTED_KEYS, and secrets never land.
    assert set(data) <= set(settings.PERSISTED_KEYS)
    assert data["config"]["target_dpi"] == 110
    assert "password" not in data["config"]
    assert "sign_pkcs12_password" not in data["config"]


def test_portable_data_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A packaged/portable launch points every app file at its own data folder."""
    portable = tmp_path / "app" / "data"
    monkeypatch.setenv("OZEMPDF_DATA_DIR", str(portable))
    assert settings.settings_dir() == portable
    assert settings.save_settings({"dark_mode": False}) is True
    assert (portable / "settings.json").is_file()
    assert settings.load_settings()["dark_mode"] is False


def test_corrupt_settings_fall_back_to_defaults(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text("{not json", encoding="utf-8")
    assert settings.load_settings() == {}


def test_unwritable_settings_are_non_fatal(config_dir: Path) -> None:
    config_dir.parent.mkdir(parents=True, exist_ok=True)
    config_dir.write_text("a file where a directory should be", encoding="utf-8")
    assert settings.save_settings({"dark_mode": False}) is False
    assert settings.load_settings() == {}


def test_language_is_persisted_and_restored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locales = tmp_path / "locales"
    locales.mkdir()
    english = json.loads((Path(i18n.__file__).parent / "locales" / "en.json").read_text(encoding="utf-8"))
    (locales / "en.json").write_text(json.dumps(english), encoding="utf-8")
    french = {"_meta": {"code": "fr", "name": "French", "native_name": "Français"}}
    french.update({key: value for key, value in english.items() if key != "_meta"})
    (locales / "fr.json").write_text(json.dumps(french), encoding="utf-8")

    monkeypatch.setattr(i18n, "_LOCALES_DIR", locales)
    monkeypatch.setattr(settings, "settings_dir", lambda: tmp_path / "config")
    assert i18n.set_language("fr") is True
    assert settings.save_settings({"language": "fr", "dark_mode": True}) is True

    # A fresh session (what the web server builds on launch) restores the choice.
    from ozempdf.server import AppState

    state = AppState("token")
    assert state.language == "fr"
    assert i18n.current_language() == "fr"
    state.thumbnail_pool.shutdown(wait=False)
    i18n.set_language("en")

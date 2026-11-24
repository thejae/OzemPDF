"""Settings persistence.

A single small JSON file in the platform's per-user config location. Writes are
atomic; a missing, corrupt or unwritable file never stops the app from working.
The payload is driven by :data:`PERSISTED_KEYS` (defect D-07) and includes the
language choice (defect D-21).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

PERSISTED_KEYS = (
    "dark_mode",
    "language",
    "preset",
    "advanced_open",
    "config",
)

PERSISTED_CONFIG_KEYS = (
    "output_mode",
    "output_suffix",
    "output_dir",
    "strict_lossless",
    "enable_downsampling",
    "target_dpi",
    "jpeg_quality",
    "quantize_ui_screenshots",
    "ui_palette_colors",
    "zopfli_iterations",
    "strip_opaque_smask",
    "deduplicate_resources",
    "use_object_streams",
    "subset_fonts",
    "strip_bloat",
    "unlock_and_flatten_signatures",
    "strip_metadata",
    "sign_after_slimming",
    "sign_field_name",
    "sign_reason",
    "max_workers",
)


def settings_dir() -> Path:
    """Platform-standard per-user config directory for OzemPDF."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "OzemPDF"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OzemPDF"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "OzemPDF"


def settings_path() -> Path:
    """Full path of the settings file."""
    return settings_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    """Read stored settings; return ``{}`` on any problem."""
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(payload: dict[str, Any]) -> bool:
    """Persist the selected keys atomically. Never raises."""
    clean = {key: payload[key] for key in PERSISTED_KEYS if key in payload}
    config = clean.get("config")
    if isinstance(config, dict):
        clean["config"] = {
            key: value for key, value in config.items() if key in PERSISTED_CONFIG_KEYS
        }
    directory = settings_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temp = directory / "settings.json.tmp"
        temp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(settings_path())
        return True
    except OSError:
        return False

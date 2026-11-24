"""Settings persistence.

A single small JSON file in the app's data directory. Writes are atomic; a
missing, corrupt or unwritable file never stops the app from working. The
payload is driven by :data:`PERSISTED_KEYS` (defect D-07) and includes the
language choice (defect D-21).

A packaged/portable build keeps everything next to the executable: the
``OZEMPDF_DATA_DIR`` environment variable wins, then a frozen engine derives
``data/`` beside the app folder. A pip-installed CLI keeps the platform-standard
per-user config location.
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


def _portable_data_dir() -> Path | None:
    """Return the portable data directory, or None for a system install.

    Resolution order:

    1. ``OZEMPDF_DATA_DIR`` — set by the Electron shell and the Windows browser
       launcher so every process agrees.
    2. A frozen engine that lives inside the packaged app: walk up from the
       executable to the folder containing ``resources/`` (or ``OzemPDF.exe``)
       and use ``<that folder>/data``.
    3. A frozen engine run on its own: ``<engine folder>/data``.
    """
    override = os.environ.get("OZEMPDF_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    for parent in (executable.parent, *executable.parents):
        try:
            if (parent / "resources").is_dir() or (parent / "OzemPDF.exe").is_file():
                return parent / "data"
        except OSError:
            continue
    return executable.parent / "data"


def settings_dir() -> Path:
    """Directory holding settings, staging and other app-owned files."""
    portable = _portable_data_dir()
    if portable is not None:
        return portable
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

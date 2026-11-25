"""Locating package data in source checkouts and PyInstaller builds."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Return a bundled resource path, honouring a frozen build's extraction dir.

    PyInstaller extracts ``--add-data`` payloads under ``sys._MEIPASS``; a source
    checkout keeps them next to this module. Both resolve through this helper so
    locales, web assets and fonts are found in either mode.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "ozempdf"
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)

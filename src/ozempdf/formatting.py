"""Human-readable byte formatting shared by every surface."""

from __future__ import annotations

_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_bytes(value: int | float | None) -> str:
    """Format a byte count as ``14.2 MB`` (binary units, UI-style labels)."""
    if value is None:
        return "—"
    size = float(value)
    negative = size < 0
    size = abs(size)
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            text = f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            return f"-{text}" if negative else text
        size /= 1024
    return f"{size:.1f} TB"


def format_percent(value: float | None, digits: int = 1) -> str:
    """Format a percentage with a leading sign and fixed decimals."""
    if value is None:
        return "—"
    return f"{value:.{digits}f}%"

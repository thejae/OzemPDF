"""JSON catalog localization.

Catalogs are plain UTF-8 JSON files in ``locales/``; dropping a new
``<code>.json`` in that directory is the entire cost of adding a language.
"""

from __future__ import annotations

import json
import locale
import os
import threading
from collections.abc import Callable
from typing import Any

from .resources import resource_path

_LOCALES_DIR = resource_path("locales")
_FALLBACK = "en"
_lock = threading.RLock()
_catalog: dict[str, Any] = {}
_fallback: dict[str, Any] = {}
_language = ""
_listeners: list[Callable[[str], None]] = []


def _read_catalog(code: str) -> dict[str, Any] | None:
    """Load one catalog file, or None when it does not exist or is invalid."""
    path = _LOCALES_DIR / f"{code}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def available_languages() -> list[dict[str, str]]:
    """Every bundled locale, sorted by code, with its native display name."""
    languages: list[dict[str, str]] = []
    if not _LOCALES_DIR.is_dir():
        return languages
    for path in sorted(_LOCALES_DIR.glob("*.json")):
        data = _read_catalog(path.stem)
        if data is None:
            continue
        meta = data.get("_meta", {})
        languages.append(
            {
                "code": path.stem,
                "name": str(meta.get("name", path.stem)),
                "native_name": str(meta.get("native_name", path.stem)),
            }
        )
    return languages


def _system_code() -> str:
    """Best-effort OS language code (``en_US``), never raising."""
    try:
        code = locale.getlocale()[0]
    except (ValueError, TypeError):
        code = None
    if not code:
        code = os.environ.get("LANG") or os.environ.get("LANGUAGE") or ""
    return code or _FALLBACK


def _resolve_code(candidate: str) -> str | None:
    """Map a requested code onto an existing catalog (full code, then short)."""
    candidate = (candidate or "").strip().replace("-", "_")
    if not candidate:
        return None
    for option in (candidate, candidate.split("_")[0], candidate.lower()):
        if _read_catalog(option) is not None:
            return option
    return None


def set_language(code: str) -> bool:
    """Activate a locale. Returns False for unknown codes, keeping the current one."""
    global _catalog, _language
    with _lock:
        resolved = _resolve_code(code)
        if resolved is None:
            return False
        catalog = _read_catalog(resolved)
        if catalog is None:
            return False
        _catalog = catalog
        _language = resolved
        listeners = list(_listeners)
    for listener in listeners:
        try:
            listener(resolved)
        except Exception:
            continue
    return True


def current_language() -> str:
    """The active locale code."""
    return _language or _FALLBACK


def register_language_listener(callback: Callable[[str], None]) -> None:
    """Register a callback fired after every successful language switch."""
    with _lock:
        _listeners.append(callback)


def _lookup(catalog: dict[str, Any], key: str) -> Any:
    """Resolve a dotted path inside a nested catalog."""
    node: Any = catalog
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def t(key: str, **kwargs: Any) -> str:
    """Translate *key*, falling back to English and then to the raw key.

    A missing placeholder or an invalid format string returns the unformatted
    template rather than raising into the caller (FR-I18N rules).
    """
    template = _lookup(_catalog, key)
    if template is None:
        template = _lookup(_fallback, key)
    if template is None:
        return key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError, IndexError):
        return template


def flatten_catalog() -> dict[str, str]:
    """Flatten the active catalog to ``{dot.key: string}`` for the web client."""
    flat: dict[str, str] = {}

    def walk(node: dict[str, Any], prefix: str) -> None:
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(value, path)
            elif isinstance(value, str):
                flat[path] = value

    with _lock:
        walk(_catalog, "")
        # Fill gaps from English so the client never renders a raw key.
        missing = {
            key: value
            for key, value in _iter_flat(_fallback).items()
            if key not in flat and not key.startswith("_meta.")
        }
    flat.update(missing)
    return flat


def _iter_flat(node: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten a catalog without touching the active locale."""
    flat: dict[str, str] = {}

    def walk(current: dict[str, Any], path_prefix: str) -> None:
        for key, value in current.items():
            path = f"{path_prefix}.{key}" if path_prefix else key
            if isinstance(value, dict):
                walk(value, path)
            elif isinstance(value, str):
                flat[path] = value

    walk(node, prefix)
    return flat


def bootstrap(preferred: str | None = None) -> str:
    """Load the fallback catalog and pick the startup language.

    Precedence: explicit *preferred* → ``OZEMPDF_LANG`` → OS locale → English.
    """
    global _fallback
    _fallback = _read_catalog(_FALLBACK) or {}
    requested = preferred or os.environ.get("OZEMPDF_LANG") or _system_code()
    if not set_language(requested):
        set_language(_FALLBACK)
    return current_language()


bootstrap()

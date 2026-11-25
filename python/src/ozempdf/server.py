"""Local HTTP API and static file server.

The browser and the Electron shell are both thin clients of this process: all
queue state, configuration and compression live here. The server binds to
loopback only and requires a per-session token cookie for every API call.
"""

from __future__ import annotations

import json
import mimetypes
import os
import stat
import subprocess
import sys
import threading
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ._version import __version__
from .config import CompressionConfig, OutputMode
from .engine import (
    BatchCancelledError,
    discover_pdf_files,
    inspect_pdf_quick_metadata,
    inspect_protection,
    render_page_png,
    render_page_thumbnail_png,
    run_batch_compression,
)
from .i18n import available_languages, current_language, flatten_catalog, set_language, t
from .logging_setup import get_logger
from .resources import resource_path
from .settings import load_settings, save_settings

log = get_logger()

WEB_DIR = resource_path("web")
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
TEMP_PREFIX = ".ozempdf_"


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #


class AppState:
    """Everything one running app session owns."""

    def __init__(self, token: str, preload: list[str] | None = None) -> None:
        self.token = token
        self.lock = threading.RLock()
        self.settings = load_settings()
        self.dark_mode = bool(self.settings.get("dark_mode", True))
        self.advanced_open = bool(self.settings.get("advanced_open", False))
        self.language = str(self.settings.get("language", "") or current_language())
        if not set_language(self.language):
            self.language = current_language()
        self.config = CompressionConfig.from_dict(
            self.settings.get("config", {}),
            base=CompressionConfig.from_preset(str(self.settings.get("preset", "balanced"))),
        )
        self.items: dict[str, dict] = {}
        self.order: list[str] = []
        self.run_thread: threading.Thread | None = None
        self.running = False
        self.stop_requested = False
        self.run_completed = 0
        self.run_total = 0
        self.run_summary: dict | None = None
        self.run_cancelled = False
        self.pending_preload = list(preload or [])
        self.thumbnail_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumb")
        self._persist()

    # -- persistence ------------------------------------------------------- #

    def _persist(self) -> None:
        """Write UI state and knobs; never store passwords or certificate paths."""
        public = self.config.to_dict(public=False)
        for secret in ("password", "sign_pkcs12_password", "sign_pkcs12_path"):
            public.pop(secret, None)
        save_settings(
            {
                "dark_mode": self.dark_mode,
                "language": self.language,
                "advanced_open": self.advanced_open,
                "preset": self.config.preset.value,
                "config": public,
            }
        )

    # -- queue ------------------------------------------------------------- #

    def add_paths(self, paths: list[str], password: str = "") -> dict:
        """Queue discovered PDFs; report what was skipped and why (US-004)."""
        with self.lock:
            suffix = self.config.output_suffix if self.config.output_mode is OutputMode.SUFFIX else ""
            pairs = discover_pdf_files([p for p in paths if p], exclude_suffix=suffix)
            skipped: list[dict] = []
            existing = {item["path"] for item in self.items.values()}
            has_results = any(item["status"] == "done" for item in self.items.values())
            added = 0
            for path, base in pairs:
                resolved = str(path)
                if resolved in existing:
                    skipped.append({"path": resolved, "reason": t("notices.skip_duplicate")})
                    continue
                item = self._make_item(path, base, "new_queued" if has_results else "queued", password)
                self.items[item["id"]] = item
                self.order.append(item["id"])
                existing.add(resolved)
                added += 1
                self._schedule_thumbnail(item)
            for raw in paths:
                candidate = Path(raw).expanduser()
                if candidate.is_file() and not _is_pdf(candidate.name):
                    skipped.append({"path": str(candidate), "reason": t("notices.skip_not_pdf")})
                elif candidate.is_dir():
                    try:
                        resolved_dir = candidate.resolve()
                    except OSError:
                        continue
                    found_inside = any(
                        discovered.parent == resolved_dir
                        or resolved_dir in discovered.parents
                        for discovered, _base in pairs
                    )
                    if not found_inside:
                        skipped.append(
                            {"path": str(candidate), "reason": t("notices.skip_no_pdfs")}
                        )
            return {"added": added, "skipped": skipped, "queue": self.queue_view()}

    def _make_item(self, path: Path, base: Path | None, status: str, password: str) -> dict:
        """Inspect one file and build its queue row."""
        protection = inspect_protection(path)
        pages, size = inspect_pdf_quick_metadata(path, password)
        locked = protection.needs_credential
        error = ""
        if pages == 0:
            try:
                import pikepdf

                with pikepdf.open(path, password=password or "") as pdf:
                    pages = len(pdf.pages)
            except PermissionError:
                error = t("errors.permission_denied", path=path)
            except Exception as exc:
                if not locked:
                    error = t("errors.read_failed")
                else:
                    log.debug("protected file could not be inspected: %s", exc)
        rel_path = ""
        if base is not None:
            try:
                rel_path = str(path.relative_to(base))
            except ValueError:
                rel_path = path.name
        return {
            "id": uuid.uuid4().hex,
            "path": str(path),
            "base_folder": str(base) if base else None,
            "name": path.name,
            "rel_path": rel_path,
            "pages": pages,
            "size": size,
            "status": "error" if error else status,
            "error": error,
            "locked": locked,
            "protection": protection.kind.value,
            "result": None,
            "thumb": None,
            "thumb_version": 0,
            # Session-only credential for rendering previews; never persisted and
            # never included in _item_view. The compressed output is always plain.
            "password": password,
        }

    def _schedule_thumbnail(self, item: dict) -> None:
        """Render the page-1 thumbnail off the request thread."""

        def work() -> None:
            data = render_page_thumbnail_png(
                Path(item["path"]), max_dimension=110, password=item.get("password", "")
            )
            with self.lock:
                item["thumb"] = data
                item["thumb_version"] += 1

        self.thumbnail_pool.submit(work)

    def remove_item(self, item_id: str) -> bool:
        with self.lock:
            if self.running or item_id not in self.items:
                return False
            del self.items[item_id]
            self.order = [key for key in self.order if key != item_id]
            return True

    def clear_queue(self) -> bool:
        with self.lock:
            if self.running:
                return False
            self.items.clear()
            self.order = []
            self.run_summary = None
            self.run_completed = 0
            self.run_total = 0
            return True

    def retry_item(self, item_id: str) -> bool:
        with self.lock:
            item = self.items.get(item_id)
            if item is None or self.running:
                return False
            item.update(status="queued", result=None, error="")
            return True

    def queue_view(self) -> list[dict]:
        with self.lock:
            return [self._item_view(self.items[key]) for key in self.order if key in self.items]

    def _item_view(self, item: dict) -> dict:
        return {
            "id": item["id"],
            "path": item["path"],
            "name": item["name"],
            "rel_path": item["rel_path"],
            "pages": item["pages"],
            "size": item["size"],
            "status": item["status"],
            "error": item["error"],
            "locked": item["locked"],
            "protection": item["protection"],
            "result": _public_result(item["result"]),
            "has_thumb": item["thumb"] is not None,
            "thumb_version": item["thumb_version"],
        }

    # -- runs -------------------------------------------------------------- #

    def start_run(self, payload: dict) -> dict:
        """Start compressing pending rows in a background thread."""
        with self.lock:
            if self.running:
                return {"ok": False, "error": t("errors.run_in_progress")}
            config = CompressionConfig.from_dict(payload.get("config", {}), base=self.config)
            password = str(payload.get("password", self.config.password) or "")
            config.password = password
            self.config = config
            ids = payload.get("ids")
            targets = [
                key
                for key in self.order
                if (ids is None or key in ids)
                and self.items[key]["status"] in {"queued", "new_queued"}
            ]
            if not targets:
                return {"ok": False, "error": t("errors.nothing_to_slim")}
            for key in targets:
                item = self.items[key]
                item.update(
                    status="processing",
                    result=None,
                    error="",
                    # A run without a password keeps whatever the row was added
                    # with, so its preview still opens.
                    password=password or item.get("password", ""),
                )
            self.stop_requested = False
            self.running = True
            self.run_cancelled = False
            self.run_completed = 0
            self.run_total = len(targets)
            self.run_summary = None
            self._persist()
            thread = threading.Thread(
                target=self._run_worker,
                args=(list(targets), config),
                name="ozempdf-batch",
                daemon=True,
            )
            self.run_thread = thread
            thread.start()
            return {"ok": True}

    def _run_worker(self, ids: list[str], config: CompressionConfig) -> None:
        """Compress the selected rows, updating state as each file completes."""
        try:
            with self.lock:
                items = [
                    (Path(self.items[key]["path"]), Path(self.items[key]["base_folder"]) if self.items[key]["base_folder"] else None)
                    for key in ids
                ]

            def progress(completed: int, total: int, result) -> None:
                with self.lock:
                    item = next(
                        (self.items[key] for key in ids if self.items[key]["path"] == result.input_path),
                        None,
                    )
                    if item is not None:
                        item["result"] = result.to_dict()
                        item["status"] = "done" if result.success else "error"
                        item["error"] = result.error_message
                        if result.success and result.output_path:
                            item["pages"] = result.page_count or item["pages"]
                    self.run_completed = completed
                    self.run_total = total
                    if self.stop_requested:
                        raise BatchCancelledError()

            summary = run_batch_compression(items, config, progress)
            with self.lock:
                self.run_summary = summary.to_dict()
                # Anything never dispatched returns to the plain queued state.
                for key in ids:
                    item = self.items.get(key)
                    if item is not None and item["status"] == "processing":
                        item.update(status="queued", result=None)
                self.run_cancelled = self.stop_requested
        except BatchCancelledError:
            with self.lock:
                for key in ids:
                    item = self.items.get(key)
                    if item is not None and item["status"] == "processing":
                        item.update(status="queued", result=None)
                self.run_cancelled = True
                self.run_summary = None
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("run failed", exc_info=True)
            with self.lock:
                for key in ids:
                    item = self.items.get(key)
                    if item is not None and item["status"] == "processing":
                        item.update(status="error", error=str(exc))
        finally:
            with self.lock:
                self.running = False
                self.run_thread = None

    def stop_run(self) -> None:
        with self.lock:
            if self.running:
                self.stop_requested = True

    def run_view(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "completed": self.run_completed,
                "total": self.run_total,
                "summary": self.run_summary,
                "cancelled": self.run_cancelled,
                "queue": self.queue_view(),
            }

    # -- views ------------------------------------------------------------- #

    def bootstrap(self) -> dict:
        with self.lock:
            if self.pending_preload:
                pending = self.pending_preload
                self.pending_preload = []
            else:
                pending = []
        if pending:
            self.add_paths(pending)
        return {
            "version": __version__,
            "language": current_language(),
            "languages": available_languages(),
            "strings": flatten_catalog(),
            "settings": {
                "dark_mode": self.dark_mode,
                "advanced_open": self.advanced_open,
                "language": current_language(),
            },
            "config": self.config.to_dict(public=True),
            "queue": self.queue_view(),
            "run": self.run_view(),
            "platform": sys.platform,
        }


def _public_result(result: dict | None) -> dict | None:
    """Strip absolute internals the UI does not need from a file result."""
    if not result:
        return None
    return {
        "success": result.get("success", False),
        "output_path": result.get("output_path", ""),
        "error_message": result.get("error_message", ""),
        "original_bytes": result.get("original_bytes", 0),
        "compressed_bytes": result.get("compressed_bytes", 0),
        "bytes_saved": result.get("bytes_saved", 0),
        "reduction_percent": result.get("reduction_percent", 0.0),
        "page_count": result.get("page_count", 0),
        "elapsed_seconds": result.get("elapsed_seconds", 0.0),
        "already_optimal": result.get("already_optimal", False),
        "kept_original_guardrail": result.get("kept_original_guardrail", False),
        "protection_kind": result.get("protection_kind", "none"),
        "protection_removed": result.get("protection_removed", False),
        "required_password": result.get("required_password", False),
        "signed": result.get("signed", False),
        "signatures_flattened": result.get("signatures_flattened", 0),
        "before_breakdown": result.get("before_breakdown", {}),
        "after_breakdown": result.get("after_breakdown", {}),
    }


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #


class OzemPDFServer(ThreadingHTTPServer):
    """Threaded loopback server carrying the session state."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: AppState) -> None:
        super().__init__(address, Handler)
        self.state = state


class Handler(BaseHTTPRequestHandler):
    """Minimal JSON + static file handler (no third-party framework)."""

    server_version = f"OzemPDF/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- helpers ----------------------------------------------------------- #

    @property
    def state(self) -> AppState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib API
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if getattr(self, "command", "") != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def _authorised(self) -> bool:
        if self.path.startswith("/api/"):
            header = self.headers.get("X-OzemPDF-Token", "")
            if header and header == self.state.token:
                return True
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "ozempdf_token" and value == self.state.token:
                    return True
            return False
        return True

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        if length > 16 * 1024 * 1024:
            raise ValueError("request too large")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _origin_ok(self) -> bool:
        """Reject cross-site POSTs from a foreign origin (CSRF defence)."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin.endswith(host) or origin in {f"http://{host}", f"https://{host}"}

    # -- routing ----------------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                return self._serve_index()
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if path == "/api/bootstrap":
                if not self._authorised():
                    return self._error(HTTPStatus.FORBIDDEN, t("errors.unauthorised"))
                return self._json(self.state.bootstrap())
            if path == "/api/run":
                if not self._authorised():
                    return self._error(HTTPStatus.FORBIDDEN, t("errors.unauthorised"))
                return self._json(self.state.run_view())
            if path == "/api/thumb":
                return self._serve_thumbnail(parse_qs(parsed.query))
            if path == "/api/page":
                return self._serve_page(parse_qs(parsed.query))
            if path == "/api/report":
                return self._serve_report()
            self._error(HTTPStatus.NOT_FOUND, t("errors.not_found"))
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("GET %s failed", self.path, exc_info=True)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        try:
            if not self._authorised():
                return self._error(HTTPStatus.FORBIDDEN, t("errors.unauthorised"))
            if not self._origin_ok():
                return self._error(HTTPStatus.FORBIDDEN, t("errors.unauthorised"))
            path = urlparse(self.path).path
            routes = {
                "/api/queue/add": self._api_add,
                "/api/queue/remove": self._api_remove,
                "/api/queue/clear": self._api_clear,
                "/api/queue/retry": self._api_retry,
                "/api/run/start": self._api_start,
                "/api/run/stop": self._api_stop,
                "/api/config": self._api_config,
                "/api/preset": self._api_preset,
                "/api/ui": self._api_ui,
                "/api/fs/list": self._api_fs_list,
                "/api/open": self._api_open,
            }
            handler = routes.get(path)
            if handler is None:
                return self._error(HTTPStatus.NOT_FOUND, t("errors.not_found"))
            return handler(self._read_json())
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("POST %s failed", self.path, exc_info=True)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_PUT(self) -> None:  # noqa: N802 - stdlib API
        try:
            if not self._authorised() or not self._origin_ok():
                return self._error(HTTPStatus.FORBIDDEN, t("errors.unauthorised"))
            parsed = urlparse(self.path)
            if parsed.path == "/api/upload":
                return self._api_upload(parse_qs(parsed.query))
            self._error(HTTPStatus.NOT_FOUND, t("errors.not_found"))
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("PUT %s failed", self.path, exc_info=True)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    # -- static ------------------------------------------------------------ #

    def _serve_index(self) -> None:
        index = WEB_DIR / "index.html"
        if not index.is_file():
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "web assets missing")
        body = index.read_bytes()
        cookie = (
            f"ozempdf_token={self.state.token}; Path=/; SameSite=Strict; HttpOnly"
        )
        self._send(
            HTTPStatus.OK,
            body,
            "text/html; charset=utf-8",
            {
                "Set-Cookie": cookie,
                "Content-Security-Policy": (
                    "default-src 'self'; img-src 'self' data: blob:; "
                    "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                    "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'"
                ),
                "Cache-Control": "no-store",
            },
        )

    def _serve_static(self, relative: str) -> None:
        target = (WEB_DIR / relative).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._error(HTTPStatus.FORBIDDEN, t("errors.not_found"))
        if not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, t("errors.not_found"))
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), content_type, {"Cache-Control": "no-cache"})

    def _serve_thumbnail(self, query: dict) -> None:
        item_id = (query.get("id") or [""])[0]
        with self.state.lock:
            item = self.state.items.get(item_id)
            data = item["thumb"] if item else None
        if not data:
            return self._error(HTTPStatus.NOT_FOUND, t("errors.not_found"))
        self._send(HTTPStatus.OK, data, "image/png", {"Cache-Control": "private, max-age=300"})

    def _serve_page(self, query: dict) -> None:
        item_id = (query.get("id") or [""])[0]
        side = (query.get("side") or ["orig"])[0]
        page = max(1, int((query.get("page") or ["1"])[0] or 1))
        zoom = max(25, min(800, int((query.get("zoom") or ["200"])[0] or 200)))
        with self.state.lock:
            item = self.state.items.get(item_id)
            if item is None:
                return self._error(HTTPStatus.NOT_FOUND, t("errors.not_found"))
            source = item["path"]
            password = item.get("password", "")
            result = item["result"]
            if side == "slim" and result and result.get("success"):
                source = result.get("output_path") or source
        data = render_page_png(
            Path(source), page_index=page - 1, zoom_percent=zoom, password=password
        )
        if not data:
            return self._error(HTTPStatus.NOT_FOUND, t("errors.render_failed"))
        self._send(HTTPStatus.OK, data, "image/png", {"Cache-Control": "private, max-age=300"})

    def _serve_report(self) -> None:
        with self.state.lock:
            summary = self.state.run_summary
        if summary is None:
            return self._error(HTTPStatus.NOT_FOUND, t("errors.not_found"))
        body = json.dumps(summary, indent=2, ensure_ascii=False).encode("utf-8")
        self._send(
            HTTPStatus.OK,
            body,
            "application/json; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="ozempdf-report.json"'},
        )

    # -- API handlers ------------------------------------------------------ #

    def _api_add(self, payload: dict) -> None:
        paths = [str(p) for p in payload.get("paths", []) if isinstance(p, str)]
        if not paths:
            return self._error(HTTPStatus.BAD_REQUEST, t("errors.no_paths"))
        result = self.state.add_paths(paths, str(payload.get("password", "")))
        return self._json({"ok": True, **result})

    def _api_remove(self, payload: dict) -> None:
        ok = self.state.remove_item(str(payload.get("id", "")))
        return self._json({"ok": ok, "queue": self.state.queue_view()})

    def _api_clear(self, payload: dict) -> None:
        ok = self.state.clear_queue()
        return self._json({"ok": ok, "queue": self.state.queue_view()})

    def _api_retry(self, payload: dict) -> None:
        ok = self.state.retry_item(str(payload.get("id", "")))
        return self._json({"ok": ok, "queue": self.state.queue_view()})

    def _api_start(self, payload: dict) -> None:
        return self._json({**self.state.start_run(payload), "run": self.state.run_view()})

    def _api_stop(self, payload: dict) -> None:
        self.state.stop_run()
        return self._json({"ok": True})

    def _api_config(self, payload: dict) -> None:
        with self.state.lock:
            config_data = payload.get("config", payload)
            if not isinstance(config_data, dict):
                return self._error(HTTPStatus.BAD_REQUEST, t("errors.bad_request"))
            self.state.config = CompressionConfig.from_dict(
                config_data, base=self.state.config
            )
            self.state._persist()
            return self._json(
                {"ok": True, "config": self.state.config.to_dict(public=True)}
            )

    def _api_preset(self, payload: dict) -> None:
        """Apply a preset while preserving the output destination and signing setup."""
        with self.state.lock:
            old = self.state.config
            preset = str(payload.get("preset", "balanced"))
            new = CompressionConfig.from_preset(preset)
            new.output_mode = old.output_mode
            new.output_suffix = old.output_suffix
            new.output_dir = old.output_dir
            new.password = old.password
            new.sign_after_slimming = old.sign_after_slimming
            new.sign_pkcs12_path = old.sign_pkcs12_path
            new.sign_pkcs12_password = old.sign_pkcs12_password
            new.sign_reason = old.sign_reason
            new.sign_field_name = old.sign_field_name
            new.sign_tsa_url = old.sign_tsa_url
            self.state.config = new.clamped()
            self.state._persist()
            return self._json(
                {"ok": True, "config": self.state.config.to_dict(public=True)}
            )

    def _api_ui(self, payload: dict) -> None:
        with self.state.lock:
            if "dark_mode" in payload:
                self.state.dark_mode = bool(payload["dark_mode"])
            if "advanced_open" in payload:
                self.state.advanced_open = bool(payload["advanced_open"])
            if "language" in payload:
                code = str(payload["language"] or "")
                if set_language(code):
                    self.state.language = current_language()
            self.state._persist()
            return self._json(
                {
                    "ok": True,
                    "language": current_language(),
                    "strings": flatten_catalog(),
                    "languages": available_languages(),
                    "settings": {
                        "dark_mode": self.state.dark_mode,
                        "advanced_open": self.state.advanced_open,
                    },
                    "config": self.state.config.to_dict(public=True),
                }
            )

    def _api_fs_list(self, payload: dict) -> None:
        raw = payload.get("path")
        kinds = payload.get("kinds") or ["pdf"]
        extensions: set[str] = set()
        if "pdf" in kinds:
            extensions.add(".pdf")
        if "cert" in kinds:
            extensions.update({".p12", ".pfx"})
        if not raw:
            start = Path.home()
        else:
            start = Path(str(raw)).expanduser()
        if not start.is_dir():
            start = start.parent if start.parent.is_dir() else Path.home()
        try:
            start = start.resolve()
        except OSError:
            start = Path.home()
        entries: list[dict] = []
        try:
            children = sorted(
                start.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())
            )
        except OSError:
            children = []
        for child in children:
            try:
                if _is_hidden(child):
                    continue
                is_dir = child.is_dir()
                if is_dir:
                    entries.append({"name": child.name, "path": str(child), "is_dir": True})
                    continue
                if child.suffix.lower() not in extensions:
                    continue
                entries.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "is_dir": False,
                        "size": child.stat().st_size,
                    }
                )
            except OSError:
                continue
        parent = str(start.parent) if start.parent != start else None
        return self._json(
            {
                "ok": True,
                "path": str(start),
                "parent": parent,
                "home": str(Path.home()),
                "roots": _file_roots(),
                "entries": entries[:1000],
            }
        )

    def _api_open(self, payload: dict) -> None:
        kind = str(payload.get("kind", "output"))
        target: Path | None = None
        with self.state.lock:
            if kind == "file":
                item = self.state.items.get(str(payload.get("id", "")))
                if item is not None:
                    result = item.get("result") or {}
                    target = Path(result.get("output_path") or item["path"])
            elif kind == "folder":
                target = Path(self.state.config.output_dir) if self.state.config.output_dir else None
                if target is None:
                    for item in self.state.items.values():
                        result = item.get("result") or {}
                        if result.get("success") and result.get("output_path"):
                            target = Path(result["output_path"]).parent
                            break
                if target is None:
                    for item in self.state.items.values():
                        target = Path(item["path"]).parent
                        break
        if target is None or not target.exists():
            return self._json({"ok": False, "error": t("errors.path_missing")})
        _open_in_os(target)
        return self._json({"ok": True})

    def _api_upload(self, query: dict) -> None:
        name = Path((query.get("name") or ["dropped.pdf"])[0]).name or "dropped.pdf"
        if not _is_pdf(name):
            return self._error(HTTPStatus.BAD_REQUEST, t("notices.skip_not_pdf"))
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            return self._error(HTTPStatus.BAD_REQUEST, t("errors.upload_failed"))
        staging = _staging_dir()
        try:
            staging.mkdir(parents=True, exist_ok=True)
        except OSError:
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, t("errors.upload_failed"))
        target = staging / f"{uuid.uuid4().hex[:8]}_{name}"
        remaining = length
        try:
            with target.open("wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
        except OSError:
            target.unlink(missing_ok=True)
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, t("errors.upload_failed"))
        result = self.state.add_paths([str(target)])
        return self._json({"ok": True, "staged": str(target), **result})


# --------------------------------------------------------------------------- #
# Platform helpers
# --------------------------------------------------------------------------- #


def _is_pdf(name: str) -> bool:
    """True for a real PDF name, excluding our temporary files."""
    return name.lower().endswith(".pdf") and not name.startswith(TEMP_PREFIX)


def _is_hidden(path: Path) -> bool:
    """Dot names and (on Windows) entries with the hidden attribute set."""
    if path.name.startswith("."):
        return True
    if os.name == "nt":
        try:
            return bool(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)
        except (OSError, AttributeError):
            return False
    return False


def _file_roots() -> list[dict]:
    """Drives on Windows, otherwise the filesystem root and home."""
    roots: list[dict] = []
    if sys.platform == "win32":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:/")
            try:
                if drive.exists():
                    roots.append({"name": f"{letter}:", "path": str(drive)})
            except OSError:
                continue
        return roots
    roots.append({"name": "/", "path": "/"})
    roots.append({"name": "Home", "path": str(Path.home())})
    return roots


def _staging_dir() -> Path:
    """Where browser file drops land (browsers cannot expose real paths)."""
    from .settings import settings_dir

    return settings_dir() / "staging"


def _open_in_os(target: Path) -> None:
    """Hand a file or folder to the system handler, never raising."""
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception:
        log.debug("could not open %s", target, exc_info=True)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def create_server(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    preload: list[str] | None = None,
) -> tuple[OzemPDFServer, AppState]:
    """Bind the server and return it with its session state."""
    token = uuid.uuid4().hex
    state = AppState(token, preload=preload)
    server = OzemPDFServer((host, port), state)
    return server, state


def serve(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    open_browser: bool = True,
    preload: list[str] | None = None,
    announce: bool = True,
) -> int:
    """Run the local app server until interrupted."""
    server, _state = create_server(host, port, preload=preload)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/"
    if announce:
        print(f"OZEMPDF_READY {json.dumps({'port': actual_port, 'url': url})}", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

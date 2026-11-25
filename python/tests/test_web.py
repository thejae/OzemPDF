"""The local HTTP API: auth, queue, run, images, picker and uploads."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

from ozempdf import settings
from ozempdf.server import create_server


class Client:
    """Cookie-carrying JSON client for the loopback API."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, path: str) -> tuple[int, bytes, dict]:
        return self._request(path, None, "GET")

    def post(self, path: str, body: dict | None = None) -> tuple[int, bytes, dict]:
        return self._request(path, body, "POST")

    def put(self, path: str, data: bytes) -> tuple[int, bytes, dict]:
        request = urllib.request.Request(self.base + path, data=data, method="PUT")
        return self._send(request)

    def _request(self, path: str, body: dict | None, method: str):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        return self._send(request)

    def _send(self, request: urllib.request.Request):
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(settings, "settings_dir", lambda: tmp_path / "config")
    server, state = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield state, base
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    state.thumbnail_pool.shutdown(wait=False)


def _json(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


def test_index_serves_ui_and_sets_session_cookie(live) -> None:
    _state, base = live
    client = Client(base)
    status, body, headers = client.get("/")
    assert status == 200
    assert b"OzemPDF" in body
    assert "ozempdf_token" in headers.get("Set-Cookie", "")

    status, body, _headers = client.get("/api/bootstrap")
    assert status == 200
    payload = _json(body)
    assert payload["version"] == "0.3.7"
    assert "header.step1" in payload["strings"]
    assert payload["queue"] == []


def test_static_version_label_matches_package_version() -> None:
    """The hardcoded fallback in index.html must never drift (it once said 0.3.0)."""
    from ozempdf import __version__
    from ozempdf.server import WEB_DIR

    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert f'id="versionLabel">v{__version__}<' in html


def test_api_requires_the_session_cookie(live) -> None:
    _state, base = live
    anonymous = Client(base)
    status, _body, _headers = anonymous.get("/api/bootstrap")
    assert status == 403


def test_static_path_traversal_is_blocked(live) -> None:
    _state, base = live
    client = Client(base)
    client.get("/")
    status, _body, _headers = client.get("/static/../server.py")
    assert status in {403, 404}


def test_queue_add_run_and_images(live, assets, tmp_path: Path) -> None:
    state, base = live
    client = Client(base)
    client.get("/")

    output_dir = tmp_path / "out"
    status, body, _headers = client.post(
        "/api/config",
        {
            "config": {
                "output_mode": "folder",
                "output_dir": str(output_dir),
                "preset": "balanced",
            }
        },
    )
    assert status == 200
    assert _json(body)["config"]["output_mode"] == "folder"

    status, body, _headers = client.post("/api/queue/add", {"paths": [str(assets.ui)]})
    assert status == 200
    payload = _json(body)
    assert payload["added"] == 1
    item = payload["queue"][0]
    assert item["status"] == "queued"
    assert item["name"] == assets.ui.name

    # Re-adding is a no-op with a reason (US-003 / US-004).
    status, body, _headers = client.post("/api/queue/add", {"paths": [str(assets.ui)]})
    duplicate = _json(body)
    assert duplicate["added"] == 0
    assert duplicate["skipped"][0]["reason"]

    status, body, _headers = client.get("/api/bootstrap")
    config = _json(body)["config"]
    status, body, _headers = client.post(
        "/api/run/start", {"ids": [item["id"]], "config": config}
    )
    assert status == 200 and _json(body)["ok"] is True

    for _ in range(120):
        status, body, _headers = client.get("/api/run")
        run = _json(body)
        if not run["running"]:
            break
        time.sleep(0.2)
    assert run["summary"]["succeeded_count"] == 1
    assert run["queue"][0]["status"] == "done"
    result = run["queue"][0]["result"]
    assert result["reduction_percent"] > 0
    assert Path(result["output_path"]).is_file()

    status, png, headers = client.get(f"/api/thumb?id={item['id']}")
    assert status == 200
    assert png.startswith(b"\x89PNG")
    assert headers["Content-Type"] == "image/png"

    status, page, _headers = client.get(
        f"/api/page?id={item['id']}&side=slim&page=1&zoom=200"
    )
    assert status == 200
    assert page.startswith(b"\x89PNG")


def test_fs_list_filters_and_hides(live, tmp_path: Path) -> None:
    _state, base = live
    client = Client(base)
    client.get("/")
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    (folder / ".hidden.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "sub").mkdir()

    status, body, _headers = client.post("/api/fs/list", {"path": str(folder), "kinds": ["pdf"]})
    assert status == 200
    payload = _json(body)
    names = {entry["name"] for entry in payload["entries"]}
    assert "a.pdf" in names
    assert "sub" in names
    assert "notes.txt" not in names
    assert ".hidden.pdf" not in names
    assert payload["parent"] is not None


def test_cert_picker_lists_p12(live, tmp_path: Path) -> None:
    _state, base = live
    client = Client(base)
    client.get("/")
    folder = tmp_path / "certs"
    folder.mkdir()
    (folder / "signer.p12").write_bytes(b"data")
    (folder / "readme.txt").write_text("x", encoding="utf-8")
    status, body, _headers = client.post("/api/fs/list", {"path": str(folder), "kinds": ["cert"]})
    names = {entry["name"] for entry in _json(body)["entries"]}
    assert names == {"signer.p12"}


def test_upload_stages_a_dropped_file(live, tmp_path: Path) -> None:
    state, base = live
    client = Client(base)
    client.get("/")
    status, body, _headers = client.put("/api/upload?name=dropped.pdf", b"%PDF-1.4\n")
    assert status == 200
    payload = _json(body)
    assert payload["added"] == 1
    staged = Path(payload["staged"])
    assert staged.is_file()
    assert staged.name.endswith("dropped.pdf")
    assert "staging" in str(staged)


def test_preset_keeps_output_destination(live, tmp_path: Path) -> None:
    _state, base = live
    client = Client(base)
    client.get("/")
    client.post(
        "/api/config",
        {"config": {"output_mode": "folder", "output_dir": str(tmp_path / "out")}},
    )
    status, body, _headers = client.post("/api/preset", {"preset": "max"})
    config = _json(body)["config"]
    assert config["preset"] == "max"
    assert config["target_dpi"] == 110
    assert config["output_mode"] == "folder"
    assert config["output_dir"] == str(tmp_path / "out")


def test_open_missing_output_is_reported_not_raised(live) -> None:
    _state, base = live
    client = Client(base)
    client.get("/")
    status, body, _headers = client.post("/api/open", {"kind": "folder"})
    assert status == 200
    assert _json(body)["ok"] is False


def test_incremental_queue_after_a_run(live, assets, tmp_path: Path) -> None:
    """US-026: adding after a run marks only the new file as pending."""
    state, base = live
    client = Client(base)
    client.get("/")
    client.post(
        "/api/config",
        {"config": {"output_mode": "folder", "output_dir": str(tmp_path / "out")}},
    )
    client.post("/api/queue/add", {"paths": [str(assets.ui)]})
    status, body, _headers = client.get("/api/bootstrap")
    config = _json(body)["config"]
    client.post("/api/run/start", {"config": config})
    for _ in range(120):
        status, body, _headers = client.get("/api/run")
        if not _json(body)["running"]:
            break
        time.sleep(0.2)

    status, body, _headers = client.post("/api/queue/add", {"paths": [str(assets.tiny)]})
    queue = _json(body)["queue"]
    statuses = {item["name"]: item["status"] for item in queue}
    assert statuses[assets.ui.name] == "done"
    assert statuses[assets.tiny.name] == "new_queued"
    # A second run only dispatches the pending file.
    status, body, _headers = client.get("/api/bootstrap")
    status, body, _headers = client.post(
        "/api/run/start", {"config": _json(body)["config"]}
    )
    assert _json(body)["run"]["total"] == 1


def test_remove_row_clears_it(live, assets) -> None:
    _state, base = live
    client = Client(base)
    client.get("/")
    status, body, _headers = client.post("/api/queue/add", {"paths": [str(assets.tiny)]})
    item = _json(body)["queue"][0]
    status, body, _headers = client.post("/api/queue/remove", {"id": item["id"]})
    payload = _json(body)
    assert payload["ok"] is True
    assert payload["queue"] == []


def test_preload_paths_are_queued_on_first_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, assets) -> None:
    monkeypatch.setattr(settings, "settings_dir", lambda: tmp_path / "config")
    server, state = create_server(port=0, preload=[str(assets.ui)])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = Client(f"http://127.0.0.1:{server.server_address[1]}")
        client.get("/")
        status, body, _headers = client.get("/api/bootstrap")
        assert status == 200
        assert len(_json(body)["queue"]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        state.thumbnail_pool.shutdown(wait=False)


def test_queue_reports_protection_kind(live, assets) -> None:
    """Permission-only files are unlocked for free; open passwords are flagged."""
    _state, base = live
    client = Client(base)
    client.get("/")
    status, body, _headers = client.post(
        "/api/queue/add", {"paths": [str(assets.signed), str(assets.protected)]}
    )
    queue = _json(body)["queue"]
    by_name = {item["name"]: item for item in queue}
    restricted = by_name[assets.signed.name]
    locked = by_name[assets.protected.name]
    assert restricted["protection"] == "permissions"
    assert restricted["locked"] is False
    assert locked["protection"] == "password"
    assert locked["locked"] is True


def test_inspector_renders_protected_pages_after_a_password(live, assets) -> None:
    """The preview keeps the row's password in memory; N-13's dark canvas fix."""
    _state, base = live
    client = Client(base)
    client.get("/")
    _status, body, _headers = client.post(
        "/api/queue/add", {"paths": [str(assets.protected)], "password": "secret"}
    )
    item = _json(body)["queue"][0]
    status, page, headers = client.get(f"/api/page?id={item['id']}&side=orig&page=1&zoom=200")
    assert status == 200
    assert page.startswith(b"\x89PNG")
    assert headers["Content-Type"] == "image/png"
    # The thumbnail is rendered off-thread with the same credential.
    for _ in range(100):
        _status, body, _headers = client.get("/api/bootstrap")
        if _json(body)["queue"][0]["has_thumb"]:
            break
        time.sleep(0.05)
    assert _json(body)["queue"][0]["has_thumb"] is True


def test_ui_language_switch(live) -> None:
    state, base = live
    client = Client(base)
    client.get("/")
    status, body, _headers = client.post("/api/ui", {"language": "en", "dark_mode": False})
    payload = _json(body)
    assert status == 200
    assert payload["language"] == "en"
    assert payload["settings"]["dark_mode"] is False

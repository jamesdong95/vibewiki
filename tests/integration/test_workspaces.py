from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from vibewiki.build import build_repository
from vibewiki.scan import scan_repository
from vibewiki.serve import create_server


def _json_request(base: str, path: str, payload: dict | None = None) -> dict:
    request = Request(
        f"{base}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    with urlopen(request) as response:
        return json.load(response)


def test_imported_workspace_survives_restart_and_refreshes_in_place(
    tmp_path: Path,
) -> None:
    initial = tmp_path / "initial"
    (initial / "src").mkdir(parents=True)
    (initial / "src/main.js").write_text(
        "export function start() { return true; }\n", encoding="utf-8"
    )
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src/main.js").write_text(
        "export function start() { return true; }\n", encoding="utf-8"
    )
    state = tmp_path / "state"
    scan_repository(initial)
    build_repository(initial)
    server = create_server(initial, port=0, state_dir=state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        imported = _json_request(base, "/api/import-path", {"path": str(source)})
        workspace_id = imported["import_source"]["workspace_id"]
        assert imported["import_source"]["persistence"] == "saved-snapshot"
        assert source.is_dir()
        assert _json_request(base, "/api/workspaces")["workspaces"][0]["id"] == (
            workspace_id
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    reopened = create_server(None, port=0, state_dir=state)
    reopened_thread = threading.Thread(target=reopened.serve_forever, daemon=True)
    reopened_thread.start()
    reopened_base = f"http://127.0.0.1:{reopened.server_address[1]}"
    try:
        summary = _json_request(reopened_base, "/api/summary")
        assert summary["source"]["workspace_id"] == workspace_id
        assert summary["counts"]["scanned_files"] == 1

        (source / "src/extra.js").write_text(
            "export const extra = true;\n", encoding="utf-8"
        )
        refreshed = _json_request(
            reopened_base, "/api/workspaces/refresh", {"id": workspace_id}
        )
        assert refreshed["refreshed"] is True
        assert refreshed["workspace_id"] == workspace_id
        assert refreshed["counts"]["scanned_files"] == 2
        listed = _json_request(reopened_base, "/api/workspaces")["workspaces"]
        assert [item["id"] for item in listed] == [workspace_id]

        delete_request = Request(
            f"{reopened_base}/api/workspaces/{workspace_id}", method="DELETE"
        )
        try:
            urlopen(delete_request)
        except Exception as error:
            assert getattr(error, "code", None) == 422
        else:
            raise AssertionError("active workspace should not be forgotten")
    finally:
        reopened.shutdown()
        reopened.server_close()
        reopened_thread.join(timeout=2)


def test_serve_without_repository_exposes_first_run_browse_flow(tmp_path: Path) -> None:
    source = tmp_path / "first-project"
    source.mkdir()
    (source / "index.js").write_text(
        "export function main() { return 'ok'; }\n", encoding="utf-8"
    )
    server = create_server(None, port=0, state_dir=tmp_path / "state")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        onboarding = _json_request(base, "/api/summary")
        assert onboarding["status"] == "onboarding"
        assert onboarding["workspace_available"] is False
        assert onboarding["capabilities"] == {
            "browse": True,
            "local_path": True,
            "github": True,
        }
        assert _json_request(base, "/api/workspaces")["workspaces"] == []
        imported = _json_request(base, "/api/import-path", {"path": str(source)})
        assert imported["import_source"]["selected_files"] == 1
        ready = _json_request(base, "/api/summary")
        assert ready["status"] == "ready"
        assert ready["counts"]["scanned_files"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

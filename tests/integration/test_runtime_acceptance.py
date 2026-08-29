from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vibewiki.build import build_repository
from vibewiki.errors import VibeWikiError
from vibewiki.observe import observe_repository
from vibewiki.scan import scan_repository


@pytest.mark.runtime
def test_browser_observer_links_real_fixture_runtime_to_static_graph(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    source = Path(__file__).parents[1] / "fixtures" / "runtime-browser-demo"
    root = tmp_path / "runtime-browser-demo"
    shutil.copytree(source, root)
    scan_repository(root, allow_generic=True)
    build_repository(root)

    process = subprocess.Popen(
        [sys.executable, "serve_fixture.py", "--port", "0"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        ready = process.stdout.readline().strip()
        assert ready.startswith("READY "), ready
        port = int(ready.split()[1])
        try:
            result = observe_repository(
                root,
                f"http://127.0.0.1:{port}/",
                mode="browser",
                max_routes=2,
            )
        except VibeWikiError as error:
            if "Chromium" in error.message:
                pytest.skip(error.message)
            raise
    finally:
        process.terminate()
        process.wait(timeout=5)

    runtime = json.loads((root / ".vibewiki/runtime.json").read_text())
    assert result["mode"] == "browser"
    assert {item["path"] for item in runtime["routes"]} == {"/", "/dashboard"}
    health = next(
        item for item in runtime["network"] if "/api/health" in item["url"]
    )
    assert health["status"] == 200
    assert "route:generic:server.js:GET:/api/health" in health["graph_nodes"]
    assert "api_call:app.js:/api/health" in health["graph_nodes"]
    assert any(
        item["text"] == "runtime fixture console error"
        for item in runtime["console"]
    )
    assert runtime["linked_nodes"] >= 3

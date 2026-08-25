from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from vibewiki.build import build_repository
from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.importer import _multipart_files
from vibewiki.scan import scan_repository


def _multipart(
    files: dict[str, str], boundary: str = "----vibewiki-generic"
) -> tuple[str, bytes]:
    parts = []
    for path, content in files.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{path}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            f"{content}\r\n"
        )
    body = ("".join(parts) + f"--{boundary}--\r\n").encode()
    return f"multipart/form-data; boundary={boundary}", body


def test_generic_javascript_repository_builds_without_app(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/main.js").write_text("export function start() { return true; }\n")
    (tmp_path / "src/App.jsx").write_text(
        "export default function App() { return null; }\n"
    )
    (tmp_path / "src/module.mjs").write_text("export function helper() { return 1; }\n")
    (tmp_path / "src/legacy.cjs").write_text("function legacy() { return 0; }\n")
    (tmp_path / "tests/app.spec.js").write_text("describe('app', () => {});\n")

    result = scan_repository(tmp_path, allow_generic=True)
    built = build_repository(tmp_path)
    facts = json.loads((tmp_path / ".vibewiki/facts.json").read_text())

    assert result["counts"]["scanned_files"] == 5
    assert built["counts"]["facts"] == 5
    assert any(
        item["semantic_key"] == "function:src/main.js:start" for item in facts["facts"]
    )
    assert any(
        item["semantic_key"] == "function:src/legacy.cjs:legacy"
        for item in facts["facts"]
    )
    assert any(
        item["semantic_key"] == "test:tests/app.spec.js" for item in facts["facts"]
    )


def test_vite_react_fixture_emits_router_objects_and_api_wrapper_edges(
    tmp_path: Path,
) -> None:
    source = Path(__file__).parents[1] / "fixtures" / "vite-react-demo"
    root = tmp_path / "vite-react-demo"
    shutil.copytree(source, root)
    scan_repository(root, allow_generic=True)
    build_repository(root)
    artifact = json.loads((root / ".vibewiki/graph.json").read_text())

    route_keys = {
        item["semantic_key"] for item in artifact["facts"] if item["kind"] == "route"
    }
    assert "route:generic:src/router.jsx:GET:/settings" in route_keys
    assert "api_call:src/App.jsx:/api/health" in {
        item["semantic_key"]
        for item in artifact["facts"]
        if item["kind"] == "api_call"
    }
    assert any(
        edge["source"] == "api_call:src/App.jsx:/api/health"
        and edge["target"] == "route:generic:server.js:GET:/api/health"
        for edge in artifact["relations"]
    )


def test_next_pages_fixture_emits_page_and_api_routes(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "fixtures" / "next-pages-demo"
    root = tmp_path / "next-pages-demo"
    shutil.copytree(source, root)
    scan_repository(root, allow_generic=True)
    build_repository(root)
    artifact = json.loads((root / ".vibewiki/graph.json").read_text())

    routes = {
        item["semantic_key"]: item
        for item in artifact["facts"]
        if item["kind"] == "route"
    }
    assert "route:next_pages:pages/index.tsx:GET:/" in routes
    assert "route:next_pages:pages/account.jsx:GET:/account" in routes
    assert "route:next_pages:pages/api/users.js:ANY:/api/users" in routes
    assert routes["route:next_pages:pages/api/users.js:ANY:/api/users"][
        "attributes"
    ]["methods"] == []
    assert any(
        edge["source"] == "api_call:src/client.ts:/api/users"
        and edge["target"] == "route:next_pages:pages/api/users.js:ANY:/api/users"
        and edge["relation"] == "calls"
        for edge in artifact["relations"]
    )


def test_generic_multi_language_repository_keeps_files_and_symbols(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("def main():\n    return True\n")
    (tmp_path / "src/main.go").write_text("package main\nfunc Run() {}\n")
    (tmp_path / "src/lib.rs").write_text("fn compute() -> i32 { 1 }\n")
    (tmp_path / "src/Widget.java").write_text("public class Widget {}\n")
    (tmp_path / "README.md").write_text("documentation is inventory evidence\n")
    (tmp_path / "config.yaml").write_text("mode: local\n")

    result = scan_repository(tmp_path, allow_generic=True)
    build_repository(tmp_path)
    facts = json.loads((tmp_path / ".vibewiki/facts.json").read_text())
    graph = json.loads((tmp_path / ".vibewiki/graph.json").read_text())

    assert result["counts"]["scanned_files"] == 6
    assert {
        item["semantic_key"] for item in facts["facts"] if item["kind"] == "function"
    } == {
        "function:src/main.go:Run",
        "function:src/main.py:main",
        "function:src/lib.rs:compute",
    }
    assert any(
        item["id"] == "symbol:src/Widget.java:Widget"
        and item["attributes"]["symbol_kind"] == "class"
        for item in graph["symbols"]
    )
    inventory_paths = {item["path"] for item in graph["inventory"]["files"]}
    assert {"README.md", "config.yaml"}.issubset(inventory_paths)


def test_generic_framework_routes_and_api_calls_build_reverse_graph(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "server.js").write_text(
        "const express = require('express');\n"
        "const app = express();\n"
        "app.get('/health', health);\n"
        "app.post('/api/items', createItem);\n"
        "function health(req, res) { res.send('ok'); }\n"
        "function createItem(req, res) { res.json({ok: true}); }\n"
    )
    (tmp_path / "src/router.jsx").write_text(
        "import { Route } from 'react-router-dom';\n"
        "import { createBrowserRouter } from 'react-router-dom';\n"
        "const router = createBrowserRouter([\n"
        "  { path: '/settings', element: <div /> }\n"
        "]);\n"
        "export function RouterView() { return "
        "<Route path='/dashboard' element={<div />} />; }\n"
    )
    (tmp_path / "src/client.js").write_text(
        "export async function loadItems() {\n"
        "  const health = await apiClient.get('/health');\n"
        "  return fetch('/api/items');\n"
        "}\n"
    )
    (tmp_path / "api.py").write_text(
        "@app.get('/users')\n"
        "def users():\n"
        "    return []\n"
    )
    (tmp_path / "main.go").write_text(
        'package main\nimport "net/http"\n'
        'func main() { http.HandleFunc("/healthz", health) }\n'
    )

    scan_repository(tmp_path, allow_generic=True)
    build_repository(tmp_path)
    artifact = json.loads((tmp_path / ".vibewiki/graph.json").read_text())

    route_keys = {
        item["semantic_key"] for item in artifact["facts"] if item["kind"] == "route"
    }
    api_keys = {
        item["semantic_key"]
        for item in artifact["facts"]
        if item["kind"] == "api_call"
    }
    assert "route:generic:server.js:GET:/health" in route_keys
    assert "route:generic:server.js:POST:/api/items" in route_keys
    assert "route:generic:src/router.jsx:GET:/dashboard" in route_keys
    assert "route:generic:src/router.jsx:GET:/settings" in route_keys
    assert "route:generic:api.py:GET:/users" in route_keys
    assert "route:generic:main.go:ANY:/healthz" in route_keys
    assert "api_call:src/client.js:/api/items" in api_keys
    assert "api_call:src/client.js:/health" in api_keys
    assert any(
        edge["source"] == "route:generic:server.js:GET:/health"
        and edge["target"] == "function:server.js:health"
        and edge["relation"] == "calls"
        for edge in artifact["relations"]
    )
    assert any(
        edge["source"] == "api_call:src/client.js:/health"
        and edge["target"] == "route:generic:server.js:GET:/health"
        and edge["relation"] == "calls"
        for edge in artifact["relations"]
    )


def test_importer_selects_nested_web_package_deterministically() -> None:
    content_type, body = _multipart(
        {
            "repo/packages/ui/src/Button.jsx": "export default function Button() {}\n",
            "repo/packages/web/app/page.js": "export default function Home() {}\n",
            "repo/packages/web/src/main.js": "export function start() {}\n",
        }
    )

    selected = _multipart_files(content_type, body)

    assert [item[1] for item in selected] == ["app/page.js", "src/main.js"]


def test_importer_rejects_unsupported_source_with_actionable_error() -> None:
    content_type, body = _multipart({"repo/image.png": "not source\n"})

    with pytest.raises(VibeWikiError) as raised:
        _multipart_files(content_type, body)

    assert raised.value.code is ErrorCode.UNSUPPORTED_STACK
    assert "supported source" in raised.value.message


def test_importer_reports_file_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    content_type, body = _multipart({"repo/src/main.js": "export function main() {}\n"})
    monkeypatch.setattr("vibewiki.importer.MAX_IMPORT_FILES", 0)

    with pytest.raises(VibeWikiError, match="too many supported files"):
        _multipart_files(content_type, body)


def test_importer_reports_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    content_type, body = _multipart({"repo/src/main.js": "export function main() {}\n"})
    monkeypatch.setattr("vibewiki.importer.MAX_IMPORT_BYTES", 1)

    with pytest.raises(VibeWikiError, match="byte limit"):
        _multipart_files(content_type, body)


def test_importer_reports_multipart_part_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    content_type, body = _multipart({"repo/src/main.js": "export function main() {}\n"})
    monkeypatch.setattr("vibewiki.importer.MAX_MULTIPART_PARTS", 0)

    with pytest.raises(VibeWikiError, match="multipart parts"):
        _multipart_files(content_type, body)

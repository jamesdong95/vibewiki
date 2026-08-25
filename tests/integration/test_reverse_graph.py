from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibewiki.build import build_repository
from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.scan import scan_repository
from vibewiki.serve import api_payload


def test_build_emits_repository_inventory_and_reverse_module_edges(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src/main.js").write_text(
        "import { helper } from './helper.js';\n"
        "export function main() { return helper(); }\n"
    )
    (tmp_path / "src/helper.js").write_text("export function helper() { return 1; }\n")
    (tmp_path / "docs/README.md").write_text("kept as text inventory evidence\n")
    (tmp_path / "assets.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / ".env.local").write_text("SECRET=never read\n")

    scan_repository(tmp_path, allow_generic=True)
    build_repository(tmp_path)
    graph = json.loads((tmp_path / ".vibewiki/graph.json").read_text())

    inventory_paths = {item["path"] for item in graph["inventory"]["files"]}
    assert inventory_paths == {
        "assets.bin",
        "docs/README.md",
        "src/helper.js",
        "src/main.js",
    }
    assert {
        (edge["source"], edge["target"])
        for edge in graph["module_edges"]
    } == {("module:src/main.js", "module:src/helper.js")}
    assert {
        (edge["source"], edge["target"])
        for edge in graph["symbol_edges"]
        if edge["relation"] == "calls"
    } == {("symbol:src/main.js:main", "symbol:src/helper.js:helper")}


def test_build_groups_nested_package_manifests(tmp_path: Path) -> None:
    (tmp_path / "packages/web/src").mkdir(parents=True)
    (tmp_path / "packages/api/src").mkdir(parents=True)
    (tmp_path / "packages/web/package.json").write_text(
        '{"name":"@demo/web","private":true}\n'
    )
    (tmp_path / "packages/api/package.json").write_text(
        '{"name":"@demo/api","private":true}\n'
    )
    (tmp_path / "packages/web/src/app.jsx").write_text(
        "export const App = () => null;\n"
    )
    (tmp_path / "packages/api/src/main.js").write_text(
        "export function main() { return true; }\n"
    )

    scan_repository(tmp_path, allow_generic=True)
    build_repository(tmp_path)
    graph = json.loads((tmp_path / ".vibewiki/graph.json").read_text())

    assert [item["id"] for item in graph["packages"]] == [
        "package:packages/api",
        "package:packages/web",
    ]
    contains = {
        (edge["source"], edge["target"])
        for edge in graph["package_edges"]
        if edge["relation"] == "contains"
    }
    assert ("package:packages/web", "module:packages/web/src/app.jsx") in contains
    assert any(
        item["id"] == "symbol:packages/web/src/app.jsx:App"
        for item in graph["symbols"]
    )


def test_source_api_returns_bounded_lines_and_rejects_traversal(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.js").write_text("one\ntwo\nthree\nfour\n")
    scan_repository(tmp_path, allow_generic=True)
    build_repository(tmp_path)
    graph = json.loads((tmp_path / ".vibewiki/graph.json").read_text())

    source = api_payload(
        tmp_path,
        graph,
        "/api/source",
        params={"path": ["src/main.js"], "start": ["2"], "end": ["3"]},
    )
    assert [line["text"] for line in source["lines"]] == ["two", "three"]

    with pytest.raises(VibeWikiError) as rejected:
        api_payload(
            tmp_path,
            graph,
            "/api/source",
            params={"path": ["../src/main.js"]},
        )
    assert rejected.value.code is ErrorCode.PATH_NOT_FOUND


def test_impact_api_returns_bounded_upstream_and_downstream_evidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.js").write_text(
        "import { helper } from './helper.js';\n"
        "export function main() { return helper(); }\n"
    )
    (tmp_path / "src/helper.js").write_text(
        "export function helper() { return 1; }\n"
    )
    scan_repository(tmp_path, allow_generic=True)
    build_repository(tmp_path)
    graph = json.loads((tmp_path / ".vibewiki/graph.json").read_text())

    helper_id = "symbol:src/helper.js:helper"
    upstream = api_payload(
        tmp_path,
        graph,
        "/api/impact",
        params={"subject": [helper_id], "direction": ["upstream"]},
    )
    downstream = api_payload(
        tmp_path,
        graph,
        "/api/impact",
        params={"subject": [helper_id], "direction": ["downstream"]},
    )

    assert upstream["node"]["id"] == helper_id
    assert any(
        item["node"]["id"] == "symbol:src/main.js:main"
        and item["direction"] == "upstream"
        for item in upstream["nodes"]
    )
    assert downstream["nodes"] == []
    assert upstream["counts"]["nodes"] <= 100

    with pytest.raises(VibeWikiError, match="direction"):
        api_payload(
            tmp_path,
            graph,
            "/api/impact",
            params={"subject": [helper_id], "direction": ["sideways"]},
        )

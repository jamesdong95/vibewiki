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

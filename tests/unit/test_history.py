from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.history import (
    MAX_SOURCE_DIFF_CHARS_PER_FILE,
    MAX_SOURCE_DIFF_FILES,
    MAX_SOURCE_DIFF_LINES_PER_FILE,
    MAX_SOURCE_SNAPSHOT_FILE_BYTES,
    graph_diff,
    graph_index,
    history_for_subject,
    load_source_diff_detail,
    manifest_diff,
    record_scan,
    record_source_diff,
    record_source_snapshot,
    stale_files,
)


def _manifest(*items: tuple[str, str]) -> dict:
    return {
        "analyzer_version": "test",
        "files": [
            {"language": "typescript", "path": path, "sha256": digest, "size": 1}
            for path, digest in items
        ],
        "schema_version": 1,
    }


def test_manifest_diff_is_sorted_and_classifies_hash_changes() -> None:
    before = _manifest(("app/a.ts", "a"), ("app/removed.ts", "r"))
    after = _manifest(("app/a.ts", "b"), ("app/new.ts", "n"))

    assert manifest_diff(before, after) == {
        "added": ["app/new.ts"],
        "changed": ["app/a.ts"],
        "removed": ["app/removed.ts"],
    }


def test_graph_diff_reports_added_changed_and_removed_nodes_and_edges() -> None:
    before = graph_index(
        {
            "facts": [
                {
                    "attributes": {"path": "/"},
                    "evidence": [{"path": "app/page.tsx"}],
                    "kind": "route",
                    "semantic_key": "route:page:/",
                    "status": "verified",
                },
                {
                    "attributes": {"name": "old"},
                    "evidence": [{"path": "app/old.ts"}],
                    "kind": "function",
                    "semantic_key": "function:old",
                    "status": "verified",
                },
            ],
            "relations": [
                {
                    "evidence": [{"path": "app/page.tsx"}],
                    "relation": "calls",
                    "source": "route:page:/",
                    "status": "verified",
                    "target": "function:old",
                }
            ],
        },
        "run-1",
    )
    after = graph_index(
        {
            "facts": [
                {
                    "attributes": {"path": "/new"},
                    "evidence": [{"path": "app/page.tsx"}],
                    "kind": "route",
                    "semantic_key": "route:page:/",
                    "status": "verified",
                },
                {
                    "attributes": {"name": "new"},
                    "evidence": [{"path": "app/new.ts"}],
                    "kind": "function",
                    "semantic_key": "function:new",
                    "status": "verified",
                },
            ],
            "relations": [
                {
                    "evidence": [{"path": "app/page.tsx"}],
                    "relation": "calls",
                    "source": "route:page:/",
                    "status": "verified",
                    "target": "function:new",
                }
            ],
        },
        "run-2",
    )

    result = graph_diff(before, after, from_run_id="run-1", to_run_id="run-2")

    assert result["status"] == "changed"
    assert result["from_run_id"] == "run-1"
    assert result["to_run_id"] == "run-2"
    assert result["counts"] == {
        "nodes_added": 1,
        "nodes_changed": 1,
        "nodes_removed": 1,
        "edges_added": 1,
        "edges_changed": 0,
        "edges_removed": 1,
    }
    assert result["nodes_changed"][0]["after"]["id"] == "route:page:/"
    assert result["nodes_removed"][0]["id"] == "function:old"


def test_record_scan_keeps_bounded_runs_and_subject_history(tmp_path: Path) -> None:
    first = _manifest(("app/page.tsx", "a"))
    second = _manifest(("app/page.tsx", "b"), ("app/new.ts", "n"))
    record_scan(tmp_path, first, None)
    record_scan(tmp_path, second, first)
    (tmp_path / ".vibewiki/graph.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "semantic_key": "route:page:/",
                        "evidence": [{"path": "app/page.tsx"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = history_for_subject(tmp_path, "route:page:/")

    assert result["paths"] == ["app/page.tsx", "route:page:/"]
    assert len(result["runs"]) == 2
    assert result["runs"][0]["changes"] == {
        "added": ["app/new.ts"],
        "changed": ["app/page.tsx"],
        "removed": [],
    }


def test_stale_files_detect_changed_and_removed_sources(tmp_path: Path) -> None:
    changed = tmp_path / "app/page.tsx"
    changed.parent.mkdir()
    changed.write_text("changed\n", encoding="utf-8")
    artifact = {
        "inventory": {
            "files": [
                {"path": "app/page.tsx", "sha256": "0" * 64},
                {"path": "app/removed.ts", "sha256": "1" * 64},
            ]
        }
    }

    result = stale_files(tmp_path, artifact)

    assert result == [
        {
            "path": "app/page.tsx",
            "reason": "source file changed after the last build",
            "status": "changed",
        },
        {
            "path": "app/removed.ts",
            "reason": "source file was removed after the last build",
            "status": "removed",
        },
    ]


def test_source_diff_captures_changed_added_and_removed_lines(tmp_path: Path) -> None:
    changed = tmp_path / "src/main.js"
    added = tmp_path / "src/new.jsx"
    removed = tmp_path / "src/removed.ts"
    changed.parent.mkdir()
    changed.write_text("const value = 1;\nkeep();\n", encoding="utf-8")
    removed.write_text("export const old = true;\n", encoding="utf-8")
    before = _manifest(
        ("src/main.js", "a"),
        ("src/removed.ts", "r"),
    )
    record_source_snapshot(tmp_path, before)

    changed.write_text("const value = 2;\nkeep();\n", encoding="utf-8")
    added.write_text("export const created = true;\n", encoding="utf-8")
    removed.unlink()
    after = _manifest(
        ("src/main.js", "b"),
        ("src/new.jsx", "n"),
    )

    payload = record_source_diff(tmp_path, before, after)

    assert payload["status"] == "available"
    assert payload["counts"] == {
        "added": 1,
        "available": 3,
        "changed": 1,
        "files": 3,
        "removed": 1,
        "unavailable": 0,
    }
    by_path = {item["path"]: item for item in payload["files"]}
    assert {item["status"] for item in by_path.values()} == {
        "added",
        "changed",
        "removed",
    }
    changed_lines = [
        line
        for hunk in by_path["src/main.js"]["hunks"]
        for line in hunk["lines"]
    ]
    assert [
        (line["kind"], line["old_number"], line["new_number"])
        for line in changed_lines
    ] == [
        ("removed", 1, None),
        ("added", None, 1),
        ("context", 2, 2),
    ]
    assert load_source_diff_detail(tmp_path, "src/main.js")["file"] == by_path[
        "src/main.js"
    ]


def test_source_diff_reports_invalid_utf8_binary_and_snapshot_bounds(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "src/valid.js"
    invalid = tmp_path / "src/invalid.js"
    binary = tmp_path / "src/image.js"
    large = tmp_path / "src/large.js"
    valid.parent.mkdir()
    valid.write_text("export const valid = true;\n", encoding="utf-8")
    before = _manifest(("src/valid.js", "a"))
    record_source_snapshot(tmp_path, before)

    invalid.write_bytes(b"\xff\xfe")
    binary.write_bytes(b"header\x00payload")
    large.write_bytes(b"x" * (MAX_SOURCE_SNAPSHOT_FILE_BYTES + 1))
    after = _manifest(
        ("src/valid.js", "a"),
        ("src/invalid.js", "i"),
        ("src/image.js", "b"),
        ("src/large.js", "l"),
    )
    payload = record_source_diff(tmp_path, before, after)

    by_path = {item["path"]: item for item in payload["files"]}
    assert payload["status"] == "unavailable"
    assert by_path["src/invalid.js"]["reason"] == "source file is not valid UTF-8"
    assert by_path["src/image.js"]["reason"] == "source file is binary"
    assert "snapshot limit" in by_path["src/large.js"]["reason"]

    many = {"files": []}
    for index in range(MAX_SOURCE_DIFF_FILES + 1):
        path = f"src/file-{index:03d}.js"
        file_path = tmp_path / path
        file_path.write_text(f"export const item{index} = {index};\n", encoding="utf-8")
        many["files"].append(
            {"language": "javascript", "path": path, "sha256": str(index), "size": 1}
        )
    record_source_snapshot(tmp_path, many)
    snapshot_index = json.loads(
        (tmp_path / ".vibewiki/source-snapshot/index.json").read_text(encoding="utf-8")
    )
    assert len(snapshot_index["files"]) <= MAX_SOURCE_DIFF_FILES
    assert snapshot_index["truncated"] is True


def test_source_diff_bounds_hunks_and_rejects_non_whitelisted_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/large.js"
    source.parent.mkdir()
    source.write_text("old\n" * (MAX_SOURCE_DIFF_LINES_PER_FILE + 20), encoding="utf-8")
    before = _manifest(("src/large.js", "a"))
    record_source_snapshot(tmp_path, before)
    source.write_text("new\n" * (MAX_SOURCE_DIFF_LINES_PER_FILE + 20), encoding="utf-8")
    after = _manifest(("src/large.js", "b"))
    payload = record_source_diff(tmp_path, before, after)
    item = payload["files"][0]

    assert item["truncated"] is True
    assert item["diff_lines"] <= MAX_SOURCE_DIFF_LINES_PER_FILE
    assert sum(
        len(line["text"])
        for hunk in item["hunks"]
        for line in hunk["lines"]
    ) <= MAX_SOURCE_DIFF_CHARS_PER_FILE
    with pytest.raises(VibeWikiError) as raised:
        load_source_diff_detail(tmp_path, "../secret.js")
    assert raised.value.code is ErrorCode.PATH_NOT_FOUND
    with pytest.raises(VibeWikiError, match="not found"):
        load_source_diff_detail(tmp_path, "src/other.js")

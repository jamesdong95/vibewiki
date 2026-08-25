from __future__ import annotations

import json
from pathlib import Path

from vibewiki.history import (
    graph_diff,
    graph_index,
    history_for_subject,
    manifest_diff,
    record_scan,
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

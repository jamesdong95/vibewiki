from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibewiki.discussions import DiscussionStore, artifact_fingerprint
from vibewiki.errors import ErrorCode, VibeWikiError


def _store(tmp_path: Path) -> DiscussionStore:
    return DiscussionStore(tmp_path / "state", "ws_demo123")


def test_discussion_store_is_bounded_atomic_and_restart_safe(tmp_path: Path) -> None:
    store = _store(tmp_path)
    thread = store.create("Checkout review")
    saved = store.append(
        thread["id"],
        question="How does checkout work?",
        answer="## Answer\n\nIt calls the API.\n\n[app/page.tsx:4]",
        fingerprint="a" * 64,
    )
    store.feedback(
        thread["id"],
        message_id=saved["message_id"],
        rating="down",
        note="Needs more evidence",
        citation={"path": "app/page.tsx", "line_start": 4, "line_end": 5},
    )

    reopened = DiscussionStore(tmp_path / "state", "ws_demo123")
    listed = reopened.list("a" * 64)
    assert listed[0]["messages"][1]["content"].startswith("## Answer")
    assert listed[0]["feedback"][0]["citation"] == {
        "path": "app/page.tsx",
        "line_start": 4,
        "line_end": 5,
    }
    assert "api_key" not in reopened.path.read_text(encoding="utf-8")
    assert "Bearer" not in reopened.path.read_text(encoding="utf-8")


def test_stale_context_requires_explicit_confirmation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    thread = store.create()
    store.append(
        thread["id"],
        question="What is this?",
        answer="Unknown",
        fingerprint="old",
    )

    listed = store.list("new")
    assert listed[0]["stale"] is True
    with pytest.raises(VibeWikiError) as raised:
        store.history(thread["id"], "new")
    assert raised.value.code is ErrorCode.INVALID_OUTPUT
    history, stale = store.history(thread["id"], "new", allow_stale=True)
    assert stale is True
    assert history[0]["role"] == "user"


def test_corrupt_or_cross_scope_state_fails_closed(tmp_path: Path) -> None:
    state = tmp_path / "state" / "discussions"
    state.mkdir(parents=True)
    path = state / "ws_demo123.json"
    path.write_text("not json", encoding="utf-8")
    assert DiscussionStore(tmp_path / "state", "ws_demo123").list("x") == []

    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workspace_id": "ws_other",
                "threads": [],
            }
        ),
        encoding="utf-8",
    )
    assert DiscussionStore(tmp_path / "state", "ws_demo123").list("x") == []


def test_artifact_fingerprint_changes_with_manifest_only(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = root / ".vibewiki"
    output.mkdir(parents=True)
    (output / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "files": [{"path": "app.tsx"}]}),
        encoding="utf-8",
    )
    first = artifact_fingerprint(root)
    (output / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "files": [{"path": "other.tsx"}]}),
        encoding="utf-8",
    )
    assert artifact_fingerprint(root) != first

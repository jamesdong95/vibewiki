from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.workspaces import WorkspaceStore


def test_workspace_store_persists_sanitized_records_and_forgets_only_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.js").write_text("export const app = true;\n", encoding="utf-8")
    state = tmp_path / "state"

    store = WorkspaceStore(state)
    record, snapshot = store.save_snapshot(
        source,
        label="  Demo   app ",
        provider="local-path",
        origin={"local_path": str(source), "secret": "never-public"},
    )
    assert record.label == "Demo app"
    assert snapshot.is_dir()
    assert (snapshot / "app.js").read_text(encoding="utf-8") == (
        "export const app = true;\n"
    )
    public = store.public()
    assert public[0]["id"] == record.id
    assert public[0]["label"] == "Demo app"
    assert "origin" not in public[0]
    assert str(source) not in json.dumps(public)

    reopened = WorkspaceStore(state)
    reopened_record, reopened_snapshot = reopened.get(record.id)
    assert reopened_record.id == record.id
    assert reopened_snapshot == snapshot

    reopened.forget(record.id)
    assert not snapshot.parent.exists()
    assert source.is_dir()
    assert not reopened.public()


def test_workspace_store_ignores_corrupt_registry_and_rejects_external_snapshot(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    store = WorkspaceStore(state)
    store.registry_path.write_text("{not-json", encoding="utf-8")
    assert store.public() == []

    store.registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workspaces": [
                    {
                        "id": "ws_external",
                        "label": "unsafe",
                        "provider": "local-path",
                        "snapshot_root": "../outside",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert store.public() == []
    with pytest.raises(VibeWikiError) as raised:
        store.get("ws_external")
    assert raised.value.code is ErrorCode.PATH_NOT_FOUND


def test_workspace_store_persists_only_non_secret_llm_preferences(
    tmp_path: Path,
) -> None:
    store = WorkspaceStore(tmp_path / "state")
    store.save_llm_preferences(
        provider="openai-compatible",
        model="minimaxai/minimax-m3",
        base_url="https://api.example.test/v1",
    )
    reopened = WorkspaceStore(tmp_path / "state")
    assert reopened.load_llm_preferences() == {
        "provider": "openai-compatible",
        "model": "minimaxai/minimax-m3",
        "base_url": "https://api.example.test/v1",
    }
    assert "api_key" not in reopened.llm_preferences_path.read_text()

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from vibewiki.build import build_repository
from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.importer import _multipart_files, cleanup_workspace
from vibewiki.scan import scan_repository
from vibewiki.serve import create_server


def _fixture_copy(tmp_path: Path) -> Path:
    source = Path(__file__).parents[1] / "fixtures" / "next-ts-demo"
    target = tmp_path / "next-ts-demo"
    shutil.copytree(source, target)
    return target


def test_build_matches_golden_facts_and_is_repeatable(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    scan_repository(root)

    first = build_repository(root)
    facts_path = root / ".vibewiki/facts.json"
    first_bytes = facts_path.read_bytes()
    expected = json.loads(
        (Path(__file__).parents[1] / "expected/next-ts-demo/facts.json").read_text()
    )

    assert json.loads(first_bytes) == expected
    assert first["counts"] == {
        "facts": 15,
        "relations": 10,
        "scanned_files": 11,
        "unknowns": 1,
    }
    assert (root / ".vibewiki/graph.db").is_file()
    assert (root / ".vibewiki/wiki/graph.mmd").is_file()

    build_repository(root)
    assert facts_path.read_bytes() == first_bytes


def test_build_requires_scan_output(tmp_path: Path) -> None:
    with pytest.raises(VibeWikiError) as raised:
        build_repository(tmp_path)

    assert raised.value.code is ErrorCode.INVALID_OUTPUT


def test_serve_exposes_real_artifact_apis(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    scan_repository(root)
    build_repository(root)
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(f"{base}/api/summary") as response:
            summary = json.load(response)
        with urlopen(f"{base}/api/nodes") as response:
            nodes = json.load(response)
        with urlopen(f"{base}/api/inspect/route:page:/signup") as response:
            inspected = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert summary["project"] == "next-ts-demo"
    assert summary["counts"] == {
        "facts": 15,
        "relations": 10,
        "scanned_files": 11,
        "unknowns": 1,
    }
    assert any(node["id"] == "route:page:/signup" for node in nodes["nodes"])
    assert inspected["node"]["attributes"]["path"] == "/signup"
    assert any(edge["relation"] == "calls" for edge in inspected["connected"])


def test_serve_imports_a_browser_selected_source_folder(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    scan_repository(root)
    build_repository(root)
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    boundary = "----vibewiki-test-boundary"
    parts = []
    for path, content in {
        "picked-source/app/page.tsx": (
            "export default function Home() { return null; }\n"
        ),
        "picked-source/.env.local": "API_KEY=must-not-be-persisted\n",
    }.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{path}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            f"{content}\r\n"
        )
    body = ("".join(parts) + f"--{boundary}--\r\n").encode()
    request = Request(
        f"http://127.0.0.1:{server.server_address[1]}/api/import",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urlopen(request) as response:
            imported = json.load(response)
        with urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/api/summary"
        ) as response:
            summary = json.load(response)
    finally:
        imported_workspace = server.imported_workspace
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if imported_workspace is not None:
            cleanup_workspace(imported_workspace)

    assert imported["command"] == "build"
    assert imported["counts"]["facts"] == 1
    assert summary["project"] == "picked-source"
    assert summary["counts"]["facts"] == 1
    assert not (imported_workspace.root / ".env.local").exists()


def test_import_limit_ignores_sensitive_and_unsupported_payloads(monkeypatch) -> None:
    monkeypatch.setattr("vibewiki.importer.MAX_IMPORT_BYTES", 1)
    boundary = "----vibewiki-limit-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="repo/app/page.tsx"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "x\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="repo/.env.local"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "a-secret-value-that-is-ignored\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="repo/README.md"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "a-large-unsupported-file-that-is-ignored\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    selected = _multipart_files(f"multipart/form-data; boundary={boundary}", body)

    assert selected == [("repo", "app/page.tsx", b"x")]

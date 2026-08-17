from __future__ import annotations

import json
import shutil
import threading
import zipfile
from io import BytesIO
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


def test_serve_exposes_real_artifact_apis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBEWIKI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("VIBEWIKI_LLM_API_KEY", raising=False)
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
        with urlopen(
            f"{base}/api/source?path=app%2Fpage.tsx&start=1&end=1"
        ) as response:
            source = json.load(response)
        with urlopen(f"{base}/api/llm/status") as response:
            llm = json.load(response)
        with urlopen(f"{base}/api/export") as response:
            export_headers = dict(response.headers)
            export_bytes = response.read()
        config_request = Request(
            f"{base}/api/llm/config",
            data=json.dumps(
                {
                    "provider": "ollama",
                    "model": "qwen2.5:7b",
                    "base_url": "http://127.0.0.1:11434",
                }
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(config_request) as response:
            configured = json.load(response)
        with urlopen(f"{base}/api/llm/status") as response:
            configured_status = json.load(response)
        reset_request = Request(
            f"{base}/api/llm/config",
            data=json.dumps({"provider": "none"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(reset_request):
            pass
        ask_request = Request(
            f"{base}/api/ask",
            data=json.dumps(
                {"question": "What is connected to signup?", "mode": "flow"}
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(ask_request) as response:
            answer = json.load(response)
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
    assert source["path"] == "app/page.tsx"
    assert source["lines"][0]["number"] == 1
    assert llm["provider"] == "none"
    assert export_headers["Content-Type"] == "application/zip"
    assert "next-ts-demo-vibewiki-export.zip" in export_headers["Content-Disposition"]
    with zipfile.ZipFile(BytesIO(export_bytes)) as exported:
        exported_names = set(exported.namelist())
        assert "vibewiki-export/wiki/index.md" in exported_names
        assert "vibewiki-export/graph.json" in exported_names
        assert not any(name.endswith("page.tsx") for name in exported_names)
    assert configured["saved"] is True
    assert configured_status["provider"] == "ollama"
    assert configured_status["has_api_key"] is False
    assert answer["provider"] == "none"
    assert answer["mode"] == "flow"
    assert answer["mode_label"] == "Flow explainer"
    assert answer["citations"]
    assert answer["schema_version"] == 1


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
        'Content-Disposition: form-data; name="files"; filename="repo/image.png"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "a-large-unsupported-file-that-is-ignored\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    selected = _multipart_files(f"multipart/form-data; boundary={boundary}", body)

    assert selected == [("repo", "app/page.tsx", b"x")]

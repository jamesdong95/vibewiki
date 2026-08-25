from __future__ import annotations

import json
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from vibewiki.build import build_repository
from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.importer import (
    _multipart_files,
    cleanup_workspace,
    import_local_workspace,
)
from vibewiki.rescan import rescan_repository
from vibewiki.scan import scan_repository
from vibewiki.serve import _artifact, create_server


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


def test_serve_bootstraps_missing_artifact(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    assert not (root / ".vibewiki").exists()

    server = create_server(root, port=0)
    try:
        assert server.auto_analyzed is True
        assert (root / ".vibewiki/graph.json").is_file()
    finally:
        server.server_close()

    server = create_server(root, port=0)
    try:
        assert server.auto_analyzed is False
    finally:
        server.server_close()


def test_serve_reports_unsupported_repository_during_auto_bootstrap(
    tmp_path: Path,
) -> None:
    with pytest.raises(VibeWikiError, match="no supported"):
        create_server(tmp_path, port=0)
    assert not (tmp_path / ".vibewiki").exists()


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
        with urlopen(f"{base}/api/profile") as response:
            profile = json.load(response)
        with urlopen(f"{base}/api/nodes") as response:
            nodes = json.load(response)
        with urlopen(f"{base}/api/history") as response:
            history = json.load(response)
        with urlopen(f"{base}/api/stale") as response:
            staleness = json.load(response)
        with urlopen(f"{base}/api/files") as response:
            files = json.load(response)
        with urlopen(f"{base}/api/inspect/route:page:/signup") as response:
            inspected = json.load(response)
        with urlopen(
            f"{base}/api/impact?subject=route%3Apage%3A%2Fsignup&direction=both"
        ) as response:
            impact = json.load(response)
        with urlopen(
            f"{base}/api/source?path=app%2Fpage.tsx&start=1&end=1"
        ) as response:
            source = json.load(response)
        with urlopen(f"{base}/api/llm/status") as response:
            llm = json.load(response)
        with urlopen(f"{base}/api/export") as response:
            export_headers = dict(response.headers)
            export_bytes = response.read()
        observe_request = Request(
            f"{base}/api/observe",
            data=json.dumps({"target": base}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(observe_request) as response:
            observed = json.load(response)
        with urlopen(f"{base}/api/runtime") as response:
            runtime = json.load(response)
        with urlopen(f"{base}/api/summary") as response:
            observed_summary = json.load(response)
        linked_artifact = {**runtime}
        linked_artifact["routes"] = [
            *runtime["routes"],
            {
                "path": "/signup",
                "status": 200,
                "url": f"{base}/signup",
            },
        ]
        linked_artifact["network"] = [
            *runtime["network"],
            {
                "method": "POST",
                "status": None,
                "url": f"{base}/api/users",
                "error": "blocked_by_safe_policy",
            },
        ]
        linked_artifact["console"] = [
            {
                "line": 12,
                "column": 3,
                "text": "runtime error",
                "type": "error",
                "url": f"{base}/signup",
            }
        ]
        (root / ".vibewiki/runtime.json").write_text(
            json.dumps(linked_artifact), encoding="utf-8"
        )
        with urlopen(f"{base}/api/nodes") as response:
            linked_nodes = json.load(response)
        with urlopen(f"{base}/api/runtime") as response:
            linked_runtime = json.load(response)
        with urlopen(f"{base}/api/inspect/route:page:/signup") as response:
            linked_inspector = json.load(response)
        screenshot_dir = root / ".vibewiki/runtime-screenshots"
        screenshot_dir.mkdir(parents=True)
        (screenshot_dir / "route-01.png").write_bytes(b"fake-png")
        with urlopen(f"{base}/api/export") as response:
            export_bytes_with_runtime = response.read()
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
    assert summary["source"] == {
        "provider": "local-workspace",
        "label": "next-ts-demo",
    }
    assert summary["profile"]["scan_mode"] == "next-app-router"
    assert summary["profile"]["package_scope"] == "single-package"
    assert profile == summary["profile"]
    assert "Next.js App Router" in profile["frameworks"]
    assert any(item["language"] == "tsx" for item in profile["languages"])
    assert summary["counts"] == {
        "facts": 15,
        "relations": 10,
        "scanned_files": 11,
        "unknowns": 1,
    }
    assert summary["graph_counts"]["edges"] > summary["counts"]["relations"]
    assert any(node["id"] == "route:page:/signup" for node in nodes["nodes"])
    assert inspected["node"]["attributes"]["path"] == "/signup"
    assert any(edge["relation"] == "calls" for edge in inspected["connected"])
    assert impact["subject"] == "route:page:/signup"
    assert impact["counts"]["nodes"] >= 1
    assert source["path"] == "app/page.tsx"
    assert source["lines"][0]["number"] == 1
    assert llm["provider"] == "none"
    assert export_headers["Content-Type"] == "application/zip"
    assert "next-ts-demo-vibewiki-export.zip" in export_headers["Content-Disposition"]
    with zipfile.ZipFile(BytesIO(export_bytes)) as exported:
        exported_names = set(exported.namelist())
        assert "vibewiki-export/wiki/index.md" in exported_names
        assert "vibewiki-export/graph.json" in exported_names
        assert "vibewiki-export/history.json" in exported_names
        assert "vibewiki-export/staleness.json" in exported_names
        assert not any(name.endswith("page.tsx") for name in exported_names)
    assert configured["saved"] is True
    assert configured_status["provider"] == "ollama"
    assert configured_status["has_api_key"] is False
    assert answer["provider"] == "none"
    assert answer["mode"] == "flow"
    assert answer["mode_label"] == "Flow explainer"
    assert answer["citations"]
    assert answer["confidence"] == "medium"
    assert answer["grounded"] is True
    assert isinstance(answer["unknowns"], list)
    assert answer["schema_version"] == 1
    assert summary["staleness"] == {"status": "current", "files": 0}
    assert len(history["runs"]) == 1
    assert staleness == {"files": [], "status": "current"}
    assert {item["path"] for item in files["files"]} >= {
        "app/page.tsx",
        "app/api/users/route.ts",
    }
    assert observed["counts"] == {
        "console_errors": 0,
        "network": 1,
        "routes": 1,
        "unknowns": 1,
    }
    assert runtime["routes"][0]["path"] == "/"
    assert runtime["routes"][0]["graph_nodes"] == ["route:page:/"]
    assert runtime["network"][0]["graph_nodes"] == ["route:page:/"]
    assert runtime["unknowns"][0]["subject"] == "runtime:javascript-and-side-effects"
    assert observed_summary["runtime"] == {
        "configured": True,
        "mode": "http",
        "routes": 1,
        "network": 1,
        "console_errors": 0,
        "observed_at": runtime["observed_at"],
    }
    signup_node = next(
        node for node in linked_nodes["nodes"] if node["id"] == "route:page:/signup"
    )
    assert len(signup_node["runtime"]["routes"]) == 1
    assert len(signup_node["runtime"]["console"]) == 1
    assert "route:handler:/api/users" in linked_runtime["network"][-1]["graph_nodes"]
    assert (
        "api_call:app/signup/page.tsx:/api/users"
        in linked_runtime["network"][-1]["graph_nodes"]
    )
    assert linked_inspector["node"]["runtime"]["console"][0]["text"] == "runtime error"
    with zipfile.ZipFile(BytesIO(export_bytes_with_runtime)) as exported:
        exported_names = set(exported.namelist())
        assert "vibewiki-export/runtime.json" in exported_names
        assert "vibewiki-export/runtime-screenshots/route-01.png" in exported_names


def test_workspace_swap_blocks_api_readers_until_artifact_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture_copy(tmp_path)
    source = tmp_path / "local-path-source"
    (source / "src").mkdir(parents=True)
    (source / "src/main.js").write_text(
        "export function start() { return true; }\n", encoding="utf-8"
    )
    scan_repository(root)
    build_repository(root)
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    entered = threading.Event()
    release = threading.Event()

    def slow_artifact(path: Path) -> dict[str, object]:
        if Path(path).name == source.name:
            entered.set()
            assert release.wait(timeout=3)
        return _artifact(path)

    monkeypatch.setattr("vibewiki.serve._artifact", slow_artifact)
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def import_path() -> dict:
        request = Request(
            f"{base}/api/import-path",
            data=json.dumps({"path": str(source)}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            return json.load(response)

    def read_summary() -> dict:
        with urlopen(f"{base}/api/summary") as response:
            return json.load(response)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            import_future = pool.submit(import_path)
            assert entered.wait(timeout=3)
            summary_future = pool.submit(read_summary)
            with pytest.raises(TimeoutError):
                summary_future.result(timeout=0.15)
            release.set()
            imported = import_future.result(timeout=5)
            summary = summary_future.result(timeout=5)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert imported["import_mode"] == "local-path"
    assert summary["project"] == "local-path-source"
    assert summary["counts"]["scanned_files"] == 1


def test_serve_exposes_viewer_from_source_checkout(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    scan_repository(root)
    build_repository(root)
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/") as response:
            html = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert "VibeWiki" in html
    assert "Browse source" in html
    assert 'id="browse-picker"' in html
    assert 'id="source-package"' in html
    assert 'id="profile-modal"' in html
    assert 'id="profile-browse"' in html
    assert 'id="profile-scope"' in html
    assert 'id="profile-focus"' in html
    assert "function nodeMatchesScope" in html
    assert "setScope(scope)" in html
    assert 'id="local-path-button"' in html
    assert 'id="local-path-modal"' in html
    assert 'id="source-path"' in html
    assert "function importLocalPath" in html
    assert 'id="github-button"' in html
    assert 'id="github-modal"' in html
    assert 'id="github-url"' in html
    assert "function importGitHub" in html
    assert "/api/import-github" in html
    assert 'data-command-key="github"' in html
    assert "Public repositories only" in html
    assert "const workspaceSource = summary.source" in html
    assert "GitHub ·" in html
    assert "function buildImportGroups" in html
    assert "function rescanCurrentWorkspace" in html
    assert "/api/rescan" in html
    assert "Artifact hiện tại vẫn được giữ nguyên" in html
    assert 'id="stale-banner"' in html
    assert "function renderStaleBanner" in html
    assert "fetch('/api/stale'" in html
    assert 'class="ask-section-label">Answer' in html
    assert 'class="ask-section-label">Evidence' in html
    assert 'class="ask-section-label">Confidence' in html
    assert 'class="ask-section-label">Unknowns' in html
    assert 'id="copy-link-button"' in html
    assert "function copyLocalLink" in html
    assert "Local viewer link copied" in html
    assert 'data-command-key="copy-link"' in html


def test_rescan_rebuilds_graph_after_source_changes(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    scan_repository(root)
    build_repository(root)
    (root / "app/new/page.tsx").parent.mkdir(parents=True)
    (root / "app/new/page.tsx").write_text(
        "export default function NewPage() { return <main>New</main>; }\n",
        encoding="utf-8",
    )
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_address[1]}/api/rescan",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request) as response:
            result = json.load(response)
        with urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/api/summary"
        ) as response:
            summary = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["command"] == "rescan"
    assert result["counts"]["scanned_files"] == 12
    assert summary["counts"]["scanned_files"] == 12
    assert summary["staleness"]["status"] == "current"


def test_rescan_restores_previous_artifact_when_scan_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture_copy(tmp_path)
    scan_repository(root)
    build_repository(root)
    artifact = root / ".vibewiki"
    before = {
        path.relative_to(artifact): path.read_bytes()
        for path in artifact.rglob("*")
        if path.is_file()
    }

    def fail_scan(*args, **kwargs):
        raise VibeWikiError(ErrorCode.UNSUPPORTED_STACK, "fixture scan failed")

    monkeypatch.setattr("vibewiki.rescan.scan_repository", fail_scan)
    with pytest.raises(VibeWikiError, match="fixture scan failed"):
        rescan_repository(root)

    after = {
        path.relative_to(artifact): path.read_bytes()
        for path in artifact.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_serve_marks_source_evidence_stale_after_build(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    scan_repository(root)
    build_repository(root)
    page = root / "app/signup/page.tsx"
    page.write_text(
        page.read_text(encoding="utf-8") + "\n// changed after build\n",
        encoding="utf-8",
    )
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(f"{base}/api/stale") as response:
            staleness = json.load(response)
        with urlopen(f"{base}/api/summary") as response:
            summary = json.load(response)
        with urlopen(f"{base}/api/inspect/route:page:/signup") as response:
            inspected = json.load(response)
        with urlopen(f"{base}/api/edges") as response:
            edges = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert staleness["status"] == "stale"
    assert staleness["files"] == [
        {
            "path": "app/signup/page.tsx",
            "reason": "source file changed after the last build",
            "status": "changed",
        }
    ]
    assert summary["staleness"] == {"status": "stale", "files": 1}
    assert inspected["node"]["status"] == "stale"
    assert any(item["status"] == "stale" for item in inspected["node"]["evidence"])
    assert any(edge["status"] == "stale" for edge in edges["edges"])


def test_serve_exposes_product_intent_and_intent_gaps(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    (root / "product.seed.yaml").write_text(
        "product:\n"
        "  name: Signup demo\n"
        "flows:\n"
        "  - id: signup\n"
        "    expected:\n"
        "      - route: /signup\n"
        "      - api: /api/users\n"
        "  - id: admin\n"
        "    expected:\n"
        "      - route: /admin\n"
        "      - test: tests/admin.test.ts\n",
        encoding="utf-8",
    )
    scan_repository(root)
    build_repository(root)
    intent_artifact = json.loads((root / ".vibewiki/intent.json").read_text())
    assert intent_artifact["counts"]["gaps"] == 1
    assert (root / ".vibewiki/intent.json").is_file()
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(f"{base}/api/intent") as response:
            intent = json.load(response)
        with urlopen(f"{base}/api/summary") as response:
            summary = json.load(response)
        with urlopen(f"{base}/api/nodes") as response:
            nodes = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert intent["counts"] == {"flows": 2, "gaps": 1, "observed": 1, "partial": 1}
    assert summary["counts"]["unknowns"] == 2
    assert summary["intent"]["configured"] is True
    assert any(item["type"] == "intent_gap" for item in nodes["unknowns"])


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


def test_serve_imports_a_local_repository_path_without_browser_picker(
    tmp_path: Path,
) -> None:
    root = _fixture_copy(tmp_path)
    source = tmp_path / "local-path-source"
    (source / "src").mkdir(parents=True)
    (source / "src/main.js").write_text(
        "export function start() { return true; }\n"
    )
    scan_repository(root)
    build_repository(root)
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_address[1]}/api/import-path",
        data=json.dumps({"path": str(source)}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
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

    assert imported["import_mode"] == "local-path"
    assert imported["counts"]["scanned_files"] == 1
    assert summary["project"] == "local-path-source"
    assert summary["counts"]["facts"] == 1


def test_serve_imports_public_github_repository_through_loopback_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture_copy(tmp_path)
    source = tmp_path / "github-source"
    (source / "src").mkdir(parents=True)
    (source / "src/main.js").write_text(
        "export function fromGithub() { return true; }\n"
    )
    scan_repository(root)
    build_repository(root)

    def fake_import(url: str, ref: str | None = None):
        assert url == "https://github.com/acme/demo"
        assert ref == "main"
        imported = import_local_workspace(source)
        imported.build_summary["import_source"] = {
            "provider": "github",
            "repository": "acme/demo",
            "ref": ref,
            "selected_files": 1,
            "skipped_files": 2,
        }
        return imported

    monkeypatch.setattr("vibewiki.serve.import_github_workspace", fake_import)
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_address[1]}/api/import-github",
        data=json.dumps(
            {"url": "https://github.com/acme/demo", "ref": "main"}
        ).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
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

    assert imported["import_mode"] == "github"
    assert imported["import_source"]["repository"] == "acme/demo"
    assert imported["import_source"]["skipped_files"] == 2
    assert summary["project"] == "github-source"
    assert summary["source"]["provider"] == "github"
    assert summary["source"]["repository"] == "acme/demo"
    assert summary["source"]["ref"] == "main"
    assert summary["counts"]["facts"] == 1


def test_github_import_api_rejects_non_loopback_server(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    scan_repository(root)
    build_repository(root)
    server = create_server(root, host="0.0.0.0", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_address[1]}/api/import-github",
        data=json.dumps({"url": "https://github.com/acme/demo"}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with pytest.raises(HTTPError) as raised:
            urlopen(request)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert raised.value.code == 422


def test_local_path_import_rejects_non_loopback_server(tmp_path: Path) -> None:
    root = _fixture_copy(tmp_path)
    scan_repository(root)
    build_repository(root)
    server = create_server(root, host="0.0.0.0", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_address[1]}/api/import-path",
        data=json.dumps({"path": str(root)}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with pytest.raises(HTTPError) as raised:
            urlopen(request)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert raised.value.code == 422


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

"""Loopback HTTP API and viewer for built local VibeWiki artifacts."""

from __future__ import annotations

import io
import json
import os
import sysconfig
import threading
import zipfile
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import ANALYZER_VERSION, SCHEMA_VERSION
from .config import MANIFEST_DIRECTORY
from .discovery.manifest import canonical_json
from .errors import ErrorCode, VibeWikiError
from .history import history_for_subject, load_history, stale_files
from .importer import (
    MAX_IMPORT_BYTES,
    ImportedWorkspace,
    cleanup_workspace,
    import_github_workspace,
    import_local_workspace,
    import_uploaded_workspace,
)
from .intent import compare_product_intent
from .llm import (
    MAX_QUESTION_CHARS,
    LLMSettings,
    ask_repository,
    configure_llm,
    llm_status,
)
from .observe import observe_repository
from .rescan import rescan_repository
from .runtime_links import attach_runtime_links


def _viewer_directory() -> Path:
    """Locate the viewer in a source checkout or a clean package install."""

    source_viewer = Path(__file__).resolve().parents[2] / "viewer"
    if (source_viewer / "index.html").is_file():
        return source_viewer

    data_root = Path(sysconfig.get_path("data") or "")
    installed_viewer = data_root / "share" / "vibewiki"
    if (installed_viewer / "index.html").is_file():
        return installed_viewer

    raise VibeWikiError(
        ErrorCode.INVALID_OUTPUT,
        "viewer asset is not installed; reinstall the vibewiki package",
    )


def _artifact(root: Path) -> dict[str, Any]:
    path = root / MANIFEST_DIRECTORY / "graph.json"
    if not path.is_file():
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "build output is missing; run build first"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "build output is invalid"
        ) from error
    if data.get("schema_version") != SCHEMA_VERSION:
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "build output is invalid")
    return data


def _ensure_artifact(root: Path) -> bool:
    """Create the first local artifact so ``serve`` works as one command."""

    graph = root / MANIFEST_DIRECTORY / "graph.json"
    if graph.is_file():
        return False
    rescan_repository(root)
    return True


def _runtime(root: Path) -> dict[str, Any]:
    path = root / MANIFEST_DIRECTORY / "runtime.json"
    if not path.is_file():
        return {
            "configured": False,
            "console": [],
            "network": [],
            "observer_mode": None,
            "routes": [],
            "screenshots": [],
            "unknowns": [],
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "runtime artifact is invalid"
        ) from error
    if not isinstance(value, dict):
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "runtime artifact is invalid")
    return {"configured": True, **value}


def _export_archive(root: Path, artifact: dict[str, Any]) -> tuple[bytes, str]:
    """Create a safe, source-free ZIP of the generated VibeWiki artifacts."""
    output = root / MANIFEST_DIRECTORY
    files = (
        "manifest.json",
        "facts.json",
        "claims.json",
        "sources.json",
        "graph.json",
        "graph.db",
        "intent.json",
        "history.json",
        "runtime.json",
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "vibewiki-export/README.md",
            "# VibeWiki export\n\n"
            "Generated from the local deterministic scan. This archive contains "
            "graph, evidence, wiki, unknowns, scan history, staleness, and "
            "runtime observation artifacts; source files are not included.\n",
        )
        for name in files:
            path = output / name
            if path.is_file():
                archive.write(path, f"vibewiki-export/{name}")
        wiki = output / "wiki"
        if wiki.is_dir():
            for path in sorted(wiki.rglob("*")):
                if path.is_file():
                    relative = path.relative_to(output).as_posix()
                    archive.write(path, f"vibewiki-export/{relative}")
        screenshots = output / "runtime-screenshots"
        if screenshots.is_dir():
            for path in sorted(screenshots.rglob("*")):
                if path.is_file():
                    relative = path.relative_to(output).as_posix()
                    archive.write(path, f"vibewiki-export/{relative}")
        archive.writestr(
            "vibewiki-export/staleness.json",
            canonical_json({"files": stale_files(root, artifact)}),
        )
    project = str(artifact.get("fixture", "workspace"))
    safe_project = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in project
    ).strip("-") or "workspace"
    return buffer.getvalue(), f"{safe_project}-vibewiki-export.zip"


def _node(fact: dict[str, Any]) -> dict[str, Any]:
    attributes = fact["attributes"]
    title = (
        attributes.get("path")
        or attributes.get("name")
        or attributes.get("model")
        or fact["semantic_key"]
    )
    meta = attributes.get("file", "")
    if attributes.get("path") and attributes.get("methods"):
        meta = f"{', '.join(attributes['methods'])} {attributes['path']}"
    return {
        "id": fact["semantic_key"],
        "kind": fact["kind"],
        "status": fact["status"],
        "title": title,
        "meta": meta,
        "attributes": attributes,
        "evidence": fact["evidence"],
    }


def _mark_stale(
    node: dict[str, Any], stale_by_path: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    if not stale_by_path:
        return node
    evidence = []
    stale = False
    for item in node.get("evidence", []):
        detail = stale_by_path.get(item.get("path"))
        if detail is None:
            evidence.append(item)
            continue
        stale = True
        evidence.append({**item, "stale_reason": detail["reason"], "status": "stale"})
    if not stale:
        return node
    return {**node, "evidence": evidence, "status": "stale"}


def _artifact_nodes(
    artifact: dict[str, Any],
    stale_by_path: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    stale_by_path = stale_by_path or {}
    nodes = [_mark_stale(_node(fact), stale_by_path) for fact in artifact["facts"]]
    existing = {node["id"] for node in nodes}
    for module in artifact.get("modules", []):
        if module["id"] in existing:
            continue
        attributes = module["attributes"]
        nodes.append(
            _mark_stale(
                {
                "id": module["id"],
                "kind": module["kind"],
                "status": module["status"],
                "title": (
                    attributes.get("path")
                    or attributes.get("module")
                    or module["id"]
                ),
                "meta": attributes.get("file") or attributes.get("module", ""),
                "attributes": attributes,
                "evidence": module["evidence"],
                },
                stale_by_path,
            )
        )
        existing.add(module["id"])
    for node_group in (artifact.get("packages", []), artifact.get("symbols", [])):
        for item in node_group:
            if item["id"] in existing:
                continue
            attributes = item["attributes"]
            nodes.append(
                _mark_stale(
                    {
                    "id": item["id"],
                    "kind": item["kind"],
                    "status": item["status"],
                    "title": (
                        attributes.get("name")
                        or attributes.get("path")
                        or attributes.get("module")
                        or item["id"]
                    ),
                    "meta": attributes.get("file") or attributes.get("path", ""),
                    "attributes": attributes,
                    "evidence": item["evidence"],
                    },
                    stale_by_path,
                )
            )
            existing.add(item["id"])
    module_ids = {
        module["attributes"].get("path") for module in artifact.get("modules", [])
    }
    for item in artifact.get("inventory", {}).get("files", []):
        if item["path"] in module_ids:
            continue
        node_id = f"file:{item['path']}"
        nodes.append(
            _mark_stale(
                {
                "id": node_id,
                "kind": "file",
                "status": "verified",
                "title": item["path"],
                "meta": f"{item['language']} · {item['size']} bytes",
                "attributes": item,
                "evidence": [
                    {
                        "kind": "file_inventory",
                        "line_end": 1,
                        "line_start": 1,
                        "path": item["path"],
                        "status": "verified",
                    }
                ],
                },
                stale_by_path,
            )
        )
    return sorted(nodes, key=lambda node: (node["kind"], node["id"]))


def _mark_edge_stale(
    edge: dict[str, Any], stale_by_path: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    if not stale_by_path:
        return edge
    evidence = []
    stale = False
    for item in edge.get("evidence", []):
        detail = stale_by_path.get(item.get("path"))
        if detail is None:
            evidence.append(item)
            continue
        stale = True
        evidence.append({**item, "stale_reason": detail["reason"], "status": "stale"})
    return {**edge, "evidence": evidence, "status": "stale"} if stale else edge


def _artifact_edges(
    artifact: dict[str, Any],
    stale_by_path: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    stale_by_path = stale_by_path or {}
    edges = (
        artifact["relations"]
        + artifact.get("module_edges", [])
        + artifact.get("package_edges", [])
        + artifact.get("symbol_edges", [])
    )
    return [_mark_edge_stale(edge, stale_by_path) for edge in edges]


MAX_TRAVERSAL_DEPTH = 4
MAX_TRAVERSAL_NODES = 100


def _graph_traversal(
    subject: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    direction: str,
    depth: int,
    limit: int,
) -> dict[str, Any]:
    """Return a bounded deterministic upstream/downstream graph neighborhood."""

    node_by_id = {node["id"]: node for node in nodes}
    root = node_by_id.get(subject)
    if root is None:
        return {
            "subject": subject,
            "direction": direction,
            "depth": depth,
            "limit": limit,
            "truncated": False,
            "node": None,
            "nodes": [],
            "edges": [],
        }

    adjacency: dict[str, list[tuple[str, dict[str, Any], str]]] = {}
    for edge in sorted(
        edges,
        key=lambda item: (
            str(item.get("source", "")),
            str(item.get("target", "")),
            str(item.get("relation", "")),
        ),
    ):
        source = edge.get("source")
        target = edge.get("target")
        if source not in node_by_id or target not in node_by_id:
            continue
        if direction in {"downstream", "both"}:
            adjacency.setdefault(source, []).append((target, edge, "downstream"))
        if direction in {"upstream", "both"}:
            adjacency.setdefault(target, []).append((source, edge, "upstream"))

    queue: deque[tuple[str, int]] = deque([(subject, 0)])
    visited = {subject}
    result_nodes: list[dict[str, Any]] = []
    result_edges: list[dict[str, Any]] = []
    truncated = False
    while queue:
        current, distance = queue.popleft()
        if distance >= depth:
            continue
        for neighbor, edge, step_direction in adjacency.get(current, []):
            if neighbor in visited:
                continue
            if len(result_nodes) >= limit:
                truncated = True
                break
            visited.add(neighbor)
            queue.append((neighbor, distance + 1))
            result_nodes.append(
                {
                    "node": node_by_id[neighbor],
                    "distance": distance + 1,
                    "direction": step_direction,
                }
            )
            result_edges.append(edge)
        if truncated:
            break

    return {
        "subject": subject,
        "direction": direction,
        "depth": depth,
        "limit": limit,
        "truncated": truncated,
        "node": root,
        "nodes": result_nodes,
        "edges": result_edges,
    }


def _traversal_params(params: dict[str, list[str]]) -> tuple[str, str, int, int]:
    subject = params.get("subject", [""])[0]
    direction = params.get("direction", ["both"])[0].casefold()
    if not subject or len(subject) > 256:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "impact query requires a valid subject",
        )
    if direction not in {"upstream", "downstream", "both"}:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "impact direction must be upstream, downstream, or both",
        )
    try:
        depth = int(params.get("depth", ["3"])[0])
        limit = int(params.get("limit", [str(MAX_TRAVERSAL_NODES)])[0])
    except ValueError as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "impact depth and limit must be integers",
        ) from error
    if not 1 <= depth <= MAX_TRAVERSAL_DEPTH:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            f"impact depth must be between 1 and {MAX_TRAVERSAL_DEPTH}",
        )
    if not 1 <= limit <= MAX_TRAVERSAL_NODES:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            f"impact limit must be between 1 and {MAX_TRAVERSAL_NODES}",
        )
    return subject, direction, depth, limit


def _safe_source_path(value: str) -> str:
    raw = unquote(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "source path was not found")
    return path.as_posix()


def _source_payload(
    root: Path,
    artifact: dict[str, Any],
    params: dict[str, list[str]],
    stale_by_path: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    requested = _safe_source_path(params.get("path", [""])[0])
    item = next(
        (
            item
            for item in artifact.get("inventory", {}).get("files", [])
            if item["path"] == requested
        ),
        None,
    )
    if item is None:
        raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "source path was not found")
    absolute = root / Path(requested)
    try:
        if absolute.is_symlink() or not absolute.is_file():
            raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "source path was not found")
        if item["kind"] == "binary":
            return {
                "binary": True,
                "lines": [],
                "path": requested,
                "source": item,
                "stale": (stale_by_path or {}).get(requested),
            }
        text = absolute.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "source file is not valid UTF-8"
        ) from error
    except OSError as error:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED, "source file could not be read"
        ) from error
    raw_start = params.get("start", ["1"])[0]
    raw_end = params.get("end", [raw_start])[0]
    try:
        start = max(1, int(raw_start))
        end = max(start, int(raw_end))
    except ValueError as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "source line range is invalid"
        ) from error
    end = min(end, start + 399)
    lines = text.splitlines()
    return {
        "binary": False,
        "lines": [
            {"number": number, "text": lines[number - 1]}
            for number in range(start, min(end, len(lines)) + 1)
        ],
        "path": requested,
        "source": item,
        "stale": (stale_by_path or {}).get(requested),
    }


def api_payload(
    root: Path,
    artifact: dict[str, Any],
    path: str,
    query: str = "",
    params: dict[str, list[str]] | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    facts = artifact["facts"]
    stale = stale_files(root, artifact)
    stale_by_path = {item["path"]: item for item in stale}
    nodes = _artifact_nodes(artifact, stale_by_path)
    edges = _artifact_edges(artifact, stale_by_path)
    params = params or {}
    intent = compare_product_intent(root, artifact)
    runtime = attach_runtime_links(_runtime(root), nodes)
    unknowns = artifact["unknowns"] + intent["gaps"] + runtime.get("unknowns", [])
    profile = artifact.get("profile", {})
    workspace_source = source or {
        "provider": "local-workspace",
        "label": root.name,
    }
    if path == "/api/profile":
        return profile
    if path == "/api/summary":
        return {
            "project": artifact["fixture"],
            "source": workspace_source,
            "profile": profile,
            "analyzer_version": ANALYZER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "counts": {
                "facts": len(facts),
                "relations": len(artifact["relations"]),
                "unknowns": len(unknowns),
                "scanned_files": len(
                    json.loads(
                        (root / MANIFEST_DIRECTORY / "manifest.json").read_text()
                    )["files"]
                ),
            },
            "evidence": sum(len(fact["evidence"]) for fact in facts)
            + sum(len(edge["evidence"]) for edge in artifact["relations"]),
            "status": "ready",
            "graph_counts": {"nodes": len(nodes), "edges": len(edges)},
            "staleness": {
                "status": "stale" if stale else "current",
                "files": len(stale),
            },
            "intent": {
                "configured": intent["configured"],
                **intent["counts"],
            },
            "runtime": {
                "configured": runtime["configured"],
                "mode": runtime.get("observer_mode"),
                "routes": len(runtime.get("routes", [])),
                "network": len(runtime.get("network", [])),
                "console_errors": len(runtime.get("console", [])),
                "observed_at": runtime.get("observed_at"),
            },
        }
    if path == "/api/nodes":
        return {"nodes": nodes, "unknowns": unknowns}
    if path == "/api/intent":
        return intent
    if path == "/api/stale":
        return {
            "files": stale,
            "status": "stale" if stale else "current",
        }
    if path == "/api/runtime":
        return runtime
    if path == "/api/history":
        return {**load_history(root), "current_staleness": stale}
    if path == "/api/history/subject":
        subject = params.get("subject", [""])[0]
        return history_for_subject(root, subject)
    if path == "/api/edges":
        return {"edges": edges}
    if path == "/api/impact":
        subject, direction, depth, limit = _traversal_params(params)
        result = _graph_traversal(
            subject,
            nodes,
            edges,
            direction=direction,
            depth=depth,
            limit=limit,
        )
        result["counts"] = {
            "nodes": len(result["nodes"]),
            "edges": len(result["edges"]),
        }
        return result
    if path == "/api/files":
        return {
            "files": [
                {
                    **item,
                    "status": (
                        "stale" if item.get("path") in stale_by_path else "current"
                    ),
                }
                for item in artifact.get("inventory", {}).get("files", [])
            ]
        }
    if path == "/api/modules":
        return {
            "edges": artifact.get("module_edges", []),
            "modules": artifact.get("modules", []),
        }
    if path == "/api/packages":
        return {
            "edges": artifact.get("package_edges", []),
            "packages": artifact.get("packages", []),
        }
    if path == "/api/symbols":
        return {
            "edges": artifact.get("symbol_edges", []),
            "symbols": artifact.get("symbols", []),
        }
    if path == "/api/source":
        return _source_payload(root, artifact, params, stale_by_path)
    if path == "/api/llm/status":
        return llm_status()
    if path == "/api/search":
        needle = query.casefold()
        return {
            "query": query,
            "nodes": [
                node
                for node in nodes
                if needle in json.dumps(node, ensure_ascii=False).casefold()
            ],
        }
    if path.startswith("/api/inspect/"):
        subject = path.removeprefix("/api/inspect/")
        match = next((node for node in nodes if node["id"] == subject), None)
        if match is None:
            unknown = next(
                (item for item in unknowns if item["subject"] == subject),
                None,
            )
            return {"node": None, "unknown": unknown, "connected": []}
        connected = [
            edge
            for edge in edges
            if edge["source"] == subject or edge["target"] == subject
        ]
        return {"node": match, "unknown": None, "connected": connected}
    raise KeyError(path)


def create_server(
    repository: str | Path, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    root = Path(repository).absolute()
    auto_analyzed = _ensure_artifact(root)
    artifact = _artifact(root)
    viewer = _viewer_directory()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(viewer), **kwargs)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/export":
                try:
                    with self.server.workspace_lock:
                        body, filename = _export_archive(
                            self.server.workspace_root,
                            self.server.workspace_artifact,
                        )
                except (OSError, ValueError, zipfile.BadZipFile) as error:
                    self._write_json(
                        500,
                        {"error": "export_failed", "message": str(error)},
                    )
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{filename}"'
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path.startswith("/api/"):
                try:
                    if parsed.path == "/api/llm/status":
                        payload = llm_status(self.server.llm_settings)
                    else:
                        with self.server.workspace_lock:
                            payload = api_payload(
                                self.server.workspace_root,
                                self.server.workspace_artifact,
                                parsed.path,
                                parse_qs(parsed.query).get("q", [""])[0],
                                parse_qs(parsed.query),
                                getattr(self.server, "workspace_source", None),
                            )
                except KeyError:
                    self.send_error(404, "not found")
                    return
                except VibeWikiError as error:
                    status = 404 if error.code is ErrorCode.PATH_NOT_FOUND else 422
                    self._write_json(
                        status,
                        {"error": error.code.value, "message": error.message},
                    )
                    return
                body = canonical_json(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/observe":
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "runtime observation payload is empty or too large",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    if not isinstance(payload, dict):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "runtime observation payload is invalid",
                        )
                    target = payload.get("target")
                    if not isinstance(target, str) or not target.strip():
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "runtime observation target is required",
                        )
                    mode = payload.get("mode", "http")
                    if mode not in {"http", "browser"}:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "runtime observation mode must be http or browser",
                        )
                    with self.server.workspace_lock:
                        result = observe_repository(
                            self.server.workspace_root,
                            target.strip(),
                            mode=mode,
                            screenshots=bool(payload.get("screenshots", False)),
                        )
                    self._write_json(200, result)
                except VibeWikiError as error:
                    self._write_json(
                        422,
                        {"error": error.code.value, "message": error.message},
                    )
                except (OSError, UnicodeDecodeError, ValueError) as error:
                    self._write_json(
                        400,
                        {"error": "invalid_output", "message": str(error)},
                    )
                return
            if parsed.path == "/api/import-github":
                imported: ImportedWorkspace | None = None
                swapped = False
                try:
                    if not self.server.github_import_allowed:
                        raise VibeWikiError(
                            ErrorCode.PERMISSION_DENIED,
                            "GitHub import is available only on a loopback server",
                        )
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "GitHub import payload is empty or too large",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    if not isinstance(payload, dict):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "GitHub import payload is invalid",
                        )
                    repository_url = payload.get("url")
                    ref = payload.get("ref")
                    if not isinstance(repository_url, str) or (
                        ref is not None and not isinstance(ref, str)
                    ):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "GitHub import requires a repository URL and optional ref",
                        )
                    imported = import_github_workspace(repository_url, ref)
                    with self.server.workspace_lock:
                        old_workspace = self.server.imported_workspace
                        self.server.workspace_root = imported.root
                        self.server.workspace_artifact = _artifact(imported.root)
                        self.server.imported_workspace = imported
                        self.server.workspace_source = imported.build_summary.get(
                            "import_source",
                            {
                                "provider": "github",
                                "label": imported.root.name,
                            },
                        )
                        swapped = True
                        if old_workspace is not None:
                            cleanup_workspace(old_workspace)
                    self._write_json(
                        200, {**imported.build_summary, "import_mode": "github"}
                    )
                except VibeWikiError as error:
                    if imported is not None and not swapped:
                        cleanup_workspace(imported)
                    self._write_json(
                        422,
                        {"error": error.code.value, "message": error.message},
                    )
                except (OSError, UnicodeDecodeError, ValueError) as error:
                    if imported is not None and not swapped:
                        cleanup_workspace(imported)
                    self._write_json(
                        400,
                        {"error": "invalid_output", "message": str(error)},
                    )
                return
            if parsed.path == "/api/import-path":
                try:
                    if not self.server.local_path_import_allowed:
                        raise VibeWikiError(
                            ErrorCode.PERMISSION_DENIED,
                            "local path import is available only on a loopback server",
                        )
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "local path import payload is empty or too large",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("path"), str
                    ):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "local repository path is required",
                        )
                    imported = import_local_workspace(payload["path"].strip())
                    with self.server.workspace_lock:
                        old_workspace = self.server.imported_workspace
                        self.server.workspace_root = imported.root
                        self.server.workspace_artifact = _artifact(imported.root)
                        self.server.imported_workspace = imported
                        self.server.workspace_source = {
                            "provider": "local-path",
                            "label": Path(payload["path"]).expanduser().name
                            or imported.root.name,
                        }
                        if old_workspace is not None:
                            cleanup_workspace(old_workspace)
                    self._write_json(
                        200, {**imported.build_summary, "import_mode": "local-path"}
                    )
                except VibeWikiError as error:
                    self._write_json(
                        422,
                        {"error": error.code.value, "message": error.message},
                    )
                except (OSError, UnicodeDecodeError, ValueError) as error:
                    self._write_json(
                        400,
                        {"error": "invalid_output", "message": str(error)},
                    )
                return
            if parsed.path == "/api/rescan":
                try:
                    if not self.server.local_path_import_allowed:
                        raise VibeWikiError(
                            ErrorCode.PERMISSION_DENIED,
                            "workspace rescan is available only on a loopback server",
                        )
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "rescan payload is too large",
                        )
                    if content_length:
                        payload = json.loads(
                            self.rfile.read(content_length).decode("utf-8")
                        )
                        if not isinstance(payload, dict):
                            raise VibeWikiError(
                                ErrorCode.INVALID_OUTPUT,
                                "rescan payload is invalid",
                            )
                    if not self.server.rescan_lock.acquire(blocking=False):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "a workspace rescan is already in progress",
                        )
                    try:
                        with self.server.workspace_lock:
                            result = rescan_repository(self.server.workspace_root)
                            self.server.workspace_artifact = _artifact(
                                self.server.workspace_root
                            )
                    finally:
                        self.server.rescan_lock.release()
                    self._write_json(200, result)
                except VibeWikiError as error:
                    self._write_json(
                        422,
                        {"error": error.code.value, "message": error.message},
                    )
                except (OSError, UnicodeDecodeError, ValueError) as error:
                    self._write_json(
                        400,
                        {"error": "invalid_output", "message": str(error)},
                    )
                return
            if parsed.path == "/api/llm/config":
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "LLM configuration payload is empty or too large",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    if not isinstance(payload, dict):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "LLM configuration payload is invalid",
                        )
                    settings = configure_llm(
                        payload, getattr(self.server, "llm_settings", None)
                    )
                    self.server.llm_settings = settings
                    self._write_json(200, {"saved": True, **llm_status(settings)})
                except VibeWikiError as error:
                    self._write_json(
                        422,
                        {"error": error.code.value, "message": error.message},
                    )
                except (OSError, UnicodeDecodeError, ValueError) as error:
                    self._write_json(
                        400,
                        {"error": "invalid_output", "message": str(error)},
                    )
                return
            if parsed.path == "/api/ask":
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT, "question payload is empty"
                        )
                    if content_length > MAX_QUESTION_CHARS * 8:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "question payload exceeds the local safety limit",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    if not isinstance(payload, dict):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT, "question payload is invalid"
                        )
                    with self.server.workspace_lock:
                        result = ask_repository(
                            self.server.workspace_root,
                            self.server.workspace_artifact,
                            payload,
                            getattr(self.server, "llm_settings", None),
                        )
                    self._write_json(200, result)
                except VibeWikiError as error:
                    self._write_json(
                        422,
                        {"error": error.code.value, "message": error.message},
                    )
                except (OSError, UnicodeDecodeError, ValueError) as error:
                    self._write_json(
                        400,
                        {"error": "invalid_output", "message": str(error)},
                    )
                return
            if parsed.path != "/api/import":
                self.send_error(404, "not found")
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT, "selected source is empty"
                    )
                if content_length > MAX_IMPORT_BYTES + 5 * 1024 * 1024:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "selected source exceeds the local import limit",
                    )
                body = self.rfile.read(content_length)
                imported = import_uploaded_workspace(
                    self.headers.get("Content-Type", ""), body
                )
                with self.server.workspace_lock:
                    old_workspace = self.server.imported_workspace
                    self.server.workspace_root = imported.root
                    self.server.workspace_artifact = _artifact(imported.root)
                    self.server.imported_workspace = imported
                    self.server.workspace_source = {
                        "provider": "browser-folder",
                        "label": imported.root.name,
                    }
                    if old_workspace is not None:
                        cleanup_workspace(old_workspace)
                payload = imported.build_summary
                self._write_json(200, payload)
            except VibeWikiError as error:
                self._write_json(
                    422,
                    {"error": error.code.value, "message": error.message},
                )
            except (OSError, ValueError) as error:
                self._write_json(
                    400,
                    {"error": "invalid_output", "message": str(error)},
                )

        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            body = canonical_json(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store, max-age=0")
            super().end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.workspace_root = root
    server.workspace_artifact = artifact
    server.llm_settings: LLMSettings | None = None
    server.imported_workspace: ImportedWorkspace | None = None
    server.workspace_source = {
        "provider": "local-workspace",
        "label": root.name,
    }
    server.local_path_import_allowed = host in {"127.0.0.1", "localhost", "::1"}
    server.github_import_allowed = server.local_path_import_allowed
    server.workspace_lock = threading.RLock()
    server.rescan_lock = threading.Lock()
    server.auto_analyzed = auto_analyzed
    return server


def serve_repository(
    repository: str | Path,
    host: str = "127.0.0.1",
    port: int = 4173,
    *,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_base_url: str | None = None,
) -> None:
    overrides = {
        "VIBEWIKI_LLM_PROVIDER": llm_provider,
        "VIBEWIKI_LLM_MODEL": llm_model,
        "VIBEWIKI_LLM_BASE_URL": llm_base_url,
    }
    previous = {key: os.environ.get(key) for key in overrides}
    for key, value in overrides.items():
        if value is not None:
            os.environ[key] = value
    server: ThreadingHTTPServer | None = None
    try:
        server = create_server(repository, host, port)
        actual_port = server.server_address[1]
        print(
            canonical_json(
                {
                    "bind": host,
                    "command": "serve",
                    "port": actual_port,
                    "artifact_root": MANIFEST_DIRECTORY,
                    "auto_analyzed": server.auto_analyzed,
                    "schema_version": SCHEMA_VERSION,
                    "status": "ready",
                }
            ),
            end="",
            flush=True,
        )
        server.serve_forever()
    finally:
        if server is not None and server.imported_workspace is not None:
            cleanup_workspace(server.imported_workspace)
        if server is not None:
            server.server_close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


__all__ = ["api_payload", "create_server", "serve_repository"]

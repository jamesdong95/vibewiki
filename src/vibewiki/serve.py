"""Loopback HTTP API and viewer for built local VibeWiki artifacts."""

from __future__ import annotations

import io
import ipaddress
import json
import os
import secrets
import sysconfig
import threading
import zipfile
from collections import deque
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import ANALYZER_VERSION, SCHEMA_VERSION
from .config import MANIFEST_DIRECTORY
from .discovery.manifest import canonical_json
from .errors import ErrorCode, VibeWikiError
from .history import (
    history_for_subject,
    load_history,
    load_source_diff_detail,
    load_source_diff_summary,
    stale_files,
)
from .importer import (
    MAX_IMPORT_BYTES,
    ImportedWorkspace,
    cleanup_workspace,
    import_github_workspace,
    import_local_workspace,
    import_uploaded_workspace,
)
from .intent import SEED_FILENAME, compare_product_intent, write_product_seed
from .llm import (
    MAX_QUESTION_CHARS,
    LLMSettings,
    ask_repository,
    configure_llm,
    llm_status,
)
from .observe import observe_repository
from .rescan import rescan_repository
from .reviews import (
    REVIEWS_FILENAME,
    load_reviews,
    review_counts,
    set_review,
    set_reviews,
)
from .runtime_links import attach_runtime_links
from .workspaces import WorkspaceStore


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


def _is_loopback_host(host: str) -> bool:
    """Return whether a bind host is unambiguously loopback-only.

    Unknown hostnames are treated as non-loopback. Refusing to resolve an
    arbitrary hostname avoids a DNS change turning a safe command into a
    network-facing server.
    """

    value = str(host).strip().lower()
    if value in {"localhost", "ip6-localhost"}:
        return True
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if "%" in value:
        value = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _validate_bind_host(host: str, share: bool) -> bool:
    """Validate a server bind and return whether it is loopback-only."""

    loopback = _is_loopback_host(host)
    if not loopback and not share:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "non-loopback server binds require --share authentication",
        )
    return loopback


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
        "graph-index.json",
        "intent.json",
        "history.json",
        REVIEWS_FILENAME,
        "runtime.json",
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "vibewiki-export/README.md",
            "# VibeWiki export\n\n"
            "Generated from the local deterministic scan. This archive contains "
            "graph, evidence, wiki, unknowns, scan history, review state, "
            "staleness, and "
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
    safe_project = (
        "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in project
        ).strip("-")
        or "workspace"
    )
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
    if path == "/api/reviews":
        reviews = load_reviews(root)
        return {**reviews, "counts": review_counts(reviews)}
    if path == "/api/changes":
        history = load_history(root)
        latest = history.get("runs", [None])[0]
        graph_changes = (
            latest.get("graph_changes") if isinstance(latest, dict) else None
        )
        if not isinstance(graph_changes, dict):
            graph_changes = {
                "counts": {
                    "nodes_added": 0,
                    "nodes_changed": 0,
                    "nodes_removed": 0,
                    "edges_added": 0,
                    "edges_changed": 0,
                    "edges_removed": 0,
                },
                "status": "unavailable",
                "truncated": False,
            }
        return {
            "files": latest.get("changes", {}) if isinstance(latest, dict) else {},
            "graph": graph_changes,
            "reviews": load_reviews(root),
            "run": latest,
            "source_diff": load_source_diff_summary(root),
            "status": graph_changes.get("status", "unavailable"),
        }
    if path == "/api/changes/source":
        requested = params.get("path", [""])[0]
        return load_source_diff_detail(root, _safe_source_path(requested))
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


def _persist_imported_workspace(
    server: ThreadingHTTPServer,
    imported: ImportedWorkspace,
    *,
    provider: str,
    label: str,
    origin: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> ImportedWorkspace:
    """Promote an import into the private managed workspace cache."""

    record, destination = server.workspace_store.save_snapshot(
        imported.root,
        label=label,
        provider=provider,
        origin=origin,
        workspace_id=workspace_id,
    )
    cleanup_workspace(imported)
    summary = dict(imported.build_summary)
    source = {
        **summary.get("import_source", {}),
        "provider": provider,
        "label": label,
        "workspace_id": record.id,
        "persistence": "saved-snapshot",
        "source_state": "snapshot",
    }
    summary["import_source"] = source
    return ImportedWorkspace(root=destination, build_summary=summary)


def _initial_llm_settings(store: WorkspaceStore) -> LLMSettings | None:
    """Restore non-secret LLM preferences while keeping keys process-local."""

    try:
        settings = LLMSettings.from_environment()
    except VibeWikiError:
        settings = None
    preferences = store.load_llm_preferences()
    if not preferences:
        return settings
    payload = {
        "provider": preferences.get(
            "provider", settings.provider if settings else "none"
        ),
        "model": preferences.get("model", settings.model if settings else "qwen2.5:7b"),
        "base_url": preferences.get(
            "base_url",
            settings.base_url if settings else "https://api.openai.com",
        ),
    }
    try:
        return configure_llm(payload, settings)
    except VibeWikiError:
        # A persisted remote provider without an environment key is still a
        # valid preference; the UI can ask for the key again without exposing
        # or writing it to disk.
        return settings


def create_server(
    repository: str | Path | None,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    share: bool = False,
    state_dir: str | Path | None = None,
) -> ThreadingHTTPServer:
    loopback_host = _validate_bind_host(host, share)
    workspace_store = WorkspaceStore(state_dir)
    workspace_id: str | None = None
    onboarding = False
    if repository is None:
        root: Path | None = None
        artifact: dict[str, Any] | None = None
        for item in workspace_store.public():
            try:
                record, candidate = workspace_store.get(item["id"])
                candidate_artifact = _artifact(candidate)
            except VibeWikiError:
                continue
            root, artifact, workspace_id = candidate, candidate_artifact, record.id
            workspace_store.touch(record.id)
            break
        if root is None or artifact is None:
            # Keep the server alive for a first-run viewer. Browse/import can
            # then create the first durable snapshot without requiring a
            # repository argument on the command line.
            root = workspace_store.root / "onboarding"
            root.mkdir(parents=True, exist_ok=True)
            onboarding = True
        auto_analyzed = False
    else:
        root = Path(repository).absolute()
        auto_analyzed = _ensure_artifact(root)
        artifact = _artifact(root)
    viewer = _viewer_directory()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(viewer), **kwargs)

        def _authorized(self) -> bool:
            """Check the shared-server bearer token without logging it."""

            if not self.server.auth_required:
                return True
            authorization = self.headers.get("Authorization", "")
            scheme, _, credential = authorization.partition(" ")
            candidate = credential.strip() if scheme.casefold() == "bearer" else ""
            expected = self.server.access_token or ""
            # Always compare a string, including for malformed/missing headers.
            # compare_digest prevents timing differences from revealing the
            # generated token one character at a time.
            if secrets.compare_digest(candidate, expected):
                return True
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except CookieError:
                cookies = SimpleCookie()
            cookie = cookies.get("vibewiki_access")
            if cookie is not None and secrets.compare_digest(cookie.value, expected):
                return True
            parsed = urlparse(self.path)
            if parsed.path not in {"/", "/index.html"}:
                return False
            query_token = parse_qs(parsed.query).get("access_token", [""])[0]
            if secrets.compare_digest(query_token, expected):
                # The first browser navigation cannot set Authorization headers.
                # A valid bootstrap token is exchanged for a session cookie, then
                # the URL is cleaned before the HTML is served.
                self._bootstrap_authorized = True
                return True
            return False

        def _require_authorization(self) -> bool:
            if self._authorized():
                return True
            body = canonical_json(
                {
                    "error": "unauthorized",
                    "message": "a valid Authorization: Bearer token is required",
                }
            ).encode("utf-8")
            self.send_response(401)
            self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._require_authorization():
                return
            parsed = urlparse(self.path)
            if getattr(self, "_bootstrap_authorized", False):
                self.send_response(302)
                self.send_header("Location", parsed.path or "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if parsed.path == "/api/export":
                try:
                    if not self.server.workspace_available:
                        raise VibeWikiError(
                            ErrorCode.PATH_NOT_FOUND,
                            "no workspace is open; Browse a local repository first",
                        )
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
            if parsed.path == "/api/workspaces":
                self._write_json(
                    200,
                    {
                        "schema_version": 1,
                        "workspaces": self.server.workspace_store.public(),
                    },
                )
                return
            if parsed.path == "/api/summary" and not self.server.workspace_available:
                self._write_json(
                    200,
                    {
                        "status": "onboarding",
                        "workspace_available": False,
                        "project": None,
                        "source": {"provider": "onboarding", "label": "No workspace"},
                        "workspaces": self.server.workspace_store.public(),
                        "capabilities": {
                            "browse": self.server.local_path_import_allowed,
                            "local_path": self.server.local_path_import_allowed,
                            "github": self.server.github_import_allowed,
                        },
                    },
                )
                return
            if parsed.path.startswith("/api/"):
                try:
                    if parsed.path == "/api/llm/status":
                        payload = llm_status(self.server.llm_settings)
                    elif not self.server.workspace_available:
                        self._write_json(
                            409,
                            {
                                "error": "workspace_unavailable",
                                "message": (
                                    "no workspace is open; Browse a local repository "
                                    "first"
                                ),
                            },
                        )
                        return
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
            if not self._require_authorization():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/workspaces/open":
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "workspace open payload is empty or too large",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    workspace_id = (
                        payload.get("id") if isinstance(payload, dict) else None
                    )
                    if not isinstance(workspace_id, str):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "workspace id is required",
                        )
                    record, root = self.server.workspace_store.get(workspace_id)
                    artifact = _artifact(root)
                    self.server.workspace_store.touch(workspace_id)
                    with self.server.workspace_lock:
                        self.server.workspace_root = root
                        self.server.workspace_artifact = artifact
                        self.server.workspace_available = True
                        self.server.workspace_id = record.id
                        self.server.imported_workspace = None
                        self.server.workspace_source = {
                            **next(
                                item
                                for item in self.server.workspace_store.public()
                                if item["id"] == record.id
                            ),
                            "workspace_id": record.id,
                        }
                    self._write_json(
                        200,
                        {
                            "opened": True,
                            "workspace_id": record.id,
                            "source": self.server.workspace_source,
                        },
                    )
                except VibeWikiError as error:
                    self._write_json(
                        422, {"error": error.code.value, "message": error.message}
                    )
                except (
                    OSError,
                    UnicodeDecodeError,
                    ValueError,
                    AttributeError,
                ) as error:
                    self._write_json(
                        400, {"error": "invalid_output", "message": str(error)}
                    )
                return
            if parsed.path == "/api/workspaces/refresh":
                refreshed: ImportedWorkspace | None = None
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "workspace refresh payload is empty or too large",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    workspace_id = (
                        payload.get("id") if isinstance(payload, dict) else None
                    )
                    if not isinstance(workspace_id, str):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "workspace id is required",
                        )
                    record, _ = self.server.workspace_store.get(workspace_id)
                    if record.provider == "local-path":
                        local_path = record.origin.get("local_path")
                        if (
                            not isinstance(local_path, str)
                            or not Path(local_path).is_dir()
                        ):
                            raise VibeWikiError(
                                ErrorCode.PATH_NOT_FOUND,
                                "the original local source path is unavailable",
                            )
                        refreshed = import_local_workspace(local_path)
                    elif record.provider == "github":
                        url = record.origin.get("url")
                        ref = record.origin.get("ref")
                        if not isinstance(url, str):
                            raise VibeWikiError(
                                ErrorCode.INVALID_OUTPUT,
                                "the original GitHub source metadata is unavailable",
                            )
                        refreshed = import_github_workspace(
                            url, ref if isinstance(ref, str) else None
                        )
                    else:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "browser-folder snapshots must be updated with Browse "
                            "source",
                        )
                    with self.server.workspace_lock:
                        refreshed_artifact = _artifact(refreshed.root)
                        refreshed = _persist_imported_workspace(
                            self.server,
                            refreshed,
                            provider=record.provider,
                            label=record.label,
                            origin=record.origin,
                            workspace_id=workspace_id,
                        )
                        self.server.workspace_root = refreshed.root
                        self.server.workspace_artifact = refreshed_artifact
                        self.server.workspace_available = True
                        self.server.workspace_id = workspace_id
                        self.server.imported_workspace = refreshed
                        self.server.workspace_source = {
                            **refreshed.build_summary.get("import_source", {}),
                            "workspace_id": workspace_id,
                            "label": record.label,
                        }
                    self._write_json(
                        200,
                        {
                            **refreshed.build_summary,
                            "refreshed": True,
                            "workspace_id": workspace_id,
                        },
                    )
                except VibeWikiError as error:
                    if refreshed is not None:
                        cleanup_workspace(refreshed)
                    self._write_json(
                        422, {"error": error.code.value, "message": error.message}
                    )
                except (OSError, UnicodeDecodeError, ValueError) as error:
                    if refreshed is not None:
                        cleanup_workspace(refreshed)
                    self._write_json(
                        400, {"error": "invalid_output", "message": str(error)}
                    )
                return
            if parsed.path == "/api/reviews/batch":
                try:
                    if not self.server.local_path_import_allowed:
                        raise VibeWikiError(
                            ErrorCode.PERMISSION_DENIED,
                            "review state is available only on a loopback server",
                        )
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "review batch payload is empty or too large",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    if not isinstance(payload, dict):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "review batch payload is invalid",
                        )
                    with self.server.workspace_lock:
                        reviews, state = set_reviews(
                            self.server.workspace_root,
                            payload.get("items"),
                        )
                    self._write_json(
                        200,
                        {
                            "counts": review_counts(state),
                            "reviews": reviews,
                            "state": state,
                            "saved": True,
                        },
                    )
                except VibeWikiError as error:
                    self._write_json(
                        422,
                        {"error": error.code.value, "message": error.message},
                    )
                except (OSError, UnicodeDecodeError, ValueError, TypeError) as error:
                    self._write_json(
                        400,
                        {"error": "invalid_output", "message": str(error)},
                    )
                return
            if parsed.path == "/api/reviews":
                try:
                    if not self.server.local_path_import_allowed:
                        raise VibeWikiError(
                            ErrorCode.PERMISSION_DENIED,
                            "review state is available only on a loopback server",
                        )
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "review payload is empty or too large",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    if not isinstance(payload, dict):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "review payload is invalid",
                        )
                    with self.server.workspace_lock:
                        review, reviews = set_review(
                            self.server.workspace_root,
                            payload.get("subject"),
                            payload.get("status"),
                            payload.get("note"),
                        )
                    self._write_json(
                        200,
                        {
                            "counts": review_counts(reviews),
                            "review": review,
                            "reviews": reviews,
                            "saved": True,
                        },
                    )
                except VibeWikiError as error:
                    self._write_json(
                        422,
                        {"error": error.code.value, "message": error.message},
                    )
                except (OSError, UnicodeDecodeError, ValueError, TypeError) as error:
                    self._write_json(
                        400,
                        {"error": "invalid_output", "message": str(error)},
                    )
                return
            if parsed.path == "/api/intent":
                try:
                    if not self.server.local_path_import_allowed:
                        raise VibeWikiError(
                            ErrorCode.PERMISSION_DENIED,
                            "product intent setup is available only on a "
                            "loopback server",
                        )
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 16 * 1024:
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "product intent payload is empty or too large",
                        )
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                    if not isinstance(payload, dict):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "product intent payload is invalid",
                        )
                    if not self.server.rescan_lock.acquire(blocking=False):
                        raise VibeWikiError(
                            ErrorCode.INVALID_OUTPUT,
                            "a workspace scan is already in progress",
                        )
                    try:
                        with self.server.workspace_lock:
                            seed_path = self.server.workspace_root / SEED_FILENAME
                            previous_seed = (
                                seed_path.read_bytes() if seed_path.is_file() else None
                            )
                            try:
                                write_product_seed(self.server.workspace_root, payload)
                                result = rescan_repository(self.server.workspace_root)
                            except Exception:
                                if previous_seed is None:
                                    try:
                                        seed_path.unlink(missing_ok=True)
                                    except OSError:
                                        pass
                                else:
                                    seed_path.write_bytes(previous_seed)
                                raise
                            self.server.workspace_artifact = _artifact(
                                self.server.workspace_root
                            )
                            intent = compare_product_intent(
                                self.server.workspace_root,
                                self.server.workspace_artifact,
                            )
                    finally:
                        self.server.rescan_lock.release()
                    self._write_json(
                        200,
                        {"saved": True, "intent": intent, "rescan": result},
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
                    imported = _persist_imported_workspace(
                        self.server,
                        imported,
                        provider="github",
                        label=(
                            imported.build_summary.get("import_source", {}).get(
                                "repository", "github-source"
                            )
                        ),
                        origin={
                            "url": repository_url,
                            "ref": ref or "HEAD",
                        },
                    )
                    with self.server.workspace_lock:
                        old_workspace = self.server.imported_workspace
                        self.server.workspace_root = imported.root
                        self.server.workspace_artifact = _artifact(imported.root)
                        self.server.workspace_available = True
                        self.server.imported_workspace = imported
                        self.server.workspace_id = imported.build_summary.get(
                            "import_source", {}
                        ).get("workspace_id")
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
                    local_path = payload["path"].strip()
                    imported = import_local_workspace(local_path)
                    with self.server.workspace_lock:
                        # Validate and promote while the workspace lock is
                        # held. Readers therefore keep seeing the previous
                        # artifact until the complete replacement is ready.
                        _artifact(imported.root)
                        imported = _persist_imported_workspace(
                            self.server,
                            imported,
                            provider="local-path",
                            label=Path(local_path).expanduser().name
                            or imported.root.name,
                            origin={
                                "local_path": str(
                                    Path(local_path).expanduser().absolute()
                                )
                            },
                        )
                        old_workspace = self.server.imported_workspace
                        self.server.workspace_root = imported.root
                        self.server.workspace_artifact = _artifact(imported.root)
                        self.server.workspace_available = True
                        self.server.imported_workspace = imported
                        self.server.workspace_id = imported.build_summary.get(
                            "import_source", {}
                        ).get("workspace_id")
                        self.server.workspace_source = {
                            **imported.build_summary.get("import_source", {}),
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
                    self.server.workspace_store.save_llm_preferences(
                        provider=settings.provider,
                        model=settings.model,
                        base_url=settings.base_url,
                    )
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
                if not self.server.local_path_import_allowed:
                    raise VibeWikiError(
                        ErrorCode.PERMISSION_DENIED,
                        "source import is available only on a loopback server",
                    )
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
                imported = _persist_imported_workspace(
                    self.server,
                    imported,
                    provider="browser-folder",
                    label=imported.root.name,
                    origin={},
                )
                with self.server.workspace_lock:
                    old_workspace = self.server.imported_workspace
                    self.server.workspace_root = imported.root
                    self.server.workspace_artifact = _artifact(imported.root)
                    self.server.workspace_available = True
                    self.server.imported_workspace = imported
                    self.server.workspace_id = imported.build_summary.get(
                        "import_source", {}
                    ).get("workspace_id")
                    self.server.workspace_source = {
                        **imported.build_summary.get("import_source", {}),
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

        def do_HEAD(self) -> None:  # noqa: N802
            if not self._require_authorization():
                return
            super().do_HEAD()

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._require_authorization():
                return
            parsed = urlparse(self.path)
            prefix = "/api/workspaces/"
            if not parsed.path.startswith(prefix):
                self.send_error(404, "not found")
                return
            workspace_id = unquote(parsed.path.removeprefix(prefix))
            try:
                if workspace_id == getattr(self.server, "workspace_id", None):
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "open another workspace before forgetting the active one",
                    )
                self.server.workspace_store.forget(workspace_id)
                self._write_json(200, {"forgotten": True, "workspace_id": workspace_id})
            except VibeWikiError as error:
                self._write_json(
                    422, {"error": error.code.value, "message": error.message}
                )

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._require_authorization():
                return
            self.send_error(405, "method not allowed")

        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            body = canonical_json(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def end_headers(self) -> None:
            if getattr(self, "_bootstrap_authorized", False):
                token = quote(self.server.access_token or "", safe="")
                self.send_header(
                    "Set-Cookie",
                    f"vibewiki_access={token}; Path=/; HttpOnly; SameSite=Strict",
                )
            self.send_header("Cache-Control", "no-store, max-age=0")
            super().end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.workspace_root = root
    server.workspace_artifact = artifact
    server.workspace_available = not onboarding
    server.llm_settings = _initial_llm_settings(workspace_store)
    server.imported_workspace: ImportedWorkspace | None = None
    server.workspace_store = workspace_store
    server.workspace_id = workspace_id
    server.workspace_source = {
        "provider": "local-workspace",
        "label": root.name,
    }
    if workspace_id is not None:
        server.workspace_source.update(
            next(
                item for item in workspace_store.public() if item["id"] == workspace_id
            )
        )
        server.workspace_source["workspace_id"] = workspace_id
    server.bind_is_loopback = loopback_host
    server.share_mode = bool(share)
    server.auth_required = bool(share and not loopback_host)
    server.access_token = secrets.token_urlsafe(32) if share else None
    server.local_path_import_allowed = loopback_host
    server.github_import_allowed = server.local_path_import_allowed
    server.workspace_lock = threading.RLock()
    server.rescan_lock = threading.Lock()
    server.auto_analyzed = auto_analyzed
    return server


def serve_repository(
    repository: str | Path | None,
    host: str = "127.0.0.1",
    port: int = 4173,
    *,
    share: bool = False,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_base_url: str | None = None,
    state_dir: str | Path | None = None,
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
        server = create_server(repository, host, port, share=share, state_dir=state_dir)
        actual_port = server.server_address[1]
        ready = {
            "bind": host,
            "command": "serve",
            "port": actual_port,
            "artifact_root": MANIFEST_DIRECTORY,
            "auto_analyzed": server.auto_analyzed,
            "schema_version": SCHEMA_VERSION,
            "status": "ready",
            "share": server.share_mode,
            "auth": "bearer" if server.auth_required else "loopback",
        }
        if server.access_token is not None:
            # This is the one intentional token disclosure: the local ready
            # event lets the person who launched the process connect. The
            # token is never copied into HTML, artifacts, or request logs.
            ready["access_token"] = server.access_token
            ready["access_url"] = (
                f"http://{host}:{actual_port}/?access_token="
                f"{quote(server.access_token, safe='')}"
            )
        print(canonical_json(ready), flush=True)
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

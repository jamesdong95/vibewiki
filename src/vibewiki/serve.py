"""Loopback HTTP API and viewer for built local VibeWiki artifacts."""

from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import ANALYZER_VERSION, SCHEMA_VERSION
from .config import MANIFEST_DIRECTORY
from .discovery.manifest import canonical_json
from .errors import ErrorCode, VibeWikiError
from .importer import (
    MAX_IMPORT_BYTES,
    ImportedWorkspace,
    cleanup_workspace,
    import_uploaded_workspace,
)
from .llm import (
    MAX_QUESTION_CHARS,
    LLMSettings,
    ask_repository,
    configure_llm,
    llm_status,
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


def _artifact_nodes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [_node(fact) for fact in artifact["facts"]]
    existing = {node["id"] for node in nodes}
    for module in artifact.get("modules", []):
        if module["id"] in existing:
            continue
        attributes = module["attributes"]
        nodes.append(
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
            }
        )
        existing.add(module["id"])
    for node_group in (artifact.get("packages", []), artifact.get("symbols", [])):
        for item in node_group:
            if item["id"] in existing:
                continue
            attributes = item["attributes"]
            nodes.append(
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
                }
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
            }
        )
    return sorted(nodes, key=lambda node: (node["kind"], node["id"]))


def _artifact_edges(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return (
        artifact["relations"]
        + artifact.get("module_edges", [])
        + artifact.get("package_edges", [])
        + artifact.get("symbol_edges", [])
    )


def _safe_source_path(value: str) -> str:
    raw = unquote(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "source path was not found")
    return path.as_posix()


def _source_payload(
    root: Path, artifact: dict[str, Any], params: dict[str, list[str]]
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
            return {"binary": True, "lines": [], "path": requested, "source": item}
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
    }


def api_payload(
    root: Path,
    artifact: dict[str, Any],
    path: str,
    query: str = "",
    params: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    facts = artifact["facts"]
    nodes = _artifact_nodes(artifact)
    edges = _artifact_edges(artifact)
    params = params or {}
    if path == "/api/summary":
        return {
            "project": artifact["fixture"],
            "analyzer_version": ANALYZER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "counts": {
                "facts": len(facts),
                "relations": len(artifact["relations"]),
                "unknowns": len(artifact["unknowns"]),
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
        }
    if path == "/api/nodes":
        return {"nodes": nodes, "unknowns": artifact["unknowns"]}
    if path == "/api/edges":
        return {"edges": edges}
    if path == "/api/files":
        return {"files": artifact.get("inventory", {}).get("files", [])}
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
        return _source_payload(root, artifact, params)
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
                (item for item in artifact["unknowns"] if item["subject"] == subject),
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
    artifact = _artifact(root)
    viewer = Path(__file__).resolve().parents[2] / "viewer"

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(viewer), **kwargs)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                try:
                    if parsed.path == "/api/llm/status":
                        payload = llm_status(self.server.llm_settings)
                    else:
                        payload = api_payload(
                            self.server.workspace_root,
                            self.server.workspace_artifact,
                            parsed.path,
                            parse_qs(parsed.query).get("q", [""])[0],
                            parse_qs(parsed.query),
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
                old_workspace = self.server.imported_workspace
                self.server.workspace_root = imported.root
                self.server.workspace_artifact = _artifact(imported.root)
                self.server.imported_workspace = imported
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

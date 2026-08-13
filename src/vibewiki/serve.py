"""Loopback HTTP API and viewer for built local VibeWiki artifacts."""

from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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


def api_payload(
    root: Path, artifact: dict[str, Any], path: str, query: str = ""
) -> dict[str, Any]:
    facts = artifact["facts"]
    nodes = [_node(fact) for fact in facts]
    edges = artifact["relations"]
    if path == "/api/summary":
        return {
            "project": artifact["fixture"],
            "analyzer_version": ANALYZER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "counts": {
                "facts": len(facts),
                "relations": len(edges),
                "unknowns": len(artifact["unknowns"]),
                "scanned_files": len(
                    json.loads(
                        (root / MANIFEST_DIRECTORY / "manifest.json").read_text()
                    )["files"]
                ),
            },
            "evidence": sum(len(fact["evidence"]) for fact in facts)
            + sum(len(edge["evidence"]) for edge in edges),
            "status": "ready",
        }
    if path == "/api/nodes":
        return {"nodes": nodes, "unknowns": artifact["unknowns"]}
    if path == "/api/edges":
        return {"edges": edges}
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
                    payload = api_payload(
                        self.server.workspace_root,
                        self.server.workspace_artifact,
                        parsed.path,
                        parse_qs(parsed.query).get("q", [""])[0],
                    )
                except KeyError:
                    self.send_error(404, "not found")
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
    server.imported_workspace: ImportedWorkspace | None = None
    return server


def serve_repository(
    repository: str | Path, host: str = "127.0.0.1", port: int = 4173
) -> None:
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
    try:
        server.serve_forever()
    finally:
        if server.imported_workspace is not None:
            cleanup_workspace(server.imported_workspace)
        server.server_close()


__all__ = ["api_payload", "create_server", "serve_repository"]

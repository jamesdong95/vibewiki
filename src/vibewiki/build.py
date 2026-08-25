"""Deterministic artifact builder for the local VibeWiki knowledge view."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .analyzer import analyze_repository, build_module_graph, write_json
from .config import MANIFEST_DIRECTORY
from .discovery.hashing import hash_file
from .discovery.manifest import canonical_json
from .errors import ErrorCode, VibeWikiError
from .history import record_graph_snapshot
from .intent import compare_product_intent
from .profile import build_project_profile


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST_DIRECTORY / "manifest.json"
    if not path.is_file():
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "scan output is missing; run scan first"
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "scan manifest is invalid"
        ) from error
    if manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(
        manifest.get("files"), list
    ):
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "scan manifest is invalid")
    return manifest


def _load_inventory(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    path = root / MANIFEST_DIRECTORY / "inventory.json"
    if path.is_file():
        try:
            inventory = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT, "scan inventory is invalid"
            ) from error
        if not isinstance(inventory.get("files"), list):
            raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "scan inventory is invalid")
        return inventory
    # Builds created by the first phase remain valid and get a source-only
    # inventory until the next scan.
    items = tuple(
        {
            "kind": "source",
            "language": item["language"],
            "mime": "text/plain",
            "path": item["path"],
            "sha256": item["sha256"],
            "size": item["size"],
        }
        for item in manifest["files"]
    )
    return {"files": list(items), "schema_version": 1}


def _write_wiki(output: Path, artifact: dict[str, Any]) -> list[str]:
    wiki = output / "wiki"
    wiki.mkdir(exist_ok=True)
    facts = artifact["facts"]
    relations = artifact["relations"] + artifact.get("module_edges", [])
    relations += artifact.get("package_edges", []) + artifact.get("symbol_edges", [])
    unknowns = artifact["unknowns"] + artifact.get("intent", {}).get("gaps", [])
    rows = [
        "# VibeWiki",
        "",
        f"Generated from `{artifact['fixture']}` by the deterministic local analyzer.",
        "",
        "## Facts",
        "",
        "| Kind | Subject | Status | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for fact in facts:
        evidence = ", ".join(
            f"{item['path']}:{item['line_start']}" for item in fact["evidence"]
        )
        rows.append(
            f"| {fact['kind']} | `{fact['semantic_key']}` | "
            f"{fact['status']} | {evidence} |"
        )
    rows.extend(["", "## Unknowns", ""])
    if unknowns:
        rows.extend(f"- `{item['subject']}` — {item['reason']}" for item in unknowns)
    else:
        rows.append("- None observed.")
    (wiki / "index.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    route_lines = ["# Routes", "", "| Route | Kind | Evidence |", "| --- | --- | --- |"]
    for fact in facts:
        if fact["kind"] == "route":
            route_lines.append(
                f"| `{fact['attributes']['path']}` | "
                f"{fact['semantic_key'].split(':')[1]} | "
                f"{fact['evidence'][0]['path']}:{fact['evidence'][0]['line_start']} |"
            )
    (wiki / "routes.md").write_text("\n".join(route_lines) + "\n", encoding="utf-8")

    flow_lines = [
        "# Flows",
        "",
        "| Source | Relation | Target |",
        "| --- | --- | --- |",
    ]
    for edge in relations:
        flow_lines.append(
            f"| `{edge['source']}` | {edge['relation']} | `{edge['target']}` |"
        )
    (wiki / "flows.md").write_text("\n".join(flow_lines) + "\n", encoding="utf-8")

    data_lines = ["# Data model", "", "| Model | Evidence |", "| --- | --- |"]
    for fact in facts:
        if fact["kind"] == "data_entity":
            data_lines.append(
                f"| `{fact['attributes']['model']}` | "
                f"{fact['evidence'][0]['path']}:{fact['evidence'][0]['line_start']} |"
            )
    (wiki / "data-model.md").write_text("\n".join(data_lines) + "\n", encoding="utf-8")

    graph = ["graph TD"]
    for edge in relations:
        source = edge["source"].replace(":", "_").replace("/", "_")
        target = edge["target"].replace(":", "_").replace("/", "_")
        graph.append(
            f'  {source}["{edge["source"]}"] '
            f'-->|{edge["relation"]}| {target}["{edge["target"]}"]'
        )
    (wiki / "graph.mmd").write_text("\n".join(graph) + "\n", encoding="utf-8")
    return [
        f"{MANIFEST_DIRECTORY}/wiki/{name}"
        for name in ("index.md", "routes.md", "flows.md", "data-model.md", "graph.mmd")
    ]


def _write_graph_db(path: Path, artifact: dict[str, Any]) -> None:
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE nodes (semantic_key TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "status TEXT NOT NULL, attributes_json TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE edges (source TEXT NOT NULL, relation TEXT NOT NULL, "
            "target TEXT NOT NULL, status TEXT NOT NULL, evidence_json TEXT NOT NULL, "
            "PRIMARY KEY (source, relation, target))"
        )
        db.execute(
            "CREATE TABLE unknowns (subject TEXT PRIMARY KEY, reason TEXT NOT NULL, "
            "status TEXT NOT NULL, evidence_json TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE files (path TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "language TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, "
            "sha256 TEXT NOT NULL)"
        )
        for fact in artifact["facts"]:
            db.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, ?)",
                (
                    fact["semantic_key"],
                    fact["kind"],
                    fact["status"],
                    canonical_json(fact["attributes"]),
                ),
            )
        for module in artifact.get("modules", []):
            db.execute(
                "INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?)",
                (
                    module["id"],
                    module["kind"],
                    module["status"],
                    canonical_json(module["attributes"]),
                ),
            )
        for node_group in (artifact.get("packages", []), artifact.get("symbols", [])):
            for node in node_group:
                db.execute(
                    "INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?)",
                    (
                        node["id"],
                        node["kind"],
                        node["status"],
                        canonical_json(node["attributes"]),
                    ),
                )
        for edge in artifact["relations"]:
            db.execute(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?)",
                (
                    edge["source"],
                    edge["relation"],
                    edge["target"],
                    edge["status"],
                    canonical_json(edge["evidence"]),
                ),
            )
        for edge in artifact.get("module_edges", []):
            db.execute(
                "INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)",
                (
                    edge["source"],
                    edge["relation"],
                    edge["target"],
                    edge["status"],
                    canonical_json(edge["evidence"]),
                ),
            )
        for edge_group in (
            artifact.get("package_edges", []),
            artifact.get("symbol_edges", []),
        ):
            for edge in edge_group:
                db.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)",
                    (
                        edge["source"],
                        edge["relation"],
                        edge["target"],
                        edge["status"],
                        canonical_json(edge["evidence"]),
                    ),
                )
        for item in artifact["unknowns"]:
            db.execute(
                "INSERT INTO unknowns VALUES (?, ?, ?, ?)",
                (
                    item["subject"],
                    item["reason"],
                    item["status"],
                    canonical_json(item["evidence"]),
                ),
            )
        for item in artifact.get("inventory", {}).get("files", []):
            db.execute(
                "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item["path"],
                    item["kind"],
                    item["language"],
                    item["mime"],
                    item["size"],
                    item["sha256"],
                ),
            )


def build_repository(repository: str | Path) -> dict[str, Any]:
    root = Path(repository).absolute()
    manifest = _load_manifest(root)
    inventory = _load_inventory(root, manifest)
    facts_artifact = analyze_repository(root, manifest)
    graph_artifact = {
        **facts_artifact,
        **build_module_graph(root, manifest, inventory),
        "inventory": inventory,
    }
    graph_artifact["profile"] = build_project_profile(
        manifest,
        inventory,
        graph_artifact["facts"],
        graph_artifact["packages"],
    )
    intent = compare_product_intent(root, graph_artifact)
    graph_artifact["intent"] = intent
    output = root / MANIFEST_DIRECTORY
    write_json(output / "facts.json", facts_artifact)
    claims = {
        "schema_version": SCHEMA_VERSION,
        "claims": [],
        "unknowns": facts_artifact["unknowns"],
        "intent": intent,
    }
    write_json(output / "claims.json", claims)
    write_json(output / "intent.json", intent)
    sources = []
    for item in manifest["files"]:
        sources.append(
            {"path": item["path"], "sha256": item["sha256"], "size": item["size"]}
        )
    schema = root / "prisma/schema.prisma"
    if schema.is_file():
        sources.append(
            {
                "path": "prisma/schema.prisma",
                "sha256": hash_file(schema),
                "size": schema.stat().st_size,
            }
        )
    write_json(
        output / "sources.json",
        {
            "schema_version": SCHEMA_VERSION,
            "sources": sorted(sources, key=lambda item: item["path"]),
        },
    )
    graph = {"schema_version": SCHEMA_VERSION, **graph_artifact}
    write_json(output / "graph.json", graph)
    _write_graph_db(output / "graph.db", graph_artifact)
    wiki_paths = _write_wiki(output, graph_artifact)
    record_graph_snapshot(root, graph_artifact)
    paths = [
        f"{MANIFEST_DIRECTORY}/{name}"
        for name in (
            "manifest.json",
            "facts.json",
            "claims.json",
            "sources.json",
            "graph.json",
            "graph.db",
            "intent.json",
        )
    ] + wiki_paths
    return {
        "command": "build",
        "counts": {
            "facts": len(facts_artifact["facts"]),
            "relations": len(facts_artifact["relations"]),
            "scanned_files": len(manifest["files"]),
            "unknowns": len(facts_artifact["unknowns"]),
        },
        "outputs": paths,
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
    }


__all__ = ["build_repository"]

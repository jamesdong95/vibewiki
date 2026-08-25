"""Local scan history and post-build evidence staleness checks."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import MANIFEST_DIRECTORY, SCHEMA_VERSION
from .discovery.hashing import hash_file
from .discovery.manifest import canonical_json
from .errors import ErrorCode, VibeWikiError

HISTORY_FILENAME = "history.json"
GRAPH_INDEX_FILENAME = "graph-index.json"
MAX_SCAN_RUNS = 50
MAX_GRAPH_CHANGE_ITEMS = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _git(root: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def git_snapshot(root: Path) -> dict[str, str] | None:
    """Read commit metadata without contacting a remote or leaking source."""

    commit = _git(root, "rev-parse", "--verify", "HEAD")
    if commit is None:
        return None
    details = _git(root, "show", "-s", "--format=%an%x1f%aI%x1f%s", commit)
    if details is None:
        return {"commit": commit}
    author, authored_at, subject = (details.split("\x1f", 2) + [""])[:3]
    return {
        "author": author,
        "authored_at": authored_at,
        "commit": commit,
        "subject": subject,
    }


def _manifest_files(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not manifest or not isinstance(manifest.get("files"), list):
        return {}
    return {
        item["path"]: item
        for item in manifest["files"]
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def manifest_diff(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> dict[str, list[str]]:
    """Return deterministic file-level changes between two scan manifests."""

    before = _manifest_files(previous)
    after = _manifest_files(current)
    before_paths, after_paths = set(before), set(after)
    changed = sorted(
        path
        for path in before_paths & after_paths
        if before[path].get("sha256") != after[path].get("sha256")
        or before[path].get("size") != after[path].get("size")
        or before[path].get("language") != after[path].get("language")
    )
    return {
        "added": sorted(after_paths - before_paths),
        "changed": changed,
        "removed": sorted(before_paths - after_paths),
    }


def _history_path(root: Path) -> Path:
    return root / MANIFEST_DIRECTORY / HISTORY_FILENAME


def _graph_index_path(root: Path) -> Path:
    return root / MANIFEST_DIRECTORY / GRAPH_INDEX_FILENAME


def load_history(root: Path) -> dict[str, Any]:
    path = _history_path(root)
    if not path.is_file():
        return {"runs": [], "schema_version": 1}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "scan history is invalid"
        ) from error
    if not isinstance(value, dict) or not isinstance(value.get("runs"), list):
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "scan history is invalid")
    return value


def _write_history(root: Path, history: dict[str, Any]) -> None:
    output = root / MANIFEST_DIRECTORY
    output.mkdir(exist_ok=True)
    _history_path(root).write_text(canonical_json(history), encoding="utf-8")


def _evidence_paths(item: dict[str, Any]) -> list[str]:
    return sorted(
        {
            evidence.get("path")
            for evidence in item.get("evidence", [])
            if isinstance(evidence, dict) and isinstance(evidence.get("path"), str)
        }
    )


def _node_index_record(item: dict[str, Any], node_id: str) -> dict[str, Any]:
    attributes = item.get("attributes", {})
    evidence = item.get("evidence", [])
    signature = {
        "attributes": attributes,
        "evidence": evidence,
        "kind": item.get("kind"),
        "status": item.get("status"),
    }
    title = (
        attributes.get("path")
        or attributes.get("name")
        or attributes.get("model")
        or node_id
    )
    return {
        "digest": hashlib.sha256(
            canonical_json(signature).encode("utf-8")
        ).hexdigest(),
        "id": node_id,
        "kind": item.get("kind", "unknown"),
        "paths": _evidence_paths(item),
        "status": item.get("status", "unknown"),
        "title": str(title),
    }


def _edge_index_record(item: dict[str, Any]) -> dict[str, Any]:
    source = str(item.get("source", ""))
    relation = str(item.get("relation", ""))
    target = str(item.get("target", ""))
    signature = {
        "evidence": item.get("evidence", []),
        "relation": relation,
        "source": source,
        "status": item.get("status"),
        "target": target,
    }
    return {
        "digest": hashlib.sha256(
            canonical_json(signature).encode("utf-8")
        ).hexdigest(),
        "key": f"{source}\x1f{relation}\x1f{target}",
        "paths": _evidence_paths(item),
        "relation": relation,
        "source": source,
        "status": item.get("status", "unknown"),
        "target": target,
    }


def graph_index(artifact: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    """Create a compact, source-free index used for deterministic graph diffs."""

    nodes: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for group in ("facts", "modules", "packages", "symbols"):
        for item in artifact.get(group, []):
            node_id = str(item.get("id", item.get("semantic_key", "")))
            if not node_id or node_id in existing_ids:
                continue
            existing_ids.add(node_id)
            nodes.append(_node_index_record(item, node_id))
    module_paths = {
        item.get("attributes", {}).get("path")
        for item in artifact.get("modules", [])
    }
    for item in artifact.get("inventory", {}).get("files", []):
        path = item.get("path")
        node_id = f"file:{path}"
        if not isinstance(path, str) or path in module_paths or node_id in existing_ids:
            continue
        existing_ids.add(node_id)
        nodes.append(
            _node_index_record(
                {
                    "attributes": item,
                    "evidence": [{"path": path}],
                    "kind": "file",
                    "status": "verified",
                },
                node_id,
            )
        )
    edges = []
    seen_edges: set[str] = set()
    for group in ("relations", "module_edges", "package_edges", "symbol_edges"):
        for item in artifact.get(group, []):
            record = _edge_index_record(item)
            if record["key"] in seen_edges:
                continue
            seen_edges.add(record["key"])
            edges.append(record)
    return {
        "edges": sorted(edges, key=lambda item: item["key"]),
        "nodes": sorted(nodes, key=lambda item: item["id"]),
        "run_id": run_id,
        "schema_version": 1,
    }


def _read_graph_index(root: Path) -> dict[str, Any] | None:
    path = _graph_index_path(root)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), list):
        return None
    if not isinstance(value.get("edges"), list):
        return None
    return value


def _bounded(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    return items[:MAX_GRAPH_CHANGE_ITEMS], len(items) > MAX_GRAPH_CHANGE_ITEMS


def graph_diff(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    from_run_id: str | None = None,
    to_run_id: str | None = None,
) -> dict[str, Any]:
    """Compare compact graph indexes while bounding UI payload size."""

    before_nodes = {item["id"]: item for item in (previous or {}).get("nodes", [])}
    after_nodes = {item["id"]: item for item in current.get("nodes", [])}
    before_edges = {item["key"]: item for item in (previous or {}).get("edges", [])}
    after_edges = {item["key"]: item for item in current.get("edges", [])}

    added_nodes = [
        after_nodes[key] for key in sorted(set(after_nodes) - set(before_nodes))
    ]
    removed_nodes = [
        before_nodes[key] for key in sorted(set(before_nodes) - set(after_nodes))
    ]
    changed_nodes = [
        {"after": after_nodes[key], "before": before_nodes[key]}
        for key in sorted(set(before_nodes) & set(after_nodes))
        if before_nodes[key].get("digest") != after_nodes[key].get("digest")
    ]
    added_edges = [
        after_edges[key] for key in sorted(set(after_edges) - set(before_edges))
    ]
    removed_edges = [
        before_edges[key] for key in sorted(set(before_edges) - set(after_edges))
    ]
    changed_edges = [
        {"after": after_edges[key], "before": before_edges[key]}
        for key in sorted(set(before_edges) & set(after_edges))
        if before_edges[key].get("digest") != after_edges[key].get("digest")
    ]
    groups = {
        "nodes_added": added_nodes,
        "nodes_changed": changed_nodes,
        "nodes_removed": removed_nodes,
        "edges_added": added_edges,
        "edges_changed": changed_edges,
        "edges_removed": removed_edges,
    }
    bounded_groups: dict[str, list[dict[str, Any]]] = {}
    truncated = False
    for name, items in groups.items():
        bounded_groups[name], was_truncated = _bounded(items)
        truncated = truncated or was_truncated
    counts = {name: len(items) for name, items in groups.items()}
    changed = any(counts.values())
    return {
        **bounded_groups,
        "counts": counts,
        "from_run_id": from_run_id,
        "status": (
            "baseline"
            if previous is None
            else "changed"
            if changed
            else "unchanged"
        ),
        "to_run_id": to_run_id,
        "truncated": truncated,
    }


def record_graph_snapshot(root: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    """Persist a compact graph index and attach its diff to the current scan run."""

    history = load_history(root)
    current_run = history.get("runs", [None])[0]
    run_id = current_run.get("run_id") if isinstance(current_run, dict) else None
    previous = _read_graph_index(root)
    current = graph_index(artifact, run_id)
    diff = graph_diff(
        previous,
        current,
        from_run_id=previous.get("run_id") if previous else None,
        to_run_id=run_id,
    )
    _graph_index_path(root).write_text(canonical_json(current), encoding="utf-8")
    if isinstance(current_run, dict):
        current_run["graph_changes"] = diff
        history["runs"][0] = current_run
        _write_history(root, history)
    return diff


def record_scan(
    root: Path,
    manifest: dict[str, Any],
    previous_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Append one bounded scan run and return the persisted run record."""

    scanned_at = _now()
    run = {
        "analyzer_version": manifest.get("analyzer_version"),
        "changes": manifest_diff(previous_manifest, manifest),
        "commit": git_snapshot(root),
        "files": len(manifest.get("files", [])),
        "run_id": scanned_at,
        "scanned_at": scanned_at,
        "schema_version": SCHEMA_VERSION,
    }
    history = load_history(root)
    history["runs"] = [run, *history.get("runs", [])][:MAX_SCAN_RUNS]
    history["schema_version"] = 1
    _write_history(root, history)
    return run


def _read_manifest(root: Path) -> dict[str, Any] | None:
    path = root / MANIFEST_DIRECTORY / "manifest.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "scan manifest is invalid"
        ) from error
    return value if isinstance(value, dict) else None


def previous_manifest(root: Path) -> dict[str, Any] | None:
    """Load the manifest that will be replaced by the next scan."""

    return _read_manifest(root)


def stale_files(root: Path, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare built inventory hashes with current disk state."""

    result = []
    for item in artifact.get("inventory", {}).get("files", []):
        relative = item.get("path")
        if not isinstance(relative, str):
            continue
        path = root / Path(relative)
        if not path.is_file() or path.is_symlink():
            result.append(
                {
                    "path": relative,
                    "reason": "source file was removed after the last build",
                    "status": "removed",
                }
            )
            continue
        try:
            digest = hash_file(path)
        except (OSError, ValueError):
            result.append(
                {
                    "path": relative,
                    "reason": "source file could not be hashed after the last build",
                    "status": "unavailable",
                }
            )
            continue
        if digest != item.get("sha256"):
            result.append(
                {
                    "path": relative,
                    "reason": "source file changed after the last build",
                    "status": "changed",
                }
            )
    return sorted(result, key=lambda item: item["path"])


def history_for_subject(root: Path, subject: str) -> dict[str, Any]:
    """Return scan runs touching a path or evidence-bearing graph subject."""

    root = Path(root)
    paths = {subject}
    graph_path = root / MANIFEST_DIRECTORY / "graph.json"
    if graph_path.is_file():
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT, "build output is invalid"
            ) from error
        for group in ("facts", "modules", "packages", "symbols"):
            for node in graph.get(group, []):
                if node.get("id", node.get("semantic_key")) != subject:
                    continue
                paths.update(
                    item.get("path")
                    for item in node.get("evidence", [])
                    if isinstance(item, dict) and isinstance(item.get("path"), str)
                )
    history = load_history(root)
    runs = []
    for run in history.get("runs", []):
        changes = run.get("changes", {})
        touched = set().union(*(set(changes.get(kind, [])) for kind in changes))
        if paths & touched:
            runs.append(run)
    return {"subject": subject, "paths": sorted(paths), "runs": runs}


__all__ = [
    "GRAPH_INDEX_FILENAME",
    "HISTORY_FILENAME",
    "MAX_SCAN_RUNS",
    "MAX_GRAPH_CHANGE_ITEMS",
    "git_snapshot",
    "graph_diff",
    "graph_index",
    "history_for_subject",
    "load_history",
    "manifest_diff",
    "previous_manifest",
    "record_graph_snapshot",
    "record_scan",
    "stale_files",
]

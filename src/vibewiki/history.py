"""Local scan history and post-build evidence staleness checks."""

from __future__ import annotations

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
MAX_SCAN_RUNS = 50


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
    "HISTORY_FILENAME",
    "MAX_SCAN_RUNS",
    "git_snapshot",
    "history_for_subject",
    "load_history",
    "manifest_diff",
    "previous_manifest",
    "record_scan",
    "stale_files",
]

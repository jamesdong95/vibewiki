"""Local scan history and post-build evidence staleness checks."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .config import MANIFEST_DIRECTORY, SCHEMA_VERSION
from .discovery.hashing import hash_file
from .discovery.manifest import canonical_json
from .errors import ErrorCode, VibeWikiError

HISTORY_FILENAME = "history.json"
GRAPH_INDEX_FILENAME = "graph-index.json"
SOURCE_DIFF_FILENAME = "source-diff.json"
SOURCE_SNAPSHOT_DIRECTORY = "source-snapshot"
SOURCE_SNAPSHOT_INDEX_FILENAME = "index.json"
MAX_SCAN_RUNS = 50
MAX_GRAPH_CHANGE_ITEMS = 200
MAX_SOURCE_DIFF_FILES = 100
MAX_SOURCE_SNAPSHOT_FILE_BYTES = 512 * 1024
MAX_SOURCE_SNAPSHOT_BYTES = 20 * 1024 * 1024
MAX_SOURCE_DIFF_LINES_PER_FILE = 400
MAX_SOURCE_DIFF_CHARS_PER_FILE = 20_000


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


def _atomic_write_text(path: Path, value: str) -> None:
    """Write a local artifact atomically without exposing a partial JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=str(path.parent)
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _source_diff_path(root: Path) -> Path:
    return root / MANIFEST_DIRECTORY / SOURCE_DIFF_FILENAME


def _source_snapshot_path(root: Path) -> Path:
    return root / MANIFEST_DIRECTORY / SOURCE_SNAPSHOT_DIRECTORY


def _safe_relative_source_path(value: str) -> str | None:
    """Normalize generated manifest paths while rejecting path escapes."""

    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
    ):
        return None
    return path.as_posix()


def _source_absolute_path(root: Path, relative: str) -> Path | None:
    safe = _safe_relative_source_path(relative)
    if safe is None:
        return None
    candidate = root / Path(safe)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _read_utf8_source(
    root: Path,
    relative: str,
    *,
    max_bytes: int = MAX_SOURCE_SNAPSHOT_FILE_BYTES,
) -> tuple[str | None, str | None, int]:
    """Read one bounded, regular, non-symlink UTF-8 source file."""

    candidate = _source_absolute_path(root, relative)
    if candidate is None:
        return None, "source path is invalid", 0
    try:
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return None, "source file is not a regular file", 0
        if metadata.st_size > max_bytes:
            return None, f"source file exceeds {max_bytes} byte snapshot limit", 0
        content = candidate.read_bytes()
    except FileNotFoundError:
        return None, "source file is not available", 0
    except PermissionError:
        return None, "source file could not be read", 0
    except OSError:
        return None, "source file could not be read", 0
    if len(content) > max_bytes:
        return (
            None,
            f"source file exceeds {max_bytes} byte snapshot limit",
            len(content),
        )
    if b"\x00" in content:
        return None, "source file is binary", len(content)
    try:
        return content.decode("utf-8"), None, len(content)
    except UnicodeDecodeError:
        return None, "source file is not valid UTF-8", len(content)


def _snapshot_file_name(relative: str) -> str:
    return f"{hashlib.sha256(relative.encode('utf-8')).hexdigest()}.txt"


def _read_source_snapshot(root: Path) -> tuple[dict[str, str] | None, str | None]:
    """Read the last successful source snapshot, never following arbitrary paths."""

    directory = _source_snapshot_path(root)
    index_path = directory / SOURCE_SNAPSHOT_INDEX_FILENAME
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or index_path.is_symlink()
        or not index_path.is_file()
    ):
        return None, "source snapshot is not available; run build before rescanning"
    try:
        value = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None, "source snapshot is invalid"
    if not isinstance(value, dict) or not isinstance(value.get("files"), list):
        return None, "source snapshot is invalid"
    if len(value["files"]) > MAX_SOURCE_DIFF_FILES:
        return None, "source snapshot exceeds its file limit"
    result: dict[str, str] = {}
    captured_bytes = 0
    for item in value["files"]:
        if not isinstance(item, dict):
            return None, "source snapshot is invalid"
        relative = item.get("path")
        file_name = item.get("file")
        if not isinstance(relative, str) or not isinstance(file_name, str):
            return None, "source snapshot is invalid"
        safe = _safe_relative_source_path(relative)
        if safe is None or file_name != _snapshot_file_name(safe):
            return None, "source snapshot is invalid"
        snapshot_file = directory / file_name
        try:
            if snapshot_file.is_symlink() or not snapshot_file.is_file():
                return None, "source snapshot is invalid"
            content = snapshot_file.read_bytes()
        except (OSError, UnicodeDecodeError):
            return None, "source snapshot is invalid"
        if (
            len(content) > MAX_SOURCE_SNAPSHOT_FILE_BYTES
            or captured_bytes + len(content) > MAX_SOURCE_SNAPSHOT_BYTES
            or b"\x00" in content
        ):
            return None, "source snapshot is invalid"
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return None, "source snapshot is invalid"
        result[safe] = text
        captured_bytes += len(content)
    return result, None


def _snapshot_summary(root: Path) -> dict[str, Any]:
    directory = _source_snapshot_path(root)
    index_path = directory / SOURCE_SNAPSHOT_INDEX_FILENAME
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or index_path.is_symlink()
        or not index_path.is_file()
    ):
        return {"captured_files": 0, "captured_bytes": 0, "truncated": False}
    try:
        value = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {"captured_files": 0, "captured_bytes": 0, "truncated": True}
    if not isinstance(value, dict):
        return {"captured_files": 0, "captured_bytes": 0, "truncated": True}
    files = value.get("files", [])
    try:
        captured_bytes = int(value.get("captured_bytes", 0) or 0)
    except (TypeError, ValueError):
        captured_bytes = 0
    return {
        "captured_files": len(files) if isinstance(files, list) else 0,
        "captured_bytes": captured_bytes,
        "truncated": bool(value.get("truncated", False)),
    }


def _line_record(
    kind: str,
    old_number: int | None,
    new_number: int | None,
    text: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "new_number": new_number,
        "old_number": old_number,
        "text": text,
    }


def _diff_hunks(before: str, after: str) -> tuple[list[dict[str, Any]], bool, int]:
    """Create bounded, line-numbered hunks without embedding full source files."""

    old_lines = before.splitlines()
    new_lines = after.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    groups = list(matcher.get_grouped_opcodes(3))
    if not groups and old_lines != new_lines:
        groups = [[("replace", 0, len(old_lines), 0, len(new_lines))]]

    hunks: list[dict[str, Any]] = []
    output_lines = 0
    output_chars = 0
    truncated = False
    for group in groups:
        if output_lines >= MAX_SOURCE_DIFF_LINES_PER_FILE:
            truncated = True
            break
        first = group[0]
        last = group[-1]
        old_start = first[1] + 1 if first[2] > first[1] else first[1]
        new_start = first[3] + 1 if first[4] > first[3] else first[3]
        hunk_lines: list[dict[str, Any]] = []
        hunk_truncated = False
        for (
            tag,
            old_start_index,
            old_end_index,
            new_start_index,
            new_end_index,
        ) in group:
            if tag == "equal":
                entries = (
                    ("context", old_number, new_number, old_lines[old_number - 1])
                    for old_number, new_number in zip(
                        range(old_start_index + 1, old_end_index + 1),
                        range(new_start_index + 1, new_end_index + 1),
                    )
                )
            elif tag == "delete":
                entries = (
                    ("removed", old_number, None, old_lines[old_number - 1])
                    for old_number in range(old_start_index + 1, old_end_index + 1)
                )
            elif tag == "insert":
                entries = (
                    ("added", None, new_number, new_lines[new_number - 1])
                    for new_number in range(new_start_index + 1, new_end_index + 1)
                )
            else:
                entries = (
                    ("removed", old_number, None, old_lines[old_number - 1])
                    for old_number in range(old_start_index + 1, old_end_index + 1)
                )
                entries = iter(
                    [
                        *entries,
                        *(
                            ("added", None, new_number, new_lines[new_number - 1])
                            for new_number in range(
                                new_start_index + 1, new_end_index + 1
                            )
                        ),
                    ]
                )
            for kind, old_number, new_number, text in entries:
                if output_lines >= MAX_SOURCE_DIFF_LINES_PER_FILE:
                    hunk_truncated = True
                    truncated = True
                    break
                remaining_chars = MAX_SOURCE_DIFF_CHARS_PER_FILE - output_chars
                if remaining_chars <= 0:
                    hunk_truncated = True
                    truncated = True
                    break
                rendered = text[:remaining_chars]
                if len(rendered) < len(text):
                    hunk_truncated = True
                    truncated = True
                hunk_lines.append(_line_record(kind, old_number, new_number, rendered))
                output_lines += 1
                output_chars += len(rendered)
                if hunk_truncated:
                    break
            if hunk_truncated:
                break
        if hunk_lines:
            hunks.append(
                {
                    "lines": hunk_lines,
                    "new_count": last[4] - first[3],
                    "new_start": new_start,
                    "old_count": last[2] - first[1],
                    "old_start": old_start,
                    "truncated": hunk_truncated,
                }
            )
        if hunk_truncated:
            break
    return hunks, truncated, len(old_lines) + len(new_lines)


def _source_change_paths(changes: dict[str, list[str]]) -> list[tuple[str, str]]:
    return sorted(
        (path, status)
        for status in ("added", "changed", "removed")
        for path in changes.get(status, [])
    )


def _source_diff_payload(
    root: Path,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    changes = manifest_diff(previous, current)
    counts = {kind: len(changes[kind]) for kind in ("added", "changed", "removed")}
    if previous is None:
        return {
            "counts": {**counts, "files": 0, "available": 0, "unavailable": 0},
            "files": [],
            "limits": {
                "max_chars_per_file": MAX_SOURCE_DIFF_CHARS_PER_FILE,
                "max_diff_lines_per_file": MAX_SOURCE_DIFF_LINES_PER_FILE,
                "max_files": MAX_SOURCE_DIFF_FILES,
                "max_snapshot_bytes": MAX_SOURCE_SNAPSHOT_BYTES,
                "max_snapshot_file_bytes": MAX_SOURCE_SNAPSHOT_FILE_BYTES,
            },
            "schema_version": 1,
            "snapshot": _snapshot_summary(root),
            "status": "baseline",
            "truncated": False,
        }

    change_paths = _source_change_paths(changes)
    snapshot, snapshot_reason = _read_source_snapshot(root)
    files: list[dict[str, Any]] = []
    truncated = len(change_paths) > MAX_SOURCE_DIFF_FILES or bool(
        _snapshot_summary(root).get("truncated", False)
    )
    for relative, status in change_paths[:MAX_SOURCE_DIFF_FILES]:
        record: dict[str, Any] = {
            "available": False,
            "diff_lines": 0,
            "hunks": [],
            "new_line_count": 0,
            "old_line_count": 0,
            "path": relative,
            "status": status,
            "truncated": False,
        }
        if snapshot is None:
            record["reason"] = snapshot_reason or "source snapshot is unavailable"
            files.append(record)
            continue

        before = snapshot.get(relative)
        after: str | None = None
        reason: str | None = None
        if status in {"changed", "removed"} and before is None:
            reason = "previous source snapshot was not captured"
        if status in {"added", "changed"}:
            after, reason, _ = _read_utf8_source(root, relative)
        if reason is not None or (status in {"changed", "removed"} and before is None):
            record["reason"] = reason or "previous source snapshot was not captured"
            files.append(record)
            continue
        old_text = before or ""
        new_text = after or ""
        hunks, file_truncated, total_lines = _diff_hunks(old_text, new_text)
        record.update(
            {
                "available": True,
                "diff_lines": sum(len(hunk["lines"]) for hunk in hunks),
                "hunks": hunks,
                "new_line_count": len(new_text.splitlines()),
                "old_line_count": len(old_text.splitlines()),
                "truncated": file_truncated,
            }
        )
        if file_truncated:
            truncated = True
            record["reason"] = "diff output reached its per-file bound"
        if total_lines == 0 and status != "changed":
            record["reason"] = "file has no text lines"
        files.append(record)

    available = sum(1 for item in files if item.get("available"))
    unavailable = len(files) - available
    if truncated:
        aggregate_status = "truncated"
    elif snapshot is None:
        aggregate_status = "unavailable"
    elif not files:
        aggregate_status = "available"
    elif available:
        aggregate_status = "available"
    else:
        aggregate_status = "unavailable"
    return {
        "counts": {
            **counts,
            "available": available,
            "files": len(change_paths),
            "unavailable": unavailable + max(0, len(change_paths) - len(files)),
        },
        "files": files,
        "limits": {
            "max_chars_per_file": MAX_SOURCE_DIFF_CHARS_PER_FILE,
            "max_diff_lines_per_file": MAX_SOURCE_DIFF_LINES_PER_FILE,
            "max_files": MAX_SOURCE_DIFF_FILES,
            "max_snapshot_bytes": MAX_SOURCE_SNAPSHOT_BYTES,
            "max_snapshot_file_bytes": MAX_SOURCE_SNAPSHOT_FILE_BYTES,
        },
        "schema_version": 1,
        "snapshot": _snapshot_summary(root),
        "status": aggregate_status,
        "truncated": truncated,
    }


def source_diff_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the source-free shape safe for history and the summary API."""

    summary_files = []
    for item in payload.get("files", []):
        if not isinstance(item, dict):
            continue
        summary = {
            "available": bool(item.get("available", False)),
            "diff_lines": int(item.get("diff_lines", 0) or 0),
            "hunks": len(item.get("hunks", []))
            if isinstance(item.get("hunks"), list)
            else 0,
            "new_line_count": int(item.get("new_line_count", 0) or 0),
            "old_line_count": int(item.get("old_line_count", 0) or 0),
            "path": item.get("path", ""),
            "status": item.get("status", "changed"),
            "truncated": bool(item.get("truncated", False)),
        }
        if isinstance(item.get("reason"), str):
            summary["reason"] = item["reason"]
        summary_files.append(summary)
    result = {
        "counts": payload.get("counts", {}),
        "files": summary_files,
        "snapshot": payload.get("snapshot", {}),
        "status": payload.get("status", "unavailable"),
        "truncated": bool(payload.get("truncated", False)),
    }
    return result


def _write_source_diff(root: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(_source_diff_path(root), canonical_json(payload))


def record_source_diff(
    root: Path,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Persist the latest bounded diff before the manifest is replaced."""

    payload = _source_diff_payload(root, previous, current)
    _write_source_diff(root, payload)
    return payload


def load_source_diff(root: Path) -> dict[str, Any] | None:
    path = _source_diff_path(root)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "source diff is invalid"
        ) from error
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("files"), list)
        or len(value["files"]) > MAX_SOURCE_DIFF_FILES
    ):
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "source diff is invalid")
    for item in value["files"]:
        if not isinstance(item, dict) or _safe_relative_source_path(
            item.get("path", "")
        ) is None:
            raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "source diff is invalid")
    return value


def load_source_diff_summary(root: Path) -> dict[str, Any]:
    payload = load_source_diff(root)
    if payload is None:
        return {
            "counts": {
                "added": 0,
                "available": 0,
                "changed": 0,
                "files": 0,
                "removed": 0,
                "unavailable": 0,
            },
            "files": [],
            "snapshot": _snapshot_summary(root),
            "status": "unavailable",
            "truncated": False,
        }
    return source_diff_summary(payload)


def load_source_diff_detail(root: Path, relative: str) -> dict[str, Any]:
    payload = load_source_diff(root)
    if payload is None:
        raise VibeWikiError(
            ErrorCode.PATH_NOT_FOUND, "source diff is not available for this path"
        )
    safe = _safe_relative_source_path(relative)
    if safe is None:
        raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "source diff path was not found")
    item = next(
        (
            item
            for item in payload["files"]
            if isinstance(item, dict) and item.get("path") == safe
        ),
        None,
    )
    if item is None:
        raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "source diff path was not found")
    return {
        "path": safe,
        "status": payload.get("status", "unavailable"),
        "file": item,
        "truncated": bool(
            payload.get("truncated", False) or item.get("truncated", False)
        ),
    }


def _replace_snapshot_directory(staging: Path, target: Path) -> None:
    """Swap a complete snapshot directory while keeping failures recoverable."""

    backup: Path | None = None
    target_exists = target.exists() or target.is_symlink()
    try:
        if target_exists:
            backup = target.parent / f".{target.name}.backup-{uuid.uuid4().hex}"
            os.replace(target, backup)
        os.replace(staging, target)
    except OSError:
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        if backup is not None and backup.exists():
            os.replace(backup, target)
        raise
    finally:
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def record_source_snapshot(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Capture a bounded UTF-8 snapshot only after a build has completed."""

    output = root / MANIFEST_DIRECTORY
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".source-snapshot-", dir=output))
    entries: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    captured_bytes = 0
    manifest_files = [
        item
        for item in manifest.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ]
    manifest_files.sort(key=lambda item: item["path"])
    truncated = len(manifest_files) > MAX_SOURCE_DIFF_FILES
    try:
        for item in manifest_files:
            relative = item["path"]
            if len(entries) >= MAX_SOURCE_DIFF_FILES:
                if len(omitted) < MAX_SOURCE_DIFF_FILES:
                    omitted.append({"path": relative, "reason": "file limit reached"})
                continue
            text, reason, size = _read_utf8_source(root, relative)
            if reason is not None or text is None:
                truncated = True
                if len(omitted) < MAX_SOURCE_DIFF_FILES:
                    omitted.append(
                        {"path": relative, "reason": reason or "unavailable"}
                    )
                continue
            encoded = text.encode("utf-8")
            if captured_bytes + len(encoded) > MAX_SOURCE_SNAPSHOT_BYTES:
                truncated = True
                if len(omitted) < MAX_SOURCE_DIFF_FILES:
                    omitted.append({"path": relative, "reason": "byte limit reached"})
                continue
            file_name = _snapshot_file_name(relative)
            (staging / file_name).write_bytes(encoded)
            entries.append(
                {
                    "file": file_name,
                    "path": relative,
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "size": size,
                }
            )
            captured_bytes += len(encoded)
        index = {
            "captured_bytes": captured_bytes,
            "files": entries,
            "omitted": omitted,
            "omitted_count": max(0, len(manifest_files) - len(entries)),
            "schema_version": 1,
            "truncated": truncated,
        }
        _atomic_write_text(
            staging / SOURCE_SNAPSHOT_INDEX_FILENAME, canonical_json(index)
        )
        _replace_snapshot_directory(staging, _source_snapshot_path(root))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "captured_bytes": captured_bytes,
        "captured_files": len(entries),
        "omitted_files": max(0, len(manifest_files) - len(entries)),
        "truncated": truncated,
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
    _atomic_write_text(_history_path(root), canonical_json(history))


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
    source_diff = record_source_diff(root, previous_manifest, manifest)
    run = {
        "analyzer_version": manifest.get("analyzer_version"),
        "changes": manifest_diff(previous_manifest, manifest),
        "commit": git_snapshot(root),
        "files": len(manifest.get("files", [])),
        "run_id": scanned_at,
        "scanned_at": scanned_at,
        "schema_version": SCHEMA_VERSION,
        "source_diff": source_diff_summary(source_diff),
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
    "MAX_SOURCE_DIFF_CHARS_PER_FILE",
    "MAX_SOURCE_DIFF_FILES",
    "MAX_SOURCE_DIFF_LINES_PER_FILE",
    "MAX_SOURCE_SNAPSHOT_BYTES",
    "MAX_SOURCE_SNAPSHOT_FILE_BYTES",
    "MAX_SCAN_RUNS",
    "MAX_GRAPH_CHANGE_ITEMS",
    "SOURCE_DIFF_FILENAME",
    "SOURCE_SNAPSHOT_DIRECTORY",
    "SOURCE_SNAPSHOT_INDEX_FILENAME",
    "git_snapshot",
    "graph_diff",
    "graph_index",
    "history_for_subject",
    "load_history",
    "load_source_diff",
    "load_source_diff_detail",
    "load_source_diff_summary",
    "manifest_diff",
    "previous_manifest",
    "record_graph_snapshot",
    "record_scan",
    "record_source_diff",
    "record_source_snapshot",
    "source_diff_summary",
    "stale_files",
]

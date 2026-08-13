"""Local browser-folder import for the loopback viewer.

The browser sends selected file bytes to the local VibeWiki process. This is
not an external upload: files are filtered, copied into a temporary workspace,
scanned, and removed when the server exits.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

from .build import build_repository
from .discovery.ignore import should_skip_path
from .errors import ErrorCode, VibeWikiError
from .scan import scan_repository

MAX_IMPORT_FILES = 10_000
MAX_IMPORT_BYTES = 200 * 1024 * 1024
MAX_MULTIPART_PARTS = 50_000
_SUPPORTED_IMPORT_SUFFIXES = {".ts", ".tsx"}


@dataclass(frozen=True, slots=True)
class ImportedWorkspace:
    root: Path
    build_summary: dict[str, Any]


def _relative_filename(filename: str) -> tuple[str, str]:
    normalized = filename.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not parts or PurePosixPath(normalized).is_absolute() or ".." in parts:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "selected source contains an unsafe relative path",
        )
    clean = tuple(part for part in parts if part not in {"", "."})
    if not clean:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "selected source contains an empty file path",
        )
    top = clean[0]
    allowed_roots = {"app", "prisma", "src", "tests"}
    if top not in allowed_roots and len(clean) > 1:
        clean = clean[1:]
    relative = PurePosixPath(*clean).as_posix()
    return top, relative


def _multipart_files(content_type: str, body: bytes) -> list[tuple[str, str, bytes]]:
    if not content_type.lower().startswith("multipart/form-data"):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "source selection must use a local directory picker",
        )
    header = (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode(
        "utf-8"
    )
    message = BytesParser(policy=policy.default).parsebytes(header + body)
    if not message.is_multipart():
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "source selection is invalid")

    selected: list[tuple[str, str, bytes]] = []
    total_bytes = 0
    part_count = 0
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if not filename:
            continue
        part_count += 1
        if part_count > MAX_MULTIPART_PARTS:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "selected source contains too many files",
            )
        top, relative = _relative_filename(filename)
        path = PurePosixPath(relative)
        if should_skip_path(path):
            continue
        if (
            path.as_posix() != "prisma/schema.prisma"
            and path.suffix.casefold() not in _SUPPORTED_IMPORT_SUFFIXES
        ):
            continue
        payload = part.get_payload(decode=True) or b""
        total_bytes += len(payload)
        if len(selected) >= MAX_IMPORT_FILES or total_bytes > MAX_IMPORT_BYTES:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "selected supported source exceeds the local import limit",
            )
        selected.append((top, relative, payload))
    if not selected:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "selected source has no supported TypeScript files",
        )
    return selected


def import_uploaded_workspace(content_type: str, body: bytes) -> ImportedWorkspace:
    """Build a temporary local workspace from browser-selected safe files."""

    selected = _multipart_files(content_type, body)
    project_name = "imported-source"
    first_top = selected[0][0]
    if first_top not in {"app", "src", "tests", "prisma"}:
        project_name = first_top

    temp_parent = Path(tempfile.mkdtemp(prefix="vibewiki-import-"))
    root = temp_parent / project_name
    root.mkdir()
    try:
        for _, relative, payload in selected:
            destination = root / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        scan_repository(root)
        build_summary = build_repository(root)
    except Exception:
        shutil.rmtree(temp_parent, ignore_errors=True)
        raise
    return ImportedWorkspace(root=root, build_summary=build_summary)


def cleanup_workspace(workspace: ImportedWorkspace) -> None:
    """Remove only the temporary workspace created by a browser import."""

    shutil.rmtree(workspace.root.parent, ignore_errors=True)


__all__ = [
    "ImportedWorkspace",
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_FILES",
    "cleanup_workspace",
    "import_uploaded_workspace",
]

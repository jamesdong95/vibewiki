"""Local browser-folder import for the loopback viewer.

The browser sends selected file bytes to the local VibeWiki process. This is
not an external upload: files are filtered, copied into a temporary workspace,
scanned, and removed when the server exits.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

from .build import build_repository
from .config import (
    GENERIC_SUFFIXES,
    LOCAL_IMPORT_MAX_BYTES,
    LOCAL_IMPORT_MAX_FILES,
    PRISMA_SCHEMA_RELATIVE_PATH,
    SUPPORTED_SUFFIXES,
)
from .discovery.files import DiscoveredFile, discover_files
from .discovery.ignore import should_skip_path
from .errors import ErrorCode, VibeWikiError
from .scan import scan_repository

MAX_IMPORT_FILES = LOCAL_IMPORT_MAX_FILES
MAX_IMPORT_BYTES = LOCAL_IMPORT_MAX_BYTES
MAX_MULTIPART_PARTS = 50_000
_SUPPORTED_IMPORT_SUFFIXES = frozenset(
    (*SUPPORTED_SUFFIXES, *GENERIC_SUFFIXES)
)
_KNOWN_SOURCE_ROOTS = frozenset({"app", "prisma", "src", "tests"})
_MONOREPO_ROOTS = frozenset(
    {"apps", "libs", "modules", "packages", "services", "workspaces"}
)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:($|/)")


@dataclass(frozen=True, slots=True)
class ImportedWorkspace:
    root: Path
    build_summary: dict[str, Any]


def _relative_filename(filename: str) -> tuple[str, str]:
    normalized = filename.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or _WINDOWS_DRIVE.match(normalized)
    ):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "selected source contains an unsafe relative path",
        )
    clean = tuple(part for part in normalized.split("/") if part not in {"", "."})
    if not clean or ".." in clean:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "selected source contains an unsafe relative path",
        )
    top = clean[0]
    if top not in _KNOWN_SOURCE_ROOTS and len(clean) > 1:
        clean = clean[1:]
    relative = PurePosixPath(*clean).as_posix()
    return top, relative


def _is_supported_path(path: PurePosixPath) -> bool:
    return (
        path.suffix.casefold() in _SUPPORTED_IMPORT_SUFFIXES
        or path.parts[-2:] == PurePosixPath(PRISMA_SCHEMA_RELATIVE_PATH).parts
    )


def _prefix_candidates(paths: list[PurePosixPath]) -> set[tuple[str, ...]]:
    candidates: set[tuple[str, ...]] = {()}
    for path in paths:
        parts = path.parts
        for index, part in enumerate(parts):
            if part in _KNOWN_SOURCE_ROOTS:
                candidates.add(parts[:index])
            if part in _MONOREPO_ROOTS and index + 1 < len(parts):
                candidates.add(parts[: index + 2])
    return candidates


def _choose_source_prefix(paths: list[PurePosixPath]) -> tuple[str, ...]:
    candidates = _prefix_candidates(paths)
    total = len(paths)

    def score(prefix: tuple[str, ...]) -> tuple[int, int, int, int, tuple[str, ...]]:
        coverage = sum(path.parts[: len(prefix)] == prefix for path in paths)
        direct_app = int(
            any(
                len(path.parts) > len(prefix)
                and path.parts[len(prefix)] == "app"
                for path in paths
            )
        )
        return (
            direct_app,
            int(coverage == total),
            coverage,
            len(prefix),
            tuple(reversed(prefix)),
        )

    return max(candidates, key=score)


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
        part_count += 1
        if part_count > MAX_MULTIPART_PARTS:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "selected source contains too many multipart parts "
                f"(limit: {MAX_MULTIPART_PARTS})",
            )
        filename = part.get_filename()
        if not filename:
            continue
        top, relative = _relative_filename(filename)
        path = PurePosixPath(relative)
        if should_skip_path(path):
            continue
        if not _is_supported_path(path):
            continue
        payload = part.get_payload(decode=True) or b""
        if len(selected) >= MAX_IMPORT_FILES:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "selected source contains too many supported files "
                f"(limit: {MAX_IMPORT_FILES})",
            )
        if total_bytes + len(payload) > MAX_IMPORT_BYTES:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "selected supported source exceeds the byte limit "
                f"(limit: {MAX_IMPORT_BYTES})",
            )
        total_bytes += len(payload)
        selected.append((top, relative, payload))
    if not selected:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "selected source has no supported source, config, or documentation files",
        )
    paths = [PurePosixPath(relative) for _, relative, _ in selected]
    chosen_prefix = _choose_source_prefix(paths)
    normalized: list[tuple[str, str, bytes]] = []
    seen: set[str] = set()
    for top, relative, payload in selected:
        path = PurePosixPath(relative)
        if path.parts[: len(chosen_prefix)] != chosen_prefix:
            continue
        trimmed = path.parts[len(chosen_prefix) :]
        if not trimmed:
            continue
        normalized_path = PurePosixPath(*trimmed).as_posix()
        if normalized_path in seen:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "selected source contains duplicate normalized paths",
            )
        seen.add(normalized_path)
        normalized.append((top, normalized_path, payload))
    if not normalized:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "selected source package contains no supported source files",
        )
    return normalized


def import_uploaded_workspace(content_type: str, body: bytes) -> ImportedWorkspace:
    """Build a temporary local workspace from browser-selected safe files."""

    selected = _multipart_files(content_type, body)
    project_name = "imported-source"
    first_top = selected[0][0]
    if first_top not in {"app", "src", "tests", "prisma"}:
        project_name = first_top

    return _build_imported_workspace(selected, project_name)


def _build_imported_workspace(
    selected: list[tuple[str, str, bytes]], project_name: str
) -> ImportedWorkspace:
    temp_parent = Path(tempfile.mkdtemp(prefix="vibewiki-import-"))
    root = temp_parent / project_name
    root.mkdir()
    try:
        for _, relative, payload in selected:
            destination = root / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        scan_repository(root, allow_generic=True)
        build_summary = build_repository(root)
    except Exception:
        shutil.rmtree(temp_parent, ignore_errors=True)
        raise
    return ImportedWorkspace(root=root, build_summary=build_summary)


def import_local_workspace(repository: str | Path) -> ImportedWorkspace:
    """Copy a user-selected local directory into a bounded import workspace.

    This loopback-only path import exists for browsers that cannot expose a
    directory picker. It uses the same ignored/sensitive path policy and byte
    and file limits as multipart Browse, then builds a temporary snapshot so
    the server never mutates the user's source repository.
    """

    source_root = Path(repository).expanduser().absolute()
    discovered = list(discover_files(source_root, include_generic=True))
    schema = source_root / Path(PRISMA_SCHEMA_RELATIVE_PATH)
    if (
        schema.is_file()
        and not schema.is_symlink()
        and not should_skip_path(PurePosixPath(PRISMA_SCHEMA_RELATIVE_PATH))
    ):
        schema_size = schema.stat().st_size
        discovered.append(
            DiscoveredFile(
                path=PRISMA_SCHEMA_RELATIVE_PATH,
                language="prisma",
                size=schema_size,
                absolute_path=schema,
            )
        )
    if not discovered:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "local path contains no supported source, config, or documentation files",
        )
    if len(discovered) > MAX_IMPORT_FILES:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "local path contains too many supported files "
            f"(limit: {MAX_IMPORT_FILES})",
        )
    total_bytes = sum(item.size for item in discovered)
    if total_bytes > MAX_IMPORT_BYTES:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "local path exceeds the supported byte limit "
            f"(limit: {MAX_IMPORT_BYTES})",
        )

    paths = [PurePosixPath(item.path) for item in discovered]
    chosen_prefix = _choose_source_prefix(paths)
    selected: list[tuple[str, str, bytes]] = []
    seen: set[str] = set()
    read_bytes = 0
    for item in discovered:
        path = PurePosixPath(item.path)
        if path.parts[: len(chosen_prefix)] != chosen_prefix:
            continue
        trimmed = path.parts[len(chosen_prefix) :]
        if not trimmed:
            continue
        normalized = PurePosixPath(*trimmed).as_posix()
        if normalized in seen:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "local path contains duplicate normalized source paths",
            )
        seen.add(normalized)
        try:
            payload = item.absolute_path.read_bytes()
        except (OSError, ValueError) as error:
            raise VibeWikiError(
                ErrorCode.PERMISSION_DENIED,
                "permission denied while reading the local source path",
            ) from error
        read_bytes += len(payload)
        if read_bytes > MAX_IMPORT_BYTES:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "local path exceeds the supported byte limit "
                f"(limit: {MAX_IMPORT_BYTES})",
            )
        selected.append((path.parts[0], normalized, payload))
    if not selected:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "local path package contains no supported source files",
        )
    project_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source_root.name).strip("-")
    return _build_imported_workspace(selected, project_name or "local-source")


def cleanup_workspace(workspace: ImportedWorkspace) -> None:
    """Remove only the temporary workspace created by a browser import."""

    shutil.rmtree(workspace.root.parent, ignore_errors=True)


__all__ = [
    "ImportedWorkspace",
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_FILES",
    "MAX_MULTIPART_PARTS",
    "cleanup_workspace",
    "import_local_workspace",
    "import_uploaded_workspace",
]

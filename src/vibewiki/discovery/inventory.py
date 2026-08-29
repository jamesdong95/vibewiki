"""Safe, content-addressed inventory for every non-ignored repository file."""

from __future__ import annotations

import mimetypes
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from ..config import GENERIC_SUFFIXES, PRISMA_SCHEMA_RELATIVE_PATH, SUPPORTED_SUFFIXES
from ..errors import ErrorCode, VibeWikiError
from .hashing import hash_file
from .ignore import should_skip_path
from .manifest import canonical_json


@dataclass(frozen=True, slots=True)
class InventoryFile:
    """A repository file that is safe to expose as local evidence metadata."""

    path: str
    language: str
    kind: str
    mime: str
    size: int
    sha256: str
    absolute_path: Path = field(repr=False, compare=False)


def _safe_relative_path(parts: tuple[str, ...]) -> str:
    if any("\\" in part for part in parts):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "repository entry could not be represented as a POSIX path",
        )
    relative = PurePosixPath(*parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "repository entry could not be represented as a relative path",
        )
    return relative.as_posix()


def _filesystem_error(error: OSError) -> VibeWikiError:
    if isinstance(error, PermissionError):
        return VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "permission denied while reading the repository",
        )
    return VibeWikiError(
        ErrorCode.PERMISSION_DENIED,
        "repository entry could not be read",
    )


def _is_binary(path: Path) -> bool:
    """Classify from a bounded prefix without persisting source content."""

    try:
        with path.open("rb") as stream:
            prefix = stream.read(8192)
    except OSError as error:
        raise _filesystem_error(error) from error
    if b"\x00" in prefix:
        return True
    try:
        prefix.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _record(path: Path, relative: str, entry_stat: os.stat_result) -> InventoryFile:
    suffix = path.suffix.casefold()
    language = SUPPORTED_SUFFIXES.get(suffix) or GENERIC_SUFFIXES.get(suffix)
    if relative == PRISMA_SCHEMA_RELATIVE_PATH:
        language = "prisma"
    binary = _is_binary(path)
    if relative == PRISMA_SCHEMA_RELATIVE_PATH:
        kind = "schema"
    elif language is not None:
        kind = "source"
    elif binary:
        kind = "binary"
        language = "binary"
    else:
        kind = "text"
        language = language or "text"
    mime = mimetypes.guess_type(path.name, strict=False)[0]
    if not mime:
        mime = "application/octet-stream" if binary else "text/plain"
    try:
        digest = hash_file(path)
    except (OSError, ValueError) as error:
        raise _filesystem_error(error) from error
    return InventoryFile(
        path=relative,
        language=language,
        kind=kind,
        mime=mime,
        size=entry_stat.st_size,
        sha256=digest,
        absolute_path=path,
    )


def _walk(directory: Path, parts: tuple[str, ...]) -> Iterator[InventoryFile]:
    try:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                relative_parts = (*parts, entry.name)
                relative_path = PurePosixPath(*relative_parts)
                if should_skip_path(relative_path):
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        yield from _walk(Path(entry.path), relative_parts)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    entry_stat = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(entry_stat.st_mode):
                        continue
                except OSError as error:
                    raise _filesystem_error(error) from error
                relative = _safe_relative_path(relative_parts)
                yield _record(Path(entry.path), relative, entry_stat)
    except VibeWikiError:
        raise
    except OSError as error:
        raise _filesystem_error(error) from error


def discover_inventory(repository: Path) -> tuple[InventoryFile, ...]:
    """Inventory all regular, non-ignored, non-symlink files in a repository."""

    root = Path(repository)
    try:
        root_stat = root.lstat()
    except OSError as error:
        raise _filesystem_error(error) from error
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "repository path was not found")
    return tuple(_walk(root, ()))


def inventory_dict(item: InventoryFile) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "language": item.language,
        "mime": item.mime,
        "path": item.path,
        "sha256": item.sha256,
        "size": item.size,
    }


def build_inventory(items: tuple[InventoryFile, ...]) -> dict[str, Any]:
    return {
        "files": [inventory_dict(item) for item in items],
        "schema_version": 1,
    }


def write_inventory(root: Path, items: tuple[InventoryFile, ...]) -> Path:
    output = Path(root) / ".vibewiki" / "inventory.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(canonical_json(build_inventory(items)), encoding="utf-8")
    return output


__all__ = [
    "InventoryFile",
    "build_inventory",
    "discover_inventory",
    "inventory_dict",
    "write_inventory",
]

"""Symlink-safe discovery of the JavaScript/TypeScript source surface."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterator, Mapping

from ..config import GENERIC_SUFFIXES, SUPPORTED_SUFFIXES
from ..errors import ErrorCode, VibeWikiError
from .ignore import should_skip_path


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    """A source candidate with a safe relative identity and private local path."""

    path: str
    language: str
    size: int
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


def _walk(
    directory: Path,
    parts: tuple[str, ...],
    suffixes: Mapping[str, str],
) -> Iterator[DiscoveredFile]:
    try:
        with os.scandir(directory) as entries:
            ordered_entries = sorted(entries, key=lambda entry: entry.name)
            for entry in ordered_entries:
                relative_parts = (*parts, entry.name)
                relative = PurePosixPath(*relative_parts)
                if should_skip_path(relative):
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        yield from _walk(Path(entry.path), relative_parts, suffixes)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    suffix = Path(entry.name).suffix.casefold()
                    language = suffixes.get(suffix)
                    if language is None:
                        continue
                    entry_stat = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(entry_stat.st_mode):
                        continue
                except OSError as error:
                    raise _filesystem_error(error) from error

                yield DiscoveredFile(
                    path=_safe_relative_path(relative_parts),
                    language=language,
                    size=entry_stat.st_size,
                    absolute_path=Path(entry.path),
                )
    except VibeWikiError:
        raise
    except OSError as error:
        raise _filesystem_error(error) from error


def discover_files(
    repository: Path, *, include_generic: bool = False
) -> tuple[DiscoveredFile, ...]:
    """Discover source/text files without symlinks.

    Strict callers retain the original JS/TS surface. Generic callers opt into
    the broader local text/source registry.
    """

    root = Path(repository)
    try:
        root_stat = root.lstat()
    except OSError as error:
        raise _filesystem_error(error) from error
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise VibeWikiError(
            ErrorCode.PATH_NOT_FOUND,
            "repository path was not found",
        )

    suffixes = dict(SUPPORTED_SUFFIXES)
    if include_generic:
        suffixes.update(GENERIC_SUFFIXES)
    return tuple(_walk(root, (), suffixes))


__all__ = ["DiscoveredFile", "discover_files"]

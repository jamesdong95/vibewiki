"""M2 local-only repository scan."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .config import SCHEMA_VERSION
from .discovery.files import DiscoveredFile, discover_files
from .discovery.hashing import hash_file
from .discovery.manifest import ManifestFile, build_manifest, write_manifest
from .errors import ErrorCode, VibeWikiError
from .offline import require_offline


def _repository_root(repository: str | os.PathLike[str]) -> Path:
    root = Path(repository)
    try:
        root_stat = root.lstat()
    except FileNotFoundError as error:
        raise VibeWikiError(
            ErrorCode.PATH_NOT_FOUND,
            "repository path was not found",
        ) from error
    except PermissionError as error:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "permission denied while reading the repository",
        ) from error
    except OSError as error:
        raise VibeWikiError(
            ErrorCode.PATH_NOT_FOUND,
            "repository path could not be inspected",
        ) from error

    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "repository stack is not supported by this command",
        )
    return root.absolute()


def _has_direct_app(root: Path) -> bool:
    app = root / "app"
    try:
        app_stat = app.lstat()
    except FileNotFoundError:
        return False
    except PermissionError as error:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "permission denied while reading the repository",
        ) from error
    except OSError as error:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "repository entry could not be read",
        ) from error
    return stat.S_ISDIR(app_stat.st_mode) and not stat.S_ISLNK(app_stat.st_mode)


def _has_pages_router_marker(root: Path) -> bool:
    pages = root / "pages"
    try:
        pages_stat = pages.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "repository entry could not be read",
        ) from error
    return stat.S_ISDIR(pages_stat.st_mode) or stat.S_ISREG(pages_stat.st_mode)


def _app_files(files: tuple[DiscoveredFile, ...]) -> tuple[DiscoveredFile, ...]:
    return tuple(item for item in files if item.path.startswith("app/"))


def _has_nested_router_surface(files: tuple[DiscoveredFile, ...]) -> bool:
    """Reject nested App/Pages Router layouts instead of guessing the app root."""

    for item in files:
        parts = PurePosixPath(item.path).parts
        if any(part in {"app", "pages"} for part in parts[1:-1]):
            return True
    return False


def _manifest_files(files: tuple[DiscoveredFile, ...]) -> list[ManifestFile]:
    records: list[ManifestFile] = []
    for item in files:
        try:
            digest = hash_file(item.absolute_path)
        except ValueError as error:
            raise VibeWikiError(
                ErrorCode.UNSUPPORTED_STACK,
                "repository contains an unsupported file entry",
            ) from error
        except (OSError, PermissionError) as error:
            raise VibeWikiError(
                ErrorCode.PERMISSION_DENIED,
                "permission denied while reading the repository",
            ) from error
        records.append(ManifestFile(item.path, item.language, item.size, digest))
    return records


def scan_repository(
    repository: str | os.PathLike[str], *, offline: bool = True
) -> dict[str, Any]:
    """Scan a supported repository and write only ``.vibewiki/manifest.json``."""

    require_offline(offline)
    root = _repository_root(repository)
    if not _has_direct_app(root) or _has_pages_router_marker(root):
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "repository stack is not supported by this command",
        )

    discovered = discover_files(root)
    if not _app_files(discovered) or _has_nested_router_surface(discovered):
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "repository stack is not supported by this command",
        )

    manifest = build_manifest(_manifest_files(discovered))
    output = write_manifest(root, manifest)
    return {
        "command": "scan",
        "counts": {
            "facts": 0,
            "relations": 0,
            "scanned_files": len(discovered),
            "unknowns": 0,
        },
        "outputs": [output.as_posix()],
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
    }


def scan(repository: str | os.PathLike[str], *, offline: bool = True) -> dict[str, Any]:
    """Short alias for callers that use the CLI command name as an API."""

    return scan_repository(repository, offline=offline)


__all__ = ["scan", "scan_repository"]

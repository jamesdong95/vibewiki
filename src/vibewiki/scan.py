"""M2 local-only repository scan."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .config import PRISMA_SCHEMA_RELATIVE_PATH, SCHEMA_VERSION
from .discovery.files import DiscoveredFile, discover_files
from .discovery.hashing import hash_file
from .discovery.inventory import discover_inventory, write_inventory
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
    """Reject nested App Router layouts instead of guessing the app root."""

    for item in files:
        parts = PurePosixPath(item.path).parts
        if any(
            index > 0
            and part == "app"
            and parts[index + 1].rsplit(".", 1)[0] in {"page", "route"}
            for index, part in enumerate(parts[:-1])
        ):
            return True
    return False


def _has_prisma_schema(root: Path) -> bool:
    schema = root / Path(PRISMA_SCHEMA_RELATIVE_PATH)
    try:
        schema_stat = schema.lstat()
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
    return stat.S_ISREG(schema_stat.st_mode) and not stat.S_ISLNK(schema_stat.st_mode)


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
    repository: str | os.PathLike[str],
    *,
    offline: bool = True,
    allow_generic: bool = False,
) -> dict[str, Any]:
    """Scan a local source tree and write only its manifest.

    A direct Next App Router is still recognized specially, while a generic
    JavaScript/TypeScript or Prisma repository is accepted without requiring
    a top-level ``app`` directory. ``allow_generic=False`` is retained as a
    strict mode for callers that need the original Next-only validation.
    """

    require_offline(offline)
    root = _repository_root(repository)
    discovered = discover_files(root, include_generic=allow_generic)
    inventory = discover_inventory(root)
    has_schema = _has_prisma_schema(root)
    if not discovered and not has_schema:
        message = (
            "repository contains no supported source, config, or documentation files"
            if allow_generic
            else (
                "repository contains no supported JavaScript, TypeScript, or "
                "Prisma source"
            )
        )
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            message,
        )
    if not allow_generic and _has_pages_router_marker(root):
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "Pages Router repositories are not supported; use an App Router "
            "or generic source tree",
        )
    if not allow_generic and (
        not _has_direct_app(root)
        or not _app_files(discovered)
        or _has_nested_router_surface(discovered)
    ):
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "repository is not a direct Next App Router source tree",
        )
    if (
        allow_generic
        and _has_direct_app(root)
        and _has_nested_router_surface(discovered)
    ):
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "repository contains a nested App Router package; import that "
            "package explicitly",
        )

    manifest = build_manifest(_manifest_files(discovered))
    output = write_manifest(root, manifest)
    write_inventory(root, inventory)
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

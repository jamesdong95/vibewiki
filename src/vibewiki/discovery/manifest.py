"""Canonical manifest records and atomic persistence."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from ..config import (
    ANALYZER_VERSION,
    MANIFEST_DIRECTORY,
    MANIFEST_RELATIVE_PATH,
    SCHEMA_VERSION,
)
from ..errors import ErrorCode, VibeWikiError

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ManifestFile:
    """The only per-file state persisted by M2."""

    path: str
    language: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        normalized = PurePosixPath(self.path)
        if (
            not self.path
            or normalized.is_absolute()
            or ".." in normalized.parts
            or normalized.as_posix() != self.path
        ):
            raise ValueError("manifest paths must be relative POSIX paths")
        if not self.language or self.size < 0 or _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("manifest file metadata is invalid")


def manifest_file_dict(item: ManifestFile) -> dict[str, object]:
    """Return a safe serializable file record without its local source path."""

    return {
        "language": item.language,
        "path": item.path,
        "sha256": item.sha256,
        "size": item.size,
    }


def manifest_cache_key(
    item: ManifestFile, *, analyzer_version: str = ANALYZER_VERSION
) -> tuple[str, str, int, str, str]:
    """Return the deterministic incremental-cache identity for one file."""

    return (item.path, item.language, item.size, item.sha256, analyzer_version)


def build_manifest(files: Iterable[ManifestFile]) -> dict[str, object]:
    """Build a sorted manifest with no duplicate relative file identities."""

    ordered = sorted(files, key=lambda item: (item.path, item.language))
    paths = [item.path for item in ordered]
    if len(paths) != len(set(paths)):
        raise ValueError("manifest contains duplicate paths")
    return {
        "analyzer_version": ANALYZER_VERSION,
        "files": [manifest_file_dict(item) for item in ordered],
        "schema_version": SCHEMA_VERSION,
    }


def canonical_json(value: Any) -> str:
    """Serialize JSON with stable key order, separators, Unicode, and newline."""

    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _output_directory(root: Path) -> Path:
    output = root / MANIFEST_DIRECTORY
    try:
        output_stat = output.lstat()
    except FileNotFoundError:
        try:
            output.mkdir()
        except OSError as error:
            raise VibeWikiError(
                ErrorCode.PERMISSION_DENIED,
                "permission denied while writing scan output",
            ) from error
        return output
    except OSError as error:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "permission denied while writing scan output",
        ) from error

    if output.is_symlink() or not stat.S_ISDIR(output_stat.st_mode):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "scan output directory is not a safe local directory",
        )
    return output


def write_manifest(root: Path, manifest: dict[str, object]) -> Path:
    """Atomically replace the manifest and leave no temporary state behind."""

    output = _output_directory(Path(root))
    target = output / "manifest.json"
    payload = canonical_json(manifest).encode("utf-8")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output,
            prefix=".manifest-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    except OSError as error:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "permission denied while writing scan output",
        ) from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return Path(MANIFEST_RELATIVE_PATH)


__all__ = [
    "ManifestFile",
    "build_manifest",
    "canonical_json",
    "manifest_cache_key",
    "manifest_file_dict",
    "write_manifest",
]

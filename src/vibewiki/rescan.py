"""Safe in-place rescans for an already built local workspace."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from .build import build_repository
from .config import MANIFEST_DIRECTORY
from .errors import ErrorCode, VibeWikiError
from .scan import scan_repository


def rescan_repository(repository: str | Path) -> dict[str, Any]:
    """Rescan and rebuild a workspace while preserving its current artifact."""

    root = Path(repository).absolute()
    artifact = root / MANIFEST_DIRECTORY
    backup_root = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.vibewiki-rescan-", dir=root.parent)
    )
    backup = backup_root / MANIFEST_DIRECTORY
    had_artifact = artifact.is_dir()

    try:
        if had_artifact:
            shutil.copytree(artifact, backup)
        scan_result = scan_repository(root)
        build_result = build_repository(root)
    except Exception as error:
        try:
            if artifact.exists() or artifact.is_symlink():
                if artifact.is_dir() and not artifact.is_symlink():
                    shutil.rmtree(artifact)
                else:
                    artifact.unlink()
            if had_artifact:
                shutil.copytree(backup, artifact)
        except OSError as restore_error:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "rescan failed and the previous VibeWiki artifact could not be "
                "restored",
            ) from restore_error
        if isinstance(error, VibeWikiError):
            raise
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            f"rescan failed; the previous VibeWiki artifact was kept: {error}",
        ) from error
    finally:
        shutil.rmtree(backup_root, ignore_errors=True)

    return {
        "command": "rescan",
        "counts": build_result["counts"],
        "scan": scan_result,
        "build": build_result,
        "status": "ok",
    }


__all__ = ["rescan_repository"]

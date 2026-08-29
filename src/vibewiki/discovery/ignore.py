"""Path policy used before a directory entry is inspected."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

# These names are intentionally conservative build/cache boundaries.  The
# policy is component-based so an ignored directory is ignored at any depth.
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".vibewiki",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".next",
        ".cache",
        ".parcel-cache",
        ".turbo",
        ".vercel",
        "node_modules",
        "dist",
        "build",
        "out",
        "coverage",
        "storybook-static",
        "target",
        "venv",
    }
)

IGNORED_FILE_NAMES = frozenset(
    {".DS_Store", ".AppleDouble", ".LSOverride", ".npmrc", ".netrc", ".pypirc"}
)

SENSITIVE_SUFFIXES = frozenset(
    {".cer", ".cert", ".crt", ".der", ".key", ".p12", ".pfx", ".pem"}
)
SENSITIVE_COMPONENT_NAMES = frozenset(
    {
        "credential",
        "credentials",
        "private",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)
SENSITIVE_BASENAMES = frozenset(
    {
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
    }
)
SENSITIVE_NAME_PATTERN = re.compile(
    r"(?i)(?:^|[-_.])("
    r"api[_-]?key|credential|password|passwd|private[_-]?key|secret|token"
    r")(?:$|[-_.])"
)


def _parts(path: Path | PurePosixPath) -> tuple[str, ...]:
    """Return path components using POSIX separators for policy checks."""

    raw = path.as_posix().replace("\\", "/")
    return tuple(part for part in PurePosixPath(raw).parts if part not in {"", "."})


def is_ignored_path(path: Path | PurePosixPath) -> bool:
    """Return whether a path belongs to a generated or VCS boundary."""

    parts = _parts(path)
    return any(
        part in IGNORED_DIRECTORY_NAMES or part in IGNORED_FILE_NAMES
        for part in parts
    )


def is_sensitive_path(path: Path | PurePosixPath) -> bool:
    """Return whether a path may contain credentials or private key material.

    This function depends only on names.  Callers must run it before statting,
    opening, hashing, or recording a candidate so sensitive files are never
    read merely to decide whether to ignore them.
    """

    parts = _parts(path)
    for part in parts:
        lower = part.casefold()
        stem = Path(lower).stem
        suffix = Path(lower).suffix
        if lower.startswith(".env"):
            return True
        if (
            lower in SENSITIVE_COMPONENT_NAMES
            or stem in SENSITIVE_COMPONENT_NAMES
            or lower in SENSITIVE_BASENAMES
        ):
            return True
        if suffix in SENSITIVE_SUFFIXES:
            return True
        if lower.endswith((".secret", ".secrets", ".credential", ".credentials")):
            return True
        if SENSITIVE_NAME_PATTERN.search(stem):
            return True
    return False


def should_skip_path(path: Path | PurePosixPath) -> bool:
    """Return whether a relative path should be skipped before filesystem I/O."""

    return is_ignored_path(path) or is_sensitive_path(path)


__all__ = [
    "IGNORED_DIRECTORY_NAMES",
    "IGNORED_FILE_NAMES",
    "SENSITIVE_SUFFIXES",
    "is_ignored_path",
    "is_sensitive_path",
    "should_skip_path",
]

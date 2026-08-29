"""Content hashing without copying source content into scan state."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest for a byte sequence."""

    return hashlib.sha256(value).hexdigest()


def hash_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a regular, non-symlink file in bounded chunks."""

    file_path = Path(path)
    file_stat = os.lstat(file_path)
    if stat.S_ISLNK(file_stat.st_mode):
        raise ValueError("cannot hash a symlink")
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("cannot hash a non-regular file")

    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["hash_file", "sha256_bytes"]

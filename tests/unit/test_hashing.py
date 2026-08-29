from __future__ import annotations

from pathlib import Path

import pytest

from vibewiki.discovery.hashing import hash_file, sha256_bytes


def test_sha256_bytes_matches_known_digest() -> None:
    assert (
        sha256_bytes(b"hello\n")
        == "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    )


def test_hash_file_matches_bytes(tmp_path: Path) -> None:
    path = tmp_path / "source.ts"
    path.write_bytes(b"export const value = 1;\n")

    assert hash_file(path) == sha256_bytes(path.read_bytes())


def test_hash_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.ts"
    target.write_bytes(b"export const value = 1;\n")
    link = tmp_path / "link.ts"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(ValueError, match="symlink"):
        hash_file(link)

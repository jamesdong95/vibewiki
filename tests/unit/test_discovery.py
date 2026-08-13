from __future__ import annotations

import os
from pathlib import Path

import pytest

from vibewiki.discovery.files import discover_files
from vibewiki.discovery.ignore import is_ignored_path, is_sensitive_path


def _paths(root: Path) -> list[str]:
    return [item.path for item in discover_files(root)]


def test_discovery_is_sorted_relative_posix_and_ignores_non_ts_surface(
    tmp_path: Path,
) -> None:
    (tmp_path / "app/api").mkdir(parents=True)
    (tmp_path / "app/page.tsx").write_text("export default function Page() {}\n")
    (tmp_path / "app/api/route.ts").write_text("export function GET() {}\n")
    (tmp_path / "app/client.js").write_text("export default {}\n")
    (tmp_path / "src/other.ts").parent.mkdir()
    (tmp_path / "src/other.ts").write_text("export const other = true;\n")
    (tmp_path / "node_modules/pkg/index.ts").parent.mkdir(parents=True)
    (tmp_path / "node_modules/pkg/index.ts").write_text("ignored\n")
    (tmp_path / ".next/cache.ts").parent.mkdir(parents=True)
    (tmp_path / ".next/cache.ts").write_text("ignored\n")

    assert _paths(tmp_path) == ["app/api/route.ts", "app/page.tsx", "src/other.ts"]
    assert all(not Path(path).is_absolute() for path in _paths(tmp_path))
    assert all(
        "\\" not in path and ".." not in Path(path).parts
        for path in _paths(tmp_path)
    )


def test_discovery_does_not_follow_symlinks_or_special_files(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "page.tsx").write_text("export default function Page() {}\n")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.ts"
    outside.write_text("secret outside source\n")
    link = app / "outside.ts"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable in this environment")

    fifo = app / "events.ts"
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo)

    discovered = _paths(tmp_path)

    assert discovered == ["app/page.tsx"]
    assert str(outside) not in discovered


def test_sensitive_paths_are_classified_without_needing_file_contents() -> None:
    assert is_sensitive_path(Path(".env.production"))
    assert is_sensitive_path(Path("config/credentials.json"))
    assert is_sensitive_path(Path("certs/server.pem"))
    assert is_sensitive_path(Path("keys/private.key"))
    assert not is_sensitive_path(Path("app/page.tsx"))


@pytest.mark.parametrize(
    "path",
    [
        ".git/config.ts",
        "node_modules/pkg/index.ts",
        ".next/cache.ts",
        ".vibewiki/manifest.json",
    ],
)
def test_ignored_paths_are_stable_policy_entries(path: str) -> None:
    assert is_ignored_path(Path(path))

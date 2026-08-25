from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.scan import scan_repository


def test_scan_writes_stable_manifest_and_inventory(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.tsx").write_text("export default function Page() {}\n")

    first = scan_repository(tmp_path)
    manifest_path = tmp_path / ".vibewiki/manifest.json"
    first_bytes = manifest_path.read_bytes()
    second = scan_repository(tmp_path)

    assert first == second
    assert manifest_path.read_bytes() == first_bytes
    output_files = sorted(
        path.relative_to(tmp_path).as_posix()
        for path in (tmp_path / ".vibewiki").iterdir()
    )
    assert output_files == [
        ".vibewiki/history.json",
        ".vibewiki/inventory.json",
        ".vibewiki/manifest.json",
    ]
    assert json.loads(first_bytes)["files"][0]["path"] == "app/page.tsx"
    assert first["counts"] == {
        "facts": 0,
        "relations": 0,
        "scanned_files": 1,
        "unknowns": 0,
    }


def test_ignored_and_sensitive_changes_do_not_change_manifest(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.ts").write_text("export default function Page() {}\n")
    (tmp_path / "node_modules/pkg").mkdir(parents=True)
    ignored = tmp_path / "node_modules/pkg/index.ts"
    ignored.write_text("first ignored value\n")
    sensitive = tmp_path / ".env.production"
    sensitive.write_text("API_KEY=first-secret\n")

    scan_repository(tmp_path)
    before = (tmp_path / ".vibewiki/manifest.json").read_bytes()
    ignored.write_text("second ignored value\n")
    sensitive.write_text("API_KEY=second-secret\n")
    scan_repository(tmp_path)

    assert (tmp_path / ".vibewiki/manifest.json").read_bytes() == before
    assert "secret" not in before.decode()


def test_sensitive_file_is_skipped_before_hashing(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "app").mkdir()
    page = tmp_path / "app/page.tsx"
    page.write_text("export default function Page() {}\n")
    sensitive = tmp_path / "app/.env.production.ts"
    sensitive.write_text("API_KEY=never-read\n")
    hashed: list[Path] = []

    from vibewiki import scan as scan_module

    original_hash_file = scan_module.hash_file

    def record_hash(path: Path) -> str:
        hashed.append(path)
        return original_hash_file(path)

    monkeypatch.setattr(scan_module, "hash_file", record_hash)
    scan_repository(tmp_path)

    assert hashed == [page]


def test_changed_supported_file_updates_same_size_digest(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    page = tmp_path / "app/page.ts"
    page.write_text("export const value = 1;\n")

    scan_repository(tmp_path)
    before = json.loads((tmp_path / ".vibewiki/manifest.json").read_text())
    page.write_text("export const value = 2;\n")
    scan_repository(tmp_path)
    after = json.loads((tmp_path / ".vibewiki/manifest.json").read_text())

    assert before["files"][0]["size"] == after["files"][0]["size"]
    assert before["files"][0]["sha256"] != after["files"][0]["sha256"]


def test_strict_scan_rejects_pages_router_without_partial_output(
    tmp_path: Path,
) -> None:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages/index.tsx").write_text("export default function Page() {}\n")

    with pytest.raises(VibeWikiError) as raised:
        scan_repository(tmp_path, allow_generic=False)

    assert raised.value.code is ErrorCode.UNSUPPORTED_STACK
    assert not (tmp_path / ".vibewiki").exists()


def test_scan_rejects_nested_app_surface_without_partial_output(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.tsx").write_text("export default function Page() {}\n")
    nested_app = tmp_path / "packages/web/app"
    nested_app.mkdir(parents=True)
    (nested_app / "page.tsx").write_text(
        "export default function NestedPage() {}\n"
    )

    with pytest.raises(VibeWikiError) as raised:
        scan_repository(tmp_path)

    assert raised.value.code is ErrorCode.UNSUPPORTED_STACK
    assert not (tmp_path / ".vibewiki").exists()


def test_scan_rejects_symlinked_output_directory_without_escape(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.tsx").write_text("export default function Page() {}\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / ".vibewiki").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(VibeWikiError) as raised:
        scan_repository(tmp_path)

    assert raised.value.code is ErrorCode.INVALID_OUTPUT
    assert not (outside / "manifest.json").exists()


def test_scan_rejects_explicitly_non_offline_mode_before_output(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.tsx").write_text("export default function Page() {}\n")

    with pytest.raises(VibeWikiError):
        scan_repository(tmp_path, offline=False)

    assert not (tmp_path / ".vibewiki").exists()

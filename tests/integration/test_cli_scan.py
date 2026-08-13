from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibewiki.cli import main


def test_cli_scan_emits_relative_deterministic_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.tsx").write_text("export default function Page() {}\n")

    assert main(["scan", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert captured.err == ""
    assert summary == {
        "command": "scan",
        "counts": {
            "facts": 0,
            "relations": 0,
            "scanned_files": 1,
            "unknowns": 0,
        },
        "outputs": [".vibewiki/manifest.json"],
        "schema_version": 1,
        "status": "ok",
    }
    assert str(tmp_path) not in captured.out


def test_cli_scan_uses_stable_unsupported_stack_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.js").write_text("export default function Page() {}\n")

    assert main(["scan", str(tmp_path)]) == 3
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == (
        "error[unsupported_stack]: repository stack is not supported by this command\n"
    )
    assert not (tmp_path / ".vibewiki").exists()


def test_cli_scan_redacts_missing_absolute_repository_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "private-project"

    assert main(["scan", str(missing)]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert str(missing) not in captured.err
    assert captured.err == "error[path_not_found]: repository path was not found\n"

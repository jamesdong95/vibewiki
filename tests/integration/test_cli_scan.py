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
        "outputs": [".vibewiki/manifest.json", ".vibewiki/history.json"],
        "schema_version": 1,
        "status": "ok",
    }
    assert str(tmp_path) not in captured.out


def test_cli_scan_uses_stable_unsupported_stack_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages/index.js").write_text("export default function Page() {}\n")

    assert main(["scan", str(tmp_path)]) == 3
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == (
        "error[unsupported_stack]: Pages Router repositories are not supported; "
        "use an App Router or generic source tree\n"
    )
    assert not (tmp_path / ".vibewiki").exists()


def test_cli_scan_generic_accepts_non_next_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("def main():\n    return True\n")

    assert main(["scan", str(tmp_path), "--generic"]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["status"] == "ok"
    assert summary["counts"]["scanned_files"] == 1


def test_cli_history_returns_runs_for_a_source_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.tsx").write_text("export default function Page() {}\n")

    assert main(["scan", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["history", str(tmp_path), "app/page.tsx"]) == 0
    history = json.loads(capsys.readouterr().out)

    assert history["paths"] == ["app/page.tsx"]
    assert len(history["runs"]) == 1


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

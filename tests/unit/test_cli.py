from __future__ import annotations

import pytest

from vibewiki.cli import build_parser, main
from vibewiki.errors import CLI_EXIT_CODES, ErrorCode, VibeWikiError


def _run_cli(*args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    try:
        code = main(list(args))
    except SystemExit as exc:
        code = int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_help_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    help_code, help_stdout, help_stderr = _run_cli("--help", capsys=capsys)
    version_code, version_stdout, version_stderr = _run_cli(
        "--version", capsys=capsys
    )

    assert help_code == 0
    assert "usage: vibewiki" in help_stdout
    assert "--version" in help_stdout
    assert help_stderr == ""

    assert version_code == 0
    assert "vibewiki" in version_stdout
    assert "analyzer" in version_stdout
    assert "schema" in version_stdout
    assert version_stderr == ""


def test_version_is_stable_and_reports_metadata(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, stdout, stderr = _run_cli("--version", capsys=capsys)

    assert code == 0
    assert stdout == (
        "vibewiki 0.1.25-preview "
        "(analyzer 0.6.0-preview, schema 1)\n"
    )
    assert stderr == ""


def test_serve_accepts_optional_llm_runtime_flags() -> None:
    arguments = build_parser().parse_args(
        [
            "serve",
            "repo",
            "--llm-provider",
            "ollama",
            "--llm-model",
            "qwen2.5:7b",
            "--llm-base-url",
            "http://127.0.0.1:11434",
        ]
    )

    assert arguments.llm_provider == "ollama"
    assert arguments.llm_model == "qwen2.5:7b"
    assert arguments.llm_base_url == "http://127.0.0.1:11434"


def test_observe_accepts_browser_runtime_flags() -> None:
    arguments = build_parser().parse_args(
        [
            "observe",
            "http://127.0.0.1:3000",
            "--repository",
            "repo",
            "--mode",
            "browser",
            "--screenshots",
        ]
    )

    assert arguments.mode == "browser"
    assert arguments.screenshots is True


@pytest.mark.parametrize("error_code", list(ErrorCode))
def test_cli_renders_categorised_errors_without_traceback(
    error_code: ErrorCode,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_argv: list[str] | None = None) -> int:
        raise VibeWikiError(
            error_code,
            "operation failed at /Users/example/app token=private-token",
        )

    monkeypatch.setattr("vibewiki.cli.run", fail)

    code = main([])
    captured = capsys.readouterr()

    assert code == CLI_EXIT_CODES[error_code]
    assert captured.out == ""
    assert captured.err.startswith(f"error[{error_code.value}]: ")
    assert "private-token" not in captured.err
    assert "/Users/example/app" not in captured.err
    assert "Traceback" not in captured.err

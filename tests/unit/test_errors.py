from __future__ import annotations

import pytest

from vibewiki.errors import (
    CLI_EXIT_CODES,
    ErrorCode,
    VibeWikiError,
    format_error,
    raise_for_code,
)


@pytest.mark.parametrize(
    ("code", "expected_exit"),
    [
        (ErrorCode.PATH_NOT_FOUND, 2),
        (ErrorCode.UNSUPPORTED_STACK, 3),
        (ErrorCode.PERMISSION_DENIED, 4),
        (ErrorCode.INVALID_OUTPUT, 5),
        (ErrorCode.CORRUPT_DATABASE, 6),
    ],
)
def test_error_codes_have_stable_nonzero_exit_codes(
    code: ErrorCode, expected_exit: int
) -> None:
    error = VibeWikiError(code, "operation cannot continue")

    assert CLI_EXIT_CODES[code] == expected_exit
    assert error.exit_code == expected_exit
    assert format_error(error) == (
        f"error[{code.value}]: operation cannot continue"
    )


def test_error_message_does_not_leak_absolute_context() -> None:
    error = VibeWikiError(
        ErrorCode.PATH_NOT_FOUND,
        "repository path was not found",
        context="/Users/example/private/project/.env",
    )

    rendered = format_error(error)

    assert "/Users/example/private/project" not in rendered
    assert ".env" not in rendered
    assert "repository path was not found" in rendered


def test_raise_for_code_creates_actionable_error() -> None:
    with pytest.raises(VibeWikiError) as raised:
        raise_for_code(ErrorCode.INVALID_OUTPUT)

    assert raised.value.code is ErrorCode.INVALID_OUTPUT
    assert "output" in raised.value.message.lower()


def test_error_redacts_colon_secrets_and_context() -> None:
    error = VibeWikiError(
        ErrorCode.PATH_NOT_FOUND,
        "api_key: secret-value /Users/example/private/project",
        context="token=private-token /Users/example/private/project/.env",
    )

    rendered = format_error(error)

    assert "secret-value" not in rendered
    assert "private-token" not in rendered
    assert "/Users/example/private/project" not in rendered
    assert error.context is not None
    assert "private-token" not in error.context
    assert "/Users/example/private/project" not in error.context
    assert "[REDACTED]" in rendered
    assert "[REDACTED_PATH]" in rendered


def test_error_redacts_single_component_and_quoted_secrets() -> None:
    quoted_message = "secret = " + '"value with spaces"' + " at /tmp"
    error = VibeWikiError(ErrorCode.INVALID_OUTPUT, quoted_message)

    rendered = format_error(error)

    assert "value with spaces" not in rendered
    assert "/tmp" not in rendered
    assert "[REDACTED]" in rendered


def test_error_preserves_application_routes() -> None:
    error = VibeWikiError(
        ErrorCode.INVALID_OUTPUT,
        "route at /api/users returned invalid output",
    )

    rendered = format_error(error)

    assert "/api/users" in rendered


def test_error_preserves_single_segment_named_route() -> None:
    error = VibeWikiError(
        ErrorCode.INVALID_OUTPUT,
        "route /signup returned invalid output",
    )

    rendered = format_error(error)

    assert "/signup" in rendered


@pytest.mark.parametrize("prefix", ["route=", "route: ", "url=", "url: "])
def test_error_preserves_routes_with_explicit_context(prefix: str) -> None:
    error = VibeWikiError(
        ErrorCode.INVALID_OUTPUT,
        f"request {prefix}/signup returned invalid output",
    )

    rendered = format_error(error)

    assert f"{prefix}/signup" in rendered


def test_error_redacts_windows_paths() -> None:
    error = VibeWikiError(
        ErrorCode.PATH_NOT_FOUND,
        r"repository path C:\Users\example\private\project\.env was not found",
    )

    rendered = format_error(error)

    assert r"C:\Users\example\private\project\.env" not in rendered
    assert "[REDACTED_PATH]" in rendered


def test_error_redacts_unclassified_posix_paths() -> None:
    error = VibeWikiError(
        ErrorCode.PATH_NOT_FOUND,
        "repository path /workspace/project/.vibewiki/graph.db was not found",
    )

    rendered = format_error(error)

    assert "/workspace/project/.vibewiki/graph.db" not in rendered
    assert "[REDACTED_PATH]" in rendered


def test_error_redacts_uncontextual_generic_posix_paths() -> None:
    error = VibeWikiError(
        ErrorCode.PATH_NOT_FOUND,
        "operation failed /workspace/project/graph.db",
    )

    rendered = format_error(error)

    assert "/workspace/project/graph.db" not in rendered
    assert "[REDACTED_PATH]" in rendered


def test_error_redacts_uncontextual_single_component_paths() -> None:
    error = VibeWikiError(
        ErrorCode.PATH_NOT_FOUND,
        "operation failed /workspace",
    )

    rendered = format_error(error)

    assert "/workspace" not in rendered
    assert "[REDACTED_PATH]" in rendered

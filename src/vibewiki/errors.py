"""Stable, redacted errors exposed by the VibeWiki CLI."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum


class ErrorCode(StrEnum):
    """Machine-readable failure categories for local CLI operations."""

    PATH_NOT_FOUND = "path_not_found"
    UNSUPPORTED_STACK = "unsupported_stack"
    PERMISSION_DENIED = "permission_denied"
    INVALID_OUTPUT = "invalid_output"
    CORRUPT_DATABASE = "corrupt_database"


CLI_EXIT_CODES: Mapping[ErrorCode, int] = {
    ErrorCode.PATH_NOT_FOUND: 2,
    ErrorCode.UNSUPPORTED_STACK: 3,
    ErrorCode.PERMISSION_DENIED: 4,
    ErrorCode.INVALID_OUTPUT: 5,
    ErrorCode.CORRUPT_DATABASE: 6,
}

_DEFAULT_MESSAGES: Mapping[ErrorCode, str] = {
    ErrorCode.PATH_NOT_FOUND: "repository path was not found",
    ErrorCode.UNSUPPORTED_STACK: "repository stack is not supported by this command",
    ErrorCode.PERMISSION_DENIED: "permission denied while reading the repository",
    ErrorCode.INVALID_OUTPUT: "generated output is invalid",
    ErrorCode.CORRUPT_DATABASE: "local VibeWiki database is corrupt",
}

_FILESYSTEM_POSIX_PATH = re.compile(
    r"(?<![\w])/(?:Users|home|root|private|tmp|var|etc|opt|Volumes|System|usr|bin|sbin|Library)"
    r"[^\s,\n;:'\")\]}]*"
)
_GENERIC_POSIX_PATH = re.compile(
    r"(?<![\w/])/(?![/\s]|api(?:/|$))[^\s,\n;:'\")\]}]+"
)
_WINDOWS_PATH = re.compile(
    r"(?<![\w])[A-Za-z]:[\\/][^\s,\n;:'\")\]}]+"
)
_SECRET_ASSIGNMENT = re.compile(
    r'(?i)\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*('
    r'"[^"]*"|\'[^\']*\'|[^\s,;]+)'
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


def _redact_generic_path(match: re.Match[str]) -> str:
    preceding = match.string[max(0, match.start() - 16) : match.start()]
    if re.search(r"(?i)\b(?:route|url)\s*(?:[:=]\s*)?$", preceding):
        return match.group(0)
    return "[REDACTED_PATH]"


def _redact_message(message: str) -> str:
    """Keep diagnostics actionable without copying paths or secret values."""
    redacted = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", message)
    redacted = _BEARER_VALUE.sub("Bearer [REDACTED]", redacted)
    redacted = _FILESYSTEM_POSIX_PATH.sub("[REDACTED_PATH]", redacted)
    redacted = _GENERIC_POSIX_PATH.sub(_redact_generic_path, redacted)
    return _WINDOWS_PATH.sub("[REDACTED_PATH]", redacted)


class VibeWikiError(Exception):
    """A user-facing, categorised error with a stable process exit code."""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        context: str | None = None,
    ) -> None:
        self.code = code
        self.message = _redact_message(message or _DEFAULT_MESSAGES[code])
        # Keep debugger context safe as well as the rendered diagnostic.
        self.context = _redact_message(context) if context is not None else None
        super().__init__(self.message)

    @property
    def exit_code(self) -> int:
        """Return the stable non-zero process code for this error category."""
        return CLI_EXIT_CODES[self.code]


def format_error(error: VibeWikiError) -> str:
    """Render the short stderr representation used by the CLI."""
    return f"error[{error.code.value}]: {_redact_message(error.message)}"


def raise_for_code(
    code: ErrorCode,
    message: str | None = None,
    *,
    context: str | None = None,
) -> None:
    """Raise a categorised error using its safe default message when needed."""
    raise VibeWikiError(code, message, context=context)

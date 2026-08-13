"""Offline policy for scan operations."""

from __future__ import annotations

from .errors import ErrorCode, VibeWikiError


class OfflineViolation(VibeWikiError):
    """Raised before any output is created when offline mode is disabled."""

    def __init__(self) -> None:
        super().__init__(
            ErrorCode.INVALID_OUTPUT,
            "scan requires offline mode",
        )


def require_offline(offline: bool = True) -> None:
    """Fail closed unless the caller explicitly keeps scanning offline."""

    if not offline:
        raise OfflineViolation()


__all__ = ["OfflineViolation", "require_offline"]

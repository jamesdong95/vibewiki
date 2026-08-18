from __future__ import annotations

from pathlib import Path

import pytest

from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.observe import _canonical_url, _origin, observe_repository


def test_observer_canonicalizes_same_origin_document_routes() -> None:
    origin = _origin("http://127.0.0.1:4175/app", allow_network=False)

    assert _canonical_url("/docs#intro", "http://127.0.0.1:4175/", origin) == (
        "http://127.0.0.1:4175/docs"
    )
    assert _canonical_url("/api/health", "http://127.0.0.1:4175/", origin) is None
    assert _canonical_url(
        "https://example.com/", "http://127.0.0.1:4175/", origin
    ) is None


def test_observer_rejects_remote_targets_without_explicit_opt_in(
    tmp_path: Path,
) -> None:
    with pytest.raises(VibeWikiError) as raised:
        observe_repository(tmp_path, "https://example.com")

    assert raised.value.code is ErrorCode.UNSUPPORTED_STACK
    assert "loopback" in raised.value.message

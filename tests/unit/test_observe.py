from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.observe import (
    _browser_request_allowed,
    _canonical_url,
    _origin,
    _safe_runtime_url,
    observe_repository,
)


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


def test_observer_rejects_credentials_in_target(tmp_path: Path) -> None:
    with pytest.raises(VibeWikiError) as raised:
        observe_repository(tmp_path, "http://user:secret@127.0.0.1:4175")

    assert raised.value.code is ErrorCode.INVALID_OUTPUT
    assert "credentials" in raised.value.message


def test_browser_policy_keeps_requests_same_origin_and_strips_query_values() -> None:
    origin = _origin("http://127.0.0.1:4175", allow_network=False)

    assert _browser_request_allowed("http://127.0.0.1:4175/app.js", origin)
    assert not _browser_request_allowed("https://cdn.example.com/app.js", origin)
    assert _browser_request_allowed("data:text/plain,ok", origin)
    assert _safe_runtime_url(
        "http://127.0.0.1:4175/page?token=private#section"
    ) == "http://127.0.0.1:4175/page"


def test_browser_mode_reports_missing_optional_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_playwright() -> None:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "browser observation requires the optional runtime dependency",
        )

    monkeypatch.setattr("vibewiki.observe._load_playwright", missing_playwright)
    with pytest.raises(VibeWikiError) as raised:
        observe_repository(tmp_path, "http://127.0.0.1:4175", mode="browser")

    assert raised.value.code is ErrorCode.UNSUPPORTED_STACK
    assert "optional runtime dependency" in raised.value.message


def test_observer_rejects_unknown_mode(tmp_path: Path) -> None:
    with pytest.raises(VibeWikiError) as raised:
        observe_repository(tmp_path, "http://127.0.0.1:4175", mode="playwright")

    assert raised.value.code is ErrorCode.INVALID_OUTPUT


def test_http_mode_rejects_screenshot_request(tmp_path: Path) -> None:
    with pytest.raises(VibeWikiError) as raised:
        observe_repository(
            tmp_path,
            "http://127.0.0.1:4175",
            screenshots=True,
        )

    assert raised.value.code is ErrorCode.INVALID_OUTPUT
    assert "browser" in raised.value.message


def test_browser_mode_records_console_routes_and_safe_request_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRequest:
        def __init__(self, method: str, url: str) -> None:
            self.method = method
            self.url = url
            self.failure = None

    class FakeRoute:
        def __init__(self, request: FakeRequest) -> None:
            self.request = request
            self.aborted = False

        def abort(self) -> None:
            self.aborted = True

        def continue_(self) -> None:
            self.aborted = False

    class FakePage:
        def __init__(self, context: "FakeContext") -> None:
            self.context = context
            self.handlers: dict[str, object] = {}

        def on(self, event: str, handler: object) -> None:
            self.handlers[event] = handler

        def _emit(self, event: str, value: object) -> None:
            handler = self.handlers.get(event)
            if handler is not None:
                handler(value)  # type: ignore[operator]

        def goto(self, url: str, **_kwargs: object) -> object:
            response = None
            for method, request_url in (
                ("GET", url),
                ("GET", "https://external.example/asset.js"),
                ("POST", f"{url.rstrip('/')}/submit"),
            ):
                request = FakeRequest(method, request_url)
                self._emit("request", request)
                route = FakeRoute(request)
                self.context.route_handler(route)
                if route.aborted:
                    continue
                response = SimpleNamespace(
                    request=request,
                    status=200,
                    headers={"content-type": "text/html"},
                )
                self._emit("response", response)
            self._emit(
                "console",
                SimpleNamespace(
                    type="error",
                    text="api_key=secret",
                    location={"columnNumber": 2, "lineNumber": 3, "url": url},
                ),
            )
            return response

        def locator(self, _selector: str) -> object:
            return SimpleNamespace(
                evaluate_all=lambda _script: [
                    "http://127.0.0.1:4175/next",
                    "https://external.example/",
                ]
            )

        def screenshot(self, *, path: str, full_page: bool) -> None:
            assert full_page is True
            Path(path).write_bytes(b"fake-png")

    class FakeContext:
        def __init__(self) -> None:
            self.route_handler = lambda _route: None
            self.page = FakePage(self)

        def route(self, _pattern: str, handler: object) -> None:
            self.route_handler = handler  # type: ignore[assignment]

        def new_page(self) -> FakePage:
            return self.page

    class FakeBrowser:
        def __init__(self) -> None:
            self.context = FakeContext()

        def new_context(self) -> FakeContext:
            return self.context

        def close(self) -> None:
            return None

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = SimpleNamespace(launch=lambda **_kwargs: FakeBrowser())

        def __enter__(self) -> "FakePlaywright":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        "vibewiki.observe._load_playwright",
        lambda: lambda: FakePlaywright(),
    )
    result = observe_repository(
        tmp_path,
        "http://127.0.0.1:4175/?token=secret",
        mode="browser",
        max_routes=2,
        screenshots=True,
    )
    runtime = json.loads((tmp_path / ".vibewiki/runtime.json").read_text())

    assert result["mode"] == "browser"
    assert runtime["observer_mode"] == "browser"
    assert len(runtime["routes"]) == 2
    assert runtime["screenshots"][0]["path"].endswith("route-01.png")
    assert runtime["target"] == "http://127.0.0.1:4175/"
    assert any(item["error"] == "blocked_by_safe_policy" for item in runtime["network"])
    assert runtime["console"][0]["text"] == "api_key=[REDACTED]"
    assert (tmp_path / ".vibewiki/runtime-screenshots/route-01.png").is_file()

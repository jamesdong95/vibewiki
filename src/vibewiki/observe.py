"""Safe, read-only runtime observation for local HTTP applications."""

from __future__ import annotations

import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import ANALYZER_VERSION, SCHEMA_VERSION
from .analyzer import write_json
from .config import MANIFEST_DIRECTORY
from .errors import ErrorCode, VibeWikiError

OBSERVER_VERSION = "0.2.0"
HTTP_OBSERVER_VERSION = "0.1.0-http"
BROWSER_OBSERVER_VERSION = "0.2.0-browser"
MAX_ROUTES = 24
MAX_NETWORK_EVENTS = 256
MAX_RESPONSE_BYTES = 512 * 1024
DEFAULT_TIMEOUT_SECONDS = 5
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _origin(url: str, *, allow_network: bool) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "observe target must be an http(s) URL",
        )
    if parsed.username or parsed.password:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "observe target must not include username or password credentials",
        )
    if not allow_network and parsed.hostname.casefold() not in _LOCAL_HOSTS:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "observe only allows loopback targets by default; use --allow-network "
            "for an explicitly remote target",
        )
    return parsed.scheme, parsed.hostname.casefold(), parsed.port


def _same_origin(url: str, origin: tuple[str, str, int | None]) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme, parsed.hostname or "", parsed.port) == origin


def _canonical_url(
    url: str, base: str, origin: tuple[str, str, int | None]
) -> str | None:
    candidate = urljoin(base, url)
    if not _same_origin(candidate, origin):
        return None
    parsed = urlsplit(candidate)
    path = parsed.path or "/"
    if path.startswith("/api/"):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _safe_runtime_url(url: str) -> str:
    """Strip query and fragment values before persisting runtime metadata."""
    parsed = urlsplit(url)
    if not parsed.scheme:
        return url
    if parsed.hostname is None:
        return urlunsplit((parsed.scheme, "", parsed.path or "/", "", ""))
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))


def _write_runtime(root: Path, runtime: dict[str, Any]) -> None:
    output = root / MANIFEST_DIRECTORY
    output.mkdir(exist_ok=True)
    write_json(output / "runtime.json", runtime)


def _runtime_summary(
    target: str, mode: str, runtime: dict[str, Any]
) -> dict[str, Any]:
    return {
        "command": "observe",
        "counts": {
            "console_errors": len(runtime["console"]),
            "network": len(runtime["network"]),
            "routes": len(runtime["routes"]),
            "unknowns": len(runtime["unknowns"]),
        },
        "mode": mode,
        "outputs": [f"{MANIFEST_DIRECTORY}/runtime.json"],
        "status": "ok",
        "target": _safe_runtime_url(target),
    }


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


class _RedirectPolicy(HTTPRedirectHandler):
    """urllib redirect handler that never leaves the selected origin silently."""

    def __init__(
        self, origin: tuple[str, str, int | None], allow_network: bool
    ) -> None:
        self.origin = origin
        self.allow_network = allow_network

    def redirect_request(
        self,
        request: Request,
        response: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        if not self.allow_network and not _same_origin(newurl, self.origin):
            raise VibeWikiError(
                ErrorCode.UNSUPPORTED_STACK,
                "observe refused a redirect outside the selected local origin",
            )
        return Request(
            newurl,
            headers=dict(request.header_items()),
            method="GET",
        )


def _fetch(
    opener: Any,
    url: str,
    timeout: float,
) -> tuple[dict[str, Any], str]:
    started = time.monotonic()
    request = Request(url, headers={"User-Agent": "VibeWikiRuntimeObserver/0.1"})
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            truncated = len(payload) > MAX_RESPONSE_BYTES
            body = payload[:MAX_RESPONSE_BYTES]
            status = int(getattr(response, "status", response.getcode()))
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
            error = None
    except HTTPError as response:
        body = response.read(MAX_RESPONSE_BYTES)
        status = response.code
        content_type = response.headers.get("Content-Type", "")
        final_url = response.geturl()
        truncated = False
        error = f"HTTP {response.code}"
    except (OSError, URLError, TimeoutError) as caught:
        body = b""
        status = None
        content_type = ""
        final_url = url
        truncated = False
        error = type(caught).__name__
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    record = {
        "content_type": content_type,
        "elapsed_ms": elapsed_ms,
        "error": error,
        "final_url": final_url,
        "method": "GET",
        "status": status,
        "url": url,
    }
    if truncated:
        record["truncated"] = True
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    return record, text


def observe_repository(
    repository: str | Path,
    target: str,
    *,
    allow_network: bool = False,
    max_routes: int = MAX_ROUTES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    mode: str = "http",
    screenshots: bool = False,
) -> dict[str, Any]:
    """Observe a local application and write its runtime evidence artifact."""

    if screenshots and mode != "browser":
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "runtime screenshots require browser observation mode",
        )
    if mode == "browser":
        return _observe_browser_repository(
            repository,
            target,
            allow_network=allow_network,
            max_routes=max_routes,
            timeout=timeout,
            screenshots=screenshots,
        )
    if mode != "http":
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "observe mode must be either http or browser",
        )

    root = Path(repository).absolute()
    if not root.is_dir():
        raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "repository path was not found")
    origin = _origin(target, allow_network=allow_network)
    max_routes = max(1, min(max_routes, MAX_ROUTES))
    opener = build_opener(_RedirectPolicy(origin, allow_network))
    queue = [_canonical_url(target, target, origin)]
    seen: set[str] = set()
    network: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    while queue and len(routes) < max_routes:
        url = queue.pop(0)
        if not url or url in seen:
            continue
        seen.add(url)
        request_record, text = _fetch(opener, url, timeout)
        network.append({"kind": "runtime_network", **request_record})
        path = urlsplit(url).path or "/"
        routes.append(
            {
                "content_type": request_record["content_type"],
                "evidence": [
                    {
                        "kind": "runtime_route",
                        "line_end": 1,
                        "line_start": 1,
                        "path": url,
                        "status": "verified" if request_record["status"] else "unknown",
                    }
                ],
                "path": path,
                "status": request_record["status"],
                "url": url,
            }
        )
        if "text/html" not in request_record["content_type"].casefold():
            continue
        parser = _LinkParser()
        try:
            parser.feed(text)
        except (TypeError, ValueError):
            continue
        for href in parser.links:
            linked = _canonical_url(href, url, origin)
            if linked and linked not in seen and linked not in queue:
                queue.append(linked)

    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    runtime = {
        "analyzer_version": ANALYZER_VERSION,
        "console": [],
        "network": network,
        "observer_mode": "http",
        "observer_version": HTTP_OBSERVER_VERSION,
        "observed_at": observed_at,
        "routes": routes,
        "schema_version": SCHEMA_VERSION,
        "screenshots": [],
        "target": _safe_runtime_url(target),
        "unknowns": [
            {
                "evidence": [
                    {
                        "kind": "runtime_observation",
                        "line_end": 1,
                        "line_start": 1,
                        "path": _safe_runtime_url(target),
                        "status": "unknown",
                    }
                ],
                "reason": (
                    "safe HTTP observation does not execute JavaScript, capture "
                    "browser console events, or submit forms"
                ),
                "status": "unknown",
                "subject": "runtime:javascript-and-side-effects",
            }
        ],
    }
    _write_runtime(root, runtime)
    return _runtime_summary(_safe_runtime_url(target), "http", runtime)


def _load_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "browser observation requires the optional runtime dependency; "
            "install vibewiki[runtime] and run playwright install chromium",
        ) from error
    return sync_playwright


def _browser_request_allowed(url: str, origin: tuple[str, str, int | None]) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme in {"data", "about", "blob"}:
        return True
    return _same_origin(url, origin)


def _redact_console_text(value: str) -> str:
    text = value[:500]
    for marker in ("api_key", "apikey", "secret", "password", "token"):
        lowered = text.casefold()
        index = lowered.find(marker)
        if index >= 0:
            end = text.find(" ", index)
            suffix = "" if end < 0 else text[end:]
            text = f"{text[:index]}{marker}=[REDACTED]{suffix}"
    return text


def _observe_browser_repository(
    repository: str | Path,
    target: str,
    *,
    allow_network: bool,
    max_routes: int,
    timeout: float,
    screenshots: bool,
) -> dict[str, Any]:
    """Observe same-origin browser routes without forms or non-GET requests."""
    root = Path(repository).absolute()
    if not root.is_dir():
        raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "repository path was not found")
    origin = _origin(target, allow_network=allow_network)
    max_routes = max(1, min(max_routes, MAX_ROUTES))
    sync_playwright = _load_playwright()
    network: list[dict[str, Any]] = []
    console: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    screenshots_metadata: list[dict[str, Any]] = []
    queue = [_canonical_url(target, target, origin)]
    seen: set[str] = set()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            requests: dict[int, dict[str, Any]] = {}

            def on_request(request: Any) -> None:
                if len(network) >= MAX_NETWORK_EVENTS:
                    return
                record = {
                    "content_type": "",
                    "elapsed_ms": None,
                    "error": None,
                    "kind": "runtime_network",
                    "method": request.method,
                    "status": None,
                    "url": _safe_runtime_url(request.url),
                }
                requests[id(request)] = record
                network.append(record)

            def on_response(response: Any) -> None:
                record = requests.get(id(response.request))
                if record is None:
                    return
                record["status"] = response.status
                record["content_type"] = response.headers.get("content-type", "")

            def on_request_failed(request: Any) -> None:
                record = requests.get(id(request))
                if record is not None:
                    record["error"] = request.failure or "request_failed"

            def on_console(message: Any) -> None:
                if message.type != "error":
                    return
                location = message.location or {}
                console.append(
                    {
                        "column": location.get("columnNumber"),
                        "line": location.get("lineNumber"),
                        "text": _redact_console_text(message.text),
                        "type": message.type,
                        "url": _safe_runtime_url(location.get("url", "")),
                    }
                )

            def on_page_error(error: Any) -> None:
                console.append(
                    {
                        "column": None,
                        "line": None,
                        "text": _redact_console_text(str(error)),
                        "type": "pageerror",
                        "url": "",
                    }
                )

            def handle_route(route: Any) -> None:
                request = route.request
                if request.method.upper() != "GET" or not _browser_request_allowed(
                    request.url, origin
                ):
                    record = requests.get(id(request))
                    if record is not None:
                        record["error"] = "blocked_by_safe_policy"
                    route.abort()
                    return
                route.continue_()

            context.route("**/*", handle_route)
            page = context.new_page()
            page.on("request", on_request)
            page.on("response", on_response)
            page.on("requestfailed", on_request_failed)
            page.on("console", on_console)
            page.on("pageerror", on_page_error)

            while queue and len(routes) < max_routes:
                url = queue.pop(0)
                if not url or url in seen:
                    continue
                seen.add(url)
                response = None
                error = None
                try:
                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=int(timeout * 1000),
                    )
                except Exception as caught:  # Playwright has several error types.
                    error = type(caught).__name__
                status = response.status if response is not None else None
                content_type = (
                    response.headers.get("content-type", "")
                    if response is not None
                    else ""
                )
                route_record = {
                    "content_type": content_type,
                    "error": error,
                    "evidence": [
                        {
                            "kind": "runtime_route",
                            "line_end": 1,
                            "line_start": 1,
                            "path": url,
                            "status": "verified" if status else "unknown",
                        }
                    ],
                    "path": urlsplit(url).path or "/",
                    "status": status,
                    "url": url,
                }
                routes.append(route_record)
                if screenshots:
                    screenshot_dir = root / MANIFEST_DIRECTORY / "runtime-screenshots"
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    relative = (
                        f"{MANIFEST_DIRECTORY}/runtime-screenshots/"
                        f"route-{len(routes):02d}.png"
                    )
                    page.screenshot(path=str(root / relative), full_page=True)
                    screenshots_metadata.append(
                        {"path": relative, "route": route_record["path"], "url": url}
                    )
                if content_type.casefold().find("text/html") < 0:
                    continue
                try:
                    links = page.locator("a").evaluate_all(
                        "items => items.map(item => item.href).filter(Boolean)"
                    )
                except Exception:
                    links = []
                for link in links:
                    linked = _canonical_url(link, url, origin)
                    if linked and linked not in seen and linked not in queue:
                        queue.append(linked)
            browser.close()
    except VibeWikiError:
        raise
    except Exception as error:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "browser observation could not start; verify Chromium is installed",
            context=type(error).__name__,
        ) from error

    unknowns = [
        {
            "evidence": [
                {
                    "kind": "runtime_observation",
                    "line_end": 1,
                    "line_start": 1,
                    "path": _safe_runtime_url(target),
                    "status": "unknown",
                }
            ],
            "reason": (
                "browser observation blocks non-GET requests and does not submit "
                "forms, authenticate, or execute destructive side effects"
            ),
            "status": "unknown",
            "subject": "runtime:side-effects-and-authentication",
        }
    ]
    if not screenshots:
        unknowns.append(
            {
                "evidence": [
                    {
                        "kind": "runtime_observation",
                        "line_end": 1,
                        "line_start": 1,
                        "path": _safe_runtime_url(target),
                        "status": "unknown",
                    }
                ],
                "reason": "browser screenshots were disabled for this observation",
                "status": "unknown",
                "subject": "runtime:screenshots",
            }
        )
    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    runtime = {
        "analyzer_version": ANALYZER_VERSION,
        "console": console,
        "network": network,
        "observer_mode": "browser",
        "observer_version": BROWSER_OBSERVER_VERSION,
        "observed_at": observed_at,
        "routes": routes,
        "schema_version": SCHEMA_VERSION,
        "screenshots": screenshots_metadata,
        "target": _safe_runtime_url(target),
        "unknowns": unknowns,
    }
    _write_runtime(root, runtime)
    return _runtime_summary(_safe_runtime_url(target), "browser", runtime)


__all__ = [
    "BROWSER_OBSERVER_VERSION",
    "HTTP_OBSERVER_VERSION",
    "MAX_ROUTES",
    "OBSERVER_VERSION",
    "observe_repository",
]

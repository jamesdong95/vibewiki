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

OBSERVER_VERSION = "0.1.0-http"
MAX_ROUTES = 24
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
) -> dict[str, Any]:
    """Observe same-origin document routes with GET only and write runtime.json."""

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
        "observer_version": OBSERVER_VERSION,
        "observed_at": observed_at,
        "routes": routes,
        "schema_version": SCHEMA_VERSION,
        "screenshots": [],
        "target": target,
        "unknowns": [
            {
                "evidence": [
                    {
                        "kind": "runtime_observation",
                        "line_end": 1,
                        "line_start": 1,
                        "path": target,
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
    output = root / MANIFEST_DIRECTORY
    output.mkdir(exist_ok=True)
    write_json(output / "runtime.json", runtime)
    return {
        "command": "observe",
        "counts": {
            "console_errors": len(runtime["console"]),
            "network": len(network),
            "routes": len(routes),
            "unknowns": len(runtime["unknowns"]),
        },
        "outputs": [f"{MANIFEST_DIRECTORY}/runtime.json"],
        "status": "ok",
        "target": target,
    }


__all__ = ["MAX_ROUTES", "OBSERVER_VERSION", "observe_repository"]

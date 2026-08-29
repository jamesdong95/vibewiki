"""Stdlib HTTP adapters for Ollama and OpenAI-compatible APIs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..errors import ErrorCode, VibeWikiError
from .base import ProviderResponse

MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 45


def _post_json(
    url: str,
    payload: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise VibeWikiError(
            ErrorCode.LLM_UNAVAILABLE,
            f"LLM provider returned HTTP {error.code}",
        ) from error
    except (OSError, URLError, TimeoutError) as error:
        raise VibeWikiError(
            ErrorCode.LLM_UNAVAILABLE,
            "LLM provider could not be reached",
        ) from error
    if len(body) > MAX_RESPONSE_BYTES:
        raise VibeWikiError(
            ErrorCode.LLM_UNAVAILABLE,
            "LLM provider response exceeded the local safety limit",
        )
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise VibeWikiError(
            ErrorCode.LLM_UNAVAILABLE,
            "LLM provider returned invalid JSON",
        ) from error
    if not isinstance(value, dict):
        raise VibeWikiError(
            ErrorCode.LLM_UNAVAILABLE, "LLM provider response is invalid"
        )
    return value


class OllamaProvider:
    """Call Ollama's local `/api/chat` endpoint."""

    name = "ollama"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def generate(
        self, messages: Sequence[dict[str, str]], *, model: str
    ) -> ProviderResponse:
        payload = _post_json(
            f"{self.base_url}/api/chat",
            {"model": model, "messages": list(messages), "stream": False},
        )
        message = payload.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise VibeWikiError(
                ErrorCode.LLM_UNAVAILABLE,
                "Ollama returned an empty response",
            )
        return ProviderResponse(text.strip(), model)


class OpenAICompatibleProvider:
    """Call a provider exposing `/v1/chat/completions`."""

    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def generate(
        self, messages: Sequence[dict[str, str]], *, model: str
    ) -> ProviderResponse:
        endpoint = (
            f"{self.base_url}/chat/completions"
            if self.base_url.endswith("/v1")
            else f"{self.base_url}/v1/chat/completions"
        )
        payload = _post_json(
            endpoint,
            {
                "model": model,
                "messages": list(messages),
                "temperature": 0.1,
                "stream": False,
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        choices = payload.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise VibeWikiError(
                ErrorCode.LLM_UNAVAILABLE,
                "compatible LLM provider returned an empty response",
            )
        return ProviderResponse(text.strip(), model)

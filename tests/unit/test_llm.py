from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.llm import LLMSettings, ask_repository, configure_llm
from vibewiki.providers.http import OllamaProvider, OpenAICompatibleProvider


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit: int = -1) -> bytes:
        return self.payload[:limit]


def test_llm_settings_default_to_evidence_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBEWIKI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("VIBEWIKI_LLM_API_KEY", raising=False)

    settings = LLMSettings.from_environment()

    assert settings.provider == "none"
    assert settings.model == "qwen2.5:7b"


def test_question_tokens_ignore_natural_language_fillers() -> None:
    from vibewiki.llm import _tokens

    assert _tokens("Which files are connected to signup?") == {"signup"}


def test_openai_compatible_settings_require_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBEWIKI_LLM_PROVIDER", "openai-compatible")
    monkeypatch.delenv("VIBEWIKI_LLM_API_KEY", raising=False)

    with pytest.raises(VibeWikiError) as raised:
        LLMSettings.from_environment()

    assert raised.value.code is ErrorCode.LLM_UNAVAILABLE
    assert "API key" in raised.value.message


def test_configure_llm_keeps_existing_key_without_returning_it() -> None:
    current = LLMSettings(
        "openai-compatible", "old-model", "https://example.test/v1", "secret"
    )

    settings = configure_llm(
        {"provider": "openai-compatible", "model": "new-model"}, current
    )

    assert settings.model == "new-model"
    assert settings.api_key == "secret"


def test_evidence_only_answer_is_local_and_cited(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("VIBEWIKI_LLM_PROVIDER", raising=False)
    source = tmp_path / "app" / "page.tsx"
    source.parent.mkdir()
    source.write_text("export default function Signup() {}\n")
    artifact = {
        "facts": [
            {
                "semantic_key": "route:page:/signup",
                "kind": "route",
                "attributes": {"path": "/signup", "file": "app/page.tsx"},
                "evidence": [
                    {
                        "path": "app/page.tsx",
                        "line_start": 1,
                        "line_end": 1,
                    }
                ],
            }
        ],
        "relations": [],
        "module_edges": [],
        "package_edges": [],
        "symbol_edges": [],
    }

    result = ask_repository(tmp_path, artifact, {"question": "signup"})

    assert result["provider"] == "none"
    assert result["citations"][0]["path"] == "app/page.tsx"
    assert result["grounded"] is True


def test_ollama_provider_uses_local_chat_contract() -> None:
    with patch(
        "vibewiki.providers.http.urlopen",
        return_value=_Response({"message": {"content": "local answer"}}),
    ) as mocked:
        result = OllamaProvider("http://127.0.0.1:11434").generate(
            [{"role": "user", "content": "hello"}], model="local-model"
        )

    request = mocked.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url == "http://127.0.0.1:11434/api/chat"
    assert payload["stream"] is False
    assert result.text == "local answer"


def test_compatible_provider_keeps_api_key_in_authorization_header() -> None:
    with patch(
        "vibewiki.providers.http.urlopen",
        return_value=_Response(
            {"choices": [{"message": {"content": "api answer"}}]}
        ),
    ) as mocked:
        result = OpenAICompatibleProvider(
            "https://example.test/v1", "secret-key"
        ).generate([{"role": "user", "content": "hello"}], model="model")

    request = mocked.call_args.args[0]
    assert request.full_url == "https://example.test/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer secret-key"
    assert result.text == "api answer"

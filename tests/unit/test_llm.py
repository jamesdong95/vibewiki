from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.llm import (
    LLMSettings,
    ask_repository,
    configure_llm,
    normalize_markdown,
    redact_source_context,
)
from vibewiki.providers.http import OllamaProvider, OpenAICompatibleProvider

_RAW_API_KEY = "sk-" + "live-raw-key-123456789"
_RAW_PASSWORD = "correct-horse-battery-staple"
_RAW_JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".signature123"


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


def test_normalize_markdown_repairs_inline_provider_sections() -> None:
    value = r"# Flow --- ## Bước 1\n- **Read source**"

    normalized = normalize_markdown(value)

    assert normalized == "# Flow\n\n---\n\n## Bước 1\n- **Read source**"


def test_normalize_markdown_does_not_rewrite_code_fences() -> None:
    value = "# Title --- ## Detail\n```text\n# literal --- ## code\n```"

    normalized = normalize_markdown(value)

    assert "## code" in normalized
    assert "\n\n---\n\n## Detail" in normalized


def test_normalize_markdown_separates_inline_step_headings() -> None:
    value = "### Các bước trong flow **Bước 1 — Khởi tạo state** - Component"

    normalized = normalize_markdown(value)

    assert normalized == (
        "### Các bước trong flow\n\n"
        "**Bước 1 — Khởi tạo state**\n"
        "- Component"
    )


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


def test_remote_source_redaction_preserves_citations_and_normal_identifiers() -> None:
    quote = chr(34)
    raw = (
        'config.ts:2: const tokenCount = 2;\n'
        f"config.ts:3: const API_KEY = {quote}{_RAW_API_KEY}{quote};\n"
        f"config.ts:4: const password = {quote}{_RAW_PASSWORD}{quote};\n"
        f"config.ts:5: const jwt = {quote}{_RAW_JWT}{quote};\n"
        "config.ts:5b: password: hunter2\n"
        "config.ts:5c: token: abc-raw-token-value\n"
        "config.ts:5d: const tokenValue = getToken();\n"
        "config.ts:6: -----BEGIN PRIVATE KEY-----\n"
        "config.ts:7: private-key-material\n"
        "config.ts:8: -----END PRIVATE KEY-----"
    )

    redacted = redact_source_context(raw)

    assert _RAW_API_KEY not in redacted
    assert _RAW_PASSWORD not in redacted
    assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
    assert "hunter2" not in redacted
    assert "abc-raw-token-value" not in redacted
    assert "private-key-material" not in redacted
    assert "config.ts:3:" in redacted
    assert "config.ts:8:" in redacted
    assert "const tokenCount = 2" in redacted
    assert "const tokenValue = getToken();" in redacted


def test_remote_provider_requires_confirmation_before_retrieval_is_sent(
    tmp_path: Path,
) -> None:
    quote = chr(34)
    source = tmp_path / "src" / "config.ts"
    source.parent.mkdir()
    source.write_text(
        f"export const API_KEY = {quote}{_RAW_API_KEY}{quote};\n"
    )
    artifact = {
        "facts": [
            {
                "semantic_key": "function:src/config.ts:loadConfig",
                "kind": "function",
                "attributes": {"name": "loadConfig", "file": "src/config.ts"},
                "evidence": [
                    {"path": "src/config.ts", "line_start": 1, "line_end": 1}
                ],
            }
        ],
        "relations": [],
        "module_edges": [],
        "package_edges": [],
        "symbol_edges": [],
    }
    settings = LLMSettings(
        "openai-compatible", "remote-model", "https://example.test", "provider-key"
    )
    provider = SimpleNamespace(name="openai-compatible")
    provider.generate = lambda messages, model: (_ for _ in ()).throw(
        AssertionError("provider must not be called before confirmation")
    )

    with patch("vibewiki.llm._provider", return_value=provider):
        with pytest.raises(VibeWikiError, match="confirmation"):
            ask_repository(tmp_path, artifact, {"question": "config"}, settings)


def test_remote_provider_receives_redacted_context_without_mutating_source(
    tmp_path: Path,
) -> None:
    quote = chr(34)
    source = tmp_path / "src" / "config.ts"
    source.parent.mkdir()
    raw = (
        f"export const API_KEY = {quote}{_RAW_API_KEY}{quote};\n"
        'export const tokenCount = 2;\n'
        f"export const password = {quote}{_RAW_PASSWORD}{quote};\n"
    )
    source.write_text(raw)
    artifact = {
        "facts": [
            {
                "semantic_key": "function:src/config.ts:loadConfig",
                "kind": "function",
                "attributes": {"name": "loadConfig", "file": "src/config.ts"},
                "evidence": [
                    {"path": "src/config.ts", "line_start": 1, "line_end": 3}
                ],
            }
        ],
        "relations": [],
        "module_edges": [],
        "package_edges": [],
        "symbol_edges": [],
    }
    captured: list[dict[str, str]] = []

    class CapturingProvider:
        name = "openai-compatible"

        def generate(self, messages, *, model):
            captured.extend(messages)
            return SimpleNamespace(text="Answer [src/config.ts:1]", model=model)

    settings = LLMSettings(
        "openai-compatible", "remote-model", "https://example.test", "provider-key"
    )
    with patch("vibewiki.llm._provider", return_value=CapturingProvider()):
        result = ask_repository(
            tmp_path,
            artifact,
            {"question": "config", "remote_confirmed": True},
            settings,
        )

    payload = json.dumps(captured, ensure_ascii=False)
    assert _RAW_API_KEY not in payload
    assert _RAW_PASSWORD not in payload
    assert "src/config.ts:1" in payload
    assert "tokenCount" in payload
    assert source.read_text() == raw
    assert result["provider"] == "openai-compatible"


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
    assert result["mode"] == "general"


def test_analysis_modes_return_grounded_local_summaries(tmp_path: Path) -> None:
    artifact = {
        "facts": [
            {
                "semantic_key": "route:page:/signup",
                "kind": "route",
                "attributes": {"path": "/signup", "file": "app/page.tsx"},
                "evidence": [
                    {"path": "app/page.tsx", "line_start": 1, "line_end": 1}
                ],
            }
        ],
        "relations": [],
        "module_edges": [],
        "package_edges": [],
        "symbol_edges": [],
        "unknowns": [
            {
                "subject": "route:page:/signup",
                "reason": "No test evidence was found.",
                "evidence": [
                    {"path": "app/page.tsx", "line_start": 1, "line_end": 1}
                ],
            }
        ],
    }

    flow = ask_repository(tmp_path, artifact, {"question": "signup", "mode": "flow"})
    unknowns = ask_repository(
        tmp_path, artifact, {"question": "signup", "mode": "unknowns"}
    )

    assert flow["mode"] == "flow"
    assert flow["mode_label"] == "Flow explainer"
    assert "flow candidates" in flow["answer"]
    assert unknowns["mode"] == "unknowns"
    assert "No test evidence was found." in unknowns["answer"]
    assert unknowns["unknowns"] == [
        "route:page:/signup: No test evidence was found."
    ]


def test_analysis_mode_rejects_unknown_value(tmp_path: Path) -> None:
    with pytest.raises(VibeWikiError) as raised:
        ask_repository(tmp_path, {"facts": []}, {"question": "hello", "mode": "map"})

    assert raised.value.code is ErrorCode.INVALID_OUTPUT
    assert "analysis mode" in raised.value.message


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

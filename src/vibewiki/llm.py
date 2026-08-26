"""Grounded local Q&A orchestration for the VibeWiki artifact."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .errors import ErrorCode, VibeWikiError
from .providers import (
    EvidenceOnlyProvider,
    LLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)

MAX_QUESTION_CHARS = 4_000
MAX_HISTORY_TURNS = 6
MAX_RETRIEVED_NODES = 8
MAX_CONTEXT_CHARS = 24_000
MAX_EXCERPT_LINES = 36
ANALYSIS_MODES = frozenset({"general", "flow", "impact", "unknowns"})
MODE_LABELS = {
    "general": "Grounded discussion",
    "flow": "Flow explainer",
    "impact": "Impact analyzer",
    "unknowns": "Unknowns investigator",
}
_TOKEN = re.compile(r"[A-Za-z0-9_]{3,}")
_STOPWORDS = frozenset(
    {
        "about",
        "and",
        "are",
        "can",
        "connected",
        "connect",
        "does",
        "how",
        "the",
        "what",
        "which",
        "where",
        "when",
        "with",
        "file",
        "files",
        "source",
        "code",
        "cua",
        "là",
        "các",
        "cho",
        "của",
        "được",
        "nào",
        "những",
        "trong",
        "và",
    }
)

# These patterns run only on the bounded text that would be sent to a remote
# provider. They deliberately target literal values, not ordinary identifiers
# such as ``tokenCount`` or calls such as ``getToken()``.
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?P<prefix>(?<![A-Za-z0-9_])(?:"
    r"api[_-]?key|secret|password|passwd|token|"
    r"(?:client|server|database|db|access|refresh|auth|private|session|"
    r"signing|encryption|jwt)[_-]?(?:key|secret|token)"
    r")(?![A-Za-z0-9_])\s*(?:[:=])\s*)"
    r"(?P<quote>[\"'`])(?P<value>[^\r\n]*?)(?P=quote)",
    re.IGNORECASE,
)
_SENSITIVE_BARE_ASSIGNMENT = re.compile(
    r"(?P<prefix>(?<![A-Za-z0-9_])(?:"
    r"api[_-]?key|secret|password|passwd|token|"
    r"(?:client|server|database|db|access|refresh|auth|private|session|"
    r"signing|encryption|jwt)[_-]?(?:key|secret|token)"
    r")(?![A-Za-z0-9_])\s*(?:[:=])\s*)"
    r"(?P<value>(?![\"'`])[^\s,;}\]]+)",
    re.IGNORECASE,
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9][A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
    r"-----END (?:[A-Z0-9][A-Z0-9 ]+ )?PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_KNOWN_API_KEY = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|rk-[A-Za-z0-9_-]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|"
    r"AIza[0-9A-Za-z_-]{20,}|xox[baprs]-[0-9A-Za-z-]{10,})\b"
)
_BEARER_VALUE = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/=-]{12,})")


def _redact_sensitive_assignment(match: re.Match[str]) -> str:
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}[REDACTED]{quote}"


def _redact_bare_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    # Do not turn ordinary code references into secrets. Bare values that look
    # like literals (digits, separators, or mixed-case secret material) remain
    # protected; environment lookups and function/identifier references do not.
    safe_reference = re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?",
        value,
    )
    if safe_reference and (
        value.casefold() in {"true", "false", "null", "none", "undefined"}
        or any(character in value for character in ".()")
        or any(character.isupper() for character in value)
    ):
        return match.group(0)
    return f"{match.group('prefix')}[REDACTED]"


def _redact_private_key(match: re.Match[str]) -> str:
    """Keep one safe marker per line so prefixed citations stay addressable."""

    redacted_lines = []
    for line in match.group(0).splitlines():
        prefix = re.match(r"^(.*?:\d+:\s*)", line)
        redacted_lines.append(
            f"{prefix.group(1) if prefix else ''}[REDACTED_PRIVATE_KEY]"
        )
    return "\n".join(redacted_lines)


def redact_source_context(value: str) -> str:
    """Redact literal credentials from text before a remote LLM call.

    The function returns a new string and never touches the repository file or
    artifact. Path/line prefixes remain intact, so citations continue to point
    at the original source location.
    """

    redacted = _PRIVATE_KEY.sub(_redact_private_key, value)
    redacted = _SENSITIVE_ASSIGNMENT.sub(_redact_sensitive_assignment, redacted)
    redacted = _SENSITIVE_BARE_ASSIGNMENT.sub(_redact_bare_assignment, redacted)
    redacted = _JWT.sub("[REDACTED_JWT]", redacted)
    redacted = _KNOWN_API_KEY.sub("[REDACTED_API_KEY]", redacted)
    return _BEARER_VALUE.sub(r"\1[REDACTED_BEARER]", redacted)


@dataclass(frozen=True, slots=True)
class LLMSettings:
    provider: str
    model: str
    base_url: str
    api_key: str | None
    remote_confirmed: bool = False

    @classmethod
    def from_values(
        cls,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None,
        remote_confirmed: bool = False,
    ) -> LLMSettings:
        provider = provider.strip().casefold()
        model = model.strip()
        base_url = base_url.strip().rstrip("/")
        if provider not in {"none", "ollama", "openai-compatible", "openai"}:
            raise VibeWikiError(
                ErrorCode.LLM_UNAVAILABLE,
                "unsupported LLM provider; use none, ollama, or openai-compatible",
            )
        if not model:
            raise VibeWikiError(ErrorCode.LLM_UNAVAILABLE, "LLM model is empty")
        if not base_url:
            raise VibeWikiError(ErrorCode.LLM_UNAVAILABLE, "LLM base URL is empty")
        if provider in {"openai", "openai-compatible"} and not api_key:
            raise VibeWikiError(
                ErrorCode.LLM_UNAVAILABLE,
                "an API key is required for the configured provider",
            )
        return cls(provider, model, base_url, api_key, bool(remote_confirmed))

    @classmethod
    def from_environment(cls) -> LLMSettings:
        provider = os.environ.get("VIBEWIKI_LLM_PROVIDER", "none").strip().casefold()
        model = os.environ.get("VIBEWIKI_LLM_MODEL", "qwen2.5:7b").strip()
        if provider == "ollama":
            base_url = os.environ.get(
                "VIBEWIKI_LLM_BASE_URL", "http://127.0.0.1:11434"
            ).strip()
        else:
            base_url = os.environ.get(
                "VIBEWIKI_LLM_BASE_URL", "https://api.openai.com"
            ).strip()
        api_key = os.environ.get("VIBEWIKI_LLM_API_KEY")
        return cls.from_values(provider, model, base_url, api_key)


def _provider(settings: LLMSettings) -> LLMProvider:
    if settings.provider == "none":
        return EvidenceOnlyProvider()
    if settings.provider == "ollama":
        return OllamaProvider(settings.base_url)
    return OpenAICompatibleProvider(settings.base_url, settings.api_key or "")


def llm_status(settings: LLMSettings | None = None) -> dict[str, Any]:
    try:
        settings = settings or LLMSettings.from_environment()
    except VibeWikiError as error:
        return {"provider": "error", "configured": False, "message": error.message}
    return {
        "provider": settings.provider,
        "model": settings.model,
        "configured": settings.provider != "none",
        "mode": "local" if settings.provider == "ollama" else settings.provider,
        "base_url": settings.base_url,
        "has_api_key": bool(settings.api_key),
        "remote": settings.provider == "openai-compatible",
        "remote_confirmation_required": settings.provider == "openai-compatible",
        "remote_confirmed": bool(settings.remote_confirmed),
    }


def configure_llm(
    payload: dict[str, Any], current: LLMSettings | None = None
) -> LLMSettings:
    """Validate a UI-supplied runtime config without persisting credentials."""

    provider = payload.get("provider")
    if not isinstance(provider, str):
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "LLM provider is required")
    provider = provider.strip().casefold()
    default_base_url = (
        "http://127.0.0.1:11434"
        if provider == "ollama"
        else "https://api.openai.com"
    )
    model = payload.get("model", current.model if current else "qwen2.5:7b")
    base_url = payload.get(
        "base_url",
        current.base_url
        if current and current.provider == provider
        else default_base_url,
    )
    if not isinstance(model, str) or not isinstance(base_url, str):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "LLM model or base URL is invalid"
        )
    supplied_key = payload.get("api_key")
    if supplied_key is None:
        api_key = current.api_key if current and current.provider == provider else None
    elif isinstance(supplied_key, str):
        api_key = supplied_key.strip() or None
    else:
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "LLM API key is invalid")
    if provider == "none":
        api_key = None
    confirmation = payload.get("remote_confirmed", False)
    if not isinstance(confirmation, bool):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "remote LLM confirmation must be boolean"
        )
    same_configuration = bool(
        current
        and current.provider == provider
        and current.model == model.strip()
        and current.base_url == base_url.strip().rstrip("/")
        and (supplied_key is None or current.api_key == api_key)
    )
    remote_confirmed = (
        confirmation
        if "remote_confirmed" in payload
        else bool(current.remote_confirmed) if same_configuration and current else False
    )
    if provider != "openai-compatible":
        remote_confirmed = False
    return LLMSettings.from_values(
        provider, model, base_url, api_key, remote_confirmed=remote_confirmed
    )


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN.findall(value)
        if token.casefold() not in _STOPWORDS
    }


def _node_text(node: dict[str, Any]) -> str:
    attributes = node.get("attributes", {})
    evidence = " ".join(item.get("path", "") for item in node.get("evidence", []))
    return " ".join(
        str(value)
        for value in (
            node.get("id", ""),
            node.get("kind", ""),
            node.get("title", ""),
            node.get("meta", ""),
            json_safe(attributes),
            evidence,
        )
    )


def json_safe(value: object) -> str:
    """Serialize small artifact values without importing a second JSON API."""

    if isinstance(value, dict):
        return " ".join(f"{key} {json_safe(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(json_safe(item) for item in value)
    return str(value)


def normalize_markdown(value: str) -> str:
    """Repair common provider formatting glitches without changing code fences."""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n")
    segments = text.split("```")
    for index in range(0, len(segments), 2):
        segment = segments[index]
        segment = re.sub(
            r"(?<!\n)[ \t]+(-{3,})[ \t]+",
            r"\n\n\1\n\n",
            segment,
        )
        segment = re.sub(r"[ \t]+(?=#{1,4}\s)", "\n\n", segment)
        segment = re.sub(
            r"[ \t]+(?=\*\*(?:Bước|Step)\s+\d+\b)",
            "\n\n",
            segment,
        )
        segment = re.sub(r"[ \t]+(?=[-*+]\s+(?:\*\*|[A-ZÀ-Ỹ]))", "\n", segment)
        segment = re.sub(r"[ \t]+(?=\d+[.)]\s+\*\*)", "\n", segment)
        segments[index] = segment
    return "```".join(segments).strip()


def _rank_nodes(
    nodes: Sequence[dict[str, Any]], query_tokens: set[str]
) -> list[dict[str, Any]]:
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for node in nodes:
        text = _node_text(node)
        tokens = _tokens(text)
        score = len(query_tokens & tokens)
        if score and node.get("kind") in {
            "route",
            "function",
            "symbol",
            "module",
            "file",
        }:
            score += 1
        if score:
            scored.append((score, str(node.get("id", "")), node))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:MAX_RETRIEVED_NODES]]


def _evidence_key(item: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(item.get("path", "")),
        int(item.get("line_start", 1)),
        int(item.get("line_end", item.get("line_start", 1))),
    )


def _retrieval(
    root: Path, artifact: dict[str, Any], question: str, mode: str = "general"
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    str,
]:
    nodes = []
    nodes.extend(
        {
            "id": fact["semantic_key"],
            "kind": fact["kind"],
            "title": fact["attributes"].get("path")
            or fact["attributes"].get("name")
            or fact["semantic_key"],
            "meta": fact["attributes"].get("file", ""),
            "attributes": fact["attributes"],
            "evidence": fact["evidence"],
        }
        for fact in artifact.get("facts", [])
    )
    for group in ("modules", "packages", "symbols"):
        nodes.extend(
            {
                "id": item["id"],
                "kind": item["kind"],
                "title": item["attributes"].get("path")
                or item["attributes"].get("name")
                or item["id"],
                "meta": item["attributes"].get("file", ""),
                "attributes": item["attributes"],
                "evidence": item["evidence"],
            }
            for item in artifact.get(group, [])
        )
    selected = _rank_nodes(nodes, _tokens(question))
    selected_ids = {node["id"] for node in selected}
    edges = (
        artifact.get("relations", [])
        + artifact.get("module_edges", [])
        + artifact.get("package_edges", [])
        + artifact.get("symbol_edges", [])
    )
    for edge in edges:
        if edge["source"] in selected_ids or edge["target"] in selected_ids:
            selected.append(
                next((node for node in nodes if node["id"] == edge["source"]), None)
                or next((node for node in nodes if node["id"] == edge["target"]), None)
            )
            if len(selected) >= MAX_RETRIEVED_NODES + 2:
                break
    selected = [node for node in selected if node is not None]
    seen_ids: set[str] = set()
    unique_nodes = []
    for node in selected:
        if node["id"] in seen_ids:
            continue
        seen_ids.add(node["id"])
        unique_nodes.append(node)
    selected = unique_nodes
    selected_ids = {node["id"] for node in selected}
    relevant_edges = [
        edge
        for edge in edges
        if edge["source"] in selected_ids or edge["target"] in selected_ids
    ][:16]
    evidence: list[dict[str, Any]] = []
    for node in selected:
        evidence.extend(node.get("evidence", []))
    if mode == "unknowns":
        for unknown in artifact.get("unknowns", [])[:12]:
            evidence.extend(unknown.get("evidence", []))
    unique_evidence = sorted(
        {_evidence_key(item): item for item in evidence}.values(),
        key=_evidence_key,
    )
    context_parts: list[str] = [
        f"ANALYSIS MODE: {MODE_LABELS.get(mode, MODE_LABELS['general'])}"
    ]
    if mode == "unknowns":
        for unknown in artifact.get("unknowns", [])[:12]:
            context_parts.append(
                f"UNKNOWN {unknown.get('subject', '')}: {unknown.get('reason', '')}"
            )
            for item in unknown.get("evidence", [])[:2]:
                context_parts.append(
                    "UNKNOWN EVIDENCE "
                    f"{item.get('path', '')}:{item.get('line_start', 1)}"
                )
    for edge in relevant_edges:
        context_parts.append(
            f"EDGE {edge['source']} --{edge['relation']}--> {edge['target']}"
        )
    for node in selected:
        context_parts.append(f"NODE {node['id']} ({node['kind']})")
        context_parts.append(f"attributes: {json_safe(node.get('attributes', {}))}")
        for item in node.get("evidence", [])[:4]:
            path = item.get("path", "")
            line = int(item.get("line_start", 1))
            context_parts.append(f"EVIDENCE {path}:{line}")
            absolute = root / Path(path)
            try:
                lines = absolute.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            end_line = min(len(lines), line + MAX_EXCERPT_LINES - 1)
            for number in range(max(1, line - 2), end_line + 1):
                context_parts.append(f"{path}:{number}: {lines[number - 1]}")
    return (
        selected,
        unique_evidence,
        relevant_edges,
        "\n".join(context_parts)[:MAX_CONTEXT_CHARS],
    )


def _evidence_only_answer(
    mode: str,
    selected: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
    artifact: dict[str, Any],
) -> str:
    if mode == "unknowns":
        unknowns = artifact.get("unknowns", [])
        if not unknowns:
            return "Evidence-only: chưa phát hiện unknown nào trong artifact hiện tại."
        return "Evidence-only: các unknown cần review:\n" + "\n".join(
            f"- {item.get('subject', 'unknown')}: {item.get('reason', '')}"
            for item in unknowns[:8]
        )
    if not selected:
        return "Evidence-only: chưa tìm thấy node phù hợp trong graph hiện tại."
    prefix = {
        "flow": "Evidence-only: flow candidates theo graph",
        "impact": "Evidence-only: impact neighborhood theo graph",
    }.get(mode, "Evidence-only: evidence liên quan")
    lines = [f"{prefix}:"]
    lines.extend(f"- {node['id']}" for node in selected[:8])
    if edges and mode in {"flow", "impact"}:
        lines.append("Edges:")
        lines.extend(
            f"- {edge['source']} --{edge['relation']}--> {edge['target']}"
            for edge in edges[:8]
        )
    return "\n".join(lines)


def _history(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[-MAX_HISTORY_TURNS:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            result.append({"role": item["role"], "content": content[:4_000]})
    return result


def ask_repository(
    root: Path,
    artifact: dict[str, Any],
    payload: dict[str, Any],
    settings: LLMSettings | None = None,
) -> dict[str, Any]:
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "question is required")
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "question exceeds the local safety limit"
        )
    mode = payload.get("mode", "general")
    if not isinstance(mode, str) or mode not in ANALYSIS_MODES:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "analysis mode must be general, flow, impact, or unknowns",
        )
    settings = settings or LLMSettings.from_environment()
    provider = _provider(settings)
    is_remote = provider.name in {"openai", "openai-compatible"}
    if is_remote and not (
        payload.get("remote_confirmed") is True
        or getattr(settings, "remote_confirmed", False)
    ):
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "remote LLM confirmation is required before source excerpts are sent; "
            "review the data boundary and confirm in LLM setup",
        )
    selected, evidence, edges, context = _retrieval(root, artifact, question, mode)
    history = _history(payload.get("history"))
    if is_remote:
        context = redact_source_context(context)
        history = [
            {"role": item["role"], "content": redact_source_context(item["content"])}
            for item in history
        ]
    messages = [
        {
            "role": "system",
            "content": (
                "You are VibeWiki, a codebase analysis assistant. Use only the "
                "retrieved evidence below. Do not invent runtime behavior. Cite "
                "claims as [path:line]. If evidence is insufficient, say Unknown. "
                "Answer in Vietnamese when the question is Vietnamese. Follow "
                f"the requested analysis mode: {MODE_LABELS[mode]}.\n\n"
                f"Retrieved evidence:\n{context or 'No matching evidence was found.'}"
            ),
        },
        *history,
        {"role": "user", "content": question},
    ]
    response = provider.generate(messages, model=settings.model)
    answer = (
        _evidence_only_answer(mode, selected, edges, artifact)
        if provider.name == "none"
        else response.text
    )
    answer = normalize_markdown(answer)
    cited = [
        item
        for item in evidence
        if f"{item.get('path')}:{item.get('line_start')}" in answer
    ]
    grounded = provider.name == "none" or bool(cited) or not selected
    unknowns = (
        [
            f"{item.get('subject', 'unknown')}: {item.get('reason', '')}"
            for item in artifact.get("unknowns", [])[:8]
        ]
        if mode == "unknowns" and provider.name == "none"
        else []
        if grounded
        else ["Model response did not cite one of the retrieved source locations."]
    )
    return {
        "answer": answer,
        "citations": cited or evidence[:8],
        "confidence": "medium" if grounded else "low",
        "grounded": grounded,
        "unknowns": unknowns,
        "provider": provider.name,
        "model": response.model,
        "retrieved": [node["id"] for node in selected],
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "schema_version": SCHEMA_VERSION,
    }

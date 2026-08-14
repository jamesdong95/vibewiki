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


@dataclass(frozen=True, slots=True)
class LLMSettings:
    provider: str
    model: str
    base_url: str
    api_key: str | None

    @classmethod
    def from_environment(cls) -> LLMSettings:
        provider = os.environ.get("VIBEWIKI_LLM_PROVIDER", "none").strip().casefold()
        model = os.environ.get("VIBEWIKI_LLM_MODEL", "qwen2.5:7b").strip()
        if provider not in {"none", "ollama", "openai-compatible", "openai"}:
            raise VibeWikiError(
                ErrorCode.LLM_UNAVAILABLE,
                "unsupported VIBEWIKI_LLM_PROVIDER; use none, ollama, or "
                "openai-compatible",
            )
        if provider == "ollama":
            base_url = os.environ.get(
                "VIBEWIKI_LLM_BASE_URL", "http://127.0.0.1:11434"
            ).strip()
        else:
            base_url = os.environ.get(
                "VIBEWIKI_LLM_BASE_URL", "https://api.openai.com"
            ).strip()
        api_key = os.environ.get("VIBEWIKI_LLM_API_KEY")
        if provider in {"openai", "openai-compatible"} and not api_key:
            raise VibeWikiError(
                ErrorCode.LLM_UNAVAILABLE,
                "VIBEWIKI_LLM_API_KEY is required for the configured provider",
            )
        if not model:
            raise VibeWikiError(
                ErrorCode.LLM_UNAVAILABLE, "VIBEWIKI_LLM_MODEL is empty"
            )
        return cls(provider, model, base_url, api_key)


def _provider(settings: LLMSettings) -> LLMProvider:
    if settings.provider == "none":
        return EvidenceOnlyProvider()
    if settings.provider == "ollama":
        return OllamaProvider(settings.base_url)
    return OpenAICompatibleProvider(settings.base_url, settings.api_key or "")


def llm_status() -> dict[str, Any]:
    try:
        settings = LLMSettings.from_environment()
    except VibeWikiError as error:
        return {"provider": "error", "configured": False, "message": error.message}
    return {
        "provider": settings.provider,
        "model": settings.model,
        "configured": settings.provider != "none",
        "mode": "local" if settings.provider == "ollama" else settings.provider,
    }


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
    root: Path, artifact: dict[str, Any], question: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
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
    evidence: list[dict[str, Any]] = []
    for node in selected:
        evidence.extend(node.get("evidence", []))
    unique_evidence = sorted(
        {_evidence_key(item): item for item in evidence}.values(),
        key=_evidence_key,
    )
    context_parts: list[str] = []
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
    return selected, unique_evidence, "\n".join(context_parts)[:MAX_CONTEXT_CHARS]


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
    root: Path, artifact: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "question is required")
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT, "question exceeds the local safety limit"
        )
    selected, evidence, context = _retrieval(root, artifact, question)
    settings = LLMSettings.from_environment()
    provider = _provider(settings)
    messages = [
        {
            "role": "system",
            "content": (
                "You are VibeWiki, a codebase analysis assistant. Use only the "
                "retrieved evidence below. Do not invent runtime behavior. Cite "
                "claims as [path:line]. If evidence is insufficient, say Unknown. "
                "Answer in Vietnamese when the question is Vietnamese.\n\n"
                f"Retrieved evidence:\n{context or 'No matching evidence was found.'}"
            ),
        },
        *_history(payload.get("history")),
        {"role": "user", "content": question},
    ]
    response = provider.generate(messages, model=settings.model)
    cited = [
        item
        for item in evidence
        if f"{item.get('path')}:{item.get('line_start')}" in response.text
    ]
    grounded = provider.name == "none" or bool(cited) or not selected
    unknowns = [] if grounded else [
        "Model response did not cite one of the retrieved source locations."
    ]
    return {
        "answer": response.text,
        "citations": cited or evidence[:8],
        "confidence": "medium" if grounded else "low",
        "grounded": grounded,
        "unknowns": unknowns,
        "provider": provider.name,
        "model": response.model,
        "retrieved": [node["id"] for node in selected],
        "schema_version": SCHEMA_VERSION,
    }

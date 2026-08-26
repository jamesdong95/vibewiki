"""Opt-in, bounded, local-only memory for grounded VibeWiki discussions."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .discovery.manifest import canonical_json
from .errors import ErrorCode, VibeWikiError

DISCUSSIONS_SCHEMA_VERSION = 1
DISCUSSIONS_FILENAME = "discussions.json"
MAX_THREADS = 12
MAX_MESSAGES_PER_THREAD = 24
MAX_FEEDBACK_PER_THREAD = 48
MAX_MESSAGE_CHARS = 4_000
MAX_TITLE_CHARS = 160
MAX_NOTE_CHARS = 1_000
MAX_STATE_BYTES = 256 * 1024
_ID_PREFIX = "d_"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_ID_PREFIX)
        and 8 <= len(value) <= 80
        and all(character.isalnum() or character in "_-" for character in value)
    )


def _safe_scope(value: object) -> bool:
    return (
        isinstance(value, str)
        and 3 <= len(value) <= 160
        and all(character.isalnum() or character in "_-" for character in value)
    )


def _clean_text(value: object, *, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, f"{name} is required")
    return " ".join(value.strip().split())[:limit]


def _bounded_text(value: object, *, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, f"{name} is required")
    return value.strip()[:limit]


def discussion_scope_id(workspace_id: str | None, root: Path) -> str:
    """Return a non-secret stable scope for an imported or direct workspace."""

    if _safe_scope(workspace_id):
        return str(workspace_id)
    digest = hashlib.sha256(str(root.absolute()).encode("utf-8")).hexdigest()[:24]
    return f"direct_{digest}"


def artifact_fingerprint(root: Path) -> str:
    """Hash only deterministic manifest metadata, never source contents."""

    manifest_path = root / ".vibewiki" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        manifest = {"missing_manifest": True}
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def _new_state(scope_id: str) -> dict[str, Any]:
    return {
        "schema_version": DISCUSSIONS_SCHEMA_VERSION,
        "workspace_id": scope_id,
        "threads": [],
    }


def _public_thread(thread: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    messages = [
        {
            "id": item["id"],
            "role": item["role"],
            "content": item["content"],
            "created_at": item["created_at"],
            "artifact_fingerprint": item["artifact_fingerprint"],
            "stale": item["artifact_fingerprint"] != fingerprint,
        }
        for item in thread.get("messages", [])
    ]
    stale = any(item["stale"] for item in messages)
    return {
        "id": thread["id"],
        "title": thread["title"],
        "created_at": thread["created_at"],
        "updated_at": thread["updated_at"],
        "stale": stale,
        "messages": messages,
        "feedback": list(thread.get("feedback", [])),
    }


class DiscussionStore:
    """Persist bounded discussion records outside the repository artifact."""

    def __init__(self, state_dir: str | Path, scope_id: str) -> None:
        if not _safe_scope(scope_id):
            raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "discussion scope is invalid")
        self.root = Path(state_dir).expanduser().absolute() / "discussions"
        self.scope_id = scope_id
        self.path = self.root / f"{scope_id}.json"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(stat.S_IRWXU)
        except OSError:
            pass

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return _new_state(self.scope_id)
        try:
            if self.path.stat().st_size > MAX_STATE_BYTES:
                return _new_state(self.scope_id)
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return _new_state(self.scope_id)
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != DISCUSSIONS_SCHEMA_VERSION
            or value.get("workspace_id") != self.scope_id
        ):
            return _new_state(self.scope_id)
        threads = value.get("threads")
        if not isinstance(threads, list):
            return _new_state(self.scope_id)
        valid: list[dict[str, Any]] = []
        for thread in threads[:MAX_THREADS]:
            if not isinstance(thread, dict) or not _safe_id(thread.get("id")):
                continue
            messages = thread.get("messages", [])
            if not isinstance(messages, list):
                messages = []
            clean_messages = []
            for item in messages[-MAX_MESSAGES_PER_THREAD:]:
                if (
                    not isinstance(item, dict)
                    or not _safe_id(item.get("id"))
                    or item.get("role") not in {"user", "assistant"}
                    or not isinstance(item.get("content"), str)
                    or not isinstance(item.get("artifact_fingerprint"), str)
                ):
                    continue
                clean_messages.append(
                    {
                        "id": item["id"],
                        "role": item["role"],
                        "content": item["content"][:MAX_MESSAGE_CHARS],
                        "created_at": str(item.get("created_at", "")),
                        "artifact_fingerprint": item["artifact_fingerprint"][:128],
                    }
                )
            feedback = thread.get("feedback", [])
            if not isinstance(feedback, list):
                feedback = []
            clean_feedback = [
                item
                for item in feedback[-MAX_FEEDBACK_PER_THREAD:]
                if isinstance(item, dict)
                and _safe_id(item.get("id"))
                and _safe_id(item.get("message_id"))
                and item.get("rating") in {"up", "down"}
            ]
            valid.append(
                {
                    "id": thread["id"],
                    "title": str(thread.get("title", "Discussion"))[:MAX_TITLE_CHARS],
                    "created_at": str(thread.get("created_at", "")),
                    "updated_at": str(thread.get("updated_at", "")),
                    "messages": clean_messages,
                    "feedback": clean_feedback,
                }
            )
        return {**_new_state(self.scope_id), "threads": valid}

    def _write(self, state: dict[str, Any]) -> None:
        state = {**_new_state(self.scope_id), "threads": state.get("threads", [])}
        encoded = canonical_json(state).encode("utf-8")
        while len(encoded) > MAX_STATE_BYTES and state["threads"]:
            oldest = state["threads"][-1]
            if oldest.get("messages"):
                oldest["messages"] = oldest["messages"][1:]
            elif oldest.get("feedback"):
                oldest["feedback"] = oldest["feedback"][1:]
            else:
                state["threads"].pop()
            encoded = canonical_json(state).encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "discussion memory exceeds the local safety limit",
            )
        fd, temporary = tempfile.mkstemp(
            prefix="discussion.", suffix=".tmp", dir=self.root
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def list(self, fingerprint: str) -> list[dict[str, Any]]:
        return [
            _public_thread(thread, fingerprint) for thread in self._load()["threads"]
        ]

    def create(self, title: str | None = None) -> dict[str, Any]:
        now = _now()
        thread = {
            "id": f"{_ID_PREFIX}{secrets.token_urlsafe(12)}",
            "title": (
                _clean_text(title, name="discussion title", limit=MAX_TITLE_CHARS)
                if title
                else "New grounded discussion"
            ),
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "feedback": [],
        }
        state = self._load()
        state["threads"] = [thread, *state["threads"]][:MAX_THREADS]
        self._write(state)
        return thread

    def _thread(self, thread_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not _safe_id(thread_id):
            raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "discussion was not found")
        state = self._load()
        thread = next(
            (item for item in state["threads"] if item["id"] == thread_id), None
        )
        if thread is None:
            raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "discussion was not found")
        return state, thread

    def history(
        self, thread_id: str, fingerprint: str, *, allow_stale: bool = False
    ) -> tuple[list[dict[str, str]], bool]:
        _state, thread = self._thread(thread_id)
        public = _public_thread(thread, fingerprint)
        if public["stale"] and not allow_stale:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "discussion context is stale after a rescan; confirm before continuing",
            )
        history = [
            {"role": item["role"], "content": item["content"]}
            for item in thread["messages"][-12:]
        ]
        return history, bool(public["stale"])

    def append(
        self,
        thread_id: str,
        *,
        question: str,
        answer: str,
        fingerprint: str,
    ) -> dict[str, Any]:
        state, thread = self._thread(thread_id)
        question = _bounded_text(question, name="question", limit=MAX_MESSAGE_CHARS)
        answer = _bounded_text(answer, name="answer", limit=MAX_MESSAGE_CHARS)
        now = _now()
        messages = thread["messages"]
        question_id = f"{_ID_PREFIX}{secrets.token_urlsafe(12)}"
        answer_id = f"{_ID_PREFIX}{secrets.token_urlsafe(12)}"
        messages.extend(
            [
                {
                    "id": question_id,
                    "role": "user",
                    "content": question,
                    "created_at": now,
                    "artifact_fingerprint": fingerprint,
                },
                {
                    "id": answer_id,
                    "role": "assistant",
                    "content": answer,
                    "created_at": now,
                    "artifact_fingerprint": fingerprint,
                },
            ]
        )
        thread["messages"] = messages[-MAX_MESSAGES_PER_THREAD:]
        thread["title"] = (
            thread["title"] if len(messages) > 2 else question[:MAX_TITLE_CHARS]
        )
        thread["updated_at"] = now
        self._write(state)
        return {"thread_id": thread_id, "message_id": answer_id}

    def feedback(
        self,
        thread_id: str,
        *,
        message_id: str,
        rating: str,
        note: str | None = None,
        citation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state, thread = self._thread(thread_id)
        if not _safe_id(message_id) or not any(
            item["id"] == message_id and item["role"] == "assistant"
            for item in thread["messages"]
        ):
            raise VibeWikiError(
                ErrorCode.PATH_NOT_FOUND, "answer message was not found"
            )
        if rating not in {"up", "down"}:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT, "feedback rating must be up or down"
            )
        item: dict[str, Any] = {
            "id": f"{_ID_PREFIX}{secrets.token_urlsafe(12)}",
            "message_id": message_id,
            "rating": rating,
            "created_at": _now(),
        }
        if note:
            item["note"] = _clean_text(note, name="feedback note", limit=MAX_NOTE_CHARS)
        if citation:
            path = citation.get("path")
            if (
                isinstance(path, str)
                and path
                and not Path(path).is_absolute()
                and ".." not in Path(path).parts
            ):
                try:
                    line_start = max(1, int(citation.get("line_start", 1)))
                    line_end = max(
                        line_start,
                        int(citation.get("line_end", line_start)),
                    )
                except (TypeError, ValueError):
                    line_start = line_end = 1
                item["citation"] = {
                    "path": path[:400],
                    "line_start": line_start,
                    "line_end": line_end,
                }
        thread["feedback"] = [*thread.get("feedback", []), item][
            -MAX_FEEDBACK_PER_THREAD:
        ]
        thread["updated_at"] = _now()
        self._write(state)
        return item

    def clear(self, thread_id: str) -> None:
        state, _thread = self._thread(thread_id)
        state["threads"] = [
            item for item in state["threads"] if item["id"] != thread_id
        ]
        self._write(state)


__all__ = [
    "DISCUSSIONS_FILENAME",
    "DISCUSSIONS_SCHEMA_VERSION",
    "DiscussionStore",
    "MAX_MESSAGE_CHARS",
    "artifact_fingerprint",
    "discussion_scope_id",
]

"""Local human-review state for unknowns and graph change subjects."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import MANIFEST_DIRECTORY
from .discovery.manifest import canonical_json
from .errors import ErrorCode, VibeWikiError

REVIEWS_FILENAME = "reviews.json"
REVIEWS_SCHEMA_VERSION = 1
MAX_REVIEW_ITEMS = 5_000
MAX_REVIEW_SUBJECT_CHARS = 256
MAX_REVIEW_NOTE_CHARS = 2_000
REVIEW_STATUSES = frozenset({"open", "reviewed"})


def _reviews_path(root: Path) -> Path:
    return Path(root) / MANIFEST_DIRECTORY / REVIEWS_FILENAME


def _empty_reviews() -> dict[str, Any]:
    return {"items": {}, "schema_version": REVIEWS_SCHEMA_VERSION}


def _invalid(message: str) -> VibeWikiError:
    return VibeWikiError(ErrorCode.INVALID_OUTPUT, f"review state: {message}")


def _validate_subject(subject: Any) -> str:
    if not isinstance(subject, str) or not subject.strip():
        raise _invalid("subject is required")
    value = subject.strip()
    if "\x00" in value or len(value) > MAX_REVIEW_SUBJECT_CHARS:
        raise _invalid(
            f"subject must be 1-{MAX_REVIEW_SUBJECT_CHARS} characters without NUL"
        )
    return value


def _validate_note(note: Any) -> str:
    if note is None:
        return ""
    if not isinstance(note, str):
        raise _invalid("note must be a string")
    if "\x00" in note or len(note) > MAX_REVIEW_NOTE_CHARS:
        raise _invalid(f"note must be at most {MAX_REVIEW_NOTE_CHARS} characters")
    return note.strip()


def _validate_item(subject: str, item: Any) -> dict[str, str]:
    if not isinstance(item, dict):
        raise _invalid("review item is invalid")
    status = item.get("status")
    if status not in REVIEW_STATUSES:
        raise _invalid("review status must be open or reviewed")
    updated_at = item.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        raise _invalid("review timestamp is invalid")
    return {
        "note": _validate_note(item.get("note", "")),
        "status": status,
        "subject": _validate_subject(subject),
        "updated_at": updated_at,
    }


def _normalise_reviews(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != REVIEWS_SCHEMA_VERSION
    ):
        raise _invalid("reviews artifact is invalid")
    items = value.get("items")
    if not isinstance(items, dict) or len(items) > MAX_REVIEW_ITEMS:
        raise _invalid("reviews artifact has too many items or is invalid")
    normalised = {
        subject: _validate_item(subject, item)
        for subject, item in items.items()
    }
    return {"items": normalised, "schema_version": REVIEWS_SCHEMA_VERSION}


def load_reviews(root: Path) -> dict[str, Any]:
    """Load local review state without reading or persisting source content."""

    path = _reviews_path(root)
    if not path.is_file():
        return _empty_reviews()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise _invalid("reviews artifact is unreadable") from error
    return _normalise_reviews(value)


def _output_directory(root: Path) -> Path:
    output = Path(root) / MANIFEST_DIRECTORY
    try:
        details = output.lstat()
    except FileNotFoundError:
        output.mkdir()
        return output
    except OSError as error:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "permission denied while writing review state",
        ) from error
    if output.is_symlink() or not stat.S_ISDIR(details.st_mode):
        raise _invalid("review output directory is not safe")
    return output


def _write_reviews(root: Path, value: dict[str, Any]) -> None:
    output = _output_directory(root)
    target = output / REVIEWS_FILENAME
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output, prefix=".reviews-", suffix=".tmp", delete=False
        ) as stream:
            temporary = stream.name
            stream.write(canonical_json(value).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    except OSError as error:
        raise VibeWikiError(
            ErrorCode.PERMISSION_DENIED,
            "permission denied while writing review state",
        ) from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def set_review(
    root: Path,
    subject: Any,
    status: Any,
    note: Any = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Set or reopen one review item and return the item plus full state."""

    value = load_reviews(root)
    clean_subject = _validate_subject(subject)
    if status not in REVIEW_STATUSES:
        raise _invalid("review status must be open or reviewed")
    existing = value["items"].get(clean_subject)
    clean_note = (
        _validate_note(note)
        if note is not None
        else (existing.get("note", "") if existing else "")
    )
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    review = {
        "note": clean_note,
        "status": status,
        "subject": clean_subject,
        "updated_at": now,
    }
    if existing is None and len(value["items"]) >= MAX_REVIEW_ITEMS:
        raise _invalid(f"review queue is limited to {MAX_REVIEW_ITEMS} items")
    value["items"][clean_subject] = review
    _write_reviews(root, value)
    return review, value


def review_counts(value: dict[str, Any]) -> dict[str, int]:
    items = value.get("items", {})
    return {
        "open": sum(item.get("status") == "open" for item in items.values()),
        "reviewed": sum(item.get("status") == "reviewed" for item in items.values()),
        "total": len(items),
    }


__all__ = [
    "MAX_REVIEW_ITEMS",
    "MAX_REVIEW_NOTE_CHARS",
    "MAX_REVIEW_SUBJECT_CHARS",
    "REVIEWS_FILENAME",
    "REVIEWS_SCHEMA_VERSION",
    "load_reviews",
    "review_counts",
    "set_review",
]

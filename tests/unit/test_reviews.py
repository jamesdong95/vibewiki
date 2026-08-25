from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.reviews import load_reviews, review_counts, set_review, set_reviews


def test_review_state_round_trips_atomically_and_counts_statuses(
    tmp_path: Path,
) -> None:
    assert load_reviews(tmp_path) == {"items": {}, "schema_version": 1}

    reviewed, state = set_review(
        tmp_path,
        "unknown:missing-test",
        "reviewed",
        "Confirmed with the product owner.",
    )
    assert reviewed["subject"] == "unknown:missing-test"
    assert reviewed["status"] == "reviewed"
    assert reviewed["note"] == "Confirmed with the product owner."
    assert review_counts(state) == {"open": 0, "reviewed": 1, "total": 1}

    reopened, state = set_review(tmp_path, "unknown:missing-test", "open")
    assert reopened["status"] == "open"
    assert reopened["note"] == reviewed["note"]
    assert review_counts(state) == {"open": 1, "reviewed": 0, "total": 1}
    assert json.loads(
        (tmp_path / ".vibewiki/reviews.json").read_text(encoding="utf-8")
    )["items"]["unknown:missing-test"]["status"] == "open"


@pytest.mark.parametrize(
    ("subject", "status", "note", "message"),
    [
        ("", "reviewed", "", "subject is required"),
        ("unknown:item", "pending", "", "status must be open or reviewed"),
        ("unknown:item", "open", "x" * 2_001, "note must be at most"),
        ("unknown\x00item", "open", "", "without NUL"),
    ],
)
def test_review_state_rejects_invalid_input(
    tmp_path: Path,
    subject: str,
    status: str,
    note: str,
    message: str,
) -> None:
    with pytest.raises(VibeWikiError) as raised:
        set_review(tmp_path, subject, status, note)

    assert raised.value.code is ErrorCode.INVALID_OUTPUT
    assert message in raised.value.message


def test_review_state_rejects_corrupt_artifact(tmp_path: Path) -> None:
    output = tmp_path / ".vibewiki"
    output.mkdir()
    (output / "reviews.json").write_text('{"items":{}}', encoding="utf-8")

    with pytest.raises(VibeWikiError, match="reviews artifact is invalid"):
        load_reviews(tmp_path)


def test_review_batch_is_atomic_and_preserves_existing_notes(tmp_path: Path) -> None:
    set_review(tmp_path, "unknown:first", "reviewed", "Keep this note.")

    updated, state = set_reviews(
        tmp_path,
        [
            {"subject": "unknown:first", "status": "open"},
            {"subject": "unknown:second", "status": "reviewed"},
        ],
    )

    assert [item["subject"] for item in updated] == [
        "unknown:first",
        "unknown:second",
    ]
    assert state["items"]["unknown:first"]["note"] == "Keep this note."
    assert review_counts(state) == {"open": 1, "reviewed": 1, "total": 2}

    with pytest.raises(VibeWikiError, match="duplicate subject"):
        set_reviews(
            tmp_path,
            [
                {"subject": "unknown:third", "status": "reviewed"},
                {"subject": "unknown:third", "status": "open"},
            ],
        )
    assert "unknown:third" not in load_reviews(tmp_path)["items"]

from __future__ import annotations

from pathlib import Path

import pytest

from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.intent import (
    compare_product_intent,
    load_product_seed,
    write_product_seed,
)


def _artifact() -> dict:
    return {
        "facts": [
            {
                "attributes": {"path": "/signup"},
                "evidence": [{"path": "app/signup/page.tsx", "line_start": 1}],
                "kind": "route",
                "semantic_key": "route:page:/signup",
            },
            {
                "attributes": {"file": "tests/signup.test.ts"},
                "evidence": [{"path": "tests/signup.test.ts", "line_start": 1}],
                "kind": "test",
                "semantic_key": "test:tests/signup.test.ts",
            },
        ],
        "inventory": {
            "files": [
                {"path": "tests/signup.test.ts", "language": "typescript"}
            ]
        },
    }


def test_product_intent_compares_observed_and_missing_expectations(
    tmp_path: Path,
) -> None:
    (tmp_path / "product.seed.yaml").write_text(
        "product:\n"
        "  name: Demo\n"
        "audience: developers\n"
        "flows:\n"
        "  - id: signup\n"
        "    name: Signup\n"
        "    expected:\n"
        "      - route: /signup\n"
        "      - test: tests/signup.test.ts\n"
        "      - api: /api/users\n",
        encoding="utf-8",
    )

    result = compare_product_intent(tmp_path, _artifact())

    assert result["configured"] is True
    assert result["counts"] == {
        "flows": 1,
        "gaps": 1,
        "observed": 0,
        "partial": 1,
    }
    expected = {item["kind"]: item for item in result["flows"][0]["expected"]}
    assert expected["route"]["status"] == "observed"
    assert expected["test"]["status"] == "observed"
    assert expected["api"]["status"] == "not_observed"
    assert result["gaps"][0]["subject"] == "intent:signup:api:/api/users"


def test_product_intent_supports_goals_and_single_aliases(tmp_path: Path) -> None:
    (tmp_path / "product.seed.yaml").write_text(
        "product: Demo\n"
        "goals:\n"
        "  - id: home\n"
        "    expected_outcomes:\n"
        "      - file: src/main.js\n",
        encoding="utf-8",
    )
    result = load_product_seed(tmp_path)
    assert result is not None
    assert result["flows"][0]["expected"] == [
        {"kind": "file", "value": "src/main.js", "label": "src/main.js"}
    ]


def test_product_intent_is_disabled_without_seed(tmp_path: Path) -> None:
    result = compare_product_intent(tmp_path, _artifact())
    assert result == {
        "configured": False,
        "flows": [],
        "gaps": [],
        "counts": {"flows": 0, "gaps": 0, "observed": 0, "partial": 0},
    }


def test_product_intent_rejects_unknown_seed_fields(tmp_path: Path) -> None:
    (tmp_path / "product.seed.yaml").write_text(
        "product: Demo\n"
        "flows:\n"
        "  - id: home\n"
        "    expected:\n"
        "      - route: /\n"
        "    surprise: true\n",
        encoding="utf-8",
    )
    with pytest.raises(VibeWikiError) as raised:
        load_product_seed(tmp_path)
    assert raised.value.code is ErrorCode.INVALID_OUTPUT
    assert "surprise" in raised.value.message


def test_product_intent_writer_round_trips_canonical_seed(tmp_path: Path) -> None:
    seed = {
        "product": {"name": "Demo"},
        "audience": "developers",
        "flows": [
            {
                "id": "signup",
                "name": "Signup",
                "expected": [
                    {"kind": "route", "value": "/signup"},
                    {"kind": "api", "value": "/api/users"},
                ],
            }
        ],
    }

    written = write_product_seed(tmp_path, seed)

    assert written["flows"][0]["expected"][0]["label"] == "/signup"
    assert load_product_seed(tmp_path) == written
    text = (tmp_path / "product.seed.yaml").read_text(encoding="utf-8")
    assert "Demo" in text
    assert "api" in text

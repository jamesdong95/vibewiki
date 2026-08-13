from __future__ import annotations

import json
from pathlib import Path

from vibewiki import ANALYZER_VERSION, SCHEMA_VERSION
from vibewiki.discovery.manifest import (
    ManifestFile,
    build_manifest,
    canonical_json,
    manifest_file_dict,
)


def test_manifest_file_dict_is_canonical_and_contains_no_machine_path() -> None:
    item = ManifestFile(
        path="app/page.tsx",
        language="tsx",
        size=12,
        sha256="a" * 64,
    )

    assert manifest_file_dict(item) == {
        "language": "tsx",
        "path": "app/page.tsx",
        "sha256": "a" * 64,
        "size": 12,
    }
    serialized = canonical_json({"z": 1, "a": "value"})
    assert serialized == '{"a":"value","z":1}\n'
    assert "/" not in json.loads(serialized)["a"]


def test_manifest_has_stable_top_level_and_file_order() -> None:
    manifest = build_manifest(
        [
            ManifestFile("src/z.ts", "typescript", 2, "b" * 64),
            ManifestFile("app/a.tsx", "tsx", 1, "a" * 64),
        ]
    )

    assert manifest == {
        "analyzer_version": ANALYZER_VERSION,
        "files": [
            {
                "language": "tsx",
                "path": "app/a.tsx",
                "sha256": "a" * 64,
                "size": 1,
            },
            {
                "language": "typescript",
                "path": "src/z.ts",
                "sha256": "b" * 64,
                "size": 2,
            },
        ],
        "schema_version": SCHEMA_VERSION,
    }


def test_fixture_manifest_is_golden(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "next-ts-demo"
    del tmp_path

    from vibewiki.discovery.files import discover_files
    from vibewiki.discovery.hashing import hash_file

    files = [
        ManifestFile(item.path, item.language, item.size, hash_file(item.absolute_path))
        for item in discover_files(fixture)
    ]
    actual = build_manifest(files)
    expected = json.loads(
        (
            Path(__file__).parents[1]
            / "expected"
            / "next-ts-demo"
            / "manifest.json"
        ).read_text()
    )

    assert actual == expected

"""Deterministic project profile derived from the local evidence artifact."""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import ANALYZER_VERSION, SCHEMA_VERSION
from .config import LOCAL_IMPORT_MAX_BYTES, LOCAL_IMPORT_MAX_FILES

_FRAMEWORK_LABELS = {
    "next_pages": "Next.js Pages Router",
    "vue_router": "Vue Router",
    "sveltekit": "SvelteKit",
    "generic": "Generic route registrations",
}


def _frameworks(facts: list[dict[str, Any]]) -> list[str]:
    detected: set[str] = set()
    for fact in facts:
        if fact.get("kind") != "route":
            continue
        attributes = fact.get("attributes", {})
        framework = attributes.get("framework")
        if framework:
            detected.add(_FRAMEWORK_LABELS.get(framework, str(framework)))
        semantic_key = str(fact.get("semantic_key", ""))
        if semantic_key.startswith(("route:page:", "route:handler:")):
            detected.add("Next.js App Router")
    return sorted(detected, key=str.casefold)


def build_project_profile(
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    facts: list[dict[str, Any]],
    packages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return stable scope and coverage metadata without reading source content."""
    manifest_files = manifest.get("files", [])
    inventory_files = inventory.get("files", [])
    source_files = [
        item for item in inventory_files if item.get("kind") in {"source", "schema"}
    ]
    language_counts = Counter(
        str(item.get("language", "unknown")) for item in source_files
    )
    language_bytes = Counter(
        {
            language: sum(
                int(item.get("size", 0))
                for item in source_files
                if item.get("language") == language
            )
            for language in language_counts
        }
    )
    roots = sorted(
        {
            (
                str(item.get("path", "")).split("/", 1)[0]
                if "/" in str(item.get("path", ""))
                else "."
            )
            for item in source_files
            if item.get("path")
        }
    )
    package_paths = sorted(
        str(item.get("attributes", {}).get("path", ".")) for item in packages
    )
    package_scope = "monorepo" if len(package_paths) > 1 else "single-package"
    mode = (
        "next-app-router"
        if any(
            str(fact.get("semantic_key", "")).startswith(
                ("route:page:", "route:handler:")
            )
            for fact in facts
        )
        else "generic"
    )
    languages = [
        {
            "language": language,
            "files": language_counts[language],
            "bytes": language_bytes[language],
        }
        for language in sorted(language_counts, key=str.casefold)
    ]
    return {
        "analyzer_version": ANALYZER_VERSION,
        "bytes": sum(int(item.get("size", 0)) for item in source_files),
        "files": len(source_files),
        "frameworks": _frameworks(facts),
        "inventory_files": len(inventory_files),
        "languages": languages,
        "limits": {
            "max_import_bytes": LOCAL_IMPORT_MAX_BYTES,
            "max_import_files": LOCAL_IMPORT_MAX_FILES,
        },
        "package_paths": package_paths,
        "package_scope": package_scope,
        "scan_mode": mode,
        "schema_version": SCHEMA_VERSION,
        "source_roots": roots,
        "status": "verified",
        "workspace_scope": "repository root",
        "scanned_manifest_files": len(manifest_files),
    }


__all__ = ["build_project_profile"]

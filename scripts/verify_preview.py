#!/usr/bin/env python3
"""Deterministic offline checks for the VibeWiki presentation repository."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "VERSION",
    "viewer/index.html",
    "docs/assets/vibewiki-hero.png",
    "docs/assets/vibewiki-product-preview.jpg",
    "docs/product-development-plan.md",
    "docs/product.seed.example.yaml",
)
REQUIRED_HTML_MARKERS = (
    "<title>VibeWiki",
    'id="page-title"',
    'id="graph-search"',
    'id="command-open"',
    'id="graph-canvas"',
    'id="browse-button"',
    'id="browse-status"',
    'id="llm-settings-button"',
    'id="llm-form"',
    'id="ask-mode"',
    'id="export-button"',
    "function exportWiki",
    'renderMarkdown',
    'JS/TS · Python · Go · Rust · config/docs',
    'MAX_IMPORT_BYTES',
    'id="sidebar-product-count"',
    'id="graph-caption-meta"',
    'data-command-key="unknowns"',
    'data-view="Scan history"',
    'function renderHistoryInspector',
    "/api/stale",
    'window.realWorkspace',
    "renderInspector",
)


def fail(message: str) -> None:
    print(f"VERIFY_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    for relative in REQUIRED_FILES:
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size == 0:
            fail(f"missing or empty required file: {relative}")

    hero = ROOT / "docs/assets/vibewiki-hero.png"
    if hero.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        fail("hero asset is not a PNG")

    html = (ROOT / "viewer/index.html").read_text(encoding="utf-8")
    for marker in REQUIRED_HTML_MARKERS:
        if marker not in html:
            fail(f"missing UI marker: {marker}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for reference in ("docs/assets/vibewiki-hero.png", "viewer/index.html"):
        if reference not in readme:
            fail(f"README does not reference {reference}")

    # Catch simple accidental assignments such as API_KEY="..." without
    # treating ordinary documentation words like "password" as secrets.
    tracked_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and path.suffix.lower()
        in {".md", ".html", ".py", ".yml", ".yaml", ".json", ".txt"}
    )
    secret_assignment = re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*[\"'][^\"']{8,}[\"']"
    )
    if secret_assignment.search(tracked_text):
        fail("possible credential assignment found in tracked text")

    print("VIBEWIKI_VERIFY_PASS")
    print(f"root={ROOT}")
    print(f"required_files={len(REQUIRED_FILES)}")
    print(f"hero_bytes={hero.stat().st_size}")
    print(f"html_bytes={(ROOT / 'viewer/index.html').stat().st_size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

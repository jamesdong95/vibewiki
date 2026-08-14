from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibewiki.build import build_repository
from vibewiki.errors import ErrorCode, VibeWikiError
from vibewiki.importer import _multipart_files
from vibewiki.scan import scan_repository


def _multipart(
    files: dict[str, str], boundary: str = "----vibewiki-generic"
) -> tuple[str, bytes]:
    parts = []
    for path, content in files.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{path}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            f"{content}\r\n"
        )
    body = ("".join(parts) + f"--{boundary}--\r\n").encode()
    return f"multipart/form-data; boundary={boundary}", body


def test_generic_javascript_repository_builds_without_app(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/main.js").write_text("export function start() { return true; }\n")
    (tmp_path / "src/App.jsx").write_text(
        "export default function App() { return null; }\n"
    )
    (tmp_path / "src/module.mjs").write_text("export function helper() { return 1; }\n")
    (tmp_path / "src/legacy.cjs").write_text("function legacy() { return 0; }\n")
    (tmp_path / "tests/app.spec.js").write_text("describe('app', () => {});\n")

    result = scan_repository(tmp_path, allow_generic=True)
    built = build_repository(tmp_path)
    facts = json.loads((tmp_path / ".vibewiki/facts.json").read_text())

    assert result["counts"]["scanned_files"] == 5
    assert built["counts"]["facts"] == 4
    assert any(
        item["semantic_key"] == "function:src/main.js:start" for item in facts["facts"]
    )
    assert any(
        item["semantic_key"] == "test:tests/app.spec.js" for item in facts["facts"]
    )


def test_importer_selects_nested_web_package_deterministically() -> None:
    content_type, body = _multipart(
        {
            "repo/packages/ui/src/Button.jsx": "export default function Button() {}\n",
            "repo/packages/web/app/page.js": "export default function Home() {}\n",
            "repo/packages/web/src/main.js": "export function start() {}\n",
        }
    )

    selected = _multipart_files(content_type, body)

    assert [item[1] for item in selected] == ["app/page.js", "src/main.js"]


def test_importer_rejects_unsupported_source_with_actionable_error() -> None:
    content_type, body = _multipart({"repo/README.md": "documentation\n"})

    with pytest.raises(VibeWikiError) as raised:
        _multipart_files(content_type, body)

    assert raised.value.code is ErrorCode.UNSUPPORTED_STACK
    assert "JavaScript" in raised.value.message


def test_importer_reports_file_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    content_type, body = _multipart({"repo/src/main.js": "export function main() {}\n"})
    monkeypatch.setattr("vibewiki.importer.MAX_IMPORT_FILES", 0)

    with pytest.raises(VibeWikiError, match="too many supported files"):
        _multipart_files(content_type, body)


def test_importer_reports_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    content_type, body = _multipart({"repo/src/main.js": "export function main() {}\n"})
    monkeypatch.setattr("vibewiki.importer.MAX_IMPORT_BYTES", 1)

    with pytest.raises(VibeWikiError, match="byte limit"):
        _multipart_files(content_type, body)


def test_importer_reports_multipart_part_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    content_type, body = _multipart({"repo/src/main.js": "export function main() {}\n"})
    monkeypatch.setattr("vibewiki.importer.MAX_MULTIPART_PARTS", 0)

    with pytest.raises(VibeWikiError, match="multipart parts"):
        _multipart_files(content_type, body)

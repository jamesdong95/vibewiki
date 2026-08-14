"""Small deterministic static analyzer for the supported M0 fixture surface."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .config import PRISMA_SCHEMA_RELATIVE_PATH, SUPPORTED_SUFFIXES
from .discovery.manifest import canonical_json

_FUNCTION = re.compile(r"export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\(")
_GENERIC_FUNCTION = re.compile(
    r"(?:^|\n)\s*(?:export\s+(?:default\s+)?)?"
    r"(?:async\s+)?function\s+(\w+)\s*\("
)
_ARROW_FUNCTION = re.compile(
    r"(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*"
    r"(?:async\s+)?(?:\([^)]*\)|\w+)\s*=>"
)
_METHOD = re.compile(
    r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\s*\("
)
_IMPORT = re.compile(r"import\s+(.*?)\s+from\s+[\"']([^\"']+)[\"']")
_REEXPORT = re.compile(r"(?:export|export\s+type)\s+.*?\s+from\s+[\"']([^\"']+)[\"']")
_REQUIRE = re.compile(r"\b(?:require|import)\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
_FETCH = re.compile(r"fetch\(\s*(['\"])(/[^'\"]+)\1")
_DESCRIBE = re.compile(r"describe\(\s*(['\"])(.*?)\1")
_MODEL = re.compile(r"^[ \t]*model\s+(\w+)\s*\{", re.MULTILINE)
_WRITE = re.compile(r"db\.(\w+)\.create\s*\(")
_SCRIPT_SUFFIXES = tuple(SUPPORTED_SUFFIXES)
_PAGE_SUFFIXES = tuple(f"/page{suffix}" for suffix in _SCRIPT_SUFFIXES)
_ROUTE_SUFFIXES = tuple(f"/route{suffix}" for suffix in _SCRIPT_SUFFIXES)


@dataclass(frozen=True, slots=True)
class Source:
    path: str
    text: str
    lines: tuple[str, ...]


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _evidence(
    path: str, kind: str, start: int, end: int | None = None
) -> dict[str, Any]:
    return {
        "kind": kind,
        "line_end": end if end is not None else start,
        "line_start": start,
        "path": path,
        "status": "verified",
    }


def _span(lines: tuple[str, ...], start: int) -> int:
    """Find a conservative closing line for a function/model block."""
    depth = 0
    opened = False
    for index in range(start - 1, len(lines)):
        line = lines[index]
        depth += line.count("{") - line.count("}")
        if "{" in line:
            opened = True
        if opened and depth <= 0:
            return index if line.strip() == "}" else index + 1
    return start


def _fact(
    kind: str, key: str, attributes: dict[str, Any], evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "attributes": attributes,
        "evidence": sorted(
            evidence,
            key=lambda item: (
                item["path"],
                item["line_start"],
                item["line_end"],
                item["kind"],
            ),
        ),
        "kind": kind,
        "semantic_key": key,
        "status": "verified",
    }


def _relation(
    source: str, relation: str, target: str, evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "evidence": sorted(
            evidence,
            key=lambda item: (
                item["path"],
                item["line_start"],
                item["line_end"],
                item["kind"],
            ),
        ),
        "relation": relation,
        "source": source,
        "status": "verified",
        "target": target,
    }


def _route_for(path: str, suffix: str) -> str:
    relative = path[len("app/") :]
    relative = relative[: -len(suffix)]
    if not relative:
        return "/"
    return "/" + "/".join(part for part in relative.split("/") if part)


def _resolve_import(source_path: str, imported: str, sources: set[str]) -> str | None:
    if not imported.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), imported))
    for candidate in (
        base,
        f"{base}.ts",
        f"{base}.tsx",
        f"{base}.js",
        f"{base}.jsx",
        f"{base}.mjs",
        f"{base}.cjs",
        f"{base}/index.ts",
        f"{base}/index.tsx",
        f"{base}/index.js",
        f"{base}/index.jsx",
        f"{base}/index.mjs",
        f"{base}/index.cjs",
    ):
        if candidate in sources:
            return candidate
    return None


def _load_sources(root: Path, manifest: dict[str, Any]) -> dict[str, Source]:
    loaded: dict[str, Source] = {}
    for item in manifest.get("files", []):
        path = item["path"]
        absolute = root / Path(path)
        text = absolute.read_text(encoding="utf-8")
        loaded[path] = Source(path, text, tuple(text.splitlines()))
    schema = root / Path(PRISMA_SCHEMA_RELATIVE_PATH)
    if schema.is_file():
        text = schema.read_text(encoding="utf-8")
        loaded["prisma/schema.prisma"] = Source(
            "prisma/schema.prisma", text, tuple(text.splitlines())
        )
    return loaded


def analyze_repository(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    sources = _load_sources(root, manifest)
    source_paths = set(sources)
    facts: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    page_keys: dict[str, str] = {}
    handler_keys: dict[str, str] = {}
    handler_keys_by_route: dict[str, str] = {}
    function_keys: dict[tuple[str, str], str] = {}
    model_keys: dict[str, str] = {}
    model_evidence: dict[str, dict[str, Any]] = {}
    test_imports: dict[str, list[dict[str, Any]]] = {}

    for path in sorted(sources):
        source = sources[path]
        if path.startswith("app/") and path.endswith(_PAGE_SUFFIXES):
            route = _route_for(
                path, next(suffix for suffix in _PAGE_SUFFIXES if path.endswith(suffix))
            )
            key = f"route:page:{route}"
            page_keys[path] = key
            match = _FUNCTION.search(source.text)
            line = _line(source.text, match.start()) if match else 1
            facts.append(
                _fact(
                    "route",
                    key,
                    {"file": path, "path": route},
                    [_evidence(path, "page_declaration", line)],
                )
            )
        elif path.startswith("app/") and path.endswith(_ROUTE_SUFFIXES):
            suffix = next(suffix for suffix in _ROUTE_SUFFIXES if path.endswith(suffix))
            route = _route_for(path, suffix)
            key = f"route:handler:{route}"
            handler_keys[path] = key
            handler_keys_by_route[route] = key
            method_matches = list(_METHOD.finditer(source.text))
            facts.append(
                _fact(
                    "route",
                    key,
                    {
                        "file": path,
                        "methods": [m.group(1) for m in method_matches],
                        "path": route,
                    },
                    [
                        _evidence(
                            path,
                            "route_handler",
                            _line(source.text, m.start()),
                            _span(source.lines, _line(source.text, m.start())),
                        )
                        for m in method_matches
                    ],
                )
            )
        elif path.endswith(_SCRIPT_SUFFIXES) and not path.startswith("tests/"):
            matches = list(_GENERIC_FUNCTION.finditer(source.text))
            matches.extend(_ARROW_FUNCTION.finditer(source.text))
            for match in matches:
                name = match.group(1)
                key = f"function:{path}:{name}"
                function_keys[(path, name)] = key
                start = _line(source.text, match.start(1))
                facts.append(
                    _fact(
                        "function",
                        key,
                        {"file": path, "name": name},
                        [
                            _evidence(
                                path, "function", start, _span(source.lines, start)
                            )
                        ],
                    )
                )
        elif path.startswith("tests/") and path.endswith(_SCRIPT_SUFFIXES):
            describe = _DESCRIBE.search(source.text)
            name = describe.group(2) if describe else path
            key = f"test:{path}"
            imports = []
            for match in _IMPORT.finditer(source.text):
                resolved = _resolve_import(path, match.group(2), source_paths)
                if resolved:
                    imports.append(
                        _evidence(
                            path, "test_reference", _line(source.text, match.start())
                        )
                    )
            test_imports[path] = imports
            facts.append(
                _fact(
                    "test",
                    key,
                    {"file": path, "name": name},
                    imports or [_evidence(path, "test_reference", 1)],
                )
            )

    schema = sources.get(PRISMA_SCHEMA_RELATIVE_PATH)
    if schema:
        for match in _MODEL.finditer(schema.text):
            name = match.group(1)
            start = _line(schema.text, match.start())
            key = f"data_entity:prisma:{name}"
            model_keys[name.casefold()] = key
            model_evidence[name.casefold()] = _evidence(
                schema.path, "schema_model", start
            )
            facts.append(
                _fact(
                    "data_entity",
                    key,
                    {"file": schema.path, "model": name},
                    [
                        _evidence(
                            schema.path,
                            "schema_model",
                            start,
                            _span(schema.lines, start),
                        )
                    ],
                )
            )

    fact_keys = {item["semantic_key"] for item in facts}
    for path, source in sorted(sources.items()):
        if path in page_keys:
            page_key = page_keys[path]
            for match in _FETCH.finditer(source.text):
                endpoint = match.group(2)
                method_match = re.search(
                    r"method\s*:\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']",
                    source.text[match.start() : match.start() + 400],
                )
                method = method_match.group(1) if method_match else "GET"
                line = _line(source.text, match.start())
                facts.append(
                    _fact(
                        "api_call",
                        f"api_call:{path}:{endpoint}",
                        {"file": path, "method": method, "path": endpoint},
                        [_evidence(path, "api_call", line)],
                    )
                )
                target = handler_keys_by_route.get(endpoint)
                if target:
                    relations.append(
                        _relation(
                            page_key,
                            "calls",
                            target,
                            [_evidence(path, "api_call", line)],
                        )
                    )
        if path in handler_keys:
            handler_key = handler_keys[path]
            post = next(
                (
                    item
                    for item in _METHOD.finditer(source.text)
                    if item.group(1) == "POST"
                ),
                None,
            )
            active_start = post.start() if post else 0
            active_source = source.text[active_start:]
            for match in _IMPORT.finditer(source.text):
                imported_path = _resolve_import(path, match.group(2), source_paths)
                if not imported_path:
                    continue
                for name in re.findall(r"\b\w+\b", match.group(1)):
                    target = function_keys.get((imported_path, name))
                    if target:
                        line = _line(source.text, match.start())
                        call = re.search(rf"\b{name}\s*\(", active_source)
                        if call:
                            call_line = _line(source.text, active_start + call.start())
                            relations.append(
                                _relation(
                                    handler_key,
                                    "imports",
                                    target,
                                    [_evidence(path, "import", line)],
                                )
                            )
                            relations.append(
                                _relation(
                                    handler_key,
                                    "calls",
                                    target,
                                    [_evidence(path, "call", call_line)],
                                )
                            )
        if path.endswith(_SCRIPT_SUFFIXES) and not path.startswith("tests/"):
            for match in _WRITE.finditer(source.text):
                model = match.group(1).casefold()
                target = model_keys.get(model)
                if not target:
                    continue
                line = _line(source.text, match.start())
                function = next(
                    (
                        item
                        for (item_path, _), item in function_keys.items()
                        if item_path == path
                        and item in fact_keys
                        and _line(source.text, _FUNCTION.search(source.text).start())
                        <= line
                    ),
                    None,
                )
                if function:
                    relations.append(
                        _relation(
                            function,
                            "writes",
                            target,
                            [_evidence(path, "write", line), model_evidence[model]],
                        )
                    )

    for test_path, evidence in sorted(test_imports.items()):
        source = sources[test_path]
        for match in _IMPORT.finditer(source.text):
            resolved = _resolve_import(test_path, match.group(2), source_paths)
            if resolved in page_keys:
                relations.append(
                    _relation(
                        page_keys[resolved], "tested_by", f"test:{test_path}", evidence
                    )
                )

    tested_pages = {
        item["source"] for item in relations if item["relation"] == "tested_by"
    }
    unknowns: list[dict[str, Any]] = []
    for path, key in sorted(page_keys.items()):
        route = next(
            item["attributes"]["path"] for item in facts if item["semantic_key"] == key
        )
        if route != "/" and key not in tested_pages:
            unknowns.append(
                {
                    "evidence": [_evidence(path, "coverage_subject", 1)],
                    "reason": (
                        "not_observed: no test import or path reference for this "
                        "page exists in the fixture"
                    ),
                    "status": "unknown",
                    "subject": f"coverage:page:{route}",
                    "type": "test_coverage",
                }
            )

    facts.sort(key=lambda item: (item["kind"], item["semantic_key"]))
    relations.sort(key=lambda item: (item["source"], item["relation"], item["target"]))
    unknowns.sort(key=lambda item: (item["subject"], item["reason"]))
    return {
        "facts": facts,
        "fixture": root.name,
        "relations": relations,
        "schema_version": SCHEMA_VERSION,
        "unknowns": unknowns,
    }


def build_module_graph(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Build a source-to-source dependency graph without changing golden facts.

    The first analyzer milestones intentionally kept ``facts.json`` stable. This
    companion graph gives the viewer a reverse dependency surface for modern
    ESM, CommonJS, and dynamic-import references while preserving that contract.
    """

    sources = _load_sources(root, manifest)
    source_paths = set(sources)
    modules = []
    module_edges = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add_edge(
        source: str, target: str, specifier: str, path: str, offset: int
    ) -> None:
        relation = "imports"
        edge_key = (source, relation, target)
        if edge_key in seen_edges:
            return
        seen_edges.add(edge_key)
        module_edges.append(
            {
                "evidence": [
                    _evidence(path, "module_import", _line(sources[path].text, offset))
                ],
                "relation": relation,
                "source": source,
                "status": "verified",
                "target": target,
                "specifier": specifier,
            }
        )

    for path in sorted(sources):
        source = sources[path]
        source_id = f"module:{path}"
        modules.append(
            {
                "attributes": {"file": path, "path": path},
                "evidence": [_evidence(path, "module_file", 1)],
                "id": source_id,
                "kind": "module",
                "status": "verified",
            }
        )
        references: list[tuple[str, int]] = [
            (match.group(2), match.start()) for match in _IMPORT.finditer(source.text)
        ]
        references.extend(
            (match.group(1), match.start()) for match in _REEXPORT.finditer(source.text)
        )
        references.extend(
            (match.group(1), match.start()) for match in _REQUIRE.finditer(source.text)
        )
        for specifier, offset in references:
            resolved = _resolve_import(path, specifier, source_paths)
            target = f"module:{resolved}" if resolved else f"external:{specifier}"
            add_edge(source_id, target, specifier, path, offset)

    external_ids = sorted(
        {
            edge["target"]
            for edge in module_edges
            if edge["target"].startswith("external:")
        }
    )
    for external_id in external_ids:
        specifier = external_id.removeprefix("external:")
        modules.append(
            {
                "attributes": {"module": specifier},
                "evidence": [],
                "id": external_id,
                "kind": "external_module",
                "status": "inferred",
            }
        )

    modules.sort(key=lambda item: item["id"])
    module_edges.sort(key=lambda item: (item["source"], item["target"]))
    return {"module_edges": module_edges, "modules": modules}


def write_json(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value), encoding="utf-8")


__all__ = ["analyze_repository", "build_module_graph", "write_json"]

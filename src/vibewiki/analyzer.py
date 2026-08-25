"""Deterministic static analyzer with Next and conservative generic adapters."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .config import GENERIC_SUFFIXES, PRISMA_SCHEMA_RELATIVE_PATH, SUPPORTED_SUFFIXES
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
_REQUIRE_BINDING = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*=\s*require\s*\(\s*[\"']([^\"']+)[\"']\s*\)"
)
_CLASS = re.compile(
    r"(?:^|\n)\s*(?:export\s+(?:default\s+)?)?class\s+(\w+)\b"
)
_LANGUAGE_FUNCTION = re.compile(
    r"(?:^|\n)\s*(?:(?:public|private|protected|static|async|override)\s+)*"
    r"(?:def|func|fn|function)\s+(\w+)\s*\("
)
_LANGUAGE_CLASS = re.compile(
    r"(?:^|\n)\s*(?:(?:public|private|protected|abstract|final|static)\s+)*"
    r"(?:class|struct|interface|trait|enum)\s+(\w+)\b"
)
_CALL = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
_FETCH = re.compile(r"fetch\(\s*(['\"])(/[^'\"]+)\1")
_GENERIC_API_CALL = re.compile(
    r"\b((?:fetch|\$fetch|axios\.(?:get|post|put|patch|delete)|"
    r"(?:api|apiClient|client|http|httpClient|request)\."
    r"(?:get|post|put|patch|delete)))\s*"
    r"\(\s*(['\"])([^'\"]+)\2"
)
_GENERIC_ROUTE_CALL = re.compile(
    r"\b(?:app|router|server|fastify|api|http|r|mux)\."
    r"(get|post|put|patch|delete|options|head|route)\s*"
    r"\(\s*(['\"])(/[^'\"]*)\2(?:\s*,\s*([A-Za-z_]\w*))?"
)
_GENERIC_ROUTE_DECORATOR = re.compile(
    r"@(?:app|router|api)\.(get|post|put|patch|delete|route)\s*"
    r"\(\s*(['\"])(/[^'\"]*)\2"
)
_GENERIC_HANDLE_FUNC = re.compile(
    r"\b(?:http|mux|router)\.HandleFunc\s*"
    r"\(\s*(['\"])(/[^'\"]*)\1(?:\s*,\s*([A-Za-z_]\w*))?"
)
_REACT_ROUTE = re.compile(
    r"<Route\b[^>]*\bpath\s*=\s*(['\"])(/[^'\"]*)\1"
)
_REACT_ROUTER_FACTORY = re.compile(
    r"\b(?:createBrowserRouter|createHashRouter|createMemoryRouter)\s*\("
)
_REACT_ROUTER_OBJECT = re.compile(r"\bpath\s*:\s*(['\"])(/[^'\"]*)\1")
_DESCRIBE = re.compile(r"describe\(\s*(['\"])(.*?)\1")
_MODEL = re.compile(r"^[ \t]*model\s+(\w+)\s*\{", re.MULTILINE)
_WRITE = re.compile(r"db\.(\w+)\.create\s*\(")
_SCRIPT_SUFFIXES = tuple(SUPPORTED_SUFFIXES)
_ANALYZABLE_SUFFIXES = tuple({*SUPPORTED_SUFFIXES, *GENERIC_SUFFIXES})
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


def _function_matches(path: str, text: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    if path.endswith(_SCRIPT_SUFFIXES):
        matches.extend(_GENERIC_FUNCTION.finditer(text))
        matches.extend(_ARROW_FUNCTION.finditer(text))
    else:
        matches.extend(_LANGUAGE_FUNCTION.finditer(text))
    return matches


def _generic_route_matches(source: Source) -> list[dict[str, Any]]:
    """Find conservative route registrations outside Next App Router files."""
    matches: list[dict[str, Any]] = []
    if source.path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        for match in _GENERIC_ROUTE_CALL.finditer(source.text):
            method = match.group(1).upper()
            matches.append(
                {
                    "kind": "route_registration",
                    "handler": match.group(4),
                    "method": "ANY" if method == "ROUTE" else method,
                    "offset": match.start(),
                    "path": match.group(3),
                }
            )
        for match in _REACT_ROUTE.finditer(source.text):
            matches.append(
                {
                    "kind": "react_route",
                    "handler": None,
                    "method": "GET",
                    "offset": match.start(),
                    "path": match.group(2),
                }
            )
        if _REACT_ROUTER_FACTORY.search(source.text):
            for match in _REACT_ROUTER_OBJECT.finditer(source.text):
                matches.append(
                    {
                        "kind": "react_router_object",
                        "handler": None,
                        "method": "GET",
                        "offset": match.start(),
                        "path": match.group(2),
                    }
                )
    if source.path.endswith(".py"):
        for match in _GENERIC_ROUTE_DECORATOR.finditer(source.text):
            method = match.group(1).upper()
            matches.append(
                {
                    "kind": "route_decorator",
                    "handler": None,
                    "method": "ANY" if method == "ROUTE" else method,
                    "offset": match.start(),
                    "path": match.group(3),
                }
            )
    if source.path.endswith(".go"):
        for match in _GENERIC_HANDLE_FUNC.finditer(source.text):
            matches.append(
                {
                    "kind": "route_registration",
                    "handler": match.group(3),
                    "method": "ANY",
                    "offset": match.start(),
                    "path": match.group(2),
                }
            )
    for item in matches:
        if item["handler"] or item["kind"] == "react_route":
            continue
        next_function = next(
            (
                match.group(1)
                for match in _function_matches(source.path, source.text)
                if match.start() > item["offset"]
            ),
            None,
        )
        item["handler"] = next_function
    return sorted(matches, key=lambda item: (item["offset"], item["path"]))


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
    api_call_keys: set[str] = set()
    generic_route_links: list[tuple[str, str, str, int]] = []

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
        elif path.endswith(_ANALYZABLE_SUFFIXES) and not path.startswith("tests/"):
            for route in _generic_route_matches(source):
                method = route["method"]
                key = f"route:generic:{path}:{method}:{route['path']}"
                if key in {item["semantic_key"] for item in facts}:
                    continue
                methods = [] if method == "ANY" else [method]
                facts.append(
                    _fact(
                        "route",
                        key,
                        {
                            "file": path,
                            "framework": "generic",
                            "methods": methods,
                            "path": route["path"],
                        },
                        [
                            _evidence(
                                path,
                                route["kind"],
                                _line(source.text, route["offset"]),
                            )
                        ],
                    )
                )
                if route["handler"]:
                    generic_route_links.append(
                        (path, key, route["handler"], route["offset"])
                    )
            for match in _function_matches(path, source.text):
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
        elif path.startswith("tests/") and path.endswith(_ANALYZABLE_SUFFIXES):
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
    generic_route_keys: dict[tuple[str, str], str] = {}
    for item in facts:
        if (
            item["kind"] != "route"
            or item["attributes"].get("framework") != "generic"
        ):
            continue
        methods = item["attributes"].get("methods") or ["ANY"]
        for method in methods:
            generic_route_keys[(item["attributes"]["path"], method)] = item[
                "semantic_key"
            ]
    for source_path, route_key, handler_name, offset in generic_route_links:
        target = function_keys.get((source_path, handler_name))
        if target:
            relations.append(
                _relation(
                    route_key,
                    "calls",
                    target,
                    [
                        _evidence(
                            source_path,
                            "route_handler",
                            _line(sources[source_path].text, offset),
                        )
                    ],
                )
            )
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
                api_key = f"api_call:{path}:{endpoint}"
                api_call_keys.add(api_key)
                facts.append(
                    _fact(
                        "api_call",
                        api_key,
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
        elif (
            path.endswith(_ANALYZABLE_SUFFIXES)
            and not path.startswith("tests/")
        ):
            for match in _GENERIC_API_CALL.finditer(source.text):
                endpoint = match.group(3)
                if not endpoint.startswith(("/", "http://", "https://")):
                    continue
                call_name = match.group(1)
                method = (
                    "GET"
                    if call_name in {"fetch", "$fetch"}
                    else call_name.rsplit(".", 1)[-1].upper()
                )
                method_match = re.search(
                    r"method\s*:\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']",
                    source.text[match.start() : match.start() + 400],
                )
                method = method_match.group(1) if method_match else method
                api_key = f"api_call:{path}:{endpoint}"
                if api_key in api_call_keys:
                    continue
                api_call_keys.add(api_key)
                facts.append(
                    _fact(
                        "api_call",
                        api_key,
                        {"file": path, "method": method, "path": endpoint},
                        [
                            _evidence(
                                path,
                                "api_call",
                                _line(source.text, match.start()),
                            )
                        ],
                    )
                )
                target = generic_route_keys.get((endpoint, method))
                if target is None:
                    target = generic_route_keys.get((endpoint, "ANY"))
                if target:
                    relations.append(
                        _relation(
                            api_key,
                            "calls",
                            target,
                            [
                                _evidence(
                                    path,
                                    "api_call",
                                    _line(source.text, match.start()),
                                )
                            ],
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


def _symbol_definitions(source: Source) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    matches: list[tuple[str, re.Match[str]]] = []
    matches.extend(
        ("function", match) for match in _GENERIC_FUNCTION.finditer(source.text)
    )
    matches.extend(
        ("function", match) for match in _ARROW_FUNCTION.finditer(source.text)
    )
    matches.extend(("class", match) for match in _CLASS.finditer(source.text))
    matches.extend(
        ("function", match) for match in _LANGUAGE_FUNCTION.finditer(source.text)
    )
    matches.extend(
        ("class", match) for match in _LANGUAGE_CLASS.finditer(source.text)
    )
    seen: set[tuple[str, int]] = set()
    for kind, match in matches:
        key = (match.group(1), match.start(1))
        if key in seen:
            continue
        seen.add(key)
        line = _line(source.text, match.start(1))
        line_text = source.lines[line - 1].lstrip() if source.lines else ""
        exported = line_text.startswith("export ")
        definitions.append(
            {
                "export_name": (
                    "default" if exported and "default" in line_text else match.group(1)
                ),
                "exported": exported,
                "kind": kind,
                "line_end": _span(source.lines, line),
                "line_start": line,
                "name": match.group(1),
                "offset": match.start(1),
            }
        )
    definitions.sort(key=lambda item: (item["line_start"], item["name"], item["kind"]))
    return definitions


def _import_bindings(source: Source) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for match in _IMPORT.finditer(source.text):
        clause = match.group(1).strip()
        specifier = match.group(2)
        if clause.startswith("{"):
            for item in clause.strip("{}").split(","):
                parts = re.split(r"\s+as\s+", item.strip())
                if parts and parts[0]:
                    bindings.append(
                        {
                            "imported": parts[0].strip(),
                            "local": (parts[-1] or parts[0]).strip(),
                            "offset": match.start(),
                            "specifier": specifier,
                        }
                    )
            continue
        if clause.startswith("*"):
            namespace = re.search(r"\bas\s+(\w+)", clause)
            if namespace:
                bindings.append(
                    {
                        "imported": "*",
                        "local": namespace.group(1),
                        "offset": match.start(),
                        "specifier": specifier,
                    }
                )
            continue
        default_name = clause.split(",", 1)[0].strip()
        if default_name:
            bindings.append(
                {
                    "imported": "default",
                    "local": default_name,
                    "offset": match.start(),
                    "specifier": specifier,
                }
            )
    for match in _REQUIRE_BINDING.finditer(source.text):
        bindings.append(
            {
                "imported": "default",
                "local": match.group(1),
                "offset": match.start(),
                "specifier": match.group(2),
            }
        )
    return bindings


def _package_graph(
    root: Path, inventory: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    package_files = [
        item["path"]
        for item in (inventory or {}).get("files", [])
        if item["path"].endswith("package.json")
    ]
    packages: list[dict[str, Any]] = []
    package_roots: set[str] = set()
    for package_file in sorted(package_files):
        path = root / Path(package_file)
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        package_root = posixpath.dirname(package_file)
        package_key = package_root or "."
        package_roots.add(package_key)
        packages.append(
            {
                "attributes": {
                    "name": metadata.get("name") or package_key,
                    "path": package_key,
                    "private": bool(metadata.get("private", False)),
                    "version": metadata.get("version"),
                },
                "evidence": [_evidence(package_file, "package_manifest", 1)],
                "id": f"package:{package_key}",
                "kind": "package",
                "status": "verified",
            }
        )
    if not package_roots:
        package_roots.add(".")
        packages.append(
            {
                "attributes": {"name": root.name, "path": ".", "private": False},
                "evidence": [],
                "id": "package:.",
                "kind": "package",
                "status": "inferred",
            }
        )
    packages.sort(key=lambda item: item["id"])
    edges: list[dict[str, Any]] = []
    for package in packages:
        prefix = package["attributes"]["path"]
        for item in (inventory or {}).get("files", []):
            path = item["path"]
            if prefix == "." or path.startswith(prefix + "/"):
                target = (
                    f"module:{path}"
                    if item["kind"] in {"source", "schema"}
                    else f"file:{path}"
                )
                edges.append(
                    _relation(
                        package["id"],
                        "contains",
                        target,
                        package["evidence"] or [_evidence(path, "package_file", 1)],
                    )
                )
    return packages, edges


def _symbol_graph(
    sources: dict[str, Source], module_paths: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    definitions: dict[str, list[dict[str, Any]]] = {}
    symbols: list[dict[str, Any]] = []
    for path in sorted(sources):
        definitions[path] = _symbol_definitions(sources[path])
        for definition in definitions[path]:
            symbol_id = f"symbol:{path}:{definition['name']}"
            symbols.append(
                {
                    "attributes": {
                        "export_name": definition["export_name"],
                        "exported": definition["exported"],
                        "file": path,
                        "name": definition["name"],
                        "symbol_kind": definition["kind"],
                    },
                    "evidence": [
                        _evidence(
                            path,
                            "symbol_definition",
                            definition["line_start"],
                            definition["line_end"],
                        )
                    ],
                    "id": symbol_id,
                    "kind": "symbol",
                    "status": "verified",
                }
            )
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(
        source: str, relation: str, target: str, evidence: list[dict[str, Any]]
    ) -> None:
        key = (source, relation, target)
        if key not in seen:
            seen.add(key)
            edges.append(_relation(source, relation, target, evidence))

    for path, source in sorted(sources.items()):
        for definition in definitions[path]:
            symbol_id = f"symbol:{path}:{definition['name']}"
            add(
                f"module:{path}",
                "defines",
                symbol_id,
                [_evidence(path, "symbol_definition", definition["line_start"])],
            )
        imports: dict[str, tuple[str, str, int]] = {}
        for binding in _import_bindings(source):
            resolved = _resolve_import(path, binding["specifier"], module_paths)
            if resolved:
                imports[binding["local"]] = (
                    resolved,
                    binding["imported"],
                    binding["offset"],
                )
        for definition in definitions[path]:
            start = definition["offset"]
            end = sum(len(line) + 1 for line in source.lines[: definition["line_end"]])
            body = source.text[start:end]
            for call in _CALL.finditer(body):
                if call.start() == 0:
                    continue
                name = call.group(1)
                if name in {"if", "for", "while", "switch", "catch", "function"}:
                    continue
                target_path = path
                target_name = name
                import_info = imports.get(name)
                if import_info:
                    target_path, target_name, _ = import_info
                    if target_name == "default":
                        exported = [
                            item
                            for item in definitions[target_path]
                            if item["export_name"] == "default"
                        ]
                        target_name = exported[0]["name"] if exported else target_name
                target = next(
                    (
                        item
                        for item in definitions.get(target_path, [])
                        if item["name"] == target_name
                        or item["export_name"] == target_name
                    ),
                    None,
                )
                if target is None:
                    continue
                line = _line(source.text, start + call.start())
                add(
                    f"symbol:{path}:{definition['name']}",
                    "calls",
                    f"symbol:{target_path}:{target['name']}",
                    [_evidence(path, "symbol_call", line)],
                )
    symbols.sort(key=lambda item: item["id"])
    edges.sort(key=lambda item: (item["source"], item["relation"], item["target"]))
    return symbols, edges


def build_module_graph(
    root: Path,
    manifest: dict[str, Any],
    inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    packages, package_edges = _package_graph(root, inventory)
    symbols, symbol_edges = _symbol_graph(sources, source_paths)
    modules.sort(key=lambda item: item["id"])
    module_edges.sort(key=lambda item: (item["source"], item["target"]))
    return {
        "module_edges": module_edges,
        "modules": modules,
        "package_edges": package_edges,
        "packages": packages,
        "symbol_edges": symbol_edges,
        "symbols": symbols,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value), encoding="utf-8")


__all__ = ["analyze_repository", "build_module_graph", "write_json"]

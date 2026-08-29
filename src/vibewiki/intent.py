"""Product intent seed loading and deterministic expected-vs-observed comparison."""

from __future__ import annotations

import ast
import json
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ErrorCode, VibeWikiError

SEED_FILENAME = "product.seed.yaml"
_ROOT_FIELDS = {"product", "audience", "goals", "flows"}
_PRODUCT_FIELDS = {"name", "audience"}
_FLOW_FIELDS = {"id", "name", "expected", "expected_outcomes", "steps"}
_STEP_FIELDS = {"id", "name", "description", "expected"}
_EXPECTED_ALIASES = {
    "api": "api",
    "entity": "entity",
    "file": "file",
    "function": "function",
    "module": "module",
    "package": "package",
    "route": "route",
    "symbol": "symbol",
    "test": "test",
}
_NODE_GROUPS = ("facts", "modules", "packages", "symbols")
TEMPLATES_SCHEMA_VERSION = 1
MAX_TEMPLATES = 32
MAX_TEMPLATE_BYTES = 128 * 1024
MAX_TEMPLATE_STEPS = 24
MAX_TEMPLATE_EXPECTATIONS = 16
MAX_TEMPLATE_NAME_CHARS = 120
MAX_TEMPLATE_DESCRIPTION_CHARS = 500


@dataclass(frozen=True)
class _YamlLine:
    indent: int
    text: str


def _invalid(message: str) -> VibeWikiError:
    return VibeWikiError(ErrorCode.INVALID_OUTPUT, f"product seed: {message}")


def _strip_comment(value: str) -> str:
    quoted = False
    quote = ""
    for index, character in enumerate(value):
        if character in {"'", '"'}:
            if not quoted:
                quoted, quote = True, character
            elif quote == character:
                quoted = False
        elif character == "#" and not quoted and (
            index == 0 or value[index - 1].isspace()
        ):
            return value[:index].rstrip()
    return value.rstrip()


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value in {"[]", "{}"}:
        return json.loads(value)
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lower() in {"null", "~"}:
        return None
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError) as error:
            raise _invalid("quoted value is invalid") from error
    if value[:1] in {"[", "{"}:
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _key_value(value: str) -> tuple[str, str] | None:
    if ":" not in value:
        return None
    key, remainder = value.split(":", 1)
    key = key.strip()
    if not key or any(character.isspace() for character in key):
        return None
    return key, remainder.strip()


def _lines(text: str) -> list[_YamlLine]:
    result = []
    for raw in text.splitlines():
        if "\t" in raw:
            raise _invalid("tabs are not supported; use spaces for indentation")
        content = _strip_comment(raw).strip()
        if not content:
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        result.append(_YamlLine(indent, content))
    return result


def _block(lines: list[_YamlLine], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines) or lines[index].indent < indent:
        return {}, index
    if lines[index].indent != indent:
        raise _invalid("indentation must increase consistently")
    if lines[index].text.startswith("-"):
        return _list(lines, index, indent)
    return _mapping(lines, index, indent)


def _mapping(
    lines: list[_YamlLine], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line = lines[index]
        if line.indent != indent or line.text.startswith("-"):
            break
        pair = _key_value(line.text)
        if pair is None:
            raise _invalid(f"expected key:value, got {line.text!r}")
        key, raw = pair
        index += 1
        if raw:
            result[key] = _scalar(raw)
            continue
        if index < len(lines) and lines[index].indent > indent:
            result[key], index = _block(lines, index, lines[index].indent)
        else:
            result[key] = None
    return result, index


def _list(
    lines: list[_YamlLine], index: int, indent: int
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line = lines[index]
        if line.indent != indent or not line.text.startswith("-"):
            break
        remainder = line.text[1:].strip()
        index += 1
        if not remainder:
            if index >= len(lines) or lines[index].indent <= indent:
                result.append(None)
                continue
            item, index = _block(lines, index, lines[index].indent)
            result.append(item)
            continue
        pair = _key_value(remainder)
        if pair is None:
            result.append(_scalar(remainder))
            continue
        key, raw = pair
        item: dict[str, Any] = {}
        if raw:
            item[key] = _scalar(raw)
        elif index < len(lines) and lines[index].indent > indent:
            item[key], index = _block(lines, index, lines[index].indent)
        else:
            item[key] = None
        if index < len(lines) and lines[index].indent > indent:
            extra, index = _block(lines, index, lines[index].indent)
            if not isinstance(extra, dict):
                raise _invalid("a list item can only merge mapping fields")
            item.update(extra)
        result.append(item)
    return result, index


def _parse_seed(text: str) -> dict[str, Any]:
    lines = _lines(text)
    if not lines:
        raise _invalid("file is empty")
    value, index = _block(lines, 0, lines[0].indent)
    if index != len(lines) or not isinstance(value, dict):
        raise _invalid("root must be a mapping")
    return value


def _validate_mapping_fields(
    value: dict[str, Any], allowed: set[str], label: str
) -> None:
    unsupported = sorted(set(value) - allowed)
    if unsupported:
        raise _invalid(f"unsupported {label} field(s): {', '.join(unsupported)}")


def _normalise_expected(value: Any, flow_id: str, index: int) -> dict[str, str]:
    if not isinstance(value, dict):
        raise _invalid(f"flow {flow_id} expected[{index}] must be a mapping")
    if "kind" in value or "value" in value:
        _validate_mapping_fields(value, {"kind", "value", "label"}, "expected")
        kind, expected_value = value.get("kind"), value.get("value")
    else:
        aliases = [key for key in value if key in _EXPECTED_ALIASES]
        if len(aliases) != 1 or set(value) != set(aliases):
            raise _invalid(
                f"flow {flow_id} expected[{index}] must use kind/value or one "
                "supported expectation alias"
            )
        kind, expected_value = aliases[0], value[aliases[0]]
    if not isinstance(kind, str) or kind not in _EXPECTED_ALIASES:
        raise _invalid(f"flow {flow_id} expected[{index}] has an unsupported kind")
    if not isinstance(expected_value, str) or not expected_value.strip():
        raise _invalid(f"flow {flow_id} expected[{index}] value is required")
    return {
        "kind": _EXPECTED_ALIASES[kind],
        "value": expected_value.strip(),
        "label": str(value.get("label", expected_value)).strip(),
    }


def _normalise_flow(flow: Any, index: int = 0) -> dict[str, Any]:
    if not isinstance(flow, dict):
        raise _invalid(f"flow[{index}] must be a mapping")
    _validate_mapping_fields(flow, _FLOW_FIELDS, "flow")
    flow_id = flow.get("id")
    if not isinstance(flow_id, str) or not flow_id.strip():
        raise _invalid(f"flow[{index}] id is required")
    flow_id = flow_id.strip()
    result: dict[str, Any] = {
        "id": flow_id,
        "name": str(flow.get("name", flow_id)).strip() or flow_id,
    }
    if "steps" in flow:
        steps = flow.get("steps")
        if not isinstance(steps, list) or not steps:
            raise _invalid(f"flow {flow_id} must define a non-empty steps list")
        if len(steps) > MAX_TEMPLATE_STEPS:
            raise _invalid(f"flow {flow_id} has too many steps")
        normalised_steps: list[dict[str, Any]] = []
        flattened: list[dict[str, str]] = []
        step_ids: set[str] = set()
        for step_index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise _invalid(f"flow {flow_id} step[{step_index}] must be a mapping")
            _validate_mapping_fields(step, _STEP_FIELDS, "step")
            step_id = step.get("id")
            if not isinstance(step_id, str) or not step_id.strip():
                raise _invalid(f"flow {flow_id} step[{step_index}] id is required")
            step_id = step_id.strip()
            if step_id in step_ids:
                raise _invalid(f"flow {flow_id} has duplicate step id {step_id}")
            step_ids.add(step_id)
            expected_values = step.get("expected")
            if not isinstance(expected_values, list) or not expected_values:
                raise _invalid(f"flow {flow_id} step {step_id} needs expectations")
            if len(expected_values) > MAX_TEMPLATE_EXPECTATIONS:
                raise _invalid(
                    f"flow {flow_id} step {step_id} has too many expectations"
                )
            normalised_expected = []
            for expected_index, expected in enumerate(expected_values):
                item = _normalise_expected(expected, flow_id, expected_index)
                item.update(
                    {
                        "step_id": step_id,
                        "step_name": str(step.get("name", step_id)).strip() or step_id,
                        "step_index": step_index,
                    }
                )
                normalised_expected.append(item)
                flattened.append(item)
            normalised_steps.append(
                {
                    "id": step_id,
                    "name": str(step.get("name", step_id)).strip() or step_id,
                    "description": str(step.get("description", "")).strip()[:
                        MAX_TEMPLATE_DESCRIPTION_CHARS
                    ],
                    "expected": normalised_expected,
                }
            )
        if len(flattened) > MAX_TEMPLATE_STEPS * MAX_TEMPLATE_EXPECTATIONS:
            raise _invalid(f"flow {flow_id} has too many expectations")
        result["steps"] = normalised_steps
        result["expected"] = flattened
        return result
    expected = flow.get("expected", flow.get("expected_outcomes"))
    if not isinstance(expected, list) or not expected:
        raise _invalid(f"flow {flow_id} must define a non-empty expected list")
    result["expected"] = [
        _normalise_expected(item, flow_id, item_index)
        for item_index, item in enumerate(expected)
    ]
    return result


def _normalise_seed(parsed: dict[str, Any]) -> dict[str, Any]:
    _validate_mapping_fields(parsed, _ROOT_FIELDS, "root")
    product = parsed.get("product", {})
    if isinstance(product, str):
        product = {"name": product}
    if not isinstance(product, dict):
        raise _invalid("product must be a mapping or string")
    _validate_mapping_fields(product, _PRODUCT_FIELDS, "product")
    flow_values = parsed.get("flows", parsed.get("goals", []))
    if not isinstance(flow_values, list):
        raise _invalid("flows or goals must be a list")
    flows = [_normalise_flow(flow, index) for index, flow in enumerate(flow_values)]
    if not flows:
        raise _invalid("define at least one flow or goal")
    return {
        "path": SEED_FILENAME,
        "product": product,
        "audience": parsed.get("audience", product.get("audience")),
        "flows": flows,
    }


def normalise_product_seed(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a product seed supplied by a local API client."""

    if not isinstance(value, dict):
        raise _invalid("root must be a mapping")
    return _normalise_seed(value)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(str(value), ensure_ascii=False)


def _seed_yaml(seed: dict[str, Any]) -> str:
    """Serialize only the validated, canonical seed shape to safe YAML."""

    lines = ["product:"]
    product = seed.get("product", {})
    if product.get("name") is not None:
        lines.append(f"  name: {_yaml_scalar(product['name'])}")
    if product.get("audience") is not None:
        lines.append(f"  audience: {_yaml_scalar(product['audience'])}")
    if seed.get("audience") is not None:
        lines.append(f"audience: {_yaml_scalar(seed['audience'])}")
    lines.append("flows:")
    for flow in seed["flows"]:
        lines.extend(
            [
                f"  - id: {_yaml_scalar(flow['id'])}",
                f"    name: {_yaml_scalar(flow['name'])}",
            ]
        )
        if flow.get("steps"):
            lines.append("    steps:")
            for step in flow["steps"]:
                lines.extend(
                    [
                        f"      - id: {_yaml_scalar(step['id'])}",
                        f"        name: {_yaml_scalar(step['name'])}",
                    ]
                )
                if step.get("description"):
                    lines.append(
                        f"        description: {_yaml_scalar(step['description'])}"
                    )
                lines.append("        expected:")
                for expected in step["expected"]:
                    lines.extend(
                        [
                            f"          - kind: {_yaml_scalar(expected['kind'])}",
                            f"            value: {_yaml_scalar(expected['value'])}",
                            f"            label: {_yaml_scalar(expected['label'])}",
                        ]
                    )
        else:
            lines.append("    expected:")
            for expected in flow["expected"]:
                lines.extend(
                    [
                        f"      - kind: {_yaml_scalar(expected['kind'])}",
                        f"        value: {_yaml_scalar(expected['value'])}",
                        f"        label: {_yaml_scalar(expected['label'])}",
                    ]
                )
    return "\n".join(lines) + "\n"


def write_product_seed(root: Path, value: dict[str, Any]) -> dict[str, Any]:
    """Validate and atomically persist product intent inside the workspace."""

    seed = normalise_product_seed(value)
    destination = Path(root) / SEED_FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".product.seed.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_seed_yaml(seed))
        os.replace(temporary, destination)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise _invalid("file could not be written") from error
    return seed


def _template_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _template_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("it_")
        and 8 <= len(value) <= 64
        and all(character.isalnum() or character in "_-" for character in value)
    )


def _template_flow_input(value: Any) -> Any:
    """Strip comparator-only step metadata before validating a stored template."""

    if not isinstance(value, dict):
        return value
    steps = value.get("steps")
    if not isinstance(steps, list):
        return value
    clean_steps = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        expected_values = step.get("expected", [])
        if not isinstance(expected_values, list):
            expected_values = []
        clean_steps.append(
            {
                **step,
                "expected": [
                    {
                        key: item[key]
                        for key in ("kind", "value", "label")
                        if key in item
                    }
                    for item in expected_values
                    if isinstance(item, dict)
                ],
            }
        )
    return {**value, "steps": clean_steps}


class IntentTemplateStore:
    """Bounded, local-only reusable intent templates."""

    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir).expanduser().absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(stat.S_IRWXU)
        except OSError:
            pass
        self.path = self.root / "intent-templates.json"

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": TEMPLATES_SCHEMA_VERSION, "templates": []}

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty()
        try:
            if self.path.stat().st_size > MAX_TEMPLATE_BYTES:
                return self._empty()
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return self._empty()
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != TEMPLATES_SCHEMA_VERSION
            or not isinstance(value.get("templates"), list)
        ):
            return self._empty()
        valid = []
        for template in value["templates"][:MAX_TEMPLATES]:
            if not isinstance(template, dict) or not _template_id(template.get("id")):
                continue
            try:
                flow = _normalise_flow(_template_flow_input(template.get("flow")), 0)
            except VibeWikiError:
                continue
            if "steps" not in flow:
                flow = {
                    "id": flow["id"],
                    "name": flow["name"],
                    "steps": [
                        {
                            "id": "step-1",
                            "name": flow["name"],
                            "description": "",
                            "expected": flow["expected"],
                        }
                    ],
                    "expected": [
                        {
                            **item,
                            "step_id": "step-1",
                            "step_name": flow["name"],
                            "step_index": 0,
                        }
                        for item in flow["expected"]
                    ],
                }
            valid.append(
                {
                    "id": template["id"],
                    "name": str(template.get("name", flow["name"]))[
                        :MAX_TEMPLATE_NAME_CHARS
                    ],
                    "created_at": str(template.get("created_at", "")),
                    "updated_at": str(template.get("updated_at", "")),
                    "flow": flow,
                }
            )
        return {**self._empty(), "templates": valid}

    def _write(self, state: dict[str, Any]) -> None:
        encoded = json.dumps(
            state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > MAX_TEMPLATE_BYTES:
            raise _invalid("template library exceeds the local safety limit")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".intent-templates.", suffix=".tmp", dir=self.root
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.write(b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        except OSError as error:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise _invalid("template library could not be written") from error
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def list(self) -> list[dict[str, Any]]:
        return self._load()["templates"]

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise _invalid("template payload must be a mapping")
        allowed = {"name", "flow"}
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            raise _invalid(f"unsupported template field(s): {', '.join(unsupported)}")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _invalid("template name is required")
        name = " ".join(name.strip().split())[:MAX_TEMPLATE_NAME_CHARS]
        flow = _normalise_flow(payload.get("flow"), 0)
        if "steps" not in flow:
            raise _invalid("template flow must define steps")
        now = _template_now()
        template = {
            "id": f"it_{secrets.token_urlsafe(10)}",
            "name": name,
            "created_at": now,
            "updated_at": now,
            "flow": flow,
        }
        state = self._load()
        state["templates"] = [template, *state["templates"]][:MAX_TEMPLATES]
        self._write(state)
        return template

    def delete(self, template_id: str) -> None:
        if not _template_id(template_id):
            raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "template was not found")
        state = self._load()
        remaining = [item for item in state["templates"] if item["id"] != template_id]
        if len(remaining) == len(state["templates"]):
            raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "template was not found")
        state["templates"] = remaining
        self._write(state)


def load_product_seed(root: Path) -> dict[str, Any] | None:
    path = root / SEED_FILENAME
    if not path.is_file():
        return None
    try:
        parsed = _parse_seed(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise _invalid("file could not be read") from error
    return _normalise_seed(parsed)


def _nodes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = []
    for group in _NODE_GROUPS:
        for node in artifact.get(group, []):
            if "id" not in node and "semantic_key" in node:
                node = {**node, "id": node["semantic_key"]}
            nodes.append(node)
    # Inventory files are first-class evidence in the viewer even when the
    # analyzer did not emit a module fact for them (for example a test or a
    # generic source file). Keep intent matching aligned with that surface.
    for item in artifact.get("inventory", {}).get("files", []):
        nodes.append(
            {
                "attributes": item,
                "evidence": [
                    {
                        "kind": "file_inventory",
                        "line_end": 1,
                        "line_start": 1,
                        "path": item["path"],
                        "status": "verified",
                    }
                ],
                "id": f"file:{item['path']}",
                "kind": "file",
                "status": "verified",
            }
        )
    return nodes


def _matches(node: dict[str, Any], expected: dict[str, str]) -> bool:
    kind, value = expected["kind"], expected["value"]
    attributes = node.get("attributes", {})
    node_kind = node.get("kind")
    if kind == "route":
        return node_kind == "route" and attributes.get("path") == value
    if kind == "api":
        return (
            node_kind == "api_call" and attributes.get("path") == value
        ) or (node_kind == "route" and attributes.get("path") == value)
    if kind in {"file", "module"}:
        return attributes.get("file") == value or attributes.get("path") == value
    if kind == "test":
        return node_kind == "test" and attributes.get("file") == value
    if kind in {"function", "symbol"}:
        return node_kind in {kind, "function", "symbol"} and attributes.get(
            "name"
        ) == value
    if kind == "entity":
        return node_kind == "data_entity" and attributes.get("model") == value
    if kind == "package":
        return node_kind == "package" and (
            attributes.get("name") == value or attributes.get("path") == value
        )
    return False


def _intent_evidence(item: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "product_intent",
            "line_end": 1,
            "line_start": 1,
            "path": SEED_FILENAME,
            "status": "verified",
        }
    ]


def _intent_subject(flow_id: str, expected: dict[str, str]) -> str:
    step_id = expected.get("step_id")
    if step_id:
        return f"intent:{flow_id}:step:{step_id}:{expected['kind']}:{expected['value']}"
    return f"intent:{flow_id}:{expected['kind']}:{expected['value']}"


def compare_product_intent(
    root: Path, artifact: dict[str, Any]
) -> dict[str, Any]:
    seed = load_product_seed(root)
    if seed is None:
        return {
            "configured": False,
            "flows": [],
            "gaps": [],
            "counts": {"flows": 0, "gaps": 0, "observed": 0, "partial": 0},
        }
    nodes = _nodes(artifact)
    flows = []
    gaps = []
    for flow in seed["flows"]:
        expected_results = []
        for expected in flow["expected"]:
            matches = [node for node in nodes if _matches(node, expected)]
            evidence = [
                evidence_item
                for node in matches
                for evidence_item in node.get("evidence", [])
            ]
            result = {
                **expected,
                "observed": [node["id"] for node in matches],
                "evidence": evidence or _intent_evidence(expected),
                "status": "observed" if matches else "not_observed",
            }
            expected_results.append(result)
            if not matches:
                gaps.append(
                    {
                        "evidence": _intent_evidence(expected),
                        "expected": expected,
                        "flow_id": flow["id"],
                        "reason": (
                            f"expected {expected['kind']} {expected['value']} "
                            "was not observed in the current build"
                        ),
                        "status": "unknown",
                        "subject": _intent_subject(flow["id"], expected),
                        "type": "intent_gap",
                    }
                )
        observed = sum(item["status"] == "observed" for item in expected_results)
        status = (
            "observed"
            if observed == len(expected_results)
            else "partially_observed"
            if observed
            else "not_observed"
        )
        flow_result = {**flow, "expected": expected_results, "status": status}
        if flow.get("steps"):
            step_results = []
            for step in flow["steps"]:
                step_expected = [
                    item
                    for item in expected_results
                    if item.get("step_id") == step["id"]
                ]
                step_observed = sum(
                    item["status"] == "observed" for item in step_expected
                )
                step_results.append(
                    {
                        **step,
                        "expected": step_expected,
                        "status": (
                            "observed"
                            if step_observed == len(step_expected)
                            else "partially_observed"
                            if step_observed
                            else "not_observed"
                        ),
                    }
                )
            flow_result["steps"] = step_results
        flows.append(flow_result)
    flows.sort(key=lambda item: item["id"])
    gaps.sort(key=lambda item: item["subject"])
    observed_count = sum(item["status"] == "observed" for item in flows)
    partial_count = sum(item["status"] == "partially_observed" for item in flows)
    return {
        "configured": True,
        "path": seed["path"],
        "product": seed["product"],
        "audience": seed["audience"],
        "flows": flows,
        "gaps": gaps,
        "counts": {
            "flows": len(flows),
            "gaps": len(gaps),
            "observed": observed_count,
            "partial": partial_count,
        },
    }


__all__ = [
    "IntentTemplateStore",
    "MAX_TEMPLATE_BYTES",
    "MAX_TEMPLATE_EXPECTATIONS",
    "MAX_TEMPLATE_STEPS",
    "MAX_TEMPLATES",
    "SEED_FILENAME",
    "compare_product_intent",
    "load_product_seed",
    "normalise_product_seed",
    "write_product_seed",
]

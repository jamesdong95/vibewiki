"""Join safe runtime observations to deterministic route/API graph facts."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def _runtime_path(value: str) -> str:
    """Return the path used to join a runtime URL to a static graph node."""
    parsed = urlsplit(value)
    return parsed.path or "/"


def _runtime_node_matches(node: dict[str, Any], path: str, method: str) -> bool:
    """Match runtime records only to route/API facts with the same contract."""
    if node.get("kind") not in {"route", "api_call"}:
        return False
    attributes = node.get("attributes", {})
    if attributes.get("path") != path:
        return False
    methods = attributes.get("methods")
    if methods and method and method.upper() not in {
        str(item).upper() for item in methods
    }:
        return False
    declared_method = attributes.get("method")
    return (
        not declared_method
        or not method
        or declared_method.upper() == method.upper()
    )


def attach_runtime_links(
    runtime: dict[str, Any], nodes: list[dict[str, Any]]
) -> dict[str, Any]:
    """Join observed routes/network/errors to deterministic static graph nodes."""
    linked = {
        **runtime,
        "routes": [dict(item) for item in runtime.get("routes", [])],
        "network": [dict(item) for item in runtime.get("network", [])],
        "console": [dict(item) for item in runtime.get("console", [])],
    }
    node_runtime = {
        node["id"]: {"console": [], "network": [], "routes": []} for node in nodes
    }

    def join(
        record: dict[str, Any], category: str, path: str, method: str = ""
    ) -> None:
        matches = [
            node["id"]
            for node in nodes
            if _runtime_node_matches(node, path, method)
        ]
        record["graph_nodes"] = matches
        for node_id in matches:
            node_runtime[node_id][category].append(record)

    for record in linked["routes"]:
        join(
            record,
            "routes",
            record.get("path") or _runtime_path(record.get("url", "")),
        )
    for record in linked["network"]:
        join(
            record,
            "network",
            _runtime_path(record.get("url", "")),
            record.get("method", ""),
        )
    for record in linked["console"]:
        url = record.get("url", "")
        if url:
            join(record, "console", _runtime_path(url))
        else:
            record["graph_nodes"] = []
    for node in nodes:
        evidence = node_runtime[node["id"]]
        if any(evidence.values()):
            node["runtime"] = evidence
    linked["linked_nodes"] = sum(
        1 for value in node_runtime.values() if any(value.values())
    )
    return linked


__all__ = ["attach_runtime_links"]

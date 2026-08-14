"""Constants for the deterministic, local-only scanner."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

from . import ANALYZER_VERSION, SCHEMA_VERSION

MANIFEST_DIRECTORY: Final[str] = ".vibewiki"
MANIFEST_RELATIVE_PATH: Final[str] = f"{MANIFEST_DIRECTORY}/manifest.json"
PRISMA_SCHEMA_RELATIVE_PATH: Final[str] = "prisma/schema.prisma"

# The deterministic analyzer accepts the common JavaScript/TypeScript module
# surface. The route and function patterns stay intentionally conservative.
SUPPORTED_SUFFIXES: Final[Mapping[str, str]] = MappingProxyType(
    {
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".mjs": "javascript-module",
        ".cjs": "javascript-commonjs",
    }
)

__all__ = [
    "ANALYZER_VERSION",
    "MANIFEST_DIRECTORY",
    "MANIFEST_RELATIVE_PATH",
    "PRISMA_SCHEMA_RELATIVE_PATH",
    "SCHEMA_VERSION",
    "SUPPORTED_SUFFIXES",
]

"""Constants for the deterministic, local-only M2 scanner."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

from . import ANALYZER_VERSION, SCHEMA_VERSION

MANIFEST_DIRECTORY: Final[str] = ".vibewiki"
MANIFEST_RELATIVE_PATH: Final[str] = f"{MANIFEST_DIRECTORY}/manifest.json"

# M2 records the TypeScript surface only.  TSX gets a separate label because
# it is a distinct cache input even though both forms are handled by a future
# TypeScript analyzer.
SUPPORTED_SUFFIXES: Final[Mapping[str, str]] = MappingProxyType(
    {".ts": "typescript", ".tsx": "tsx"}
)

__all__ = [
    "ANALYZER_VERSION",
    "MANIFEST_DIRECTORY",
    "MANIFEST_RELATIVE_PATH",
    "SCHEMA_VERSION",
    "SUPPORTED_SUFFIXES",
]

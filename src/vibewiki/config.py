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

# Additional text/source formats are enabled only for generic scans and local
# Browse imports. The strict CLI contract still uses SUPPORTED_SUFFIXES so the
# original Next.js golden manifest remains byte-compatible.
GENERIC_SUFFIXES: Final[Mapping[str, str]] = MappingProxyType(
    {
        ".py": "python",
        ".pyi": "python-stub",
        ".go": "go",
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin-script",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".cs": "csharp",
        ".c": "c",
        ".h": "c-header",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".cxx": "cpp",
        ".hpp": "cpp-header",
        ".swift": "swift",
        ".dart": "dart",
        ".lua": "lua",
        ".ex": "elixir",
        ".exs": "elixir-script",
        ".scala": "scala",
        ".hs": "haskell",
        ".sh": "shell",
        ".bash": "shell",
        ".zsh": "shell",
        ".fish": "shell",
        ".sql": "sql",
        ".css": "css",
        ".scss": "scss",
        ".less": "less",
        ".html": "html",
        ".vue": "vue",
        ".svelte": "svelte",
        ".md": "markdown",
        ".mdx": "mdx",
        ".json": "json",
        ".jsonc": "jsonc",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".xml": "xml",
        ".ini": "ini",
        ".conf": "config",
    }
)

__all__ = [
    "ANALYZER_VERSION",
    "GENERIC_SUFFIXES",
    "MANIFEST_DIRECTORY",
    "MANIFEST_RELATIVE_PATH",
    "PRISMA_SCHEMA_RELATIVE_PATH",
    "SCHEMA_VERSION",
    "SUPPORTED_SUFFIXES",
]

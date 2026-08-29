"""Safe file discovery and deterministic manifest helpers."""

from .files import DiscoveredFile, discover_files
from .hashing import hash_file, sha256_bytes
from .ignore import is_ignored_path, is_sensitive_path
from .manifest import (
    ManifestFile,
    build_manifest,
    canonical_json,
    manifest_cache_key,
    write_manifest,
)

__all__ = [
    "DiscoveredFile",
    "ManifestFile",
    "build_manifest",
    "canonical_json",
    "discover_files",
    "hash_file",
    "is_ignored_path",
    "is_sensitive_path",
    "manifest_cache_key",
    "sha256_bytes",
    "write_manifest",
]

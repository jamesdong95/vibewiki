"""Local browser-folder import for the loopback viewer.

The browser sends selected file bytes to the local VibeWiki process. This is
not an external upload: files are filtered, copied into a temporary workspace,
scanned, and removed when the server exits.
"""

from __future__ import annotations

import io
import json
import posixpath
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

from .analyzer import (
    PackageAlias,
    Source,
    _module_references,
    _resolve_import,
)
from .build import build_repository
from .config import (
    GENERIC_SUFFIXES,
    LOCAL_IMPORT_MAX_BYTES,
    LOCAL_IMPORT_MAX_FILES,
    PRISMA_SCHEMA_RELATIVE_PATH,
    SUPPORTED_SUFFIXES,
)
from .discovery.files import DiscoveredFile, discover_files
from .discovery.ignore import should_skip_path
from .errors import ErrorCode, VibeWikiError
from .scan import scan_repository

MAX_IMPORT_FILES = LOCAL_IMPORT_MAX_FILES
MAX_IMPORT_BYTES = LOCAL_IMPORT_MAX_BYTES
MAX_MULTIPART_PARTS = 50_000
MAX_GITHUB_ARCHIVE_MEMBERS = 50_000
MAX_GITHUB_ARCHIVE_BYTES = MAX_IMPORT_BYTES + 8 * 1024 * 1024
_SUPPORTED_IMPORT_SUFFIXES = frozenset((*SUPPORTED_SUFFIXES, *GENERIC_SUFFIXES))
_KNOWN_SOURCE_ROOTS = frozenset(
    {"app", "lib", "pages", "prisma", "routes", "server", "src", "tests"}
)
_MONOREPO_ROOTS = frozenset(
    {"apps", "libs", "modules", "packages", "services", "workspaces"}
)
_CODE_SUFFIXES = frozenset(
    {
        *SUPPORTED_SUFFIXES,
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".dart",
        ".ex",
        ".exs",
        ".fs",
        ".fsx",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".php",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:($|/)")
_GITHUB_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_GITHUB_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ImportedWorkspace:
    root: Path
    build_summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _ImportFile:
    """A bounded file whose identity is relative to the repository root."""

    source_top: str
    repo_relative_path: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class _CandidateCollection:
    files: tuple[_ImportFile, ...]
    skipped_files: int


@dataclass(frozen=True, slots=True)
class _ImportSelection:
    files: tuple[_ImportFile, ...]
    skipped_files: int
    retained_bytes: int
    primary_package: str
    closure_packages: tuple[str, ...]
    unresolved_workspace_imports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PackageInfo:
    root: str
    manifest_path: str | None
    name: str | None
    metadata: dict[str, Any]
    entries: tuple[str, ...] = ()
    exports: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _json_bytes(payload: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _package_targets(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(target for item in value for target in _package_targets(item))
    if not isinstance(value, dict):
        return ()
    preferred = ("types", "typings", "import", "require", "default")
    keys = [key for key in preferred if key in value]
    keys.extend(sorted(key for key in value if key not in keys))
    return tuple(target for key in keys for target in _package_targets(value[key]))


def _package_target(root: str, target: str) -> str | None:
    if not target or target.startswith("/"):
        return None
    normalized = posixpath.normpath(posixpath.join(root, target))
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def _package_index(
    files: dict[str, _ImportFile],
) -> tuple[tuple[_PackageInfo, ...], dict[str, _PackageInfo]]:
    packages: list[_PackageInfo] = []
    for manifest_path in sorted(
        path
        for path in files
        if path.endswith("/package.json") or path == "package.json"
    ):
        metadata = _json_bytes(files[manifest_path].payload)
        if not metadata:
            continue
        package_root = posixpath.dirname(manifest_path) or "."
        name = metadata.get("name")
        if not isinstance(name, str) or not name.strip():
            name = None
        entries: list[str] = []
        for field in ("types", "typings", "module", "main"):
            value = metadata.get(field)
            if isinstance(value, str) and (
                target := _package_target(package_root, value)
            ):
                entries.append(target)
        raw_exports = metadata.get("exports")
        export_map: dict[str, tuple[str, ...]] = {}
        subpath_exports = isinstance(raw_exports, dict) and any(
            str(key).startswith(".") for key in raw_exports
        )
        if subpath_exports:
            for key, value in raw_exports.items():
                if not isinstance(key, str) or not key.startswith("."):
                    continue
                targets = tuple(
                    target
                    for raw_target in _package_targets(value)
                    if (target := _package_target(package_root, raw_target))
                )
                if targets:
                    export_map[key] = targets
        if raw_exports is not None:
            raw_entry = raw_exports.get(".") if subpath_exports else raw_exports
            entries.extend(
                target
                for raw_target in _package_targets(raw_entry)
                if (target := _package_target(package_root, raw_target))
            )
        entries.extend(
            target
            for fallback in ("src/index", "index")
            if (target := _package_target(package_root, fallback))
        )
        packages.append(
            _PackageInfo(
                root=package_root,
                manifest_path=manifest_path,
                name=name.strip() if name else None,
                metadata=metadata,
                entries=tuple(dict.fromkeys(entries)),
                exports=tuple(sorted(export_map.items())),
            )
        )
    known_roots = {package.root for package in packages}
    inferred_roots = {
        "/".join(path.split("/")[: index + 2])
        for path in files
        for index, part in enumerate(path.split("/"))
        if part in _MONOREPO_ROOTS and index + 1 < len(path.split("/"))
    }
    for root in sorted(inferred_roots - known_roots):
        packages.append(
            _PackageInfo(root=root, manifest_path=None, name=None, metadata={})
        )
    packages.sort(key=lambda item: (item.root != ".", item.root))
    by_name = {package.name: package for package in packages if package.name}
    return tuple(packages), by_name


def _path_aliases(
    files: dict[str, _ImportFile],
) -> tuple[tuple[str, tuple[str, ...], str], ...]:
    aliases: list[tuple[str, tuple[str, ...], str]] = []
    for config_path in sorted(
        path
        for path in files
        if posixpath.basename(path) in {"tsconfig.json", "jsconfig.json"}
    ):
        config = _json_bytes(files[config_path].payload)
        compiler = config.get("compilerOptions") if config else None
        if not isinstance(compiler, dict):
            continue
        raw_paths = compiler.get("paths")
        if not isinstance(raw_paths, dict):
            continue
        base_url = compiler.get("baseUrl", ".")
        if not isinstance(base_url, str):
            continue
        config_dir = posixpath.dirname(config_path)
        alias_base = posixpath.normpath(posixpath.join(config_dir, base_url))
        for pattern, targets in raw_paths.items():
            if not isinstance(pattern, str) or pattern.count("*") > 1:
                continue
            if isinstance(targets, str):
                targets = [targets]
            if isinstance(targets, list) and all(
                isinstance(target, str) for target in targets
            ):
                aliases.append((pattern, tuple(targets), alias_base))
    return tuple(sorted(aliases, key=lambda item: (-len(item[0]), item[0])))


def _package_aliases(packages: Iterable[_PackageInfo]) -> tuple[PackageAlias, ...]:
    return tuple(
        PackageAlias(
            name=package.name or "",
            root=package.root,
            entries=package.entries,
            exports=package.exports,
        )
        for package in packages
        if package.name
    )


def _package_for_path(path: str, packages: tuple[_PackageInfo, ...]) -> str:
    matches = [
        package.root
        for package in packages
        if package.root == "."
        or path == package.root
        or path.startswith(f"{package.root}/")
    ]
    return max(matches, key=lambda item: (item != ".", len(item))) if matches else "."


def _choose_primary_package(
    paths: Iterable[str], packages: tuple[_PackageInfo, ...]
) -> str:
    path_list = tuple(paths)
    roots = tuple(dict.fromkeys(package.root for package in packages)) or (".",)

    def score(root: str) -> tuple[int, int, int, int, str]:
        in_package = [
            path for path in path_list if _package_for_path(path, packages) == root
        ]
        has_app = int(
            any(
                path.startswith(f"{root}/app/")
                or (root == "." and path.startswith("app/"))
                for path in in_package
            )
        )
        has_source = int(
            any(
                posixpath.basename(path)
                in {
                    "page.tsx",
                    "page.ts",
                    "page.jsx",
                    "page.js",
                    "route.ts",
                    "route.tsx",
                    "route.js",
                    "route.jsx",
                }
                or "/src/" in f"/{path}"
                for path in in_package
            )
        )
        manifest = int(
            any(package.root == root and package.manifest_path for package in packages)
        )
        return (
            has_app,
            has_source,
            len(in_package),
            manifest,
            "" if root == "." else root,
        )

    return max(roots, key=score)


def _select_import(
    candidates: _CandidateCollection, *, full_workspace: bool = False
) -> _ImportSelection:
    by_path = {item.repo_relative_path: item for item in candidates.files}
    if not by_path:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "selected source has no supported source, config, or documentation files",
        )
    packages, packages_by_name = _package_index(by_path)
    primary = _choose_primary_package(by_path, packages)
    if full_workspace:
        selected_paths = set(by_path)
    else:
        selected_paths = {
            path for path in by_path if _package_for_path(path, packages) == primary
        }
        for path in by_path:
            if path in {"package.json", "tsconfig.json", "jsconfig.json"}:
                selected_paths.add(path)
        for path in by_path:
            if _package_for_path(path, packages) == primary and posixpath.basename(
                path
            ) in {"package.json", "tsconfig.json", "jsconfig.json"}:
                selected_paths.add(path)

    source_paths = set(by_path)
    aliases = _path_aliases(by_path)
    package_aliases = _package_aliases(packages)
    unresolved: set[str] = set()
    visited: set[str] = set()
    queue = sorted(
        path for path in selected_paths if path.endswith(tuple(_CODE_SUFFIXES))
    )
    while queue:
        path = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        item = by_path[path]
        try:
            text = item.payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        source = Source(path, text, tuple(text.splitlines()))
        for specifier, _offset in _module_references(source):
            resolved = _resolve_import(
                path, specifier, source_paths, aliases, package_aliases
            )
            if resolved and resolved in by_path:
                if resolved not in selected_paths:
                    selected_paths.add(resolved)
                    queue.append(resolved)
                package_root = _package_for_path(resolved, packages)
                package = next(
                    (item for item in packages if item.root == package_root), None
                )
                if (
                    package
                    and package.manifest_path
                    and package.manifest_path in by_path
                ):
                    selected_paths.add(package.manifest_path)
            elif specifier in packages_by_name or any(
                specifier.startswith(f"{name}/") for name in packages_by_name
            ):
                unresolved.add(specifier)
    closure = tuple(
        sorted({_package_for_path(path, packages) for path in selected_paths})
    )
    retained = tuple(by_path[path] for path in sorted(selected_paths))
    skipped = candidates.skipped_files + len(by_path) - len(retained)
    return _ImportSelection(
        files=retained,
        skipped_files=skipped,
        retained_bytes=sum(len(item.payload) for item in retained),
        primary_package=primary,
        closure_packages=closure,
        unresolved_workspace_imports=tuple(sorted(unresolved)),
    )


def _safe_relative_path(filename: str) -> PurePosixPath:
    if not isinstance(filename, str):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "selected source contains an invalid relative path",
        )
    normalized = filename.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or _WINDOWS_DRIVE.match(normalized)
    ):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "selected source contains an unsafe relative path",
        )
    clean = tuple(part for part in normalized.split("/") if part not in {"", "."})
    if not clean or ".." in clean:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "selected source contains an unsafe relative path",
        )
    path = PurePosixPath(*clean)
    if path.is_absolute() or ".." in path.parts:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "selected source contains an unsafe relative path",
        )
    return path


def _relative_filename(filename: str) -> tuple[str, str]:
    """Validate an import name without flattening its repository path."""

    path = _safe_relative_path(filename)
    return path.parts[0], path.as_posix()


def _normalize_candidates(
    records: Iterable[tuple[str, str, bytes]], *, force_archive_wrapper: bool = False
) -> tuple[_ImportFile, ...]:
    """Remove only a synthetic picker/archive wrapper.

    Browser directory pickers and GitHub archives prepend one outer directory.
    The real monorepo path below it must remain intact for evidence and package
    resolution.
    """

    raw = [
        (top, _safe_relative_path(relative), payload)
        for top, relative, payload in records
    ]
    if not raw:
        return ()
    first_parts = {path.parts[0] for _, path, _ in raw}
    common_first = next(iter(first_parts)) if len(first_parts) == 1 else None
    remove_wrapper = (
        common_first is not None
        and all(len(path.parts) > 1 for _, path, _ in raw)
        and (
            force_archive_wrapper
            or common_first not in _KNOWN_SOURCE_ROOTS | _MONOREPO_ROOTS
        )
    )
    normalized: list[_ImportFile] = []
    seen: set[str] = set()
    for top, path, payload in raw:
        parts = path.parts[1:] if remove_wrapper else path.parts
        if not parts:
            continue
        normalized_path = PurePosixPath(*parts).as_posix()
        if normalized_path in seen:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "selected source contains duplicate normalized paths",
            )
        seen.add(normalized_path)
        normalized.append(
            _ImportFile(
                source_top=top or path.parts[0],
                repo_relative_path=normalized_path,
                payload=payload,
            )
        )
    return tuple(normalized)


def _is_supported_path(path: PurePosixPath) -> bool:
    return (
        path.suffix.casefold() in _SUPPORTED_IMPORT_SUFFIXES
        or path.parts[-2:] == PurePosixPath(PRISMA_SCHEMA_RELATIVE_PATH).parts
    )


def _github_repository(url: str) -> tuple[str, str]:
    if not isinstance(url, str) or not url.strip():
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub repository URL is required",
        )
    value = url.strip()
    if len(value) > 2_048:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub repository URL is too long",
        )
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub repository URL is invalid",
        ) from error
    if (
        parsed.scheme.casefold() != "https"
        or hostname is None
        or hostname.casefold() not in {"github.com", "www.github.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub import accepts only a public https://github.com/owner/repo URL",
        )
    path = unquote(parsed.path).strip("/")
    parts = path.split("/") if path else []
    if len(parts) != 2:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub repository URL must look like https://github.com/owner/repo",
        )
    owner, repository = parts
    if repository.casefold().endswith(".git"):
        repository = repository[:-4]
    if (
        not _GITHUB_COMPONENT.fullmatch(owner)
        or not _GITHUB_COMPONENT.fullmatch(repository)
        or owner in {".", ".."}
        or repository in {".", ".."}
    ):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub repository URL contains an invalid owner or repository name",
        )
    return owner, repository


def _github_ref(value: str | None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "HEAD"
    if not isinstance(value, str):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub ref must be a branch or tag name",
        )
    ref = value.strip()
    if (
        len(ref) > 128
        or not _GITHUB_REF.fullmatch(ref)
        or ".." in ref
        or "//" in ref
        or ref.endswith("/")
    ):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub ref must be a safe branch or tag name",
        )
    return ref


def _download_github_archive(owner: str, repository: str, ref: str) -> bytes:
    archive_url = (
        "https://codeload.github.com/"
        f"{quote(owner, safe='')}/{quote(repository, safe='')}/tar.gz/"
        f"{quote(ref, safe='/')}"
    )
    request = Request(
        archive_url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "VibeWiki-local-import",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > MAX_GITHUB_ARCHIVE_BYTES:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "GitHub archive exceeds the local download limit "
                        f"(limit: {MAX_GITHUB_ARCHIVE_BYTES} bytes)",
                    )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(
                    min(1024 * 1024, MAX_GITHUB_ARCHIVE_BYTES - total + 1)
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_GITHUB_ARCHIVE_BYTES:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "GitHub archive exceeds the local download limit "
                        f"(limit: {MAX_GITHUB_ARCHIVE_BYTES} bytes)",
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except VibeWikiError:
        raise
    except HTTPError as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub archive download failed "
            f"(HTTP {error.code}); check the URL and ref",
        ) from error
    except (TimeoutError, URLError, OSError) as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub archive download failed; check the URL, ref, "
            "and network connection",
        ) from error


def _github_candidates(archive_bytes: bytes) -> _CandidateCollection:
    """Index supported archive members before reading their payloads."""

    records: list[tuple[str, str, tarfile.TarInfo]] = []
    skipped_files = 0
    member_count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            for member in archive:
                member_count += 1
                if member_count > MAX_GITHUB_ARCHIVE_MEMBERS:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "GitHub archive contains too many members "
                        f"(limit: {MAX_GITHUB_ARCHIVE_MEMBERS})",
                    )
                if not member.isfile():
                    continue
                top, relative = _relative_filename(member.name)
                path = PurePosixPath(relative)
                if should_skip_path(path) or not _is_supported_path(path):
                    skipped_files += 1
                    continue
                if member.size < 0:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "GitHub archive contains a file with an invalid size",
                    )
                records.append((top, relative, member))
    except VibeWikiError:
        raise
    except (EOFError, OSError, tarfile.TarError) as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub archive is invalid or could not be read",
        ) from error

    if not records:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "GitHub repository has no supported source, config, or documentation files",
        )
    if len(records) > MAX_IMPORT_FILES:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub repository contains too many supported files "
            f"(limit: {MAX_IMPORT_FILES})",
        )

    normalized_names = _normalize_candidates(
        ((top, relative, b"") for top, relative, _ in records),
        force_archive_wrapper=True,
    )
    selected: list[_ImportFile] = []
    total_bytes = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            for item, (_, _, original_member) in zip(
                normalized_names, records, strict=True
            ):
                if original_member.size > MAX_IMPORT_BYTES:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "GitHub repository exceeds the supported byte limit "
                        f"(limit: {MAX_IMPORT_BYTES})",
                    )
                extracted = archive.extractfile(original_member)
                if extracted is None:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "GitHub archive contains an unreadable source file",
                    )
                payload = extracted.read(original_member.size)
                if len(payload) != original_member.size:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "GitHub archive contains a truncated source file",
                    )
                total_bytes += len(payload)
                if total_bytes > MAX_IMPORT_BYTES:
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "GitHub repository exceeds the supported byte limit "
                        f"(limit: {MAX_IMPORT_BYTES})",
                    )
                selected.append(
                    _ImportFile(item.source_top, item.repo_relative_path, payload)
                )
    except VibeWikiError:
        raise
    except (EOFError, OSError, tarfile.TarError) as error:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "GitHub archive is invalid or could not be read",
        ) from error
    return _CandidateCollection(tuple(selected), skipped_files)


def _github_archive_files(
    archive_bytes: bytes,
) -> tuple[list[tuple[str, str, bytes]], int]:
    collection = _github_candidates(archive_bytes)
    return (
        [
            (item.source_top, item.repo_relative_path, item.payload)
            for item in collection.files
        ],
        collection.skipped_files,
    )


def _multipart_candidates(content_type: str, body: bytes) -> _CandidateCollection:
    if not content_type.lower().startswith("multipart/form-data"):
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "source selection must use a local directory picker",
        )
    header = (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode(
        "utf-8"
    )
    message = BytesParser(policy=policy.default).parsebytes(header + body)
    if not message.is_multipart():
        raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "source selection is invalid")

    records: list[tuple[str, str, Any]] = []
    skipped_files = 0
    part_count = 0
    for part in message.walk():
        if part.is_multipart():
            continue
        part_count += 1
        if part_count > MAX_MULTIPART_PARTS:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "selected source contains too many multipart parts "
                f"(limit: {MAX_MULTIPART_PARTS})",
            )
        filename = part.get_filename()
        if not filename:
            skipped_files += 1
            continue
        top, relative = _relative_filename(filename)
        path = PurePosixPath(relative)
        if should_skip_path(path) or not _is_supported_path(path):
            skipped_files += 1
            continue
        records.append((top, relative, part))

    if not records:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "selected source has no supported source, config, or documentation files",
        )
    if len(records) > MAX_IMPORT_FILES:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            "selected source contains too many supported files "
            f"(limit: {MAX_IMPORT_FILES})",
        )

    normalized_names = _normalize_candidates(
        ((top, relative, b"") for top, relative, _ in records)
    )
    parts_by_name = {
        item.repo_relative_path: part
        for item, (_, _, part) in zip(normalized_names, records, strict=True)
    }
    selected: list[_ImportFile] = []
    total_bytes = 0
    for item in normalized_names:
        payload = parts_by_name[item.repo_relative_path].get_payload(decode=True) or b""
        total_bytes += len(payload)
        if total_bytes > MAX_IMPORT_BYTES:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "selected supported source exceeds the byte limit "
                f"(limit: {MAX_IMPORT_BYTES})",
            )
        selected.append(_ImportFile(item.source_top, item.repo_relative_path, payload))
    return _CandidateCollection(tuple(selected), skipped_files)


def _multipart_files(content_type: str, body: bytes) -> list[tuple[str, str, bytes]]:
    """Compatibility view used by the import tests and local integrations."""

    collection = _multipart_candidates(content_type, body)
    return [
        (item.source_top, item.repo_relative_path, item.payload)
        for item in collection.files
    ]


def import_uploaded_workspace(content_type: str, body: bytes) -> ImportedWorkspace:
    """Build a temporary local workspace from browser-selected safe files."""

    selection = _select_import(_multipart_candidates(content_type, body))
    project_name = "imported-source"
    first_top = selection.files[0].source_top
    if first_top not in {"app", "src", "tests", "prisma"}:
        project_name = first_top

    return _build_imported_workspace(selection, project_name, provider="browser-folder")


def _build_imported_workspace(
    selection: _ImportSelection,
    project_name: str,
    *,
    provider: str,
    extra_source: dict[str, Any] | None = None,
) -> ImportedWorkspace:
    temp_parent = Path(tempfile.mkdtemp(prefix="vibewiki-import-"))
    root = temp_parent / project_name
    root.mkdir()
    try:
        for item in selection.files:
            destination = root / Path(item.repo_relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(item.payload)
        scan_repository(root, allow_generic=True)
        build_summary = build_repository(root)
    except Exception:
        shutil.rmtree(temp_parent, ignore_errors=True)
        raise
    import_source: dict[str, Any] = {
        "provider": provider,
        "selected_files": len(selection.files),
        "skipped_files": selection.skipped_files,
        "retained_bytes": selection.retained_bytes,
        "primary_package": selection.primary_package,
        "closure_packages": list(selection.closure_packages),
        "unresolved_workspace_imports": list(selection.unresolved_workspace_imports),
    }
    if extra_source:
        import_source.update(extra_source)
    build_summary["import_source"] = import_source
    return ImportedWorkspace(root=root, build_summary=build_summary)


def import_local_workspace(repository: str | Path) -> ImportedWorkspace:
    """Copy a user-selected local directory into a bounded import workspace.

    This loopback-only path import exists for browsers that cannot expose a
    directory picker. It uses the same ignored/sensitive path policy and byte
    and file limits as multipart Browse, then builds a temporary snapshot so
    the server never mutates the user's source repository.
    """

    source_root = Path(repository).expanduser().absolute()
    discovered = list(discover_files(source_root, include_generic=True))
    schema = source_root / Path(PRISMA_SCHEMA_RELATIVE_PATH)
    if (
        schema.is_file()
        and not schema.is_symlink()
        and not should_skip_path(PurePosixPath(PRISMA_SCHEMA_RELATIVE_PATH))
    ):
        schema_size = schema.stat().st_size
        discovered.append(
            DiscoveredFile(
                path=PRISMA_SCHEMA_RELATIVE_PATH,
                language="prisma",
                size=schema_size,
                absolute_path=schema,
            )
        )
    if not discovered:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "local path contains no supported source, config, or documentation files",
        )
    if len(discovered) > MAX_IMPORT_FILES:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            f"local path contains too many supported files (limit: {MAX_IMPORT_FILES})",
        )
    total_bytes = sum(item.size for item in discovered)
    if total_bytes > MAX_IMPORT_BYTES:
        raise VibeWikiError(
            ErrorCode.INVALID_OUTPUT,
            f"local path exceeds the supported byte limit (limit: {MAX_IMPORT_BYTES})",
        )

    selected: list[_ImportFile] = []
    seen: set[str] = set()
    for item in discovered:
        path = PurePosixPath(item.path)
        normalized = path.as_posix()
        if normalized in seen:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "local path contains duplicate normalized source paths",
            )
        seen.add(normalized)
        try:
            payload = item.absolute_path.read_bytes()
        except (OSError, ValueError) as error:
            raise VibeWikiError(
                ErrorCode.PERMISSION_DENIED,
                "permission denied while reading the local source path",
            ) from error
        selected.append(_ImportFile(path.parts[0], normalized, payload))
    if not selected:
        raise VibeWikiError(
            ErrorCode.UNSUPPORTED_STACK,
            "local path package contains no supported source files",
        )
    project_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source_root.name).strip("-")
    selection = _select_import(
        _CandidateCollection(tuple(selected), 0), full_workspace=True
    )
    return _build_imported_workspace(
        selection, project_name or "local-source", provider="local-path"
    )


def import_github_workspace(url: str, ref: str | None = None) -> ImportedWorkspace:
    """Import a public GitHub repository archive into a temporary workspace.

    Network access is deliberately limited to this explicit user-triggered
    action. The downloaded archive is bounded before extraction, and only
    regular supported files survive the same ignore/secret policy as local
    Browse imports.
    """

    owner, repository = _github_repository(url)
    selected_ref = _github_ref(ref)
    archive_bytes = _download_github_archive(owner, repository, selected_ref)
    candidates = _github_candidates(archive_bytes)
    selection = _select_import(candidates)
    project_name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{owner}-{repository}").strip("-")
    imported = _build_imported_workspace(
        selection,
        project_name or "github-source",
        provider="github",
        extra_source={
            "repository": f"{owner}/{repository}",
            "ref": selected_ref,
            "url": f"https://github.com/{owner}/{repository}",
            "archive_bytes": len(archive_bytes),
        },
    )
    return imported


def cleanup_workspace(workspace: ImportedWorkspace) -> None:
    """Remove only the temporary workspace created by a browser import."""

    shutil.rmtree(workspace.root.parent, ignore_errors=True)


__all__ = [
    "ImportedWorkspace",
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_FILES",
    "MAX_MULTIPART_PARTS",
    "MAX_GITHUB_ARCHIVE_BYTES",
    "cleanup_workspace",
    "import_github_workspace",
    "import_local_workspace",
    "import_uploaded_workspace",
]

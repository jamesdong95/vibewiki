"""Durable, local-only storage for imported VibeWiki workspaces.

The registry deliberately stores only provenance metadata and a pointer to a
managed snapshot.  Public callers receive a sanitised view; absolute source
paths remain private to the local registry.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ErrorCode, VibeWikiError

REGISTRY_VERSION = 1
MAX_WORKSPACES = 32
MAX_LABEL_CHARS = 120
MAX_PREFERENCE_CHARS = 240
_ID_PREFIX = "ws_"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_state_dir() -> Path:
    override = os.environ.get("VIBEWIKI_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "") or str(Path.home())
        return Path(base) / "VibeWiki"
    if sys_platform := os.environ.get("XDG_DATA_HOME", "").strip():
        return Path(sys_platform) / "vibewiki"
    return Path.home() / ".local" / "share" / "vibewiki"


def _safe_label(value: str, fallback: str = "workspace") -> str:
    label = " ".join(str(value).split()).strip()
    return label[:MAX_LABEL_CHARS] or fallback


def _safe_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_ID_PREFIX)
        and 8 <= len(value) <= 80
        and all(character.isalnum() or character in "_-" for character in value)
    )


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    id: str
    label: str
    provider: str
    snapshot_relpath: str
    origin: dict[str, Any]
    created_at: str
    last_opened_at: str
    source_state: str

    @property
    def snapshot_path(self) -> Path:
        raise RuntimeError("snapshot_path requires a WorkspaceStore")


class WorkspaceStore:
    """Manage private snapshot storage with atomic registry updates."""

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.root = Path(state_dir).expanduser() if state_dir else _default_state_dir()
        self.root = self.root.absolute()
        self.cache_root = self.root / "workspaces"
        self.registry_path = self.root / "registry.json"
        self._ensure_private_dir(self.root)
        self._ensure_private_dir(self.cache_root)

    @property
    def llm_preferences_path(self) -> Path:
        return self.root / "llm-preferences.json"

    @staticmethod
    def _ensure_private_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(stat.S_IRWXU)
        except OSError:
            pass

    def _read(self) -> list[WorkspaceRecord]:
        if not self.registry_path.is_file():
            return []
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return []
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != REGISTRY_VERSION
        ):
            return []
        records: list[WorkspaceRecord] = []
        for item in value.get("workspaces", []):
            if not isinstance(item, dict) or not _safe_id(item.get("id", "")):
                continue
            snapshot = item.get("snapshot_root")
            if not isinstance(snapshot, str) or Path(snapshot).is_absolute():
                continue
            snapshot_path = (self.root / Path(snapshot)).absolute()
            try:
                snapshot_path.relative_to(self.cache_root.absolute())
            except ValueError:
                continue
            if snapshot_path == self.cache_root.absolute():
                continue
            origin = item.get("origin")
            if not isinstance(origin, dict):
                origin = {}
            records.append(
                WorkspaceRecord(
                    id=item["id"],
                    label=_safe_label(item.get("label", "workspace")),
                    provider=str(item.get("provider", "browser-folder")),
                    snapshot_relpath=Path(snapshot).as_posix(),
                    origin=dict(origin),
                    created_at=str(item.get("created_at", "")),
                    last_opened_at=str(item.get("last_opened_at", "")),
                    source_state=str(item.get("source_state", "snapshot")),
                )
            )
        return records[:MAX_WORKSPACES]

    def _write(self, records: list[WorkspaceRecord]) -> None:
        payload = {
            "schema_version": REGISTRY_VERSION,
            "workspaces": [
                {
                    "id": item.id,
                    "label": item.label,
                    "provider": item.provider,
                    "snapshot_root": item.snapshot_relpath,
                    "origin": item.origin,
                    "created_at": item.created_at,
                    "last_opened_at": item.last_opened_at,
                    "source_state": item.source_state,
                }
                for item in records
            ],
        }
        fd, temporary = tempfile.mkstemp(
            prefix="registry.", suffix=".tmp", dir=self.root
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.registry_path)
            try:
                self.registry_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def load_llm_preferences(self) -> dict[str, str]:
        """Load only non-secret LLM connection preferences."""

        try:
            value = json.loads(self.llm_preferences_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return {}
        if not isinstance(value, dict):
            return {}
        result: dict[str, str] = {}
        for key in ("provider", "model", "base_url"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                result[key] = item.strip()[:MAX_PREFERENCE_CHARS]
        return result

    def save_llm_preferences(
        self, *, provider: str, model: str, base_url: str
    ) -> None:
        """Persist provider metadata without API keys or remote consent."""

        payload = {
            "schema_version": 1,
            "provider": provider[:MAX_PREFERENCE_CHARS],
            "model": model[:MAX_PREFERENCE_CHARS],
            "base_url": base_url[:MAX_PREFERENCE_CHARS],
        }
        fd, temporary = tempfile.mkstemp(
            prefix="llm-preferences.", suffix=".tmp", dir=self.root
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.llm_preferences_path)
            try:
                self.llm_preferences_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _path_for(self, record: WorkspaceRecord) -> Path:
        candidate = (self.root / Path(record.snapshot_relpath)).absolute()
        try:
            candidate.relative_to(self.cache_root.absolute())
        except ValueError as error:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT,
                "workspace snapshot is outside the managed cache",
            ) from error
        return candidate

    def _record(self, workspace_id: str) -> WorkspaceRecord:
        if not _safe_id(workspace_id):
            raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "workspace was not found")
        record = next((item for item in self._read() if item.id == workspace_id), None)
        if record is None:
            raise VibeWikiError(ErrorCode.PATH_NOT_FOUND, "workspace was not found")
        return record

    def public(self) -> list[dict[str, Any]]:
        records = sorted(
            self._read(), key=lambda item: (item.last_opened_at, item.id), reverse=True
        )
        result: list[dict[str, Any]] = []
        for record in records:
            snapshot = self._path_for(record)
            available = snapshot.is_dir()
            if record.provider == "local-path":
                source = record.origin.get("local_path")
                source_available = isinstance(source, str) and Path(source).is_dir()
                state = "live" if source_available else "source_unavailable"
            else:
                state = "available" if available else "unavailable"
            result.append(
                {
                    "id": record.id,
                    "label": record.label,
                    "provider": record.provider,
                    "state": state,
                    "source_state": record.source_state,
                    "last_opened_at": record.last_opened_at,
                    "can_refresh": record.provider in {"local-path", "github"},
                }
            )
        return result

    def get(self, workspace_id: str) -> tuple[WorkspaceRecord, Path]:
        record = self._record(workspace_id)
        path = self._path_for(record)
        if not path.is_dir():
            raise VibeWikiError(
                ErrorCode.PATH_NOT_FOUND, "workspace snapshot is unavailable"
            )
        return record, path

    def touch(self, workspace_id: str) -> WorkspaceRecord:
        records = self._read()
        record = self._record(workspace_id)
        updated = WorkspaceRecord(
            record.id,
            record.label,
            record.provider,
            record.snapshot_relpath,
            record.origin,
            record.created_at,
            _now(),
            record.source_state,
        )
        self._write([updated if item.id == workspace_id else item for item in records])
        return updated

    def save_snapshot(
        self,
        source_root: str | Path,
        *,
        label: str,
        provider: str,
        origin: dict[str, Any] | None = None,
        workspace_id: str | None = None,
    ) -> tuple[WorkspaceRecord, Path]:
        source = Path(source_root).absolute()
        if not source.is_dir():
            raise VibeWikiError(
                ErrorCode.PATH_NOT_FOUND, "workspace snapshot source was not found"
            )
        identifier = (
            workspace_id
            if workspace_id and _safe_id(workspace_id)
            else f"{_ID_PREFIX}{secrets.token_urlsafe(12)}"
        )
        if not _safe_id(identifier):
            raise VibeWikiError(ErrorCode.INVALID_OUTPUT, "workspace id is invalid")
        workspace_dir = self.cache_root / identifier
        destination = workspace_dir / "repo"
        if workspace_dir.exists() or workspace_dir.is_symlink():
            if workspace_dir.is_symlink() or not workspace_dir.is_dir():
                raise VibeWikiError(
                    ErrorCode.INVALID_OUTPUT,
                    "workspace snapshot destination is not a managed directory",
                )
        workspace_dir.mkdir(parents=True, exist_ok=True)
        staging_parent = Path(
            tempfile.mkdtemp(prefix=f".{identifier}.", dir=self.cache_root)
        )
        staging = staging_parent / "repo"
        try:
            shutil.copytree(source, staging, symlinks=False)
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink() or not destination.is_dir():
                    raise VibeWikiError(
                        ErrorCode.INVALID_OUTPUT,
                        "workspace snapshot destination is not a managed directory",
                    )
                shutil.rmtree(destination)
            os.replace(staging, destination)
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)
        now = _now()
        existing = next((item for item in self._read() if item.id == identifier), None)
        record = WorkspaceRecord(
            identifier,
            _safe_label(label, source.name),
            provider,
            destination.relative_to(self.root).as_posix(),
            dict(origin or {}),
            existing.created_at if existing else now,
            now,
            "snapshot",
        )
        records = [item for item in self._read() if item.id != identifier]
        self._write([record, *records][:MAX_WORKSPACES])
        return record, destination

    def forget(self, workspace_id: str) -> None:
        records = self._read()
        record = self._record(workspace_id)
        target = self._path_for(record)
        managed_workspace = target.parent
        try:
            managed_workspace.relative_to(self.cache_root.absolute())
        except ValueError as error:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT, "workspace cache path is invalid"
            ) from error
        if managed_workspace == self.cache_root or managed_workspace == self.root:
            raise VibeWikiError(
                ErrorCode.INVALID_OUTPUT, "workspace cache path is invalid"
            )
        if managed_workspace.exists():
            shutil.rmtree(managed_workspace)
        self._write([item for item in records if item.id != workspace_id])


__all__ = ["REGISTRY_VERSION", "WorkspaceRecord", "WorkspaceStore"]

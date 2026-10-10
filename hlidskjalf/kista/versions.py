"""Slice 10 — Kista artifact versioning & provenance lineage.

Every version binds a content hash to an explicit parent set, forming a
provenance DAG. ``history()`` walks the chain backwards from a version;
``lineage()`` walks it from an artifact hash up to its genesis.

Persisted as one JSON document per version under ``<root>/versions/`` plus
a lightweight children index so heads() is cheap.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from hlidskjalf.kista._compat import (
    NotFoundError,
    StorageError,
    ValidationError,
    get_logger,
    new_id,
)
from hlidskjalf.kista.store import ArtifactStore

log = get_logger(__name__)


class VersionGraph:
    """Provenance DAG over artifact hashes, persisted beside the store."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store
        self.root = Path(store.root) / "versions"
        self.root.mkdir(parents=True, exist_ok=True)

    # -- persistence ---------------------------------------------------
    def _path(self, version_id: str) -> Path:
        return self.root / f"{version_id}.json"

    def _write(self, version_id: str, record: Dict[str, Any]) -> None:
        tmp = self.root / f".tmp-{new_id()}.json"
        try:
            tmp.write_text(json.dumps(record, indent=2))
            os.replace(tmp, self._path(version_id))
        finally:
            tmp.unlink(missing_ok=True)

    def _read(self, version_id: str) -> Dict[str, Any]:
        path = self._path(version_id)
        if not path.exists():
            raise NotFoundError(f"version not found: {version_id}")
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise StorageError(
                f"corrupt version record {version_id} at {path}: {exc}"
            ) from exc

    def list_version_ids(self) -> List[str]:
        return sorted(
            p.stem
            for p in self.root.iterdir()
            if p.is_file() and p.suffix == ".json" and not p.name.startswith(".tmp-")
        )

    # -- creation ------------------------------------------------------
    def create_version(
        self,
        data: bytes,
        parents: Optional[List[str]] = None,
        message: str = "",
        tags: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store data and record a new version node.

        ``parents`` are version ids this version derives from. Genesis
        versions pass ``parents=[]`` (or None).
        """
        parents = list(parents or [])
        for pid in parents:
            self._read(pid)  # validate: no orphan parents
        digest = self.store.put(data, tags=tags, meta=meta)
        version_id = new_id()
        record = {
            "version_id": version_id,
            "hash": digest,
            "parents": parents,
            "message": message,
            "created_at": time.time(),
        }
        self._write(version_id, record)
        log.debug("kista.version id=%s hash=%s parents=%s", version_id[:8], digest[:12], len(parents))
        return version_id

    # -- queries -------------------------------------------------------
    def get(self, version_id: str) -> Dict[str, Any]:
        return self._read(version_id)

    def history(self, version_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Provenance chain, newest first, following first-parent links."""
        out: List[Dict[str, Any]] = []
        seen = set()
        current: Optional[str] = version_id
        while current and current not in seen:
            seen.add(current)
            record = self._read(current)
            out.append(record)
            parents = record.get("parents", [])
            current = parents[0] if parents else None
            if limit is not None and len(out) >= limit:
                break
        return out

    def lineage(self, digest: str) -> List[Dict[str, Any]]:
        """All version records whose content hash is ``digest``, newest first."""
        matches = [self._read(vid) for vid in self.list_version_ids()]
        return sorted(
            (r for r in matches if r["hash"] == digest),
            key=lambda r: r["created_at"],
            reverse=True,
        )

    def versions_of(self, digest: str) -> List[str]:
        return [r["version_id"] for r in self.lineage(digest)]

    def children(self, version_id: str) -> List[str]:
        self._read(version_id)  # validate existence
        return [
            r["version_id"]
            for r in (self._read(v) for v in self.list_version_ids())
            if version_id in r.get("parents", [])
        ]

    def heads(self) -> List[str]:
        """Version ids with no children (DAG tips)."""
        all_ids = self.list_version_ids()
        has_parent = set()
        records = {vid: self._read(vid) for vid in all_ids}
        for rec in records.values():
            has_parent.update(rec.get("parents", []))
        return [vid for vid in all_ids if vid not in has_parent]

    def ancestors(self, version_id: str) -> List[str]:
        """All ancestor version ids (transitive parents), deduplicated."""
        out: List[str] = []
        stack = [version_id]
        seen = {version_id}
        while stack:
            rec = self._read(stack.pop())
            for pid in rec.get("parents", []):
                if pid not in seen:
                    seen.add(pid)
                    out.append(pid)
                    stack.append(pid)
        return out

    def get_data(self, version_id: str) -> bytes:
        """Resolve a version to its artifact bytes."""
        return self.store.get(self._read(version_id)["hash"])

    def validate(self) -> List[str]:
        """Check for orphan parents; return list of problem descriptions."""
        problems: List[str] = []
        ids = set(self.list_version_ids())
        for vid in ids:
            for pid in self._read(vid).get("parents", []):
                if pid not in ids:
                    problems.append(f"version {vid} has missing parent {pid}")
        return problems

    def __len__(self) -> int:
        return len(self.list_version_ids())

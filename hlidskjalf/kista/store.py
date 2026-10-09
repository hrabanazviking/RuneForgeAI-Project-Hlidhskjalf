"""Slice 9 — Kista content-addressed artifact store.

Artifacts are addressed by the SHA-256 of their bytes. Identical bytes
deduplicate automatically. Each artifact carries a JSON sidecar with
metadata (size, tags, timestamps, provenance hints).

On-disk layout under ``<root>/``::

    objects/ab/cdef...   # raw bytes, sharded by first 2 hex chars
    meta/ab/cdef....json  # sidecar: hash, size, created_at, tags, meta

Writes are atomic (temp file + rename) and dedup is lock-free by nature:
the same hash always names the same bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from hlidskjalf.kista._compat import (
    NotFoundError,
    StorageError,
    ValidationError,
    get_logger,
    new_id,
)

log = get_logger(__name__)

META_SCHEMA_VERSION = 1


class ArtifactStore:
    """Filesystem-backed content-addressed store."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.meta = self.root / "meta"
        for d in (self.objects, self.meta):
            d.mkdir(parents=True, exist_ok=True)

    # -- paths ---------------------------------------------------------
    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest[2:]

    def _meta_path(self, digest: str) -> Path:
        return self.meta / digest[:2] / (digest[2:] + ".json")

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    # -- writes --------------------------------------------------------
    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f".tmp-{new_id()}"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except OSError as exc:  # pragma: no cover - os failure
            raise StorageError(f"failed writing {path}: {exc}") from exc
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def put(
        self,
        data: bytes,
        tags: Optional[Iterable[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store bytes; return the SHA-256 hex digest. Idempotent."""
        if not isinstance(data, (bytes, bytearray)):
            raise ValidationError("data must be bytes")
        payload = bytes(data)
        digest = self._digest(payload)

        obj_path = self._object_path(digest)
        if not obj_path.exists():
            self._atomic_write(obj_path, payload)

        sidecar = {
            "schema": META_SCHEMA_VERSION,
            "hash": digest,
            "size": len(payload),
            "created_at": time.time(),
            "tags": sorted(set(tags or [])),
            "meta": dict(meta or {}),
        }
        self._atomic_write(self._meta_path(digest), json.dumps(sidecar, indent=2).encode())
        log.debug("kista.put hash=%s size=%d", digest[:12], len(payload))
        return digest

    # -- reads ---------------------------------------------------------
    def exists(self, digest: str) -> bool:
        return self._object_path(digest).exists()

    def get(self, digest: str) -> bytes:
        path = self._object_path(digest)
        if not path.exists():
            raise NotFoundError(f"artifact not found: {digest}")
        with open(path, "rb") as fh:
            return fh.read()

    def get_meta(self, digest: str) -> Dict[str, Any]:
        path = self._meta_path(digest)
        if not path.exists():
            raise NotFoundError(f"artifact metadata not found: {digest}")
        with open(path, "rb") as fh:
            return json.loads(fh.read().decode())

    def delete(self, digest: str) -> bool:
        """Remove artifact + sidecar. Returns True if something was removed."""
        removed = False
        for path in (self._object_path(digest), self._meta_path(digest)):
            if path.exists():
                path.unlink()
                removed = True
        return removed

    # -- inventory -----------------------------------------------------
    def list_hashes(self) -> List[str]:
        hashes: List[str] = []
        for shard in self.objects.iterdir():
            if not shard.is_dir():
                continue
            for blob in shard.iterdir():
                if blob.is_file() and not blob.name.startswith(".tmp-"):
                    hashes.append(shard.name + blob.name)
        return sorted(hashes)

    def stats(self) -> Dict[str, Any]:
        total_bytes = 0
        count = 0
        for digest in self.list_hashes():
            p = self._object_path(digest)
            if p.exists():
                total_bytes += p.stat().st_size
                count += 1
        return {"artifacts": count, "bytes": total_bytes, "root": str(self.root)}

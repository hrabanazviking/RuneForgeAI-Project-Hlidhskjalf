"""Slice 14 — Kista vault sync (host <-> edge).

Protocol (local-dir transport for now; the same manifest comparison drives
a future LAN transport):

1. Each side builds a **manifest**: ``{hash: {"size", "mtime"}}``.
2. ``diff_manifests(local, remote)`` yields ``missing_in_remote``,
   ``missing_in_local``, and ``metadata_conflicts`` (same hash, sidecar
   metadata differs — conflict resolution: newest sidecar wins + logged).
3. ``push`` copies missing artifacts to the peer; ``pull`` copies them
   back. ``sync`` does both directions.

Artifacts are content-addressed, so identical hashes never conflict on
bytes — only sidecar metadata can diverge, and newest-wins resolves it.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from hlidskjalf.kista._compat import get_logger
from hlidskjalf.kista.store import ArtifactStore

log = get_logger(__name__)


@dataclass
class SyncReport:
    pushed: List[str] = field(default_factory=list)
    pulled: List[str] = field(default_factory=list)
    metadata_conflicts: List[str] = field(default_factory=list)
    conflict_resolutions: List[Dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "pushed": sorted(self.pushed),
            "pulled": sorted(self.pulled),
            "metadata_conflicts": sorted(self.metadata_conflicts),
            "conflict_resolutions": self.conflict_resolutions,
        }


def manifest(store: ArtifactStore) -> Dict[str, Dict]:
    """Build a manifest of everything in the store."""
    out: Dict[str, Dict] = {}
    for digest in store.list_hashes():
        try:
            meta = store.get_meta(digest)
            out[digest] = {"size": meta.get("size", 0), "mtime": meta.get("created_at", 0)}
        except Exception:  # noqa: BLE001 - best effort
            out[digest] = {"size": 0, "mtime": 0}
    return out


def diff_manifests(
    local: Dict[str, Dict], remote: Dict[str, Dict]
) -> Dict[str, List[str]]:
    """Compare manifests.

    Returns ``missing_in_remote`` (local has, remote lacks),
    ``missing_in_local`` (remote has, local lacks) and
    ``metadata_conflicts`` (same hash present on both sides with
    differing manifest mtimes — newest sidecar wins on resolve).
    """
    missing_in_remote = [h for h in local if h not in remote]
    missing_in_local = [h for h in remote if h not in local]
    metadata_conflicts = [
        h
        for h in local
        if h in remote and local[h].get("mtime") != remote[h].get("mtime")
    ]
    return {
        "missing_in_remote": sorted(missing_in_remote),
        "missing_in_local": sorted(missing_in_local),
        "metadata_conflicts": sorted(metadata_conflicts),
    }


def _copy_artifact(src: ArtifactStore, dst: ArtifactStore, digest: str) -> None:
    dst._atomic_write(dst._object_path(digest), src.get(digest))
    dst._atomic_write(
        dst._meta_path(digest),
        json.dumps(src.get_meta(digest)).encode(),
    )


def _resolve_metadata_conflict(
    local: ArtifactStore, remote: ArtifactStore, digest: str
) -> Dict:
    """Newest sidecar wins; returns a resolution record (always logged)."""
    lmeta, rmeta = local.get_meta(digest), remote.get_meta(digest)
    winner = "local" if lmeta.get("created_at", 0) >= rmeta.get("created_at", 0) else "remote"
    src, dst = (local, remote) if winner == "local" else (remote, local)
    dst._atomic_write(dst._meta_path(digest), json.dumps(src.get_meta(digest)).encode())
    record = {
        "hash": digest,
        "winner": winner,
        "local_mtime": lmeta.get("created_at"),
        "remote_mtime": rmeta.get("created_at"),
        "resolved_at": time.time(),
    }
    log.info("kista.sync.conflict hash=%s winner=%s", digest[:12], winner)
    return record


class VaultSync:
    """Bidirectional sync between two local vault directories."""

    def __init__(self, local: ArtifactStore, remote_root: str | os.PathLike[str]) -> None:
        self.local = local
        self.remote = ArtifactStore(remote_root)

    def compare(self) -> Dict[str, List[str]]:
        return diff_manifests(manifest(self.local), manifest(self.remote))

    def push(self, resolve_conflicts: bool = True) -> SyncReport:
        report = SyncReport()
        diff = self.compare()
        for digest in diff["missing_in_remote"]:
            _copy_artifact(self.local, self.remote, digest)
            report.pushed.append(digest)
        if resolve_conflicts:
            for digest in diff["metadata_conflicts"]:
                report.metadata_conflicts.append(digest)
                report.conflict_resolutions.append(
                    _resolve_metadata_conflict(self.local, self.remote, digest)
                )
        log.info("kista.sync.push pushed=%d conflicts=%d", len(report.pushed), len(report.metadata_conflicts))
        return report

    def pull(self, resolve_conflicts: bool = True) -> SyncReport:
        report = SyncReport()
        diff = self.compare()
        for digest in diff["missing_in_local"]:
            _copy_artifact(self.remote, self.local, digest)
            report.pulled.append(digest)
        if resolve_conflicts:
            for digest in diff["metadata_conflicts"]:
                report.metadata_conflicts.append(digest)
                report.conflict_resolutions.append(
                    _resolve_metadata_conflict(self.local, self.remote, digest)
                )
        log.info("kista.sync.pull pulled=%d conflicts=%d", len(report.pulled), len(report.metadata_conflicts))
        return report

    def sync(self, resolve_conflicts: bool = True) -> SyncReport:
        """Full bidirectional sync: push then pull, merged into one report."""
        pushed = self.push(resolve_conflicts=resolve_conflicts)
        pulled = self.pull(resolve_conflicts=resolve_conflicts)
        return SyncReport(
            pushed=pushed.pushed,
            pulled=pulled.pulled,
            metadata_conflicts=sorted(set(pushed.metadata_conflicts) | set(pulled.metadata_conflicts)),
            conflict_resolutions=pushed.conflict_resolutions + pulled.conflict_resolutions,
        )

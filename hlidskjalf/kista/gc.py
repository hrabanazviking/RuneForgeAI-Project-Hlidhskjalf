"""Slice 12 — Kista mark/sweep garbage collection.

Roots are artifact hashes that must survive (e.g. version heads, pinned
artifacts). Everything not reachable from a root is swept. Referenced
artifacts are never deleted; the report says exactly what was kept and
what was reclaimed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from hlidskjalf.kista._compat import get_logger
from hlidskjalf.kista.store import ArtifactStore

log = get_logger(__name__)


@dataclass
class GarbageReport:
    kept: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    reclaimed_bytes: int = 0

    def as_dict(self) -> dict:
        return {
            "kept": sorted(self.kept),
            "removed": sorted(self.removed),
            "reclaimed_bytes": self.reclaimed_bytes,
        }


def collect(
    store: ArtifactStore,
    roots: Iterable[str],
    dry_run: bool = False,
    extra_refs: Optional[Iterable[str]] = None,
) -> GarbageReport:
    """Mark/sweep unreferenced artifacts.

    ``roots``: hashes that must survive. ``extra_refs``: additional
    hashes treated as roots (e.g. version-graph hashes). With
    ``dry_run=True`` nothing is deleted; the report still lists what
    *would* be removed.
    """
    root_set = {r for r in roots if r} | {r for r in (extra_refs or []) if r}
    report = GarbageReport()

    for digest in store.list_hashes():
        if digest in root_set:
            report.kept.append(digest)
            continue
        size = 0
        try:
            size = store.get_meta(digest).get("size", 0)
        except Exception:  # noqa: BLE001 - best-effort size accounting
            pass
        if dry_run:
            report.removed.append(digest)
            report.reclaimed_bytes += size
        else:
            if store.delete(digest):
                report.removed.append(digest)
                report.reclaimed_bytes += size

    log.info(
        "kista.gc kept=%d removed=%d reclaimed=%d dry_run=%s",
        len(report.kept),
        len(report.removed),
        report.reclaimed_bytes,
        dry_run,
    )
    return report


def collect_from_versions(
    store: ArtifactStore,
    version_graph: object,
    dry_run: bool = False,
) -> GarbageReport:
    """GC where every version-graph hash is a root (no orphans deleted).

    ``version_graph`` is duck-typed on ``list_version_ids()`` and ``get()``.
    """
    refs = set()
    for vid in version_graph.list_version_ids():  # type: ignore[attr-defined]
        rec = version_graph.get(vid)  # type: ignore[attr-defined]
        refs.add(rec["hash"])
    return collect(store, refs, dry_run=dry_run)

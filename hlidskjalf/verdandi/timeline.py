"""Slice 18 — Verdandi append-only event timeline.

Events: ``{"id", "ts", "type", "entity", "payload"}``. Appends are strictly
ordered by ``ts`` (ties broken by sequence number). Queries:
``range_query(t0, t1)``, ``before(event_id)``, ``after(event_id)``,
``by_entity(entity)``. Persist/restore as JSON.
"""

from __future__ import annotations

import bisect
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from hlidskjalf.kista._compat import NotFoundError, ValidationError, get_logger, new_id

log = get_logger(__name__)


class Timeline:
    """Append-only, timestamp-ordered event timeline."""

    def __init__(self) -> None:
        self._events: List[Dict[str, Any]] = []
        #: Parallel sort keys (ts, seq) kept in the same order as
        #: ``_events`` so appends bisect into it directly instead of
        #: rebuilding the key list on every append (was O(n^2)).
        self._keys: List[tuple] = []
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._seq = 0

    def append(
        self,
        event_type: str,
        entity: str,
        payload: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> str:
        """Append an event; returns its id."""
        if not event_type:
            raise ValidationError("event_type must be non-empty")
        event = {
            "id": new_id(),
            "ts": float(ts) if ts is not None else time.time(),
            "type": event_type,
            "entity": entity,
            "payload": dict(payload or {}),
            "seq": self._seq,
        }
        self._seq += 1
        key = (event["ts"], event["seq"])
        pos = bisect.bisect_right(self._keys, key)
        self._keys.insert(pos, key)
        self._events.insert(pos, event)
        self._by_id[event["id"]] = event
        return event["id"]

    def get(self, event_id: str) -> Dict[str, Any]:
        if event_id not in self._by_id:
            raise NotFoundError(f"event not found: {event_id}")
        return self._by_id[event_id]

    def range_query(self, t0: float, t1: float) -> List[Dict[str, Any]]:
        """All events with t0 <= ts <= t1, in order."""
        if t0 > t1:
            raise ValidationError("t0 must be <= t1")
        return [e for e in self._events if t0 <= e["ts"] <= t1]

    def before(self, event_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Events strictly before the given event, newest first."""
        anchor = self.get(event_id)
        key = (anchor["ts"], anchor["seq"])
        earlier = [e for e in self._events if (e["ts"], e["seq"]) < key]
        earlier.reverse()
        return earlier if limit is None else earlier[:limit]

    def after(self, event_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Events strictly after the given event, oldest first."""
        anchor = self.get(event_id)
        key = (anchor["ts"], anchor["seq"])
        later = [e for e in self._events if (e["ts"], e["seq"]) > key]
        return later if limit is None else later[:limit]

    def by_entity(self, entity: str) -> List[Dict[str, Any]]:
        return [e for e in self._events if e["entity"] == entity]

    def by_type(self, event_type: str) -> List[Dict[str, Any]]:
        return [e for e in self._events if e["type"] == event_type]

    def head(self) -> Optional[Dict[str, Any]]:
        return self._events[-1] if self._events else None

    def __len__(self) -> int:
        return len(self._events)

    def all(self) -> List[Dict[str, Any]]:
        return list(self._events)

    # -- persist -------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"events": self._events, "seq": self._seq}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Timeline":
        tl = cls()
        for event in data.get("events", []):
            tl._events.append(dict(event))
            tl._by_id[event["id"]] = tl._events[-1]
        tl._seq = int(data.get("seq", len(tl._events)))
        tl._events.sort(key=lambda e: (e["ts"], e["seq"]))
        tl._keys = [(e["ts"], e["seq"]) for e in tl._events]
        return tl

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str) -> "Timeline":
        return cls.from_dict(json.loads(Path(path).read_text()))

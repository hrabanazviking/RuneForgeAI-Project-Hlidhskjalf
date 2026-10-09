"""Slice 19 — Verdandi timeline branching (what-if timelines).

A branch records a base event id on the main timeline plus its own
divergent events. ``merge`` replays branch events onto main; a
**conflict** is detected when, after the branch point, main *and* the
branch both carry events for the same entity — the branch event for that
entity is held back and reported instead of silently overwriting.

``branches`` maps name -> {"base_event_id", "events"}. Main is just the
underlying Timeline.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from hlidskjalf.kista._compat import NotFoundError, ValidationError, get_logger, new_id
from hlidskjalf.verdandi.timeline import Timeline

log = get_logger(__name__)


class BranchManager:
    """Branch/merge manager over a main Timeline."""

    def __init__(self, main: Optional[Timeline] = None) -> None:
        self.main = main if main is not None else Timeline()
        self.branches: Dict[str, Dict[str, Any]] = {}

    def branch(self, name: str, from_event: Optional[str] = None) -> str:
        """Create a branch at ``from_event`` (defaults to main head)."""
        if not name:
            raise ValidationError("branch name must be non-empty")
        if name in self.branches:
            raise ValidationError(f"branch already exists: {name}")
        base_id = from_event
        if base_id is None:
            head = self.main.head()
            if head is None:
                raise ValidationError("cannot branch an empty timeline without from_event")
            base_id = head["id"]
        else:
            self.main.get(base_id)  # validate existence
        self.branches[name] = {"base_event_id": base_id, "created_at": time.time(), "events": []}
        log.debug("verdandi.branch name=%s base=%s", name, base_id[:8])
        return name

    def append(self, branch: str, event_type: str, entity: str,
               payload: Optional[Dict[str, Any]] = None,
               ts: Optional[float] = None) -> str:
        """Append a divergent event to a branch (not to main)."""
        if branch not in self.branches:
            raise NotFoundError(f"branch not found: {branch}")
        event = {
            "id": new_id(),
            "ts": float(ts) if ts is not None else time.time(),
            "type": event_type,
            "entity": entity,
            "payload": dict(payload or {}),
            "branch": branch,
        }
        self.branches[branch]["events"].append(event)
        return event["id"]

    def branch_events(self, branch: str) -> List[Dict[str, Any]]:
        if branch not in self.branches:
            raise NotFoundError(f"branch not found: {branch}")
        return list(self.branches[branch]["events"])

    def drop(self, branch: str) -> None:
        if branch not in self.branches:
            raise NotFoundError(f"branch not found: {branch}")
        del self.branches[branch]

    def merge(self, branch: str) -> Dict[str, Any]:
        """Merge a branch into main.

        Events for entities untouched on main since the branch point are
        replayed onto main. Events for entities that *did* diverge on main
        are reported as conflicts and held back.
        """
        if branch not in self.branches:
            raise NotFoundError(f"branch not found: {branch}")
        info = self.branches[branch]
        base_id = info["base_event_id"]

        main_after = self.main.after(base_id)
        diverged_entities = {e["entity"] for e in main_after}

        merged: List[str] = []
        conflicts: List[Dict[str, Any]] = []
        for event in sorted(info["events"], key=lambda e: (e["ts"], e["id"])):
            if event["entity"] in diverged_entities:
                conflicts.append(
                    {
                        "entity": event["entity"],
                        "branch_event": event,
                        "main_events": [
                            e for e in main_after if e["entity"] == event["entity"]
                        ],
                    }
                )
                continue
            eid = self.main.append(
                event["type"], event["entity"], event["payload"], ts=event["ts"]
            )
            merged.append(eid)

        del self.branches[branch]
        result = {"branch": branch, "merged": merged, "conflicts": conflicts}
        log.info("verdandi.merge branch=%s merged=%d conflicts=%d", branch, len(merged), len(conflicts))
        return result

    def list_branches(self) -> List[str]:
        return sorted(self.branches)

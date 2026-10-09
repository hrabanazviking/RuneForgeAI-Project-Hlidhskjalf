"""Slice 20 — WYRD unified query API.

Single entry point over the causal graph + Verdandi timeline. Every
query is a plain dict; every result is JSON-able.

Supported ``q["kind"]`` values:

- ``{"kind": "node", "id": ...}`` — one node
- ``{"kind": "nodes", "type": ..., "attrs": {...}}`` — node search
- ``{"kind": "neighbors", "id": ..., "direction": "out"|"in"|"both"}``
- ``{"kind": "causes", "node": ..., "max_depth": 5}`` — upstream walk
- ``{"kind": "effects", "node": ..., "max_depth": 5}`` — downstream walk
- ``{"kind": "timeline", "t0": ..., "t1": ...}`` — temporal range
- ``{"kind": "events", "entity": ...}`` — events for an entity
- ``{"kind": "before", "event": ...}`` — what happened before X
"""

from __future__ import annotations

from typing import Any, Dict

from hlidskjalf.kista._compat import ValidationError, get_logger
from hlidskjalf.verdandi.timeline import Timeline
from hlidskjalf.wyrd import inference
from hlidskjalf.wyrd.graph import CausalGraph

log = get_logger(__name__)


def _ok(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "kind": kind, **payload}


def _err(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}


def query(
    graph: CausalGraph,
    timeline: Timeline,
    q: Dict[str, Any],
) -> Dict[str, Any]:
    """Run a unified query; always returns a JSON-able dict."""
    if not isinstance(q, dict) or "kind" not in q:
        return _err("query must be a dict with a 'kind' field")
    kind = q["kind"]
    try:
        if kind == "node":
            return _ok(kind, {"node": graph.get_node(q["id"])})
        if kind == "nodes":
            nodes = graph.find_nodes(q.get("type"), q.get("attrs"))
            return _ok(kind, {"nodes": nodes, "count": len(nodes)})
        if kind == "neighbors":
            direction = q.get("direction", "both")
            node = graph.get_node(q["id"])
            out = {"node": node}
            if direction in ("out", "both"):
                out["successors"] = graph.successors(q["id"])
            if direction in ("in", "both"):
                out["predecessors"] = graph.predecessors(q["id"])
            return _ok(kind, out)
        if kind == "causes":
            ranked = inference.causes_of(graph, q["node"], max_depth=int(q.get("max_depth", 5)))
            return _ok(
                kind,
                {"node": q["node"], "causes": [{"node": n, "score": s} for n, s in ranked]},
            )
        if kind == "effects":
            ranked = inference.effects_of(graph, q["node"], max_depth=int(q.get("max_depth", 5)))
            return _ok(
                kind,
                {"node": q["node"], "effects": [{"node": n, "score": s} for n, s in ranked]},
            )
        if kind == "timeline":
            events = timeline.range_query(float(q["t0"]), float(q["t1"]))
            return _ok(kind, {"events": events, "count": len(events)})
        if kind == "events":
            events = timeline.by_entity(q["entity"])
            return _ok(kind, {"entity": q["entity"], "events": events, "count": len(events)})
        if kind == "before":
            events = timeline.before(q["event"], limit=q.get("limit"))
            return _ok(kind, {"event": q["event"], "before": events, "count": len(events)})
        return _err(f"unknown query kind: {kind!r}")
    except (KeyError, TypeError, ValueError) as exc:
        return _err(f"invalid query: {exc}")
    except ValidationError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001 - API must not raise
        log.warning("wyrd.api.query failed: %s", exc)
        return _err(str(exc))

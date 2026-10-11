"""Slice 15 — WYRD causal world graph core.

Nodes are entities: ``{"id", "type", "attrs"}``. Edges are causal links:
``{"from", "to", "weight", "label"}``. Weight is a 0..1 causal strength.
The graph lives in memory and persists/restores as JSON.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from hlidskjalf.kista._compat import (
    NotFoundError,
    ValidationError,
    get_logger,
    new_id,
)

log = get_logger(__name__)


def _validate_weight(weight: Any) -> float:
    """A causal weight must be a real, finite number in [0, 1].

    ``bool`` is rejected explicitly: it is an ``int`` subclass, so
    ``weight=True`` used to slip through the range check as ``1.0``.
    """
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        raise ValidationError(
            f"weight must be a number in [0, 1], got {type(weight).__name__}"
        )
    if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
        raise ValidationError("weight must be in [0, 1]")
    return float(weight)


class CausalGraph:
    """In-memory causal graph with JSON persist/restore."""

    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []

    # -- nodes ---------------------------------------------------------
    def add_node(
        self,
        node_id: Optional[str] = None,
        node_type: str = "entity",
        attrs: Optional[Dict[str, Any]] = None,
    ) -> str:
        if node_id is not None and (
            not isinstance(node_id, str) or not node_id
        ):
            raise ValidationError("node_id must be a non-empty string")
        if attrs is not None and not isinstance(attrs, Mapping):
            raise ValidationError("attrs must be a mapping or None")
        nid = node_id or new_id()
        if not isinstance(node_type, str) or not node_type:
            raise ValidationError("node_type must be a non-empty string")
        self.nodes[nid] = {
            "id": nid,
            "type": node_type,
            "attrs": dict(attrs or {}),
            "created_at": self.nodes.get(nid, {}).get("created_at", time.time()),
        }
        return nid

    def get_node(self, node_id: str) -> Dict[str, Any]:
        if node_id not in self.nodes:
            raise NotFoundError(f"node not found: {node_id}")
        return self.nodes[node_id]

    def remove_node(self, node_id: str) -> None:
        self.get_node(node_id)
        del self.nodes[node_id]
        self.edges = [e for e in self.edges if e["from"] != node_id and e["to"] != node_id]

    def find_nodes(
        self, node_type: Optional[str] = None, attrs: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        out = []
        for node in self.nodes.values():
            if node_type and node["type"] != node_type:
                continue
            if attrs and not all(node["attrs"].get(k) == v for k, v in attrs.items()):
                continue
            out.append(node)
        return out

    # -- edges ---------------------------------------------------------
    def add_edge(
        self,
        from_id: str,
        to_id: str,
        weight: float = 1.0,
        label: str = "causes",
    ) -> Dict[str, Any]:
        if from_id not in self.nodes:
            raise NotFoundError(f"edge source not found: {from_id}")
        if to_id not in self.nodes:
            raise NotFoundError(f"edge target not found: {to_id}")
        weight = _validate_weight(weight)
        edge = {"from": from_id, "to": to_id, "weight": weight, "label": label}
        self.edges.append(edge)
        return edge

    def remove_edge(self, from_id: str, to_id: str, label: Optional[str] = None) -> int:
        before = len(self.edges)
        self.edges = [
            e
            for e in self.edges
            if not (
                e["from"] == from_id
                and e["to"] == to_id
                and (label is None or e["label"] == label)
            )
        ]
        return before - len(self.edges)

    def edges_from(self, node_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.edges if e["from"] == node_id]

    def edges_to(self, node_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.edges if e["to"] == node_id]

    def successors(self, node_id: str) -> List[Dict[str, Any]]:
        return [self.get_node(e["to"]) for e in self.edges_from(node_id)]

    def predecessors(self, node_id: str) -> List[Dict[str, Any]]:
        return [self.get_node(e["from"]) for e in self.edges_to(node_id)]

    # -- persist -------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": list(self.nodes.values()),
            "edges": list(self.edges),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CausalGraph":
        if not isinstance(data, Mapping):
            raise ValidationError("graph data must be a mapping")
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise ValidationError("graph 'nodes' and 'edges' must be lists")
        g = cls()
        for node in nodes:
            if not isinstance(node, Mapping):
                raise ValidationError("graph node entries must be mappings")
            nid = node.get("id")
            if not isinstance(nid, str) or not nid:
                raise ValidationError("graph node 'id' must be a non-empty string")
            if nid in g.nodes:
                raise ValidationError(f"duplicate node id in graph data: {nid}")
            g.nodes[nid] = dict(node)
        for edge in edges:
            if not isinstance(edge, Mapping):
                raise ValidationError("graph edge entries must be mappings")
            src, dst = edge.get("from"), edge.get("to")
            if src not in g.nodes or dst not in g.nodes:
                raise ValidationError(
                    f"graph edge references unknown node: {src!r} -> {dst!r}"
                )
            _validate_weight(edge.get("weight", 1.0))
            g.edges.append(dict(edge))
        return g

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))
        log.debug("wyrd.graph.save path=%s nodes=%d edges=%d", path, len(self.nodes), len(self.edges))

    @classmethod
    def load(cls, path: str) -> "CausalGraph":
        data = json.loads(Path(path).read_text())
        g = cls.from_dict(data)
        log.debug("wyrd.graph.load path=%s nodes=%d edges=%d", path, len(g.nodes), len(g.edges))
        return g

    def node_count(self) -> int:
        return len(self.nodes)

    def edge_count(self) -> int:
        return len(self.edges)

"""Slice 17 — WYRD world-state snapshots & diffs.

``snapshot`` freezes a graph into a plain JSON-able dict. ``diff(a, b)``
reports ``added_nodes``, ``removed_nodes``, ``changed_nodes`` (same id,
attrs differ), ``added_edges``, ``removed_edges`` — all as id lists /
edge dicts, readable for logs and HUDs.
"""

from __future__ import annotations

import copy
from typing import Dict, List

from hlidskjalf.kista._compat import get_logger
from hlidskjalf.wyrd.graph import CausalGraph

log = get_logger(__name__)


def snapshot(graph: CausalGraph) -> Dict:
    """Freeze the graph into a compact JSON-able dict."""
    return {
        "nodes": {n["id"]: {"type": n["type"], "attrs": copy.deepcopy(n["attrs"])} for n in graph.nodes.values()},
        "edges": [
            {"from": e["from"], "to": e["to"], "weight": e["weight"], "label": e["label"]}
            for e in graph.edges
        ],
    }


def _edge_key(edge: Dict) -> tuple:
    return (edge["from"], edge["to"], edge["label"], round(edge["weight"], 9))


def diff(a: Dict, b: Dict) -> Dict:
    """Diff snapshot ``a`` against snapshot ``b`` (a -> b).

    Added/removed are relative to the transition a -> b.
    """
    a_nodes, b_nodes = a.get("nodes", {}), b.get("nodes", {})
    a_edges = {_edge_key(e) for e in a.get("edges", [])}
    b_edges = {_edge_key(e) for e in b.get("edges", [])}

    added_nodes = [nid for nid in b_nodes if nid not in a_nodes]
    removed_nodes = [nid for nid in a_nodes if nid not in b_nodes]
    changed_nodes = [
        nid
        for nid in a_nodes
        if nid in b_nodes and a_nodes[nid] != b_nodes[nid]
    ]

    edge_by_key_b = {_edge_key(e): e for e in b.get("edges", [])}
    edge_by_key_a = {_edge_key(e): e for e in a.get("edges", [])}

    result = {
        "added_nodes": sorted(added_nodes),
        "removed_nodes": sorted(removed_nodes),
        "changed_nodes": sorted(changed_nodes),
        "added_edges": [edge_by_key_b[k] for k in sorted(b_edges - a_edges)],
        "removed_edges": [edge_by_key_a[k] for k in sorted(a_edges - b_edges)],
    }
    log.debug(
        "wyrd.snapshot.diff +n=%d -n=%d ~n=%d +e=%d -e=%d",
        len(result["added_nodes"]),
        len(result["removed_nodes"]),
        len(result["changed_nodes"]),
        len(result["added_edges"]),
        len(result["removed_edges"]),
    )
    return result


def apply_diff(graph: CausalGraph, d: Dict) -> None:
    """Apply a diff (as produced by ``diff``) onto a graph.

    Note: ``changed_nodes`` only carries ids — the caller must resolve full
    node state separately; this helper adds/removes nodes by id and
    adds/removes edges.
    """
    for nid in d.get("removed_nodes", []):
        if nid in graph.nodes:
            graph.remove_node(nid)
    for edge in d.get("removed_edges", []):
        graph.remove_edge(edge["from"], edge["to"], edge["label"])
    for nid in d.get("added_nodes", []):
        if nid not in graph.nodes:
            graph.add_node(node_id=nid)
    for edge in d.get("added_edges", []):
        if edge["from"] in graph.nodes and edge["to"] in graph.nodes:
            graph.add_edge(edge["from"], edge["to"], edge["weight"], edge["label"])

"""Slice 16 — WYRD causal inference: "what causes X?"

``causes_of`` walks upstream edges (effects <- causes) from a target node,
propagating score = parent_score * edge_weight * decay per hop. When
several paths reach the same node its strongest score wins. Cycles are cut
by a visited set; results are ranked strongest-first.

Returns ``[(node_dict, score)]`` sorted by descending score.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Tuple

from hlidskjalf.kista._compat import NotFoundError, ValidationError, get_logger
from hlidskjalf.wyrd.graph import CausalGraph

log = get_logger(__name__)


def _validate_walk(max_depth: int, decay: float) -> None:
    """Shared argument validation for both walk directions.

    ``effects_of`` used to skip the decay check entirely, so decay=0 or
    NaN silently produced empty/garbage rankings while ``causes_of``
    raised. ``bool`` is rejected for both arguments (int subclass).
    """
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1:
        raise ValidationError("max_depth must be an integer >= 1")
    if (
        isinstance(decay, bool)
        or not isinstance(decay, (int, float))
        or not math.isfinite(decay)
        or not 0.0 < decay <= 1.0
    ):
        raise ValidationError("decay must be in (0, 1]")


def causes_of(
    graph: CausalGraph,
    node_id: str,
    max_depth: int = 5,
    decay: float = 0.7,
) -> List[Tuple[Dict, float]]:
    """Ranked upstream cause chain for ``node_id``."""
    graph.get_node(node_id)  # validate target exists
    _validate_walk(max_depth, decay)

    best: Dict[str, float] = {}
    queue: deque[Tuple[str, float, int]] = deque([(node_id, 1.0, 0)])
    visited: Dict[Tuple[str, int], float] = {}

    while queue:
        current, score, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in graph.edges_to(current):
            cause = edge["from"]
            step_score = score * edge["weight"] * decay
            key = (cause, depth + 1)
            if step_score <= visited.get(key, 0.0):
                continue
            visited[key] = step_score
            if step_score > best.get(cause, 0.0):
                best[cause] = step_score
            queue.append((cause, step_score, depth + 1))

    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    return [(graph.get_node(nid), s) for nid, s in ranked]


def effects_of(
    graph: CausalGraph,
    node_id: str,
    max_depth: int = 5,
    decay: float = 0.7,
) -> List[Tuple[Dict, float]]:
    """Ranked downstream effects of ``node_id`` (forward walk)."""
    graph.get_node(node_id)
    _validate_walk(max_depth, decay)

    best: Dict[str, float] = {}
    queue: deque[Tuple[str, float, int]] = deque([(node_id, 1.0, 0)])
    visited: Dict[Tuple[str, int], float] = {}

    while queue:
        current, score, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in graph.edges_from(current):
            effect = edge["to"]
            step_score = score * edge["weight"] * decay
            key = (effect, depth + 1)
            if step_score <= visited.get(key, 0.0):
                continue
            visited[key] = step_score
            if step_score > best.get(effect, 0.0):
                best[effect] = step_score
            queue.append((effect, step_score, depth + 1))

    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    return [(graph.get_node(nid), s) for nid, s in ranked]

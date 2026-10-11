"""Dawn forge 2026-10-11 — Track A: wyrd graph / inference / api."""

import pytest

from hlidskjalf.common.errors import ValidationError
from hlidskjalf.verdandi.timeline import Timeline
from hlidskjalf.wyrd import inference
from hlidskjalf.wyrd.api import query
from hlidskjalf.wyrd.graph import CausalGraph


def _graph():
    g = CausalGraph()
    a = g.add_node(node_id="a")
    b = g.add_node(node_id="b")
    return g, a, b


# A1 — add_edge weight validation
@pytest.mark.parametrize("bad", [True, False, "x", None, float("nan"), float("inf"), -0.1, 1.1])
def test_a1_add_edge_rejects_bad_weight(bad):
    g, a, b = _graph()
    with pytest.raises(ValidationError):
        g.add_edge(a, b, weight=bad)


def test_a1_add_edge_accepts_valid_weights():
    g, a, b = _graph()
    assert g.add_edge(a, b, weight=0)["weight"] == 0.0
    assert g.add_edge(a, b, weight=1)["weight"] == 1.0


# A2 — add_node validation
@pytest.mark.parametrize(
    "kwargs",
    [
        {"node_id": 123},
        {"node_id": ""},
        {"node_id": "x", "node_type": ""},
        {"node_id": "x", "node_type": 7},
        {"node_id": "x", "attrs": [1, 2]},
        {"node_id": "x", "attrs": "nope"},
    ],
)
def test_a2_add_node_rejects_bad_inputs(kwargs):
    g = CausalGraph()
    with pytest.raises(ValidationError):
        g.add_node(**kwargs)


def test_a2_add_node_valid_still_works():
    g = CausalGraph()
    nid = g.add_node(node_id="n1", node_type="event", attrs={"k": 1})
    assert g.get_node(nid)["attrs"] == {"k": 1}


# A3 — from_dict validation
@pytest.mark.parametrize(
    "data",
    [
        "not-a-dict",
        {"nodes": "nope"},
        {"nodes": [{"type": "entity"}]},  # missing id
        {"nodes": [{"id": "x"}, {"id": "x"}]},  # duplicate id
        {"nodes": [{"id": "x"}], "edges": [{"from": "x", "to": "ghost", "weight": 1.0, "label": "c"}]},
        {"nodes": [{"id": "x"}, {"id": "y"}],
         "edges": [{"from": "x", "to": "y", "weight": True, "label": "c"}]},
        {"nodes": [{"id": "x"}], "edges": ["nope"]},
    ],
)
def test_a3_from_dict_rejects_corrupt(data):
    with pytest.raises(ValidationError):
        CausalGraph.from_dict(data)


def test_a3_from_dict_round_trip():
    g, a, b = _graph()
    g.add_edge(a, b, weight=0.5)
    g2 = CausalGraph.from_dict(g.to_dict())
    assert g2.node_count() == 2 and g2.edge_count() == 1


# A4 — inference parity
def test_a4_effects_of_rejects_bad_decay():
    g, a, b = _graph()
    g.add_edge(a, b)
    for bad in (0, -0.5, 1.5, float("nan"), float("inf"), True, "x"):
        with pytest.raises(ValidationError):
            inference.effects_of(g, a, decay=bad)


def test_a4_both_walks_reject_bad_max_depth():
    g, a, b = _graph()
    g.add_edge(a, b)
    for bad in (0, -1, True, 2.5, "3"):
        with pytest.raises(ValidationError):
            inference.causes_of(g, b, max_depth=bad)
        with pytest.raises(ValidationError):
            inference.effects_of(g, a, max_depth=bad)


def test_a4_valid_walks_unchanged():
    g, a, b = _graph()
    g.add_edge(a, b, weight=0.5)
    effects = inference.effects_of(g, a)
    assert [n["id"] for n, _ in effects] == ["b"]
    causes = inference.causes_of(g, b)
    assert [n["id"] for n, _ in causes] == ["a"]


# A5 — api strictness
def test_a5_neighbors_bad_direction_is_error():
    g, a, _ = _graph()
    res = query(g, Timeline(), {"kind": "neighbors", "id": a, "direction": "sideways"})
    assert res["ok"] is False


def test_a5_neighbors_valid_directions():
    g, a, b = _graph()
    g.add_edge(a, b)
    res = query(g, Timeline(), {"kind": "neighbors", "id": a, "direction": "out"})
    assert res["ok"] is True and [n["id"] for n in res["successors"]] == ["b"]


@pytest.mark.parametrize("bad", [True, "5", 2.5, 0])
def test_a5_api_max_depth_not_coerced(bad):
    g, a, b = _graph()
    g.add_edge(a, b)
    res = query(g, Timeline(), {"kind": "causes", "node": b, "max_depth": bad})
    assert res["ok"] is False


def test_a5_api_valid_causes_query():
    g, a, b = _graph()
    g.add_edge(a, b)
    res = query(g, Timeline(), {"kind": "causes", "node": b, "max_depth": 3})
    assert res["ok"] is True and res["causes"][0]["node"]["id"] == "a"

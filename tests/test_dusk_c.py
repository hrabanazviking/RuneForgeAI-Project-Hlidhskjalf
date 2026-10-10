"""Forge Worker C — Hlidhskjalf dusk run regression tests.

C1: NaN/inf outcomes rejected at the ensemble boundary (seidr/montecarlo).
C2: tick(NaN)/tick(inf) raise instead of silently poisoning the clock (autonomy/loops).
C3: Timeline.append no longer O(n^2) (verdandi/timeline).
"""

from __future__ import annotations

import gc
import math
import random
import time

import pytest

from hlidskjalf.autonomy.loops import AutonomousLoops, LoopSpec
from hlidskjalf.seidr.montecarlo import run_ensemble
from hlidskjalf.seidr.simulator import Scenario
from hlidskjalf.verdandi.timeline import Timeline


# ---------------------------------------------------------------------------
# C1 — NaN/inf outcomes


def _counter(state, rng):
    return {"n": state["n"] + 1}


def _scenario():
    return Scenario(name="counter", steps=5, initial_state={"n": 0},
                    transition_fn=_counter)


def _nan_outcome(trace):
    return float("nan")


def _inf_outcome(trace):
    return float("inf")


def _final_n(trace):
    return trace.final_state["n"]


def test_c1_nan_outcome_raises_with_histogram_on():
    with pytest.raises(ValueError, match="finite"):
        run_ensemble(_scenario(), n=10, seed=1, outcome_fn=_nan_outcome,
                     histogram_bins=10)


def test_c1_nan_outcome_raises_with_histogram_off():
    # histogram_bins=0 used to let NaN slip through with silently-NaN stats.
    with pytest.raises(ValueError, match="finite"):
        run_ensemble(_scenario(), n=10, seed=1, outcome_fn=_nan_outcome,
                     histogram_bins=0)


def test_c1_inf_outcome_raises():
    with pytest.raises(ValueError, match="finite"):
        run_ensemble(_scenario(), n=10, seed=1, outcome_fn=_inf_outcome)


def test_c1_normal_float_outcomes_still_work():
    res = run_ensemble(_scenario(), n=10, seed=1, outcome_fn=_final_n)
    assert res.outcomes == [5.0] * 10
    assert res.mean == 5.0
    assert res.variance == 0.0
    assert math.isfinite(res.mean) and math.isfinite(res.variance)
    assert len(res.histogram) > 0


# ---------------------------------------------------------------------------
# C2 — tick(NaN)/tick(inf)


def test_c2_tick_nan_raises():
    loops = AutonomousLoops()
    with pytest.raises(ValueError, match="finite"):
        loops.tick(float("nan"))
    assert loops.now == 0.0  # clock untouched


def test_c2_tick_inf_raises():
    loops = AutonomousLoops()
    with pytest.raises(ValueError, match="finite"):
        loops.tick(float("inf"))
    assert loops.now == 0.0


def test_c2_tick_negative_still_raises():
    loops = AutonomousLoops()
    with pytest.raises(ValueError, match=">= 0"):
        loops.tick(-1.0)


def test_c2_scheduler_not_poisoned_after_bad_tick():
    loops = AutonomousLoops()
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            loops.tick(bad)
    ran = loops.tick(3600.0)
    assert math.isfinite(loops.now)
    assert loops.now == 3600.0
    # both built-ins become due at t=3600 and actually run
    assert "memory_consolidation" in ran
    assert "simulation_tick" in ran
    assert loops.get("memory_consolidation").run_count == 1


def test_c2_nonfinite_start_rejected():
    with pytest.raises(ValueError, match="finite"):
        AutonomousLoops(start=float("nan"))


def test_c2_nan_interval_rejected():
    with pytest.raises(ValueError, match="finite"):
        LoopSpec("bad", float("nan"), lambda l, s: None)


# ---------------------------------------------------------------------------
# C3 — Timeline.append scaling


def test_c3_append_ordering_matches_sort_reference():
    # Random order with heavy ts ties; sort-based reference is total
    # because (ts, seq) keys are unique per event.
    rng = random.Random(20261010)
    tl = Timeline()
    inserted = []
    n = 2000
    order = list(range(n))
    rng.shuffle(order)
    for i in order:
        ts = float(rng.choice([1.0, 1.0, 2.0, 3.0, 3.0, 3.0])) + rng.random()
        eid = tl.append("evt", "ent", ts=ts)
        inserted.append(tl.get(eid))
    reference = sorted(inserted, key=lambda e: (e["ts"], e["seq"]))
    got = tl.all()
    assert [e["id"] for e in got] == [e["id"] for e in reference]
    # parallel key list stays in lockstep with the event list
    assert tl._keys == [(e["ts"], e["seq"]) for e in got]
    assert all(got[i]["ts"] <= got[i + 1]["ts"] for i in range(n - 1))


def _bench_appends(n, ts_pool):
    tl = Timeline()
    for _ in range(500):  # warmup: caches, allocator, page faults
        tl.append("w", "w", ts=0.0)
    gc.collect()
    gc.disable()
    try:
        t0 = time.perf_counter()
        for i in range(n):
            tl.append("e", "x", ts=ts_pool[i])
        return time.perf_counter() - t0
    finally:
        gc.enable()


def test_c3_append_growth_is_subquadratic():
    # Old code: 2000 -> 160ms, 8000 -> 4363ms (ratio ~27).
    # Fixed code on this machine: ~15ms / ~72ms (ratio ~5).
    # Generous bounds: absolute ceiling far below the old 4.4s, and a
    # ratio ceiling far below the old ~27x. Median of 3 runs to be
    # robust against one noisy sample.
    rng = random.Random(99)
    pool = [rng.random() * 1000.0 for _ in range(8000)]

    def median3(n, sub):
        return sorted(_bench_appends(n, sub) for _ in range(3))[1]

    t_small = median3(2000, pool[:2000])
    t_large = median3(8000, pool)
    assert t_small > 0, "timing resolution too coarse"
    ratio = t_large / t_small
    assert t_large < 1.5, f"8000 appends took {t_large:.2f}s (old code: 4.36s)"
    assert ratio < 10, f"growth ratio {ratio:.1f}x not sub-quadratic (old: ~27x)"


def test_c3_from_dict_keeps_key_index_in_sync():
    tl = Timeline()
    tl.append("a", "x", ts=3.0)
    tl.append("b", "x", ts=1.0)
    tl.append("c", "y", ts=2.0)
    tl2 = Timeline.from_dict(tl.to_dict())
    assert [e["id"] for e in tl2.all()] == [e["id"] for e in tl.all()]
    assert tl2._keys == [(e["ts"], e["seq"]) for e in tl2.all()]
    # appends after restore still land in the right spot
    tl2.append("d", "z", ts=1.5)
    ts_list = [e["ts"] for e in tl2.all()]
    assert ts_list == sorted(ts_list)
    assert tl2._keys == [(e["ts"], e["seq"]) for e in tl2.all()]

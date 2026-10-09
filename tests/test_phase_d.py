"""Tests for Phase D — Seidr simulator/Monte Carlo + Draupnir forge stack.

All subprocess/multiprocessing timeouts are kept short so the suite runs
fast. Worker callables are top-level so the "spawn" context can pickle them.
"""

from __future__ import annotations

import os
import time

import pytest

from hlidskjalf.draupnir.aggregate import aggregate, filter_results, summarize
from hlidskjalf.draupnir.coder import CodeSpec, CodegenError, MythicCoder
from hlidskjalf.draupnir.exec import BlockedCodeError, prescan, run_python
from hlidskjalf.draupnir.forge import ConcurrencyLimitError, Forge, TaskSpec
from hlidskjalf.draupnir.lifecycle import Supervisor
from hlidskjalf.seidr.montecarlo import run_ensemble
from hlidskjalf.seidr.simulator import Scenario, run


# ---------------------------------------------------------------------------
# Simulator helpers (pure functions — spawn-safe and picklable)


def _random_walk(state, rng):
    x = state["x"] + (1 if rng.random() < 0.5 else -1)
    return {"x": x}


def _counter(state, rng):
    return {"n": state["n"] + 1}


def _final_x(trace):
    return trace.final_state["x"]


def _final_n(trace):
    return trace.final_state["n"]


# ---------------------------------------------------------------------------
# Slice 21 — Seidr simulator


def test_simulator_runs_all_steps():
    sc = Scenario(name="walk", steps=10, initial_state={"x": 0},
                  transition_fn=_random_walk)
    trace = run(sc, seed=42)
    assert trace.scenario_name == "walk"
    assert trace.seed == 42
    assert trace.step_count == 11  # initial + 10 steps
    assert isinstance(trace.duration_s, float)


def test_simulator_deterministic_with_seed():
    sc = Scenario(name="walk", steps=50, initial_state={"x": 0},
                  transition_fn=_random_walk)
    a = run(sc, seed=7)
    b = run(sc, seed=7)
    assert [s["x"] for s in a.states] == [s["x"] for s in b.states]


def test_simulator_different_seeds_diverge():
    sc = Scenario(name="walk", steps=50, initial_state={"x": 0},
                  transition_fn=_random_walk)
    a = run(sc, seed=1)
    b = run(sc, seed=2)
    assert [s["x"] for s in a.states] != [s["x"] for s in b.states]


def test_simulator_stop_fn_halts_early():
    sc = Scenario(name="counter", steps=100, initial_state={"n": 0},
                  transition_fn=_counter, stop_fn=lambda s: s["n"] >= 3)
    trace = run(sc, seed=0)
    assert trace.final_state == {"n": 3}
    assert trace.step_count == 4


def test_simulator_bad_steps_rejected():
    with pytest.raises(ValueError):
        Scenario(name="bad", steps=-1, initial_state={},
                 transition_fn=_counter)


# ---------------------------------------------------------------------------
# Slice 22 — Monte Carlo


def test_ensemble_statistics_sane():
    sc = Scenario(name="walk", steps=100, initial_state={"x": 0},
                  transition_fn=_random_walk)
    res = run_ensemble(sc, n=60, seed=1234, outcome_fn=_final_x)
    assert res.n == 60
    assert res.mean == pytest.approx(sum(res.outcomes) / 60)
    assert res.variance >= 0.0
    assert res.min == min(res.outcomes)
    assert res.max == max(res.outcomes)
    assert sum(bucket[2] for bucket in res.histogram) == 60
    assert sum(count for _, count in res.ranked) == 60


def test_ensemble_deterministic():
    sc = Scenario(name="walk", steps=40, initial_state={"x": 0},
                  transition_fn=_random_walk)
    kw = dict(n=25, seed=99, outcome_fn=_final_x)
    assert run_ensemble(sc, **kw).outcomes == run_ensemble(sc, **kw).outcomes


def test_ensemble_to_dict():
    sc = Scenario(name="counter", steps=5, initial_state={"n": 0},
                  transition_fn=_counter)
    d = run_ensemble(sc, n=10, seed=0, outcome_fn=_final_n).to_dict()
    assert d["mean"] == 5.0 and d["variance"] == 0.0
    assert d["ranked"] == [{"value": 5.0, "count": 10}]


def test_ensemble_rejects_bad_n():
    sc = Scenario(name="x", steps=1, initial_state={}, transition_fn=_counter)
    with pytest.raises(ValueError):
        run_ensemble(sc, n=0, seed=0, outcome_fn=_final_n)


# ---------------------------------------------------------------------------
# Slice 23 — forge


def _tiny_task(value):
    return value * 2


def _slow_task():
    time.sleep(5)
    return "done"


def test_forge_spawn_and_result():
    forge = Forge(max_concurrency=2)
    try:
        aid = forge.spawn(TaskSpec(name="double", fn=_tiny_task, args=(21,)))
        assert aid.startswith("agent-")
        item = forge.result_queue.get(timeout=10)
        assert item["agent_id"] == aid and item["ok"] is True
        assert item["output"] == 42
    finally:
        forge.shutdown()


def test_forge_heartbeat_tracked():
    forge = Forge(max_concurrency=2, heartbeat_interval=0.05)
    try:
        aid = forge.spawn(TaskSpec(name="slow", fn=_slow_task))
        deadline = time.monotonic() + 10
        age = None
        while time.monotonic() < deadline:
            age = forge.get_heartbeat(aid)
            if age is not None:
                break
            time.sleep(0.1)
        assert age is not None and age < 2.0
        assert forge.is_alive(aid)
    finally:
        forge.shutdown()


def test_forge_concurrency_cap():
    forge = Forge(max_concurrency=1)
    try:
        forge.spawn(TaskSpec(name="a", fn=_slow_task))
        with pytest.raises(ConcurrencyLimitError):
            forge.spawn(TaskSpec(name="b", fn=_slow_task))
    finally:
        forge.shutdown()


def test_forge_kill_agent():
    forge = Forge(max_concurrency=2)
    try:
        aid = forge.spawn(TaskSpec(name="slow", fn=_slow_task))
        assert forge.kill_agent(aid) is True
        assert not forge.is_alive(aid)
    finally:
        forge.shutdown()


def test_forge_list_agents():
    forge = Forge(max_concurrency=2)
    try:
        aid = forge.spawn(TaskSpec(name="double", fn=_tiny_task, args=(1,)))
        names = [a["agent_id"] for a in forge.list_agents()]
        assert aid in names
    finally:
        forge.shutdown()


# ---------------------------------------------------------------------------
# Slice 24 — lifecycle


def _crash_task():
    raise SystemExit(3)  # hard exit, no result posted -> "crashed"


def _boom_task():
    raise RuntimeError("kaboom")


def test_supervisor_collects_results():
    forge = Forge(max_concurrency=2)
    sup = Supervisor(forge)
    try:
        aid = sup.submit(TaskSpec(name="double", fn=_tiny_task, args=(21,)))
        sup.spin(6)
        agent = sup.get(aid)
        assert agent is not None
        assert agent.status == "finished"
        assert agent.result["output"] == 42
    finally:
        sup.shutdown()


def test_supervisor_timeout_kills():
    forge = Forge(max_concurrency=2)
    sup = Supervisor(forge)
    try:
        aid = sup.submit(TaskSpec(name="slow", fn=_slow_task, timeout=0.5))
        sup.spin(4)
        agent = sup.get(aid)
        assert agent.status == "timeout"
        assert agent.result["ok"] is False
        assert "timeout" in agent.result["error"]
    finally:
        sup.shutdown()


def test_supervisor_bounded_crash_restart():
    forge = Forge(max_concurrency=2)
    sup = Supervisor(forge, max_restarts=2)
    try:
        aid = sup.submit(TaskSpec(name="crasher", fn=_crash_task, timeout=5))
        sup.spin(13)
        records = sup.list_agents()
        assert len(records) == 1
        final = records[0]
        assert final["restarts"] == 2  # bounded: never exceeded max_restarts
        assert final["status"] == "failed"
        assert len(final["history"]) == 3  # original + 2 restarts
    finally:
        sup.shutdown()


def test_supervisor_exception_result_is_failure():
    forge = Forge(max_concurrency=2)
    sup = Supervisor(forge, max_restarts=1)
    try:
        sup.submit(TaskSpec(name="boomer", fn=_boom_task, timeout=5))
        sup.spin(8)
        records = sup.list_agents()
        assert len(records) == 1
        assert records[0]["restarts"] <= 1
    finally:
        sup.shutdown()


def test_supervisor_kill_agent():
    forge = Forge(max_concurrency=2)
    sup = Supervisor(forge)
    try:
        aid = sup.submit(TaskSpec(name="slow", fn=_slow_task, timeout=30))
        assert sup.kill_agent(aid) is True
        assert sup.get(aid).status == "killed"
    finally:
        sup.shutdown()


# ---------------------------------------------------------------------------
# Slice 25 — coder


def test_coder_writes_and_validates(tmp_path):
    coder = MythicCoder()
    spec = CodeSpec(
        name="hello",
        files={
            "main.py": "def greet():\n    return 'hail'\n",
            "pkg/util.py": "X = 1\n",
            "notes.txt": "not python\n",
        },
    )
    written = coder.generate_code(spec, tmp_path)
    assert written == ["main.py", "notes.txt", "pkg/util.py"]
    assert (tmp_path / "pkg" / "util.py").read_text() == "X = 1\n"


def test_coder_rejects_bad_syntax(tmp_path):
    coder = MythicCoder()
    spec = CodeSpec(name="bad", files={"bad.py": "def broken(:\n"})
    with pytest.raises(CodegenError):
        coder.generate_code(spec, tmp_path)


def test_coder_rejects_path_escape(tmp_path):
    coder = MythicCoder()
    spec = CodeSpec(name="evil", files={"../escape.py": "X = 1\n"})
    with pytest.raises(CodegenError):
        coder.generate_code(spec, tmp_path)


def test_coder_generator_hook(tmp_path):
    def gen(name, context):
        return f"# {context['who']} forged\nVALUE = {context['v']!r}\n"

    coder = MythicCoder(generator=gen)
    spec = CodeSpec(
        name="gen",
        file_names=["spell.py"],
        context={"who": "draupnir", "v": 9},
    )
    written = coder.generate_code(spec, tmp_path / "ws")
    assert written == ["spell.py"]
    assert "draupnir forged" in (tmp_path / "ws" / "spell.py").read_text()


def test_coder_needs_generator_but_has_none(tmp_path):
    coder = MythicCoder()
    spec = CodeSpec(name="gen", file_names=["x.py"])
    with pytest.raises(CodegenError):
        coder.generate_code(spec, tmp_path)


# ---------------------------------------------------------------------------
# Slice 26 — exec


def test_exec_runs_safe_code():
    res = run_python("print('hail odin')\nprint(6 * 7)")
    assert res.ok
    assert res.stdout == "hail odin\n42\n"
    assert res.returncode == 0 and not res.timed_out


def test_exec_captures_stderr_and_returncode():
    res = run_python("import sys\nsys.stderr.write('oops\\n')\nraise ValueError('x')")
    assert not res.ok
    assert res.returncode != 0
    assert "oops" in res.stderr


def test_exec_blocks_socket_import():
    with pytest.raises(BlockedCodeError):
        run_python("import socket\nprint('nope')")


def test_exec_blocks_os_system():
    with pytest.raises(BlockedCodeError):
        run_python("import os\nos.system('echo pwned')")


def test_exec_blocked_result_without_raise():
    res = run_python("import subprocess", raise_on_block=False)
    assert res.blocked and not res.ok
    assert "subprocess" in res.blocked_reason


def test_exec_prescan_api():
    assert prescan("import socket") is not None
    assert prescan("print('safe')") is None


def test_exec_timeout():
    res = run_python("import time\ntime.sleep(10)", timeout=0.5)
    assert res.timed_out


def test_exec_rlimits_applied_or_documented():
    res = run_python("print('x')")
    # POSIX: applied; non-POSIX (no resource module): documented flag
    assert isinstance(res.rlimits_applied, bool)


# ---------------------------------------------------------------------------
# Slice 27 — aggregate


def _mk(agent_id, ok, output=None, error=None):
    return {"agent_id": agent_id, "ok": ok, "output": output, "error": error}


def test_aggregate_concat():
    report = aggregate([
        _mk("a1", True, output="one"),
        _mk("a2", True, output="two"),
        _mk("a3", False, error="boom"),
    ])
    assert report["total"] == 3
    assert report["succeeded"] == 2 and report["failed"] == 1
    assert report["merged_output"] == "one\ntwo"
    assert report["errors"] == [{"agent_id": "a3", "error": "boom"}]
    assert summarize(report) == "2/3 agents succeeded, 1 failed"


def test_aggregate_extend_strategy():
    report = aggregate(
        [_mk("a1", True, output=[1, 2]), _mk("a2", True, output=[3])],
        strategy="extend",
    )
    assert report["merged_output"] == [1, 2, 3]


def test_aggregate_merge_dicts_strategy():
    report = aggregate(
        [_mk("a1", True, output={"x": 1}), _mk("a2", True, output={"y": 2})],
        strategy="merge_dicts",
    )
    assert report["merged_output"] == {"x": 1, "y": 2}


def test_aggregate_empty_and_all_failed():
    report = aggregate([])
    assert report["total"] == 0 and report["merged_output"] == ""
    report = aggregate([_mk("a1", False, error="e1"), _mk("a2", False, error="e2")])
    assert report["succeeded"] == 0 and len(report["errors"]) == 2


def test_aggregate_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        aggregate([_mk("a1", True, output="x")], strategy="nope")


def test_filter_results():
    rows = [_mk("a", True, "x"), _mk("b", False, error="e")]
    assert [r["agent_id"] for r in filter_results(rows, ok=True)] == ["a"]
    assert [r["agent_id"] for r in filter_results(rows, ok=False)] == ["b"]
    assert len(filter_results(rows)) == 2

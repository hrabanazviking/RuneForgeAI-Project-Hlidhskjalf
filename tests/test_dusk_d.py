"""Forge Worker D — Hlidhskjalf dusk run (slices D1–D6).

Regression tests, one section per slice.  Every test proves its fix with a
real assertion; nothing here is a placeholder.
"""
from __future__ import annotations

import itertools
import math
import random
import time

import pytest

# --- D1: embeddings -------------------------------------------------------
from hlidskjalf.hailo.embeddings import batch_embed, embed

# --- D2: forge ------------------------------------------------------------
from hlidskjalf.draupnir.forge import Forge, TaskSpec

# --- D3: exec prescan -----------------------------------------------------
from hlidskjalf.draupnir.exec import prescan

# --- D4: compositor + dice ------------------------------------------------
from hlidskjalf.himinbjorg.compositor import Compositor, HeadlessCanvas, Rect
from hlidskjalf.himinbjorg.viewports.dice import (
    DiceViewport,
    _distribution_cached,
    distribution,
)

# --- D5: viewport data contracts ------------------------------------------
from hlidskjalf.himinbjorg.viewports.divination import DivinationViewport
from hlidskjalf.himinbjorg.viewports.vitals import VitalsViewport
from hlidskjalf.host import divination as divination_adapter
from hlidskjalf.host.sagnaskemma import PartyMember as SagnaMember
from hlidskjalf.host.sagnaskemma import PartyState

# --- D6: scheduler + tts + mcp --------------------------------------------
from hlidskjalf.hailo import tts as tts_module
from hlidskjalf.hailo.scheduler import AGING_STEP, QUEUED, Job, NeuralScheduler
from hlidskjalf.host.mcp import MockMCPServer, make_pipe_pair


# ===========================================================================
# D1 — embed(text, dim) for any positive dim
# ===========================================================================

class TestD1EmbedDims:
    @pytest.mark.parametrize("dim", [1, 16, 64, 128, 256])
    def test_embed_any_dim(self, dim):
        vec = embed("the quick brown fox jumps over the lazy dog", dim=dim)
        assert len(vec) == dim
        assert math.sqrt(sum(x * x for x in vec)) == pytest.approx(1.0)

    def test_embed_dim_1_no_crash(self):
        assert len(embed("odinn", dim=1)) == 1

    def test_batch_embed_dim(self):
        vecs = batch_embed(["a", "b", "c"], dim=16)
        assert all(len(v) == 16 for v in vecs)

    def test_embed_deterministic(self):
        assert embed("yrsa", dim=64) == embed("yrsa", dim=64)


# ===========================================================================
# D2 — forge zombie workers (blocking put on a full result queue)
# ===========================================================================

def _instant_task():
    return 42


class TestD2ForgeZombies:
    def test_no_zombie_workers_when_result_queue_full(self):
        # Standalone Forge: nobody drains the result queue.  Pre-acquire the
        # queue's backing semaphore to simulate "full" deterministically —
        # with the old blocking put() every worker would wedge here forever
        # as a fresh-heartbeat "running" zombie.
        forge = Forge(max_concurrency=3, heartbeat_interval=0.05,
                      result_queue_size=2)
        try:
            sem = forge._result_queue._sem
            sem.acquire()
            sem.acquire()
            try:
                ids = [forge.spawn(TaskSpec(name=f"t{i}", fn=_instant_task))
                       for i in range(3)]
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    statuses = [r["status"] for r in forge.list_agents()]
                    if len(statuses) == 3 and all(
                        s in ("finished", "crashed") for s in statuses
                    ):
                        break
                    time.sleep(0.05)
                agents = forge.list_agents()
                assert len(agents) == 3
                assert all(
                    r["status"] in ("finished", "crashed") for r in agents
                ), f"zombie workers stuck: {agents}"
                # No fresh-heartbeat "running" ghosts.
                assert all(forge.get_heartbeat(a) is None for a in ids)
                # Every result accounted for: drained + dropped == spawned.
                drained = forge.drain_results()
                assert len(drained) + forge.dropped_results == 3
                assert forge.dropped_results == 3
            finally:
                sem.release()
                sem.release()
        finally:
            forge.shutdown()

    def test_drain_results_happy_path(self):
        forge = Forge(max_concurrency=2, heartbeat_interval=0.05)
        try:
            aid = forge.spawn(TaskSpec(name="double", fn=_instant_task))
            deadline = time.monotonic() + 15
            got = []
            while time.monotonic() < deadline and not got:
                got = forge.drain_results()
                time.sleep(0.05)
            assert len(got) == 1
            assert got[0]["agent_id"] == aid and got[0]["ok"] is True
            assert got[0]["output"] == 42
            assert forge.drain_results() == []  # idempotent when empty
        finally:
            forge.shutdown()


# ===========================================================================
# D3 — exec prescan: bare builtin calls
# ===========================================================================

class TestD3ExecPrescanBareBuiltins:
    @pytest.mark.parametrize("code", [
        'eval("1+1")',
        'exec("x = 1")',
        'compile("1+1", "<s>", "eval")',
        'open("/etc/passwd").read()',
        '__import__("os")',
    ])
    def test_bare_builtin_calls_blocked(self, code):
        assert prescan(code) is not None, f"{code!r} passed the scan"

    def test_block_reason_names_builtin(self):
        assert "eval" in prescan('eval("1+1")')
        assert "exec" in prescan('exec("x = 1")')

    def test_attribute_forms_still_blocked(self):
        assert prescan("import builtins\nbuiltins.eval('1')") is not None
        assert prescan("import os\nos.system('echo pwned')") is not None

    @pytest.mark.parametrize("code", [
        "print('safe')",
        "x = [i * i for i in range(10)]",
        "import math\nprint(math.sqrt(16))",
        "def f(n):\n    return n + 1\nprint(f(41))",
    ])
    def test_legitimate_code_passes(self, code):
        assert prescan(code) is None


# ===========================================================================
# D4 — compositor NaN tick + dice distribution memoization
# ===========================================================================

class TestD4CompositorTick:
    def test_tick_rejects_nan(self):
        comp = Compositor(HeadlessCanvas())
        with pytest.raises(ValueError):
            comp.tick(float("nan"))
        assert comp.frames == 0
        assert comp.fps == 0.0

    def test_tick_rejects_inf(self):
        comp = Compositor(HeadlessCanvas())
        with pytest.raises(ValueError):
            comp.tick(float("inf"))

    def test_tick_still_accepts_normal_dt(self):
        comp = Compositor(HeadlessCanvas())
        comp.tick(1 / 60)
        assert comp.frames == 1
        assert comp.fps == pytest.approx(60.0)


class TestD4DiceRenderCost:
    def test_render_costs_about_one_distribution(self):
        # 30d30: render() needs the distribution twice (bar chart +
        # prob_at_least).  With the memoized distribution the second call
        # is a cache hit, so render ~= 1x a single cold distribution call
        # (old code: ~2x).
        canvas = HeadlessCanvas()
        vp = DiceViewport(num=30, sides=30, target=450)
        vp.rect = Rect(0, 0, 1200, 600)
        _distribution_cached.cache_clear()  # warmup render
        vp.render(canvas)
        _distribution_cached.cache_clear()
        t0 = time.perf_counter()
        vp.render(canvas)
        t_render = time.perf_counter() - t0
        _distribution_cached.cache_clear()
        t0 = time.perf_counter()
        distribution(30, 30)
        t_single = time.perf_counter() - t0
        assert t_render < 1.5 * t_single, (
            f"render {t_render * 1000:.0f}ms vs "
            f"single distribution {t_single * 1000:.0f}ms"
        )


# ===========================================================================
# D5 — viewport data contracts
# ===========================================================================

class TestD5VitalsContract:
    def test_sagnaskemma_members_key_renders(self):
        party = PartyState([
            SagnaMember(name="Eirik", hp=24, max_hp=30),
            SagnaMember(name="Astrid", hp=0, max_hp=22, status="down"),
        ])
        state = party.to_viewport_state()
        assert set(state) == {"members"}
        vp = VitalsViewport()
        vp.rect = Rect(0, 0, 800, 600)
        vp.update(state)
        canvas = HeadlessCanvas()
        vp.render(canvas)
        texts = canvas.texts()
        assert "no party data" not in texts
        assert "Eirik" in texts and "Astrid" in texts
        assert any("24/30" in t for t in texts)
        assert any("0/22" in t for t in texts)

    def test_party_key_still_works(self):
        vp = VitalsViewport()
        vp.rect = Rect(0, 0, 800, 600)
        vp.update({"party": [{"name": "Bjorn", "hp": 10, "max_hp": 20}]})
        canvas = HeadlessCanvas()
        vp.render(canvas)
        assert "Bjorn" in canvas.texts()


class TestD5DivinationContract:
    def test_tarot_adapter_shape_renders_names_and_reversals(self):
        spread = divination_adapter.draw_tarot(3, seed=42)
        raw = spread["spread"]
        assert all(set(c) == {"position", "card", "upright"} for c in raw)
        vp = DivinationViewport()
        vp.rect = Rect(0, 0, 900, 400)
        vp.update(spread)
        assert len(vp.spread) == 3
        for card, entry in zip(vp.spread, raw):
            assert card.name == entry["card"], f"name lost: {card.name!r}"
            assert card.name != "?"
            assert card.position == entry["position"]
            assert card.reversed == (not entry["upright"])
        canvas = HeadlessCanvas()
        vp.render(canvas)
        texts = canvas.texts()
        assert "no spread drawn" not in texts
        for entry in raw:
            first_word = entry["card"].split()[0]
            assert any(first_word in t for t in texts), entry["card"]
        if any(c.reversed for c in vp.spread):
            assert "reversed" in texts

    def test_rune_cast_shape_accepted(self):
        cast = divination_adapter.draw_runes(3, seed=7)
        raw = cast["cast"]
        vp = DivinationViewport()
        vp.rect = Rect(0, 0, 900, 400)
        vp.update(cast)
        assert [c.name for c in vp.spread] == [r["rune"] for r in raw]
        assert [c.reversed for c in vp.spread] == [
            not r["upright"] for r in raw
        ]

    def test_legacy_shape_still_works(self):
        vp = DivinationViewport()
        vp.update({"spread": [
            {"name": "The Fool", "position": "Past", "reversed": True}
        ]})
        assert vp.spread[0].name == "The Fool"
        assert vp.spread[0].reversed is True


# ===========================================================================
# D6a — scheduler: heap instead of sort-per-pop, same order semantics
# ===========================================================================

class _SortReferenceScheduler:
    """Faithful copy of the pre-D6a sort-based scheduler (parity oracle)."""

    def __init__(self, aging_step: int = AGING_STEP) -> None:
        self._aging_step = aging_step
        self._jobs = {}
        self._queue = []
        self._seq = itertools.count()
        self._ids = itertools.count(1)

    def submit(self, fn, kind, priority=0):
        job_id = f"job-{next(self._ids)}"
        seq = next(self._seq)
        job = Job(id=job_id, kind=kind, priority=priority, seq=seq,
                  effective_priority=priority, _fn=fn)
        self._jobs[job_id] = job
        self._queue.append(job)
        return job_id

    def _pop_next(self):
        candidates = sorted(
            (j for j in self._queue if j.status == QUEUED),
            key=lambda j: (-j.effective_priority, j.seq),
        )
        if not candidates:
            return None
        chosen = candidates[0]
        for job in candidates[1:]:
            job.effective_priority += self._aging_step
        self._queue.remove(chosen)
        return chosen

    def run_next(self):
        job = self._pop_next()
        if job is None:
            return None
        job.run()
        return job

    def run_all(self):
        finished = []
        while True:
            job = self._pop_next()
            if job is None:
                break
            try:
                job.run()
            except BaseException:
                pass
            finished.append(job)
        return finished


def _random_ops(rng, n_ops):
    ops = []
    for _ in range(n_ops):
        ops.append(("submit", rng.randint(-3, 15)))
        if rng.random() < 0.45:
            ops.append(("run",))
    return ops


class TestD6aSchedulerHeap:
    @pytest.mark.parametrize("seed", [1, 20261010, 777])
    def test_pop_order_parity_with_sort_reference(self, seed):
        rng = random.Random(seed)
        new, ref = NeuralScheduler(), _SortReferenceScheduler()
        order_new, order_ref = [], []
        for op in _random_ops(rng, 150):
            if op[0] == "submit":
                new.submit(lambda: None, kind="t", priority=op[1])
                ref.submit(lambda: None, kind="t", priority=op[1])
            else:
                jn, jr = new.run_next(), ref.run_next()
                assert (jn is None) == (jr is None)
                if jn is not None:
                    order_new.append(jn.id)
                    order_ref.append(jr.id)
        order_new.extend(j.id for j in new.run_all())
        order_ref.extend(j.id for j in ref.run_all())
        assert order_new == order_ref

    def test_pending_matches_pop_order(self):
        rng = random.Random(99)
        sched = NeuralScheduler()
        for _ in range(60):
            sched.submit(lambda: None, kind="t", priority=rng.randint(0, 9))
        expect = [j.id for j in sched.pending()]
        got = []
        while True:
            job = sched.run_next()
            if job is None:
                break
            got.append(job.id)
        assert got == expect

    def test_run_all_subquadratic_growth(self):
        def bench(n):
            sched = NeuralScheduler()
            for i in range(n):
                sched.submit(lambda: None, kind="t", priority=i % 7)
            t0 = time.perf_counter()
            sched.run_all()
            return time.perf_counter() - t0

        bench(200)  # warmup
        t_small = min(bench(800) for _ in range(3))
        t_big = min(bench(3200) for _ in range(3))
        ratio = t_big / max(t_small, 1e-9)
        # Heap run_all is O(n log n): 4x jobs -> ~4.5x time.  The old
        # sort-per-pop was O(n^2 log n): 4x jobs -> ~16x+ time.
        assert ratio < 8.0, f"growth looks quadratic: {ratio:.1f}x for 4x jobs"


# ===========================================================================
# D6b — tts: hash once per distinct char, not once per sample
# ===========================================================================

class TestD6bTtsCharCache:
    def test_sha256_called_once_per_distinct_char(self, monkeypatch):
        calls = []
        orig = tts_module._char_frequency

        def counting(char, base_freq, pitch):
            calls.append(char)
            return orig(char, base_freq, pitch)

        monkeypatch.setattr(tts_module, "_char_frequency", counting)
        # 60 chars -> 72765 samples, but only 2 distinct characters.
        wav = tts_module.synthesize("ab" * 30)
        assert wav[:4] == b"RIFF"
        assert len(set(calls)) == 2
        assert len(calls) == 2  # one hash per distinct char, not per sample


# ===========================================================================
# D6c — mcp: read timeouts
# ===========================================================================

def _hang_forever(args):
    time.sleep(10)
    return {}


class TestD6cMcpTimeout:
    def test_call_tool_timeout_raises(self):
        server = MockMCPServer()
        server.register_tool("hang", _hang_forever)
        client, _server = make_pipe_pair(server)
        try:
            start = time.monotonic()
            with pytest.raises(TimeoutError):
                client.call_tool("hang", {}, timeout=0.5)
            elapsed = time.monotonic() - start
            assert elapsed < 2.0, f"timeout took too long: {elapsed:.2f}s"
        finally:
            client.close()
        # NOTE: server thread is a daemon stuck in the hanging handler;
        # it dies with the process — no stop() (would join 5s).

    def test_normal_calls_unaffected(self):
        server = MockMCPServer()
        server.register_tool("ping", lambda args: {"pong": True})
        client, server = make_pipe_pair(server)
        try:
            tools = client.list_tools(timeout=5)
            assert any(t["name"] == "ping" for t in tools)
            result = client.call_tool("ping", {})
            assert result["isError"] is False
            # And without any timeout (previous behaviour).
            assert client.call_tool("ping", {})["isError"] is False
        finally:
            client.close()
            server.stop()

    def test_nonpositive_timeout_rejected(self):
        server = MockMCPServer()
        client, server = make_pipe_pair(server)
        try:
            with pytest.raises(ValueError):
                client.call_tool("ping", {}, timeout=0)
        finally:
            client.close()
            server.stop()

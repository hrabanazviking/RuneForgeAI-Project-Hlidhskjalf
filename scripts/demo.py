#!/usr/bin/env python3
"""Hlidskjalf surge-build demo — Slice 50 (Phase H).

Exercises every subsystem that exists (defensive imports: phases A-G
are built in parallel, so missing modules are reported as SKIPPED, not
failures), runs the autonomous loops on a virtual clock, checks the
deploy/release artifacts, prints a summary report, and exits 0 on
success (nonzero only if an *existing* subsystem fails).

Stdlib only.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # run from a fresh checkout without install

SUBSYSTEMS = [
    ("common.logging", "hlidskjalf.common.logging"),
    ("common.errors", "hlidskjalf.common.errors"),
    ("common.ids", "hlidskjalf.common.ids"),
    ("common.config", "hlidskjalf.common.config"),
    ("protocol.envelope", "hlidskjalf.protocol.envelope"),
    ("protocol.bus", "hlidskjalf.protocol.bus"),
    ("heimdall.auth", "hlidskjalf.heimdall.auth"),
    ("heimdall.validate", "hlidskjalf.heimdall.validate"),
    ("heimdall.gateway", "hlidskjalf.heimdall.gateway"),
    ("heimdall.dispatch", "hlidskjalf.heimdall.dispatch"),
    ("kista.store", "hlidskjalf.kista.store"),
    ("kista.versions", "hlidskjalf.kista.versions"),
    ("kista.crypto", "hlidskjalf.kista.crypto"),
    ("kista.gc", "hlidskjalf.kista.gc"),
    ("kista.index", "hlidskjalf.kista.index"),
    ("kista.sync", "hlidskjalf.kista.sync"),
    ("wyrd.graph", "hlidskjalf.wyrd.graph"),
    ("wyrd.inference", "hlidskjalf.wyrd.inference"),
    ("wyrd.snapshot", "hlidskjalf.wyrd.snapshot"),
    ("wyrd.api", "hlidskjalf.wyrd.api"),
    ("verdandi.timeline", "hlidskjalf.verdandi.timeline"),
    ("verdandi.branches", "hlidskjalf.verdandi.branches"),
    ("seidr.simulator", "hlidskjalf.seidr.simulator"),
    ("seidr.montecarlo", "hlidskjalf.seidr.montecarlo"),
    ("draupnir.forge", "hlidskjalf.draupnir.forge"),
    ("draupnir.lifecycle", "hlidskjalf.draupnir.lifecycle"),
    ("draupnir.coder", "hlidskjalf.draupnir.coder"),
    ("draupnir.exec", "hlidskjalf.draupnir.exec"),
    ("draupnir.aggregate", "hlidskjalf.draupnir.aggregate"),
    ("himinbjorg.compositor", "hlidskjalf.himinbjorg.compositor"),
    ("himinbjorg.theme", "hlidskjalf.himinbjorg.theme"),
    ("himinbjorg.layout", "hlidskjalf.himinbjorg.layout"),
    ("himinbjorg.input", "hlidskjalf.himinbjorg.input"),
    ("himinbjorg.viewports.vitals", "hlidskjalf.himinbjorg.viewports.vitals"),
    ("himinbjorg.viewports.celestial", "hlidskjalf.himinbjorg.viewports.celestial"),
    ("himinbjorg.viewports.divination", "hlidskjalf.himinbjorg.viewports.divination"),
    ("himinbjorg.viewports.dice", "hlidskjalf.himinbjorg.viewports.dice"),
    ("himinbjorg.viewports.muse_stream", "hlidskjalf.himinbjorg.viewports.muse_stream"),
    ("hailo.runtime", "hlidskjalf.hailo.runtime"),
    ("hailo.embeddings", "hlidskjalf.hailo.embeddings"),
    ("hailo.tts", "hlidskjalf.hailo.tts"),
    ("hailo.stt", "hlidskjalf.hailo.stt"),
    ("hailo.scheduler", "hlidskjalf.hailo.scheduler"),
    ("host.mcp", "hlidskjalf.host.mcp"),
    ("host.harvester", "hlidskjalf.host.harvester"),
    ("host.transport", "hlidskjalf.host.transport"),
    ("host.sagnaskemma", "hlidskjalf.host.sagnaskemma"),
    ("host.divination", "hlidskjalf.host.divination"),
    ("host.tools", "hlidskjalf.host.tools"),
    ("autonomy.loops", "hlidskjalf.autonomy.loops"),
]


@dataclass
class Row:
    name: str
    status: str  # OK / SKIPPED / FAILED
    detail: str


def _try_import(module: str):
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        return exc


def check_subsystems() -> list[Row]:
    rows: list[Row] = []
    for label, module in SUBSYSTEMS:
        result = _try_import(module)
        if isinstance(result, ImportError):
            rows.append(Row(label, "SKIPPED", "not implemented yet (parallel phase)"))
        else:
            doc = (result.__doc__ or "").strip().splitlines()
            rows.append(Row(label, "OK", doc[0][:60] if doc else "imported"))
    return rows


def check_autonomy() -> Row:
    """Drive the autonomous loops on a virtual clock (no sleeping)."""
    try:
        from hlidskjalf.autonomy.loops import AutonomousLoops, LoopSpec

        calls: list[str] = []

        def _custom(loops, spec):
            calls.append(spec.name)
            loops.journal.append({"loop": spec.name, "at": loops.now,
                                  "note": "demo custom loop"})

        loops = AutonomousLoops(intervals={"simulation_tick": 60.0,
                                           "memory_consolidation": 3600.0})
        loops.register(LoopSpec("demo.custom", 30.0, _custom))
        ran_30 = loops.tick(30.0)
        ran_60 = loops.tick(30.0)  # t=60: simulation_tick + demo.custom due
        assert "demo.custom" in ran_30, f"expected demo.custom at t=30, got {ran_30}"
        assert "simulation_tick" in ran_60, f"expected simulation_tick at t=60, got {ran_60}"
        assert calls == ["demo.custom", "demo.custom"], calls
        assert abs(loops.now - 60.0) < 1e-9
        return Row("autonomy.loops (virtual-clock drive)", "OK",
                   f"t=60.0s, journal entries={len(loops.journal)}")
    except Exception:
        return Row("autonomy.loops (virtual-clock drive)", "FAILED",
                   traceback.format_exc(limit=1).strip().splitlines()[-1])


def check_artifacts() -> list[Row]:
    rows: list[Row] = []
    checks = [
        ("deploy/hlidskjalf-heimdall.service", ["[Unit]", "ExecStart", "Restart=always"]),
        ("deploy/hlidskjalf-hud.service", ["[Unit]", "ExecStart", "Restart=always"]),
        ("scripts/watchdog.sh", ["set -euo pipefail", "systemctl", "MAX_RETRIES"]),
        ("CHANGELOG.md", ["0.1.0"]),
        # README.md is Volmarr-only (EDITING_README.md_FILE_FORBIDDEN.txt);
        # the surge roadmap doc carries the quickstart instead.
        ("tasks/SURGE_50_SLICE_ROADMAP.md", ["50-SLICE", "PHASE A"]),
    ]
    for rel, needles in checks:
        path = REPO / rel
        if not path.exists():
            rows.append(Row(rel, "FAILED", "file missing"))
            continue
        text = path.read_text(encoding="utf-8")
        missing = [n for n in needles if n not in text]
        if missing:
            rows.append(Row(rel, "FAILED", f"missing markers: {missing}"))
        else:
            rows.append(Row(rel, "OK", "present, markers found"))
    return rows


def main() -> int:
    print("=" * 70)
    print("HLIDSKJALF surge-build demo — exercising every subsystem that exists")
    print("=" * 70)

    rows: list[Row] = []
    rows.append(Row("--- subsystems ---", "", ""))
    rows.extend(check_subsystems())
    rows.append(Row("--- autonomy ---", "", ""))
    rows.append(check_autonomy())
    rows.append(Row("--- deploy / release artifacts ---", "", ""))
    rows.extend(check_artifacts())

    ok = sum(1 for r in rows if r.status == "OK")
    skipped = sum(1 for r in rows if r.status == "SKIPPED")
    failed = sum(1 for r in rows if r.status == "FAILED")

    print()
    for r in rows:
        if not r.status:
            print(f"\n{r.name}")
        else:
            print(f"  [{r.status:7}] {r.name:42} {r.detail}")
    print()
    print("-" * 70)
    print(f"SUMMARY: {ok} OK | {skipped} SKIPPED (parallel phases not yet landed) "
          f"| {failed} FAILED")
    print("-" * 70)
    if failed:
        print("DEMO RESULT: FAILURE — an existing subsystem or artifact failed.")
        return 1
    print("DEMO RESULT: SUCCESS — all existing subsystems green; "
          "missing phases skipped explicitly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

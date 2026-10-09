# Changelog — Project Hliðskjálf

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-10-09 — Surge Build (Phase H: Integration & Autonomy)

First surge-build release: the monorepo scaffold plus Phase H
integration, autonomy, deploy, and release packaging. Phases A–G are
being built in parallel; subsystem modules land slice by slice and the
integration tests below skip explicitly for anything not yet present.

### Added
- **Slice 47 — End-to-end integration tests** (`tests/test_e2e.py`):
  happy-path smoke tests for every subsystem module that exists
  (harvest → gateway → minds → HUD pipeline), failure-injection tests
  (malformed envelopes rejected, never crash the gateway; execution
  surfaces must expose timeout/deadline bounds; a crashing autonomous
  loop cannot stop the other loops), plus deploy/release artifact checks.
  All skips are explicit — missing parallel-phase modules never fail.
- **Slice 48 — systemd units & watchdog**:
  - `deploy/hlidskjalf-heimdall.service` — HEIMDALL ingestion gateway unit
    (`ExecStart=/usr/bin/python3 -m hlidskjalf.heimdall.gateway`,
    `Restart=always`, hardening flags).
  - `deploy/hlidskjalf-hud.service` — HIMINBJÖRG Omni-HUD compositor unit
    (`ExecStart=/usr/bin/python3 -m hlidskjalf.himinbjorg.compositor`,
    `Restart=always`, hardening flags).
  - `scripts/watchdog.sh` — health watchdog: checks a health URL or a
    process pattern, restarts the service via systemctl with bounded
    retries, logs every step; quiet on healthy rounds; exit 1 when
    retries are exhausted.
- **Slice 49 — Autonomous loops** (`hlidskjalf/autonomy/loops.py`):
  `AutonomousLoops` virtual-clock scheduler (`LoopSpec` {name,
  interval_s, fn}; `tick(dt)` advances the clock and runs due loops —
  testable without sleeping); built-in stubs `memory_consolidation`
  (hourly) and `simulation_tick` (minutely), both replaceable via
  `register()`; per-loop failure isolation with `last_error` recording;
  idempotent scheduling; `journal` of loop activity.
- **Slice 50 — Release packaging**:
  - `scripts/demo.py` — exercises every subsystem that exists (defensive
    imports), prints a summary report, exits 0 unless an existing
    subsystem fails.
  - `README.md` — appended "Quickstart (Surge Build)" section
    (append-only; existing content untouched).
  - `pyproject.toml` — package `hlidskjalf` 0.1.0, stdlib-only runtime,
    pytest dev extra; `Makefile` with `test`/`lint`/`install` targets;
    `config/hlidskjalf.yaml` layered-config placeholder.

### Notes
- Runtime dependencies: none (stdlib only). Dev: `pytest>=7`.
- Python requirement: >= 3.10.
- No secrets are stored in the repo; services run as the unprivileged
  `hlidskjalf` user and read configuration, not code.

[0.1.0]: https://github.com/hrabanazviking/RuneForgeAI-Project-Hlidhskjalf/releases/tag/v0.1.0

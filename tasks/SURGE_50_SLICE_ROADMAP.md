# SURGE 50-SLICE ROADMAP — Project Hliðskjálf

**Goal 7 of Volmarr's coding surge (2026-10-09).**
Build out Project Hliðskjálf per all data/ideas in the repo.

## The Vision

**Hliðskjálf** — Odin's high seat, the all-seeing throne above the worlds.
An edge-native sovereign AGI co-processor: a **Raspberry Pi 5 (16GB) +
Hailo-10 NPU** that serves as Meta Muse's autonomous perceptual canvas,
memory vault, and local cognitive co-processor.

**Split-brain architecture:**
- **HOST (workstation):** Muse agent core — planner, reasoning, dialogue;
  Sagnaskemma TTRPG harness; Astrology/Divination engine; MCP client
- **EDGE (Pi 5 + Hailo-10):** HEIMDALL gateway, HIMINBJÖRG Omni-HUD
  (60 FPS), YGGDRASIL cognitive co-processor

**Subsystems:**
- **HEIMDALL** — ingestion gateway: auth, schema validation, IPC dispatch
- **HIMINBJÖRG** — Omni-HUD: party vitals, celestial wheel, tarot/runes,
  dice probability, Muse thought/speech stream
- **YGGDRASIL** — WYRD causal world graph, Verdandi timeline tracker,
  Kista artifact vault, Seidr simulator, Draupnir sub-agent forge,
  Mythic Coder / Aesir Exec
- **HAILO** — NPU neural pipeline: embeddings, local TTS/STT

**Package layout:**
```
hlidskjalf/
  common/       # logging, config, errors, ids
  protocol/     # envelope schemas, wire protocol
  heimdall/     # gateway: auth, validation, dispatch
  kista/        # artifact vault
  wyrd/         # causal world graph
  verdandi/     # timeline tracker
  seidr/        # heuristic simulator
  draupnir/     # sub-agent forge + exec
  himinbjorg/   # Omni-HUD compositor + viewports
  hailo/        # NPU pipeline (mock for dev)
  host/         # MCP client, harvester, transport
tests/
```

Every slice: Skald → Rúnhild → Eldra → Sólrún → Védis → Scribe.
Production quality, tests green, no hardcoding, law: config not code.

---

## PHASE A — Foundation (slices 1–8)

### Slice 1: Monorepo skeleton
Goal: package tree, pyproject.toml, Makefile, pytest config.
Files: `hlidskjalf/__init__.py`, `pyproject.toml`, `Makefile`, `tests/`.
Accept: `pip install -e .`, `pytest` collects, `make test` works.

### Slice 2: Common — logging, errors, ids
Goal: structured logging, error taxonomy, ULID/UUID ids.
Files: `hlidskjalf/common/{logging,errors,ids}.py`.
Accept: JSON log lines, typed errors, unique sortable ids.

### Slice 3: Common — config
Goal: layered config (defaults < YAML < env < overrides), validation.
Files: `hlidskjalf/common/config.py`, `config/hlidskjalf.yaml`.
Accept: loads, validates, reports effective config.

### Slice 4: Protocol envelope
Goal: versioned JSON envelope for all messages (per repo spec).
Files: `hlidskjalf/protocol/envelope.py`, `protocol/schemas/envelope.json`.
Accept: build/parse/validate round-trip; rejects bad versions.

### Slice 5: IPC backbone
Goal: in-process + unix-socket message bus (ZeroMQ-style, stdlib first).
Files: `hlidskjalf/protocol/bus.py`.
Accept: pub/sub, req/rep, envelope-wrapped, <1ms local.

### Slice 6: Heimdall — authentication
Goal: API-key + mTLS auth for ingress.
Files: `hlidskjalf/heimdall/auth.py`.
Accept: accepts valid creds, rejects invalid, logs attempts.

### Slice 7: Heimdall — schema validation
Goal: validate inbound payloads against schemas before dispatch.
Files: `hlidskjalf/heimdall/validate.py`.
Accept: valid passes, invalid rejected with reasons.

### Slice 8: Heimdall — dispatcher + health
Goal: route validated messages to subsystems; /health endpoint.
Files: `hlidskjalf/heimdall/{gateway,dispatch}.py`.
Accept: routes by type, unknown → dead-letter, health reports all.

## PHASE B — Kista Vault (slices 9–14)

### Slice 9: Content-addressed artifact store
Goal: put/get artifacts by SHA256, filesystem backend.
Files: `hlidskjalf/kista/store.py`.
Accept: round-trip, dedup by hash, metadata sidecar.

### Slice 10: Artifact versioning & lineage
Goal: versions, parent links, provenance chain.
Files: `hlidskjalf/kista/versions.py`.
Accept: history query, lineage walk, no orphans.

### Slice 11: Vault encryption at rest
Goal: AES-GCM per-artifact encryption with key rotation.
Files: `hlidskjalf/kista/crypto.py`.
Accept: encrypted bytes on disk, decrypt on get, key rotation works.

### Slice 12: GC & compaction
Goal: mark/sweep unreferenced artifacts, compact storage.
Files: `hlidskjalf/kista/gc.py`.
Accept: reclaims space, never deletes referenced.

### Slice 13: Artifact query & index
Goal: tag/full-text index, query API.
Files: `hlidskjalf/kista/index.py`.
Accept: tag search, text search, paginated results.

### Slice 14: Vault sync to host
Goal: push/pull artifacts over the LAN transport.
Files: `hlidskjalf/kista/sync.py`.
Accept: sync both directions, conflict → newest wins + log.

## PHASE C — WYRD World Model (slices 15–20)

### Slice 15: Causal graph core
Goal: nodes (entities), edges (causal links), in-memory + persisted.
Files: `hlidskjalf/wyrd/graph.py`.
Accept: add/query nodes/edges, persist/restore.

### Slice 16: Causal inference
Goal: "what causes X?" — upstream walk with weights.
Files: `hlidskjalf/wyrd/inference.py`.
Accept: returns ranked cause chains.

### Slice 17: World-state snapshots & diffs
Goal: snapshot graph, diff two snapshots.
Files: `hlidskjalf/wyrd/snapshot.py`.
Accept: compact snapshots, readable diffs.

### Slice 18: Verdandi timeline tracker
Goal: append-only event timeline, temporal queries.
Files: `hlidskjalf/verdandi/timeline.py`.
Accept: append, range query, "what happened before X".

### Slice 19: Timeline branching
Goal: branch/merge timelines (what-if).
Files: `hlidskjalf/verdandi/branches.py`.
Accept: branch, merge, conflict detection.

### Slice 20: World-model query API
Goal: unified query: graph + timeline.
Files: `hlidskjalf/wyrd/api.py`.
Accept: single query endpoint, JSON results.

## PHASE D — Seidr & Draupnir (slices 21–27)

### Slice 21: Seidr simulator core
Goal: heuristic simulation runner (discrete steps).
Files: `hlidskjalf/seidr/simulator.py`.
Accept: runs scenarios, deterministic with seed.

### Slice 22: Monte Carlo scenarios
Goal: N-run ensembles, distribution of outcomes.
Files: `hlidskjalf/seidr/montecarlo.py`.
Accept: mean/variance, top outcomes ranked.

### Slice 23: Draupnir forge — spawn
Goal: spawn sub-agents (processes) with task specs.
Files: `hlidskjalf/draupnir/forge.py`.
Accept: spawn, heartbeat, cap on concurrency.

### Slice 24: Sub-agent lifecycle
Goal: supervise, restart, timeout, collect results.
Files: `hlidskjalf/draupnir/lifecycle.py`.
Accept: timeout kills, crash restarts (bounded), results collected.

### Slice 25: Mythic Coder
Goal: code-generation task type (template + LLM-hook interface).
Files: `hlidskjalf/draupnir/coder.py`.
Accept: generates code files, syntax-validates.

### Slice 26: Aesir Exec — sandboxed execution
Goal: run code in sandbox (timeout, no network, rlimits).
Files: `hlidskjalf/draupnir/exec.py`.
Accept: runs safe code, blocks unsafe, captures output.

### Slice 27: Result aggregation
Goal: fan-in results from sub-agents, merge/summarize.
Files: `hlidskjalf/draupnir/aggregate.py`.
Accept: merged report, partial-failure tolerant.

## PHASE E — Himinbjörg HUD (slices 28–35)

### Slice 28: HUD compositor core
Goal: 60 FPS main loop, viewport registry (headless-testable).
Files: `hlidskjalf/himinbjorg/compositor.py`.
Accept: runs headless at 60fps, viewports plug in.

### Slice 29: Viewport — party vitals
Goal: TTRPG party HP/status display.
Files: `hlidskjalf/himinbjorg/viewports/vitals.py`.
Accept: renders party state, updates live.

### Slice 30: Viewport — celestial wheel
Goal: 360° astrology wheel (uses host ephemeris data).
Files: `hlidskjalf/himinbjorg/viewports/celestial.py`.
Accept: draws wheel, planets positioned.

### Slice 31: Viewport — tarot/runes
Goal: divination display (cards/runes drawn).
Files: `hlidskjalf/himinbjorg/viewports/divination.py`.
Accept: renders spreads.

### Slice 32: Viewport — dice probability
Goal: dice odds HUD.
Files: `hlidskjalf/himinbjorg/viewports/dice.py`.
Accept: correct probabilities, live updates.

### Slice 33: Viewport — Muse stream
Goal: thought/speech stream from host.
Files: `hlidskjalf/himinbjorg/viewports/muse_stream.py`.
Accept: displays streamed text, scrollback.

### Slice 34: HUD theming & layout
Goal: themes, layout engine (grid/tiles).
Files: `hlidskjalf/himinbjorg/{theme,layout}.py`.
Accept: two themes, layouts switch live.

### Slice 35: HUD input
Goal: keyboard/touch input routing to viewports.
Files: `hlidskjalf/himinbjorg/input.py`.
Accept: key events reach focused viewport.

## PHASE F — Hailo Neural (slices 36–40)

### Slice 36: Hailo runtime abstraction
Goal: NPU interface with CPU mock fallback for dev.
Files: `hlidskjalf/hailo/runtime.py`.
Accept: mock runs everywhere, real path stubbed.

### Slice 37: Embedding pipeline
Goal: text → embeddings (mock model).
Files: `hlidskjalf/hailo/embeddings.py`.
Accept: deterministic vectors, cosine similarity works.

### Slice 38: Local TTS
Goal: text → audio (mock synthesizer, WAV out).
Files: `hlidskjalf/hailo/tts.py`.
Accept: produces valid WAV, voice config.

### Slice 39: STT ingestion
Goal: audio → text (mock recognizer).
Files: `hlidskjalf/hailo/stt.py`.
Accept: round-trip with TTS mock.

### Slice 40: Neural workload scheduler
Goal: queue/prioritize NPU jobs.
Files: `hlidskjalf/hailo/scheduler.py`.
Accept: FIFO + priority, no starvation.

## PHASE G — Host Integration (slices 41–46)

### Slice 41: MCP client
Goal: JSON-RPC client per MCP spec (mock server for tests).
Files: `hlidskjalf/host/mcp.py`.
Accept: call/list tools against mock.

### Slice 42: Host harvester
Goal: collect host state (processes, files) into envelopes.
Files: `hlidskjalf/host/harvester.py`.
Accept: produces valid state envelopes.

### Slice 43: LAN transport
Goal: HTTP/WS bidirectional host↔edge (mock both ends).
Files: `hlidskjalf/host/transport.py`.
Accept: message round-trip, reconnect on drop.

### Slice 44: Sagnaskemma adapter
Goal: TTRPG harness state → HUD vitals viewport feed.
Files: `hlidskjalf/host/sagnaskemma.py`.
Accept: party state flows to viewport.

### Slice 45: Divination adapter
Goal: ephemeris/tarot data → celestial/divination viewports.
Files: `hlidskjalf/host/divination.py`.
Accept: planet positions + draws flow through.

### Slice 46: Tool invocation bridge
Goal: host tools callable from edge via MCP.
Files: `hlidskjalf/host/tools.py`.
Accept: invoke mock tool, result returns.

## PHASE H — Integration & Autonomy (slices 47–50)

### Slice 47: End-to-end integration tests
Goal: full pipeline tests (harvest → gateway → minds → HUD).
Files: `tests/test_e2e.py`.
Accept: green, covers happy path + failure injection.

### Slice 48: systemd units & watchdog
Goal: service files, auto-restart, health watchdog.
Files: `deploy/hlidskjalf-*.service`, `scripts/watchdog.sh`.
Accept: units valid (systemd-analyze), watchdog revives.

### Slice 49: Autonomous loops
Goal: scheduled cognition (memory consolidation, simulation ticks).
Files: `hlidskjalf/autonomy/loops.py`.
Accept: loops run on schedule, idempotent.

### Slice 50: Release packaging & docs
Goal: version, changelog, README quickstart, demo script.
Files: `CHANGELOG.md`, `scripts/demo.py`.
Accept: fresh checkout → demo runs green.

---

## Acceptance (whole roadmap)

- [ ] All 50 slices implemented, tests green (`make test`)
- [ ] No hardcoded secrets; config-driven
- [ ] Docs updated (README quickstart)
- [ ] Pushed to origin, HEAD verified
- [ ] `tasks/SURGE_50_SLICE_ROADMAP.md` marked complete

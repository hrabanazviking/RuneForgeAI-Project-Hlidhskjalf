# Repository Architecture & Unified Roadmap: Project Hliðskjálf

(Repository_Architecture_Unified_Roadmap_Project_Hlidhskjalf.md)

1. Repository Naming Strategy
The ideal name for this unified system is Hlidskjalf-Edge-Core (or simply Project-Hlidskjalf), with Yggdrasil-Omni-Node as the primary engineering alternative.
Option 1 (Recommended): Hlidskjalf-Edge-Core
 * Mythological Foundation: Hliðskjálf is the high seat of Odin set above the world, from which the watcher perceives all nine realms simultaneously and directs worldly action.
 * Architectural Justification: This combines both halves of your system into a single cohesive concept: Himinbjörg (the high observatory and sensory HUD) and Yggdrasil (the causal trunk, memory, and cognitive action engine). It represents the convergence of complete real-time perception with autonomous edge authority.
Option 2 (Systemic Focus): Yggdrasil-Omni-Node
 * Mythological Foundation: The World Tree supporting the cosmic framework, roots anchored in memory (Urðarbrunnr / Kista), and branches extending into all realms.
 * Architectural Justification: Emphasizes the hardware edge device (Pi 5 + Hailo-10) as a multi-purpose peripheral node extending Muse’s central intelligence.
Option 3 (Bridging Focus): Bifrost-Omni-Runtime
 * Mythological Foundation: The burning rainbow bridge connecting Midgard (physical terminal) to Asgard (Muse’s cognitive cloud/host compute).
 * Architectural Justification: Highlights the bi-directional telemetry and tool-calling protocol linking host and hardware edge.
2. Unified Monorepo Architecture
A clean monorepo layout prevents circular dependencies between the graphics compositor (Himinbjörg) and the cognitive co-processor (Yggdrasil) while sharing schemas, models, and IPC channels.
hlidskjalf-edge-core/
├── README.md
├── pyproject.toml
├── Makefile
├── config/
│   ├── default_config.yaml
│   └── display_profiles.json
├── protocol/                     # Shared Wire Formats & Schemas
│   ├── schemas/
│   │   ├── envelope.json
│   │   ├── ttrpg_event.json
│   │   ├── divination_event.json
│   │   └── yggdrasil_mcp.json
│   └── serialization.py          # MessagePack / JSON encode-decode
├── services/
│   ├── yggdrasil/                # Edge AGI Co-Processor Services
│   │   ├── __init__.py
│   │   ├── daemon.py             # JSON-RPC / MCP Core Server
│   │   ├── gateway/              # Heimdall Gateway & Security Sentry
│   │   ├── memory/               # Kista Vault & hermes-state SQLite/Vector DB
│   │   ├── world_model/          # WYRD Causal DAG & Verdandi Timeline Tracker
│   │   ├── simulation/           # Seidr Probabilistic & Divination Forecaster
│   │   ├── subagents/            # Draupnir Recursive Worker Pool
│   │   └── sandbox/              # Mythic Coder CLI & A.E.S.I.R. Execution Harness
│   ├── himinbjorg/               # Omni-HUD Visual Display Engine
│   │   ├── __init__.py
│   │   ├── compositor.py         # Pygame / ModernGL 60 FPS Render Loop
│   │   ├── canvas_ttrpg.py       # Sagnaskemma Party Vitals, Encounters, Rolls
│   │   ├── canvas_divination.py  # 360° Celestial Wheel, Aspect Chords, Tarot Spreads
│   │   ├── canvas_system.py      # Sub-agent telemetry, WYRD DAG, Resource Monitor
│   │   └── typography.py         # UTF-8 Runic glyphs & Astrological fonts
│   └── hailo/                    # Hailo-10 AI2+ HAT Neural Acceleration
│       ├── __init__.py
│       ├── pipeline_manager.py   # HailoRT VStream Controller
│       ├── tts_worker.py         # Kokoro / Piper Neural Audio Synthesis
│       └── embed_worker.py       # Local BGE / Nomic Embeddings on NPU
├── client/                       # Host-Side Integration (Runs on Muse's Workstation)
│   ├── harvester.py              # CLI Output Interceptor (Sagnaskemma & Astrology)
│   ├── mcp_bridge.py             # Model Context Protocol Client for Muse
│   └── muse_tool_defs.json       # Exportable Tool Manifest for Muse
├── deploy/                       # Deployment, Systemd, & Hardware Scripts
│   ├── systemd/
│   │   ├── hlidskjalf-core.service
│   │   └── hlidskjalf-hud.service
│   ├── install_pi5_dependencies.sh
│   └── setup_hailo10_pcie.sh
└── tests/
    ├── test_protocol.py
    ├── test_kista_vault.py
    ├── test_wyrd_dag.py
    └── test_compositor.py

3. Seven-Slice Implementation Roadmap
  ┌──────────────────────────────────────────────────────────────┐
  │ SLICE 1: Monorepo Foundation, Wire Protocol & IPC Backbone   │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ SLICE 2: Kista Vault & WYRD World Model (Memory & State)     │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ SLICE 3: Seidr Simulation & Draupnir Sub-Agent Engine        │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ SLICE 4: Himinbjörg Omni-HUD Multi-Viewport Compositor       │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ SLICE 5: Hailo-10 Neural Pipeline (NPU Audio & Embeddings)   │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ SLICE 6: Host-Side Harvester & Muse Model Context Protocol   │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ SLICE 7: End-to-End Integration, Systemd & Autonomous Loops  │
  └──────────────────────────────────────────────────────────────┘

Slice 1: Monorepo Foundation, Wire Protocol & IPC Backbone
Goal: Establish unified project scaffolding, deterministic communication protocols, and a local inter-process communication (IPC) event bus on the Pi 5.
 * Tasks:
   * Initialize monorepo directory tree, packaging configurations (pyproject.toml), and local virtual environments.
   * Implement protocol/schemas/envelope.json defining strict JSON-RPC 2.0 and event message wrappers.
   * Build an in-memory Pub/Sub event bus (protocol/bus.py) with zero-copy local threading queues and a local loopback Unix domain socket /tmp/hlidskjalf_ipc.sock.
   * Write automated serialization unit tests verifying sub-millisecond JSON and MessagePack round-trip latency.
Slice 2: Kista Vault & WYRD World Model (Memory & Causality)
Goal: Provide Muse with long-term memory persistence and an interactive causal reality graph on the edge.
 * Tasks:
   * Build KistaVault backed by SQLite with write-ahead logging (WAL) enabled in /opt/yggdrasil/kista.db for low-latency atomic reads/writes.
   * Implement artifact classification (code, lore, transits, state_snapshots) with full text search (FTS5).
   * Implement WyrdWorldModel: a Directed Acyclic Graph (DAG) tracking entity states, actions, and causal dependencies.
   * Implement VerdandiTimeline: an active snapshot manager that serializes the current state of manifest reality and prunes orphaned speculative branches.
Slice 3: Seidr Simulation & Draupnir Sub-Agent Engine
Goal: Equip the Pi 5 to run heuristic evaluations, probabilistic forecasts, and autonomous sub-agent workers.
 * Tasks:
   * Build SeidrEngine: implement mathematical blending of deterministic empirical metrics and symbolic/intuitive factors.
   * Build MythicCoderSandbox: isolated directory environment with resource-constrained execution wrappers (timeout enforcement, process tree cleanup).
   * Build DraupnirForge: recursive sub-agent spawner that generates lightweight Python worker scripts, queues them in the sandbox, and aggregates execution results.
   * Expose all functions as structured JSON-RPC methods on HeimdallGateway.
Slice 4: Himinbjörg Omni-HUD Multi-Viewport Compositor
Goal: Create the hardware-accelerated, multi-mode visual interface running at a locked 60 FPS on the Raspberry Pi 5.
 * Tasks:
   * Build Compositor: Pygame/SDL2 rendering engine utilizing hardware acceleration with double-buffering.
   * Build TTRPG Viewport: Dynamic party roster cards, color-interpolated health bars (HP/MaxHP), AC shields, encounter text blocks, and dice roll logs for Sagnaskemma.
   * Build Divination Viewport: 360° celestial wheel with Ascendant horizontal lock, aspect chord lines (trine, square, opposition), Chaldean planetary hours, and 3-card Tarot/Runic oracle frames.
   * Build Yggdrasil System Viewport: Real-time visual DAG monitor showing active WYRD entities, memory operations, and Hailo NPU throughput.
   * Implement auto-switching logic based on incoming event types, with manual keyboard overrides (Tab, 1, 2, 3).
Slice 5: Hailo-10 Neural Pipeline (NPU Audio & Embeddings)
Goal: Leverage the Hailo-10 AI2+ HAT's 40 TOPS and 8GB RAM for zero-host-overhead neural inference.
 * Tasks:
   * Configure PCIe Gen 3 interface and initialize HailoRT virtual streams inside services/hailo/pipeline_manager.py.
   * Deploy text-to-speech .hef model (Kokoro or Piper TTS) into Hailo NPU memory; connect output queue directly to the local ALSA audio device.
   * Deploy local vector embedding model (BGE-Small/Large .hef) to accelerate semantic searches inside Kista Vault without host LLM calls.
   * Route all text tagged for voice narration from Muse directly through this worker.
Slice 6: Host-Side Harvester & Muse Model Context Protocol (MCP)
Goal: Connect Muse's workstation to the Pi 5 seamlessly.
 * Tasks:
   * Implement client/harvester.py: a CLI wrapper for Muse's computer that intercepts stdout from Sagnaskemma and astrology-engine, extracts entities and dice rolls via regex/JSON, and emits them to the Pi 5.
   * Implement client/mcp_bridge.py: an official Model Context Protocol (MCP) server exposing all Yggdrasil tools (kista.*, wyrd.*, seidr.*, coder.*, hud.*) to Muse's agent loop.
   * Validate bi-directional tool calling: Muse invokes a tool \rightarrow Pi executes it \rightarrow results return to Muse \rightarrow visual updates appear instantly on the HUD.
Slice 7: End-to-End Integration, Systemd & Autonomous Loops
Goal: Package the system into resilient, unattended system services that boot automatically on the Pi 5.
 * Tasks:
   * Write systemd unit files: hlidskjalf-core.service (daemon & API) and hlidskjalf-hud.service (visual compositor).
   * Implement watchdog health checks and auto-restart policies on socket disconnects.
   * Create an automated test script (tests/e2e_stress_test.py) that simulates concurrent dice rolls, celestial transit updates, sub-agent spawns, and TTS playback.
   * Document the complete setup in README.md for zero-friction deployment by AI coding agents.
4. Unified Hardware Resource Matrix (Pi 5 + Hailo-10)
| Subsystem Component | Process / Service | Hardware Target | Memory Footprint | Compute Budget |
|---|---|---|---|---|
| Heimdall & Core Daemons | hlidskjalf-core | Pi 5 Cortex-A76 (Core 0) | ~250 MB RAM | < 3% CPU |
| WYRD DAG & Kista Vault | SQLite WAL + Python | Pi 5 Cortex-A76 (Core 1) | ~1.5 GB RAM | < 5% CPU |
| Mythic Coder Sandbox | Worker Subprocesses | Pi 5 Cortex-A76 (Core 2) | Dynamic (Up to 4 GB) | Scaled per task |
| Himinbjörg HUD Canvas | Pygame / VideoCore VII | Pi 5 Cortex-A76 (Core 3) + GPU | ~400 MB RAM | 60 FPS / ~12% CPU |
| Local Neural Audio (TTS) | HailoRT VStreams | Hailo-10 M.2+ HAT | ~2.1 GB NPU Memory | 0% CPU (Offloaded) |
| Vector Embeddings (Vault) | HailoRT VStreams | Hailo-10 M.2+ HAT | ~1.8 GB NPU Memory | 0% CPU (Offloaded) |
| System Headroom & Cache | Linux OS / Buffers | Pi 5 System LPDDR4X | ~9.8 GB Free | Stable thermal baseline |

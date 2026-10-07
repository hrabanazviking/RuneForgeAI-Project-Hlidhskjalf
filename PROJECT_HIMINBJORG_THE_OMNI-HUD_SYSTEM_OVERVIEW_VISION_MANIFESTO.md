# PROJECT HIMINBJÖRG: THE OMNI-HUD SYSTEM OVERVIEW & VISION MANIFESTO

(PROJECT_HIMINBJORG_THE_OMNI-HUD_SYSTEM_OVERVIEW_VISION_MANIFESTO.md)

1. Executive Summary
Project Himinbjörg (The Omni-HUD) is a dedicated, real-time visual companion terminal and telemetry dashboard designed for the Meta Muse AI agent.
Instead of relegating Muse to a headless terminal window or plain text chat, this project decouples agent reasoning and compute from visual representation. Muse runs her heavy agent logic and engines on her host computer, while streaming structured telemetry over the local network to a dedicated Raspberry Pi 5 (16GB RAM) outfitted with a Hailo-10 AI2+ HAT (8GB RAM).
The Pi 5 acts as a hardware-accelerated, ambient display console that renders high-fidelity 2D/3D visual graphics, tactical data, interactive spreads, and live reasoning streams across two primary domains:
 * Tabletop Roleplaying (TTRPG): Live combat tracking, party health/AC, encounter lore, and dice-roll telemetry driven by Sagnaskemma.
 * Astrology & Divination: Real-time celestial wheel projections, planetary transit matrices, planetary hours, and visual Tarot/Runic card spreads driven by the Astrology Engine.
 * Local Edge Intelligence: Utilizing the Hailo-10 NPU to handle real-time neural speech synthesis (TTS) and vision generation locally on the display unit itself.
┌────────────────────────────────────────────────────────┐
│                   MUSE AGENT HOST                      │
│                                                        │
│  [Meta Muse Agent] ─── Headless Execution Loop         │
│         │                                              │
│         ├── Sagnaskemma CLI (D&D / TTRPG Engine)       │
│         └── Astrology & Divination CLI                 │
│                                                        │
│  [Telemetry Bridge] ── Converts CLI Output to JSON     │
└──────────────────────────┬─────────────────────────────┘
                           │ Network Ingestion (LAN HTTP/WS)
                           ▼
┌────────────────────────────────────────────────────────┐
│         RASPBERRY PI 5 + HAILO-10 DISPLAY NODE         │
│                                                        │
│  [VideoCore VII GPU] ── 60 FPS Pygame/OpenGL Canvas    │
│         ├── TTRPG Tactical & Lore View                 │
│         ├── Celestial 360° Wheel & Tarot Spreads       │
│         └── Real-Time Muse Reasoning & Dialogue HUD    │
│                                                        │
│  [Hailo-10 NPU 8GB]  ── Local Neural Speech (TTS)      │
└────────────────────────────────────────────────────────┘

2. Problem Statement & User Objectives
The Problem
 * Headless Isolation: Advanced command-line engines (like Sagnaskemma and astrology-engine) produce rich narrative, mathematical, and spatial data. When an AI agent runs them headlessly, the output is collapsed into raw terminal text or summarized verbally, stripping away visual context, immersion, and quick readability.
 * Host Resource Contention: Running heavy graphic compositors, web dashboards, or local speech synthesis on the same machine running the agent's inference loops and reasoning processes causes latency, audio stuttering, and resource competition.
The Objective
 * Build an ambient physical HUD that sits in the workspace as a dedicated companion window.
 * Enable Meta Muse to programmatically push updates to the screen whenever she rolls dice, consults lore, casts an astrological chart, or pulls cards.
 * Provide an immediate, scannable visual experience featuring dark, high-contrast, modern runic aesthetics (deep blues, purples, golds, and slate backgrounds).
 * Utilize the Raspberry Pi 5 + Hailo-10 HAT to its full potential: Pi handles graphics and networking; Hailo-10 handles edge AI inference (voice generation).
3. Core Functional Domains
                                  OMNI-HUD
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
   SAGNASKEMMA TTRPG                                 DIVINATION & ASTROLOGY
 ┌───────────────────────────┐                     ┌───────────────────────────┐
 │ • Party Vitals & Armor    │                     │ • 360° Celestial Wheel    │
 │ • Real-Time Health Bars   │                     │ • Planetary Aspect Chords │
 │ • Encounter Lore Canvas   │                     │ • Chaldean Planetary Hour │
 │ • Probability & Dice Log  │                     │ • Multi-Card Tarot Spreads│
 └───────────────────────────┘                     └───────────────────────────┘

Domain A: Sagnaskemma (D&D / TTRPG Lore Engine)
 * Party Manifest: Persistent tracking of player and companion character cards (e.g., Volmarr, Astrid, Torin) displaying dynamic health bars (HP / MaxHP), Armor Class (AC), class levels, and status effects.
 * Encounter & World Lore: An auto-scrolling narrative canvas displaying atmospheric descriptions, environmental hazards, room dimensions, and ancient inscriptions generated during the session.
 * Dice Telemetry & Resolution: Visual roll log capturing player and AI rolls, displaying dice notation (e.g., 1d20+7), DC checks, advantage/disadvantage states, and final outcomes.
Domain B: Astrology & Divination Engine
 * Mathematical Celestial Wheel: A 360-degree radial chart calculated from Swiss Ephemeris data, orienting the Ascendant at the 9 o'clock horizon (180^\circ screen coordinates) and plotting planetary bodies (Sun, Moon, Mars, Saturn, Jupiter) at their exact tropical longitudes.
 * Geometric Aspect Matrix: Automatic calculation and rendering of aspect lines connecting celestial bodies across the central hub:
   * Trines (120^\circ \pm 4^\circ, rendered in luminous blue)
   * Squares (90^\circ \pm 4^\circ, rendered in alert red)
   * Oppositions (180^\circ \pm 4^\circ, rendered in royal purple)
 * Tarot & Runic Spreads: Modular card rendering supporting 3-card oracle spreads (Past/Present/Future or Origin/Threshold/Outcome) and multi-card layouts. Includes card titles, upright/reversed orientations, Elder Futhark rune emblems, and core symbolic keywords.
 * Planetary Hours: Real-time computation of diurnal and nocturnal Chaldean planetary hours based on current local sunrise/sunset coordinates.
4. Hardware Architecture & Compute Matrix
| Hardware Component | Hardware Specs | Assigned Subsystem Role |
|---|---|---|
| Primary Host Machine | High-Compute Workstation | Muse Agent Brain: Runs Meta Muse, orchestrator scripts, local LLMs, headless Sagnaskemma engine, and astrology calculation scripts. Dispatches JSON over LAN. |
| Raspberry Pi 5 | Quad-Core ARM Cortex-A76 @ 2.4GHz
16GB LPDDR4X RAM
VideoCore VII GPU | Compositor & Server: Runs native Linux desktop, local HTTP/WebSocket ingestion daemon, state cache, Pygame/SDL2 rendering loop at 60 FPS. |
| Hailo-10 AI2+ HAT | 40 TOPS AI Compute
8GB Dedicated On-Module Memory
PCIe Gen 3 Interface | Edge Neural Acceleration: Hosts local neural text-to-speech models (Kokoro/Piper in .hef format) to vocalize Muse's speech output directly through Pi audio hardware without host streaming. |
5. Software Ecosystem & Information Flow
1. Sagnaskemma / Astrology CLI
   └─> Headless execution triggered by Muse
2. muse_telemetry_harvester.py
   └─> Regex & JSON extraction parses CLI output
3. HTTP / WebSocket Link (Port 8080 / 8765)
   └─> Wire protocol envelope sent across LAN
4. omni_hud_display_node.py (Pi 5)
   ├─> Mutex-protected state store updated
   ├─> Pygame/SDL2 re-renders frame (60 FPS)
   └─> Text utterance pushed to hailo_neural_worker.py
5. Hailo-10 NPU
   └─> Neural speech synthesized to local speakers

 * Trigger: Muse executes a command in Sagnaskemma or astrology-engine.
 * Harvesting: A lightweight wrapper script captures stdout, extracts structured data (dice rolls, party vitals, card pulls, planetary coordinates), and packages it into a unified Omni-HUD JSON envelope.
 * Transport: The envelope is sent via an HTTP POST request or binary WebSocket to the Raspberry Pi 5 IP address (http://<PI_IP>:8080/api/event).
 * Compositing: The Pi 5's display daemon receives the JSON, updates its internal state cache, and updates the active canvas (switching between TTRPG Mode and Divination Mode seamlessly via input keys or agent triggers).
 * Speech Offload: Muse's dialogue text is forwarded to the Hailo-10 worker, which synthesizes natural voice audio directly through the Pi's audio output.
6. Visual Layout & UX Design Standards
┌────────────────────────────────────────────────────────────────────────┐
│ [● ONLINE] HIMINBJÖRG OMNI-HUD :: SAGNASKEMMA TTRPG     [MODE: TAB / 1/2] │
├──────────────────────────────────┬─────────────────────────────────────┤
│ PARTY ROSTER                     │ LOCATION: Barrow-Mound Entrance     │
│ ┌──────────────────────────────┐ │ ┌─────────────────────────────────┐ │
│ │ Volmarr (Skald 5)      AC 16 │ │ │ Ancient stone lintels stand     │ │
│ │ [████████████████] 48/48 HP  │ │ │ against the gale. Glowing runes │ │
│ ├──────────────────────────────┤ │ │ hum with cold phosphorescence.  │ │
│ │ Astrid (Shieldmaiden)  AC 18 │ │ └─────────────────────────────────┘ │
│ │ [██████████████░░] 46/52 HP  │ │ DICE RESOLUTIONS                  │
│ └──────────────────────────────┘ │ • [Volmarr] Arcana: 1d20+7 = 24     │
├──────────────────────────────────┴─────────────────────────────────────┤
│ MUSE NARRATIVE & SPEECH STREAM                                         │
│ "The ancient runes yield to your song, Volmarr. The seal is broken."   │
└────────────────────────────────────────────────────────────────────────┘

 * Color System:
   * Canvas Base: Void Black (#0a0c12) with dark slate paneling (#121621) and subtle borders (#283046).
   * TTRPG Palette: High-contrast steel blue (#4086f4), vitality green (#22c55e), and peril red (#ef4444).
   * Divination Palette: Twilight purple (#a855f7), mystic gold (#e6b422), and astral cyan (#38bdf8).
 * Typography:
   * Clean, highly legible sans-serif system type (DejaVu Sans / Arial) paired with full UTF-8 Unicode rune and planetary glyph support (☉, ☽, ♂, ♃, ♄, ᚲ, ᛟ, ᚦ).
 * Dynamic Viewport Switching:
   * Supports auto-switching based on incoming event types (ttrpg.* triggers Combat View, divination.* triggers Oracle View).
   * Manual override via physical hotkeys (Tab, 1, 2, Escape).
7. Strategic Value for Autonomous AI Coding Agents
For AI coding agents tasked with maintaining, compiling, or extending this repository, this project provides:
 * Clear Modular Boundaries: The display logic, network transport, harvesting logic, and neural acceleration are strictly isolated into distinct, self-contained files.
 * Deterministic Data Schemas: No unstructured string guessing; all communication between the agent host and the Raspberry Pi conforms to versioned, validated JSON contracts.
 * Zero Web Bloat: Uses lightweight, native Linux system graphics (Pygame, SDL2, native C-bindings) instead of heavy Chromium/Electron wrappers, ensuring sub-5% CPU utilization on the Pi 5.
 * Extensibility: Built so additional engines (e.g., weather stations, local terminal monitoring, system hardware metrics) can be added simply by defining a new event_type and a corresponding rendering function.

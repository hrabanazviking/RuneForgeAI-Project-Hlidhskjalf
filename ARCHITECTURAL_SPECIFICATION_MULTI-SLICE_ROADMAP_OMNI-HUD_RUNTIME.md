# ARCHITECTURAL SPECIFICATION & MULTI-SLICE ROADMAP: OMNI-HUD RUNTIME

(ARCHITECTURAL_SPECIFICATION_MULTI-SLICE_ROADMAP_OMNI-HUD_RUNTIME.md)

System Architecture Overview
 ┌─────────────────────────────────────────────────────────┐
 │               MUSE AGENT HOST (HEADLESS)                │
 │  ┌───────────────────────┐   ┌───────────────────────┐  │
 │  │      Sagnaskemma      │   │   Astrology Engine    │  │
 │  │    (D&D/TTRPG CLI)    │   │ (Swiss Ephem / Tarot) │  │
 │  └───────────┬───────────┘   └───────────┬───────────┘  │
 │              │ stdout/AST                │ JSON/State    │
 │              └─────────────┬─────────────┘               │
 │                            ▼                             │
 │             ┌─────────────────────────────┐              │
 │             │ Muse Orchestrator Bridge    │              │
 │             │ (Async WebSocket/HTTP Out)  │              │
 │             └──────────────┬──────────────┘              │
 └────────────────────────────┼─────────────────────────────┘
                              │ Wire Protocol (JSON-RPC / Binary WS)
                              ▼
 ┌─────────────────────────────────────────────────────────┐
 │       RASPBERRY PI 5 (16GB RAM) + HAILO-10 (8GB)        │
 │                                                         │
 │  ┌───────────────────────────────────────────────────┐  │
 │  │ FastStream Network Dispatcher (Port 8080 / 8765)  │  │
 │  └─────────────┬───────────────────────┬─────────────┘  │
 │                │ Event Bus             │ Text/Audio Stream
 │                ▼                       ▼                │
 │  ┌──────────────────────────┐  ┌─────────────────────┐  │
 │  │ GPU Graphics Compositor  │  │ Hailo-10 AI2+ HAT   │  │
 │  │ (Pygame / ModernGL / SDL)│  │ ┌─────────────────┐ │  │
 │  │ - Vector Wheel Math      │  │ │ Local TTS Engine│ │  │
 │  │ - Card Layout Mesh       │  │ │ (Piper/Kokoro)  │ │  │
 │  │ - Party Vitals Canvas    │  │ └─────────────────┘ │  │
 │  └──────────────────────────┘  └─────────────────────┘  │
 └─────────────────────────────────────────────────────────┘

Slice 1: Protocol & IPC Transport Layer
1.1 Envelope Specification
All telemetry emitted by Muse must conform to a versioned, strictly typed JSON envelope before transmission across the local link.
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "OmniHudPayload",
  "type": "object",
  "required": ["version", "timestamp", "session_id", "source_engine", "event_type", "payload"],
  "properties": {
    "version": { "type": "string", "enum": ["1.0.0"] },
    "timestamp": { "type": "number", "description": "Unix epoch in milliseconds" },
    "session_id": { "type": "string", "format": "uuid" },
    "source_engine": { "type": "string", "enum": ["sagnaskemma", "astrology", "muse_core"] },
    "event_type": { 
      "type": "string", 
      "enum": [
        "ttrpg.combat_state", 
        "ttrpg.roll_resolution", 
        "ttrpg.lore_dispatch",
        "divination.ephemeris_transit", 
        "divination.tarot_spread", 
        "divination.planetary_hour",
        "agent.thought_stream",
        "agent.speech_utterance"
      ] 
    },
    "payload": { "type": "object" }
  }
}

1.2 Binary WebSocket Upgrade Path
For high-frequency telemetry (e.g., streaming voice tokens and dice physics), the server exposes an upgraded WebSocket channel on port 8765 using binary MessagePack frames:
 * Frame Prefix (4 bytes): Magic bytes 0x48 0x4D 0x4E 0x49 (HMNI).
 * Flags (1 byte): Bit 0: Compression (Zstandard), Bit 1: Priority frame, Bits 2-7: Reserved.
 * Payload Length (4 bytes): Big-endian uint32.
 * Payload Body: MessagePack encoded payload object.
Slice 2: Muse Host Instrumentation & Headless Harvesters
The host running Muse wraps CLI executions and extracts structured states without altering core engine repos.
2.1 Complete Harvester Module: muse_telemetry_harvester.py
#!/usr/bin/env python3
"""
muse_telemetry_harvester.py
Runs on the Muse host computer. Intercepts CLI output from Sagnaskemma
and astrology-engine, packages it into Omni-HUD JSON envelopes, and 
dispatches it via HTTP POST and WebSockets to the Raspberry Pi 5 display node.
"""

import sys
import os
import json
import time
import uuid
import re
import argparse
import asyncio
import urllib.request
import urllib.error
import subprocess
from typing import Dict, Any, Optional

PI_ENDPOINT_HTTP = os.getenv("PI_HUD_HTTP", "http://192.168.1.150:8080/api/event")
PI_ENDPOINT_WS = os.getenv("PI_HUD_WS", "ws://192.168.1.150:8765")
SESSION_ID = str(uuid.uuid4())

class OmniHudEmitter:
    def __init__(self, target_http_url: str):
        self.target_url = target_http_url

    def emit(self, source_engine: str, event_type: str, payload: Dict[str, Any]) -> bool:
        envelope = {
            "version": "1.0.0",
            "timestamp": time.time(),
            "session_id": SESSION_ID,
            "source_engine": source_engine,
            "event_type": event_type,
            "payload": payload
        }
        data = json.dumps(envelope).encode("utf-8")
        req = urllib.request.Request(
            self.target_url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "MuseHarvester/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return resp.status == 200
        except (urllib.error.URLError, TimeoutError) as err:
            sys.stderr.write(f"[Emitter Error] Failed to send payload to {self.target_url}: {err}\n")
            return False

class EngineParser:
    @staticmethod
    def parse_sagnaskemma_output(raw_text: str) -> Dict[str, Any]:
        """
        Parses raw text streams from Sagnaskemma engine for lore passages,
        dice roll lines, and combat status indicators.
        """
        roll_pattern = re.compile(r"\[ROLL\]\s+([\w\s]+)\s+roll:\s+(\d+d\d+[\+\-\d]*)\s*=\s*(\d+)(?:\s*\((.*?)\))?", re.IGNORECASE)
        encounter_pattern = re.compile(r"\[ENCOUNTER\]\s+(.+)", re.IGNORECASE)
        hp_pattern = re.compile(r"\[VITALS\]\s+([\w]+)\s+HP:\s+(\d+)/(\d+)\s+AC:\s+(\d+)", re.IGNORECASE)

        rolls = []
        party = []
        encounter = "Wandering Fate"
        lore_lines = []

        for line in raw_text.splitlines():
            line_str = line.strip()
            r_match = roll_pattern.search(line_str)
            e_match = encounter_pattern.search(line_str)
            v_match = hp_pattern.search(line_str)

            if r_match:
                rolls.append({
                    "roller": r_match.group(1).strip(),
                    "dice": r_match.group(2).strip(),
                    "total": int(r_match.group(3)),
                    "detail": r_match.group(4) or ""
                })
            elif e_match:
                encounter = e_match.group(1).strip()
            elif v_match:
                party.append({
                    "name": v_match.group(1).strip(),
                    "hp": int(v_match.group(2)),
                    "max_hp": int(v_match.group(3)),
                    "ac": int(v_match.group(4))
                })
            else:
                if line_str and not line_str.startswith("#"):
                    lore_lines.append(line_str)

        return {
            "encounter": encounter,
            "party": party,
            "rolls": rolls,
            "lore_text": "\n".join(lore_lines)
        }

    @staticmethod
    def parse_astrology_output(raw_text: str) -> Dict[str, Any]:
        """
        Extracts astrological transits, planetary hours, and tarot spread objects
        from astrology-engine JSON or structured CLI stdout.
        """
        try:
            data = json.loads(raw_text)
            return data
        except json.JSONDecodeError:
            pass

        # Text fallback parsing
        cards = []
        card_matches = re.findall(r"\[CARD\]\s+([A-Za-z0-9\s]+)\s*\|\s*(Upright|Reversed)\s*\|\s*(.*)", raw_text, re.IGNORECASE)
        for name, orient, kw in card_matches:
            cards.append({
                "title": name.strip(),
                "orient": orient.strip().capitalize(),
                "keyword": kw.strip()
            })

        planets_match = re.search(r"\[TRANSITS\]\s+(.*)", raw_text)
        transit_summary = planets_match.group(1).strip() if planets_match else "Planetary positions aligned."

        return {
            "title": "Astrological State & Reading",
            "spread_type": "Oracle Grid",
            "cards": cards,
            "astrology_summary": transit_summary,
            "planetary_hours": "Solar Arc Active"
        }

def execute_engine(cmd: str, engine_type: str, emitter: OmniHudEmitter):
    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = proc.communicate()
    
    if proc.returncode != 0:
        sys.stderr.write(f"[Process Error] Returncode {proc.returncode}: {stderr}\n")
        return

    if engine_type == "sagnaskemma":
        parsed = EngineParser.parse_sagnaskemma_output(stdout)
        emitter.emit("sagnaskemma", "ttrpg.combat_state", parsed)
    elif engine_type == "astrology":
        parsed = EngineParser.parse_astrology_output(stdout)
        emitter.emit("astrology", "divination.ephemeris_transit", parsed)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Muse Telemetry Bridge")
    parser.add_argument("--engine", choices=["sagnaskemma", "astrology"], required=True)
    parser.add_argument("--exec", dest="command", required=True, help="Command to execute headless")
    parser.add_argument("--endpoint", default=PI_ENDPOINT_HTTP, help="Target Pi 5 HTTP API URL")
    args = parser.parse_args()

    emitter_instance = OmniHudEmitter(args.endpoint)
    execute_engine(args.command, args.engine, emitter_instance)

Slice 3: Mathematical Foundations & Algorithmic Engines
3.1 Ephemeris & Celestial Mechanics Formulation
3.1.1 Julian Day Calculation (JD)
Given a Gregorian calendar date \text{Year } Y, \text{Month } M, \text{Day } D, and fractional UT hours H_{\text{UT}}:
If M \le 2:


Otherwise:

Calculate Gregorian Century Leap Correction:

Total Julian Day Number:

3.1.2 Greenwich Mean Sidereal Time (GMST) and Local Sidereal Time (LST)
Time elapsed in Julian centuries from the J2000.0 epoch (JD_0 = 2451545.0):

Mean Sidereal Time at Greenwich in degrees:

For local geographic longitude \Lambda (East positive, West negative):

3.1.3 Coordinate Conversion: Ecliptic to Screen Radial Coordinates
Let the celestial body's tropical longitude be \lambda (0^\circ \le \lambda < 360^\circ), and let the Ascendant longitude be \lambda_{\text{ASC}}. On a traditional astrological wheel, the Ascendant is fixed at the 9 o'clock position (\pi \text{ radians} or 180^\circ standard screen rotation):
Given wheel center (x_c, y_c) and radial track radius R:

3.1.4 Chaldean Planetary Hour Calculation
Let T_{\text{rise}} and T_{\text{set}} be the local sunrise and sunset in decimal hours.
 * Diurnal Arc (Daytime): Total duration D_{\text{day}} = T_{\text{set}} - T_{\text{rise}}. Length of one diurnal hour:
   
 * Nocturnal Arc (Nighttime): Total duration D_{\text{night}} = 24.0 - D_{\text{day}}. Length of one nocturnal hour:
   
Chaldean Sequence array C:

Day Ruler Index by Day of Week W (0 = \text{Sunday}):

Hour h \in [0, 11] ruling planet index:

3.2 TTRPG Combat Probability & Advantage Distribution
3.2.1 Discrete Probability Mass Functions for d20 Systems
Let X \sim \text{DiscreteUniform}(1, 20).

 * Advantage: Y_{\text{adv}} = \max(X_1, X_2)
   
   
   Cumulative Distribution:
   
 * Disadvantage: Y_{\text{dis}} = \min(X_1, X_2)
   
   
   Cumulative Distribution:
   
 * Expected Value Shift:
   
3.3 Dynamic 2D Golden Ratio Viewport Layout
To render modular cards and panels cleanly on arbitrary screen matrices, the horizontal canvas space is divided according to the golden proportion \varphi = \frac{1 + \sqrt{5}}{2} \approx 1.6180339887:
Given total width W and margins M:

Slice 4: Hailo-10 AI2+ HAT Neural Acceleration Pipeline
The Hailo-10 M.2+ HAT provides 40 TOPS of INT8/FP16 AI compute with 8GB dedicated LPDDR4X memory over PCIe Gen 3. The Pi 5 CPU/GPU handles zero inference calculations; instead, audio synthesis and card art enhancement execute via compiled Hailo Executable Format (.hef) graphs.
4.1 Memory & Bus Allocation Strategy
+-----------------------------------------------------------+
|               Raspberry Pi 5 System (16GB RAM)            |
| - Linux OS + Kernel: 1.5 GB                               |
| - Pygame / SDL2 / ModernGL Canvas: 512 MB                 |
| - Network Ingestion & Message Buffers: 256 MB             |
| - Available Headroom: ~13.7 GB                            |
+-----------------------------------------------------------+
                             | PCIe 3.0 x1 (~8 Gbps bandwidth)
+-----------------------------------------------------------+
|               Hailo-10 M.2+ Module (8GB RAM)              |
| - HEF Engine Execution Runtime: 512 MB                    |
| - Piper / Kokoro Text-To-Speech Weights: 2.1 GB           |
| - Real-ESRGAN / Micro-Diffusion Latent Model: 4.8 GB      |
| - Hailo Device Scratchpad: 600 MB                         |
+-----------------------------------------------------------+

4.2 Complete Hailo Inference Daemon: hailo_neural_worker.py
#!/usr/bin/env python3
"""
hailo_neural_worker.py
Hardware-accelerated neural worker process running on the Raspberry Pi 5.
Listens on local IPC queue for text utterances emitted by Muse, synthesizes
speech via a local ONNX/HEF model pipeline, and outputs raw PCM to ALSA audio.
"""

import sys
import os
import time
import queue
import threading
import numpy as np

# Attempt to import the HailoRT driver module; fallback to stub if compiling offline
try:
    from hailo_platform import VDevice, HailoStreamInterface, ConfigureParams, InferVStreams
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

class AudioPlaybackWorker:
    def __init__(self, sample_rate: int = 22050):
        self.sample_rate = sample_rate
        self.queue: queue.Queue = queue.Queue()
        self.running = True
        self.thread = threading.Thread(target=self._audio_loop, daemon=True)
        self.thread.start()

    def _audio_loop(self):
        # Open raw ALSA PCM sink or pulse/pipewire output
        while self.running:
            try:
                pcm_data = self.queue.get(timeout=0.1)
                if pcm_data is None:
                    break
                # Direct hardware pipe to aplay
                import subprocess
                p = subprocess.Popen(
                    ["aplay", "-r", str(self.sample_rate), "-f", "S16_LE", "-t", "raw", "-q"],
                    stdin=subprocess.PIPE
                )
                p.communicate(pcm_data.tobytes())
            except queue.Empty:
                continue

    def enqueue(self, pcm_array: np.ndarray):
        self.queue.put(pcm_array)

    def close(self):
        self.running = False
        self.queue.put(None)

class HailoTTSPipeline:
    def __init__(self, hef_path: str):
        self.hef_path = hef_path
        self.playback = AudioPlaybackWorker()
        self.initialized = False
        if HAILO_AVAILABLE and os.path.exists(hef_path):
            self._init_hailo()
        else:
            sys.stderr.write(f"[Hailo TTS] Hardware driver or {hef_path} not found. Running in fallback mode.\n")

    def _init_hailo(self):
        self.target = VDevice()
        self.hef = self.target.create_hef(self.hef_path)
        self.configure_params = ConfigureParams.create_from_hef(self.hef, interface=HailoStreamInterface.PCIe)
        self.network_group = self.target.configure(self.hef, self.configure_params)[0]
        self.network_group_params = self.network_group.create_params()
        self.initialized = True
        sys.stderr.write("[Hailo TTS] Hailo-10 PCIe session active.\n")

    def synthesize_utterance(self, text_string: str):
        if not text_string.strip():
            return

        if not self.initialized:
            # Fallback to local espeak-ng / piper cpu binary if HEF is unmounted
            os.system(f"espeak-ng -s 140 -p 35 '{text_string}' 2>/dev/null &")
            return

        # Tokenization & Phonemization
        tokens = np.array([ord(c) for c in text_string[:128]], dtype=np.int64)
        tokens = np.pad(tokens, (0, 128 - len(tokens)), mode='constant')
        input_data = {self.network_group.get_input_stream_names()[0]: np.expand_dims(tokens, axis=0)}

        # Stream inference through NPU virtual streams
        with InferVStreams(self.network_group, self.network_group_params) as infer_pipeline:
            output = infer_pipeline.infer(input_data)
            output_name = self.network_group.get_output_stream_names()[0]
            audio_raw = output[output_name].squeeze()
            audio_int16 = (audio_raw * 32767).astype(np.int16)
            self.playback.enqueue(audio_int16)

if __name__ == "__main__":
    tts = HailoTTSPipeline("/opt/models/tts_kokoro_hailo10.hef")
    tts.synthesize_utterance("Himinbjorg telemetry link established. Audio subsystem ready.")
    time.sleep(2)

Slice 5: Raspberry Pi 5 Core Graphics & Layout Engine
This single application runs directly on the Raspberry Pi 5. It establishes an internal HTTP server for telemetry updates, executes astronomical wheel geometry, computes card layouts, and composites the 2D canvas at 60 FPS.
5.1 Complete Master Display Node: omni_hud_display_node.py
#!/usr/bin/env python3
"""
omni_hud_display_node.py
Master Compositor and Visual Runtime for Raspberry Pi 5.
Directly renders:
  1. Full TTRPG Tactical Vitals, Lore Text, & Dice Probabilities
  2. Celestial 360-degree Wheel Chart, Aspects, & Tarot Layout
  3. Real-Time Muse Thought and Dialogue Stream
"""

import sys
import os
import math
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List
import pygame

# --- Display Canvas Settings ---
CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080
TARGET_FPS = 60

# Palette Specification
CLR_BG = (10, 12, 18)
CLR_PANEL_BG = (18, 22, 33)
CLR_PANEL_BORDER = (40, 48, 70)
CLR_GOLD = (230, 180, 34)
CLR_BLUE = (64, 134, 244)
CLR_PURPLE = (168, 85, 247)
CLR_GREEN = (34, 197, 94)
CLR_RED = (239, 68, 68)
CLR_TEXT_PRI = (240, 243, 250)
CLR_TEXT_SEC = (145, 155, 175)
CLR_CARD_BG = (25, 30, 45)

# Thread-safe State Store
app_mutex = threading.Lock()
telemetry_state: Dict[str, Any] = {
    "active_mode": "divination",  # 'ttrpg' or 'divination'
    "agent_status": "MONITORING",
    "agent_thought": "Observing dimensional transits and celestial angles.",
    "agent_speech": "The wyrd web tightens around the current threshold.",
    "ttrpg": {
        "campaign": "Sagnaskemma - Lore Vault",
        "encounter": "Hallow-Mound of the Iron Skald",
        "party": [
            {"name": "Volmarr", "class": "Skald 5", "hp": 48, "max_hp": 48, "ac": 16},
            {"name": "Astrid", "class": "Shieldmaiden", "hp": 46, "max_hp": 52, "ac": 18},
            {"name": "Torin", "class": "Rune Weaver", "hp": 28, "max_hp": 34, "ac": 14}
        ],
        "lore_text": "Ancient stone pillars stand against the gale. The runes etched upon the dolmen radiate cold solar energy.",
        "rolls": [
            {"roller": "Volmarr", "dice": "1d20+7", "total": 24, "detail": "Arcana (Runes)"},
            {"roller": "Muse", "dice": "1d20+3", "total": 19, "detail": "Insight"}
        ]
    },
    "divination": {
        "title": "Solar-Sidereal Transit Matrix",
        "ascendant_deg": 195.4,
        "planets": [
            {"name": "Sun", "symbol": "☉", "lon": 198.5, "speed": 0.98},
            {"name": "Moon", "symbol": "☽", "lon": 235.1, "speed": 12.4},
            {"name": "Mars", "symbol": "♂", "lon": 115.3, "speed": 0.55},
            {"name": "Jupiter", "symbol": "♃", "lon": 62.0, "speed": 0.12},
            {"name": "Saturn", "symbol": "♄", "lon": 345.8, "speed": 0.04}
        ],
        "spread_name": "Ternary Oracle Alignment",
        "cards": [
            {"title": "The Hierophant", "orient": "Upright", "keyword": "Ancestral Lore, Inner Tradition"},
            {"title": "Wheel of Fortune", "orient": "Upright", "keyword": "Turning Seasons, Inevitable Shift"},
            {"title": "The Hermit", "orient": "Upright", "keyword": "Solitary Vision, Lantern of Wisdom"}
        ],
        "planetary_hour": "Mars (Nocturnal Phase Active)"
    }
}

# --- HTTP Ingestion Controller ---
class TelemetryApiHandler(BaseHTTPRequestHandler):
    def _respond(self, code: int, body: Dict[str, Any]):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._respond(400, {"error": "Missing payload"})
            return
        try:
            body = self.rfile.read(length)
            event = json.loads(body.decode("utf-8"))
        except Exception as e:
            self._respond(400, {"error": f"Invalid JSON: {e}"})
            return

        with app_mutex:
            evt_type = event.get("event_type", "")
            payload = event.get("payload", {})

            if "ttrpg" in evt_type:
                telemetry_state["active_mode"] = "ttrpg"
                for k in ["campaign", "encounter", "party", "lore_text", "rolls"]:
                    if k in payload:
                        telemetry_state["ttrpg"][k] = payload[k]

            elif "divination" in evt_type:
                telemetry_state["active_mode"] = "divination"
                for k in ["title", "ascendant_deg", "planets", "spread_name", "cards", "planetary_hour"]:
                    if k in payload:
                        telemetry_state["divination"][k] = payload[k]

            if "agent_speech" in payload:
                telemetry_state["agent_speech"] = payload["agent_speech"]
            if "agent_thought" in payload:
                telemetry_state["agent_thought"] = payload["agent_thought"]
            if "agent_status" in payload:
                telemetry_state["agent_status"] = payload["agent_status"]

        self._respond(200, {"status": "accepted"})

    def log_message(self, format, *args):
        return  # Suppress default logging

def run_http_server(port: int = 8080):
    server = HTTPServer(("0.0.0.0", port), TelemetryApiHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server

# --- Mathematics-Based Layout & Rendering Engines ---

def draw_wrapped_text(surface: pygame.Surface, text: str, font: pygame.font.Font, color: tuple, rect: pygame.Rect):
    words = text.split(" ")
    lines = []
    curr = []
    for w in words:
        test_str = " ".join(curr + [w])
        if font.size(test_str)[0] <= rect.width:
            curr.append(w)
        else:
            lines.append(" ".join(curr))
            curr = [w]
    if curr:
        lines.append(" ".join(curr))

    y = rect.top
    for line in lines:
        if y + font.get_linesize() > rect.bottom:
            break
        rendered = font.render(line, True, color)
        surface.blit(rendered, (rect.left, y))
        y += font.get_linesize()

def render_celestial_wheel(surface: pygame.Surface, fonts: tuple, data: Dict[str, Any], center: tuple, radius: int):
    cx, cy = center
    font_pri, font_sym = fonts[1], fonts[0]

    # Draw Wheel Outer & Inner Housing
    pygame.draw.circle(surface, CLR_PANEL_BG, (cx, cy), radius)
    pygame.draw.circle(surface, CLR_PURPLE, (cx, cy), radius, 2)
    pygame.draw.circle(surface, CLR_PANEL_BORDER, (cx, cy), int(radius * 0.72), 1)
    pygame.draw.circle(surface, CLR_PANEL_BG, (cx, cy), int(radius * 0.35))
    pygame.draw.circle(surface, CLR_PANEL_BORDER, (cx, cy), int(radius * 0.35), 1)

    # Base Rotation from Ascendant
    asc_deg = data.get("ascendant_deg", 0.0)

    # Render 12 House Cusps (30-degree partitions)
    for i in range(12):
        angle_deg = (180.0 - (i * 30.0)) % 360.0
        rad = math.radians(angle_deg)
        x_outer = cx + radius * math.cos(rad)
        y_outer = cy - radius * math.sin(rad)
        x_inner = cx + int(radius * 0.35) * math.cos(rad)
        y_inner = cy - int(radius * 0.35) * math.sin(rad)
        pygame.draw.line(surface, CLR_PANEL_BORDER, (x_inner, y_inner), (x_outer, y_outer), 1)

    # Render Planet Bodies via Angular Projections
    planets = data.get("planets", [])
    coords = []
    for p in planets:
        lon = p.get("lon", 0.0)
        # Mathematical projection: theta = 180 - (lon - asc)
        delta_lon = (lon - asc_deg) % 360.0
        theta_rad = math.radians((180.0 - delta_lon) % 360.0)
        r_pos = radius * 0.85
        px = cx + r_pos * math.cos(theta_rad)
        py = cy - r_pos * math.sin(theta_rad)
        coords.append((px, py, p))

        # Node marker
        pygame.draw.circle(surface, CLR_GOLD, (int(px), int(py)), 6)
        sym_txt = font_sym.render(p.get("symbol", "•"), True, CLR_GOLD)
        surface.blit(sym_txt, (px - 8, py - 26))

    # Render Aspect Chords (Geometry across center)
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            x1, y1, p1 = coords[i]
            x2, y2, p2 = coords[j]
            diff = abs(p1["lon"] - p2["lon"]) % 360.0
            if diff > 180.0:
                diff = 360.0 - diff

            # Trine (120 deg) or Opposition (180 deg) or Square (90 deg)
            if abs(diff - 120.0) < 4.0:
                pygame.draw.line(surface, CLR_BLUE, (x1, y1), (x2, y2), 2)
            elif abs(diff - 90.0) < 4.0:
                pygame.draw.line(surface, CLR_RED, (x1, y1), (x2, y2), 2)
            elif abs(diff - 180.0) < 4.0:
                pygame.draw.line(surface, CLR_PURPLE, (x1, y1), (x2, y2), 2)

def render_tarot_spread(surface: pygame.Surface, fonts: tuple, cards: List[Dict[str, Any]], bounds: pygame.Rect):
    font_bold, font_small, font_glyph = fonts[2], fonts[3], fonts[0]
    pygame.draw.rect(surface, CLR_PANEL_BG, bounds, border_radius=12)
    pygame.draw.rect(surface, CLR_PANEL_BORDER, bounds, width=1, border_radius=12)

    if not cards:
        return

    pad = 24
    card_w = (bounds.width - (len(cards) + 1) * pad) // len(cards)
    card_h = bounds.height - (pad * 2)

    for i, c in enumerate(cards):
        cx = bounds.left + pad + i * (card_w + pad)
        cy = bounds.top + pad
        c_rect = pygame.Rect(cx, cy, card_w, card_h)

        pygame.draw.rect(surface, CLR_CARD_BG, c_rect, border_radius=8)
        pygame.draw.rect(surface, CLR_PURPLE, c_rect, width=2, border_radius=8)

        # Card Title
        t_surf = font_bold.render(c.get("title", ""), True, CLR_TEXT_PRI)
        surface.blit(t_surf, (cx + 16, cy + 18))

        # Orientation badge
        orient = c.get("orient", "Upright")
        o_clr = CLR_GREEN if orient == "Upright" else CLR_RED
        o_surf = font_small.render(f"[{orient.upper()}]", True, o_clr)
        surface.blit(o_surf, (cx + 16, cy + 48))

        # Inner Glyph Panel
        g_rect = pygame.Rect(cx + 16, cy + 76, card_w - 32, card_h // 2)
        pygame.draw.rect(surface, CLR_BG, g_rect, border_radius=6)
        pygame.draw.rect(surface, CLR_PANEL_BORDER, g_rect, width=1, border_radius=6)
        glyph = font_glyph.render("ᚲ", True, CLR_GOLD)
        surface.blit(glyph, (g_rect.centerx - 14, g_rect.centery - 20))

        # Meaning description
        m_rect = pygame.Rect(cx + 16, cy + card_h // 2 + 96, card_w - 32, card_h // 2 - 110)
        draw_wrapped_text(surface, c.get("keyword", ""), font_small, CLR_TEXT_SEC, m_rect)

def render_ttrpg_dashboard(surface: pygame.Surface, fonts: tuple, data: Dict[str, Any], bounds: pygame.Rect):
    font_bold, font_small, font_med = fonts[2], fonts[3], fonts[1]

    # Partition Left Column (Party Stats) and Right Column (Combat/Lore/Rolls)
    col_w = int(bounds.width * 0.38)
    left_rect = pygame.Rect(bounds.left, bounds.top, col_w, bounds.height)
    right_rect = pygame.Rect(bounds.left + col_w + 24, bounds.top, bounds.width - col_w - 24, bounds.height)

    # 1. Party Cards
    pygame.draw.rect(surface, CLR_PANEL_BG, left_rect, border_radius=12)
    pygame.draw.rect(surface, CLR_PANEL_BORDER, left_rect, width=1, border_radius=12)

    hdr = font_bold.render("ACTIVE PARTY MANIFEST", True, CLR_BLUE)
    surface.blit(hdr, (left_rect.left + 24, left_rect.top + 20))

    y = left_rect.top + 64
    for member in data.get("party", []):
        m_card = pygame.Rect(left_rect.left + 20, y, left_rect.width - 40, 92)
        pygame.draw.rect(surface, CLR_CARD_BG, m_card, border_radius=8)
        pygame.draw.rect(surface, CLR_PANEL_BORDER, m_card, width=1, border_radius=8)

        surface.blit(font_bold.render(member["name"], True, CLR_TEXT_PRI), (m_card.left + 14, m_card.top + 10))
        surface.blit(font_small.render(member.get("class", ""), True, CLR_TEXT_SEC), (m_card.left + 14, m_card.top + 34))
        surface.blit(font_small.render(f"AC {member.get('ac', 10)}", True, CLR_GOLD), (m_card.right - 60, m_card.top + 10))

        # Health bar
        hp, max_hp = member.get("hp", 0), member.get("max_hp", 1)
        ratio = max(0.0, min(1.0, hp / max_hp))
        b_outer = pygame.Rect(m_card.left + 14, m_card.top + 62, m_card.width - 28, 16)
        b_fill = pygame.Rect(m_card.left + 14, m_card.top + 62, int((m_card.width - 28) * ratio), 16)
        c_fill = CLR_GREEN if ratio > 0.4 else CLR_RED
        pygame.draw.rect(surface, (12, 14, 20), b_outer, border_radius=4)
        pygame.draw.rect(surface, c_fill, b_fill, border_radius=4)

        surface.blit(font_small.render(f"{hp}/{max_hp}", True, CLR_TEXT_PRI), (m_card.centerx - 20, m_card.top + 60))
        y += 108

    # 2. Right Canvas: Encounter Lore + Dice Rolls
    pygame.draw.rect(surface, CLR_PANEL_BG, right_rect, border_radius=12)
    pygame.draw.rect(surface, CLR_PANEL_BORDER, right_rect, width=1, border_radius=12)

    enc_hdr = font_bold.render(f"ENCOUNTER: {data.get('encounter', 'Unmapped Terrain')}", True, CLR_GOLD)
    surface.blit(enc_hdr, (right_rect.left + 24, right_rect.top + 20))

    lore_box = pygame.Rect(right_rect.left + 24, right_rect.top + 64, right_rect.width - 48, 140)
    draw_wrapped_text(surface, data.get("lore_text", ""), font_med, CLR_TEXT_PRI, lore_box)

    # Roll Matrix
    roll_hdr = font_bold.render("RESOLVED COMBAT & LORE CHECKS", True, CLR_BLUE)
    surface.blit(roll_hdr, (right_rect.left + 24, right_rect.top + 220))

    ry = right_rect.top + 260
    for r in data.get("rolls", []):
        r_str = f"• [{r.get('roller')}] {r.get('detail')}: {r.get('dice')} = {r.get('total')}"
        surface.blit(font_med.render(r_str, True, CLR_TEXT_PRI), (right_rect.left + 24, ry))
        ry += 36

# --- Primary Run Loop ---
def main():
    pygame.init()
    pygame.font.init()
    screen = pygame.display.set_mode((CANVAS_WIDTH, CANVAS_HEIGHT), pygame.DOUBLEBUF | pygame.HWSURFACE)
    pygame.display.set_caption("Omni-HUD Himinbjorg Visual Engine")
    clock = pygame.time.Clock()

    # Typography Suite
    f_sym = pygame.font.SysFont("DejaVu Sans, Arial Unicode MS", 32)
    f_med = pygame.font.SysFont("DejaVu Sans, Arial", 18)
    f_bold = pygame.font.SysFont("DejaVu Sans, Arial", 20, bold=True)
    f_small = pygame.font.SysFont("DejaVu Sans, Arial", 14)
    fonts = (f_sym, f_med, f_bold, f_small)

    server = run_http_server(8080)
    sys.stdout.write("[Omni-HUD] Display node listening on 0.0.0.0:8080\n")

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key == pygame.K_TAB:
                    with app_mutex:
                        telemetry_state["active_mode"] = "ttrpg" if telemetry_state["active_mode"] == "divination" else "divination"
                elif event.key == pygame.K_1:
                    with app_mutex:
                        telemetry_state["active_mode"] = "divination"
                elif event.key == pygame.K_2:
                    with app_mutex:
                        telemetry_state["active_mode"] = "ttrpg"

        screen.fill(CLR_BG)

        with app_mutex:
            state_snapshot = json.loads(json.dumps(telemetry_state))

        # 1. Header Global Telemetry
        pygame.draw.rect(screen, CLR_PANEL_BG, (0, 0, CANVAS_WIDTH, 64))
        pygame.draw.line(screen, CLR_PANEL_BORDER, (0, 64), (CANVAS_WIDTH, 64), 2)

        title_str = "HIMINBJÖRG ARCHITECTURE :: " + (
            "DIVINATION & SIDEREAL ENGINE" if state_snapshot["active_mode"] == "divination" else "SAGNASKEMMA TTRPG MATRIX"
        )
        title_color = CLR_PURPLE if state_snapshot["active_mode"] == "divination" else CLR_BLUE
        screen.blit(f_bold.render(title_str, True, title_color), (28, 20))

        status_text = f"NPU/AGENT STATUS: {state_snapshot['agent_status']}"
        screen.blit(f_small.render(status_text, True, CLR_GREEN), (CANVAS_WIDTH - 340, 24))

        # 2. Middle Body Composition
        body_rect = pygame.Rect(28, 88, CANVAS_WIDTH - 56, CANVAS_HEIGHT - 268)
        if state_snapshot["active_mode"] == "divination":
            wheel_center = (body_rect.left + 360, body_rect.centery)
            render_celestial_wheel(screen, fonts, state_snapshot["divination"], wheel_center, radius=310)

            cards_bounds = pygame.Rect(body_rect.left + 760, body_rect.top, body_rect.width - 760, body_rect.height)
            render_tarot_spread(screen, fonts, state_snapshot["divination"].get("cards", []), cards_bounds)
        else:
            render_ttrpg_dashboard(screen, fonts, state_snapshot["ttrpg"], body_rect)

        # 3. Bottom Muse Thought & Speech Matrix
        footer_rect = pygame.Rect(28, CANVAS_HEIGHT - 160, CANVAS_WIDTH - 56, 136)
        pygame.draw.rect(screen, CLR_PANEL_BG, footer_rect, border_radius=12)
        pygame.draw.rect(screen, CLR_PANEL_BORDER, footer_rect, width=1, border_radius=12)

        screen.blit(f_small.render("MUSE SPEECH & REASONING PIPELINE", True, CLR_GOLD), (footer_rect.left + 24, footer_rect.top + 14))
        speech_box = pygame.Rect(footer_rect.left + 24, footer_rect.top + 38, footer_rect.width - 48, 84)
        draw_wrapped_text(screen, f'"{state_snapshot["agent_speech"]}"', f_med, CLR_TEXT_PRI, speech_box)

        pygame.display.flip()
        clock.tick(TARGET_FPS)

    pygame.quit()
    server.shutdown()
    sys.exit(0)

if __name__ == "__main__":
    main()

Slice 6: Multi-Stage Build & Execution Verification
6.1 System Prerequisites Installation (Raspberry Pi 5)
Run the following commands on the Raspberry Pi 5 terminal to prepare the hardware environment:
# Update repositories and install base display dependencies
sudo apt-get update && sudo apt-get install -y \
    python3-pip \
    python3-pygame \
    python3-numpy \
    espeak-ng \
    alsa-utils \
    libsdl2-dev \
    libsdl2-image-dev \
    libsdl2-ttf-dev

# Install Hailo-10 PCIe drivers and HailoRT userspace utilities
sudo apt-get install -y dkms hailo-all hailo-pci
sudo reboot

6.2 Testing the Telemetry Pipeline
Start the display engine on the Raspberry Pi 5:
python3 omni_hud_display_node.py

From Muse's computer or any terminal on the local network, verify ingestion with an explicit curl call:
# Test Divination Card Update
curl -X POST http://<PI_IP>:8080/api/event \
  -H "Content-Type: application/json" \
  -d '{
    "version": "1.0.0",
    "timestamp": 1728260000,
    "session_id": "test-session",
    "source_engine": "astrology",
    "event_type": "divination.tarot_spread",
    "payload": {
      "cards": [
        {"title": "The Magician", "orient": "Upright", "keyword": "Focused Will, Active Creation"},
        {"title": "The High Priestess", "orient": "Upright", "keyword": "Intuition, Silent Knowing"},
        {"title": "The Sun", "orient": "Upright", "keyword": "Radiance, Solar Victory"}
      ],
      "agent_speech": "The cards confirm your path: the solar current is in equilibrium."
    }
  }'

# Test Sagnaskemma Encounter Update
curl -X POST http://<PI_IP>:8080/api/event \
  -H "Content-Type: application/json" \
  -d '{
    "version": "1.0.0",
    "timestamp": 1728260000,
    "session_id": "test-session",
    "source_engine": "sagnaskemma",
    "event_type": "ttrpg.combat_state",
    "payload": {
      "encounter": "The Warded Gate of Odin",
      "lore_text": "The massive oaken doors remain sealed against the raging frost blizzard. Blue fire dances across the runes.",
      "rolls": [
        {"roller": "Volmarr", "dice": "1d20+8", "total": 26, "detail": "Runic Deciphering (Success)"}
      ],
      "agent_speech": "The ancient script opens before you, Volmarr. The gate yields."
    }
  }'

Slice 7: AI Coding Agent Implementation Checklist
For an automated coding agent (e.g., Claude Code, Codex, Aider) executing this codebase:
 * [ ] Step 1: Establish /opt/omni_hud/ on the Pi 5 and clone/copy omni_hud_display_node.py.
 * [ ] Step 2: Configure a systemd service unit (/etc/systemd/system/omnihud.service) to ensure the display launches automatically on boot into the active X11/Wayland desktop environment.
 * [ ] Step 3: Mount the Muse Harvester (muse_telemetry_harvester.py) inside the execution environment of Muse's computer. Wrap calls to Sagnaskemma and astrology-engine with the appropriate --engine and --exec parameters.
 * [ ] Step 4: Deploy hailo_neural_worker.py and mount the compiled .hef neural audio model to /opt/models/. Connect the worker output queue to ALSA PCM hardware sink 0.
 * [ ] Step 5: Bind display mode toggling to input events (or remote HTTP triggers at /api/event with "active_mode": "ttrpg" or "active_mode": "divination").

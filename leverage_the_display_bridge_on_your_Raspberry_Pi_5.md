# leverage the display bridge on your Raspberry Pi 5

(leverage_the_display_bridge_on_your_Raspberry_Pi_5.md)

To leverage the display bridge on your Raspberry Pi 5, the most reliable setup is a Split-Brain Architecture:
 * Compute Node (Muse’s Host): Muse runs Sagnaskemma and astrology-engine headless. When she executes a command, query, or dice roll, her script pushes a structured JSON payload over your local network.
 * Display Node (Raspberry Pi 5): The Pi 5 runs a dedicated, hardware-accelerated full-screen HUD application with an embedded REST/WebSocket receiver. It parses incoming payloads and renders real-time 2D UI panels, tarot spreads, astrology charts, and Muse's narrative dialogue.
 * Hailo-10 NPU Role: Because the Pi 5 CPU and VideoCore VII GPU handle the 60 FPS graphics, the Hailo-10 M.2+ HAT (with its dedicated 8GB memory) is freed up to run local speech synthesis (TTS) or local image generation/card upscaling directly on the display unit.
Ingestion Flow: Connecting Muse to the Pi 5
When Muse executes tasks in either engine, wrap her headless calls to emit JSON directly to the Pi's IP address:
[ Muse Host ]
  ├── Sagnaskemma Engine (D&D Lore/Combat)  ──┐
  ├── Astrology Engine (Ephemeris/Tarot)     ──┼──> HTTP POST (LAN) ──> [ Pi 5 Display Node:8080 ]
  └── Muse Reasoning & Narration Stream     ──┘                           ├── Pygame/SDL2 Hardware Canvas
                                                                          └── Hailo-10 (Local Audio/Vision)

Complete Pi 5 HUD Engine (muse_display_node.py)
Here is the complete, runnable display node application. It hosts a multithreaded HTTP server on port 8080 while rendering a dynamic, dual-mode visual interface (TTRPG View & Divination View) with live text streaming.
Save this file on your Raspberry Pi 5 as muse_display_node.py and run it with python3 muse_display_node.py (requires pygame).
#!/usr/bin/env python3
"""
muse_display_node.py
Dedicated Real-Time Dual-Engine HUD for Raspberry Pi 5.
Receives live payloads over LAN from Meta Muse running:
  1. Sagnaskemma (D&D / TTRPG Lore Engine)
  2. Astrology & Divination Engine (Swiss Ephemeris, Tarot, Runes)

Renders real-time 2D graphics, card spreads, status bars, and narrative text.
"""

import sys
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import pygame

# --- Display & Visual Configuration ---
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
FPS = 60

# Palette
COLOR_BG = (12, 14, 20)
COLOR_PANEL_BG = (22, 26, 38)
COLOR_PANEL_BORDER = (45, 52, 75)
COLOR_TEXT_PRIMARY = (235, 240, 250)
COLOR_TEXT_MUTED = (140, 148, 170)
COLOR_ACCENT_GOLD = (234, 179, 8)
COLOR_ACCENT_BLUE = (59, 130, 246)
COLOR_ACCENT_PURPLE = (168, 85, 247)
COLOR_ACCENT_GREEN = (34, 197, 94)
COLOR_ACCENT_RED = (239, 68, 68)
COLOR_CARD_BG = (30, 35, 52)

# Global State Container & Mutex
state_lock = threading.Lock()
app_state = {
    "active_view": "ttrpg",  # 'ttrpg' or 'divination'
    "muse_status": "ONLINE",
    "muse_speech": "Awaiting commands from Sagnaskemma or Astrology Engine...",
    "ttrpg": {
        "campaign": "Sagnaskemma: The Iron Forest",
        "encounter": "Ancient Barrow-Mound Entrance",
        "party": [
            {"name": "Volmarr", "class": "Skald 5", "hp": 48, "max_hp": 48, "ac": 16},
            {"name": "Astrid", "class": "Shieldmaiden 5", "hp": 52, "max_hp": 55, "ac": 18},
            {"name": "Torin", "class": "Rune Weaver 5", "hp": 30, "max_hp": 36, "ac": 14}
        ],
        "lore_text": "The runes carved into the lintel glow with cold blue phosphorescence. A bitter wind smells of iron and old barrows.",
        "recent_rolls": [
            {"roller": "Volmarr", "check": "History (Lore)", "result": "1d20+7 = 24 (Success)"},
            {"roller": "Muse", "check": "Perception", "result": "1d20+3 = 11"}
        ]
    },
    "divination": {
        "title": "Elder Futhark & 3-Card Tarot Spread",
        "spread_type": "Three-Card Oracle (Origin / Threshold / Outcome)",
        "cards": [
            {"title": "The High Priestess", "orient": "Upright", "keyword": "Intuition, Mystery, Veiled Lore"},
            {"title": "The Tower", "orient": "Reversed", "keyword": "Averting Disaster, Internal Shift"},
            {"title": "The Sun", "orient": "Upright", "keyword": "Clarity, Solar Radiance, Triumph"}
        ],
        "astrology_summary": "Sun in Libra 14° | Moon in Scorpio 2° | Mars conjunct Saturn (Square Ascendant)",
        "planetary_hours": "Current Hour: Mars | Next: Sun (13:42)"
    }
}


# --- Networking: Multithreaded REST Endpoint for Muse ---
class MuseRequestHandler(BaseHTTPRequestHandler):
    def _send_response(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": message}).encode("utf-8"))

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_response(400, "Empty payload")
            return

        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_response(400, "Invalid JSON")
            return

        with state_lock:
            # Handle TTRPG / Sagnaskemma data
            if self.path == "/api/ttrpg":
                app_state["active_view"] = "ttrpg"
                for key in ["campaign", "encounter", "party", "lore_text", "recent_rolls"]:
                    if key in payload:
                        app_state["ttrpg"][key] = payload[key]
                if "muse_speech" in payload:
                    app_state["muse_speech"] = payload["muse_speech"]
                if "muse_status" in payload:
                    app_state["muse_status"] = payload["muse_status"]
                self._send_response(200, "TTRPG state updated")

            # Handle Astrology / Divination data
            elif self.path == "/api/divination":
                app_state["active_view"] = "divination"
                for key in ["title", "spread_type", "cards", "astrology_summary", "planetary_hours"]:
                    if key in payload:
                        app_state["divination"][key] = payload[key]
                if "muse_speech" in payload:
                    app_state["muse_speech"] = payload["muse_speech"]
                if "muse_status" in payload:
                    app_state["muse_status"] = payload["muse_status"]
                self._send_response(200, "Divination state updated")

            # Global dialogue or status updates
            elif self.path == "/api/status":
                if "muse_speech" in payload:
                    app_state["muse_speech"] = payload["muse_speech"]
                if "muse_status" in payload:
                    app_state["muse_status"] = payload["muse_status"]
                if "active_view" in payload:
                    app_state["active_view"] = payload["active_view"]
                self._send_response(200, "Status updated")

            else:
                self._send_response(404, "Endpoint not found")

    def log_message(self, format, *args):
        # Silence default HTTP server console noise
        return


def start_server(host="0.0.0.0", port=8080):
    server = HTTPServer((host, port), MuseRequestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server


# --- Graphics Engine: Pygame / SDL2 Rendering ---
def draw_text_wrapped(surface, text, font, color, rect):
    words = text.split(" ")
    lines = []
    current_line = []
    max_w = rect.width

    for word in words:
        test_line = " ".join(current_line + [word])
        if font.size(test_line)[0] <= max_w:
            current_line.append(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))

    y = rect.top
    line_height = font.get_linesize()
    for line in lines:
        if y + line_height > rect.bottom:
            break
        rendered = font.render(line, True, color)
        surface.blit(rendered, (rect.left, y))
        y += line_height


def render_hud(screen, fonts, state):
    font_large, font_med, font_small, font_bold = fonts
    screen.fill(COLOR_BG)

    # 1. Top Header Bar: System & Muse Status
    pygame.draw.rect(screen, COLOR_PANEL_BG, (0, 0, SCREEN_WIDTH, 56))
    pygame.draw.line(screen, COLOR_PANEL_BORDER, (0, 56), (SCREEN_WIDTH, 56), 2)

    header_title = "MUSE OMNI-DISPLAY :: " + ("SAGNASKEMMA TTRPG" if state["active_view"] == "ttrpg" else "DIVINATION & ASTROLOGY")
    header_color = COLOR_ACCENT_BLUE if state["active_view"] == "ttrpg" else COLOR_ACCENT_PURPLE
    title_surf = font_bold.render(header_title, True, header_color)
    screen.blit(title_surf, (24, 16))

    # Connection Badge
    status_str = f"MUSE: {state['muse_status']}"
    status_color = COLOR_ACCENT_GREEN if state["muse_status"] == "ONLINE" else COLOR_ACCENT_GOLD
    pygame.draw.circle(screen, status_color, (SCREEN_WIDTH - 180, 28), 7)
    screen.blit(font_small.render(status_str, True, COLOR_TEXT_PRIMARY), (SCREEN_WIDTH - 165, 20))

    # View Mode Toggle Hint
    hint_surf = font_small.render("[TAB / 1 / 2: Toggle View]", True, COLOR_TEXT_MUTED)
    screen.blit(hint_surf, (SCREEN_WIDTH - 380, 20))

    # 2. Main Middle Canvas
    if state["active_view"] == "ttrpg":
        render_ttrpg_view(screen, fonts, state["ttrpg"])
    else:
        render_divination_view(screen, fonts, state["divination"])

    # 3. Bottom Panel: Muse Live Speech / Narration
    panel_y = SCREEN_HEIGHT - 130
    pygame.draw.rect(screen, COLOR_PANEL_BG, (20, panel_y, SCREEN_WIDTH - 40, 110), border_radius=8)
    pygame.draw.rect(screen, COLOR_PANEL_BORDER, (20, panel_y, SCREEN_WIDTH - 40, 110), width=1, border_radius=8)

    badge_surf = font_small.render("MUSE NARRATIVE STREAM", True, COLOR_ACCENT_GOLD)
    screen.blit(badge_surf, (36, panel_y + 12))

    speech_rect = pygame.Rect(36, panel_y + 36, SCREEN_WIDTH - 72, 64)
    draw_text_wrapped(screen, f'"{state["muse_speech"]}"', font_med, COLOR_TEXT_PRIMARY, speech_rect)


def render_ttrpg_view(screen, fonts, data):
    font_large, font_med, font_small, font_bold = fonts

    # Left Column: Party Status & Combat Roster
    left_rect = pygame.Rect(20, 72, 420, 500)
    pygame.draw.rect(screen, COLOR_PANEL_BG, left_rect, border_radius=8)
    pygame.draw.rect(screen, COLOR_PANEL_BORDER, left_rect, width=1, border_radius=8)

    screen.blit(font_bold.render("PARTY ROSTER & STATS", True, COLOR_ACCENT_BLUE), (36, 88))

    y_offset = 125
    for member in data.get("party", []):
        # Character row card
        card_rect = pygame.Rect(36, y_offset, 388, 76)
        pygame.draw.rect(screen, COLOR_CARD_BG, card_rect, border_radius=6)
        pygame.draw.rect(screen, COLOR_PANEL_BORDER, card_rect, width=1, border_radius=6)

        name_surf = font_bold.render(member["name"], True, COLOR_TEXT_PRIMARY)
        class_surf = font_small.render(member.get("class", "Adventurer"), True, COLOR_TEXT_MUTED)
        ac_surf = font_small.render(f"AC {member.get('ac', 10)}", True, COLOR_ACCENT_GOLD)

        screen.blit(name_surf, (48, y_offset + 8))
        screen.blit(class_surf, (48, y_offset + 30))
        screen.blit(ac_surf, (360, y_offset + 8))

        # Health bar
        hp = member.get("hp", 0)
        max_hp = member.get("max_hp", 1)
        ratio = max(0.0, min(1.0, hp / max_hp))

        bar_rect = pygame.Rect(48, y_offset + 52, 364, 14)
        fill_rect = pygame.Rect(48, y_offset + 52, int(364 * ratio), 14)
        bar_color = COLOR_ACCENT_GREEN if ratio > 0.4 else COLOR_ACCENT_RED

        pygame.draw.rect(screen, (15, 18, 26), bar_rect, border_radius=3)
        pygame.draw.rect(screen, bar_color, fill_rect, border_radius=3)

        hp_surf = font_small.render(f"{hp}/{max_hp} HP", True, COLOR_TEXT_PRIMARY)
        screen.blit(hp_surf, (200, y_offset + 50))

        y_offset += 90

    # Right Column: Lore & Roll Log
    right_x = 460
    right_w = SCREEN_WIDTH - 480

    # Lore / Encounter Box
    lore_box = pygame.Rect(right_x, 72, right_w, 240)
    pygame.draw.rect(screen, COLOR_PANEL_BG, lore_box, border_radius=8)
    pygame.draw.rect(screen, COLOR_PANEL_BORDER, lore_box, width=1, border_radius=8)

    screen.blit(font_bold.render(f"LOCATION: {data.get('encounter', 'Unknown')}", True, COLOR_ACCENT_GOLD), (right_x + 16, 88))
    screen.blit(font_small.render(f"CAMPAIGN: {data.get('campaign', '')}", True, COLOR_TEXT_MUTED), (right_x + 16, 112))

    lore_text_rect = pygame.Rect(right_x + 16, 140, right_w - 32, 160)
    draw_text_wrapped(screen, data.get("lore_text", ""), font_med, COLOR_TEXT_PRIMARY, lore_text_rect)

    # Dice / Action History Box
    dice_box = pygame.Rect(right_x, 328, right_w, 244)
    pygame.draw.rect(screen, COLOR_PANEL_BG, dice_box, border_radius=8)
    pygame.draw.rect(screen, COLOR_PANEL_BORDER, dice_box, width=1, border_radius=8)

    screen.blit(font_bold.render("RECENT DICEROLLS & RESOLUTIONS", True, COLOR_ACCENT_BLUE), (right_x + 16, 344))
    y_roll = 380
    for roll in data.get("recent_rolls", []):
        r_text = f"• [{roll.get('roller')}] {roll.get('check')}: {roll.get('result')}"
        screen.blit(font_med.render(r_text, True, COLOR_TEXT_PRIMARY), (right_x + 16, y_roll))
        y_roll += 32


def render_divination_view(screen, fonts, data):
    font_large, font_med, font_small, font_bold = fonts

    # Top Section: Astrological Aspect Summary & Planetary Hours
    astro_rect = pygame.Rect(20, 72, SCREEN_WIDTH - 40, 110)
    pygame.draw.rect(screen, COLOR_PANEL_BG, astro_rect, border_radius=8)
    pygame.draw.rect(screen, COLOR_PANEL_BORDER, astro_rect, width=1, border_radius=8)

    screen.blit(font_bold.render("SWISS EPHEMERIS / TRANSIT MATRIX", True, COLOR_ACCENT_PURPLE), (36, 88))
    screen.blit(font_med.render(data.get("astrology_summary", ""), True, COLOR_TEXT_PRIMARY), (36, 116))
    screen.blit(font_small.render(data.get("planetary_hours", ""), True, COLOR_ACCENT_GOLD), (36, 148))

    # Bottom Section: Tarot Cards Spread
    cards_panel = pygame.Rect(20, 198, SCREEN_WIDTH - 40, 374)
    pygame.draw.rect(screen, COLOR_PANEL_BG, cards_panel, border_radius=8)
    pygame.draw.rect(screen, COLOR_PANEL_BORDER, cards_panel, width=1, border_radius=8)

    screen.blit(font_bold.render(f"DIVINATION SPREAD: {data.get('spread_type', '')}", True, COLOR_ACCENT_GOLD), (36, 214))

    cards = data.get("cards", [])
    if cards:
        card_w = (cards_panel.width - (len(cards) + 1) * 24) // len(cards)
        card_h = 300
        for i, card in enumerate(cards):
            cx = cards_panel.left + 24 + i * (card_w + 24)
            cy = 250
            crect = pygame.Rect(cx, cy, card_w, card_h)

            pygame.draw.rect(screen, COLOR_CARD_BG, crect, border_radius=8)
            pygame.draw.rect(screen, COLOR_ACCENT_PURPLE, crect, width=2, border_radius=8)

            # Card Header
            title_surf = font_bold.render(card.get("title", ""), True, COLOR_TEXT_PRIMARY)
            screen.blit(title_surf, (cx + 14, cy + 16))

            # Orientation Badge
            orient = card.get("orient", "Upright")
            o_color = COLOR_ACCENT_GREEN if orient.lower() == "upright" else COLOR_ACCENT_RED
            orient_surf = font_small.render(f"[{orient.upper()}]", True, o_color)
            screen.blit(orient_surf, (cx + 14, cy + 44))

            # Card Symbolic glyph / placeholder box
            glyph_rect = pygame.Rect(cx + 14, cy + 72, card_w - 28, 120)
            pygame.draw.rect(screen, (15, 18, 26), glyph_rect, border_radius=4)
            pygame.draw.rect(screen, COLOR_PANEL_BORDER, glyph_rect, width=1, border_radius=4)
            
            icon_text = font_large.render("ᛟ", True, COLOR_ACCENT_GOLD)
            screen.blit(icon_text, (cx + (card_w // 2) - 12, cy + 115))

            # Keyword notes
            kw_rect = pygame.Rect(cx + 14, cy + 204, card_w - 28, 80)
            draw_text_wrapped(screen, card.get("keyword", ""), font_small, COLOR_TEXT_MUTED, kw_rect)


def main():
    pygame.init()
    pygame.display.set_caption("Meta Muse HUD Display Node")
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    clock = pygame.time.Clock()

    # Load Standard System Fonts
    font_large = pygame.font.SysFont("DejaVu Sans, Arial, Helvetica", 28, bold=True)
    font_bold = pygame.font.SysFont("DejaVu Sans, Arial, Helvetica", 18, bold=True)
    font_med = pygame.font.SysFont("DejaVu Sans, Arial, Helvetica", 16)
    font_small = pygame.font.SysFont("DejaVu Sans, Arial, Helvetica", 13)
    fonts = (font_large, font_med, font_small, font_bold)

    # Launch HTTP Server Thread
    server = start_server(port=8080)
    print("Muse Display Node listening on http://0.0.0.0:8080")

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE or event.key == pygame.K_q:
                    running = False
                elif event.key == pygame.K_TAB or event.key == pygame.K_SPACE:
                    with state_lock:
                        app_state["active_view"] = "divination" if app_state["active_view"] == "ttrpg" else "ttrpg"
                elif event.key == pygame.K_1:
                    with state_lock:
                        app_state["active_view"] = "ttrpg"
                elif event.key == pygame.K_2:
                    with state_lock:
                        app_state["active_view"] = "divination"

        with state_lock:
            current_render_state = json.loads(json.dumps(app_state))

        render_hud(screen, fonts, current_render_state)
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    server.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    main()

How Muse Sends Updates from Her Computer
Whenever Muse executes commands or completes headless tasks on her host, she dispatches updates to the Pi 5's local IP address (http://<PI_IP>:8080).
1. Dispatching Sagnaskemma Lore & Dice Rolls
When Muse resolves an encounter or rolls dice in the D&D engine:
curl -X POST http://<PI_IP>:8080/api/ttrpg \
  -H "Content-Type: application/json" \
  -d '{
    "campaign": "Sagnaskemma - Volmarr'\''s Campaign",
    "encounter": "Harrowed Barrow Entrance",
    "lore_text": "The iron hinges groan as the tomb stone shifts back. Ancient warding runes hum in warning.",
    "muse_speech": "Volmarr, your Skald roll of 24 pieced together the elder inscription. The way is open.",
    "recent_rolls": [
      {"roller": "Volmarr", "check": "Lore (History)", "result": "1d20+7 = 24 (Success)"}
    ]
  }'

2. Dispatching Tarot Spreads & Astrological Transits
When Muse runs astrology-engine to compute planetary hours or pull a card spread:
curl -X POST http://<PI_IP>:8080/api/divination \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Elder Futhark & 3-Card Reading",
    "spread_type": "Past, Present, and Unfolding Fate",
    "cards": [
      {"title": "The Magician", "orient": "Upright", "keyword": "Focused Will, Sovereign Creation"},
      {"title": "Wheel of Fortune", "orient": "Upright", "keyword": "Turning Cycles, Wyrd Unfolding"},
      {"title": "The Star", "orient": "Upright", "keyword": "Guiding Light, Hope Restored"}
    ],
    "astrology_summary": "Sun in Libra | Mars Sextile Jupiter | Moon Entering Scorpio",
    "planetary_hours": "Planetary Hour: Sun (Peak Vitality)",
    "muse_speech": "The cards align directly with your transit matrix. The Wheel turns in your favor."
  }'

Leveraging the Hailo-10 AI HAT on the Pi 5
Because the Pi 5's Broadcom CPU handles the network endpoints and the GPU renders the Pygame canvas at 60 FPS, the Hailo-10 M.2+ HAT (with its 8GB memory) can be used for local offload tasks:
 * Local Neural Speech Synthesis (TTS): When Muse sends text to /api/status or /api/ttrpg, run a local GGUF/ONNX voice model on the Hailo-10 to vocalize Muse's narrative directly through the Pi's audio output without sending audio streams across the network.
 * Local Card / Portrait Generation: If Muse generates or describes a new NPC or tarot card, the Hailo-10 can run local diffusion or thumbnail render pipelines to output image files directly into the display canvas directory.

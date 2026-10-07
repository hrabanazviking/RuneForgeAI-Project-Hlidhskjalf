# Leveraging LVGL through Meta’s muse-gadget-sdk

(Leveraging_LVGL_through_Metas_muse-gadget-sdk.md)

Leveraging LVGL through Meta’s muse-gadget-sdk is best achieved by treating the display as a decoupled state consumer for Muse, whether deploying directly to an embedded ESP32 microcontroller or driving a display via a Linux edge host.
1. Choose Your Hardware Deployment Tier
The muse-gadget-sdk supports two distinct operational pipelines:
| Tier | Best For | Typical Hardware | Display Engine |
|---|---|---|---|
| ESP32 Firmware (esp32/) | Dedicated physical gadgets, desk companions, low-power ambient HUDs | ESP32-S3 or ESP32-C5 with \ge8MB PSRAM (Waveshare AMOLED, M5Stack CoreS3, Seeed reTerminal) | Native LVGL compiled into ESP-IDF firmware |
| Linux SDK (linux/) | High-resolution multi-panel HUDs, Raspberry Pi displays, desktop overlays | Raspberry Pi 5, single-board computers, or native Linux workstations | LVGL via SDL2 driver or Python GUI runtimes driven by the SDK daemon |
2. Implementation: Dedicated ESP32 Device
When targeting an ESP32 board, the board runs an encrypted session directly to Meta Muse over Wi-Fi.
 * Obtain SDK Credentials: Claim your device token from the Muse Gadgets Developer Portal and enable Developer Mode in the official Muse mobile application.
 * Configure Board Overlays: In the esp32/devices/ directory, select or create an sdkconfig overlay matching your chip, SPI/I2C/QSPI pinouts, and panel driver (ST7789, GC9A01, RM67162 AMOLED, etc.).
 * Handle Incoming Muse Events in LVGL:
   * State Indicator: Maintain an LVGL status icon or color ring tied to the agent connection state: IDLE, LISTENING, THINKING, and DISPATCHING.
   * Text Buffer Streaming: Pipe incoming text chunks directly into an lv_label object configured with LV_LABEL_LONG_WRAP and an auto-scrolling container (LV_SCROLL_DIR_VER).
   * Image Ingestion: Muse dispatches JPEG/PNG buffers over the encrypted session. Ensure LV_USE_SJPG or LV_USE_PNG is enabled in lv_conf.h and allocate image decode memory inside external PSRAM (heap_caps_malloc(size, MALLOC_CAP_SPIRAM)) to avoid internal RAM starvation.
3. Implementation: Linux Edge Display Bridge
The linux/ SDK runs as a daemon exposing four core agent capabilities: system.run, file.read, file.write, and device.health. Rather than executing arbitrary desktop actions, configure the Muse Linux SDK to write structured display payloads to a shared IPC pipe, file watch path, or local Unix domain socket. A local UI frontend then renders these states with LVGL (via SDL2) or a lightweight graphical loop.
Complete Display Bridge Service (muse_hud_bridge.py)
The complete Python script below acts as a display bridge. It sets up an IPC inbox for Muse's file.write updates, tracks state, and uses an interactive Pygame/SDL graphics canvas to render real-time agent status, streaming text, and 2D telemetry overlays:
#!/usr/bin/env python3
"""
muse_hud_bridge.py
Complete local display HUD bridge for Meta Muse Linux Gadget SDK.
Monitors an IPC inbox directory written to by the Muse agent, rendering
live state, streaming text, and visual alerts in real time.
"""

import os
import sys
import time
import json
import pygame
from pathlib import Path

# --- Configuration ---
HUD_WIDTH = 800
HUD_HEIGHT = 480
FPS = 60
INBOX_DIR = Path("/tmp/muse_gadget_hud")
STATE_FILE = INBOX_DIR / "state.json"

# Palette
COLOR_BG = (15, 17, 23)
COLOR_PANEL = (26, 29, 39)
COLOR_TEXT_PRIMARY = (235, 237, 243)
COLOR_TEXT_MUTED = (130, 137, 153)
COLOR_ACCENT = (79, 128, 255)
COLOR_THINKING = (245, 158, 11)
COLOR_ACTIVE = (16, 185, 129)
COLOR_BORDER = (45, 51, 69)


def initialize_environment():
    """Ensure inbox directories exist with clean state."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        initial_payload = {
            "status": "IDLE",
            "agent_name": "Muse Agent",
            "message": "Waiting for Muse session input...",
            "task": "Standby",
            "timestamp": time.time()
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(initial_payload, f, indent=2)


def load_state():
    """Safely read the latest JSON state written by the Muse daemon."""
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return None


def draw_hud(screen, fonts, state):
    """Render the 2D HUD interface."""
    screen.fill(COLOR_BG)
    font_large, font_med, font_small = fonts

    # 1. Header Bar
    pygame.draw.rect(screen, COLOR_PANEL, (0, 0, HUD_WIDTH, 60))
    pygame.draw.line(screen, COLOR_BORDER, (0, 60), (HUD_WIDTH, 60), 2)

    title_surf = font_large.render(state.get("agent_name", "Muse Agent"), True, COLOR_TEXT_PRIMARY)
    screen.blit(title_surf, (20, 15))

    # Connection / Thinking Indicator
    status = state.get("status", "IDLE").upper()
    status_color = COLOR_ACTIVE if status == "ACTIVE" else (COLOR_THINKING if status == "THINKING" else COLOR_TEXT_MUTED)
    
    pygame.draw.circle(screen, status_color, (HUD_WIDTH - 120, 30), 8)
    status_surf = font_small.render(status, True, status_color)
    screen.blit(status_surf, (HUD_WIDTH - 100, 20))

    # 2. Main Content Panel (Agent Reasoning & Output)
    pygame.draw.rect(screen, COLOR_PANEL, (20, 80, HUD_WIDTH - 40, 280), border_radius=8)
    pygame.draw.rect(screen, COLOR_BORDER, (20, 80, HUD_WIDTH - 40, 280), width=1, border_radius=8)

    label_surf = font_small.render("LIVE OUTPUT / REASONING STREAM", True, COLOR_ACCENT)
    screen.blit(label_surf, (35, 95))

    # Word-wrap message output
    raw_message = state.get("message", "")
    words = raw_message.split(' ')
    lines = []
    current_line = []
    max_text_width = HUD_WIDTH - 80

    for word in words:
        test_line = ' '.join(current_line + [word])
        if font_med.size(test_line)[0] < max_text_width:
            current_line.append(word)
        else:
            lines.append(' '.join(current_line))
            current_line = [word]
    if current_line:
        lines.append(' '.join(current_line))

    # Display wrapped lines (capped to 6 visible lines)
    y_text = 130
    for line in lines[:6]:
        line_surf = font_med.render(line, True, COLOR_TEXT_PRIMARY)
        screen.blit(line_surf, (35, y_text))
        y_text += 28

    # 3. Footer / Task Status Bar
    pygame.draw.rect(screen, COLOR_PANEL, (20, 380, HUD_WIDTH - 40, 80), border_radius=8)
    pygame.draw.rect(screen, COLOR_BORDER, (20, 380, HUD_WIDTH - 40, 80), width=1, border_radius=8)

    task_label = font_small.render("CURRENT OBJECTIVE", True, COLOR_TEXT_MUTED)
    screen.blit(task_label, (35, 395))

    current_task = state.get("task", "None")
    task_surf = font_med.render(current_task, True, COLOR_TEXT_PRIMARY)
    screen.blit(task_surf, (35, 420))


def main():
    initialize_environment()
    pygame.init()
    pygame.display.set_caption("Meta Muse Gadget Display HUD")
    screen = pygame.display.set_mode((HUD_WIDTH, HUD_HEIGHT))
    clock = pygame.time.Clock()

    font_large = pygame.font.SysFont("DejaVu Sans, Arial, Helvetica", 24, bold=True)
    font_med = pygame.font.SysFont("DejaVu Sans, Arial, Helvetica", 18)
    font_small = pygame.font.SysFont("DejaVu Sans, Arial, Helvetica", 14, bold=True)
    fonts = (font_large, font_med, font_small)

    current_state = load_state()
    last_check = 0.0

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        # Reload state file every 100ms
        now = time.time()
        if now - last_check > 0.1:
            updated = load_state()
            if updated:
                current_state = updated
            last_check = now

        draw_hud(screen, fonts, current_state)
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()

4. Operational Best Practices
 * Constrain Linux Permissions: The Linux SDK runs commands with the execution authority of the user executing the daemon. Create an unprivileged user (e.g., adduser --system musegadget) specifically to run the SDK daemon, restricting its access to /tmp/muse_gadget_hud/ and strictly relevant directories.
 * Double Buffering in LVGL: When using SPI or parallel displays on an ESP32, define two draw buffers sized at \frac{1}{10}\text{th} of the screen resolution in internal SRAM while keeping large image assets in PSRAM to maintain 30+ FPS without tearing.
 * Keep Message Envelopes Concise: Keep JSON messages passed between Muse and the display bridge lightweight (under 64 KB per chunk, matching the SDK's native chunk window).

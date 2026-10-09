"""Celestial wheel viewport.

Slice 30 — 360° astrology wheel. ``update`` accepts ``{"planets": {name:
degrees}}`` (or a bare mapping); ``render`` draws the 12-house wheel and a
marker per planet. Convention: 0° at the top, increasing clockwise.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping

from hlidskjalf.himinbjorg.compositor import Canvas, Viewport


def _polar(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg % 360.0)
    return (cx + r * math.sin(rad), cy - r * math.cos(rad))


class CelestialViewport(Viewport):
    """Draws the 12-house wheel with planet markers."""

    name = "celestial"
    can_focus = False
    HOUSES = 12

    def __init__(self, name: str | None = None) -> None:
        super().__init__(name)
        self.planets: Dict[str, float] = {}

    # -- state ----------------------------------------------------------------
    def update(self, state: Mapping[str, Any]) -> None:
        raw: Any = state.get("planets", state) if isinstance(state, Mapping) else {}
        planets: Dict[str, float] = {}
        if isinstance(raw, Mapping):
            for planet, deg in raw.items():
                try:
                    planets[str(planet)] = float(deg) % 360.0
                except (TypeError, ValueError):
                    continue
        self.planets = planets

    # -- render ---------------------------------------------------------------
    def render(self, canvas: Canvas) -> None:
        t = self.theme
        self.title_bar(canvas, "CELESTIAL WHEEL")
        r = self.rect
        cx = r.x + r.w / 2.0
        cy = r.y + r.h / 2.0 + 10
        radius = max(10.0, min(r.w, r.h) / 2.0 - 34)

        # wheel rings
        canvas.circle(cx, cy, radius, t.accent, False)
        canvas.circle(cx, cy, radius * 0.70, t.muted, False)

        # 12 house spokes + house numbers
        for i in range(self.HOUSES):
            deg = i * 30.0
            x1, y1 = _polar(cx, cy, radius * 0.70, deg)
            x2, y2 = _polar(cx, cy, radius, deg)
            canvas.line(x1, y1, x2, y2, t.muted, 1)
            nx, ny = _polar(cx, cy, radius * 0.85, deg + 15.0)
            canvas.text(nx - 6, ny - 7, str(i + 1), t.muted, t.font_size - 3)

        # planet markers
        for i, (planet, deg) in enumerate(sorted(self.planets.items())):
            px, py = _polar(cx, cy, radius * 0.52, deg)
            canvas.circle(px, py, 5, t.accent2, True)
            canvas.text(px + 8, py - 7, f"{planet} {deg:.0f}°", t.foreground,
                        t.font_size - 2)

        if not self.planets:
            canvas.text(cx - 60, cy, "no ephemeris data", t.muted, t.font_size)

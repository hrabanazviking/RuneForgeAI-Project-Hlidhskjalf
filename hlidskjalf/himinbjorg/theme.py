"""Himinbjörg HUD themes.

Slice 34a — two switchable colour themes: ``"ember"`` (dark, the default)
and ``"frost"`` (light). Colours are ``#rrggbb`` strings; viewports read them
off ``compositor.theme`` / ``viewport.theme`` so themes switch live.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Theme:
    """Full palette for one HUD theme."""

    name: str
    background: str
    panel: str
    foreground: str
    muted: str
    accent: str
    accent2: str
    ok: str
    warn: str
    crit: str
    font_size: int = 14

    def color(self, key: str, default: str = "#ffffff") -> str:
        """Look up a colour by attribute name with a safe fallback."""
        return getattr(self, key, default)


THEMES: Dict[str, Theme] = {
    "ember": Theme(
        name="ember",
        background="#0d0b08",   # near-black, warm
        panel="#1a1410",        # dark umber panel
        foreground="#e8dcc8",   # bone parchment text
        muted="#8a7a5f",        # dim bronze
        accent="#ff9e3d",       # ember orange
        accent2="#c74b2a",      # deep ember red
        ok="#7fb069",           # moss green
        warn="#e0a458",         # amber
        crit="#d64545",         # blood red
    ),
    "frost": Theme(
        name="frost",
        background="#eef2f6",   # pale ice
        panel="#ffffff",        # snow panel
        foreground="#1c2733",   # deep slate text
        muted="#6b7d90",        # grey-blue
        accent="#2a7fd4",       # glacier blue
        accent2="#7fb3e0",      # pale glacier
        ok="#2e8b57",           # sea green
        warn="#c98a1b",         # bronze
        crit="#c0392b",         # cold red
    ),
}

DEFAULT_THEME = "ember"


def get_theme(name: str) -> Theme:
    """Return the named theme; raises :class:`KeyError` if unknown."""
    try:
        return THEMES[name]
    except KeyError:
        raise KeyError(f"unknown himinbjorg theme: {name!r} "
                       f"(known: {sorted(THEMES)})") from None


def list_themes() -> List[str]:
    return sorted(THEMES)

"""Muse thought/speech stream viewport.

Slice 33 — appends streamed text into a scrollback buffer capped at 200
lines and renders the latest lines that fit the tile. Focusable:
``up``/``down`` (or ``pageup``/``pagedown``) scroll the history; ``home``
jumps back to live.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Deque, Iterable, Mapping

from hlidskjalf.himinbjorg.compositor import Canvas, Viewport


class MuseStreamViewport(Viewport):
    """Scrolling text stream from the host Muse core."""

    name = "muse_stream"
    can_focus = True
    MAX_LINES = 200

    def __init__(self, name: str | None = None) -> None:
        super().__init__(name)
        self._lines: Deque[str] = deque(maxlen=self.MAX_LINES)
        self._scroll: int = 0  # lines scrolled back from live; 0 = live tail

    # -- state ----------------------------------------------------------------
    def append(self, text: str) -> None:
        """Append text (may be multiline); newest entries stick to live."""
        for line in str(text).splitlines() or [""]:
            self._lines.append(line)
        self._scroll = 0

    def update(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            return
        lines: Any = state.get("stream", state.get("lines", []))
        if isinstance(lines, str):
            self.append(lines)
        elif isinstance(lines, Iterable):
            for line in lines:
                self.append(str(line))

    @property
    def lines(self) -> list:
        return list(self._lines)

    @property
    def line_count(self) -> int:
        return len(self._lines)

    # -- input -----------------------------------------------------------------
    def handle_key(self, key: str) -> bool:
        k = key.lower()
        if k in {"up", "k"}:
            self._scroll = min(len(self._lines), self._scroll + 1)
            return True
        if k in {"down", "j"}:
            self._scroll = max(0, self._scroll - 1)
            return True
        if k in {"pageup"}:
            self._scroll = min(len(self._lines), self._scroll + 10)
            return True
        if k in {"pagedown", "end"}:
            self._scroll = max(0, self._scroll - 10)
            return True
        if k in {"home"}:
            self._scroll = 0
            return True
        return False

    # -- render ----------------------------------------------------------------
    def render(self, canvas: Canvas) -> None:
        t = self.theme
        suffix = f"  (scrolled -{self._scroll})" if self._scroll else ""
        content_y = self.title_bar(canvas, f"MUSE STREAM{suffix}")
        r = self.rect
        line_h = t.font_size + 6
        visible = max(0, int((r.y + r.h - content_y - 8) / line_h))
        if not self._lines:
            canvas.text(r.x + 10, content_y, "…listening…", t.muted, t.font_size)
            return
        end = len(self._lines) - self._scroll
        start = max(0, end - visible)
        y = content_y
        for line in list(self._lines)[start:end]:
            canvas.text(r.x + 10, y, line, t.foreground, t.font_size)
            y += line_h

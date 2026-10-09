"""Himinbjörg Omni-HUD compositor core.

Slice 28 — 60 FPS main loop, viewport registry, and the Canvas rendering
abstraction. The HUD is fully headless-testable: every viewport renders to
the :class:`Canvas` interface, tests use :class:`HeadlessCanvas` (which
records draw calls), and :class:`PygameCanvas` is an optional live backend
that degrades to headless with a clear log line when pygame is unavailable.
"""
from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from hlidskjalf.himinbjorg.theme import Theme, get_theme

log = logging.getLogger(__name__)

#: A recorded draw call: (kind, *args).  Kinds: clear, rect, circle, line,
#: arc, text.
DrawCall = Tuple[Any, ...]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

@dataclass
class Rect:
    """Viewport tile rectangle in canvas pixels."""

    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h

    def inset(self, pad: float) -> "Rect":
        return Rect(self.x + pad, self.y + pad, max(0.0, self.w - 2 * pad),
                    max(0.0, self.h - 2 * pad))

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


# ---------------------------------------------------------------------------
# Canvas abstraction
# ---------------------------------------------------------------------------

class Canvas(abc.ABC):
    """Rendering target for viewports. Colours are ``#rrggbb`` strings."""

    @property
    @abc.abstractmethod
    def width(self) -> int: ...

    @property
    @abc.abstractmethod
    def height(self) -> int: ...

    @abc.abstractmethod
    def clear(self, color: str) -> None: ...

    @abc.abstractmethod
    def rect(self, x: float, y: float, w: float, h: float,
             color: str, filled: bool = True) -> None: ...

    @abc.abstractmethod
    def circle(self, cx: float, cy: float, r: float,
               color: str, filled: bool = True) -> None: ...

    @abc.abstractmethod
    def line(self, x1: float, y1: float, x2: float, y2: float,
             color: str, width: int = 1) -> None: ...

    @abc.abstractmethod
    def arc(self, cx: float, cy: float, r: float,
            start_deg: float, end_deg: float, color: str, width: int = 1) -> None: ...

    @abc.abstractmethod
    def text(self, x: float, y: float, s: str, color: str, size: int = 14) -> None: ...


class HeadlessCanvas(Canvas):
    """Test/edge canvas: records every draw call instead of drawing.

    ``calls`` holds :data:`DrawCall` tuples in order; :meth:`calls_of`
    filters by kind, :meth:`texts` returns every text string drawn.
    """

    def __init__(self, width: int = 1280, height: int = 720) -> None:
        self._width = int(width)
        self._height = int(height)
        self.calls: List[DrawCall] = []

    # -- Canvas -----------------------------------------------------------
    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def clear(self, color: str) -> None:
        self.calls.append(("clear", color))

    def rect(self, x: float, y: float, w: float, h: float,
             color: str, filled: bool = True) -> None:
        self.calls.append(("rect", x, y, w, h, color, filled))

    def circle(self, cx: float, cy: float, r: float,
               color: str, filled: bool = True) -> None:
        self.calls.append(("circle", cx, cy, r, color, filled))

    def line(self, x1: float, y1: float, x2: float, y2: float,
             color: str, width: int = 1) -> None:
        self.calls.append(("line", x1, y1, x2, y2, color, width))

    def arc(self, cx: float, cy: float, r: float,
            start_deg: float, end_deg: float, color: str, width: int = 1) -> None:
        self.calls.append(("arc", cx, cy, r, start_deg, end_deg, color, width))

    def text(self, x: float, y: float, s: str, color: str, size: int = 14) -> None:
        self.calls.append(("text", x, y, s, color, size))

    # -- inspection helpers -----------------------------------------------
    def calls_of(self, kind: str) -> List[DrawCall]:
        """All recorded calls of one kind, e.g. ``"text"``."""
        return [c for c in self.calls if c[0] == kind]

    def texts(self) -> List[str]:
        """Every text string drawn, in order."""
        return [c[3] for c in self.calls_of("text")]

    def clear_calls(self) -> None:
        self.calls.clear()

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for kind, *_ in self.calls:
            counts[kind] = counts.get(kind, 0) + 1
        return counts


class PygameCanvas(HeadlessCanvas):
    """Live canvas backed by pygame when importable.

    Always records calls like :class:`HeadlessCanvas` (so behaviour is
    observable even without a display); when pygame imports successfully it
    additionally blits to a real surface.  When pygame is missing it logs a
    clear warning once and stays headless.
    """

    def __init__(self, width: int = 1280, height: int = 720,
                 caption: str = "Himinbjörg Omni-HUD") -> None:
        super().__init__(width, height)
        self.available: bool = False
        self._surface = None
        self._fonts: Dict[int, Any] = {}
        try:
            import pygame  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("PygameCanvas: pygame unavailable (%s); running headless", exc)
            return
        try:
            pygame.init()
            self._surface = pygame.display.set_mode((width, height))
            pygame.display.set_caption(caption)
            self._pg = pygame
            self.available = True
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("PygameCanvas: display init failed (%s); running headless", exc)

    # -- live blitting ------------------------------------------------------
    def _font(self, size: int):
        if size not in self._fonts:
            self._fonts[size] = self._pg.font.SysFont(None, size)
        return self._fonts[size]

    @staticmethod
    def _to_color(color: str):
        h = color.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    def _blit(self, kind: str, call: DrawCall) -> None:
        if not self.available:
            return
        pg = self._pg
        surf = self._surface
        try:
            if kind == "clear":
                surf.fill(self._to_color(call[1]))
            elif kind == "rect":
                _, x, y, w, h, color, filled = call
                if filled:
                    pg.draw.rect(surf, self._to_color(color), (x, y, w, h))
                else:
                    pg.draw.rect(surf, self._to_color(color), (x, y, w, h), 1)
            elif kind == "circle":
                _, cx, cy, r, color, filled = call
                if filled:
                    pg.draw.circle(surf, self._to_color(color), (cx, cy), int(r))
                else:
                    pg.draw.circle(surf, self._to_color(color), (cx, cy), int(r), 1)
            elif kind == "line":
                _, x1, y1, x2, y2, color, width = call
                pg.draw.line(surf, self._to_color(color), (x1, y1), (x2, y2), width)
            elif kind == "arc":
                _, cx, cy, r, s_deg, e_deg, color, width = call
                rect = (cx - r, cy - r, 2 * r, 2 * r)
                pg.draw.arc(surf, self._to_color(color), rect,
                            -__import__("math").radians(e_deg),
                            -__import__("math").radians(s_deg), width)
            elif kind == "text":
                _, x, y, s, color, size = call
                img = self._font(size).render(str(s), True, self._to_color(color))
                surf.blit(img, (x, y))
            if kind == "clear":
                pg.display.flip()
        except Exception:  # pragma: no cover - never kill the HUD for blit errors
            log.exception("PygameCanvas blit failed for %s", kind)

    # -- record + blit -------------------------------------------------------
    def clear(self, color: str) -> None:
        super().clear(color)
        self._blit("clear", self.calls[-1])

    def rect(self, x, y, w, h, color, filled=True) -> None:
        super().rect(x, y, w, h, color, filled)
        self._blit("rect", self.calls[-1])

    def circle(self, cx, cy, r, color, filled=True) -> None:
        super().circle(cx, cy, r, color, filled)
        self._blit("circle", self.calls[-1])

    def line(self, x1, y1, x2, y2, color, width=1) -> None:
        super().line(x1, y1, x2, y2, color, width)
        self._blit("line", self.calls[-1])

    def arc(self, cx, cy, r, start_deg, end_deg, color, width=1) -> None:
        super().arc(cx, cy, r, start_deg, end_deg, color, width)
        self._blit("arc", self.calls[-1])

    def text(self, x, y, s, color, size=14) -> None:
        super().text(x, y, s, color, size)
        self._blit("text", self.calls[-1])


# ---------------------------------------------------------------------------
# Viewport base
# ---------------------------------------------------------------------------

class Viewport:
    """A HUD tile. Subclasses implement :meth:`render` and feed state via
    :meth:`update`.  ``rect`` is assigned by the layout engine; ``theme`` is
    kept current by the compositor."""

    name: str = "viewport"
    can_focus: bool = False

    def __init__(self, name: Optional[str] = None) -> None:
        self.name = name or self.name
        self.rect = Rect()
        self.theme: Theme = get_theme("ember")

    def update(self, state: Mapping[str, Any]) -> None:
        """Receive fresh state. Default: ignore."""

    def render(self, canvas: Canvas) -> None:
        raise NotImplementedError

    def handle_key(self, key: str) -> bool:
        """React to a routed key event. Return True if consumed."""
        return False

    def title_bar(self, canvas: Canvas, title: str) -> float:
        """Draw a panel background + title; return the y offset for content."""
        t = self.theme
        r = self.rect
        canvas.rect(r.x, r.y, r.w, r.h, t.panel, True)
        canvas.rect(r.x, r.y, r.w, r.h, t.muted, False)
        canvas.text(r.x + 10, r.y + 8, title, t.accent, t.font_size + 2)
        canvas.line(r.x + 8, r.y + 30, r.x + r.w - 8, r.y + 30, t.muted, 1)
        return r.y + 40


# ---------------------------------------------------------------------------
# Compositor
# ---------------------------------------------------------------------------

class Compositor:
    """60 FPS HUD loop + viewport registry.

    :meth:`tick` is headless-steppable: tests (and edge schedulers) call
    ``tick(1/60)`` directly; :meth:`run` drives the loop for a duration.
    """

    def __init__(self, canvas: Canvas, target_fps: float = 60.0,
                 theme_name: str = "ember") -> None:
        if target_fps <= 0:
            raise ValueError("target_fps must be positive")
        self.canvas = canvas
        self.target_fps = float(target_fps)
        self.theme: Theme = get_theme(theme_name)
        self._viewports: Dict[str, Viewport] = {}
        self._layout = None
        self.frames: int = 0
        self._elapsed: float = 0.0

    # -- registry -----------------------------------------------------------
    def register(self, viewport: Viewport) -> Viewport:
        """Plug a viewport into the HUD (replaces same-named)."""
        viewport.theme = self.theme
        self._viewports[viewport.name] = viewport
        log.info("himinbjorg: registered viewport %s", viewport.name)
        return viewport

    def remove(self, name: str) -> bool:
        """Unplug a viewport; True if it was present."""
        if name in self._viewports:
            del self._viewports[name]
            log.info("himinbjorg: removed viewport %s", name)
            return True
        return False

    @property
    def viewports(self) -> Dict[str, Viewport]:
        return self._viewports

    def viewport_names(self) -> List[str]:
        return list(self._viewports)

    # -- theme / layout ------------------------------------------------------
    def set_theme(self, name: str) -> Theme:
        """Switch theme live; every viewport picks it up on the next tick."""
        self.theme = get_theme(name)
        for vp in self._viewports.values():
            vp.theme = self.theme
        log.info("himinbjorg: theme switched to %s", name)
        return self.theme

    def set_layout(self, layout) -> None:
        """Attach a layout engine (see :mod:`hlidskjalf.himinbjorg.layout`)."""
        self._layout = layout

    # -- state feeds ----------------------------------------------------------
    def push(self, name: str, state: Mapping[str, Any]) -> bool:
        """Feed state to one viewport; False if unknown."""
        vp = self._viewports.get(name)
        if vp is None:
            return False
        vp.update(state)
        return True

    def update_all(self, states: Mapping[str, Mapping[str, Any]]) -> None:
        """Feed ``{viewport_name: state}`` to every known viewport."""
        for name, state in states.items():
            self.push(name, state)

    # -- main loop ------------------------------------------------------------
    def _apply_layout(self) -> None:
        if self._layout is None:
            return
        tiles = self._layout.layout(self.canvas.width, self.canvas.height)
        for name, rect in tiles.items():
            vp = self._viewports.get(name)
            if vp is not None:
                vp.rect = rect

    def tick(self, dt: float) -> None:
        """Advance the HUD by ``dt`` seconds and render one frame."""
        if dt <= 0:
            raise ValueError("dt must be positive")
        self.frames += 1
        self._elapsed += dt
        self._apply_layout()
        self.canvas.clear(self.theme.background)
        for vp in self._viewports.values():
            vp.theme = self.theme
            try:
                vp.render(self.canvas)
            except Exception:  # a sick viewport must never kill the HUD
                log.exception("himinbjorg: viewport %s render failed", vp.name)

    @property
    def fps(self) -> float:
        """Measured frames-per-second over all ticked time."""
        return self.frames / self._elapsed if self._elapsed > 0 else 0.0

    def run(self, duration: float, fps: Optional[float] = None,
            real_time: bool = False) -> int:
        """Run the loop for ``duration`` seconds at ``fps`` (default target).

        With ``real_time=True`` sleeps between frames to pace the wall clock;
        otherwise steps as fast as possible (headless). Returns frames drawn.
        """
        if duration <= 0:
            raise ValueError("duration must be positive")
        rate = fps or self.target_fps
        dt = 1.0 / rate
        steps = int(duration * rate)
        for _ in range(steps):
            self.tick(dt)
            if real_time:
                time.sleep(dt)
        return steps

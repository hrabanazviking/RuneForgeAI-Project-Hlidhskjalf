"""Himinbjörg input router.

Slice 35 — keyboard events reach the focused viewport. The router cycles
focus across focusable viewports (``viewport.can_focus``) in registration
order with :meth:`focus_next`; :meth:`handle_key` delivers a key to the
focused viewport's ``handle_key`` and reports whether it was consumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

from hlidskjalf.himinbjorg.compositor import Compositor, Viewport


@dataclass
class KeyEvent:
    """A normalised key press (e.g. ``"up"``, ``"a"``, ``"enter"``)."""

    key: str

    def __post_init__(self) -> None:
        self.key = str(self.key).lower()


def normalise_key(event: Union[str, dict, KeyEvent]) -> str:
    """Accept a bare string, ``{"key": ...}`` mapping, or :class:`KeyEvent`."""
    if isinstance(event, KeyEvent):
        return event.key
    if isinstance(event, dict):
        return str(event.get("key", "")).lower()
    return str(event).lower()


class InputRouter:
    """Routes key events to the focused viewport of a compositor."""

    def __init__(self, compositor: Compositor) -> None:
        self.compositor = compositor
        self._focus_order: List[str] = []
        self._focus_idx: int = -1
        self.refresh()

    # -- focus ------------------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the focusable-viewport order from the compositor."""
        self._focus_order = [name for name, vp in self.compositor.viewports.items()
                             if vp.can_focus]
        if self._focus_idx >= len(self._focus_order):
            self._focus_idx = -1

    def focusables(self) -> List[str]:
        self.refresh()
        return list(self._focus_order)

    def focused(self) -> Optional[Viewport]:
        """Currently focused viewport, or None."""
        self.refresh()
        if 0 <= self._focus_idx < len(self._focus_order):
            return self.compositor.viewports.get(self._focus_order[self._focus_idx])
        return None

    def focus(self, name: str) -> bool:
        """Focus a viewport by name. True if it is focusable."""
        self.refresh()
        if name in self._focus_order:
            self._focus_idx = self._focus_order.index(name)
            return True
        return False

    def focus_next(self) -> Optional[Viewport]:
        """Cycle focus to the next focusable viewport (wraps)."""
        self.refresh()
        if not self._focus_order:
            return None
        self._focus_idx = (self._focus_idx + 1) % len(self._focus_order)
        return self.focused()

    def focus_prev(self) -> Optional[Viewport]:
        """Cycle focus to the previous focusable viewport (wraps)."""
        self.refresh()
        if not self._focus_order:
            return None
        self._focus_idx = (self._focus_idx - 1) % len(self._focus_order)
        return self.focused()

    def blur(self) -> None:
        self._focus_idx = -1

    # -- routing -----------------------------------------------------------------
    def handle_key(self, event: Union[str, dict, KeyEvent]) -> bool:
        """Deliver a key to the focused viewport. True if consumed.

        The special key ``"tab"`` cycles focus instead of reaching viewports.
        """
        key = normalise_key(event)
        if key == "tab":
            self.focus_next()
            return True
        vp = self.focused()
        if vp is None:
            return False
        try:
            return bool(vp.handle_key(key))
        except Exception:
            return False

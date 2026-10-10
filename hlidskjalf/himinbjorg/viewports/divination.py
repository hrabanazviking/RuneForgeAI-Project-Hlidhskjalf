"""Divination viewport (tarot / runes).

Slice 31 — ``update`` accepts ``{"spread": [{"name", "position",
"reversed"}]}`` (or a bare list); ``render`` lays the spread out as card
tiles with position labels and a reversed marker.

Reader-tolerant: the host divination adapter's tarot shape
``{"spread": [{"position", "card", "upright"}]}`` is normalised
(``card`` → ``name``, ``upright`` → ``not reversed``), and the rune shape
``{"cast": [{"rune", "upright"}]}`` is accepted as well.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping

from hlidskjalf.himinbjorg.compositor import Canvas, Viewport


@dataclass
class SpreadCard:
    name: str
    position: str = ""
    reversed: bool = False


def _is_reversed(entry: Mapping[str, Any]) -> bool:
    """Normalise the reversal flag across adapter shapes.

    Explicit ``"reversed"`` wins; otherwise the host adapter's
    ``"upright"`` boolean is inverted.  Defaults to not reversed.
    """
    if "reversed" in entry:
        return bool(entry["reversed"])
    if "upright" in entry:
        return not bool(entry["upright"])
    return False


class DivinationViewport(Viewport):
    """Renders a card/rune spread as labelled tiles."""

    name = "divination"
    can_focus = False

    def __init__(self, name: str | None = None) -> None:
        super().__init__(name)
        self.spread: List[SpreadCard] = []

    # -- state ----------------------------------------------------------------
    def update(self, state: Mapping[str, Any]) -> None:
        if isinstance(state, Mapping):
            raw: Any = state.get("spread", state.get("cast", state))
        else:
            raw = state
        cards: List[SpreadCard] = []
        if isinstance(raw, (list, tuple)):
            for entry in raw:
                if isinstance(entry, Mapping):
                    cards.append(SpreadCard(
                        name=str(entry.get(
                            "name", entry.get("card", entry.get("rune", "?")))),
                        position=str(entry.get("position", "")),
                        reversed=_is_reversed(entry),
                    ))
        self.spread = cards

    # -- render ---------------------------------------------------------------
    def _wrap(self, name: str, max_chars: int = 14) -> List[str]:
        words, lines, cur = str(name).split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if len(trial) <= max_chars or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or ["?"]

    def render(self, canvas: Canvas) -> None:
        t = self.theme
        content_y = self.title_bar(canvas, "DIVINATION")
        r = self.rect
        n = len(self.spread)
        if n == 0:
            canvas.text(r.x + 10, content_y, "no spread drawn", t.muted, t.font_size)
            return
        gap = 10.0
        card_w = (r.w - gap * (n + 1)) / n
        card_h = min(r.h - (content_y - r.y) - 16, 190.0)
        for i, card in enumerate(self.spread):
            x = r.x + gap + i * (card_w + gap)
            y = content_y
            # position label
            if card.position:
                canvas.text(x, y, card.position, t.muted, t.font_size - 2)
                y += 16
            # card tile
            border = t.warn if card.reversed else t.accent
            canvas.rect(x, y, card_w, card_h, t.panel, True)
            canvas.rect(x, y, card_w, card_h, border, False)
            cy = y + 14
            for line in self._wrap(card.name):
                canvas.text(x + 8, cy, line, t.foreground, t.font_size - 1)
                cy += t.font_size + 2
            if card.reversed:
                canvas.text(x + 8, y + card_h - 20, "reversed", t.warn, t.font_size - 2)

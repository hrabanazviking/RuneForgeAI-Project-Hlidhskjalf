"""Dice probability viewport.

Slice 32 — exact NdS odds. :func:`distribution` builds the total
distribution by convolution with :class:`~fractions.Fraction` arithmetic
(no float drift); :func:`prob_at_least` gives P(total >= X). The viewport
draws the distribution as a bar chart plus the headline ``P(>=X)``.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Dict, Mapping

from hlidskjalf.himinbjorg.compositor import Canvas, Viewport


def distribution(num: int, sides: int) -> Dict[int, Fraction]:
    """Exact probability distribution of the sum of ``num`` d``sides``.

    Returns ``{total: Fraction}``. Raises :class:`ValueError` on bad dice.
    """
    if not isinstance(num, int) or not isinstance(sides, int):
        raise ValueError("num and sides must be integers")
    if num < 1:
        raise ValueError("num must be >= 1")
    if sides < 2:
        raise ValueError("sides must be >= 2")
    dist: Dict[int, Fraction] = {face: Fraction(1, sides)
                                   for face in range(1, sides + 1)}
    step = Fraction(1, sides)
    for _ in range(num - 1):
        nxt: Dict[int, Fraction] = {}
        for total, prob in dist.items():
            for face in range(1, sides + 1):
                nxt[total + face] = nxt.get(total + face, Fraction(0)) + prob * step
        dist = nxt
    return dict(sorted(dist.items()))


def prob_at_least(num: int, sides: int, target: int) -> Fraction:
    """Exact P(sum of NdS >= target)."""
    return sum((p for total, p in distribution(num, sides).items() if total >= target),
               Fraction(0))


class DiceViewport(Viewport):
    """Bar chart of the NdS distribution plus P(total >= target).

    Focusable: ``up``/``+`` and ``down``/``-`` nudge the target.
    """

    name = "dice"
    can_focus = True

    def __init__(self, name: str | None = None,
                 num: int = 2, sides: int = 6, target: int = 7) -> None:
        super().__init__(name)
        self.num = num
        self.sides = sides
        self.target = target
        self._validate()

    # -- state ----------------------------------------------------------------
    def _validate(self) -> None:
        if self.num < 1 or self.sides < 2:
            raise ValueError("need num >= 1 and sides >= 2")
        lo, hi = self.num, self.num * self.sides
        self.target = max(lo, min(hi, int(self.target)))

    def update(self, state: Mapping) -> None:
        if not isinstance(state, Mapping):
            return
        self.num = int(state.get("num", state.get("n", self.num)))
        self.sides = int(state.get("sides", state.get("s", self.sides)))
        self.target = int(state.get("target", self.target))
        self._validate()

    # -- input -----------------------------------------------------------------
    def handle_key(self, key: str) -> bool:
        k = key.lower()
        if k in {"up", "+"}:
            self.target = min(self.num * self.sides, self.target + 1)
            return True
        if k in {"down", "-"}:
            self.target = max(self.num, self.target - 1)
            return True
        return False

    # -- render ----------------------------------------------------------------
    def render(self, canvas: Canvas) -> None:
        t = self.theme
        content_y = self.title_bar(canvas, f"DICE {self.num}d{self.sides}")
        r = self.rect
        dist = distribution(self.num, self.sides)
        p_hit = prob_at_least(self.num, self.sides, self.target)

        canvas.text(r.x + 10, content_y,
                    f"P(>={self.target}) = {float(p_hit):.4f}  ({p_hit})",
                    t.accent, t.font_size)

        # bar chart
        chart_y = content_y + 26
        chart_h = max(40.0, r.y + r.h - chart_y - 30)
        totals = list(dist)
        n = len(totals)
        max_p = max(dist.values())
        slot = (r.w - 20) / n
        bar_w = max(2.0, slot * 0.7)
        for i, total in enumerate(totals):
            x = r.x + 10 + i * slot + (slot - bar_w) / 2
            h = chart_h * (dist[total] / max_p)
            color = t.accent2 if total >= self.target else t.muted
            canvas.rect(x, chart_y + chart_h - h, bar_w, h, color, True)
            if slot > 22:
                canvas.text(x - 2, chart_y + chart_h + 4, str(total),
                            t.muted, t.font_size - 4)

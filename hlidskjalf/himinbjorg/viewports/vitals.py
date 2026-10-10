"""Party vitals viewport.

Slice 29 — TTRPG party HP/status display.  ``update`` accepts
``{"party": [{"name", "hp", "max_hp", "status"}]}``,
``{"members": [...]}`` (the Sagnaskemma adapter shape), or a bare list;
``render`` draws a labelled HP bar and status text per member, coloured by
remaining health.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping

from hlidskjalf.himinbjorg.compositor import Canvas, Viewport


@dataclass
class PartyMember:
    name: str
    hp: int
    max_hp: int
    status: str = "ok"

    @property
    def fraction(self) -> float:
        if self.max_hp <= 0:
            return 0.0
        return max(0.0, min(1.0, self.hp / self.max_hp))


class VitalsViewport(Viewport):
    """Renders party members: name, HP bar, ``hp/max`` text, status."""

    name = "vitals"
    can_focus = False
    ROW_H = 46

    def __init__(self, name: str | None = None) -> None:
        super().__init__(name)
        self.members: List[PartyMember] = []

    # -- state ----------------------------------------------------------------
    def update(self, state: Mapping[str, Any]) -> None:
        # Reader-tolerant: accept the "party" key, the Sagnaskemma
        # adapter's "members" key, or a bare member list.
        if isinstance(state, Mapping):
            raw: Any = state.get("party", state.get("members", state))
        else:
            raw = state
        members: List[PartyMember] = []
        if isinstance(raw, (list, tuple)):
            for entry in raw:
                if isinstance(entry, Mapping):
                    members.append(PartyMember(
                        name=str(entry.get("name", "?")),
                        hp=int(entry.get("hp", 0)),
                        max_hp=int(entry.get("max_hp", 0)),
                        status=str(entry.get("status", "ok")),
                    ))
        self.members = members

    # -- render ---------------------------------------------------------------
    def _status_color(self, member: PartyMember) -> str:
        t = self.theme
        if member.status.lower() in {"dead", "down", "unconscious", "dying"}:
            return t.crit
        frac = member.fraction
        if frac >= 0.5:
            return t.ok
        if frac >= 0.25:
            return t.warn
        return t.crit

    def render(self, canvas: Canvas) -> None:
        t = self.theme
        content_y = self.title_bar(canvas, "PARTY VITALS")
        r = self.rect
        for i, member in enumerate(self.members):
            y = content_y + i * self.ROW_H
            if y + self.ROW_H > r.y + r.h:
                canvas.text(r.x + 10, y, f"… +{len(self.members) - i} more", t.muted, t.font_size)
                break
            # name
            canvas.text(r.x + 10, y + 2, member.name, t.foreground, t.font_size)
            # hp bar
            bar_x = r.x + 10
            bar_y = y + 22
            bar_w = max(20.0, r.w - 150)
            canvas.rect(bar_x, bar_y, bar_w, 12, t.muted, True)
            fill = bar_w * member.fraction
            if fill > 0:
                canvas.rect(bar_x, bar_y, fill, 12, self._status_color(member), True)
            # hp numbers + status
            canvas.text(bar_x + bar_w + 8, bar_y - 2,
                        f"{max(0, member.hp)}/{member.max_hp}", t.foreground, t.font_size - 1)
            canvas.text(r.x + r.w - 90, y + 2, member.status,
                        self._status_color(member), t.font_size - 1)
        if not self.members:
            canvas.text(r.x + 10, content_y, "no party data", t.muted, t.font_size)

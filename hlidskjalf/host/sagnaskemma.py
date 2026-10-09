"""Slice 44 — Sagnaskemma TTRPG adapter (Phase G: Host Integration).

Bridges the Sagnaskemma tabletop-roleplay harness (party state: hit points,
statuses, conditions) into the HIMINBJÖRG Omni-HUD vitals viewport.

Public API:
    PartyState          — mutable party roster with damage/heal/status rules.
    PartyMember         — one adventurer's vitals.
    member_from_dict / party_from_dict — build state from plain data.

Viewport format (matches ``himinbjorg`` vitals ``update()``)::

    {"members": [
        {"name": ..., "hp": ..., "max_hp": ..., "hp_pct": ...,
         "status": ..., "conditions": [...]},
        ...
    ]}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Statuses the viewport understands; anything else passes through untouched.
KNOWN_STATUSES = ("alive", "down", "dead", "stable", "unconscious")


@dataclass
class PartyMember:
    """One adventurer's vitals."""

    name: str
    hp: int
    max_hp: int
    status: str = "alive"
    conditions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_hp < 1:
            raise ValueError("max_hp must be >= 1")
        self.hp = max(0, min(self.hp, self.max_hp))
        self.conditions = list(self.conditions)

    @property
    def hp_pct(self) -> float:
        """Hit points as a percentage of maximum (0–100)."""
        return round(self.hp / self.max_hp * 100.0, 1)

    def to_viewport_member(self) -> dict[str, Any]:
        """Render this member in the vitals viewport member format."""
        return {
            "name": self.name,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "hp_pct": self.hp_pct,
            "status": self.status,
            "conditions": list(self.conditions),
        }


def member_from_dict(data: dict[str, Any]) -> PartyMember:
    """Build a PartyMember from a plain dict."""
    return PartyMember(
        name=str(data["name"]),
        hp=int(data.get("hp", 0)),
        max_hp=int(data.get("max_hp", 1)),
        status=str(data.get("status", "alive")),
        conditions=list(data.get("conditions", [])),
    )


class PartyState:
    """Mutable TTRPG party roster with simple combat bookkeeping."""

    def __init__(self, members: Optional[list[PartyMember]] = None) -> None:
        self._members: dict[str, PartyMember] = {}
        for member in members or []:
            self.add_member(member)

    # -- roster management --------------------------------------------
    def add_member(self, member: PartyMember) -> None:
        if member.name in self._members:
            raise ValueError(f"duplicate member name: {member.name!r}")
        self._members[member.name] = member

    def remove_member(self, name: str) -> PartyMember:
        try:
            return self._members.pop(name)
        except KeyError as exc:
            raise KeyError(f"unknown party member: {name!r}") from exc

    def get(self, name: str) -> PartyMember:
        try:
            return self._members[name]
        except KeyError as exc:
            raise KeyError(f"unknown party member: {name!r}") from exc

    @property
    def members(self) -> list[PartyMember]:
        return list(self._members.values())

    # -- combat bookkeeping --------------------------------------------
    def damage(self, name: str, amount: int) -> PartyMember:
        """Apply damage; member drops to 'down' at 0 hp."""
        member = self.get(name)
        member.hp = max(0, member.hp - max(0, amount))
        if member.hp == 0 and member.status == "alive":
            member.status = "down"
        return member

    def heal(self, name: str, amount: int) -> PartyMember:
        """Restore hit points (never above max); revives 'down' to 'alive'."""
        member = self.get(name)
        member.hp = min(member.max_hp, member.hp + max(0, amount))
        if member.hp > 0 and member.status == "down":
            member.status = "alive"
        return member

    def set_status(self, name: str, status: str) -> PartyMember:
        member = self.get(name)
        member.status = status
        return member

    def add_condition(self, name: str, condition: str) -> PartyMember:
        member = self.get(name)
        if condition not in member.conditions:
            member.conditions.append(condition)
        return member

    def remove_condition(self, name: str, condition: str) -> PartyMember:
        member = self.get(name)
        if condition in member.conditions:
            member.conditions.remove(condition)
        return member

    # -- viewport export ------------------------------------------------
    def to_viewport_state(self) -> dict[str, Any]:
        """Return the vitals viewport state: ``{"members": [...]}``."""
        return {
            "members": [member.to_viewport_member() for member in self.members]
        }


def party_from_dict(data: dict[str, Any]) -> PartyState:
    """Build a PartyState from ``{"members": [{name, hp, max_hp, ...}]}``."""
    members = [member_from_dict(entry) for entry in data.get("members", [])]
    return PartyState(members)

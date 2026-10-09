"""Slice 45 — Divination adapter (Phase G: Host Integration).

Feeds the HIMINBJÖRG celestial wheel and tarot/rune viewports from the host:

    planet_positions()  — ecliptic longitudes for the classical planets.
    draw_tarot(n)       — n-card tarot spread (upright/reversed).
    draw_runes(n)       — n-rune cast from the Elder Futhark (upright/merkstave).

IMPORTANT — MOCK DATA: this slice ships with a deterministic *mock* ephemeris
(``planet_positions`` is computed from a hash-seeded pseudo-random generator,
not from real astronomical calculations) and seeded random draws. Every return
value carries ``"mock": True`` so downstream consumers can never mistake these
for real ephemeris or divination-engine output. A later slice wires in the
real astrology engine.

All functions accept an optional ``seed`` for reproducibility; without one the
draws use system entropy.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Optional

MOCK = True  # This adapter is mock data only; see module docstring.

PLANETS = [
    "sun",
    "moon",
    "mercury",
    "venus",
    "mars",
    "jupiter",
    "saturn",
]

TAROT_MAJOR_ARCANA = [
    "The Fool", "The Magician", "The High Priestess", "The Empress",
    "The Emperor", "The Hierophant", "The Lovers", "The Chariot",
    "Strength", "The Hermit", "Wheel of Fortune", "Justice",
    "The Hanged Man", "Death", "Temperance", "The Devil",
    "The Tower", "The Star", "The Moon", "The Sun",
    "Judgement", "The World",
]

TAROT_SUITS = ["Wands", "Cups", "Swords", "Pentacles"]
TAROT_RANKS = [
    "Ace", "Two", "Three", "Four", "Five", "Six", "Seven",
    "Eight", "Nine", "Ten", "Page", "Knight", "Queen", "King",
]

ELDER_FUTHARK = [
    "Fehu", "Uruz", "Thurisaz", "Ansuz", "Raidho", "Kenaz",
    "Gebo", "Hagalaz", "Nauthiz", "Isa", "Jera", "Eihwaz",
    "Perthro", "Algiz", "Sowilo", "Tiwaz", "Berkana", "Ehwaz",
    "Mannaz", "Laguz", "Ingwaz", "Othala", "Dagaz",
]


def _mock_rng(planet: str, seed: Optional[int]) -> random.Random:
    """Deterministic RNG per planet: identical output for identical input."""
    key = f"{planet}:{seed if seed is not None else 'epoch'}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest, "big"))


def planet_positions(seed: Optional[int] = None) -> dict[str, Any]:
    """Return MOCK ecliptic longitudes (degrees, 0–360) per planet.

    The values are deterministic pseudo-positions derived from a hash of the
    planet name and seed — clearly marked ``"mock": True``. Not a real
    ephemeris; the real astrology-engine wiring comes in a later slice.
    """
    positions = {
        planet: round(_mock_rng(planet, seed).uniform(0.0, 360.0), 4)
        for planet in PLANETS
    }
    return {"mock": MOCK, "positions": positions}


def _tarot_deck() -> list[str]:
    deck = list(TAROT_MAJOR_ARCANA)
    for suit in TAROT_SUITS:
        for rank in TAROT_RANKS:
            deck.append(f"{rank} of {suit}")
    return deck


_SPREAD_POSITIONS = {
    1: ["Focus"],
    2: ["Situation", "Challenge"],
    3: ["Past", "Present", "Future"],
    4: ["Foundation", "Challenge", "Guidance", "Outcome"],
    5: ["Past", "Present", "Hidden", "Guidance", "Outcome"],
}


def draw_tarot(n: int, seed: Optional[int] = None) -> dict[str, Any]:
    """Draw an ``n``-card tarot spread (mock; marked ``"mock": True``).

    Returns ``{"mock": True, "spread": [{"position", "card", "upright"}]}``.
    Classic layouts are used for n=1..5; larger spreads use numbered positions.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    rng = random.Random(seed)
    deck = _tarot_deck()
    rng.shuffle(deck)
    if n > len(deck):
        raise ValueError(f"cannot draw {n} cards from a {len(deck)}-card deck")
    positions = _SPREAD_POSITIONS.get(n, [f"Card {i + 1}" for i in range(n)])
    spread = [
        {
            "position": positions[i],
            "card": deck[i],
            "upright": rng.random() < 0.75,
        }
        for i in range(n)
    ]
    return {"mock": MOCK, "spread": spread}


def draw_runes(n: int, seed: Optional[int] = None) -> dict[str, Any]:
    """Cast ``n`` runes from the 24 Elder Futhark (mock; ``"mock": True``).

    Returns ``{"mock": True, "cast": [{"rune", "upright"}]}`` where
    ``upright=False`` means merkstave (reversed) reading.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    if n > len(ELDER_FUTHARK):
        raise ValueError(
            f"cannot cast {n} runes from {len(ELDER_FUTHARK)} runes"
        )
    rng = random.Random(seed)
    bag = list(ELDER_FUTHARK)
    rng.shuffle(bag)
    cast = [
        {"rune": bag[i], "upright": rng.random() < 0.8} for i in range(n)
    ]
    return {"mock": MOCK, "cast": cast}

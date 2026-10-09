"""Autonomous cognition — scheduled loops that keep the edge mind alive."""

from hlidskjalf.autonomy.loops import (
    DEFAULT_INTERVALS,
    AutonomousLoops,
    LoopFn,
    LoopSpec,
    memory_consolidation,
    simulation_tick,
)

__all__ = [
    "DEFAULT_INTERVALS",
    "AutonomousLoops",
    "LoopFn",
    "LoopSpec",
    "memory_consolidation",
    "simulation_tick",
]

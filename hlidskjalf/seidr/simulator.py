"""Seidr simulator core (Slice 21) — heuristic discrete-step simulation.

Seidr, the art of seeing and shaping the threads of wyrd, is modeled here
as a deterministic discrete-time simulation: a scenario declares an initial
state and a transition function; each step threads the state forward while
a seeded RNG supplies the stochastic thread. Identical seeds always produce
identical traces.
"""

from __future__ import annotations

import copy
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

State = Dict[str, Any]
TransitionFn = Callable[[State, random.Random], State]
StopFn = Callable[[State], bool]


@dataclass
class Scenario:
    """A named simulation: steps, initial state, and a transition function.

    The ``transition_fn`` receives (state, rng) and returns the next state.
    It MUST NOT mutate ``state`` in place — return a fresh (or modified copy)
    state. The rng passed in is a ``random.Random`` instance seeded by
    :func:`run`, so transition logic that uses only that rng stays
    deterministic per seed.
    """

    name: str
    steps: int
    initial_state: State
    transition_fn: TransitionFn
    stop_fn: Optional[StopFn] = None

    def __post_init__(self) -> None:
        if self.steps < 0:
            raise ValueError("steps must be >= 0")
        if not isinstance(self.initial_state, dict):
            raise TypeError("initial_state must be a dict")


@dataclass
class Trace:
    """Recorded run of a :class:`Scenario`."""

    scenario_name: str
    seed: int
    states: List[State] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def final_state(self) -> Optional[State]:
        return self.states[-1] if self.states else None

    @property
    def step_count(self) -> int:
        return len(self.states)


def run(scenario: Scenario, seed: int) -> Trace:
    """Run a scenario deterministically for the given seed.

    Returns a :class:`Trace` holding every intermediate state (deep copies,
    so later mutation cannot rewrite history).
    """
    rng = random.Random(seed)
    state = copy.deepcopy(scenario.initial_state)
    states: List[State] = [copy.deepcopy(state)]

    started = time.monotonic()
    for _ in range(scenario.steps):
        state = scenario.transition_fn(copy.deepcopy(state), rng)
        if not isinstance(state, dict):
            raise TypeError("transition_fn must return a dict state")
        states.append(copy.deepcopy(state))
        if scenario.stop_fn is not None and scenario.stop_fn(state):
            break
    duration = time.monotonic() - started

    return Trace(
        scenario_name=scenario.name,
        seed=seed,
        states=states,
        duration_s=duration,
    )


def replay(trace: Trace, from_step: int = 0) -> List[State]:
    """Return a sub-slice of a trace's states (deep copies)."""
    return [copy.deepcopy(s) for s in trace.states[from_step:]]

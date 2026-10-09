"""Autonomous cognition loops for Project Hlidskjalf (Slice 49, Phase H).

Scheduled, idempotent background cognition: memory consolidation,
simulation ticks, and any future loops Volmarr's surge wires in.

The scheduler runs on a **virtual clock** advanced by
:meth:`AutonomousLoops.tick`, so tests and demos never need to sleep.
Real deployments advance the clock from wall time (see ``__main__``).

Loop functions receive ``(loops, spec)`` and may read/write
``loops.journal`` (a list of dicts) to record what they did. A failing
loop is isolated: its error is recorded on the spec and the scheduler
keeps running the rest.

Stdlib only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

__all__ = [
    "LoopFn",
    "LoopSpec",
    "AutonomousLoops",
    "memory_consolidation",
    "simulation_tick",
    "DEFAULT_INTERVALS",
]

log = logging.getLogger("hlidskjalf.autonomy")

#: A loop function receives the scheduler and its own spec.
LoopFn = Callable[["AutonomousLoops", "LoopSpec"], None]

#: Default cadence (seconds) for the built-in loops. Config, not code:
#: override via ``AutonomousLoops(intervals={...})``.
DEFAULT_INTERVALS: Dict[str, float] = {
    "memory_consolidation": 3600.0,  # hourly: distill working memory
    "simulation_tick": 60.0,  # minutely: advance the seidr simulator
}


@dataclass
class LoopSpec:
    """One scheduled autonomous loop.

    Only ``name``, ``interval_s`` and ``fn`` are required; the rest is
    scheduler bookkeeping. Re-registering a spec with an existing name
    replaces the old one (this is how built-in stubs are overridden).
    """

    name: str
    interval_s: float
    fn: LoopFn
    enabled: bool = True
    last_run: float = field(default=0.0, repr=False)
    run_count: int = field(default=0, repr=False)
    last_error: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("LoopSpec.name must be non-empty")
        if self.interval_s <= 0:
            raise ValueError(
                f"LoopSpec.interval_s must be > 0 (got {self.interval_s!r})"
            )
        if not callable(self.fn):
            raise TypeError("LoopSpec.fn must be callable")


def memory_consolidation(loops: "AutonomousLoops", spec: LoopSpec) -> None:
    """Built-in stub: distill short-term memory into the vault.

    Replace with a real implementation via::

        loops.register(LoopSpec("memory_consolidation", 3600.0, my_consolidator))
    """
    loops.journal.append(
        {
            "loop": spec.name,
            "at": loops.now,
            "note": "memory_consolidation stub ran (no-op); "
            "replace via AutonomousLoops.register()",
        }
    )


def simulation_tick(loops: "AutonomousLoops", spec: LoopSpec) -> None:
    """Built-in stub: advance the seidr simulation one tick.

    Replace with a real implementation via::

        loops.register(LoopSpec("simulation_tick", 60.0, my_tick))
    """
    loops.journal.append(
        {
            "loop": spec.name,
            "at": loops.now,
            "note": "simulation_tick stub ran (no-op); "
            "replace via AutonomousLoops.register()",
        }
    )


class AutonomousLoops:
    """Virtual-clock scheduler for autonomous cognition loops.

    Usage::

        loops = AutonomousLoops()
        loops.register(LoopSpec("custom", 30.0, my_fn))
        ran = loops.tick(60.0)   # advance 60 virtual seconds, run due loops
    """

    def __init__(
        self,
        *,
        start: float = 0.0,
        intervals: Optional[Dict[str, float]] = None,
    ) -> None:
        """Create the scheduler.

        :param start: initial virtual-clock time (seconds).
        :param intervals: overrides for built-in loop cadences, e.g.
            ``{"simulation_tick": 10.0}``.
        """
        self._clock: float = float(start)
        self._loops: Dict[str, LoopSpec] = {}
        #: Observable record of loop activity; stubs and custom loops
        #: append dicts here.
        self.journal: List[dict] = []

        cadence = dict(DEFAULT_INTERVALS)
        cadence.update(intervals or {})
        self.register(
            LoopSpec("memory_consolidation", cadence["memory_consolidation"],
                     memory_consolidation)
        )
        self.register(
            LoopSpec("simulation_tick", cadence["simulation_tick"],
                     simulation_tick)
        )

    # -- clock ---------------------------------------------------------
    @property
    def now(self) -> float:
        """Current virtual-clock time in seconds."""
        return self._clock

    # -- registry ------------------------------------------------------
    def register(self, spec: LoopSpec) -> LoopSpec:
        """Add a loop, or replace the existing loop with the same name.

        Returns the registered spec.
        """
        self._loops[spec.name] = spec
        log.debug("registered loop %r every %ss", spec.name, spec.interval_s)
        return spec

    def unregister(self, name: str) -> bool:
        """Remove a loop by name. Returns True if one was removed."""
        return self._loops.pop(name, None) is not None

    def get(self, name: str) -> Optional[LoopSpec]:
        """Return the spec for ``name``, or None."""
        return self._loops.get(name)

    def loops(self) -> List[LoopSpec]:
        """All registered loop specs, sorted by name."""
        return sorted(self._loops.values(), key=lambda s: s.name)

    # -- scheduling ----------------------------------------------------
    def due(self) -> List[LoopSpec]:
        """Specs whose interval has elapsed since their last run."""
        return sorted(
            (
                s
                for s in self._loops.values()
                if s.enabled and (self._clock - s.last_run) >= s.interval_s
            ),
            key=lambda s: s.name,
        )

    def tick(self, dt: float = 1.0) -> List[str]:
        """Advance the virtual clock by ``dt`` seconds and run due loops.

        Returns the names of the loops that ran, in run order. Never
        sleeps; never raises because of a loop failure (failures are
        recorded on the spec and logged).
        """
        if dt < 0:
            raise ValueError(f"tick dt must be >= 0 (got {dt!r})")
        self._clock += dt
        ran: List[str] = []
        for spec in self.due():
            self._run(spec)
            ran.append(spec.name)
        return ran

    def run(self, name: str) -> None:
        """Force-run one loop now, regardless of schedule."""
        spec = self._loops.get(name)
        if spec is None:
            raise KeyError(f"no loop registered as {name!r}")
        self._run(spec)

    def _run(self, spec: LoopSpec) -> None:
        try:
            spec.fn(self, spec)
        except Exception as exc:  # isolate: one bad loop must not kill the rest
            spec.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("autonomous loop %r failed", spec.name)
        finally:
            spec.last_run = self._clock
            spec.run_count += 1


def main() -> int:
    """Drive the built-in loops on wall-clock time (demo / systemd use)."""
    loops = AutonomousLoops()
    print("hlidskjalf.autonomy: driving built-in loops on wall clock "
          "(Ctrl-C to stop)")
    last = time.monotonic()
    try:
        while True:
            time.sleep(1.0)
            now = time.monotonic()
            ran = loops.tick(now - last)
            last = now
            for name in ran:
                print(f"[{loops.now:8.1f}s] ran loop: {name}")
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

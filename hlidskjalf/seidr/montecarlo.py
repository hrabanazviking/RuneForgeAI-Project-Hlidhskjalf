"""Monte Carlo ensembles over the Seidr simulator (Slice 22).

Run the same scenario many times with different seeds and summarize the
distribution of a numeric outcome extracted from each trace.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

from .simulator import Scenario, Trace, run

OutcomeFn = Callable[[Trace], float]


@dataclass
class EnsembleResult:
    """Aggregated statistics from a Monte Carlo ensemble."""

    scenario_name: str
    n: int
    seed: int
    mean: float
    variance: float
    stddev: float
    min: float
    max: float
    outcomes: List[float] = field(default_factory=list)
    histogram: List[Tuple[float, float, int]] = field(default_factory=list)
    ranked: List[Tuple[Any, int]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "n": self.n,
            "seed": self.seed,
            "mean": self.mean,
            "variance": self.variance,
            "stddev": self.stddev,
            "min": self.min,
            "max": self.max,
            "outcomes": list(self.outcomes),
            "histogram": [
                {"lo": lo, "hi": hi, "count": c} for lo, hi, c in self.histogram
            ],
            "ranked": [{"value": v, "count": c} for v, c in self.ranked],
        }


def _build_histogram(values: List[float], bins: int) -> List[Tuple[float, float, int]]:
    if not values or bins < 1:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [(lo, hi, len(values))]
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / width), bins - 1)
        counts[idx] += 1
    return [(lo + i * width, lo + (i + 1) * width, counts[i]) for i in range(bins)]


def run_ensemble(
    scenario: Scenario,
    n: int,
    seed: int,
    outcome_fn: OutcomeFn,
    histogram_bins: int = 10,
) -> EnsembleResult:
    """Run ``n`` simulations with seeds ``seed .. seed+n-1`` and summarize.

    ``outcome_fn`` maps each trace to a numeric outcome. The ensemble is
    deterministic: the same (scenario, n, seed, outcome_fn) always yields
    the same result. ``ranked`` lists distinct outcomes ordered by frequency
    (most common first).
    """
    if n < 1:
        raise ValueError("n must be >= 1")

    traces: List[Trace] = []
    outcomes: List[float] = []
    for i in range(n):
        trace = run(scenario, seed + i)
        traces.append(trace)
        outcome = outcome_fn(trace)
        if not isinstance(outcome, (int, float)) or isinstance(outcome, bool):
            raise TypeError("outcome_fn must return a numeric outcome")
        outcomes.append(float(outcome))

    mean = statistics.fmean(outcomes)
    variance = statistics.pvariance(outcomes) if n > 1 else 0.0

    counts = Counter(outcomes)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    return EnsembleResult(
        scenario_name=scenario.name,
        n=n,
        seed=seed,
        mean=mean,
        variance=variance,
        stddev=math.sqrt(variance),
        min=min(outcomes),
        max=max(outcomes),
        outcomes=outcomes,
        histogram=_build_histogram(outcomes, histogram_bins),
        ranked=ranked,
    )

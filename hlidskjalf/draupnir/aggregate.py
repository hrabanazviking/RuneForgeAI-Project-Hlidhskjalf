"""Result aggregation (Slice 27) — fan-in from sub-agents.

Collects per-agent outcomes ({agent_id, ok, output, error[, ...]}) and
merges them into a single report: counts of succeeded/failed, a merged
output (string concatenation, list extension, or dict merge depending on
payload shape), and the individual failures. Partial failure is normal:
failed agents never abort the merge.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

DEFAULT_STRATEGY = "concat"


def _merge_payloads(
    payloads: List[Any], strategy: str, separator: str
) -> Any:
    if strategy == "concat":
        parts: List[str] = []
        for p in payloads:
            parts.append("" if p is None else p if isinstance(p, str) else repr(p))
        return separator.join(parts)
    if strategy == "extend":
        merged: List[Any] = []
        for p in payloads:
            if isinstance(p, (list, tuple)):
                merged.extend(p)
            else:
                merged.append(p)
        return merged
    if strategy == "merge_dicts":
        merged_dict: Dict[Any, Any] = {}
        for p in payloads:
            if isinstance(p, dict):
                merged_dict.update(p)
        return merged_dict
    raise ValueError(f"unknown merge strategy: {strategy!r}")


def aggregate(
    results: Iterable[Dict[str, Any]],
    strategy: str = DEFAULT_STRATEGY,
    separator: str = "\n",
) -> Dict[str, Any]:
    """Fan-in ``results`` into a merged report.

    Each result is a mapping with at least ``ok`` (bool); ``agent_id``,
    ``output`` and ``error`` are honored when present. Strategies:
    ``"concat"`` (default), ``"extend"``, ``"merge_dicts"``.
    """
    results = list(results)
    succeeded = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]

    payloads = [r.get("output") for r in succeeded]
    errors = [
        {
            "agent_id": r.get("agent_id"),
            "error": r.get("error") or "unknown failure",
        }
        for r in failed
    ]

    return {
        "total": len(results),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "errors": errors,
        "merged_output": _merge_payloads(payloads, strategy, separator),
        "outputs": {
            r.get("agent_id", f"result-{i}"): r.get("output")
            for i, r in enumerate(succeeded)
        },
    }


def summarize(report: Dict[str, Any]) -> str:
    """One-line human summary of an :func:`aggregate` report."""
    return (
        f"{report.get('succeeded', 0)}/{report.get('total', 0)} agents succeeded, "
        f"{report.get('failed', 0)} failed"
    )


def filter_results(
    results: Iterable[Dict[str, Any]], ok: Optional[bool] = None
) -> List[Dict[str, Any]]:
    """Return results filtered by outcome (None = all)."""
    if ok is None:
        return list(results)
    return [r for r in results if bool(r.get("ok")) == ok]

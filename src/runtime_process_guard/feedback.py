"""Pure feedback policy for privacy-safe runtime shadow snapshots."""

from __future__ import annotations

from typing import Any


def _metric(snapshot: dict[str, object], name: str) -> int:
    if name not in snapshot:
        raise ValueError(f"missing feedback metric: {name}")
    value = snapshot[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid feedback metric: {name}")
    return value


def _validate_snapshot(snapshot: dict[str, object]) -> None:
    if snapshot.get("schema_version") != "runtime-process-guard/shadow-v1":
        raise ValueError("invalid shadow schema")
    for name in ("owner", "process_name"):
        value = snapshot.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid shadow target: {name}")
    _metric(snapshot, "process_count")
    _metric(snapshot, "generation_count_lower_bound")
    _metric(snapshot, "inaccessible_processes")


def _unknown(next_action: str, growth_cycles: int = 0) -> dict[str, object]:
    return {
        "phase": "check-act",
        "trend": "unknown",
        "process_delta": None,
        "generation_delta": None,
        "consecutive_growth_cycles": growth_cycles,
        "next_action": next_action,
        "persist_state": False,
    }


def evaluate_feedback(
    current: dict[str, object], previous_state: dict[str, Any] | None
) -> dict[str, object]:
    """Compare two observations and return the next safe control action."""
    prior_growth = 0
    previous: dict[str, object] | None = None
    if previous_state is not None:
        if previous_state.get("schema_version") != "runtime-process-guard/feedback-v1":
            return _unknown("repair-feedback-state")
        candidate = previous_state.get("snapshot")
        if not isinstance(candidate, dict):
            return _unknown("repair-feedback-state")
        previous = candidate
        try:
            _validate_snapshot(previous)
            prior_growth = _metric(previous_state, "consecutive_growth_cycles")
        except ValueError:
            return _unknown("repair-feedback-state")

    try:
        _validate_snapshot(current)
    except ValueError:
        return _unknown("repair-observation", prior_growth)
    if _metric(current, "inaccessible_processes"):
        return _unknown("repair-observation", prior_growth)

    current_processes = _metric(current, "process_count")
    current_generations = _metric(current, "generation_count_lower_bound")
    if previous_state is None:
        return {
            "phase": "baseline",
            "trend": "unknown",
            "process_delta": None,
            "generation_delta": None,
            "consecutive_growth_cycles": 0,
            "next_action": "observe-next-cycle",
            "persist_state": True,
        }

    assert previous is not None
    if _metric(previous, "inaccessible_processes"):
        return _unknown("repair-feedback-state", prior_growth)
    if (
        current["owner"] != previous["owner"]
        or current["process_name"] != previous["process_name"]
    ):
        return _unknown("repair-feedback-state", prior_growth)
    process_delta = current_processes - _metric(previous, "process_count")
    generation_delta = current_generations - _metric(
        previous, "generation_count_lower_bound"
    )
    if process_delta > 0 or generation_delta > 0:
        trend = "worsening"
        growth_cycles = prior_growth + 1
        next_action = (
            "human-review-runtime-pressure"
            if growth_cycles >= 2
            else "confirm-growth-next-cycle"
        )
    elif process_delta < 0 or generation_delta < 0:
        trend = "improving"
        growth_cycles = 0
        next_action = "continue-shadow-observation"
    else:
        trend = "stable"
        growth_cycles = 0
        next_action = "continue-shadow-observation"

    return {
        "phase": "check-act",
        "trend": trend,
        "process_delta": process_delta,
        "generation_delta": generation_delta,
        "consecutive_growth_cycles": growth_cycles,
        "next_action": next_action,
        "persist_state": True,
    }

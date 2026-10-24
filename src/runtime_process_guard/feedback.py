"""Pure feedback policy for privacy-safe runtime shadow snapshots."""

from __future__ import annotations

import math
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


def _evaluate_process_feedback(
    current: dict[str, object], previous_state: dict[str, Any] | None
) -> dict[str, object]:
    """Compare two observations and return the next safe control action."""
    prior_growth = 0
    previous: dict[str, object] | None = None
    if previous_state is not None:
        if not isinstance(previous_state, dict):
            return _unknown("repair-feedback-state")
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


_MEMORY_METRICS = (
    "host_total_memory_bytes",
    "host_available_memory_bytes",
    "host_swap_used_bytes",
    "owner_rss_sum_bytes",
)
_MEMORY_GROWTH_BYTES = 128 * 1024 * 1024
_MEMORY_GROWTH_METRICS = ("host_swap_used_bytes", "owner_rss_sum_bytes")


def _resources(snapshot: dict[str, object]) -> dict[str, Any]:
    value = snapshot.get("resources")
    if not isinstance(value, dict) or value.get("complete") is not True:
        raise ValueError("incomplete resources")
    for name in _MEMORY_METRICS:
        _metric(value, name)
    boot = value.get("boot_time_epoch")
    if isinstance(boot, bool) or not isinstance(boot, (int, float)) or boot <= 0:
        raise ValueError("invalid boot time")
    try:
        finite_boot = math.isfinite(boot)
    except OverflowError:
        raise ValueError("invalid boot time") from None
    if not finite_boot:
        raise ValueError("invalid boot time")
    if value.get("owner_rss_sum_may_overlap") is not True:
        raise ValueError("invalid RSS accounting")
    return value


def evaluate_feedback(
    current: dict[str, object], previous_state: dict[str, Any] | None
) -> dict[str, object]:
    """Extend the legacy process policy only when resource observations exist.

    RSS sums may overlap shared memory; growth does not identify an MCP cause.
    """
    result = _evaluate_process_feedback(current, previous_state)
    previous = (
        previous_state.get("snapshot") if isinstance(previous_state, dict) else None
    )
    if "resources" not in current and not (
        isinstance(previous, dict) and "resources" in previous
    ):
        return result
    prior_memory_growth = 0
    prior_streaks = dict.fromkeys(_MEMORY_GROWTH_METRICS, 0)
    try:
        if isinstance(previous_state, dict):
            prior_memory_growth = _metric(
                {
                    "consecutive_memory_growth_cycles": previous_state.get(
                        "consecutive_memory_growth_cycles", 0
                    )
                },
                "consecutive_memory_growth_cycles",
            )
            if "memory_growth_streaks" in previous_state:
                candidate = previous_state["memory_growth_streaks"]
                if not isinstance(candidate, dict) or set(candidate) != set(_MEMORY_GROWTH_METRICS):
                    raise ValueError("invalid memory growth streaks")
                prior_streaks = {name: _metric(candidate, name) for name in _MEMORY_GROWTH_METRICS}
                if max(prior_streaks.values()) != prior_memory_growth:
                    raise ValueError("inconsistent memory growth projection")
    except ValueError:
        result = _unknown("repair-feedback-state")
    result.update(
        memory_trend="unknown",
        memory_delta=None,
        consecutive_memory_growth_cycles=prior_memory_growth,
        memory_growth_streaks=prior_streaks,
        owner_rss_sum_may_overlap=True,
    )
    if not result["persist_state"]:
        return result
    try:
        resources = _resources(current)
    except ValueError:
        result.update(
            trend="unknown", persist_state=False, next_action="repair-observation"
        )
        return result
    if previous is None or "resources" not in previous:
        result.update(
            phase="baseline",
            trend="unknown",
            process_delta=None,
            generation_delta=None,
            consecutive_growth_cycles=0,
            consecutive_memory_growth_cycles=0,
            memory_growth_streaks=dict.fromkeys(_MEMORY_GROWTH_METRICS, 0),
            next_action="observe-next-cycle",
        )
        return result
    try:
        previous_resources = _resources(previous)
    except ValueError:
        result.update(
            trend="unknown", persist_state=False, next_action="repair-feedback-state"
        )
        return result
    if abs(resources["boot_time_epoch"] - previous_resources["boot_time_epoch"]) > 1.0:
        result.update(
            phase="baseline",
            trend="unknown",
            process_delta=None,
            generation_delta=None,
            consecutive_growth_cycles=0,
            consecutive_memory_growth_cycles=0,
            memory_growth_streaks=dict.fromkeys(_MEMORY_GROWTH_METRICS, 0),
            next_action="observe-next-cycle",
        )
        return result
    delta = {
        name: resources[name] - previous_resources[name] for name in _MEMORY_METRICS
    }
    streaks = {
        name: prior_streaks[name] + 1 if delta[name] >= _MEMORY_GROWTH_BYTES else 0
        for name in _MEMORY_GROWTH_METRICS
    }
    cycles = max(streaks.values())
    growing = cycles > 0
    memory_trend = (
        "worsening"
        if growing
        else (
            "improving"
            if delta["host_swap_used_bytes"] < 0 or delta["owner_rss_sum_bytes"] < 0
            else "stable"
        )
    )
    result.update(
        memory_delta=delta,
        memory_trend=memory_trend,
        consecutive_memory_growth_cycles=cycles,
        memory_growth_streaks=streaks,
    )
    if growing:
        result["trend"] = "worsening"
        if cycles >= 2:
            result["next_action"] = "human-review-runtime-pressure"
        elif result["next_action"] != "human-review-runtime-pressure":
            result["next_action"] = "confirm-growth-next-cycle"
    return result

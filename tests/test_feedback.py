import pytest

from runtime_process_guard.feedback import evaluate_feedback


@pytest.mark.parametrize("previous", [[], 17, "invalid", True])
def test_wrong_type_memory_state_is_anonymous_unknown(previous):
    result = evaluate_feedback(memory_snapshot(), previous)
    assert result["persist_state"] is False
    assert result["trend"] == "unknown"
    assert result["next_action"] == "repair-feedback-state"


def snapshot(process_count: int, generation_count: int) -> dict[str, object]:
    return {
        "schema_version": "runtime-process-guard/shadow-v1",
        "owner": "codex.exe",
        "process_name": "node.exe",
        "process_count": process_count,
        "generation_count_lower_bound": generation_count,
        "inaccessible_processes": 0,
    }


def test_first_feedback_cycle_establishes_baseline() -> None:
    result = evaluate_feedback(snapshot(12, 3), None)

    assert result["phase"] == "baseline"
    assert result["trend"] == "unknown"
    assert result["next_action"] == "observe-next-cycle"


def test_feedback_cycle_escalates_sustained_growth() -> None:
    previous = {
        "schema_version": "runtime-process-guard/feedback-v1",
        "snapshot": snapshot(12, 3),
        "consecutive_growth_cycles": 1,
    }

    result = evaluate_feedback(snapshot(20, 5), previous)

    assert result["trend"] == "worsening"
    assert result["consecutive_growth_cycles"] == 2
    assert result["next_action"] == "human-review-runtime-pressure"


def test_feedback_cycle_resets_growth_counter_after_improvement() -> None:
    previous = {
        "schema_version": "runtime-process-guard/feedback-v1",
        "snapshot": snapshot(20, 5),
        "consecutive_growth_cycles": 2,
    }

    result = evaluate_feedback(snapshot(12, 3), previous)

    assert result["trend"] == "improving"
    assert result["consecutive_growth_cycles"] == 0
    assert result["next_action"] == "continue-shadow-observation"


def test_feedback_cycle_rejects_state_for_another_target() -> None:
    other = snapshot(20, 5)
    other["owner"] = "other.exe"
    previous = {
        "schema_version": "runtime-process-guard/feedback-v1",
        "snapshot": other,
        "consecutive_growth_cycles": 2,
    }

    result = evaluate_feedback(snapshot(12, 3), previous)

    assert result["trend"] == "unknown"
    assert result["persist_state"] is False
    assert result["next_action"] == "repair-feedback-state"


def test_feedback_cycle_rejects_incomplete_observation_without_resetting_growth() -> (
    None
):
    current = snapshot(10, 2)
    current["inaccessible_processes"] = 1
    previous = {
        "schema_version": "runtime-process-guard/feedback-v1",
        "snapshot": snapshot(20, 5),
        "consecutive_growth_cycles": 2,
    }

    result = evaluate_feedback(current, previous)

    assert result["trend"] == "unknown"
    assert result["consecutive_growth_cycles"] == 2
    assert result["persist_state"] is False
    assert result["next_action"] == "repair-observation"


def test_feedback_cycle_rejects_invalid_metrics() -> None:
    current = snapshot(12, 3)
    current["process_count"] = 12.5

    result = evaluate_feedback(current, None)

    assert result["trend"] == "unknown"
    assert result["persist_state"] is False
    assert result["next_action"] == "repair-observation"


def memory_snapshot(swap=0, rss=100, boot=10.0):
    result = snapshot(12, 3)
    result["resources"] = {
        "host_total_memory_bytes": 1000,
        "host_available_memory_bytes": 500,
        "host_swap_used_bytes": swap,
        "owner_rss_sum_bytes": rss,
        "boot_time_epoch": boot,
        "owner_rss_sum_may_overlap": True,
        "complete": True,
    }
    return result


def memory_state(current, growth=0):
    return {
        "schema_version": "runtime-process-guard/feedback-v1",
        "snapshot": current,
        "consecutive_growth_cycles": 0,
        "consecutive_memory_growth_cycles": growth,
    }


def test_memory_pressure_escalates_after_two_growth_cycles():
    size = 128 * 1024 * 1024
    first = evaluate_feedback(
        memory_snapshot(swap=size), memory_state(memory_snapshot())
    )
    assert first["memory_trend"] == "worsening"
    assert first["next_action"] == "confirm-growth-next-cycle"
    second = evaluate_feedback(
        memory_snapshot(swap=2 * size), feedback_state(memory_snapshot(swap=size), first)
    )
    assert second["next_action"] == "human-review-runtime-pressure"
    assert second["consecutive_memory_growth_cycles"] == 2
    assert second["memory_delta"]["host_swap_used_bytes"] == size


def test_rss_growth_also_escalates_and_stability_resets():
    size = 128 * 1024 * 1024
    first_current = memory_snapshot(rss=size + 100)
    first = evaluate_feedback(first_current, memory_state(memory_snapshot()))
    result = evaluate_feedback(
        memory_snapshot(rss=2 * size + 100), feedback_state(first_current, first)
    )
    assert result["next_action"] == "human-review-runtime-pressure"
    stable = evaluate_feedback(memory_snapshot(), memory_state(memory_snapshot(), 2))
    assert stable["consecutive_memory_growth_cycles"] == 0


def test_incomplete_resources_preserve_previous_state():
    current = memory_snapshot()
    current["resources"]["complete"] = False
    current["resources"]["host_swap_used_bytes"] = None
    result = evaluate_feedback(current, memory_state(memory_snapshot(), 1))
    assert result["memory_trend"] == "unknown"
    assert result["persist_state"] is False
    assert result["consecutive_memory_growth_cycles"] == 1


def test_resources_transition_and_reboot_establish_new_baseline():
    for previous in (snapshot(12, 3), memory_snapshot(boot=8.0)):
        result = evaluate_feedback(memory_snapshot(), memory_state(previous, 2))
        assert result["phase"] == "baseline"
        assert result["memory_trend"] == "unknown"
        assert result["consecutive_memory_growth_cycles"] == 0
        assert result["persist_state"] is True


@pytest.mark.parametrize("offset", [-1.0, 1.0])
def test_boot_rounding_preserves_consecutive_memory_growth(offset):
    size = 128 * 1024 * 1024
    previous = memory_snapshot(boot=100.0, rss=size + 100)
    first = evaluate_feedback(previous, memory_state(memory_snapshot(boot=100.0)))
    current = memory_snapshot(boot=100.0 + offset, rss=2 * size + 100)
    result = evaluate_feedback(current, feedback_state(previous, first))
    assert result["phase"] != "baseline"
    assert result["consecutive_memory_growth_cycles"] == 2
    assert result["next_action"] == "human-review-runtime-pressure"


@pytest.mark.parametrize("offset", [-1.01, 1.01])
def test_boot_change_beyond_rounding_resets_memory_baseline(offset):
    previous = memory_snapshot(boot=100.0)
    current = memory_snapshot(boot=100.0 + offset, rss=256 * 1024 * 1024)
    result = evaluate_feedback(current, memory_state(previous, 1))
    assert result["phase"] == "baseline"
    assert result["consecutive_memory_growth_cycles"] == 0


def test_missing_resources_cannot_overwrite_memory_state():
    result = evaluate_feedback(snapshot(12, 3), memory_state(memory_snapshot(), 1))
    assert result["trend"] == "unknown"
    assert result["persist_state"] is False


def test_invalid_memory_metric_is_unknown():
    current = memory_snapshot()
    current["resources"]["owner_rss_sum_bytes"] = True
    result = evaluate_feedback(current, None)
    assert result["persist_state"] is False


def test_subthreshold_growth_and_decrease_reset_memory_counter():
    size = 128 * 1024 * 1024
    for current in (memory_snapshot(swap=size - 1), memory_snapshot(rss=99)):
        result = evaluate_feedback(current, memory_state(memory_snapshot(), 2))
        assert result["consecutive_memory_growth_cycles"] == 0
        assert result["next_action"] == "continue-shadow-observation"


def test_invalid_previous_resources_cannot_replace_state():
    previous = memory_snapshot()
    previous["resources"]["boot_time_epoch"] = float("nan")
    result = evaluate_feedback(memory_snapshot(), memory_state(previous, 1))
    assert result["persist_state"] is False
    assert result["next_action"] == "repair-feedback-state"


def test_oversized_boot_time_is_anonymous_unknown():
    malformed = memory_snapshot(boot=10**1000)
    for current, previous, action in (
        (malformed, None, "repair-observation"),
        (memory_snapshot(), memory_state(malformed, 1), "repair-feedback-state"),
    ):
        result = evaluate_feedback(current, previous)
        assert result["trend"] == "unknown"
        assert result["memory_trend"] == "unknown"
        assert result["persist_state"] is False
        assert result["next_action"] == action
        assert "OverflowError" not in str(result)



def feedback_state(current, result):
    return {"schema_version": "runtime-process-guard/feedback-v1", "snapshot": current, **result}


def test_alternating_rss_and_swap_growth_never_combines_history():
    size = 128 * 1024 * 1024
    current = memory_snapshot()
    state = feedback_state(current, evaluate_feedback(current, None))
    for rss, swap in ((size + 100, 0), (size + 100, size),
                      (2 * size + 100, size), (2 * size + 100, 2 * size)):
        current = memory_snapshot(rss=rss, swap=swap)
        result = evaluate_feedback(current, state)
        assert result["next_action"] == "confirm-growth-next-cycle"
        assert result["consecutive_memory_growth_cycles"] == 1
        state = feedback_state(current, result)
    assert max(result["memory_growth_streaks"].values()) == 1


@pytest.mark.parametrize("metric", ["host_swap_used_bytes", "owner_rss_sum_bytes"])
def test_same_memory_metric_grows_twice_and_resets_independently(metric):
    size = 128 * 1024 * 1024
    current = memory_snapshot()
    state = feedback_state(current, evaluate_feedback(current, None))
    for cycle in (1, 2):
        current = memory_snapshot(swap=cycle * size if metric == "host_swap_used_bytes" else 0,
                                  rss=cycle * size + 100 if metric == "owner_rss_sum_bytes" else 100)
        result = evaluate_feedback(current, state)
        assert result["memory_growth_streaks"][metric] == cycle
        assert result["consecutive_memory_growth_cycles"] == cycle
        state = feedback_state(current, result)
    assert result["next_action"] == "human-review-runtime-pressure"
    stable = evaluate_feedback(current, state)
    assert stable["consecutive_memory_growth_cycles"] == 0
    assert stable["memory_growth_streaks"] == {"host_swap_used_bytes": 0, "owner_rss_sum_bytes": 0}


def test_legacy_scalar_history_does_not_invent_memory_metric():
    size = 128 * 1024 * 1024
    current = memory_snapshot(rss=size + 100)
    result = evaluate_feedback(current, memory_state(memory_snapshot(), 5))
    assert result["next_action"] == "confirm-growth-next-cycle"
    assert result["consecutive_memory_growth_cycles"] == 1
    assert result["memory_growth_streaks"] == {"host_swap_used_bytes": 0, "owner_rss_sum_bytes": 1}
    next_current = memory_snapshot(rss=2 * size + 100)
    second = evaluate_feedback(next_current, feedback_state(current, result))
    assert second["next_action"] == "human-review-runtime-pressure"


@pytest.mark.parametrize("streaks", [None, [], "invalid", {},
    {"host_swap_used_bytes": 1},
    {"host_swap_used_bytes": 1, "owner_rss_sum_bytes": True},
    {"host_swap_used_bytes": -1, "owner_rss_sum_bytes": 0},
    {"host_swap_used_bytes": 1, "owner_rss_sum_bytes": "1"},
    {"host_swap_used_bytes": 1, "owner_rss_sum_bytes": 0, "other": 0},
])
def test_invalid_memory_streak_map_does_not_replace_state(streaks):
    previous = memory_state(memory_snapshot(), 1)
    previous["memory_growth_streaks"] = streaks
    result = evaluate_feedback(memory_snapshot(), previous)
    assert result["persist_state"] is False
    assert result["next_action"] == "repair-feedback-state"


def test_reboot_resets_per_metric_history_and_incomplete_observation_preserves_it():
    size = 128 * 1024 * 1024
    current = memory_snapshot(rss=size + 100)
    result = evaluate_feedback(current, memory_state(memory_snapshot()))
    state = feedback_state(current, result)
    unavailable = memory_snapshot(rss=2 * size + 100)
    unavailable["resources"]["complete"] = False
    rejected = evaluate_feedback(unavailable, state)
    assert rejected["persist_state"] is False
    assert rejected["memory_growth_streaks"] == result["memory_growth_streaks"]
    assert rejected["consecutive_memory_growth_cycles"] == 1
    rebooted = evaluate_feedback(memory_snapshot(boot=12.0), state)
    assert rebooted["memory_growth_streaks"] == {"host_swap_used_bytes": 0, "owner_rss_sum_bytes": 0}
    assert rebooted["consecutive_memory_growth_cycles"] == 0



def test_memory_projection_disagreement_cannot_replace_state():
    previous = memory_state(memory_snapshot(), 2)
    previous["memory_growth_streaks"] = {"host_swap_used_bytes": 0, "owner_rss_sum_bytes": 1}
    result = evaluate_feedback(memory_snapshot(), previous)
    assert result["persist_state"] is False
    assert result["next_action"] == "repair-feedback-state"


def test_simultaneous_growth_projects_max_not_sum_and_subthreshold_resets():
    size = 128 * 1024 * 1024
    current = memory_snapshot(swap=size, rss=size + 100)
    first = evaluate_feedback(current, memory_state(memory_snapshot()))
    assert first["consecutive_memory_growth_cycles"] == 1
    assert first["memory_growth_streaks"] == {"host_swap_used_bytes": 1, "owner_rss_sum_bytes": 1}
    next_current = memory_snapshot(swap=2 * size, rss=2 * size + 100)
    second = evaluate_feedback(next_current, feedback_state(current, first))
    assert second["consecutive_memory_growth_cycles"] == 2
    assert second["memory_growth_streaks"] == {"host_swap_used_bytes": 2, "owner_rss_sum_bytes": 2}
    subthreshold = evaluate_feedback(
        memory_snapshot(swap=3 * size - 1, rss=3 * size + 99),
        feedback_state(next_current, second))
    assert subthreshold["consecutive_memory_growth_cycles"] == 0
    assert subthreshold["memory_growth_streaks"] == {"host_swap_used_bytes": 0, "owner_rss_sum_bytes": 0}
    assert subthreshold["next_action"] == "continue-shadow-observation"

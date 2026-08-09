from runtime_process_guard.feedback import evaluate_feedback


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

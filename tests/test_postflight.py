from runtime_process_guard.postflight import verify_owned_disappearance
import pytest


def test_postflight_reports_gone_when_job_is_empty() -> None:
    assert verify_owned_disappearance(lambda: 0, timeout_seconds=1) == "gone"


def test_postflight_reports_unknown_when_job_query_fails() -> None:
    def fail() -> int:
        raise OSError("access denied")

    assert verify_owned_disappearance(fail, timeout_seconds=1) == "unknown"


def test_postflight_reports_still_running_at_deadline() -> None:
    ticks = iter((0.0, 1.0))
    assert (
        verify_owned_disappearance(
            lambda: 1,
            timeout_seconds=1,
            clock=lambda: next(ticks),
            sleep=lambda _: None,
        )
        == "still-running"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_postflight_rejects_non_finite_intervals(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        verify_owned_disappearance(lambda: 0, timeout_seconds=value)

    with pytest.raises(ValueError, match="finite"):
        verify_owned_disappearance(
            lambda: 0,
            timeout_seconds=1,
            poll_seconds=value,
        )


def test_postflight_caps_sleep_to_remaining_deadline() -> None:
    elapsed = 0.0
    sleeps = []

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        sleeps.append(seconds)
        elapsed += seconds

    assert verify_owned_disappearance(
        lambda: 1, timeout_seconds=1, poll_seconds=60,
        clock=lambda: elapsed, sleep=sleep,
    ) == "still-running"
    assert sleeps == [1.0]
    assert elapsed == 1.0

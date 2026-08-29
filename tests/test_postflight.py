from runtime_process_guard.postflight import verify_owned_disappearance


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

from datetime import datetime, timedelta, timezone

from runtime_process_guard.lifecycle import (
    LifecycleObservation,
    LifecyclePolicy,
    decide_lifecycle,
)


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)
POLICY = LifecyclePolicy(idle_timeout=timedelta(minutes=10), grace_timeout=timedelta(seconds=30))


def observe(**overrides: object) -> LifecycleObservation:
    values: dict[str, object] = {
        "managed": True,
        "owner_matches": True,
        "owner_alive": True,
        "active_requests": 0,
        "last_activity_at": NOW - timedelta(minutes=1),
        "shutdown_requested_at": None,
    }
    values.update(overrides)
    return LifecycleObservation(**values)


def test_recent_managed_connection_is_kept() -> None:
    assert decide_lifecycle(observe(), now=NOW, policy=POLICY).action == "keep"


def test_active_request_is_never_reclaimed_for_idle_timeout() -> None:
    result = decide_lifecycle(
        observe(active_requests=1, last_activity_at=NOW - timedelta(hours=1)),
        now=NOW,
        policy=POLICY,
    )
    assert result.action == "keep"


def test_idle_managed_connection_requests_graceful_shutdown() -> None:
    result = decide_lifecycle(
        observe(last_activity_at=NOW - timedelta(minutes=11)),
        now=NOW,
        policy=POLICY,
    )
    assert result.action == "graceful-shutdown"


def test_grace_expiry_allows_only_managed_job_termination() -> None:
    result = decide_lifecycle(
        observe(shutdown_requested_at=NOW - timedelta(seconds=31)),
        now=NOW,
        policy=POLICY,
    )
    assert result.action == "terminate-managed-job"


def test_active_request_still_blocks_termination_after_grace_expiry() -> None:
    result = decide_lifecycle(
        observe(
            active_requests=1,
            shutdown_requested_at=NOW - timedelta(seconds=31),
        ),
        now=NOW,
        policy=POLICY,
    )
    assert result.action == "keep"
    assert result.reason == "request-active"


def test_unmanaged_or_unknown_owner_fails_closed() -> None:
    assert decide_lifecycle(observe(managed=False), now=NOW, policy=POLICY).action == "report-only"
    assert decide_lifecycle(observe(owner_matches=False), now=NOW, policy=POLICY).action == "report-only"

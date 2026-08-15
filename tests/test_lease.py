from datetime import datetime, timedelta, timezone

import pytest

from runtime_process_guard.lease import (
    LeaseRecord,
    OwnerObservation,
    classify_lease,
)


NOW = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)


def make_lease(**overrides: object) -> LeaseRecord:
    values: dict[str, object] = {
        "lease_id": "lease_abc123",
        "owner_identity": "owner_abc123",
        "server_identity": "server_abc123",
        "owner_pid": 1234,
        "owner_created_at": NOW - timedelta(minutes=5),
        "acquired_at": NOW - timedelta(minutes=4),
        "heartbeat_at": NOW - timedelta(seconds=10),
        "expires_at": NOW + timedelta(seconds=50),
    }
    values.update(overrides)
    return LeaseRecord(**values)


def test_active_requires_matching_owner_instance() -> None:
    lease = make_lease()
    observation = OwnerObservation(
        pid=1234,
        created_at=NOW - timedelta(minutes=5),
        alive=True,
    )

    result = classify_lease(lease, now=NOW, owner=observation)

    assert result.status == "active"
    assert result.safe_to_reclaim is False


def test_expired_matching_live_owner_is_not_reclaimable() -> None:
    lease = make_lease(expires_at=NOW - timedelta(seconds=1))
    observation = OwnerObservation(
        pid=1234,
        created_at=NOW - timedelta(minutes=5),
        alive=True,
    )

    result = classify_lease(lease, now=NOW, owner=observation)

    assert result.status == "expired-owner-alive"
    assert result.safe_to_reclaim is False


def test_dead_matching_owner_is_stale_and_reclaimable() -> None:
    lease = make_lease()
    observation = OwnerObservation(
        pid=1234,
        created_at=NOW - timedelta(minutes=5),
        alive=False,
    )

    result = classify_lease(lease, now=NOW, owner=observation)

    assert result.status == "stale-owner-dead"
    assert result.safe_to_reclaim is True


def test_pid_reuse_is_stale_only_when_new_instance_is_observed() -> None:
    lease = make_lease()
    observation = OwnerObservation(
        pid=1234,
        created_at=NOW - timedelta(minutes=1),
        alive=True,
    )

    result = classify_lease(lease, now=NOW, owner=observation)

    assert result.status == "stale-owner-replaced"
    assert result.safe_to_reclaim is True


def test_missing_observation_fails_closed() -> None:
    result = classify_lease(make_lease(), now=NOW, owner=None)

    assert result.status == "unknown-owner"
    assert result.safe_to_reclaim is False


def test_record_rejects_raw_or_malformed_identity() -> None:
    with pytest.raises(ValueError, match="owner_identity"):
        make_lease(owner_identity="raw identity with spaces")


def test_record_round_trip_uses_utc_iso8601() -> None:
    lease = make_lease()

    restored = LeaseRecord.from_dict(lease.to_dict())

    assert restored == lease
    assert lease.to_dict()["expires_at"].endswith("Z")

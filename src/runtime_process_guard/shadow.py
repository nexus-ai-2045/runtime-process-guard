"""Privacy-safe shadow observation of process generations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math

import psutil

from .privacy import command_identity


_MISSING = object()


@dataclass(frozen=True)
class ProcessIdentityRow:
    identity: str
    created_at_epoch: float


@dataclass(frozen=True)
class Generation:
    started_at: str
    process_count: int
    unique_identity_count: int
    identity_prefixes: tuple[str, ...]


def group_generations(
    rows: list[ProcessIdentityRow], *, window_seconds: float = 2.0
) -> list[Generation]:
    if not rows:
        return []
    ordered = sorted(rows, key=lambda row: row.created_at_epoch)
    cohorts: list[list[ProcessIdentityRow]] = []
    for row in ordered:
        if (
            not cohorts
            or row.created_at_epoch - cohorts[-1][0].created_at_epoch > window_seconds
        ):
            cohorts.append([row])
        else:
            cohorts[-1].append(row)

    result: list[Generation] = []
    for cohort in cohorts:
        identities = tuple(sorted({row.identity[:12] for row in cohort}))
        result.append(
            Generation(
                started_at=datetime.fromtimestamp(
                    cohort[0].created_at_epoch, timezone.utc
                ).isoformat(),
                process_count=len(cohort),
                unique_identity_count=len(identities),
                identity_prefixes=identities,
            )
        )
    return result


def _has_owner(process: psutil.Process, owner_name: str) -> bool | None:
    expected = owner_name.lower()
    current = process
    for _ in range(12):
        try:
            current = current.parent()
            if current is None:
                return False
            name = current.name()
            if not isinstance(name, str) or not name:
                return None
            if name.lower() == expected:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
    return None


def collect_shadow_snapshot(
    owner_name: str, *, process_name: str | None = None
) -> dict[str, object]:
    rows: list[ProcessIdentityRow] = []
    inaccessible = 0
    try:
        processes = psutil.process_iter(
            ["name", "cmdline", "create_time"], ad_value=_MISSING
        )
        for process in processes:
            try:
                info = getattr(process, "info", {})
                if not isinstance(info, dict):
                    inaccessible += 1
                    continue
                name = info.get("name", _MISSING)
                if process_name and (not isinstance(name, str) or not name):
                    inaccessible += 1
                    continue
                if process_name and name.lower() != process_name.lower():
                    continue
                ownership = _has_owner(process, owner_name)
                if ownership is None:
                    inaccessible += 1
                    continue
                if not ownership:
                    continue
                cmdline = info.get("cmdline", _MISSING)
                created_at = info.get("create_time", _MISSING)
                if (
                    not isinstance(cmdline, (list, tuple))
                    or not cmdline
                    or not isinstance(created_at, (int, float))
                    or isinstance(created_at, bool)
                    or created_at <= 0
                    or not math.isfinite(float(created_at))
                    or not all(isinstance(value, str) for value in cmdline)
                ):
                    inaccessible += 1
                    continue
                # Reject finite values that cannot be rendered on this platform
                # before they reach generation grouping outside this boundary.
                datetime.fromtimestamp(created_at, timezone.utc)
                rows.append(
                    ProcessIdentityRow(
                        identity=command_identity(cmdline),
                        created_at_epoch=float(created_at),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                inaccessible += 1
            except (TypeError, ValueError, OverflowError, OSError):
                inaccessible += 1
    except Exception:
        # Enumeration failures are intentionally anonymous and fail closed.
        inaccessible += 1

    generations = group_generations(rows)
    return {
        "schema_version": "runtime-process-guard/shadow-v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "owner": owner_name.lower(),
        "process_name": process_name.lower() if process_name else "any",
        "read_only": True,
        "process_count": len(rows),
        "generation_count_lower_bound": len(generations),
        "inaccessible_processes": inaccessible,
        "observation_complete": inaccessible == 0,
        "generations": [asdict(generation) for generation in generations],
    }

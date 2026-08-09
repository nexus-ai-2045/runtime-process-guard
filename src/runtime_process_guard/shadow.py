"""Privacy-safe shadow observation of process generations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import psutil

from .privacy import command_identity


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
            if current.name().lower() == expected:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
    return False


def collect_shadow_snapshot(
    owner_name: str, *, process_name: str | None = None
) -> dict[str, object]:
    rows: list[ProcessIdentityRow] = []
    inaccessible = 0
    for process in psutil.process_iter(["name", "cmdline", "create_time"]):
        try:
            if (
                process_name
                and (process.info.get("name") or "").lower() != process_name.lower()
            ):
                continue
            cmdline = process.info.get("cmdline") or []
            if not cmdline:
                continue
            ownership = _has_owner(process, owner_name)
            if ownership is None:
                inaccessible += 1
                continue
            if not ownership:
                continue
            rows.append(
                ProcessIdentityRow(
                    identity=command_identity(cmdline),
                    created_at_epoch=float(process.info["create_time"]),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            inaccessible += 1
        except (TypeError, ValueError):
            continue

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
        "generations": [asdict(generation) for generation in generations],
    }

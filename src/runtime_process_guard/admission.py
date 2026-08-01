"""Pure admission policy for process launches."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    min_available_memory_mb: int = 2048
    max_cpu_percent: float = 90.0


@dataclass(frozen=True)
class Observation:
    identity: str
    executable: str
    duplicate_pids: tuple[int, ...]
    available_memory_mb: int
    cpu_percent: float
    collection_errors: tuple[str, ...] = ()
    inaccessible_processes: int = 0
    reuse_policy: str = "singleton"


@dataclass(frozen=True)
class AdmissionResult:
    decision: str
    reason_codes: tuple[str, ...]
    identity_prefix: str
    executable: str
    existing_count: int
    available_memory_mb: int
    cpu_percent: float
    inaccessible_processes: int


def evaluate(observation: Observation, budget: Budget) -> AdmissionResult:
    reasons: list[str] = []
    if observation.collection_errors:
        decision = "unknown"
        reasons.append("collection-failed")
    elif observation.duplicate_pids and observation.reuse_policy == "singleton":
        decision = "reuse"
        reasons.append("same-identity-running")
    else:
        if observation.duplicate_pids and observation.reuse_policy == "dedicated-stdio":
            reasons.append("same-identity-dedicated-stdio")
        pressure_reasons: list[str] = []
        if observation.available_memory_mb < budget.min_available_memory_mb:
            pressure_reasons.append("available-memory-below-budget")
        if observation.cpu_percent > budget.max_cpu_percent:
            pressure_reasons.append("cpu-above-budget")
        reasons.extend(pressure_reasons)
        decision = "defer" if pressure_reasons else "allow"

    return AdmissionResult(
        decision=decision,
        reason_codes=tuple(reasons),
        identity_prefix=observation.identity[:12],
        executable=observation.executable,
        existing_count=len(observation.duplicate_pids),
        available_memory_mb=observation.available_memory_mb,
        cpu_percent=round(observation.cpu_percent, 1),
        inaccessible_processes=observation.inaccessible_processes,
    )

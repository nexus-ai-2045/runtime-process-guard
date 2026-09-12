from runtime_process_guard.admission import Budget, Observation, evaluate


def observation(**overrides: object) -> Observation:
    values = {
        "identity": "a" * 64,
        "executable": "node.exe",
        "duplicate_pids": (),
        "available_memory_mb": 8192,
        "cpu_percent": 10.0,
        "collection_errors": (),
        "reuse_policy": "singleton",
    }
    values.update(overrides)
    return Observation(**values)


def test_allow_when_healthy_and_unique() -> None:
    result = evaluate(observation(), Budget())
    assert result.decision == "allow"


def test_reuse_when_singleton_already_exists() -> None:
    result = evaluate(observation(duplicate_pids=(123,)), Budget())
    assert result.decision == "reuse"
    assert result.existing_count == 1


def test_dedicated_stdio_does_not_reuse_existing_process() -> None:
    result = evaluate(
        observation(duplicate_pids=(123,), reuse_policy="dedicated-stdio"), Budget()
    )
    assert result.decision == "allow"
    assert "same-identity-dedicated-stdio" in result.reason_codes


def test_defer_when_memory_is_low() -> None:
    result = evaluate(observation(available_memory_mb=500), Budget(min_available_memory_mb=2048))
    assert result.decision == "defer"


def test_defer_when_cpu_is_saturated() -> None:
    result = evaluate(observation(cpu_percent=99), Budget(max_cpu_percent=90))
    assert result.decision == "defer"


def test_unknown_when_collection_failed() -> None:
    result = evaluate(observation(collection_errors=("access-denied",)), Budget())
    assert result.decision == "unknown"


def test_unknown_when_any_process_is_inaccessible() -> None:
    result = evaluate(observation(inaccessible_processes=1), Budget())
    assert result.decision == "unknown"
    assert "process-observation-incomplete" in result.reason_codes


def test_dedicated_stdio_does_not_require_unrelated_process_cmdlines() -> None:
    result = evaluate(
        observation(inaccessible_processes=1, reuse_policy="dedicated-stdio"),
        Budget(),
    )
    assert result.decision == "allow"
    assert "unrelated-process-observation-incomplete" in result.reason_codes

from runtime_process_guard.shadow import ProcessIdentityRow, group_generations


def test_groups_processes_started_within_window_into_generations() -> None:
    rows = [
        ProcessIdentityRow("a" * 64, 100.0),
        ProcessIdentityRow("b" * 64, 100.4),
        ProcessIdentityRow("a" * 64, 110.0),
        ProcessIdentityRow("b" * 64, 110.5),
    ]

    generations = group_generations(rows, window_seconds=2.0)

    assert len(generations) == 2
    assert generations[0].process_count == 2
    assert generations[0].unique_identity_count == 2
    assert generations[1].process_count == 2


def test_generation_output_contains_only_identity_prefixes() -> None:
    rows = [ProcessIdentityRow("a" * 64, 100.0)]
    generation = group_generations(rows)[0]
    assert generation.identity_prefixes == ("aaaaaaaaaaaa",)

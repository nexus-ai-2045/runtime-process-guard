"""全体観測の合成fixture。実プロセスの起動・停止はしない。"""
import argparse
import json
import sqlite3

import pytest

from runtime_process_guard import inventory, resident, system_observation as system

THREAD = "00000000-0000-7000-8000-000000000001"


def process(pid=1, ppid=0, *, name="app", birth=1, cpu=0, rss=10):
    return dict(pid=pid, ppid=ppid, name=name, created_at=birth, cpu_seconds=cpu, rss_bytes=rss)


def build(rows, *, previous=None, host="host", mono=10, inaccessible=0, bindings=None, now=100):
    return system.build_report(rows, bindings or [], now=now, sample_monotonic=mono,
                               host=host, previous=previous, inaccessible=inaccessible)


def binding(**changes):
    return dict(pid=3, created_at=3, thread_id=THREAD, server_pid=2, server_created_at=2,
                scope="execution-kernel", method="live-marker-and-os-fd", verified_at=90,
                expires_at=150, evidence_sha256="a" * 64, **changes)


def test_all_roots_descendants_missing_parents_cycles_and_totals_are_unique():
    rows = [process(1), process(2, 1, birth=2), process(3, 900, birth=3),
            process(4, 5), process(5, 4), process(6, 6)]
    result = build(rows)
    assert sorted(r["pid"] for r in result["processes"]) == list(range(1, 7))
    assert result["totals"]["rss_bytes"] == 60
    assert sum(g["rss_bytes"] for g in result["process_groups"]) == 60
    assert sum(g["process_count"] for g in result["process_groups"]) == 6
    mapped = {r["pid"]: r for r in result["processes"]}
    assert mapped[2]["parent_pid"] == 1
    assert all(mapped[p]["parent_pid"] is None for p in (3, 4, 5, 6))
    assert mapped[4]["ppid"] == 5


def test_existing_binding_descendants_related_transport_and_siblings_stay_separate():
    result = build([process(1), process(2, 1, name="node_repl", birth=2),
                    process(3, 2, name="node", birth=3), process(4, 2, name="node", birth=4),
                    process(5, 3, birth=5)], bindings=[binding()])
    rows = {r["pid"]: r for r in result["processes"]}
    assert rows[3]["thread_ids"] == rows[5]["thread_ids"] == [THREAD]
    assert rows[4]["thread_ids"] == []
    assert rows[2]["thread_ids"] == []
    assert rows[2]["related_thread_ids"] == [THREAD]
    assert all(r["status"] == "protected" for r in resident.assess(result))


def test_cpu_uses_monotonic_time_despite_wall_clock_reversal():
    previous = build([process(cpu=1)])
    result = build([process(cpu=2)], previous=previous, mono=12, now=90)
    assert result["processes"][0]["cpu_percent"] == 50
    assert result["totals"]["count_delta"] == 0


@pytest.mark.parametrize("host,mono", [("rebooted", 12), (None, 12), ("host", 10), ("host", 9)])
def test_unknown_host_or_nonincreasing_clock_resets_cpu_and_growth(host, mono):
    previous = build([process(cpu=1)])
    result = build([process(cpu=2)], previous=previous, host=host, mono=mono)
    assert result["processes"][0]["cpu_percent"] is None
    assert result["diff"]["comparison_status"] == "unverified"
    assert result["totals"]["count_delta"] is None


def test_pid_reuse_and_counter_reversal_are_not_cpu_measurements():
    previous = build([process(cpu=3), process(2, cpu=3)])
    result = build([process(birth=2, cpu=4), process(2, cpu=1)], previous=previous, mono=12)
    assert all(r["cpu_percent"] is None for r in result["processes"])
    assert result["diff"]["added_pids"] == result["diff"]["removed_pids"] == [1]


@pytest.mark.parametrize("old_missing,new_missing", [(0, 1), (1, 0), (1, 1)])
def test_partial_or_changed_coverage_never_confirms_proliferation(old_missing, new_missing):
    previous = build([process()], inaccessible=old_missing)
    result = build([process(), process(2)], previous=previous, mono=12, inaccessible=new_missing)
    assert result["totals"]["count_delta"] is None
    assert result["diff"]["added_pids"] == []
    assert all(g["count_delta"] is None for g in result["process_groups"])


def test_group_growth_counts_and_disappeared_group_are_displayed():
    previous = build([process(name="old"), process(2, name="node")])
    result = build([process(2, name="node"), process(3, name="node")], previous=previous, mono=12)
    groups = {g["name"]: g for g in result["process_groups"]}
    assert groups["old"]["count_delta"] == -1
    assert groups["old"]["process_count"] == 0
    assert groups["node"]["count_delta"] == groups["node"]["added_count"] == 1
    assert result["totals"]["count_delta"] == 0


def test_names_are_redacted_even_in_pure_fixture_and_disappeared_groups():
    previous = build([process(name="token=secret-value")])
    result = build([], previous=previous, mono=12)
    assert "secret-value" not in json.dumps(previous)
    assert "secret-value" not in json.dumps(result)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    args = argparse.Namespace(scope="system", state=tmp_path / "system.json", bindings=None, threads_db=None)
    calls = []
    monkeypatch.setattr(inventory, "collect_processes", lambda: calls.append("walk") or ([process()], 0))
    monkeypatch.setattr(system, "host_session", lambda: "host")
    monkeypatch.setattr(resident, "collect_pressure", lambda: dict(status="normal", memory_pressure_level=1, swap_used_bytes=0))
    return args, calls


def test_system_entry_has_separate_schema_protected_assessments_and_one_walk(setup):
    args, calls = setup
    result = resident.observe(args)
    assert calls == ["walk"]
    assert result["schema"] == "runtime_process_guard.observatory.v2"
    assert result["schema_version"] == 2
    assert result["inventory"]["schema_version"] == system.SCHEMA
    assert result["overall"] == "ok"
    assert result["managed_connections_registered"] == 0
    assert result["next_interval_seconds"] == 120
    assert all(r["status"] == "protected" for r in result["assessment"])
    assert inventory.read_json(args.state)["owner"] == "system"


def test_legacy_state_cannot_be_overwritten_by_system(setup):
    args, calls = setup
    args.state.write_text(json.dumps(dict(schema_version=inventory.SCHEMA, owner="ChatGPT")))
    before = args.state.read_bytes()
    assert resident.observe(args)["inventory"] is None
    assert not calls
    assert args.state.read_bytes() == before


def test_invalid_bindings_preserve_observation_as_unknown(setup):
    args, calls = setup
    args.bindings = args.state.parent / "bindings.json"
    args.bindings.write_text('{"schema_version":"invalid"}')
    result = resident.observe(args)
    assert result["overall"] == "unknown"
    assert result["inventory"]["totals"]["process_count"] == 1
    assert result["inventory"]["processes"][0]["attribution"] == "unknown"
    assert result["error_code"] == "bindings-unavailable"
    assert calls == ["walk"]


def test_catalog_uses_existing_metadata_reader_once_and_redacts_title(setup, monkeypatch):
    args, calls = setup
    args.threads_db = args.state.parent / "threads.db"
    with sqlite3.connect(args.threads_db) as connection:
        connection.execute("CREATE TABLE threads (id TEXT,title TEXT)")
        connection.execute("INSERT INTO threads VALUES (?,?)", (THREAD, "合成 token=catalog-secret"))
    original = inventory.registered_chats
    def counted(*args, **kwargs):
        calls.append("catalog")
        return original(*args, **kwargs)
    monkeypatch.setattr(inventory, "registered_chats", counted)
    monkeypatch.setattr(inventory, "chat_titles", lambda *a, **k: pytest.fail("duplicate database read"))
    result = resident.observe(args)
    assert calls == ["walk", "catalog"]
    catalog = result["inventory"]["registered_chats"]
    assert catalog[0]["process_mapping"] == "unverified"
    assert catalog[0]["attributed_pids"] == []
    assert "catalog-secret" not in json.dumps(result)
    assert "catalog-secret" not in args.state.read_text(encoding="utf-8")


def test_scope_cli_and_partial_envelope(setup, monkeypatch, capsys):
    args, _ = setup
    monkeypatch.setattr(inventory, "collect_processes", lambda: ([process(name="Discord")], 2))
    code = resident.main(["observe", "--scope", "system", "--state", str(args.state), "--json"])
    value = json.loads(capsys.readouterr().out)
    assert code == 40
    assert value["error_code"] == "incomplete-observation"
    assert value["assessment"][0]["reason"] == "protected-process"
    assert value["inventory"]["coverage"]["inaccessible"] == 2
    assert value["next_interval_seconds"] == 300


def test_unavailable_catalog_keeps_all_process_rows(setup):
    args, calls = setup
    args.threads_db = args.state.parent / "missing.db"
    result = resident.observe(args)
    assert result["error_code"] == "catalog-unavailable"
    assert result["inventory"]["catalog_status"] == "unavailable"
    assert result["inventory"]["registered_chats"] == []
    assert len(result["inventory"]["processes"]) == 1
    assert calls == ["walk"]


def test_expired_binding_is_rejected_without_hiding_system_processes():
    expired = binding()
    expired["expires_at"] = 99
    result = build([process(2, name="node_repl", birth=2),
                    process(3, 2, name="node", birth=3)], bindings=[expired])
    assert result["totals"]["process_count"] == 2
    assert result["binding_rejections"] == 1
    assert all(row["attribution"] == "unknown" for row in result["processes"])
    assert all(row["status"] == "protected" for row in resident.assess(result))


@pytest.mark.parametrize('field,value', [
    ('coverage', None), ('processes', [None]),
    ('sample_epoch', float('inf')), ('sample_monotonic', float('inf')),
])
def test_corrupt_cache_stays_private_and_is_not_overwritten(setup, field, value):
    args, calls = setup
    resident.observe(args)
    cached = json.loads(args.state.read_text(encoding='utf-8'))
    cached[field] = value
    args.state.write_text(json.dumps(cached), encoding='utf-8')
    before = args.state.read_bytes()
    calls.clear()
    result = resident.observe(args)
    assert result['overall'] == 'unknown'
    assert result['inventory'] is None
    assert args.state.read_bytes() == before
    assert not calls


def test_cached_group_fields_are_not_republished(setup, monkeypatch):
    args, _ = setup
    resident.observe(args)
    cached = json.loads(args.state.read_text(encoding='utf-8'))
    cached['processes'][0]['name'] = 'token=old-secret'
    cached['process_groups'][0]['kind'] = 'token=cache-secret'
    args.state.write_text(json.dumps(cached), encoding='utf-8')
    monkeypatch.setattr(inventory, 'collect_processes', lambda: ([process(name='new')], 0))
    result = resident.observe(args)
    output = json.dumps(result) + args.state.read_text(encoding='utf-8')
    assert 'cache-secret' not in output
    assert 'old-secret' not in output
    assert result['inventory'] is not None


def test_observe_after_reboot_discards_old_monotonic_baseline(setup, monkeypatch):
    args, calls = setup
    resident.observe(args)
    cached = json.loads(args.state.read_text(encoding='utf-8'))
    cached['sample_monotonic'] = 1_000_000
    args.state.write_text(json.dumps(cached), encoding='utf-8')
    monkeypatch.setattr(system, 'host_session', lambda: 'rebooted')
    monkeypatch.setattr(system.time, 'monotonic', lambda: 100)
    result = resident.observe(args)
    assert result['inventory'] is not None
    assert result['inventory']['host_session'] == 'rebooted'
    assert result['inventory']['processes'][0]['cpu_percent'] is None
    assert result['inventory']['diff']['comparison_status'] == 'unverified'
    assert calls == ['walk', 'walk']

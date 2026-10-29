#!/usr/bin/env python3
"""架空fixtureで表示入力の安全境界と有限collectorを検査する。"""
import json
from pathlib import Path
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parent
app = root / 'build/RuntimeProcessMenuBar.app/Contents/MacOS/RuntimeProcessMenuBar'
valid = json.loads((root / 'Fixtures/valid.json').read_text())
fixture_checks = []
with tempfile.TemporaryDirectory(prefix='verification-', dir=root / 'build') as temporary:
    target = Path(temporary)

    def fixture(value, expected):
        path = target / 'case.json'
        path.write_text(json.dumps(value))
        result = subprocess.run([str(app), '--validate-fixture', str(path)], capture_output=True)
        assert result.returncode == expected, (result.returncode, expected)
        fixture_checks.append(expected)

    fixture(valid, 0)
    for interval in (120, 300):
        low_frequency = json.loads(json.dumps(valid))
        low_frequency['next_interval_seconds'] = interval
        fixture(low_frequency, 0)
    invalid_interval = json.loads(json.dumps(valid))
    invalid_interval['next_interval_seconds'] = 301
    fixture(invalid_interval, 40)
    managed = json.loads((root / "Fixtures/managed-capability.json").read_text())
    fixture(managed, 0)
    invalid = json.loads(json.dumps(managed))
    invalid["automatic_cleanup_supported"] = False
    fixture(invalid, 40)
    invalid = json.loads(json.dumps(managed))
    invalid["managed_connections_registered"] = 1
    fixture(invalid, 40)
    partial = json.loads((root / 'Fixtures/partial.json').read_text())
    fixture(partial, 0)
    invalid = json.loads(json.dumps(valid))
    invalid['assessment'][0]['status'] = 'stop-ready'
    fixture(invalid, 40)
    invalid = json.loads(json.dumps(valid))
    invalid['assessment'][0]['created_at'] += 1
    fixture(invalid, 40)
    invalid = json.loads(json.dumps(valid))
    invalid['inventory']['processes'][0]['parent_pid'] = 101
    fixture(invalid, 40)
    invalid = json.loads(json.dumps(valid))
    invalid['automatic_cleanup_supported'] = True
    fixture(invalid, 40)
    invalid = json.loads(json.dumps(valid))
    invalid['inventory']['totals']['rss_bytes'] += 1
    fixture(invalid, 40)

    system = json.loads((root / 'Fixtures/system.json').read_text())
    fixture(system, 0)
    for mutation in ('unprotected', 'coverage', 'interval', 'schema'):
        invalid = json.loads(json.dumps(system))
        if mutation == 'unprotected':
            invalid['assessment'][0]['status'] = 'stop-ready'
        elif mutation == 'coverage':
            invalid['inventory']['coverage']['inaccessible'] = 1
        elif mutation == 'interval':
            invalid['next_interval_seconds'] = 30
        else:
            invalid['scope'] = 'legacy'
        fixture(invalid, 40)

    collector = target / 'collector.sh'
    # 自分で作る静的collectorだけを実行。OSプロセスを照会しない。
    collector.write_text('#!/bin/sh\ncat "' + str(root / 'Fixtures/partial.json') + '"\nexit 40\n')
    collector.chmod(0o700)
    args = [str(app), '--cli', str(collector), '--state', str(target / 'cache/latest.json'),
            '--scope', 'legacy', '--no-ui', '--smoke-seconds', '2']
    running = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.3)
    duplicate = subprocess.run(args, capture_output=True, text=True)
    assert duplicate.returncode == 40, '同一stateの二重起動を拒否しなかった'
    output, errors = running.communicate(timeout=18)
    receipt = json.loads(output)
    assert running.returncode == 40, (running.returncode, errors)
    assert receipt['collector_successes'] == 0
    assert receipt['collector_partial_observations'] == 1
    assert receipt['collector_failures'] == 0
    assert receipt['maximum_parallel_collectors'] == 1
    assert receipt['collector_running'] is False
    assert receipt['automatic_cleanup_supported'] is False
    assert receipt['managed_connections_registered'] == 0
    assert receipt['reaped_children_cpu_seconds'] >= 0
    assert receipt['monitor_total_cpu_seconds'] >= receipt['app_cpu_seconds']
    assert abs(receipt['monitor_total_cpu_seconds'] - receipt['app_cpu_seconds'] - receipt['reaped_children_cpu_seconds']) < 1e-9
    collector.write_text('#!/bin/sh\ncat "' + str(root / 'Fixtures/managed-capability.json') + '"\n')
    completed = subprocess.run(args, capture_output=True, text=True, timeout=18)
    managed_receipt = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert managed_receipt['collector_successes'] == 1
    assert managed_receipt['automatic_cleanup_supported'] is True
    assert managed_receipt['managed_connections_registered'] == 0
    assert managed_receipt['collector_running'] is False
    invocation = target / 'invocation.txt'
    collector.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "' + str(invocation) + '"\ncat "' + str(root / 'Fixtures/system.json') + '"\n')
    system_args = [str(app), '--cli', str(collector), '--state', str(target / 'system/latest.json'),
                   '--scope', 'system', '--no-ui', '--smoke-seconds', '2']
    system_run = subprocess.run(system_args, capture_output=True, text=True, timeout=18)
    assert system_run.returncode == 0, system_run.stderr
    assert invocation.read_text().splitlines()[:4] == ['resident', 'observe', '--scope', 'system']
    assert json.loads(system_run.stdout)['maximum_parallel_collectors'] == 1
    # 実アプリをfixture collectorで起動し、実際のNSTableView経路を使う。
    ui_fixture = json.loads(json.dumps(system))
    row = ui_fixture['inventory']['processes'][0]
    assessment = ui_fixture['assessment'][0]
    ui_fixture['inventory']['processes'] = []
    ui_fixture['assessment'] = []
    for pid, name in ((90, 'synthetic-match-a'), (42, 'synthetic-match-b'), (75, 'synthetic-other')):
        ui_fixture['inventory']['processes'].append({**row, 'pid': pid, 'name': name})
        ui_fixture['assessment'].append({**assessment, 'pid': pid})
    ui_fixture['inventory']['totals']['process_count'] = 3
    ui_fixture['inventory']['totals']['rss_bytes'] = 3 * row['rss_bytes']
    ui_fixture['inventory']['coverage']['observed'] = 3
    ui_path = target / 'ui-fixture.json'
    ui_path.write_text(json.dumps(ui_fixture))
    collector.write_text('#!/bin/sh\ncat "' + str(ui_path) + '"\n')
    ui_args = [str(app), '--cli', str(collector), '--state', str(target / 'ui/latest.json'),
               '--scope', 'system', '--show-window', '--smoke-seconds', '2',
               '--smoke-search', 'synthetic-match', '--smoke-sort-pid', '--smoke-repeat-status']
    conflict = subprocess.run(ui_args + ['--no-ui'], capture_output=True, text=True, timeout=18)
    assert conflict.returncode == 40
    ui_run = subprocess.run(ui_args, capture_output=True, text=True, timeout=18)
    assert ui_run.returncode == 0, ui_run.stderr
    ui_receipt = json.loads(ui_run.stdout)
    assert ui_receipt['details_visible'] is True
    assert ui_receipt['details_rebuild_count'] == 1
    assert ui_receipt['details_total_rows'] == 3
    assert ui_receipt['details_displayed_rows'] == 2
    assert ui_receipt['details_catalog_rows'] == 1
    assert ui_receipt['details_group_rows'] == 1
    assert ui_receipt['details_sort_changed'] is True
    assert ui_receipt['details_sort_ascending'] is True
    print(json.dumps({'新版能力表示': managed_receipt, '検査': '成功', '形式事例': len(fixture_checks), '二重起動拒否': True, '部分観測': receipt, '表の検証': ui_receipt}, ensure_ascii=False))

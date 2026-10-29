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
with tempfile.TemporaryDirectory(prefix='verification-', dir=root / 'build') as temporary:
    target = Path(temporary)

    def fixture(value, expected):
        path = target / 'case.json'
        path.write_text(json.dumps(value))
        result = subprocess.run([str(app), '--validate-fixture', str(path)], capture_output=True)
        assert result.returncode == expected, (result.returncode, expected)

    fixture(valid, 0)
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

    collector = target / 'collector.sh'
    # 自分で作る静的collectorだけを実行。OSプロセスを照会しない。
    collector.write_text('#!/bin/sh\ncat "' + str(root / 'Fixtures/partial.json') + '"\nexit 40\n')
    collector.chmod(0o700)
    args = [str(app), '--cli', str(collector), '--state', str(target / 'cache/latest.json'),
            '--no-ui', '--smoke-seconds', '2']
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
    print(json.dumps({'新版能力表示': managed_receipt, '検査': '成功', '形式事例': 10, '二重起動拒否': True, '部分観測': receipt}, ensure_ascii=False))

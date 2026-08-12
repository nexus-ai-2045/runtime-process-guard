"""Scheduled Task から pythonw.exe で実行する不可視の調停入口。

判定と書き込みの実体は runtime_process_guard.plugin_window_policy にある。
この shim は sys.path の解決だけを担う (collector と launcher の分離)。
既定は check のみ。書き換えるには --apply を明示する。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from runtime_process_guard.plugin_window_policy import run_cli

if __name__ == "__main__":
    raise SystemExit(run_cli())

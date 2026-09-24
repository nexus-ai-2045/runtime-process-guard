"""CI must actually execute the declared repo-preflight consistency gate.

`.repo-preflight-consistency.json` alone is "declared but not enforced":
this test fails if ci.yml stops invoking the upstream gate with this repo's
config (required, not weakened below the declared mode).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
CONFIG = ROOT / ".repo-preflight-consistency.json"


def test_ci_runs_repo_preflight_consistency_gate_with_repo_config() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    workflow = CI.read_text(encoding="utf-8")

    # upstream repo-preflight is checked out at a pinned full SHA (no logic copy)
    assert re.search(
        r"repository:\s*nexus-ai-2045/repo-preflight\s*\n\s*ref:\s*[0-9a-f]{40}\b",
        workflow,
    ), "ci.yml must check out nexus-ai-2045/repo-preflight at a pinned SHA"

    gate_lines = [
        line for line in workflow.splitlines() if "scripts/consistency_gate.py" in line
    ]
    assert gate_lines, "ci.yml must run repo-preflight scripts/consistency_gate.py"
    gate = gate_lines[0]
    assert "--repo ." in gate
    assert "--require-config" in gate
    assert f"--require-mode {config['mode']}" in gate
    assert "scripts/consistency_gate.py" not in {
        p.relative_to(ROOT).as_posix() for p in ROOT.rglob("consistency_gate.py")
    }, "gate logic must not be copied into this repo"

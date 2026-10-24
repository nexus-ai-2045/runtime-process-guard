# 控えから取り込んだ古い版（2026-10-24）

このフォルダには、履歴を作り直す前の非公開の控えにだけ残っていた中身を、消さずに残すために置いています。

- 置き方: `<控えの枝またはタグ>/<元のパス>`。中身は控えのままです（下の「伏せたもの」を除く）。
- ここにあるファイルは実行も読み込みもされません。テストの収集からも外しています。
- 古い版の `.md` は末尾に `.txt` を付けて置いています。中の相対リンクが今のリポの文書リンク検査に掛からないようにするためで、中身は変えていません。
- 今のコードへ採り入れるかは、ファイルごとに別のPRで決めます。
- 公開側の履歴に同じ中身があるもの、行がすべて公開側にあるもの、ほかの版で行がそろうものは入れていません。
- 意図して外されていたもの（個人の情報を含む、公開側で削除済みなど）は入れていません。

## 入れたファイル

| 元のパス | 控えの枝・タグ | 置き場所 | 備考 |
|---|---|---|---|
| `.repo-preflight-consistency.json` | `codex/ahead-chore-ci-commit-20261007` ほか1 | `codex/ahead-chore-ci-commit-20261007/.repo-preflight-consistency.json` |  |
| `LICENSE` | `codex/ahead-chore-ci-commit-20261007` | `codex/ahead-chore-ci-commit-20261007/LICENSE` |  |
| `PREFLIGHT.md` | `codex/ahead-chore-ci-commit-20261007` | `codex/ahead-chore-ci-commit-20261007/PREFLIGHT.md.txt` |  |
| `README.md` | `codex/ahead-chore-ci-commit-20261007` | `codex/ahead-chore-ci-commit-20261007/README.md.txt` |  |
| `docs/NEXT_RUN.md` | `codex/ahead-chore-ci-commit-20261007` | `codex/ahead-chore-ci-commit-20261007/docs/NEXT_RUN.md.txt` |  |
| `docs/PROCESS_LIFECYCLE_DECISION.md` | `codex/ahead-chore-ci-commit-20261007` | `codex/ahead-chore-ci-commit-20261007/docs/PROCESS_LIFECYCLE_DECISION.md.txt` |  |
| `docs/architecture.md` | `codex/ahead-chore-ci-commit-20261007` | `codex/ahead-chore-ci-commit-20261007/docs/architecture.md.txt` |  |
| `docs/task_consolidation-2026-08-10.md` | `codex/ahead-chore-ci-commit-20261007` | `codex/ahead-chore-ci-commit-20261007/docs/task_consolidation-2026-08-10.md.txt` |  |
| `docs/task_consolidation-2026-08-14-claude-closeout.md` | `codex/ahead-chore-ci-commit-20261007` | `codex/ahead-chore-ci-commit-20261007/docs/task_consolidation-2026-08-14-claude-closeout.md.txt` |  |
| `src/runtime_process_guard/cli.py` | `codex/ahead-chore-ci-commit-20261007` | `codex/ahead-chore-ci-commit-20261007/src/runtime_process_guard/cli.py` |  |
| `src/runtime_process_guard/collector.py` | `codex/ahead-chore-ci-commit-20261007` ほか1 | `codex/ahead-chore-ci-commit-20261007/src/runtime_process_guard/collector.py` |  |
| `src/runtime_process_guard/shadow.py` | `codex/ahead-chore-ci-commit-20261007` ほか1 | `codex/ahead-chore-ci-commit-20261007/src/runtime_process_guard/shadow.py` |  |
| `tests/test_cli.py` | `codex/ahead-chore-ci-commit-20261007` ほか1 | `codex/ahead-chore-ci-commit-20261007/tests/test_cli.py` |  |
| `.github/workflows/ci.yml` | `tags/v0.1.0` | `tags/v0.1.0/.github/workflows/ci.yml` |  |
| `AGENTS.md` | `tags/v0.1.0` | `tags/v0.1.0/AGENTS.md.txt` |  |
| `PREFLIGHT.md` | `tags/v0.1.0` | `tags/v0.1.0/PREFLIGHT.md.txt` |  |
| `README.md` | `tags/v0.1.0` | `tags/v0.1.0/README.md.txt` |  |
| `REPO_GOAL.md` | `tags/v0.1.0` | `tags/v0.1.0/REPO_GOAL.md.txt` |  |
| `docs/NEXT_RUN.md` | `tags/v0.1.0` | `tags/v0.1.0/docs/NEXT_RUN.md.txt` |  |
| `docs/PROCESS_LIFECYCLE_DECISION.md` | `tags/v0.1.0` | `tags/v0.1.0/docs/PROCESS_LIFECYCLE_DECISION.md.txt` |  |
| `docs/adr/ADR-0001-runtime-owned-stdio-lifecycle.md` | `tags/v0.1.0` | `tags/v0.1.0/docs/adr/ADR-0001-runtime-owned-stdio-lifecycle.md.txt` |  |
| `docs/task_consolidation-2026-08-12-claude.md` | `tags/v0.1.0` | `tags/v0.1.0/docs/task_consolidation-2026-08-12-claude.md.txt` |  |
| `src/runtime_process_guard/guarded_stdio.py` | `tags/v0.1.0` | `tags/v0.1.0/src/runtime_process_guard/guarded_stdio.py` |  |
| `src/runtime_process_guard/lease_registry.py` | `tags/v0.1.0` | `tags/v0.1.0/src/runtime_process_guard/lease_registry.py` |  |
| `tests/test_guarded_stdio.py` | `tags/v0.1.0` | `tags/v0.1.0/tests/test_guarded_stdio.py` |  |
| `tests/test_lease_registry.py` | `tags/v0.1.0` | `tags/v0.1.0/tests/test_lease_registry.py` |  |

## 伏せたもの

- なし

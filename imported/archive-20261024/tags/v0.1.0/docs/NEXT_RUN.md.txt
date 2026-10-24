# 次回実行契約: Codex MCP起動経路への統合

## 状態

- status: local-owned-lifecycle-complete-awaiting-pilot-review
- owner: codex（このタスクの再開先）
- recorded_at: 2026-08-19T00:00:00+09:00
- last_verified_at: 2026-08-29T13:00:00+09:00
- project: `runtime-process-guard`
- canonical_repo: `Projects/Documents/.repos/nexus_ai/private/runtime-process-guard`
- scope: Codex配下のMCP起動経路を1経路だけ選び、pre-launch admission、runtime-owned lease、guarded launcherの限定pilotに接続する
- excluded: 別監視タスクの新設、`worktree-lifecycle-control` へのowner移管、固定間隔の外部killの再導入
- goal: Codex Desktopの各tool callでMCP世代が再蓄積する経路を、active sessionを保護したまま起動前のowner確定と終了後検証へ接続する
- done_when: local implementation、test、1種類のMCPでの人間review付きpilot、再起動後runtime受入、Git/worktree closeoutを個別に確認する
- external_boundary: process停止、Codex再起動、設定変更、hook有効化、Scheduled Task変更、commit、push、PR、merge、公開はそれぞれ別承認
- return_path: 実装とlocal test後に本書へ証拠を追記し、runtime操作の前に人間reviewへ戻す
- denied_scope: Claude設定変更、process一括停止、remote作成、push、公開、外部送信

## 2026-08-18負荷診断の吸収

2026-08-18のread-only実測タスクは、新規の常駐監視または別projectにせず、本契約へ吸収した。観測時はCodex app-server直下にMCP系processが多世代存在し、CPU 100%と長いprocessor queueを確認した。ただしPID、process数、CPU、memoryはスナップショットであり、次回操作前に再測定する。

吸収後の残務は次の3 laneに限定する。

1. **本体実装**: PR #7でpre-launch admission、永続lease registry、guarded launcher、Windows Job Object、heartbeat、graceful shutdown、Job全体postflightまで接続済み。
2. **runtime受入**: 人間review後、1種類のMCPだけでpilotし、Codex再起動前後の起動要求数、世代数、CPU queue、保護対象生存、postflightを比較する。
3. **Git/worktree closeout**: canonical checkoutのowner不明dirty差分を保護し、Scheduled Task参照をcanonicalへ移した後にだけdetached worktreeを整理し、branch、PR、default branch反映を別々に読み戻す。

`worktree-lifecycle-control` は3の安全手順に利用できるが、タスクownerは本repositoryから移さない。

## trigger_or_due

PCまたはCodexアプリの再起動後、このrepositoryを開いて本書を読み、ライブprocess treeを再測定して着手する。

## 実装順

1. [完了] `runtime_process_doctor.py` と `node_process_tree.py` で再起動後baselineを取得する。
2. [完了] Codex MCP起動経路の同一identity世代増殖を一次証拠で特定する。
3. [完了] 起動を止めない匿名 `shadow-snapshot` を実装する。
4. [完了] stdio MCPを単純reuseしないtransport-aware policyをテストする。
5. [完了] `feedback-cycle` で前回比較、trend判定、next action、state保存を1コマンドへ接続する。
6. [完了] lease registryを実process観測と永続registryへ接続する。
7. [完了] PR #7でテスト用guarded launcherとWindows Job Object adapterを実装し、Windows実processを使うshadow round-trip testを追加する。
8. [完了] graceful shutdown、timeout、Job close、起動前admission、runtime lease、同じowned Jobを照合するpostflightをlocal testする。
9. [待機] `HUMAN_REVIEW.md` にpilot証拠を追記し、1種類のMCPでのruntime操作の承認を得る。
10. [待機] 承認後のpilotと再起動後受入を実施する。

## evidence_path

- `reports/codex-mcp-baseline.json`
- `reports/codex-mcp-shadow.jsonl`
- `reports/codex-mcp-shadow-summary.json`
- repository test suite
- `reports/runtime-feedback-state.json`

レポートにはraw command line、環境変数、secret、ユーザー名、home絶対パスを保存しない。

## success_condition

- 対象MCPの起動要求数、unique identity数、重複候補数が匿名集計される。
- shadow modeは対象processを停止・抑止しない。
- privacy testと回帰testが成功する。
- block modeへ進めるかを、人間が判断できる証拠が揃う。

## failure_condition

- 起動経路を一次証拠で特定できない。
- command identity生成前にraw値を永続化する必要がある。
- Codex runtimeに安全なpre-launch接続点がない。
- active sessionと重複processを区別できない。

失敗時はfail-closedで設定を変えず、`reports/codex-mcp-shadow-summary.json` に原因と次の確認方法を残す。

## next_action

次はPR #7のCIが実行可能になった後、同一HEADを確認してmerge判断へ戻す。その後、Obsidian 1種類だけのshadow pilot設定を人間reviewし、再起動前後のlineageと負荷を再測定する。process停止、Codex再起動、enforce、block／kill、Scheduled Task変更への昇格は別承認とする。

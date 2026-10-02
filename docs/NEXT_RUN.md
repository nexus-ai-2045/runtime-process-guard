# 次回実行契約: Codex MCP起動経路への統合

## 状態

- status: code-pr-reviewing-runtime-pilot-deferred
- owner: codex（このタスクの再開先）
- recorded_at: 2026-08-19T00:00:00+09:00
- last_verified_at: 2026-10-02T22:36:00+09:00
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
9. [完了] `docs/MCP_LIFECYCLE_PILOT.md` と ADR-0003/0004 に限定試験、owner証拠ゲート、復帰条件を記録する。
10. [延期] PR #19のコード統合と実runtime採用を分離する。Obsidian設定の適用は利用者が後日に再開を選ぶまで行わない。

## evidence_path

- `reports/codex-mcp-baseline.json`
- `reports/codex-mcp-shadow.jsonl`
- `reports/codex-mcp-shadow-summary.json`
- repository test suite
- `reports/runtime-feedback-state.json`

レポートにはraw command line、環境変数、secret、ユーザー名、home絶対パスを保存しない。

## success_condition

- 対象MCPの起動要求数、unique identity数、重複候補数が匿名集計される。
- `shadow-snapshot` は観測のみで、対象processを停止・抑止しない。不完全観測は成功した受入証拠として扱わない。
- `guarded-stdio --mode shadow` は管理付き起動であり、admissionによる起動抑止、lease保存、所有Jobの終了後検証を行う。idle終了は無効。実runtimeへの接続には別途pilotの人間レビューを必要とする。
- privacy testと回帰testが成功する。
- block modeへ進めるかを、人間が判断できる証拠が揃う。

## failure_condition

- 起動経路を一次証拠で特定できない。
- command identity生成前にraw値を永続化する必要がある。
- Codex runtimeに安全なpre-launch接続点がない。
- active sessionと重複processを区別できない。

失敗時はfail-closedで設定を変えず、`reports/codex-mcp-shadow-summary.json` に原因と次の確認方法を残す。

## next_action

PR #7、#9、#10はマージ済み。PR #19で接続上限、所有Jobの終了管理、stdio owner診断をレビューする。
コード統合後もDesktopへのguard配線は行わない。再開時はdirect stdioの最新baseline、接続ごとのowner証拠またはEOF境界、
ローカルでのguard追加コストを確認して設定差分と復帰方法をレビューする。Desktopでの起動時間・メモリ・handle・残留Jobと改善効果は、限定適用後に判定する。
process停止、Codex再起動、設定変更、enforce、block／kill、Scheduled Task変更への昇格は別承認とする。

## 2026-10-02: PR #19とruntime採用の分離

- ローカルstdio比較は直起動・guard経由とも30/30成功し、guard終了後のlease残留は0件だった。
- guard経由は局所比較で起動時間・process数・メモリ・handle数が増えたため、負荷削減効果は未証明である。
- Desktopの継承stdio probeは共通長寿命peerまでしか示さず、接続ごとのowner証明にはならなかった。
- 実Desktop設定、既存process、plugin cacheは変更せず、コードPRの統合だけを先に判定する。
- owner観測が途中でunknownになった場合もgraceful shutdownへ入るfail-closed回帰を追加した。

## 2026-09-20: 観測欠落修正の検証と残務

- scope: collector・shadow観測・CLI・既存feedbackへの欠落伝達。別repoへの移管やruntime設定は対象外。
- 根因: psutilが読取不能な属性を既定の `None` に変換するため、空のコマンドと区別しない実装では欠落を見落としていた。
- 修正: 欠落・不正型・親探索の打切り・範囲外時刻を不完全観測へ分類し、shadow CLIは終了コード40を返す。正常なfeedback stateを不完全観測で上書きしない。
- 回帰テスト: 欠落、正常な空コマンド、専用stdio、資源不足、非有限・範囲外時刻、JSONと保存reportの一致、feedback state保持。
- 本線の全体試験: Windows / Python 3.13で `133 passed, 4 skipped, 1 failed`。失敗は既存の `test_windows_shadow_round_trip_and_eof_cleanup` の10秒timeout。単独再実行でも再現した。
- 比較: admission・collector・CLI・guarded launcher・Windows Job・lease registry・postflight・該当testが `origin/baseline` と一致する別checkoutでも同じtimeoutを再現。今回差分だけの回帰とは認められないが、原因確定・実runtime受入の証拠でもない。
- `compileall` と `git diff --check` は成功。独立コードレビューでは今回差分のP1/P2所見なし。
- 読取専用の実機shadowスモーク（2026-09-20 02:00 JST）は、観測195件・読取不能1件に対して `complete=false` / `overall=unknown` / 終了コード40を確認。所要96.63秒であり、観測処理の実機性能は未保証。

再実行はrepo rootで `python -m pytest -q --basetemp=<この実行専用の一時ディレクトリ>`。
結果は対象HEADに結び付け、過去の合格や未起動CIで置き換えない。

| 残務 | owner | 次の行動と完了条件 |
|---|---|---|
| Windows実プロセス試験のtimeout | 実装担当 | CIと同一HEADの結果を回収し、環境条件と起動・観測・終了のどこで遅延するか特定。期限を緩めるだけで合格にしない |
| 後続PRの受入 | 実装担当＋人間 | 同一HEADのCI・レビュー・差分を確認し、merge直前に判断する |
| 実runtimeの長期pilot | 運用担当＋人間 | 対象1種類と具体的設定差分をレビュー後、既存の期間・回数・保護対象条件を実測する |
| 旧checkoutの未コミット差分 | この実装タスク | 後続PRとの同等性を確認してから整理候補を提示。既存WIPやworktreeを自動削除しない |

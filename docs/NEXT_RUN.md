# 次回実行契約: Codex MCP起動経路への統合

## 状態

- status: committed-next
- owner: codex（このタスクの再開先）
- recorded_at: 2026-08-01T02:12:30+09:00
- scope: Codex配下のMCP起動経路を1経路だけ選び、`runtime-process-guard preflight` を接続する
- denied_scope: Claude設定変更、process一括停止、remote作成、push、公開、外部送信

## trigger_or_due

PCまたはCodexアプリの再起動後、このrepositoryを開いて本書を読み、ライブprocess treeを再測定して着手する。

## 実装順

1. `runtime_process_doctor.py` と `node_process_tree.py` で再起動後baselineを取得する。
2. Codex MCP起動経路のうち、同一identityが増殖する1経路を一次証拠で特定する。
3. 起動前preflightをshadow modeで接続する。shadowでは起動を止めない。
4. false positive、identity衝突、観測不能率をテスト・実測する。
5. block modeへの昇格案を人間レビューへ返す。承認前にblockを有効化しない。

## evidence_path

- `reports/codex-mcp-baseline.json`
- `reports/codex-mcp-shadow.jsonl`
- `reports/codex-mcp-shadow-summary.json`
- repository test suite

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

再起動後baselineを取得し、shadow integrationの対象1経路と変更ファイルを提示してから実装する。

# runtime-process-guard

ローカルの subprocess、MCP server、automation を起動する**前**に、重複・CPU・メモリを確認するための privacy-first admission control です。

## 現在の範囲

- read-only の `preflight`、`shadow-snapshot`、`feedback-cycle`、`lineage-snapshot`
- 同一 command identity の既存プロセスを検出
- Codex配下processの世代幅、前回差分、親子lineageを匿名集計
- `allow` / `reuse` / `defer` / `deny` / `unknown` を機械可読JSONで返す
- Windows、macOS、Linuxで同じ判定語彙を使用
- プロセス停止、設定変更、外部送信、telemetry は行わない

## privacy boundary

次の値は標準出力、レポート、stateへ保存しません。

- raw command lineと引数
- 環境変数
- API key、token、password、secret
- ユーザー名を含む絶対パス

照合用identityは、機密値とhome pathを正規化した後のSHA-256です。表示は実行ファイル名、identityの先頭12文字、集計値だけです。

## 使用例

```powershell
$env:PYTHONPATH = "src"  # editable install前のPowerShell開発実行
python -m runtime_process_guard.cli preflight -- node server.mjs --stdio
python -m runtime_process_guard.cli preflight --json -- node server.mjs --stdio
python -m runtime_process_guard.cli preflight --reuse-policy dedicated-stdio -- node server.mjs --stdio
python -m runtime_process_guard.cli shadow-snapshot --owner codex.exe --process-name node.exe --json
python -m runtime_process_guard.cli feedback-cycle --owner codex.exe --process-name node.exe --state-path reports/runtime-feedback-state.json --json
python -m runtime_process_guard.cli lineage-snapshot --owner codex.exe --recent-minutes 10 --report-path reports/codex-lineage.json --mermaid-path reports/codex-lineage.md
python -m runtime_process_guard.cli lineage-snapshot --owner codex.exe --previous-report reports/codex-lineage.json --report-path reports/codex-lineage-next.json --mermaid-path reports/codex-lineage-next.md
```

既定出力は人間向けのoperational command contract形式です。自動回収では `--json` を指定します。

`feedback-cycle` は1回の実行でshadow snapshotを取得し、前回stateと比較して `baseline / stable / improving / worsening` を判定し、次の安全なactionと今回stateをatomicに保存します。2サイクル連続でprocess数またはgeneration下限が増えた場合だけ `human-review-runtime-pressure` へ上げます。process停止、設定変更、外部送信は行いません。

stateの対象不一致、schema不正、観測不能、同時実行lockでは既存stateを上書きせず、終了コード`40`で修復を要求します。初回baselineの `unknown` は正常な観測開始なので終了コード`0`です。

終了コード:

- `0`: `allow`
- `10`: `reuse`
- `20`: `defer`
- `30`: `deny`
- `40`: `unknown`

`preflight` は対象プロセスを起動しません。呼び出し側は判定結果を確認してから起動してください。

stdio MCPはclientごとに専用pipeを持つため、既存processへ単純にreuseできません。`--reuse-policy dedicated-stdio` は同一identityを観測してもreuseせず、resource pressureだけで `allow` / `defer` を判断します。

## 開発

```powershell
python -m pytest -q
```

## 非目標

- 名前一致による一括kill
- OS全体へのhook注入
- command lineの監査ログ化
- 自動的な公開、送信、クラウド同期

## Repository visibility

このrepositoryはprivate運用を前提とします。ライセンスはAll rights reservedで、remote作成とpushはローカル実装とは別の承認境界です。

## Next run

Codex MCP起動経路への統合は [`docs/NEXT_RUN.md`](docs/NEXT_RUN.md) の再開契約に従います。最初はshadow modeとし、人間レビュー前にprocess起動をblockしません。

ローカル実測、既存の安全回収、Windows Job Objects、Codexの既知事例を統合した採否判断は [`docs/PROCESS_LIFECYCLE_DECISION.md`](docs/PROCESS_LIFECYCLE_DECISION.md) を参照してください。

定期観測、異常判定、親子差分、回復後検証の運用手順は [`docs/RUNTIME_PDCA.md`](docs/RUNTIME_PDCA.md) を参照してください。

`lineage-snapshot` はPIDだけでなく作成時刻も比較し、PID再利用を新規・消滅の両方として記録します。Mermaidでは前回から追加されたプロセスを緑で表示し、raw command lineやユーザーpathは保存しません。

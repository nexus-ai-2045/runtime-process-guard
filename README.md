# runtime-process-guard

ローカルの subprocess、MCP server、automation を起動する**前**に、重複・CPU・メモリを確認するための privacy-first admission control です。

## 現在の範囲

- read-only の `preflight` のみ
- 同一 command identity の既存プロセスを検出
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
python -m runtime_process_guard.cli preflight -- node server.mjs --stdio
python -m runtime_process_guard.cli preflight --json -- node server.mjs --stdio
```

既定出力は人間向けのoperational command contract形式です。自動回収では `--json` を指定します。

終了コード:

- `0`: `allow`
- `10`: `reuse`
- `20`: `defer`
- `30`: `deny`
- `40`: `unknown`

`preflight` は対象プロセスを起動しません。呼び出し側は判定結果を確認してから起動してください。

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

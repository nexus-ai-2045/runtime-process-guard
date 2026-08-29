# runtime-process-guard

ローカルのsubprocess、MCP server、automationを起動する前に、重複・CPU・メモリ・親子関係を確認するprivacy-firstの実行前ガードです。

既定動作はread-onlyです。プロセス停止、設定変更、外部送信、telemetryは行いません。

## できること

| 機能 | 用途 |
|---|---|
| `preflight` | 同一command identityとresource pressureを確認し、起動可否を返す |
| `shadow-snapshot` | Codex配下processの数・世代幅・メモリを匿名集計する |
| `feedback-cycle` | 前回stateと比較して`stable` / `improving` / `worsening`を判定する |
| `lineage-snapshot` | PID・作成時刻・親子lineageの差分をJSON／Mermaidへ出す |
| Windows plugin policy | plugin更新後の`npx`起動を`conhost.exe --headless`へ調停する |

Windows、macOS、Linuxで共通の判定語彙を使います。

## 安全境界

次の情報は標準出力、receipt、stateへ保存しません。

- raw command lineと引数
- 環境変数
- API key、token、password、secret
- ユーザー名を含む絶対パス

照合用identityは、機密値とhome pathを正規化した後のSHA-256です。表示するのは実行ファイル名、identityの先頭12文字、集計値だけです。

このrepositoryは次を行いません。

- 名前一致による一括kill
- OS全体へのhook注入
- command lineの監査ログ化
- 自動的な公開、送信、クラウド同期

## クイックスタート

```powershell
python -m pip install -e .
python -m runtime_process_guard.cli preflight -- node server.mjs --stdio
```

JSONが必要な場合：

```powershell
python -m runtime_process_guard.cli preflight --json -- node server.mjs --stdio
```

editable install前に直接試す場合：

```powershell
$env:PYTHONPATH = "src"
python -m runtime_process_guard.cli preflight -- node server.mjs --stdio
```

`preflight`は対象プロセスを起動しません。呼び出し側が判定結果を確認してから起動してください。

## 判定と終了コード

| 終了コード | 判定 | 意味 |
|---:|---|---|
| `0` | `allow` | 起動を許可できる |
| `10` | `reuse` | 既存processの再利用候補がある |
| `20` | `defer` | 現時点では起動を延期する |
| `30` | `deny` | 定義済みpolicyにより拒否する |
| `40` | `unknown` | 安全に判定できないため停止する |

stdio MCPはclientごとに専用pipeを持つため、既存processへ単純にreuseできません。`--reuse-policy dedicated-stdio`では、同一identityを観測してもreuseせず、resource pressureだけで`allow` / `defer`を判断します。

## 主なコマンド

### 実行前確認

```powershell
python -m runtime_process_guard.cli preflight --reuse-policy dedicated-stdio -- node server.mjs --stdio
```

### 現在の匿名snapshot

```powershell
python -m runtime_process_guard.cli shadow-snapshot --owner codex.exe --process-name node.exe --json
```

### 前回との差分と傾向

```powershell
python -m runtime_process_guard.cli feedback-cycle `
  --owner codex.exe `
  --process-name node.exe `
  --state-path reports/runtime-feedback-state.json `
  --json
```

`feedback-cycle`は`baseline` / `stable` / `improving` / `worsening`を判定します。process数またはgeneration下限が2サイクル連続で増えた場合だけ、`human-review-runtime-pressure`へ上げます。

stateの対象不一致、schema不正、観測不能、同時実行lockでは既存stateを上書きせず、終了コード`40`で停止します。初回baselineの`unknown`は正常な観測開始なので終了コード`0`です。

### 親子lineageの差分

```powershell
python -m runtime_process_guard.cli lineage-snapshot `
  --owner codex.exe `
  --recent-minutes 10 `
  --report-path reports/codex-lineage.json `
  --mermaid-path reports/codex-lineage.md

python -m runtime_process_guard.cli lineage-snapshot `
  --owner codex.exe `
  --previous-report reports/codex-lineage.json `
  --report-path reports/codex-lineage-next.json `
  --mermaid-path reports/codex-lineage-next.md
```

PIDだけでなく作成時刻も比較するため、PID再利用を新規・消滅の両方として記録します。Mermaidでは前回から追加されたprocessを緑で表示します。

## guarded stdio（限定pilot用）

guard自身が起動したstdio serverだけをWindows Job Objectで所有する入口です。既定はshadowで、
stdin EOF時の通常終了は管理しますが、idle timeoutによる終了は行いません。

```powershell
runtime-process-guard guarded-stdio --mode shadow --lease-state <state-path> --idle-seconds 600 --grace-seconds 30 -- <executable> <args...>
```

`--mode enforce` はactive JSON-RPC request追跡が未接続のため、現在はfail-closedで拒否します。
Obsidian 1種類のshadow受入、request追跡の実装、再レビュー後にだけ解禁します。commandは引数列として直接起動され、shell文字列は受け付けません。
`--lease-state` は明示必須です。registryには匿名identity、owner PID/生成時刻、heartbeat、期限だけを保存し、argv、環境変数、MCP本文は保存しません。

## Windows plugin policy

`scripts/reconcile_codex_plugin_windows.py`は、plugin cacheの`plugin.json` / `.mcp.json`にあるstdio MCPの`npx`起動だけを`conhost.exe --headless`で包みます。

- `plugin.json`：`mcpServers`配下だけが対象
- `.mcp.json`：トップレベルのserver定義が対象
- plugin metadataはMCP設定として扱わない
- symlink、junction、reparse pointの配下へ降りない
- receiptへplugin path、server名、command lineを保存しない

### まずread-onlyで確認

```powershell
python scripts/reconcile_codex_plugin_windows.py `
  --receipt reports/plugin-window-policy-latest.json
```

変更候補は`pending`として報告され、plugin cacheは変更されません。

### 書込みは別承認

```powershell
python scripts/reconcile_codex_plugin_windows.py `
  --receipt reports/plugin-window-policy-latest.json `
  --apply
```

`--apply`はplugin cacheを変更します。Scheduled Task登録、設定変更、実runtimeへの適用と同じく、人間の明示承認後だけ実行してください。

異常時はfail-closedです。

| 状況 | 結果 |
|---|---|
| cacheを走査できない | `invalid` / `unknown` |
| `npx`定義を安全に変換できない | `unsupported` / `unknown` |
| scan後にmanifestが変化した | `conflict` / `defer` |
| 複数manifestの途中で書込み失敗 | 自動rollbackせず、`partial-write` / `unknown`として報告 |
| scan後に未処理manifestの第三者更新を検出 | その更新を保持し、途中適用済みなら`partial-write` / `unknown` |

複数manifestを通常のfilesystem操作だけで完全なtransactionとして扱うことはできません。第三者更新を上書きしないことを優先し、途中失敗時は適用済みmanifestを保持して人間確認へ止めます。

原本のdurableな複製は既定で作りません。manifestにはraw command lineや`env`が含まれ得るためです。人手の復旧用に控えが必要な場合だけ`--backup`を明示すると、同じディレクトリへ`<name>.pre-headless.bak`を作ります。

## 開発と検証

```powershell
python -m pytest -q
git diff --check
```

変更ルールは[CONTRIBUTING.md](CONTRIBUTING.md)、security報告は[SECURITY.md](SECURITY.md)を参照してください。

## 運用文書

| 文書 | 内容 |
|---|---|
| [NEXT_RUN.md](docs/NEXT_RUN.md) | 次回再開時の契約とshadow pilot |
| [PROCESS_LIFECYCLE_DECISION.md](docs/PROCESS_LIFECYCLE_DECISION.md) | safe recovery、Windows Job Objects、採否判断 |
| [RUNTIME_PDCA.md](docs/RUNTIME_PDCA.md) | 定期観測、異常判定、親子差分、回復後検証 |

## Repository visibility

このrepositoryはprivate運用を前提とします。ライセンスはAll rights reservedです。push、Pull Request、merge、visibility変更、公開はそれぞれ別の承認境界です。

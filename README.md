# runtime-process-guard

ローカルのsubprocess、MCP server、automationを起動する前に、重複・CPU・メモリ・親子関係を確認するprivacy-firstの実行前ガードです。

観測・起動判定の入口はread-onlyです。明示的に選ぶ `guarded-stdio` は子プロセスの起動と終了を管理します。設定変更、外部送信、telemetryは行いません。

## 目的

ローカルで subprocess / MCP / automation を起動する直前に、重複・資源圧・親子関係を匿名で判定し、危険な一括停止や秘密の保存をせずに起動可否を返す。

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

次の URL を AI に貼り、先に危険レビューを出させてください。削除・GitHub write・visibility・secret・unknown を安全と読まないこと。

https://github.com/nexus-ai-2045/runtime-process-guard

貼付後の確認例（人が叩く手順ではない）: `python -m runtime_process_guard.cli preflight -- node server.mjs --stdio`。`preflight` は対象を起動しません。

## 判定と終了コード

| 終了コード | 判定 | 意味 |
|---:|---|---|
| `0` | `allow` | 起動を許可できる |
| `10` | `reuse` | 既存processの再利用候補がある |
| `20` | `defer` | 現時点では起動を延期する |
| `30` | `deny` | 定義済みpolicyにより拒否する |
| `40` | `unknown` | 安全に判定できないため停止する |

stdio MCPはclientごとに専用pipeを持つため、既存processへ単純にreuseできません。`--reuse-policy dedicated-stdio`では、同一identityを観測してもreuseせず、resource pressureだけで`allow` / `defer`を判断します。

singleton判定では、コマンドを読み取れないprocessがあれば `unknown` を返します。
`dedicated-stdio` は他processを再利用しないため、その欠落を件数として伝えつつ資源予算を判定します。
process一覧や資源計測そのものに失敗した場合は、どちらも `unknown` です。

## 主なコマンド

### 実行前確認

```powershell
python -m runtime_process_guard.cli preflight --reuse-policy dedicated-stdio -- node server.mjs --stdio
```

### 現在の匿名snapshot

```powershell
python -m runtime_process_guard.cli shadow-snapshot --owner codex.exe --process-name node.exe --json
```

対象候補の属性・親子関係を確認できない場合は、不完全な観測として終了コード `40`、
`overall=unknown`、`complete=false`、`next_action=repair-observation` を返します。
観測できた件数は残しますが、processが存在しない証拠やpilotの成功回数には使いません。
`feedback-cycle` もその観測では前回の正常stateを上書きしません。

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

guard自身が起動したstdio serverだけをWindows Job Objectで所有する、承認後の限定pilot用入口です。
既存CLIとの互換性のためmode名は `shadow` を維持していますが、読み取り専用ではありません。
起動前admissionで資源不足・観測失敗時の起動を抑止し、leaseを保存し、子を起動します。
stdin EOF、relay終了、guardの中断時には通常終了を待ち、猶予超過や子孫残存時には所有Jobを終了して消滅を検証します。
`shadow` が無効にするのはidle timeoutによる終了です。観測のみには `shadow-snapshot` を使います。

```powershell
runtime-process-guard guarded-stdio --mode shadow --lease-state <state-path> --idle-seconds 600 --grace-seconds 30 -- <executable> <args...>
```

`--mode enforce` はactive JSON-RPC request追跡が未接続のため、現在はfail-closedで拒否します。
Obsidian 1種類のshadow受入、request追跡の実装、再レビュー後にだけ解禁します。commandは引数列として直接起動され、shell文字列は受け付けません。
`--lease-state` は明示必須です。registryには匿名identity、owner PID/生成時刻、heartbeat、期限だけを保存し、argv、環境変数、MCP本文は保存しません。

## Windows plugin policy

receipt の `overall` は plugin cache の window policy だけを評価します。
`coverage.process_lifecycle` は常に `not-evaluated` であり、`overall=ok` はprocess lifecycleの健全性を意味しません。

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

## 必要時起動と接続上限

接続単位の必要時起動・終了後回収を行う限定経路は、既存の `guarded-stdio` を使用します。
`--max-server-instances 2 --max-total-instances 4` を明示すると、共通lease registryのロック内で起動枠を予約します。
上限に達した場合は新起動だけを終了コード20で拒否し、既存接続を保持します。無指定の旧CLIは従来互換であり、自動的に2/4上限が適用されるわけではありません。
最初のtool callまでの遅延起動、通信空白によるidle停止、既存PIDの回収は行いません。
実runtime適用前に [限定pilotの契約](docs/MCP_LIFECYCLE_PILOT.md) を確認してください。

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

このrepositoryは public（MIT）です。push、Pull Request、merge、Release 作成はそれぞれ別の承認境界です。

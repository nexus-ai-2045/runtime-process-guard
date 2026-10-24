# 人間レビュー: Codex MCP generation pressure

## 記録情報

- schema_version: `runtime-process-guard/review-v1`
- recorded_at: `2026-08-01T12:50:32+09:00`
- recorded_by: `codex`
- owner: `codex`
- scope: Codex Desktop配下で各tool call時に生成されるplugin MCP process
- external_boundary: process停止、設定変更、再起動、remote、push、公開は未実行

## 結論

推奨は、既存processのreuseや自動killではなく、**不要なplugin MCPをserver単位で無効化し、1回のCodex再起動ごとに世代幅が減るか検証する段階パイロット**です。

stdio MCPはclientごとに専用pipeを持つため、同一command identityだけを根拠に既存processへreuseすると接続を壊す可能性があります。`runtime-process-guard` は `dedicated-stdio` policyを追加し、この場合は重複を観測してもreuseしません。

## 検証済み事実

| claim | source | actor | event_time | observed_at | scope |
|---|---|---|---|---|---|
| Codex配下のNodeは36 processだった | `reports/codex-mcp-baseline.json` | runtime-process-guard | unknown | 2026-08-01T12:50:32+09:00 | local Windows host / owner codex.exe / process node.exe |
| 匿名identityは4種類で、世代ごとに同じ4種類がまとまって生成された | `reports/codex-mcp-baseline.json` | runtime-process-guard | 2026-08-01T12:37:53+09:00以降 | 2026-08-01T12:50:32+09:00 | Codex descendant node.exe |
| 2秒窓によるgeneration lower boundは7だった | `reports/codex-mcp-baseline.json` | runtime-process-guard | unknown | 2026-08-01T12:50:32+09:00 | Codex descendant node.exe |
| 4 identityは `creative-production`、`data-analytics`、`codex-security`、`openai-developers` のstdio MCPに対応する | plugin `.mcp.json` と匿名process cwd分類 | Codex plugin runtime | unknown | 2026-08-01T12:50:32+09:00 | installed plugin cache and live process ancestry |
| `creative-production` pluginとserverはconfig上falseだが、Codex起動時刻の約40秒後にconfigが更新されていた | `%CODEX_HOME%/config.toml` metadata and process start time | filesystem / Windows process API | 2026-08-01T12:38:14+09:00 | 2026-08-01T12:50:32+09:00 | current Codex Desktop session |

## 不明点

| unknown | reason | verification_next |
|---|---|---|
| `creative-production=false` が次回起動で反映されるか | 現在sessionはconfig更新より先に起動している | Codexを1回再起動し、最初のshadow snapshotでidentity `d46d9edf8e1b` の有無を確認 |
| 各stdio MCPがactive request処理中か、回収漏れか | process identityと親chainだけではstdio pipeの利用状態を証明できない | blockせず、世代の増加と終了を時系列shadow記録する |
| Codex製品runtimeに公式pre-launch hookがあるか | ローカルplugin manifestにはhook宣言がない | 製品仕様確認。cache改変は行わない |

## 推奨パイロット

### 正確な操作

1. 現在の作業がcommit済みであることを確認する。
2. Codex Desktopアプリを通常終了して再度起動する。
3. 設定ファイルは変更しない。
4. 起動直後に次をread-onlyで実行する。

```powershell
$env:PYTHONPATH='src'
python -m runtime_process_guard.cli shadow-snapshot `
  --owner codex.exe `
  --process-name node.exe `
  --json `
  --report-path reports/codex-mcp-after-creative-disabled.json
```

### 成功条件

- `creative-production` identity `d46d9edf8e1b` が0。
- 1世代のprocess幅が4から3へ減る。
- Codexの通常tool callが成功する。

### 失敗条件

- identityが残る。
- tool callが失敗する。
- process幅が変わらない、または別identityが増える。

失敗時は設定を追加変更せず、結果をreviewへ戻します。

## 次段階の候補

パイロット成功後に限り、使用頻度と必要機能を人間が確認したうえで、次のserver-level設定を1つずつfalseにする案をレビューします。

- `openai-api-key-local-confirmation`
- `dataAnalyticsWidgets`
- `codex-security`

plugin全体のskillは保持し、MCP serverだけを対象にします。一括変更、自動kill、plugin cacheの直接編集は採用しません。

## 人間判断

上記の「設定変更なし・Codex Desktop 1回再起動・read-only再測定」パイロットを実行してよいか判断してください。

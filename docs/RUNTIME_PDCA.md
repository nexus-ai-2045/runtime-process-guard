# Windows runtime PDCA運用

## 目的

プロセス数、CPU待ち行列、メモリ、ディスク、親子・孫チェーンを分離して観測し、観測器自身の負荷と実際のリークを混同しない。

## Plan

1. `runtime_process_watch.py --dry-run` で量的tripを確認する。
2. `windows_performance_pdca.py --json` でCPU、Processor Queue Length、空きメモリ、ページング、ディスクキューを5標本確認する。
3. tripまたは高負荷が継続する時だけ、psutilベースの親子診断と本repositoryの`lineage-snapshot`を実行する。
4. 前回JSONを`--previous-report`へ渡し、追加・消滅・安定プロセスを作成時刻込みで比較する。

## Do

```powershell
$env:PYTHONPATH = "src"
python -m runtime_process_guard.cli lineage-snapshot `
  --owner codex.exe `
  --recent-minutes 10 `
  --previous-report reports/codex-lineage.json `
  --report-path reports/codex-lineage-next.json `
  --mermaid-path reports/codex-lineage-next.md
```

この段階はread-onlyである。raw command line、ユーザー名、home絶対path、secretは保存しない。

## Check

- `added_count`が観測ごとに増え続けるか。
- 同一匿名identityが複数世代に積層しているか。
- `codex.exe -> cmd.exe -> node.exe -> cmd.exe -> node.exe` の深さが増えているか。
- CPU 100%だけでなくProcessor Queue Lengthが複数標本で高いか。
- Pages/secとディスクキューが同時に高いか。
- Task Manager、PowerShell、WMI/CIMなど観測器自身のCPUを分離したか。
- `overall=unknown` または `complete=false` なら差分を運用判断へ使わず、権限・欠損processを修復して再取得する。

### stale feedback lock

`next_action=repair-stale-feedback-lock` は、lockに記録されたPIDと作成時刻のprocessが既に存在しないことを検出した状態である。競合中の有効lockを誤削除しないため自動回収はしない。`feedback-cycle`が動いていないことを人間が再確認してから、対象stateと同じdirectoryの `<state-file>.lock` だけを削除し、1回再実行する。PIDまたは作成時刻を確認できないlockはstaleと推定しない。

## Act

1. 正常なら変更しない。
2. 重複世代が確認できても、active stdio接続またはowner leaseが不明ならreport-onlyへ戻す。
3. 回収する場合は最新世代を残し、作成時刻を再照合したexact PIDだけを子孫leaf-firstで扱う。
4. app-root、自分のchain、生存session、一般Node、WSL、WMIを名前一致で停止しない。
5. 回収後は対象PID消滅、保護対象生存、総数、空きメモリ、CPU待ち行列を再測定する。

## 2026-08-09の機械的エラーと修正

- 症状: `runtime_process_doctor.py` が巨大なPowerShell/WMI JSONの`Invalid \\escape`で全観測を中断した。
- 根本原因: プロセス収集がPowerShell/WMI文字列変換へ依存し、1件の不正command lineで全件を失うL2設計だった。
- 修正: 共有診断器のプロセス収集を単一psutil snapshotへ置換し、PowerShellはWindows Event Logの限定取得だけに縮退した。
- 回帰証拠: Windows backslashを含むcommand line fixtureを追加し、JSON round-tripを通さず保持する。
- 実機証拠: 修正後の診断は`process_total=555`を返し、例外なしで完走した。

回収器にも同じPowerShell多重起動問題があり、19 PIDの再発回収をpsutil版で検証した。旧実装は209〜288 PIDで2〜5分の実行上限に達したが、psutil版は19 PIDを17.3秒で全件終了し、skip 0、直後の再判定は8最新世代・終了候補0だった。列挙と停止のためのPowerShell/WMI呼び出しは不要になった。

## 回収前後の親子差分

2026-08-09 12:40 JSTのCodex配下168プロセスを基準に、13:55 JSTに再測定した。

- 現存: 43
- 前回から追加: 38
- 前回から消滅: 163
- 同一PIDかつ同一作成時刻で安定: 5
- 最大深さ: 5

大量世代の回収効果は確認できた。一方、追加38は現行セッションの新規起動を含むため、追加数だけで再リークとは判定しない。今後の観測では同一identityの世代数と連続増加を合わせて確認する。

## 停止線

設定変更、Scheduled Task登録、常時自動kill、Job Objectによる強制終了、remote作成は個別レビュー対象とする。read-only shadowが十分な履歴を持つ前にenforcementへ昇格しない。

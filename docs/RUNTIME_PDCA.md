# 実行環境の負荷を計測して改善する

## 目的

プロセス数、`CPU`待ち行列、メモリ、ディスク、親子・孫チェーンを分離して観測し、観測器自身の負荷と実際のリークを混同しない。

## 計画

1. `runtime_process_watch.py --dry-run` で量的`trip`を確認する。
2. `windows_performance_pdca.py --json` で`CPU`、`Processor` `Queue` `Length`、空きメモリ、ページング、ディスクキューを5標本確認する。
3. `trip`または高負荷が継続する時だけ、`psutil`ベースの親子診断と本`repository`の`lineage-snapshot`を実行する。
4. 前回`JSON`を`--previous-report`へ渡し、追加・消滅・安定プロセスを作成時刻込みで比較する。

## 実行

```powershell
$env:PYTHONPATH = "src"
python -m runtime_process_guard.cli lineage-snapshot `
  --owner codex.exe `
  --recent-minutes 10 `
  --previous-report reports/codex-lineage.json `
  --report-path reports/codex-lineage-next.json `
  --mermaid-path reports/codex-lineage-next.md
```

この段階は`read-only`である。`raw` `command` `line`、ユーザー名、`home`絶対`path`、`secret`は保存しない。

## 確認

- `added_count`が観測ごとに増え続けるか。
- 同一匿名`identity`が複数世代に積層しているか。
- `codex.exe -> cmd.exe -> node.exe -> cmd.exe -> node.exe` の深さが増えているか。
- `CPU` 100%だけでなく`Processor` `Queue` `Length`が複数標本で高いか。
- `Pages/sec`とディスクキューが同時に高いか。
- `Task` `Manager`、`PowerShell`、`WMI/CIM`など観測器自身の`CPU`を分離したか。
- `overall=unknown` または `complete=false` なら差分を運用判断へ使わず、権限・欠損`process`を修復して再取得する。

### 停滞した改善履歴のロック

`next_action=repair-stale-feedback-lock` は、`lock`に記録された`PID`と作成時刻の`process`が既に存在しないことを検出した状態である。競合中の有効`lock`を誤削除しないため自動回収はしない。`feedback-cycle`が動いていないことを人間が再確認してから、対象`state`と同じ`directory`の `<state-file>.lock` だけを削除し、1回再実行する。`PID`または作成時刻を確認できない`lock`は`stale`と推定しない。

## 改善

1. 正常なら変更しない。
2. 重複世代が確認できても、`active` `stdio`接続または`owner` `lease`が不明なら`report-only`へ戻す。
3. 回収する場合は最新世代を残し、作成時刻を再照合した`exact` `PID`だけを子孫`leaf-first`で扱う。
4. `app-root`、自分の`chain`、生存`session`、一般`Node`、`WSL`、`WMI`を名前一致で停止しない。
5. 回収後は対象`PID`消滅、保護対象生存、総数、空きメモリ、`CPU`待ち行列を再測定する。

## 2026-08-09の機械的エラーと修正

- 症状: `runtime_process_doctor.py` が巨大な`PowerShell/WMI` `JSON`の`Invalid \\escape`で全観測を中断した。
- 根本原因: プロセス収集が`PowerShell/WMI`文字列変換へ依存し、1件の不正`command` `line`で全件を失う`L2`設計だった。
- 修正: 共有診断器のプロセス収集を単一`psutil` `snapshot`へ置換し、`PowerShell`は`Windows` `Event` `Log`の限定取得だけに縮退した。
- 回帰証拠: `Windows` `backslash`を含む`command` `line` `fixture`を追加し、`JSON` `round-trip`を通さず保持する。
- 実機証拠: 修正後の診断は`process_total=555`を返し、例外なしで完走した。

回収器にも同じ`PowerShell`多重起動問題があり、19 `PID`の再発回収を`psutil`版で検証した。旧実装は209〜288 `PID`で2〜5分の実行上限に達したが、`psutil`版は19 `PID`を17.3秒で全件終了し、`skip` 0、直後の再判定は8最新世代・終了候補0だった。列挙と停止のための`PowerShell/WMI`呼び出しは不要になった。

## 回収前後の親子差分

2026-08-09 12:40 `JST`の`Codex`配下168プロセスを基準に、13:55 `JST`に再測定した。

- 現存: 43
- 前回から追加: 38
- 前回から消滅: 163
- 同一`PID`かつ同一作成時刻で安定: 5
- 最大深さ: 5

大量世代の回収効果は確認できた。一方、追加38は現行セッションの新規起動を含むため、追加数だけで再リークとは判定しない。今後の観測では同一`identity`の世代数と連続増加を合わせて確認する。

## 停止線

設定変更、`Scheduled` `Task`登録、常時自動`kill`、`Job` `Object`による強制終了、`remote`作成は個別レビュー対象とする。`read-only` `shadow`が十分な履歴を持つ前に`enforcement`へ昇格しない。


## 対象を絞った診断の取得負荷を抑える

`shadow-snapshot` は、プロセス名と親子関係で対象を判定してから、対象のコマンド情報と作成時刻を取得する。対象外のコマンド情報を全件先読みしない。情報取得中に対象が消えたり権限不足になった場合は、不完全な観測として扱う。判定条件、出力形式、停止権限は変更しない。

変更の確認では、同じホストで変更前と変更後を交互に各4回実行し、`Process.cmdline()` の呼出し数と処理時間を計測した。呼出し数の中央値は1,504回から725回へ約52%減少し、8回すべての観測が完全だった。処理時間の中央値は0.341秒から0.328秒で、同時に動く処理の影響を分離できていない。取得回数の削減を確認した結果であり、デスクトップアプリの画面操作や返信の高速化を証明するものではない。

名前で除外されたプロセスと親が対象外のプロセスについて、コマンド情報を読まない回帰検査を追加した。実機の生ログ、コマンド本文、個人パスはこの記録へ含めていない。定期監視や自動停止を新たに有効化せず、必要時の読み取り専用診断として使う。

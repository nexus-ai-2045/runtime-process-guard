# Runtime Process Guard 関連タスク統合（Claude Code 側 / 2026-08-12）

[`THREAD_CONSOLIDATION_PROTOCOL.md`](THREAD_CONSOLIDATION_PROTOCOL.md) の状態機械に従う。
到達状態: `unrelated_routed` まで。`self_closeout_verified` は未達のため **archive しない**。

## 統合先

- 管制（Claude 側）: この Claude Code チャット `local_bd2be0d2-52c5-4740-9d76-810e3fdbbac9`
- 管制（Codex 側 / 前回）: [`codex://thread/019fda3d-67d9-7a82-9d22-001d57159ef0`](codex://thread/019fda3d-67d9-7a82-9d22-001d57159ef0)
- 実装正本: `nexus-ai-2045/runtime-process-guard`（当時 private。現在は public / MIT）

[`task_consolidation-2026-08-10.md`](task_consolidation-2026-08-10.md) が回収したのは **Codex thread 18 件のみ**。
本書は未回収だった **Claude Code チャット側** を対象とする。

## 探索方法（実測）

| 手順 | 実測値 |
|---|---|
| CCD session metadata | `<user-home>\AppData\Roaming\Claude\claude-code-sessions\<ws>\<proj>\local_<sid>.json` = 118 件 |
| Deeplink 解決 | 各 metadata の `cliSessionId` → `<claude-projects-dir>\<cwd-slug>\<cliSessionId>.jsonl`（対応 118/118、欠落 0） |
| 語彙走査 | テーマ語 11 種で全件走査 → ヒット 113、core 語 2 件以上 95 |
| transcript 直読 | 31 session（並列 worker 24 体） |

キーワード一致だけで判定しない（protocol 禁止事項）を機械的に満たすため、全ヒットを
「起動時注入（skill 一覧 / MCP tool 一覧 / task_reminder / MEMORY.md ダンプ）」と実作業に分離した。
`pdf-viewer` `conhost` の大量ヒットはほぼ前者。偽陽性と確定した主なもの:
`修正：nexus-ai-skills`（44 件すべて注入）/ `ワークツリーの整理`（`runtime-process-doctor-salvage` は名前一致のみ）/
`ローカル動作不具合の棚卸し` / `Fableの推奨される使い方` / `Windows 音量自動調節` / `ディスプレイが暗くなる原因`。

## 回収したチャット

| チャット | 回収内容 | 扱い |
|---|---|---|
| PCが重い | プロセス数爆発と確定（718/8core）、2 段 watcher 実装、黒窓の正体 = `DiscordAiPartyBot` タスク | 残務を本書へ吸収 |
| CC パフォーマンス調査 | pdf-viewer MCP leak 特定、`runtime_process_doctor.py` + skill 新設 | 同 |
| コマンドウィンドウの瞬間表示問題 | `CREATE_NO_WINDOW` 主犯修正、PR #406 / #415 | 同 |
| 実行中の作業を安全に完了 | MCP 残骸 60 本を exact PID 回収、保護 8 本生存確認、総数 570→540 | 同 + 下記 A3 |
| 不要なサービスの停止 | 常駐実測、GitHub 一次ソース 7 件超で照合、chrome-devtools-mcp リーク説を自機で反証 | 同 |
| プロセス増殖とCPUスレッド圧迫の根本原因 | 系統 A（Codex リーク）/ B（並走 session × 拡張の掛け算）の判別法確立 | 同 |
| PC環境の肥大化問題 | `memory_watch.ps1` 拡張 P1–P5、drift 検知実装 | 同 |
| Codex GPT アプリクラッシュ | root cause を「Claude 側の掛け算」へ訂正（寄与比 6:1） | 同 |
| タスクマネージャーのCPU使用率検出 | 全プロセス 0% 表示は counter 破損ではなく CPU 飢餓による計測タイムアウト | 同 |
| ワーキングツリーの整理 / GitHub PRs 整理 / Obsidian Tasks / Note Publishing / Hook push強要 / プルリクエストレビュー / Gmail チェック | 成果物 dirty による push 阻害が 7 session で再発 | 下記 C |
| Candidate6 / fractal-decision-ecosystem / Git同期 | 窓抑止・skill 解決失敗・PR #414 の断片 | 同 |

## 前提の訂正（実測により覆した記述）

1. **local clone は存在する**。`<local-checkout>`（本 repo のローカル clone）。
   初回の `find -Depth 6` が階層不足で取りこぼしていた。
2. **「宙吊りの runtime-process-doctor 救出」は PR #475（MERGED 2026-08-02）**。#474 は別件（事実来歴 hook の Python3 探索、CLOSED）。
3. **本ドメインには repo が 2 つある**。本 repo（Codex 配下の admission control）と、
   別 private repo `nexus-ai-2045/windows-process-doctor`。後者は **PR #535（MERGED 2026-08-09）で
   `Projects/shared` へ統合済み**で、`windows_process_doctor_retirement_manifest.json` +
   `windows_process_doctor_retirement_gate.py` が retirement gate として機能している。
   ssot-registry に現れた `runtime-process-doctor-retirement-gate` はこれ。本 repo とは別物。
4. **`sibling_dirty_gate.py` の `owner=runtime-process-guard` 分岐は origin/main にあり、
   ローカル checkout には無い**。ローカル `Projects` main checkout が origin/main より
   19〜97 commit 遅れているため、同じ問いに対して session 間で矛盾した観測が出ていた。
5. `.worktrees/runtime-process-doctor-salvage-20260728` の worktree と branch は既に削除済み。
   残っているのは到達不能な dangling commit `bc0bda7d2` のみで、内容は PR #475 と一致（データ損失なし）。

## 本セッションで閉じた残務

- **A1 稼働中コードが版管理外だった** → PR [#2](https://github.com/nexus-ai-2045/runtime-process-guard/pull/2)。
  Scheduled Task `\Nexus MCP Window Policy Reconciler`（Enabled / 約 5 分間隔 / `pythonw.exe`）が
  未追跡の `scripts/reconcile_codex_plugin_windows.py` を実 plugin cache に対して実行していた。
  CEO 承認のうえ当該タスクを **Disabled** にし、既定を read-only check へ寄せ、原本退避を追加して版管理へ入れた。
  検証: local test 32 → **45 passed** / 実機 smoke（check）`scanned=230 already_compliant=230 pending=0 exit=0`、
  本番 receipt 無変更、`.bak` 未生成。実 runtime 統合（`--apply` 付き再登録）は未実施。
- **A2 branch 位置** → `codex/runtime-pdca-hardening` は PR #1 で merge 済みの死んだ branch と確定。
  `origin/baseline` から `codex/plugin-window-policy` を切り直した。

## 残務（テーマ関連 / archive 阻害要因）

### 人間判断待ち（repo docs 由来 / 前回統合から継続）

- **B1** [`HUMAN_REVIEW.md`](HUMAN_REVIEW.md) のパイロット可否。「設定変更なし・Codex Desktop 1 回再起動・
  read-only 再測定」。成功条件 = identity `d46d9edf8e1b` が 0 / 世代幅 4→3 / tool call 成功。
  [`NEXT_RUN.md`](NEXT_RUN.md) 実装順 6 = 待機。
- **B2** [`PROCESS_LIFECYCLE_DECISION.md`](PROCESS_LIFECYCLE_DECISION.md) 実装順 2–5 未着手。
  lease registry schema / Windows Job Object adapter / graceful shutdown + postflight / 1 MCP 限定 pilot。
  **重複チェック済み: この 2 機能は別名でも存在しない（真の新規）**。近接するのは
  `operations_session_lock.py`（単一 writer 用の heartbeat lease）のみで、OS レベルの子孫寿命束縛ではない。
- **B3** PR #2 merge 後の Scheduled Task `--apply` 付き再登録（`AGENTS.md:12` により明示承認）。
  再登録するまで plugin 更新後の設定戻りは調停されない。

### 成果物の所有権（7 session で同じ摩擦が再発）

- **C1** `~/.claude/runtime-process-doctor.jsonl` / `.latest.json` は **tracked** のまま。
  後から `.gitignore` に足しても効かない（`git rm --cached` 未実施）。観測ツールが吐くたび dirty が復活する。
  対照的に `runtime-process-watch.*` は `.claude/.gitignore:141-142` で除外され根治済み。
  三択（`git rm --cached` / 自動 commit タスク / gate allowlist で恒久許可）が未決。
- **C2** PR #523「dot-claude の machine churn を確定させる自動 commit」が OPEN。
  merge 判断 + merge 後の Scheduled Task 登録（Type1）が残る。核心関数が `NotImplementedError` の骨組みという報告もあり要確認。
- **C3** bash-gate が #523 のスクリプト内容を保護パス書込と誤検知し dry-run 自体を block。canonical path 免除が必要。
- **C4** `.claude`（dot-claude）`windows` branch が `origin/windows` に対し 16 commit 未 push。
  うち本テーマ直結が `6e44dc3` / `0e8ad35`。

### skill 側の分裂

- **D1** home shadow（`~/.claude/skills/` `~/.agents/skills/`）は 77 行の最古版。
  2026-08-09 の 2 系統更新をどちらも含まない。さらに `~/.agents` 版は 8・17 行目で
  「Claude Code」が「Codex」へ誤置換されている実バグあり。**skill 発火時に読まれるのはこの版**。
- **D2** 2026-08-09 に互いを知らない 2 分岐が発生。(A) ローカル未 commit ドラフト 106 行
  = `runtime_process_watch.py` 入口ゲート + 終了手順 6 ステップ + 罠 7–9、
  (B) origin/main PR #535 の 154 行 = retirement 統合 + redaction 規則。統合判断が必要。
- **D3** 想定していた復旧経路 `sync_agent_skills.sh --prune --apply --include-claude` は**効かない**。
  同 script は `Projects/shared` を読まず、既存実体 dir を skip するため、実行しても何も直らない。
- **D4** skill 側は本 repo / `runtime_process_guard` CLI を一切参照していない（grep 0 件）。
  参照しているのは旧 `windows-process-doctor` 側。**repo と skill が未接続**。
- **D5** `codex_mcp_duplicate_guard.py` と `windows_terminal_flash_guard.py` は
  `.gitignore` の allowlist に載っておらず **どの branch にも commit されていない**。
  ディスク消失で復元不能。前者は skill の「終了の実行手順」から `--apply` で参照されている本番経路。
- **D6** `Unknown skill: Projects:runtime-process-doctor` の再現条件は未確定。
  同名 skill が project 級と home 級に併存する構造が原因の候補だが、ログ現物は未発見。

### 窓抑止

- **E1** `DiscordAiPartyBot` タスクの `conhost --headless` 化 未実行（システム設定なので CEO 判断）。
- **E2** `Rotate OpenAI API key` タスクの conhost 化 未実施（drift 検出のみ）。
- **E3** スケジュールタスク 3 件（ccusage-snapshot / Codex Changelog Monitor Quiet /
  GitHub Public Repo Lockdown）が **2 度目の集団消失**。犯人未特定。
  TaskScheduler Operational ログが無効で追跡不能。`restore-tasks.ps1`（要 admin）未実行完了。
  `Codex Post Restart Guard` は 07-26 から Disabled（終了コード 267014）。

### プロセス圧迫の未実行対処

- **F1** `cc-disable-extensions.ps1` 実行未確認（osascript / tooluniverse / blender-mcp /
  desktopcommander の 4 拡張が `isEnabled=true`）。本命の「Claude Desktop 完全終了による一括解放」も未測定。
- **F2** `runtime_process_watch.py` の Scheduled Task 登録 保留。加えて
  **2026-08-09〜08-10 に 5–6 分間隔で 267 件記録した主体が不明**（schtasks 0 件 / 現行プロセス 0 件）。
  `runtime-process-doctor.*` は 08-07 以降更新なし、`watch.state.json` は 08-06 で stale。
  「平常だから発火していない」のか「watch 自体が動いていない」のかが未分離。
- **F3** pdf-viewer OFF 後に 26proc/4.0GB → **52proc/9,487MB へ再悪化**（conhost 63→220）。
  検証ループ未実施。memory `mcp-plugin-process-leak.md` は解決済み時点の内容のみで再悪化が未反映（SSOT drift）。
- **F4** `memory_watch.ps1` / `expected_extensions_state.json` が `.gitignore:412 /shared/scripts/*` で除外され repo に定着しない。
- **F5** worktree `claude/mcp-drift-detector-20260803` の commit `0a4acb0d6` が未 push / 未 PR。
- **F6** 孤児 dry-run → 自動 kill 昇格は 20 サイクル未達（現在 1）。
- **F7** `enable-codex-crashdump.ps1`（WER LocalDumps、要 admin）未実行。07-31 の突然死 4 回中 3 回は
  WER / crashpad / EventLog すべて記録ゼロで死因未特定。crashdump を入れないと再現待ちしかない。
- **F8** 個別 kill 承認待ちの積み残し: TextInputHost（1 コア 90%）/ 孤児 `find.exe` PID 18552 /
  Taskmgr / MCP 重複 8 本 / 24h 超 sdk-child 13 本 / telemetry watchdog 18 本（約 2.0GB）。
- **F9** `NODE_OPTIONS=--max-old-space-size` 未適用（node 88 本中 0 本）。
- **F10** PR #414 の Scheduled Task Active 配線は shadow 観測 7 日 + 誤判定レビュー + 別承認が条件。
- **F11** `memory-watch` を 07-26 に Disable した実行者が未特定。`expected_scheduled_tasks.json` へ
  登録して穴は塞いだが、次回誤 disable で実際に検知するかは未検証。

### 台帳

- **G1** 本 repo は **repo 台帳 2 つのどちらにも未登録**。
  `Documents/references/github-account-repo-map.md` と
  `Documents/nexus_ai/config/nexus_managed_repositories.json` の双方で grep 0 件。

## 引継ぎ（テーマ外 / 送信済み）

| 引継ぎ先 | 内容 | receipt |
|---|---|---|
| `local_c9432334`（Note Publishing Suite 設計レビュー） | daily-note-job が draft 止まり / `content/公開待ち/` 実体なし / published 台帳二重化 / digest の JST-UTC 混在 7 件脱落 / [個人identity] 排除の PR #530・#524 未処理 | queued |
| `local_7ef92d40`（ワークツリーの整理） | branch `claude/runtime-process-doctor-salvage-20260728` の `-D` / squash-merge 済み 49 本の削除 / dirty worktree 回収 blocked / Storage Sense による `%TEMP%` worktree 破損 / main checkout 分岐 | sent |
| `local_a364be97`（DCB snapshot store分裂の解消） | person-registry 空 と snowflake 決定論照合 / wiki 執筆層の分離 / DCB scheduled task の conhost 起動 | sent |

上記以外（ADR 番号衝突、push_gate escape hatch 7 系統、inbox 滞留 92 件、Colab GPU ローテーション、
Cursor 無承認配布 rollback、sales pipeline 17 日停止ほか棚卸し 15 件）は引継ぎ先が一意でないため
protocol 手順 5 に従い管制へ戻す。

## 判断を戻す項目（protocol 手順 5）

1. **二重管制**。Codex 側管制と Claude 側管制のどちらを正本にするか。並存は SSOT 違反。
2. **C1 の三択**（`git rm --cached` / 自動 commit / gate allowlist）。判断系。
3. **E3 の犯人特定は Claude 側から構造的に不可能**。Codex Desktop 常駐セッションは CCD 検索の対象外。
4. **D2 の 2 分岐統合**。どちらの追加も相手に含まれていない。
5. **security**: Candidate6 チャットで `colab.log` grep 時に OAuth Bearer トークンが平文出力された
   自己申告あり。テーマ外だが引継ぎ先が不明。ローテーション要否の判断が必要。

## 2026-08-12 同日追補

本書を起票した後、同日中に確定した事項。前例（[`task_consolidation-2026-08-10.md`](task_consolidation-2026-08-10.md)
の「厳格再監査」節）に倣い、追記はここで閉じる。以降の差分は次の日付の記録へ移す。

### C0 現状測定（read-only / 承認不要で実施）

30 件の残務の多くが「重い → 測る → 提案 → 承認待ちで停止」の途中で止まっていたが、
**症状の現在値が未測定**だった。`runtime_process_watch.py --dry-run` は `[quiet]`
（total=445 / threshold=1143 / 空き 14.4GB）。契約どおり doctor へは上げず、psutil で対象名のみ集計した。

| 指標 | 悪化時（記録） | 良好時（記録） | 2026-08-12 |
|---|---|---|---|
| claude.exe | 52 本 / 9,487MB | 26 本 / 4.0GB | 30 本 / 6,895MB |
| conhost.exe | 220 | 63 | 67 |
| node.exe | 159 | 39 | 33 |
| pdf-mcp-server | — | 0 | 0 |
| codex.exe / chatgpt.exe | — | — | 0 / 0（未起動） |

→ **F3 の再悪化は解消**。memory `mcp-plugin-process-leak.md` の SSOT drift も「再悪化は一時的だった」で確定。
→ **F8 の個別 kill 承認待ちは対象消滅**。TextInputHost / 孤児 `find.exe` PID 18552 / MCP 重複 8 本 /
sdk-child 13 本 / telemetry watchdog 18 本は、いずれも現在のプロセス表に存在しない。
→ 系統 A（Codex 配下のリーク）は Codex 未起動のため現時点の寄与ゼロ。

### D5（追跡漏れ）を閉鎖 — 漏れは 2 本ではなく 4 本だった

[lm93TRQN5WSL/Projects PR #546](https://github.com/lm93TRQN5WSL/Projects/pull/546)。
`origin/main` に不在であることを 1 件ずつ実測確認した。

| ファイル | 役割 |
|---|---|
| `runtime_process_watch.py` | **2 段 watcher の入口そのもの** |
| `runtime_process_watch_contract_check.py` | 上記の契約チェッカー |
| `codex_mcp_duplicate_guard.py` | 重複 MCP の回収器 |
| `windows_terminal_flash_guard.py` | 黒窓検知 |

4 本すべて `ssot-registry.yaml` へ登録（gate に正しく止められたため）。
`shared/scripts/tests/` は既に allowlist 済みで、対応する test 2 本は単に untracked だっただけ。

### contract check の C3 が到達不能化していた（発見・修正）

- **root cause**: `total_threshold()` は `max(baseline中央値 x factor, floor)` を返す。
  つまり `--total-floor` は閾値を**上げることしかできない**
- C3 は「floor を下げれば trip する」と仮定していたが、成り立つのは history 3 件未満で floor が採用される間だけ
- ログが 267 件（中央値 915 → 閾値 1143）まで溜まった時点で構造的に発火不能化。
  **コード変更なしにデータ蓄積だけで壊れる型**
- C2（quiet を強制）が通り続けたのは「floor を上げる」方向だから。この非対称性が見落とされていた
- 2026-08-05 のセッション記録は「契約テスト全項目 PASS」。当時はログが新品だった
- 修正後 `RESULT: ALL PASS`（C1 / C1b / C2 / C3 / C3b / C4）

### A1（PR #2）の改訂と junction の実測

Codex レビューで P1 x6 / P2 x2。反論する項目はなく、**fail-open だった 5 経路**を fail-closed へ寄せた
（走査エラー無視 / 変換不能な npx を準拠扱い / 部分適用 / 読み書き間の競合 / junction 未検出）。
併せて既定の backup を廃止（原本の durable な複製が raw command line と env を残すため）、
包む先の `cmd.exe` から絶対パスを削除。test 45 → 52 passed、CI 4 leg 全 SUCCESS。

**junction は実機で現に起きていた**。`cache/openai-bundled/chrome/latest` は Junction で
同一 cache 内の `26.803.81509` を指し、実体の `plugin.json` は 1 つ。旧コードは同じ物理ファイルを
`latest/` 経由と実体経由で **2 回処理**していた（`scanned` 230 → 229 はこの二重計上の解消で、取りこぼしではない）。
今回は junction 先が cache 内だったため外部への書き込みは起きていないが、外を指す junction なら
cache 外のファイルを書き換えていた。

なお当初の「失敗時に自動 rollback する」案は後続 commit `326b01a` / `0f4a984` で撤回した。
filesystem API に compare-and-swap が無いため、**読み取り確認後の rollback がその直後に入った
第三者更新を上書きし得る**。修正しようとした競合と同型の欠陥を rollback 経路に持ち込んでいた。
現在は適用済み manifest を保持し、partial-write として人間確認へ上げる。

## 自己closeout receipt

```json
{
  "thread_id": "local_bd2be0d2-52c5-4740-9d76-810e3fdbbac9",
  "theme": "runtime-process-guard (Claude Code side)",
  "theme_residual": 25,
  "unrelated_residual": 0,
  "human_wait": 12,
  "external_wait": 3,
  "unknown": 5,
  "handoff_receipts": [
    "local_c9432334-e561-45d8-b428-9e9cd85e6878",
    "local_7ef92d40-6623-44f2-8321-1c2697ea8689",
    "local_a364be97-5a51-4e4b-b083-6f43861f85a8"
  ],
  "self_archive_requested": false,
  "archive_result": "not-archived"
}
```

数え方の訂正: `theme_residual` の母数は B〜G の 28 件。起票時の 30 は、既に閉じた A1 / A2 を
含めた数え違いだった。同日追補で D5 / F3 / F8 を閉じたので 25。
`external_wait` は merge 判断待ちの 3 PR（本 repo #2 / #3、Projects #546）。

`theme_residual` が 0 でないため archive しない。protocol 手順 8 に従い 1〜7 を繰り返す。

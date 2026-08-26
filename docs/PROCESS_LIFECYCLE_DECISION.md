# プロセス所有権・回収方式の統合判断

## 目的

この文書は、Windows上で増殖したCodex配下のMCP／Nodeプロセスについて、ローカル実測、既存の回収機構、Windowsの標準機構、Codexの既知事例を一つの判断面へ統合する。

結論は次のとおり。

1. 主策は、起動時にownerと終了責任を確定するguarded launcherである。
2. Windows実装ではJob Objectを利用し、launcherが所有するプロセス木だけを単位管理する。
3. 既に起動したプロセスへの事後回収は、旧世代の重複MCPを再確認できた場合だけに限定する。
4. 名前一致の一括kill、一般Nodeの推測終了、app-root停止は採用しない。
5. WSL、WMI、PowerShellは独立して観測するが、2026-08-09の直近事象の主因とは判定しない。

## 2026-08-09 ローカル実測

使用したread-only入口は `runtime_process_watch.py --dry-run`、`windows_performance_pdca.py --json`、`runtime_process_doctor.py --plain`、`node_process_tree.py --plain` である。

| 観測 | 結果 | 判断 |
|---|---:|---|
| 総プロセス数 | 589から最大715へ増加 | 一時的な観測器負荷だけでは説明できない継続増加 |
| `node.exe` | 最大152 | Codex配下のMCP世代積層が中心 |
| `cmd.exe` | 最大70 | `codex.exe -> cmd.exe -> node.exe -> cmd.exe -> node.exe` の反復 |
| Codex系working set | 約8.3〜9.0GB | 主なメモリ圧迫源 |
| 空きメモリ | 約10.1GBから最小約6.8GB | Node／MCP増加と同時に低下 |
| Processor Queue Length | 多くは0、一時10 | 継続的なCPU詰まりではない |
| WSL／WMI | 上位CPUプロセス外 | 今回事象の主因を支持する証拠なし |
| PowerShell／PWSH | おおむね各1.6〜3.4% | 診断器自身を含む軽い負荷。主因ではない |

代表的な親チェーンは次の形だった。PIDは再利用されるため、恒久ポリシーではPIDそのものをidentityにしない。

```text
explorer.exe
  -> ChatGPT.exe
    -> codex.exe
      -> cmd.exe
        -> node.exe
          -> cmd.exe
            -> node.exe
```

## 既存仕組みと実績

### `runtime-process-guard`

現在の正本はこのrepositoryである。現行MVPはprivacy-firstのread-only admission controlで、`allow / reuse / defer / deny / unknown` を返す。stdio MCPは接続ごとに専用pipeを持つため、同一command identityだけを根拠に単純reuseしない。

### 共有診断・回収器

- `runtime_process_watch.py`: 軽量なtrip判定。既定read-only。
- `runtime_process_doctor.py`: owner、親チェーン、役割、stop policyの診断。
- `node_process_tree.py`: Nodeの生成源を親チェーンで分類。
- `codex_mcp_duplicate_guard.py`: 既定dry-run。`--apply`時も候補を再判定し、各重複グループの最新世代を残す。

2026-08-09の手動レビュー付き適用では、重複MCPと確認済み孤児shellを合わせて61 PID終了し、総数570から540、Codex系42から26へ減少した。保護対象8本の生存を事後確認した。この実績は「限定回収が可能」であることは示すが、「常時自動killが安全」であることまでは示さない。

同日後半には大規模な再蓄積を回収した後、旧19 PIDの再発を検出した。PowerShellをPIDごとに起動する実装をpsutilの単一snapshotとexact PID停止へ置換し、19 PIDを17.3秒で終了、skip 0、事後候補0を確認した。これは機械的回収経路の性能改善を示すが、active stdio接続をleaseで証明する仕組みが未実装であるため、常時自動enforcementの承認根拠にはしない。

旧 `memory-watch` のAutoClean運用は、2026-07-26から08-03のログ72件で終了報告数の合計が2,184だった。ただし同一PIDの反復計上を除いた一意件数ではなく、現在はAutoCleanなしである。この値を安全性や実効性の証明には使わない。

### 固定間隔の自動回収を採用しない理由

2026-08-09の5分間隔automationでは、`codex_mcp_duplicate_guard.py` が183 rootを候補化した一方、追加ポリシーがChatGPT／Codex app-root配下を一律除外したため、全候補が必ず除外された。これは安全に回収できた証拠ではなく、候補条件と除外条件が論理的に衝突したreport-onlyループである。

`codex_mcp_duplicate_guard.py` はCodex ancestryを候補条件に含む。したがって、**app-root実行ファイルそのものを停止しない**ことと、**app-rootの全子孫を候補から外す**ことを混同してはならない。後者を採用すると、このguardは構造上回収不能になる。一方、Codex ancestryだけでも終了根拠にはならない。stdio接続の利用状態、生存セッションとの所有関係、匿名server identity、世代、作成時刻を合わせて確認できない対象は `unknown` として残す。

同じ実行ではPlaywright／Chrome DevTools系の一般Nodeも50 root混在した。command identityの類似だけでCodex内蔵MCPと一般Nodeを同じ回収集合へ入れない。`stop_policy=unknown`、一般Node、観測者自身のchainは常にfail-closedである。

このため5分間隔automationは廃止し、運用判断と改善はこのrepositoryへ集約する。再導入する場合も、固定間隔の外部killではなく、起動前admission、runtime-owned lease、read-only shadowを先に実装し、個別の人間レビュー付きpilotから始める。

## Windows Job Objectsの位置づけ

MicrosoftのJob Objectsは、複数プロセスを一単位として制限・計測・終了でき、通常は所属プロセスが生成した子も同じJobへ入る。`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` を使えば、最後のJobハンドルを閉じた時に所属プロセスを終了できる。

ただし、Job Objectはlauncherがプロセス生成時から所有する設計で最も安全に機能する。既存のCodexプロセス木へ外部guardが後付けで割り当てると、nested jobやbreakaway設定、別Jobへの所属との衝突があり得る。したがって次の段階では、任意PIDを囲い込む回収器ではなく、`runtime-process-guard` 自身のguarded launcherが開始した対象だけをJobへ所属させる。

一次資料:

- [Microsoft Learn: Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [Microsoft Learn: TerminateJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)

## Codexの既知事例

ローカル事象と完全に同一だとは断定しないが、Codex公式repositoryには同型の報告がある。

- [#18881 MCP child processes leak when McpConnectionManager is replaced](https://github.com/openai/codex/issues/18881): manager置換後もstdio MCP子プロセスの寿命が残る問題。
- [#19469 stdio MCP child process leak causing unbounded memory growth](https://github.com/openai/codex/issues/19469): 古いMCP子が回収されず、プロセス数とメモリが増える報告。
- [#20349 Codex Desktop accumulates duplicate MCP/helper process trees](https://github.com/openai/codex/issues/20349): Desktop配下で100件超のMCP／helper木と複数GBの常駐を観測した報告。
- [#21984 MCP servers eagerly start per session](https://github.com/openai/codex/issues/21984): セッションごとにMCPが起動して蓄積するライフサイクル問題。
- [#26984 MCP stdio servers leak pipe fds and orphan child processes](https://github.com/openai/codex/issues/26984): `npm`／`npx` wrapperの孫Nodeが回収対象から外れ、pipeと孤児プロセスが累積する報告。

これらは、ローカルで観測した多段 `cmd.exe`／`node.exe` チェーンと整合する。ただしissueは報告・議論段階を含むため、修正版の導入済み証明としては扱わない。

## 採用する制御モデル

```text
起動要求
  -> privacy normalization
  -> admission判定
  -> owner lease作成
  -> guarded launcherがJob Objectを作成
  -> 対象プロセスをJobへ所属させて起動
  -> owner終了時にgraceful shutdown
  -> waitして残存確認
  -> 最終手段としてJob単位終了
  -> postflightで対象消滅と保護対象生存を確認
```

事後回収は次をすべて満たす場合だけ許可する。

- Codex ancestryと匿名server identityが一致する。
- 同一グループに複数世代があり、最新世代をkeepできる。
- 候補を終了直前に再snapshotして一致する。
- 自分のチェーン、生存セッション、app-root実行ファイルそのもの、一般Nodeを除外できる。app-root ancestryだけを理由に全子孫を除外しない。
- stdio接続の利用状態またはruntime-owned leaseにより、終了対象が現役接続ではないと確認できる。
- lock、cooldown、circuit breakerが有効である。
- 終了PID、残した理由、保護対象生存、前後総数を記録できる。

一つでも不明ならfail-closedでreport-onlyに戻す。

## 実装順序

1. [完了] 5分自動回収を廃止し、`feedback-cycle` でread-only shadow、前回比較、trend、next action、state保存を接続する。
2. [ローカル実装済み / runtime未接続] lease registryのschemaとstale判定をpure functionで実装する。
   `runtime_process_guard.lease` は匿名identity、owner PIDと生成時刻、heartbeat、期限を保持し、
   owner不明・期限切れだがowner生存中のleaseを回収不可としてfail-closedに分類する。
   実processの観測、永続registry、launcherへの接続は次段階に分離する。
3. [ローカル実装済み / runtime未接続] Windows Job Object adapterとguarded stdio launcherを実装する。
   `guarded-stdio` は自身が起動した子だけをJobへ割り当て、`KILL_ON_JOB_CLOSE`を設定する。
   既存PIDの採用、名前一致停止、shell文字列実行は行わない。
4. [ローカルsmoke済み / 長期受入未実施] stdin EOF時のclose、graceful wait、猶予超過時のmanaged Job終了を検証する。
   receiptはstderrへ匿名イベントだけを出し、argv、絶対path、MCP本文、環境変数を保存しない。
5. [人間レビュー待ち] Obsidian MCPだけでshadowから限定pilotへ進める。

## guarded stdio pilotの境界

初期値は `shadow` であり、idle timeoutによる終了を行わない。`enforce` はactive JSON-RPC request追跡が未接続のため、
現在はfail-closedで拒否する。byte inactivityだけでは長時間requestを誤停止し得るため、設定変更や人間承認だけでは解禁しない。

限定pilotの昇格条件は次のとおり。

- Obsidian 1種類だけを対象にshadowを7日間、最低30 lifecycle観測する。
- 誤回収0、保護対象停止0、unknownからの自動回収0を満たす。
- 30回の起動終了smokeで残存0、P95 shutdown 30秒未満、P99 120秒未満を確認する。
- Codex設定変更、`enforce`有効化、既存プロセス回収は個別の人間レビュー後に行う。

Codex本体のplugin／MCP設定変更、既存起動経路への接続、Scheduled Task登録、外部pushは別の人間承認境界とする。

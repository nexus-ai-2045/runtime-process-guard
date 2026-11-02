# プロセス所有権・回収方式の統合判断

## 目的

この文書は、`Windows`上で増殖した`Codex`配下の`MCP`／`Node`プロセスについて、ローカル実測、既存の回収機構、`Windows`の標準機構、`Codex`の既知事例を一つの判断面へ統合する。

結論は次のとおり。

1. 主策は、起動時に`owner`と終了責任を確定する`guarded` `launcher`である。
2. `Windows`実装では`Job` `Object`を利用し、`launcher`が所有するプロセス木だけを単位管理する。
3. 既に起動したプロセスへの事後回収は、旧世代の重複`MCP`を再確認できた場合だけに限定する。
4. 名前一致の一括`kill`、一般`Node`の推測終了、`app-root`停止は採用しない。
5. `WSL`、`WMI`、`PowerShell`は独立して観測するが、2026-08-09の直近事象の主因とは判定しない。

## 2026-08-09 ローカル実測

使用した`read-only`入口は `runtime_process_watch.py --dry-run`、`windows_performance_pdca.py --json`、`runtime_process_doctor.py --plain`、`node_process_tree.py --plain` である。

| 観測 | 結果 | 判断 |
|---|---:|---|
| 総プロセス数 | 589から最大715へ増加 | 一時的な観測器負荷だけでは説明できない継続増加 |
| `node.exe` | 最大152 | `Codex`配下の`MCP`世代積層が中心 |
| `cmd.exe` | 最大70 | `codex.exe -> cmd.exe -> node.exe -> cmd.exe -> node.exe` の反復 |
| `Codex`系`working` `set` | 約8.3〜9.0`GB` | 主なメモリ圧迫源 |
| 空きメモリ | 約10.1`GB`から最小約6.8`GB` | `Node`／`MCP`増加と同時に低下 |
| `Processor` `Queue` `Length` | 多くは0、一時10 | 継続的な`CPU`詰まりではない |
| `WSL`／`WMI` | 上位`CPU`プロセス外 | 今回事象の主因を支持する証拠なし |
| `PowerShell`／`PWSH` | おおむね各1.6〜3.4% | 診断器自身を含む軽い負荷。主因ではない |

代表的な親チェーンは次の形だった。`PID`は再利用されるため、恒久ポリシーでは`PID`そのものを`identity`にしない。

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

現在の正本はこの`repository`である。現行`MVP`は`privacy-first`の`read-only` `admission` `control`で、`allow / reuse / defer / deny / unknown` を返す。`stdio` `MCP`は接続ごとに専用`pipe`を持つため、同一`command` `identity`だけを根拠に単純`reuse`しない。

### 共有診断・回収器

- `runtime_process_watch.py`: 軽量な`trip`判定。既定`read-only`。
- `runtime_process_doctor.py`: `owner`、親チェーン、役割、`stop` `policy`の診断。
- `node_process_tree.py`: `Node`の生成源を親チェーンで分類。
- `codex_mcp_duplicate_guard.py`: 既定`dry-run`。`--apply`時も候補を再判定し、各重複グループの最新世代を残す。

2026-08-09の手動レビュー付き適用では、重複`MCP`と確認済み孤児`shell`を合わせて61 `PID`終了し、総数570から540、`Codex`系42から26へ減少した。保護対象8本の生存を事後確認した。この実績は「限定回収が可能」であることは示すが、「常時自動`kill`が安全」であることまでは示さない。

同日後半には大規模な再蓄積を回収した後、旧19 `PID`の再発を検出した。`PowerShell`を`PID`ごとに起動する実装を`psutil`の単一`snapshot`と`exact` `PID`停止へ置換し、19 `PID`を17.3秒で終了、`skip` 0、事後候補0を確認した。これは機械的回収経路の性能改善を示すが、`active` `stdio`接続を`lease`で証明する仕組みが未実装であるため、常時自動`enforcement`の承認根拠にはしない。

旧 `memory-watch` の`AutoClean`運用は、2026-07-26から08-03のログ72件で終了報告数の合計が2,184だった。ただし同一`PID`の反復計上を除いた一意件数ではなく、現在は`AutoClean`なしである。この値を安全性や実効性の証明には使わない。

### 固定間隔の自動回収を採用しない理由

2026-08-09の5分間隔`automation`では、`codex_mcp_duplicate_guard.py` が183 `root`を候補化した一方、追加ポリシーが`ChatGPT`／`Codex` `app-root`配下を一律除外したため、全候補が必ず除外された。これは安全に回収できた証拠ではなく、候補条件と除外条件が論理的に衝突した`report-only`ループである。

`codex_mcp_duplicate_guard.py` は`Codex` `ancestry`を候補条件に含む。したがって、**`app-root`実行ファイルそのものを停止しない**ことと、**`app-root`の全子孫を候補から外す**ことを混同してはならない。後者を採用すると、この`guard`は構造上回収不能になる。一方、`Codex` `ancestry`だけでも終了根拠にはならない。`stdio`接続の利用状態、生存セッションとの所有関係、匿名`server` `identity`、世代、作成時刻を合わせて確認できない対象は `unknown` として残す。

同じ実行では`Playwright`／`Chrome` `DevTools`系の一般`Node`も50 `root`混在した。`command` `identity`の類似だけで`Codex`内蔵`MCP`と一般`Node`を同じ回収集合へ入れない。`stop_policy=unknown`、一般`Node`、観測者自身の`chain`は常に`fail-closed`である。

このため5分間隔`automation`は廃止し、運用判断と改善はこの`repository`へ集約する。再導入する場合も、固定間隔の外部`kill`ではなく、起動前`admission`、`runtime-owned` `lease`、`read-only` `shadow`を先に実装し、個別の人間レビュー付き`pilot`から始める。

## `Windows` `Job` `Objects`の位置づけ

`Microsoft`の`Job` `Objects`は、複数プロセスを一単位として制限・計測・終了でき、通常は所属プロセスが生成した子も同じ`Job`へ入る。`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` を使えば、最後の`Job`ハンドルを閉じた時に所属プロセスを終了できる。

ただし、`Job` `Object`は`launcher`がプロセス生成時から所有する設計で最も安全に機能する。既存の`Codex`プロセス木へ外部`guard`が後付けで割り当てると、`nested` `job`や`breakaway`設定、別`Job`への所属との衝突があり得る。したがって次の段階では、任意`PID`を囲い込む回収器ではなく、`runtime-process-guard` 自身の`guarded` `launcher`が開始した対象だけを`Job`へ所属させる。

一次資料:

- [マイクロソフト公式：ジョブオブジェクト](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [マイクロソフト公式：ジョブ全体の終了](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)

## `Codex`の既知事例

ローカル事象と完全に同一だとは断定しないが、`Codex`公式`repository`には同型の報告がある。

- [#18881 接続管理の置換後に子プロセスが残留](https://github.com/openai/codex/issues/18881): `manager`置換後も`stdio` `MCP`子プロセスの寿命が残る問題。
- [#19469 標準入出力サーバーの残留とメモリ増加](https://github.com/openai/codex/issues/19469): 古い`MCP`子が回収されず、プロセス数とメモリが増える報告。
- [#20349 デスクトップで補助プロセスの木が重複](https://github.com/openai/codex/issues/20349): `Desktop`配下で100件超の`MCP`／`helper`木と複数`GB`の常駐を観測した報告。
- [#21984 セッションごとのサーバー先行起動](https://github.com/openai/codex/issues/21984): セッションごとに`MCP`が起動して蓄積するライフサイクル問題。
- [#26984 標準入出力のパイプと孤児プロセスが残留](https://github.com/openai/codex/issues/26984): `npm`／`npx` `wrapper`の孫`Node`が回収対象から外れ、`pipe`と孤児プロセスが累積する報告。

これらは、ローカルで観測した多段 `cmd.exe`／`node.exe` チェーンと整合する。ただし`issue`は報告・議論段階を含むため、修正版の導入済み証明としては扱わない。

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

- `Codex` `ancestry`と匿名`server` `identity`が一致する。
- 同一グループに複数世代があり、最新世代を`keep`できる。
- 候補を終了直前に再`snapshot`して一致する。
- 自分のチェーン、生存セッション、`app-root`実行ファイルそのもの、一般`Node`を除外できる。`app-root` `ancestry`だけを理由に全子孫を除外しない。
- `stdio`接続の利用状態または`runtime-owned` `lease`により、終了対象が現役接続ではないと確認できる。
- `lock`、`cooldown`、`circuit` `breaker`が有効である。
- 終了`PID`、残した理由、保護対象生存、前後総数を記録できる。

一つでも不明なら`fail-closed`で`report-only`に戻す。

## 実装順序

1. [完了] 5分自動回収を廃止し、`feedback-cycle` で`read-only` `shadow`、前回比較、`trend`、`next` `action`、`state`保存を接続する。
2. [ローカル実装・`launcher`接続済み / `runtime`未接続] `lease` `registry`の`schema`と`stale`判定を実装する。
   `runtime_process_guard.lease` は匿名`identity`、`owner` `PID`と生成時刻、`heartbeat`、期限を保持し、
   `owner`不明・期限切れだが`owner`生存中の`lease`を回収不可として`fail-closed`に分類する。
   永続`registry`は`exclusive` `lock`と`atomic` `replace`を使い、`launcher`起動前の登録、`heartbeat`、`postflight`後の`CAS`解除まで接続済み。
   `Windows`で置き換え先が一瞬開かれていて `os.replace` が `PermissionError`（`WinError` 5）になるときは、排他`lock`の中で有限回だけ同じ置き換えをやり直し、尽きたら従来どおり失敗で止める。待ちの合計は`lock`待ち（`lock_timeout_seconds`）の半分までにする。`heartbeat`の更新期限（`renewal_deadline`）は、最後の1回も含めて置き換えの直前に毎回確かめ、過ぎていたら保存せずに失敗にする。
3. [ローカル実装済み / `runtime`未接続] `Windows` `Job` `Object` `adapter`と`guarded` `stdio` `launcher`を実装する。
   `guarded-stdio` は自身が起動した子だけを`Job`へ割り当て、`KILL_ON_JOB_CLOSE`を設定する。
   既存`PID`の採用、名前一致停止、`shell`文字列実行は行わない。
4. [ローカル`smoke`済み / 長期受入未実施] `stdin` `EOF`時の`close`、`graceful` `wait`、猶予超過時の`managed` `Job`終了、`Job`全体`postflight`を検証する。
   `receipt`は`stderr`へ匿名イベントだけを出し、`argv`、絶対`path`、`MCP`本文、環境変数を保存しない。
5. [人間レビュー待ち] `Obsidian` `MCP`だけで`shadow`から限定`pilot`へ進める。

## `guarded` `stdio` `pilot`の境界

観測のみの `shadow-snapshot` と、管理付き起動の `guarded-stdio --mode shadow` を分ける。
後者は`admission`による起動抑止、`lease`保存、子の起動と所有`Job`の終了・消滅確認を行う。読み取り専用ではない。
互換性のため初期`mode`名は `shadow` を維持し、`idle` `timeout`による終了を行わない。当初の `enforce` は要求追跡が未接続のため拒否していた。2026年10月29日、`macOS`で`--track-requests`を明示し、入力終端・観測済み要求完了・出力転送終了を確認する限定回収を追加した。無通信による停止、既存プロセスへの所有の後付け、全接続への本番適用は解禁しない。詳細は[アーキテクチャ](architecture.md)を参照する。

限定`pilot`の昇格条件は次のとおり。

- `Obsidian` 1種類だけを対象に`shadow`を7日間、最低30 `lifecycle`観測する。
- 誤回収0、保護対象停止0、`unknown`からの自動回収0を満たす。
- 30回の起動終了`smoke`で残存0、`P95` `shutdown` 30秒未満、`P99` 120秒未満を確認する。
- `Codex`設定変更、`enforce`有効化、既存プロセス回収は個別の人間レビュー後に行う。

`Codex`本体の`plugin`／`MCP`設定変更、既存起動経路への接続、`Scheduled` `Task`登録、外部`push`は別の人間承認境界とする。

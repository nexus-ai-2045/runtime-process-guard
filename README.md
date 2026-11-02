# ランタイム起動・回収ガード（`runtime-process-guard`）

## Windowsの常駐圧迫監視

`python -I -m runtime_process_guard.pressure_watch --report-path <絶対JSONパス> --stop-file <絶対停止markerパス>` は10秒ごとに軽量測定し、最新1標本を保存します。有限試験は `--interval-seconds 2 --max-cycles 2`。OS lockで重複起動を拒否します。初回CPU差分はwarming-upとして扱い、正常判定に数えません。

通常インストールしたvenvのpythonw.exeと専用state directoryを使い、`windows/resident-task.ps1 -Mode install -Pythonw <pythonw.exe> -StateDirectory <directory>` でログオン時起動します。`-Mode status` で状態確認、`-Mode disable` で登録を無効化して停止markerを置きます。stop markerがある状態での自動再起動はしません。

`defer-new-work` は新規作業を増やさないための観測結果です。Codexの未管理起動を強制抑止する機能ではありません。管理対象の同種2・全体4上限は既存guardへ接続した起動だけに適用されます。未管理PIDの自動killは行いません。詳細は [ADR-0006](docs/adr/ADR-0006-windows-resident-pressure-watch.md) を参照してください。

ローカルの`subprocess`、`MCP` サーバー、`automation`を起動する前に、重複・`CPU`・メモリ・親子関係を確認する`privacy-first`の実行前ガードです。

観測・起動判定の入口は読み取り専用です。明示的に選ぶ `guarded-stdio` は子プロセスの起動と終了を管理します。`reconcile-disabled-plugin --apply` は、別途確認した既存`MCP`の限定停止を扱う手動操作です。設定変更、外部送信、`telemetry`は行いません。

## 目的

ローカルで `subprocess` / `MCP` / `automation` を起動する直前に、重複・資源圧・親子関係を匿名で判定し、危険な一括停止や秘密の保存をせずに起動可否を返す。

## メニューバーでの継続観測

`macOS`向けの軽量な常駐表示は[メニューバーアプリ](macos/MenuBar/README.md)を参照してください。
新しい常駐はネイティブアプリ一つで、既存の一覧取得を単発で呼び出します。平常時は120秒、メモリ圧迫・観測不明・失敗時は300秒の間隔です。収集に2秒を超えた場合も300秒待ち、監視による負荷の追加を抑えます。接続終了時の回収は別の要求追跡処理が担当します。

`resident observe --scope system`で、アクセス可能な`PC`全体のプロセスを観測できます。`Discord`・`Orca`を含め全件を保護し、チャットへの直接帰属・関連のみ・未対応を区別します。メニューバーの「全件を表で表示」では、検索・並べ替え、登録チャット、種類別の本数と合計負荷を確認できます。起動時に`--scope system`を指定し、従来の状態ファイルとは別の保存先を使います。手順と制限は[メニューバーの説明](macos/MenuBar/README.md)を参照してください。
`resident snapshot`は一覧、保護理由、圧迫状態を一つの応答にまとめます。取得不完全を正常とは扱いません。

全既存プロセスを保護し、任意の既存`PID`への自動停止は行いません。自動整理は要求追跡を有効にした`guarded-stdio`の管理付き接続が終了するときだけを対象とし、アプリへ所有権を移しません。対応能力と実接続への採用は別です。アプリへの接続登録がない間は「管理付き接続 0件・待機」と表示します。低い`CPU`・子なし・同名重複・期限切れだけでは停止しません。既存の`MCP`設定やログイン設定をこの観測コマンドが変更することもありません。


## できること

| 機能 | 用途 |
|---|---|
| `preflight` | 同一`command` 識別値と`resource` `pressure`を確認し、起動可否を返す |
| `shadow-snapshot` | `Codex`配下プロセスの数・世代幅・メモリを匿名集計する |
| `feedback-cycle` | 前回`state`と比較して`stable` / `improving` / `worsening`を判定する |
| `lineage-snapshot` | `PID`・作成時刻・親子`lineage`の差分を`JSON`／`Mermaid`へ出す |
| `Windows` プラグイン `policy` | プラグイン更新後の`npx`起動を`conhost.exe --headless`へ調停する |
| `reconcile-disabled-plugin` | 保存設定で無効な`MCP`を実体照合し、確認済みの計画だけで限定停止する |

`Windows`、`macOS`、`Linux`で共通の判定語彙を使います。

## 安全境界

次の情報は標準出力、`receipt`、`state`へ保存しません。

- `raw` `command` `line`と引数
- 環境変数
- `API` `key`、`token`、`password`、`secret`
- ユーザー名を含む絶対パス

照合用識別値は、機密値と`home` `path`を正規化した後の`SHA-256`です。表示するのは実行ファイル名、識別値の先頭12文字、集計値だけです。

この`repository`は次を行いません。

- 名前一致による一括`kill`
- `OS`全体への`hook`注入
- `command` `line`の監査ログ化
- 自動的な公開、送信、クラウド同期

## クイックスタート

`Python` 3.11以上の独立した環境へ `python -m pip install .` で通常インストールできます。
`Projects`、`Codex`、他`repository`の`script`は`CLI`実行の依存ではありません。
インストール後は`checkout`外から `runtime-process-guard --help` を実行できます。
観測コマンドは`OS`共通です。`guarded-stdio` は`Windows`の所有`Job`と、`macOS`の専用プロセスグループを扱います。

`CI`は`wheel`を別`venv`へ通常インストールし、`checkout`外・`Python`隔離モードで`CLI`を検査します。
`Windows`では試験用`echo`子プロセスの往復、`EOF`後の所有`Job`消滅、`lease`解放も確認します。
この試験は`Codex` `Desktop`の設定配線や`MCP`必要時選択の受入とは別です。

次の `URL` を `AI` に貼り、先に危険レビューを出させてください。削除・`GitHub` `write`・`visibility`・`secret`・`unknown` を安全と読まないこと。

[リポジトリ](https://github.com/nexus-ai-2045/runtime-process-guard)

貼付後の確認例（人が叩く手順ではない）: `python -m runtime_process_guard.cli preflight -- node server.mjs --stdio`。`preflight` は対象を起動しません。

## 判定と終了コード

| 終了コード | 判定 | 意味 |
|---:|---|---|
| `0` | `allow` | 起動を許可できる |
| `10` | `reuse` | 既存プロセスの再利用候補がある |
| `20` | `defer` | 現時点では起動を延期する |
| `30` | `deny` | 定義済み`policy`により拒否する |
| `40` | `unknown` | 安全に判定できないため停止する |

`stdio` `MCP`は`client`ごとに専用`pipe`を持つため、既存プロセスへ単純に`reuse`できません。`--reuse-policy dedicated-stdio`では、同一識別値を観測しても`reuse`せず、`resource` `pressure`だけで`allow` / `defer`を判断します。

`singleton`判定では、コマンドを読み取れないプロセスがあれば `unknown` を返します。
`dedicated-stdio` は他プロセスを再利用しないため、その欠落を件数として伝えつつ資源予算を判定します。
プロセス一覧や資源計測そのものに失敗した場合は、どちらも `unknown` です。

## 主なコマンド

### 実行前確認

```powershell
python -m runtime_process_guard.cli preflight --reuse-policy dedicated-stdio -- node server.mjs --stdio
```

### 現在の匿名スナップショット

```powershell
python -m runtime_process_guard.cli shadow-snapshot --owner codex.exe --process-name node.exe --json
```

対象候補の属性・親子関係を確認できない場合は、不完全な観測として終了コード `40`、
`overall=unknown`、`complete=false`、`next_action=repair-observation` を返します。
観測できた件数は残しますが、プロセスが存在しない証拠や`pilot`の成功回数には使いません。
`feedback-cycle` もその観測では前回の正常`state`を上書きしません。

### 前回との差分と傾向

```powershell
python -m runtime_process_guard.cli feedback-cycle `
  --owner codex.exe `
  --process-name node.exe `
  --state-path reports/runtime-feedback-state.json `
  --json
```

`feedback-cycle`は`baseline` / `stable` / `improving` / `worsening`を判定します。プロセス数または世代数の下限が2周期連続で増えた場合、または対象プロセスの常駐メモリ（`RSS`）合計かホストのスワップ使用量が128`MiB`以上ずつ2周期連続で増えた場合に、`human-review-runtime-pressure`へ上げます。`RSS`合計は共有メモリを重複して数える場合があり、この判定だけでは増加の原因を特定できません。通知の配達は呼出元の責務です。

起動時刻の比較は[公式仕様にあるプロセス間の1秒の丸め差](https://psutil.readthedocs.io/stable/#psutil.boot_time)を許容し、差が1秒を超えた場合に新しい観測基準へ戻します。

連続増加は同じ指標ごとに数えます。`RSS`とスワップの交互の増加を合算して通知しません。指標別履歴は既存状態の `memory_growth_streaks`、既存の `consecutive_memory_growth_cycles` はその最大値の互換用表示です。指標を特定できない旧履歴は各指標へ割り当てず、新しい観測から継続を確認します。

`state`の対象不一致、`schema`不正、観測不能、同時実行`lock`では既存`state`を上書きせず、終了コード`40`で停止します。初回`baseline`の`unknown`は正常な観測開始なので終了コード`0`です。

既存の空・不正なロック内容は、書込み中の旧所有者と終了済みの初期化を区別できないため、自動上書きせず `repair-invalid-feedback-lock` を返します。所有者確認後の復旧は呼出元の責務です。リンクされたロックは拒否し、リンク先の無関係なファイルを変更しません。

### チャット別のプロセス一覧と軽い継続観測

`inventory` は停止・再起動・アーカイブを行わず、既存の親子差分機能を使って現在一覧を保存します。保存先は非公開のローカル領域に指定してください。チャット名を含む一覧・対応記録は公開リポジトリへ追加しません。

```bash
python -m runtime_process_guard.cli inventory snapshot --owner ChatGPT --state /private/path/latest.json --bindings /private/path/bindings.json --threads-db /private/path/state_5.sqlite --tree
python -m runtime_process_guard.cli inventory show --state /private/path/latest.json --tree
python -m runtime_process_guard.cli inventory chats --threads-db /private/path/state_5.sqlite --state /private/path/latest.json --limit 50
python -m runtime_process_guard.cli inventory watch --owner ChatGPT --state /private/path/latest.json --bindings /private/path/bindings.json --threads-db /private/path/state_5.sqlite --interval 30 --samples 120
```

登録チャット名はローカルのメタデータから読めます。`chats` は登録された表示名（`name`）を初回依頼文（`title`）より優先し、名前の一覧と保存時点の `PID` 対応を別々に出します。本文・全ログは読みません。未対応は名前取得失敗とは区別します。この一覧は明示実行時だけ読むため、監視の更新ごとに全チャットを走査しません。

初回は対応記録なしで実行できます。`--bindings` と `--threads-db` は任意です。所有元の実行ファイル名は実機に合わせます。表示は保存時刻と経過秒数を明記し、`show` はプロセスや会話本文を再走査しません。`watch` は指定回数で終了する前景処理で、常駐サービス・フック・定期実行の登録は行いません。既存の実行基盤からは `snapshot` を一度ずつ呼べます。停止済み監視を勝手に再開しません。

一覧は `MCP`、`node_repl`、`Node`、描画などを実行ファイル名で分類し、親子ツリー・直下と子孫の常駐メモリ（`RSS`）・チャット別合計・全体合計を出します。種別の推定は利用中や接続成功の証明ではありません。全体とチャット別合計は同じプロセスを一度だけ数え、共有・未特定を別枠にします。共有メモリを含む `RSS` の算術和であり、物理メモリやスワップの実使用量とは異なります。`CPU` は前回からの実測時間と累積使用時間の差で計算し、初回は未測定とします。

#### チャットとの対応を登録する

起動時刻・作業場所・共通の親だけから名前を確定しません。対象チャットの実ツールから得た識別子と、実行処理が開く専用ファイルを照合します。

1. `inventory marker-script --marker /private/path/unique-marker.json` で診断用の短い命令を生成します。専用ファイルは未作成の絶対パスを指定します。
2. 対象チャットの `node_repl/js` でその命令を一度実行します。`requestMeta.threadId`、一度限りの識別子、時刻が保存され、ファイルの開いた状態を保持します。実ツールの戻り値と対象チャットを操作者が照合してください。
3. 読み取り専用の `lsof -t /private/path/unique-marker.json` などで、そのファイルを開く実行 `PID` を確認します。
4. 60秒以内に次を実行します。

```bash
python -m runtime_process_guard.cli inventory bind --marker /private/path/unique-marker.json --kernel-pid 12345 --bindings /private/path/bindings.json
```

登録時だけ開いたファイルのデバイス番号・実体番号と、実行 `PID`・親サーバーの起動世代を照合します。現在は `macOS` の `lsof` と `Linux` のプロセス情報で実体照合し、確認できない環境では登録しません。ファイルの場所だけの一致や古い対応記録を採用しません。対応記録は既定30分、最大24時間で失効し、`PID`再利用や親の変更でも失効します。

直接対応するのは実行処理の `PID` です。親の `node_repl` に他の実行処理がいる場合、それらを同じチャットへ割り当てません。親には関連チャット名を表示し、専有未確認の資源は未特定枠に残します。複数の直接観測があるプロセスは共有枠にします。記録は操作者が採用する診断証拠であり、署名・所有権・停止許可を作るものではありません。

#### 観測自身の負荷と保存量

通常の更新は実行ファイル名・親 `PID`・起動時刻・常駐メモリ・累積 `CPU` 時間を一度取得し、引数・環境変数・開いた全ファイル・会話本文・全ログを読みません。`JSON` の `collector` に収集時間・自身の `CPU` 時間・自身の常駐メモリ・走査件数を記録します。書込みを含む実行全体の時間は外側の計測と区別してください。

保存する一覧は最新1件と同時更新防止の既存ロックだけです。対応記録は同じ世代・チャットを置換し、更新時に期限切れを除きます。無制限の履歴・追加の常駐アプリは作りません。更新間隔は5秒以上、既定30秒です。読み取り不能・入力不正・競合・旧版の誤帰属を含み得る記録は未確認として停止し、既存一覧を維持します。

### 親子`lineage`の差分

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

`PID`だけでなく作成時刻も比較するため、`PID`再利用を新規・消滅の両方として記録します。`Mermaid`では前回から追加されたプロセスを緑で表示します。

## 所有する標準入出力サーバーの管理（限定試験用）

`guard`自身が起動した`stdio`サーバーだけを所有する限定試験の入口です。`Windows`では`Job Object`、`macOS`では新規の専用セッションとプロセスグループを使います。
既存`CLI`との互換性のため`mode`名は `shadow` を維持していますが、読み取り専用ではありません。
起動前`admission`で資源不足・観測失敗時の起動を抑止し、`lease`を保存し、子を起動します。
`stdin` `EOF`、`relay`終了、`guard`の中断時には通常終了を待ち、猶予超過や子孫残存時には所有`Job`を終了して消滅を検証します。
`shadow` が無効にするのは`idle` `timeout`による終了です。観測のみには `shadow-snapshot` を使います。

```powershell
runtime-process-guard guarded-stdio --mode shadow --lease-state <state-path> --idle-seconds 600 --grace-seconds 30 -- <executable> <args...>
```

`macOS`で接続終了時の安全な自動回収を使う場合は、要求追跡を明示します。対応する`guard`だけで使える限定経路です。

```sh
runtime-process-guard guarded-stdio --track-requests --mode enforce \
  --lease-state /absolute/private/path/managed-leases.json \
  --grace-seconds 30 -- /absolute/path/mcp-server
```

`--mode enforce`だけでは要求追跡がないため拒否します。`command`は引数列として直接起動され、`shell`文字列は受け付けません。自動回収はクライアント入力の終端（`stdin EOF`）を確認し、双方向で観測済みの要求が応答済みとなり、出力転送も完了した場合だけです。要求追跡の破損、未応答、受付が継続中の場合は保護します。無通信時間による終了は行いません。`Discord`・`Orca`を管理付き起動へ明示接続する場合は、それぞれ`--protected-server discord`、`--protected-server orca`を指定し、自動停止を無効にします。既存の未管理プロセスは観測のみで、進行中の要求へ停止権限を広げません。
`--lease-state` は明示必須です。`registry`には匿名識別値、`owner` `PID/`生成時刻、`heartbeat`、期限だけを保存し、`argv`、環境変数、`MCP`本文は保存しません。

## `Windows`プラグイン起動規則

`receipt` の `overall` は プラグイン `cache` の `window` `policy` だけを評価します。
`coverage.process_lifecycle` は常に `not-evaluated` であり、`overall=ok` はプロセス `lifecycle`の健全性を意味しません。

`scripts/reconcile_codex_plugin_windows.py`は、プラグイン `cache`の`plugin.json` / `.mcp.json`にある`stdio` `MCP`の`npx`起動だけを`conhost.exe --headless`で包みます。

- `plugin.json`：`mcpServers`配下だけが対象
- `.mcp.json`：トップレベルのサーバー定義が対象
- プラグイン メタデータは`MCP`設定として扱わない
- `symlink`、`junction`、`reparse` `point`の配下へ降りない
- `receipt`へプラグイン `path`、サーバー名、`command` `line`を保存しない

### まず読み取り専用で確認

```powershell
python scripts/reconcile_codex_plugin_windows.py `
  --receipt reports/plugin-window-policy-latest.json
```

変更候補は`pending`として報告され、プラグイン `cache`は変更されません。

### 書込みは別承認

```powershell
python scripts/reconcile_codex_plugin_windows.py `
  --receipt reports/plugin-window-policy-latest.json `
  --apply
```

`--apply`はプラグイン `cache`を変更します。`Scheduled` `Task`登録、設定変更、実ランタイムへの適用と同じく、人間の明示承認後だけ実行してください。

異常時は`fail-closed`です。

| 状況 | 結果 |
|---|---|
| `cache`を走査できない | `invalid` / `unknown` |
| `npx`定義を安全に変換できない | `unsupported` / `unknown` |
| `scan`後に`manifest`が変化した | `conflict` / `defer` |
| 複数`manifest`の途中で書込み失敗 | 自動`rollback`せず、`partial-write` / `unknown`として報告 |
| `scan`後に未処理`manifest`の第三者更新を検出 | その更新を保持し、途中適用済みなら`partial-write` / `unknown` |

複数`manifest`を通常の`filesystem`操作だけで完全な`transaction`として扱うことはできません。第三者更新を上書きしないことを優先し、途中失敗時は適用済み`manifest`を保持して人間確認へ止めます。

原本の`durable`な複製は既定で作りません。`manifest`には`raw` `command` `line`や`env`が含まれ得るためです。人手の復旧用に控えが必要な場合だけ`--backup`を明示すると、同じディレクトリへ`<name>.pre-headless.bak`を作ります。

## 必要時起動と接続上限

接続単位の必要時起動・終了後回収を行う限定経路は、既存の `guarded-stdio` を使用します。
`--max-server-instances 2 --max-total-instances 4` を明示すると、共通所有記録の管理台帳のロック内で起動枠を予約します。
上限に達した場合は新起動だけを終了コード20で拒否し、既存接続を保持します。無指定の旧`CLI`は従来互換であり、自動的に2/4上限が適用されるわけではありません。
この `guarded-stdio` 経路は、最初のツール呼出しまでの遅延起動、通信空白による`idle`停止、既存`PID`の回収を行いません。
実ランタイム適用前に [限定試験の契約](docs/MCP_LIFECYCLE_PILOT.md) を確認してください。

## 無効化済み`MCP`の明示的な手動回収

`reconcile-disabled-plugin` は`macOS/Linux`向けの独立した操作者経路です。既定では確認計画を保存するだけで、停止・設定変更・定期実行は行いません。既存`PID`を `guarded-stdio` が起動した子や`lease`へ後付けで登録する機能ではありません。

停止前に、操作者が使用中の処理がないことと、対象設定スナップショットによる停止の承認を確認してください。保存された設定の `enabled=false` だけでは、別`profile`・起動時`override`を含む実効設定や、進行中`JSON-RPC`の有無を証明できません。`--idle-confirmed` はこの操作者確認を記録する引数で、署名付きの権限証明や自動`idle`検出ではありません。

対象は正しい配布元付きプラグイン `ID`、プラグイン メタデータ、`MCP` `manifest`、絶対パスで指定する実行ファイル、親`PID`とその作成時刻から導きます。起動コマンドは先頭の実行名を含む全引数が定義と完全一致する場合だけ対象にし、実行ファイルへの別名は統合しません。相対パス起動も作業ディレクトリで区別し、旧配布元名を現在のプラグインの別名として扱いません。メタデータが明示的に `.mcp.json` 一つへ接続する構成だけを扱います。同じ`manifest`内で実行ファイル名・引数・作業場所が重なるサーバー定義は、環境変数や有効状態が異なっても区別できないため拒否します。計画には`PID`、作成時刻、匿名識別値、ファイルの版情報だけを保存します。`argv`、環境変数、設定本文、個人パスは出力しません。

下記の変数は実測した対象に設定してください。過去の`PID`や設定案を再利用しないでください。

```bash
runtime-process-guard reconcile-disabled-plugin \
  --config "$RPG_CONFIG" --plugin-root "$RPG_PLUGIN_ROOT" \
  --plugin-id "$RPG_PLUGIN_ID" --server-name "$RPG_SERVER" \
  --expected-executable "$RPG_EXECUTABLE" \
  --owner-pid "$RPG_OWNER_PID" --owner-created-at "$RPG_OWNER_CREATED_AT" \
  --max-targets 20 --plan-path "$RPG_PLAN"
```

保存先は既存ファイルを上書きしません。計画の有効期限は5分です。出力された `plan_sha256` を確認し、同じ入力に `--apply --idle-confirmed --expected-plan-sha256 "$RPG_PLAN_SHA256"` を追加した場合だけ`TERM`を送ります。上限超過、設定・実体・親・`PID`世代・対象集合の変化、観測不能では停止を拒否します。

全件の事前検査に加え、各`TERM`直前にも再検査します。途中で状態が変わった場合は `partial-stop` として、停止要求済みと未実行を分けます。検査と`signal`送信は`OS`上で完全に原子的にはできません。子孫やプロセス `group`へ停止範囲を広げず、`SIGKILL`やアプリ全体の再起動へ自動昇格しません。完了判定は選択した`PID`世代の消滅と、同じ定義に一致する残存数を区別します。

停止済みの`PID`や進行中処理を元の状態へ復元することはできません。再利用が必要になった場合の設定復旧とクライアント側での再接続は、操作者が別途扱います。この`CLI`が保存設定を書き換えたり、停止したサーバーを勝手に起動し直したりはしません。

この経路の`fixture`検査成功を、`Mac` `Desktop`の全`MCP`起動抑制、自動回収、実クライアント統合の成功へ読み替えないでください。過去に`CLI`外で行った手動停止を、この`CLI`が遡及実行した記録へ変換しません。

## 開発と検証

```powershell
python -m pytest -q
git diff --check
```

変更ルールは[変更・貢献手順](CONTRIBUTING.md)、`security`報告は[安全性の報告](SECURITY.md)を参照してください。

## 運用文書

| 文書 | 内容 |
|---|---|
| [次回の実行手順](docs/NEXT_RUN.md) | 次回再開時の契約と`shadow` `pilot` |
| [プロセス管理の判断](docs/PROCESS_LIFECYCLE_DECISION.md) | `safe` `recovery`、`Windows Job Objects`、採否判断 |
| [運用の改善手順](docs/RUNTIME_PDCA.md) | 定期観測、異常判定、親子差分、回復後検証 |

## リポジトリの公開範囲

この`repository`は `public`（`MIT`）です。`push`、`Pull` `Request`、`merge`、`Release` 作成はそれぞれ別の承認境界です。


## `Mac`の管理付き起動と採用条件

`guarded-stdio --mode shadow` は、`Mac`でも新規に起動する子だけを管理します。起動前の資源予算と、既存の所有記録台帳による接続上限を再利用します。これは起動・終了を行う明示的な入口です。現在の製品内の接続や既存`PID`へ、自動で接続する機能ではありません。

`Mac`の追跡付き経路ではクライアント入力の終端（`stdin EOF`）と双方向の未応答要求ゼロを確認してから子の入力を閉じ、猶予時間を待ちます。残存する場合も、生きているリーダーの`PID`世代・利用者・`SID`・`PGID`を再確認できる専用グループへ`TERM`を送る範囲に限ります。リーダー消失後の古いグループへは停止信号を送りません。`SIGKILL`へ自動昇格しません。接続中は無通信・出力終了・所有記録の更新失敗・所有者の観測失敗だけを理由に終了させません。

`Mac`の所有台帳は次の形を使います。既存台帳を上書きして移行せず、専用の保存先を指定します。元の台帳形式を使う読み手は、この形式を拒否します。

```json
{"schema":"runtime-process-guard/retained-lease-registry-v3","capacity":{"max_server_instances":2,"max_total_instances":2},"leases":[]}
```

`guard`死亡、期限切れ、終了確認の失敗では予約枠を再利用しません。接続が正常終了し、管理グループの消滅を確認できた場合だけ自分の予約を解除します。異常終了後に残った予約は、残存プロセスの実体・世代と設定を別途照合して調停する必要があります。台帳の削除による自動復旧はしません。

上限は同じ台帳を使う管理接続数を対象にします。既存の未管理プロセスや、別グループ・別セッションへ離脱した子孫の総数を制限するものではありません。自律的に子孫を起動するサーバーの自動回収には採用せず、一種類の実装と終了境界を検証してから接続します。

`reconcile-disabled-plugin --idle-confirmed` は操作者が使用状況を確認した手動経路のままです。監視のタイマーから未使用確認を自動生成しません。既存監視は資源圧迫と増殖の検知を継続し、未所有・処理中か不明なプロセスを停止しません。

2026年10月16日の`Mac`実測では、同じ実サーバーへ初期化とツール一覧取得だけを行い、直接起動と管理付き起動を交互に各3回比較しました。両経路とも接続終了後は3回正常終了しました。プロセス数は1から2へ増え、起動時間の中央値は約2.7秒と5.5秒、合計`RSS`の中央値は約120と134`MiB`でした。負荷変動を含む小規模比較であり、製品のチャット起動時間や因果効果を確定する測定ではありません。削減効果と接続単位の所有情報が揃っていないため、既存の製品接続への適用は見合わせています。製品設定・プラグインのキャッシュ・既存プロセスはこの試験で変更していません。


## `MCP`フックの観測試験

既存の`PreToolUse`と`PostToolUse`へ、対象サーバーを絞る短時間の観測コマンドを接続できます。新しい常駐サービスは作りません。設定の自動登録、信頼の自動承認、停止や所有台帳の変更も行いません。

```sh
python -m runtime_process_guard.hook_probe --server example --report-dir <private-report-directory>
python -m runtime_process_guard.hook_probe --server example --report-dir <private-report-directory> --summarize
```

コールバックの入力は標準入力から受けます。保存するのは匿名化したセッション・ターン・呼出し・ツール識別子、イベント段階、観測時刻、通信メタデータの有無、観測コードの指紋だけです。専用の診断サーバー`rpg_stdio_probe`についてのみ、既知の診断形式にある自己申告の`PID`と生成時刻を限定保存できます。一般の引数、結果本文、環境変数、自己申告の所有権は保存・採用しません。

記録先は非公開の専用ディレクトリです。各記録を排他的に新規作成し、既存ファイルを上書きせず、128件を上限にします。集計は記録先全体が対象で、サーバー選択は受信時に行います。集計だけではディレクトリを作りません。観測器の設定・入力・保存に問題があっても、元のツールを拒否する終了コードは返しません。

開始と応答は`session_id`・`turn_id`・`tool_use_id`の組で対応付けます。同一の再配信は冪等に処理し、矛盾、逆順、欠落、コード指紋の不一致は不明として保護します。開始だけなら処理中の観測として保持します。応答が揃っても、バックグラウンド処理や接続全体の未使用を証明するものではありません。

出力は常に`binding_status=unverified_binding`、`stop_authorized=false`、`protection_required=true`です。フックを受けたプロセスの`PID`、時刻の近さ、候補が一件だけであることを、対象`MCP`の所有証明へ変換しません。既存の`lease`をイベントから取得・解除しません。

`probe-stdio-owner --mcp`は、`Mac`でも自己プロセスの`PID`と生成時刻を返せます。これは自己情報だけの観測で、標準入出力の相手や所有権を証明するものではありません。起動元が別途取得した子プロセスの実体・生成時刻と照合するための診断情報です。

2026年10月16日の限定試験では、ネイティブ`CLI`から既存の診断`MCP`を`guard`経由で一時起動し、実呼出し一回の開始・応答イベントを受信しました。応答の自己`PID`・生成時刻は、起動時に取得した`guard`の子プロセスと一致しました。試験では同梱のコードモード実行ファイルを一時的な検索パスで利用し、ホームの実行環境は変更していません。対象とコードを固定した診断上の対応であり、任意のサーバーや既存の製品接続への所有権の一般化はしていません。この試験では`CLI`表示用の`item.id`とフックの`tool_use_id`は別の値だったため、表示識別子で結合しません。

既存の製品接続からの`codex_app.list_artifacts`呼出しは成功しましたが、一時登録した観測フックのコールバックは受信を確認できませんでした。登録・信頼状態の読取りと、実コールバック受領は別の証拠として扱います。試験後は一時変更したホーム設定を復元し、製品の停止・再起動や新しい常駐サービスの導入は行っていません。製品全体のフック対応や自動制御が完成したとは扱いません。


### 設定の保存と実行中チャットへの反映

設定の保存、`hooks/list`による検出、定義の信頼、実行中チャットへの反映、実イベント受領を別々に確認します。`hooks/list`で有効・信頼済みと表示されても、実行中チャットがその設定を使う証拠にはなりません。子担当の起動だけを設定再読込みの代わりにしません。

2026年10月16日、製品と同じ版の実行ファイル（`0.162.0-alpha.17.2`）を一時的に別起動し、診断`MCP`一種類を使って反映経路を比較しました。同じチャット内で、それぞれ実呼出し一回が成功しました。

| 操作 | 設定一覧 | 開始・応答イベント |
| --- | --- | --- |
| ファイル上で有効化し、一覧を読み直す | 有効・信頼済み | 0件 |
| 同じ実行元で正式な設定反映を行う | 有効・信頼済み | 2件 |

使用した反映処理は`config/batchWrite`の`reloadUserConfig: true`です。[公式プロトコル資料](https://learn.chatgpt.com/docs/app-server)と、利用中の版が生成するスキーマを確認して使います。反映を依頼する接続先は、検証したいチャットを管理している実行元でなければなりません。別起動の`CLI`で反映に成功しても、既存`Desktop`接続への反映成功にはしません。

`reloadUserConfig`は指定チャットだけの操作ではなく、同じ実行元が読み込んでいる全チャットへ設定反映を試みます。既存クライアントのフック設定画面からの操作でも、この影響範囲を狭めたとは扱いません。試験用の実行元を隔離するか、読み込み済みの全対象チャットについて承認・処理状況・設定差分を確認できる場合だけ反映します。対象一覧や処理状況が不明なら反映せず保護します。

保存前の設定と差分を保全し、反映後は影響を受ける全対象チャットで有効状態と実発火を確認します。対象外チャットを記録しないための`--session-id`も指定しますが、この受信後の絞り込みだけでは設定反映自体の影響範囲は狭まりません。観測が不要になった際の解除も同じ範囲の承認と検証を行い、保存内容と実行中設定の両方を確認します。`MCP`設定全体の再読込みやアプリ再起動を、観測フック反映の代用として自動実行しません。

この比較が示すのは、設定一覧と実行中設定の反映が別であることです。既存`Desktop`での実イベント受領、任意の接続の所有`PID`、負荷削減は、それぞれ別に検証します。イベント未受信・所有不明・処理中の接続は引き続き保護し、自動停止の許可には使いません。

回収の対象は標準入力の終端を確認した専用接続です。未読出力やサーバー内部の非同期処理の不存在までは証明しません。長寿命の購読・自律的な作業を行うサーバーは個別に保護し、稼働中の既存接続へ一括適用しません。

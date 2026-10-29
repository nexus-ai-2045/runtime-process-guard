# `Codex`プロセス観測メニューバー

`Swift/AppKit`のアプリ一つだけが常駐し、既存 `runtime-process-guard resident snapshot` を単発で呼びます。既定は `ChatGPT` 所有配下、`--scope system`指定時はアクセス可能な`PC`全体が表示対象です。常駐`Python`、別`daemon`、自動起動登録、インストール、設定変更を行いません。

## ビルドと手動起動

`macOS`で`Xcode` `Command` `Line` `Tools`の `swiftc` を使います。追加ライブラリは不要です。

```sh
./macos/MenuBar/build.sh
./macos/MenuBar/build/RuntimeProcessMenuBar.app/Contents/MacOS/RuntimeProcessMenuBar \
  --cli /absolute/path/runtime-process-guard \
  --state /absolute/private/path/menubar/latest.json
```

アプリを引数なしで起動する場合は、ビルド時に実体が確定した`CLI`とアプリ専用`cache`を指定します。

```sh
./macos/MenuBar/build.sh \
  --cli /absolute/path/runtime-process-guard \
  --state /absolute/private/path/menubar/latest.json
```

上記は生成`bundle`内の `Resources/ResidentDefaults.plist` だけに既定値を保存します。`OS`や`Codex`の設定は変更しません。既定値は実行時の引数で上書きできます。既定値なしの再ビルドでは以前の`bundle`既定値を削除します。ビルド成果物は`Git`の対象外です。既存アプリと異なる`bundle` `ID`は `local.runtime-process-guard.resident-observatory` です。

必要な場合にだけ `--threads-db ABS` と `--bindings ABS` を追加します。チャット`DB`と対応記録は読み取り専用です。`CLI`は絶対`path`の実行可能`file`を指定し、`shell`を介さず引数配列で起動します。`raw` `command` `line`、`secret`、`home` `path`はメニューや診断エラーへ表示しません。

## 表示と安全境界

メニューに件数、`RSS`算術和、`CPU`差分、観測からの経過秒数、負荷状態、保護件数、停止候補0件を表示します。`RSS`は物理メモリ使用量ではありません。初回`CPU`と`sleep`復帰直後の`CPU`は未測定として表示します。保存済み親子ツリーは`PID`と登録名を表示し、上限は80行、字下げは最大8段です。メニューを開く操作では再収集しません。

監視一覧の`backend`は全件保護です。要求追跡付きの`guard`が対応していれば「自動整理: 管理付き接続の終了時のみ」「未管理プロセスは保護」と表示します。旧`backend`や観測失敗の場合は対応を未確認として表示します。対応能力は実接続への採用を意味しません。現在アプリへの接続登録はなく「管理付き接続 0件・待機（アプリへの接続登録なし）」です。静的な能力情報や書込み可能な記録から実プロセスの停止権限を作りません。`idle`、低`CPU`、子なし、同名の重複、チャットへの対応を停止根拠にしません。対象の停止・再起動・アーカイブ・`stdio`共有の操作はありません。

`backend`の通常120秒、負荷・不明・失敗時300秒の間隔に従います。収集が2秒を超えた場合も300秒待ちます。収集は同時最大1件で、処理中の追加要求は重ねません。`sleep`中は`timer`を止め、復帰後に一回更新します。収集中に復帰した場合はその処理の完了に合流します。不完全な観測は警告を付け、取得できた範囲の一覧と件数を表示します。形式不正や取得失敗は件数を未確認に戻し、古い正常値を現在値として表示しません。取得から90秒超の値は古い観測と明示します。低頻度化後もこの鮮度表示は維持するため、次回の自動更新前には古い観測と表示されます。

`collector`の出力は標準エラーを含め最大4`MiB`、起動から10秒で時間超過です。時間超過・出力超過・アプリ終了時に停止要求を送れるのは、このアプリが起動した`collector`自身だけです。監視対象の`PID`へ`signal`を送りません。`collector`が`SIGTERM`に応答しない場合は再起動せず未確認として停止し、`smoke`の `collector_running=true` と失敗終了で残存を報告します。任意の`CLI`を指定するため、指定実体の信頼性は操作者が確認してください。

アプリ専用`state`隣の0600 `lock`で単一起動を制御します。同じ`state`で二重起動した場合は終了コード40です。別`state`の別起動は別観測として扱います。`state`と`bundle`内既定値には`private`な`path`が含まれ得るため、ローカルの`private`領域へ保存してください。

## 有限の検証

```sh
APP=./macos/MenuBar/build/RuntimeProcessMenuBar.app/Contents/MacOS/RuntimeProcessMenuBar
"$APP" --validate-fixture "$PWD/macos/MenuBar/Fixtures/valid.json"
"$APP" --cli "$PWD/macos/MenuBar/Fixtures/fixture-collector.sh" \
  --state "$PWD/macos/MenuBar/build/test-state/latest.json" --no-ui --smoke-seconds 2
```

`--no-ui` は `--smoke-seconds 1...300` が必須です。終了時`JSON`に`collector`の起動数・成功数・部分観測数・失敗数・最大並列数・実行中残存、アプリ自身の`CPU`秒・`peak` `RSS`、回収済み子プロセスの`CPU`秒（`RUSAGE_CHILDREN`）、両者の合計`CPU`秒、`collector` `wall`秒を出します。子の`CPU`値には回収済み子と、その子が回収した子孫が含まれます。未回収・生存中の子孫や監視対象の資源量は含まず、ホスト全体の負荷も示しません。`CLI`が起動されただけでは成功と数えず、終了コード0/40の`resident` `envelope`を検査し、完全な正常観測だけ成功数を増やします。不完全な一覧は部分観測として分け、`smoke`は終了コード40を返します。`smoke`時間終了時に収集中なら終了要求と回収を待つため、指定秒数を超える場合があります。

`fixture`は架空の名前・`PID`を用いた静的出力で、実観測や実機`UI`受入の証拠には使いません。形式検証は`unknown` `envelope`の形式が正しい場合にも成功します。`--validate-fixture` の成功は現況が正常という意味ではありません。旧版の未対応情報と新版の管理付き接続終了時のみ対応という組は受理します。真偽と理由の矛盾、未実装の接続登録件数、未保護`assessment`、`PID`世代の不一致、重複・循環した親子関係、合計の不整合は拒否します。


## ログイン時の起動設定

`install_login.py`は、このアプリ専用のユーザー`LaunchAgent`だけを扱います。既定はプレビューで、`--apply`を付けた時だけ設置します。追加ライブラリは不要です。

```sh
python3 macos/MenuBar/install_login.py --app /absolute/path/RuntimeProcessMenuBar.app
python3 macos/MenuBar/install_login.py --app /absolute/path/RuntimeProcessMenuBar.app --apply
```

`bundle`の識別子と実行ファイルを確認し、`ProgramArguments`はその実行ファイル一つです。引数なしで動くよう、先に必要な`bundle`既定値を設定してビルドしてください。固定`label`のユーザー`LaunchAgents`領域へ0600で原子的に設置します。`RunAtLoad=true`、`KeepAlive=false`のため、アプリの手動終了を尊重します。

このスクリプトは`launchctl bootstrap`を実行しません。設置成功と、ログイン登録の読戻し・実起動は別の検証です。起動登録の実行は、対象を確認した操作者が担当します。他の`LaunchAgent`や無効化済みの監視サービスは変更しません。

```sh
python3 macos/MenuBar/install_login.py --remove
python3 macos/MenuBar/install_login.py --remove --apply --show-paths
```

削除もプレビューが既定で、専用印と内容・実体を検査できる自分の設定だけを対象にします。別`label`、別アプリ、リンクされた保存先、他者が書き込める親や既存ファイルは拒否します。設定を削除する前に同じ領域へ0600のバックアップを残します。復旧は、そのバックアップを元の設定先へ戻して内容を読み戻します。設定削除自体は、既に起動中のアプリを停止しません。

通常の`JSON`出力には個人の絶対パスを含めません。`--show-paths`を明示した時だけ、対象とバックアップのパスを表示します。`applied`は要求モード、`changed`は実際の書込み有無、`launchctl_executed=false`は起動登録をまだ実行していないことを示します。一時`home`と架空`bundle`だけの検証は次の入口です。

```sh
python3 -m pytest macos/MenuBar/test_install_login.py -q
```

登録解除時の控えは、ユーザーの`Library/Application Support/RuntimeProcessGuard/login-backups`へ保存します。自動起動の読込み対象である`LaunchAgents`へは控えを置きません。


## `PC`全体とチャット対応の表示

全体観測は明示的な`system`指定で起動します。従来の`v1`状態ファイルとは別の保存先を使います。

```sh
./macos/MenuBar/build/RuntimeProcessMenuBar.app/Contents/MacOS/RuntimeProcessMenuBar \
  --cli /absolute/path/runtime-process-guard \
  --scope system \
  --state /absolute/private/path/system/latest.json \
  --threads-db /absolute/private/path/state.sqlite \
  --bindings /absolute/private/path/chat-bindings.json \
  --show-window
```

`--threads-db`と`--bindings`は任意です。指定しなくても全件を表示し、チャット情報は未確認と示します。ビルド時の`--scope system`は生成`bundle`の既定値だけを変えます。`--show-window`は起動時に表を開く指定で、`--no-ui`とは併用できません。メニューの「全件を表で表示」からも開けます。

- 全プロセス表: 観測できた全行を検索・並べ替えできます。`PID`、`OS`の親`PID`、名前、種類、`CPU`、`RSS`、チャット、帰属、保護理由を表示します。メニューの80行上限とは別の表です。
- 登録チャット表: 登録名と対応`PID`を分けて表示します。現在は最新1000件まで読み、上限に達した場合は省略の可能性を表示します。登録名だけで実行プロセスを推定しません。
- 種類別集計: 同じ名前・種類の本数と負荷の合計を表示します。部分観測の増減は比較不可です。合計値は観測できた範囲の部分和です。
- 対応の証拠: 既存の`inventory marker-script`と`inventory bind`を再利用します。対象チャットの実行メタデータ、マーカーを開いた実行プロセス、`OS`のファイル記述子、`PID`世代を照合して登録します。登録は期限付きの証拠で、表示のたびに`FD`を再検証するものではありません。専有・現在の使用状態・停止許可は証明しません。
- 関連のみ: 実行カーネルと共有の補助プロセスを区別します。親・兄弟へ専有帰属を広げず、関連先は「関連のみ・専有未確認」と表示します。

収集は同時1件、通常120秒、圧迫・部分・失敗時300秒です。「今すぐ更新」も処理中は無効です。検索や並べ替えでは`OS`や`DB`を再収集しません。詳細表示の高頻度サンプリングと6標本の履歴は未実装です。終了ボタンは観測アプリ自身を閉じるもので、一覧のプロセスを停止しません。

### 整理できる対象

一覧の全行は保護します。低`CPU`、古い`PID`、同じ名前、本数の増加、チャットとの対応だけでは停止しません。`Discord`・`Orca`・処理中・共有・不明は自動整理の候補にしません。

自動整理は、既存の`guarded-stdio --mode enforce --track-requests`で起動時から所有する専用接続が、受付終了と要求・出力の排出、世代と所有の再確認を通った場合に限ります。既存の製品接続への一括適用や、未管理`PID`の所有の後付けはしません。保護対象を管理付きで起動する場合は`--protected-server discord`または`--protected-server orca`で明示保護します。実接続は未登録のため、アプリは「管理付き接続0件」と表示します。

### 再現できる検査

```sh
python -m pytest -q tests/test_system_observation.py tests/test_resident.py tests/test_inventory.py tests/test_privacy.py
./macos/MenuBar/build.sh
python macos/MenuBar/verify.py
```

`verify.py`は合成データで実アプリの表を起動し、検索・並べ替え・行数・単一起動を検査します。`--smoke-seconds`の`JSON`は`UI`と回収済み収集子の`CPU`秒、観測件数、部分観測、表示行数を返します。欠落を伴う終了コード40は正常成功と数えず、部分観測と実行エラーを分けて読みます。`--smoke-search`と`--smoke-sort`は有限の表示検査用で、`--show-window`と`--smoke-seconds`を併用します。実チャット名や原ログを公開用`fixture`へコピーしないでください。

# Windows常駐観測の限定受入（2026-10-30）

## 実装と依存

PR #8 head `a0347febd408f86ed32f344fb9f0fd32dc5e540b` の常駐観測・所有終了の境界を再利用したWindows追加分。未管理PIDの停止権限は追加していない。

Python 3.13でwheelを通常インストールし、別venv・隔離モードから2標本の有限試験に成功した。Windows Task Schedulerに一般ユーザーのログオン起動を登録し、IgnoreNew・無期限実行・自動restartなしを読み戻した。既存taskは変更していない。

## 検証結果

- 全体試験は417 passed、91 skipped。Windowsのsymlink作成権限がない試験は明示skipし、権限設定を変更していない。
- 独立レビュー2件（初回CPU取得失敗の匿名化、停止中待機の再確認）を修正し、回帰テストと独立再確認に成功。
- 有限試験の2標本目はCPU33.3%、空き約9849MiB、496process、採取時間0.52ms。
- 実taskはRunningで最新JSONのcycleと時刻が進むことを再読した。二重起動は40で拒否。
- 起動直後の実処理PythonはRSS約24.6MiB・CPU時間約0.156秒。venv launcherは別に約5.5MiB。単発値であり長期上限ではない。

## 未保証と次のゲート

ログオン後の再起動試験、5〜6作業の実際の並列発射前後比較、通常操作の応答時間、Desktop発射制限への接続は未確認。reportは `admission_enforced=false` と明記する。

CPU85%以上・空き2048MiB未満・process1100超で `defer-new-work` を返すが、未管理のDesktop起動は抑止しない。強制上限は既存guard経由に限定する。停止・復帰はWindowsのresident-task手順を使い、未知の既存PIDや壊れた他taskは自動操作しない。

# ADR-0006: Windowsの軽量常駐観測と起動制御の境界

- 状態: 実装・Windows限定受入中
- 日付: 2026-10-30
- 関連: ADR-0003、ADR-0005

## 判断

5〜6件の並列起動で繰り返し遅くなる利用状況に対し、10秒ごとの軽量観測を常駐させる。全件command line、SQLite、詳細inventoryを高頻度に走査せず、CPU差分、空きメモリ、PID件数のみを取得する。最新1標本だけを原子的に保存する。既存のOS lockとreport writerを再利用する。

CPU 85%以上、空き2048MiB未満、総プロセス1100超は `defer-new-work`。これは表示・判断用で、Codex Desktopの発射を自動抑止したという意味ではない。強制起動制御は既存guardのadmission・共有lease・同種2/全体4の予算を通る起動だけに効く。新規proxyや未管理PIDの自動killは追加しない。

## 運用

Windows Task Schedulerのログオン時起動・一般ユーザー・IgnoreNewを使い、単一pythonwを維持する。重複はOS lockでも拒否。観測失敗はunknownを保存して20/40秒へバックオフ、3回で終了。Task Schedulerの無限restartを設定しない。停止は専用stop marker、再開時はその状態を人間が確認する。

明示的な常駐導入依頼の範囲で専用taskを登録する。既存のFeedback Shadowと壊れたwindow reconcilerは別責務のため自動変更しない。名前や閾値超過だけで既存PIDを停止しない。

## 受入

有限2標本、同一出力への重複拒否、異常入力、観測失敗の有限停止をテストする。Windowsで実task起動、reportの時刻更新、常駐のCPU時間/RSSを測定する。ログオン後の再開と実際の5〜6作業の比較が未測定なら、その運用保証を付けない。

# `ADR-0007`: 別チャット2件の実行前受付と`FIFO`待機

- 状態: ローカル実装・合成実プロセス試験済み、`Desktop`受入前
- 日付: 2026-10-31
- 関連: `ADR-0003`、`ADR-0006`

## 対象と再利用

対象は別`Desktop`チャットの同時実行であり、`subagent`数ではない。既存の`LeaseRegistry` `OS` `lock`と`atomic` `report` `writer`を再利用する。`Projects`の`orchestration_queue`は`task`定義・`worker`配送と`POSIX` `fcntl`に依存するため、`Windows` `Desktop`の`turn`受付には直接接続できない。その配送器を複製せず、`turn`受付だけを本`repo`の責務とする。

公式`UserPromptSubmit`の同期`hook`でモデル開始前に2枠を取得する。3件目以降は0.5秒間隔の`FIFO`待機、先行`turn`の`agent-turn-complete`通知を受けると自動で進む。`session`/`turn`は`hash`化して保存し、`prompt`、通知本文、`command`、環境値は保存しない。

`Stop` `hook`は他の`Stop`チェックが処理を継続させ得るため解放に使わない。通知の`thread-id`/`turn-id`を完全一致させ、遅れた通知が次`turn`の枠を解放しない。`Interrupt`はその`turn`だけを解放する。`SessionEnd`は正確な`turn` `ID`を持たないため解放権限にしない。新規`subagent`は`SubagentStart`で入場済み親を登録した場合だけ親`chat`の枠を使う。`agent_id`フラグ単独の除外は行わない。

完了イベントは匿名の`durable` `marker`へ先に保存する。公開・`state`更新は有限再試行し、残った`marker`は次の受付前に再処理する。適用済み`ID`を`state`と同時に記録し、`marker`の削除失敗は`commit`済み入場の失敗に変換しない。保存自体が不能なら永続化済みと扱わず、枠を推測で解放しない。

## 失敗と上限

`state`不正、`lock`競合、`process`世代の観測不能、`owner`不明は明示`block`。死んだ`backend`世代の枠だけを整理し、経過時間や`idle`では回収しない。完了通知が欠落すれば枠を保持し、可用性のために上限を黙って緩めない。待機は最大3600秒、`hook` `timeout`は3605秒とし、内側期限で明示`block`して再送判断へ返す。永久待機・無限`retry`は行わない。

ただし製品側の`hook`実行失敗・`timeout`はエラー扱いで`block`と同一ではない。`hook`が未信頼・無効・起動不能なら受付は機能しない。`OS`操作や`Desktop`を改造して絶対上限と主張しない。既存同時実行を検出して強制停止する機能ではなく、切替前に進行中`turn`を完了させる必要がある。

## 適用ゲート

変更するのは`home` `hooks.json`への`UserPromptSubmit`/`Interrupt`/`SubagentStart`追記と、既存`notify`を保持する`wrapper`への差替え。既存`hook`、モデル、認証、`CUA`通知、`fact-provenance`を削除しない。ライブ設定は`backup`・`hash`・差分をレビューし、公式/`hooks`で新規定義を信頼確認する。`trusted_hash`の直接生成や`dangerously-bypass-hook-trust`で代用しない。

適用後は`Desktop`の3試験チャットで`A`/`B`の取得、`C`の待機表示、`A`完了後の`C`開始、`B`非影響を確認する。`hook`入力の合成実プロセス試験を`Desktop` `E2E`と読み替えない。復帰は今回の`hook`追記と`notify`差分だけを戻し、第三者変更のある全`config`を`backup`で上書きしない。

公式契約: `https`://`learn.chatgpt.com`/`docs`/`hooks`

## 取消履歴の上限と導入前の復帰条件

取消は正確な`session`/`turn`の組を永続保持し、後続`poll`が同じ取消を忘れて再受付しない。履歴は512組までで、513組目では`marker`を残して安全側に受付と通知処理を拒否する。この状態は自然復帰しない。処理中の作業を止めたり、古い取消を期限で削ったりして空きを作らない。

実機への有効化前に、この上限と次の復帰手順を操作者へ示す。

1. 新規受付の`hook`を明示的に休止する。既存作業は継続させる。
2. 旧`state`を参照する全`waiter`が終了し、全`turn`の完了・取消通知が収束したことを、対応`session`/`turn`と`PID`世代で確認する。不明なら復帰しない。時間経過・低`CPU`だけで静止と判定しない。
3. `state`と`releases`を一組として非公開領域へ`backup`し、原本は保持する。生存`poll`がある状態で履歴を消さない。
4. 操作者の設定変更承認後、受付と通知の全入口を新しい`state`保存先へ同時に切り替える。旧入口を混在させず、遅れて届く旧通知は旧`state`に保持する。
5. 2枠、3件目待機、正確な取消、別`turn`の受付、完了解放を合成試験で再確認してから再開する。

自動`reset`や自動ローテーションは実装していない。本`PR`のマージは`live` `hook`の有効化を意味しない。履歴の残量と上限時の通知・復帰`UI`は、実機導入前の運用受入項目として残す。

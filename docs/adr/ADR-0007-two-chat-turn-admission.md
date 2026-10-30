# ADR-0007: 別チャット2件の実行前受付とFIFO待機

- 状態: ローカル実装・合成実プロセス試験済み、Desktop受入前
- 日付: 2026-10-31
- 関連: ADR-0003、ADR-0006

## 対象と再利用

対象は別Desktopチャットの同時実行であり、subagent数ではない。既存のLeaseRegistry OS lockとatomic report writerを再利用する。Projectsのorchestration_queueはtask定義・worker配送とPOSIX fcntlに依存するため、Windows Desktopのturn受付には直接接続できない。その配送器を複製せず、turn受付だけを本repoの責務とする。

公式UserPromptSubmitの同期hookでモデル開始前に2枠を取得する。3件目以降は0.5秒間隔のFIFO待機、先行turnのagent-turn-complete通知を受けると自動で進む。session/turnはhash化して保存し、prompt、通知本文、command、環境値は保存しない。

Stop hookは他のStopチェックが処理を継続させ得るため解放に使わない。通知のthread-id/turn-idを完全一致させ、遅れた通知が次turnの枠を解放しない。Interruptはそのturnだけを解放する。SessionEndは正確なturn IDを持たないため解放権限にしない。新規subagentはSubagentStartで入場済み親を登録した場合だけ親chatの枠を使う。agent_idフラグ単独の除外は行わない。

完了イベントは匿名のdurable markerへ先に保存する。公開・state更新は有限再試行し、残ったmarkerは次の受付前に再処理する。適用済みIDをstateと同時に記録し、markerの削除失敗はcommit済み入場の失敗に変換しない。保存自体が不能なら永続化済みと扱わず、枠を推測で解放しない。

## 失敗と上限

state不正、lock競合、process世代の観測不能、owner不明は明示block。死んだbackend世代の枠だけを整理し、経過時間やidleでは回収しない。完了通知が欠落すれば枠を保持し、可用性のために上限を黙って緩めない。待機は最大3600秒、hook timeoutは3605秒とし、内側期限で明示blockして再送判断へ返す。永久待機・無限retryは行わない。

ただし製品側のhook実行失敗・timeoutはエラー扱いでblockと同一ではない。hookが未信頼・無効・起動不能なら受付は機能しない。OS操作やDesktopを改造して絶対上限と主張しない。既存同時実行を検出して強制停止する機能ではなく、切替前に進行中turnを完了させる必要がある。

## 適用ゲート

変更するのはhome hooks.jsonへのUserPromptSubmit/Interrupt/SubagentStart追記と、既存notifyを保持するwrapperへの差替え。既存hook、モデル、認証、CUA通知、fact-provenanceを削除しない。ライブ設定はbackup・hash・差分をレビューし、公式/hooksで新規定義を信頼確認する。trusted_hashの直接生成やdangerously-bypass-hook-trustで代用しない。

適用後はDesktopの3試験チャットでA/Bの取得、Cの待機表示、A完了後のC開始、B非影響を確認する。hook入力の合成実プロセス試験をDesktop E2Eと読み替えない。復帰は今回のhook追記とnotify差分だけを戻し、第三者変更のある全configをbackupで上書きしない。

公式契約: https://learn.chatgpt.com/docs/hooks

# MCP必要時起動・増殖防止の限定pilot

## 状態と対象

設計・ローカル実装の受入用。実Codex設定はこの文書の作成だけで変更しない。
対象はWindows / Codex Desktop / Obsidian direct stdio MCPの1種類。
CLIとDesktopのバージョンは別に記録し、CLIの表示だけでDesktopの版本や設定反映を保証しない。
設定管理側の既存成果は保持する。Plugin cacheの直接編集、APIキー、追加API課金、idle enforcementは使用しない。

## 起動インターフェース

`guarded-stdio`に次のopt-in引数を追加する。

- `--max-server-instances 2 --max-total-instances 4`: 同一registryで管理する接続枠。両方同時指定が必要。
- `--client-owner-pid` と `--client-owner-created-at`: 接続ownerのPIDとtimezone付きISO 8601作成時刻。必ず組で指定する。
- `--lease-state`: 全管理対象で共通のローカルstate。上限付きstateはv2となり、異なる予算・旧binaryからの書込みを拒否する。
- `--grace-seconds 30 --lease-ttl-seconds 30`: 既存既定値を維持する。

transportのcommand/argsだけをguard経由へ置換し、元のserver argv/env/cwdを保持する。commandはshell文字列に組み立てない。
元の機密transportは本repositoryやreceiptに保存しない。復帰は利用元で保管した原設定の該当transportだけを、第三者変更のないことを確認して戻す。

## 適用前の必須ゲート

1. 対象の実効起動経路がdirect stdioであることを確認。Plugin定義commandの置換は行わない。
2. Desktopの接続ownerを一次証拠で確定する。直近shellや近いCodex祖先という理由だけでPIDを選ばない。
3. 静的設定に特定PIDを恒久埋込みしない。再起動・別接続でownerが変わる場合は利用元adapterが毎回渡す必要がある。公式接続点で渡せない場合はpilotを止め、EOFだけでowner監視受入済みと扱わない。
4. 旧launcherが新stateへ書き込まず、すべての管理対象が共通のstateと2/4予算を使用することを確認。
5. 具体的な対象バージョン、設定差分、保護対象、復帰手順を人間レビューする。実設定適用・再起動・既存process回収は別操作。

## 受入と計測

最初は同時1接続で5回smoke。その後2接続を同時に使い、片方の終了が他方へ影響しないことと、同種3本目・合計5本目の新起動が拒否されることを確認する。
拒否は待機queueではなく終了コード20であり、guardは自動retryしない。クライアント側のretryが増殖する場合はpilot不合格。
本受入は30回の有限反復とレビュー済み再起動前後。起動要求数・実起動数・接続数・owned Job残留・外部子孫観測・lease残留・終了時間・guard込みメモリ/handle数を比較する。
長い呼出しの通信空白で切断しないこと、shimの直接子が先に終了しても孫の応答が成功することも確認する。
未知owner、registry破損、Job割当て失敗、観測欠落、Job内外の食い違いはunknownとして停止。閾値を緩めるだけで合格にしない。

## 小さい改善・大きい改善

小さいループは一つの再現失敗、最小修正、該当検査と所要時間の再測定。大きいループは同じ操作の反復・再起動・復帰の比較。
負荷はguardを含む全体で比較し、未測定の削減率を作らない。ローカルtest合格、コード反映、Desktop受入、運用効果は独立の証拠を必要とする。

# ADR-0004: Desktop接続ownerの証拠ゲート

- 状態: 読取診断を採用、runtime owner adapterは実証待ち
- 日付: 2026-10-02
- 関連: ADR-0003、MCP_LIFECYCLE_PILOT.md

## 文脈と判断

実環境までの完了プランを、現行commit 19ef5efのコード付きで4AIへレビューした。
静的なMCP commandへclient owner PIDを固定埋込みする案は採用しない。既存の明示owner引数は、確定したownerを渡せる利用元と試験の契約として保持する。
名前、getppid、近い祖先、公開Codex mainとの類似だけで利用中Desktopの接続ownerを確定しない。

先行する追加実装は、起動された自分が継承するstdin/stdout handleを読み取るprobeである。
OSの文書化されたpipe APIの成否と数値エラー、peer PID候補と作成時刻をローカル診断へ記録する。名前検索、他processのhandle-table走査、debug権限追加をしない。
pipe APIの成功はowner証明ではない。両stdioのpeerが一致してもownership_statusはunknownを維持し、接続終了との対応と2接続の隔離を実証するまでguardのclient ownerへ渡さない。
長寿命共通runtime PIDは、接続ごとの寿命の十分な証拠ではない。PID生存だけでは、runtimeがhandleを保持したまま放棄した接続を判定できない。

## 読取probeの範囲

`probe-stdio-owner`は1回のJSON観測、`--mcp`は診断用MCPとして同じ観測を返す。
本体MCPを起動せず、registry・設定・vaultを書き換えず、プロセスを停止しない。
MCP stdoutはJSON-RPCだけ。EOFのreceiptはstderrへ出す。protocol frame上限を設け、機密値や不正入力本文をエラーへ返さない。
非Windows、API失敗、peer不明・不一致はunknownである。JSON観測の成功終了とowner証明の成功は別にする。

## 実runtimeへの適用ゲート

まずオフライン試験と独立レビューを完了し、診断MCP1件の具体的設定差分と復帰方法を提示する。
liveのMCP登録・通常再起動は対象別承認後。既存Obsidian transportを置換する前に、別名の診断serverで接続証拠を確認する。
thread終了、新規thread、通常アプリ終了でEOF、peer生存、probe生存を別々に観測する。2接続の片方終了を混同しない。
API不成立、peerが共通長寿命processだけ、観測欠落ではowner adapterの配線を止める。EOF＋上限＋Job回収だけへ完成条件を黙って下げない。

## 運用上の補足

上限はhost共有registryの管理対象接続に効き、Desktop専用やPC全体の上限ではない。
拒否をqueue待機と呼ばない。拒否後の60秒間の起動・再試行を記録し、反復負荷があれば受入を止める。
起動時間をguard startup、Job assign、initialize応答に分け、既存の初回timeoutを期限延長だけで解消したと扱わない。
以前の構成も世代蓄積しない場合、guard導入による削減と主張せず、追加コストと蓄積なしの実測を報告する。

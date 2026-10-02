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

## 2026-10-02: Obsidian一種類の限定試験

利用者は、共通長寿命peerしか観測できなかった結果を確認したうえで、Obsidian direct stdio一種類についてEOF、所有Job回収、同種2・管理対象全体4の上限で限定試験する方針を選択した。
これはowner証明を得たという判断ではない。既存の `--client-owner-pid` / `--client-owner-created-at` を省略し、stdio EOFと中継終了を接続終了の一次信号とする。guardは自身が起動したJobだけを猶予30秒後に回収する。上限は共有v2 registryに参加する接続だけに効く。

限定試験の合格条件は、通常呼出し、長い呼出し、EOF後のleaseと所有Jobの消滅、2接続の隔離、同種3本目の拒否、拒否後60秒の再試行増殖なし、5回smokeと30回反復、guard込みの総負荷比較、元設定への復帰を別々に実測すること。owner PIDに基づく接続放棄検知はこの試験の保証に含めない。
EOFを閉じずに共通runtimeが接続を保持し続ける場合、対象外processの自動killや無通信idle停止へ移らず、不合格として報告する。設定をOFFへ戻しても既存processが即時終了するとは仮定しない。差分と保護対象を確認してからDesktopへ適用し、通常再起動の前後も観測する。

この限定試験で「接続ごとのowner証明」条件は満たせない。実運用採用とタスク全体の完了判定には、測定した効果とこの制約を人間へ示して判断を得る。接続ownerの証明なしで完成扱いにする範囲変更は行わない。

## 限定試験の事前実測と設定ゲート

元のObsidian wrapperが参照する `local-ai-foundation` checkoutが欠けていた。既存private repoのmainを既定参照先にcloneして復元後、元の起動とguard経由の起動はともにinitializeとtools/listが成功した。設定値は未変更。

同じローカルstdio手順を直起動とguard経由で各30回実行し、全回成功。guard経由の終了後leaseは全回0。比較記録は終了後の子孫数・Job内外の残留数を収集しておらず、この点は未検証である。観測した起動木の最大値は直起動4 process・55.7MB・376 handles、guard経由6 process・95.1MB・647 handles。初期応答の中央値は約0.265秒対1.038秒。観測中のプロセス消滅による属性読取失敗があったため、最大値は厳密なピーク保証ではない。この局所試験から実Desktopでの負荷削減は主張しない。

設定差分案を独立レビューした結果、稼働中のCodexや別writerがconfig.tomlを書き換え得る状態では、直前hash確認と `os.replace` の間に第三者更新が入る可能性がある。ファイル名に対する原子的CASを保証できないため、アプリ稼働中の自動設定適用は停止する。実Desktopではまず元のdirect stdio設定で接続数・終了・負荷を観測し、残留や負荷の実害を示した場合だけ、他writerを止めた設定適用と原本保全をレビューしてguard配線へ進む。

## 運用上の補足

上限はhost共有registryの管理対象接続に効き、Desktop専用やPC全体の上限ではない。
拒否をqueue待機と呼ばない。拒否後の60秒間の起動・再試行を記録し、反復負荷があれば受入を止める。
起動時間をguard startup、Job assign、initialize応答に分け、既存の初回timeoutを期限延長だけで解消したと扱わない。
以前の構成も世代蓄積しない場合、guard導入による削減と主張せず、追加コストと蓄積なしの実測を報告する。

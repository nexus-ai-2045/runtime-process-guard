# 別チャット2枠受付の`Windows`限定試験（2026-10-31）

## 実測範囲

`Desktop`同梱`CLI`は0.165.0-`alpha.1.2`。既存`home` `hook`には`Orca`の`UserPromptSubmit`/`Interrupt`、`fact-provenance`の`Stop`/`SessionEnd`、通知には`CUA`と`notify` `mux`の既存連鎖がある。それらの変更・信頼回避は行っていない。

`Windows`の`multiprocessing` `spawn`で実際に第3`process`を開始し、2件`active`の間は`waiting`行が保存され`process`が待機すること、`A`の`exact` `turn`を解放後に自動で成功終了することを確認した。`Desktop3`チャット試験・`model`開始順の`E2E`ではない。

集中回帰15件成功。2枠、`FIFO`、同一`chat`の`steering`、遅延完了通知、有限待機、破損`state`、`agent` `flag`迂回拒否、登録済み子`agent`、解放保存失敗の`replay`、`ticket`破損、清掃失敗後の冪等性、未反映親完了の保護、`marker`公開`retry`、513件の清掃障害からの回復を検査した。独立レビューの指摘を有限3修正で処理し、最後の`backlog`修正は回帰テストで検証した。最後の差分の再レビューは`PR`で回収する。

## 適用前に残る判断

公式`hook`は新規定義を`review`/`trust`するまで実行されない。実行失敗や外側`timeout`を強制`block`へ変える権限もこの`package`にはない。既存`turn`を`drain`し、同じ登録を使う3試験チャットを開始できることを確認してから、`exact`定義の信頼確認と`live`接続を行う。未確認の間は観測常駐を受付制限済みと扱わない。

既存`notify`はローカルの`previous-notify` `JSON`へ退避した`argv`配列をそのまま委譲する。入力`payload`は既存通知へ渡すが本`gate`の`state`/ログへ本文を保存しない。復帰時は後続変更を照合し、今回の3`hook`と`notify`差分だけを戻す。

## 2026年11月2日のレビュー修復

受付と解放の判断を`pure` `function`へ分離し、`OS`の生存観測・`lock`・保存から独立して試験できるようにした。`Interrupt`の正確な`session`/`turn`を取消履歴として保持し、待機中の後続`poll`・`marker`再生・`state`再読取り後にも再受付を拒否する。

追加3件は修復前に失敗。修復後の集中22件と独立担当の同じ22件が成功し、`ruff`・契約・差分検査も成功した。`Desktop`実機の`hook`統合は未実施。512組の取消履歴上限を超えると無関係な受付・通知も安全側に拒否するため、導入前に`ADR-0007`の静止確認・`backup`・新`state`への明示切替を受入条件にする。今回その設定変更は実行していない。

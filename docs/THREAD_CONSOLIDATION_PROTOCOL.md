# テーマ別Codexタスク統合プロトコル

## 目的

テーマに関する会話、成果、TODOをDeeplink付きで正本タスクへ集約し、無関係話題を適切なタスクへ移譲した後、元タスク自身が残務ゼロを確認して自己archiveする。

## 状態機械

`discovered -> read -> topic_extracted -> unrelated_routed -> self_closeout_requested -> self_closeout_verified -> archived`

途中状態を飛ばさない。`archived`は`self_closeout_verified`のreceiptがある場合だけ許可する。

## 手順

1. state DBのtitle・preview・cwdで候補を出し、rollout JSONLの実user/assistant messageを直接読んで偽陽性を除く。
2. 各タスクに `codex://thread/<thread_id>`、取得状態、テーマ成果、TODO、混在話題、前身・後継を付ける。
3. テーマ成果とTODOを正本タスクへ移し、source Deeplinkと証拠位置を残す。
4. 無関係話題は機能名で既存タスクを検索する。引継ぎ先が一意なら送信し、受領receiptを取る。
5. 引継ぎ先が不明、親タスク・project不在、複数候補が競合する場合は、推測で新規タスクを作らず正本タスクへ判断を戻す。
6. 元タスクへ自己closeout packetを送り、元タスク自身に未完・人間待ち・外部操作待ち・unknownを再確認させる。
7. 元タスクが残務ゼロを返した場合、元タスク自身がarchiveする。中央管制はarchive状態をread-onlyで再確認する。
8. 残務があれば1〜7を繰り返す。同じ失敗が3回続いたら、原因、試した経路、未移譲項目、修復案を正本タスクへ戻し、仕組みの欠陥として扱う。

## 自己closeout receipt

```json
{
  "thread_id": "<thread-id>",
  "theme": "<theme>",
  "theme_residual": 0,
  "unrelated_residual": 0,
  "human_wait": 0,
  "external_wait": 0,
  "unknown": 0,
  "handoff_receipts": ["<destination-thread-id>"],
  "self_archive_requested": true,
  "archive_result": "archived"
}
```

1項目でも0でなければarchiveしない。receiptを書いたことだけで成功扱いせず、Codex APIまたはタスク一覧で`archived`を再確認する。

## 禁止事項

- memory・要約だけで全回収と断定しない。
- キーワード一致だけでテーマ関連と判定しない。
- 無関係話題を正本タスクへ混ぜたまま元タスクをarchiveしない。
- 中央管制による一括archiveを自己archiveと呼ばない。
- archive済みタスクを通知やreceipt作成のためにunarchiveしない。

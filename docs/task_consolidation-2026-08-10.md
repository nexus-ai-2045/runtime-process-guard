# Runtime Process Guard 関連タスク統合（2026-08-10）

## 統合先

- 管制・実装: [このCodexタスク](codex://thread/019fda3d-67d9-7a82-9d22-001d57159ef0)
- 実装正本: `nexus-ai-2045/runtime-process-guard`（当時 private。現在は public / MIT）
- 対象機能: Codex配下processのread-only観測、起動前判定、世代比較、lineage差分、fail-closedフィードバック

## 回収したタスク

| タスク | 回収内容 | 残務・扱い |
|---|---|---|
| [PCが重すぎる](codex://thread/019fb6f0-85ef-7c01-8440-2798cd31859f) | 高負荷実測、MCP多重化、起動前抑制案、leaseとshadow設計 | 実装・PR残務を本タスクへ吸収後、archive候補 |
| [ジピコのミュート設定](codex://thread/019fe474-fb46-7c53-a045-9534aaafbe5a) | 音声遅延からruntime診断を切り出し | runtime部分のみ吸収。`Ctrl+M`設定は音声タスク固有で、ユーザー操作待ちのため保持 |
| [windows-process-doctor統合](codex://thread/019fe481-3b8c-71a1-9033-94c551f1e1f1) | Projects共通診断へ統合、PR #535 merge | 機能残務0、既にarchive済み |
| [feedback-cycle実装](codex://thread/019fe4b6-e016-79d2-a03d-6e167f070b5e) | trend判定、state保存、exit 40 fail-closed、26 tests | 未commit・未push・未PRを本タスクへ吸収後、archive候補 |
| [MCP遅延寄与分類](codex://thread/019fa772-9725-7b42-a8f7-4ce9fb41ef1e) | 常駐量とopen時fan-outを主要仮説として記録 | 500ms粒度計測は `NEXT_RUN.md` の将来検証へ吸収 |
| [Codex latency診断設計](codex://thread/019fa7ce-af32-7ca0-88c8-235fe6beb868) | runtime/storage/context/UI/OSの責任境界 | 設計前史として吸収、個別実装残務なし |

## 廃止済みautomation run

次の13件は「5分ごと Codex重複MCP安全回収」の定期runである。固定間隔の外部killは安全条件を満たせず、automation自体は削除済み。診断順序、保護条件、自然変動との区別だけを本repoへ吸収する。

- [12:25](codex://thread/019fe48d-a405-7590-b828-036f2450eaec)
- [12:30](codex://thread/019fe492-38d5-7ca3-ad0c-379b5fad476f)
- [12:35](codex://thread/019fe496-cef6-7143-b770-c364bebda076)
- [12:40](codex://thread/019fe49b-d663-7962-a91b-9e125d95ce78)
- [12:45](codex://thread/019fe4a0-0a9f-7052-873d-c5a086d65868)
- [12:55](codex://thread/019fe4a9-1d32-7461-9827-a73960697446)
- [13:00](codex://thread/019fe4ad-b4e5-7000-9ad0-c1819e072d61)
- [13:05](codex://thread/019fe4b2-4af3-7973-afbc-0cfd9587347d)
- [13:10 / feedback-cycleへ転換](codex://thread/019fe4b6-e016-79d2-a03d-6e167f070b5e)
- [13:15](codex://thread/019fe4bb-74d6-7401-be3f-60d7c78636fe)
- [13:20](codex://thread/019fe4c0-0d59-7ff2-b46a-b4804aad1b51)
- [13:25](codex://thread/019fe4c4-9cec-78e1-88b4-7cbec0d1ae5a)
- [13:30](codex://thread/019fe4c9-3186-71a1-a2c4-319291e635df)（既にarchive済み）

## 機能境界と残務

- 完了対象: read-only preflight、shadow snapshot、feedback-cycle、lineage snapshot、privacy、fail-closed、テスト、CI、private PR。
- 将来対象: runtime-owned lease、Windows Job Object guarded launcher、graceful shutdown/postflight、Codex実起動経路の1 MCP限定pilot。
- 禁止線: 名前一致kill、一括停止、設定・hook・scheduled task変更、実runtimeへのblock昇格は別の明示承認なしに行わない。
- 運用保証の表現: ローカルテスト、CI、ライブread-only smoke、継続運用、block/回収を分け、未実施を成功扱いしない。

## archive判定

本repoへの回収、private PR作成、各タスクへの移譲通知、Codex APIでの状態再確認が揃ったタスクだけをarchiveする。ユーザー操作待ちや未移譲の固有残務があるタスクは保持する。

## 2026-08-10 厳格再監査

- 関連18タスクの内訳は、今回archive 15、以前からarchive 2、音声UI固有残務のためactive保持 1である。
- runtimeテーマの成果・TODOは本タスクへ回収し、音声タスクの`Ctrl+M`設定は元の音声タスクへ残した。
- 今回archiveした15件は統合元タスク自身の自己closeoutではなく、統合管制からCodex APIを呼んだ中央archiveである。したがって「各タスク自身が残務ゼロを確認して自己archive」の証拠は未取得であり、厳格条件5/7はhistorical exceptionとする。
- archive済みタスクを通知目的でunarchiveしない。今後は [`THREAD_CONSOLIDATION_PROTOCOL.md`](THREAD_CONSOLIDATION_PROTOCOL.md) の自己closeout receiptをarchive前の必須条件にする。

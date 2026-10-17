# Claude 側管制チャットの closeout（2026-08-14）

[`THREAD_CONSOLIDATION_PROTOCOL.md`](THREAD_CONSOLIDATION_PROTOCOL.md) 手順 5〜7 の実行記録。
[`task_consolidation-2026-08-12-claude.md`](task_consolidation-2026-08-12-claude.md) の後続（次の日付の記録）。

## 決定: 管制の一本化（CEO 承認 2026-08-14）

**正本 = Codex 側管制 thread** [`codex://thread/019fda3d-67d9-7a82-9d22-001d57159ef0`](codex://thread/019fda3d-67d9-7a82-9d22-001d57159ef0)。
根拠: 前回統合（2026-08-10）の管制であり、repo docs が管制と明記済み。B2 (lease) の実装も
Codex 側で進行中（local branch `codex/lease-registry`、未 push）。
Claude 側チャット `local_bd2be0d2-...` は本記録をもって管制役を降り、self-closeout する。
二重管制の SSOT 違反はこれで解消。

## 08-12 記録以降に閉じたもの（証拠つき）

| 項目 | 証拠 |
|---|---|
| A1/A2 plugin-window-policy の版管理 + fail-closed 化 | PR #2 MERGED (2026-08-12) |
| B3 Reconciler 再稼働 | 2026-08-14 実測: 停止2日で drift 4件 → apply changed=4 → 収束。タスク State=Ready / LastTaskResult=0 / receipt が新 schema (mode=apply) で更新。実行元は Codex 作業ブランチと分離した専用 worktree `runtime-process-guard-taskrun`（origin/baseline に detached 固定） |
| C1 machine churn 根治 | dot-claude `5a9e776` (`git rm --cached`)。クラス防止として Projects #546 / #549 (tracked∧ignored ratchet gate) MERGED |
| D5 追跡漏れ 4 本 | Projects #546 MERGED |
| F3 / F8 | C0 実測 (2026-08-12) で対象消滅 |
| 同類矛盾 779 件 | 棚卸し 779 → 要判断 7（Projects #550 MERGED 2026-08-14） |

副産物: 汎用化版を独立 repo `nexus-ai-2045/ai-ratchet-gate` として作成・push 済み（CI green）。

## 残件の移管表（この記録の merge をもって正本へ移る）

| 群 | 内容 | 移管先 / 所有者 |
|---|---|---|
| B1 | HUMAN_REVIEW パイロット（Codex Desktop 再起動 + read-only 再測定） | **Codex 管制**（人間判断待ち） |
| B2 | lease → Job Object → graceful shutdown → 1-MCP pilot | **Codex 管制**（lease は Codex 作業中） |
| G1 | 本 repo 自身の台帳 2 つへの登録 | 登録 commit を Projects PR #552 / nexus_ai PR #109 へ追加済み（merge 待ち） |
| C2/C3 | Projects PR #523 (dot-claude autocommit) の merge 判断 + bash-gate 誤検知 | workspace 運用（CEO 判断） |
| D1-D4, D6 | skill home shadow の drift・2 分岐統合・repo/skill 未接続 | workspace 運用（D2 統合は CEO 判断が先） |
| E1-E3 | DiscordAiPartyBot conhost 化 / Rotate API key タスク / restore-tasks (要 admin) | workspace 運用（CEO 操作） |
| F1, F2, F4-F7, F9-F11 | 拡張無効化・watch タスク登録・crashdump ほか | workspace 運用 |
| — | push_gate 空 remote 初回 push 対応 | 別セッション進行中（chip task_48673b8f） |
| — | main checkout 567 dirty 仕分け + salvage branch 2 commit の後始末 | CEO 指示で parked（`salvage/main-checkout-orphans-20260814` に保全済み） |
| — | security: OAuth Bearer token の平文露出 (Candidate6 chat) のローテーション要否 | **CEO 判断（未回答のまま移管）** |

## 引継ぎ 3 件の到達状況

| 宛先チャット | 状態 |
|---|---|
| DCB snapshot store分裂の解消 | 受領確認（transcript 反映を検索で実測） |
| Note Publishing Suite 設計レビュー | 送達 (queued)。transcript 反映は未確認 |
| ワークツリーの整理 | 送達。transcript 反映は未確認 |

未確認 2 件の引継ぎ内容は merge 済みの 08-12 記録 §3 に恒久記録済みのため、消失リスクなし。

## self-closeout receipt（Claude 側チャット）

```json
{
  "thread_id": "local_bd2be0d2-52c5-4740-9d76-810e3fdbbac9",
  "theme": "runtime-process-guard (Claude Code side)",
  "theme_residual": 0,
  "unrelated_residual": 0,
  "human_wait": 0,
  "external_wait": 0,
  "unknown": 0,
  "handoff_receipts": [
    "codex://thread/019fda3d-67d9-7a82-9d22-001d57159ef0",
    "local_a364be97-5a51-4e4b-b083-6f43861f85a8",
    "local_c9432334-e561-45d8-b428-9e9cd85e6878",
    "local_7ef92d40-6623-44f2-8321-1c2697ea8689"
  ],
  "self_archive_requested": true,
  "archive_result": "pending-merge-then-archive"
}
```

各 0 は「消えた」ではなく「正本（Codex 管制）と各所有者へ移管済み」の 0。
本記録の merge 前に archive しない（receipt を書いたことだけで成功扱いしない）。
merge 後、チャット自身が self-archive し、archive 状態は CEO が read-only で確認できる。

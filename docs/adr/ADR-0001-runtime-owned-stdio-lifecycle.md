# ADR-0001: runtime-owned stdio lifecycle

- 状態: Accepted for shadow pilot
- 日付: 2026-08-26
- 正本topic: guarded stdioの所有権と終了境界
- 関連判断: [PROCESS_LIFECYCLE_DECISION.md](../PROCESS_LIFECYCLE_DECISION.md)

## 文脈

Codex Desktopのthread／MCP manager世代更新に伴い、stdio MCPの子processが複数世代残る事象がある。
既存のadmission、lease分類、shadow観測は起動・終了を所有していないため、名前一致killや固定間隔回収へ安全に昇格できない。
上流CodexにもJob Objectとstdio close→wait→terminateの実装例があるため、同じOS標準の寿命モデルを採用し、独自の回収概念は作らない。

## 判断

`runtime-process-guard guarded-stdio` が直接起動したprocessだけをmanaged対象とする。WindowsではJob Objectへ割り当て、
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`を設定する。stdin EOFまたは明示されたidle policyにより子stdinを閉じ、grace期間を待ち、
残存時だけmanaged Jobを終了する。既存PIDの後付け採用、一般Node、名前一致、Codex app-rootの停止は扱わない。

既定modeはshadowとし、idle終了は無効にする。enforceはactive JSON-RPC request追跡が未接続の間はfail-closedで拒否する。
request追跡の実装、Obsidian 1種類の受入証拠、人間レビューをすべて満たした後の別操作とする。

## 不変条件と検知

- commandはshell文字列ではなくargvとして直接起動する。
- receiptへargv、絶対path、MCP本文、環境変数を保存しない。
- Job割当失敗時は子を残さず、起動失敗として返す。
- unmanagedまたはowner不明はreport-onlyとする。
- shadow 7日／30 lifecycle、誤回収0、保護対象停止0をenforce昇格条件とする。

## 影響と限界

このadapterは新規にguard経由で起動したprocess木の寿命を保証するが、Codex app-server内部のmanager leakそのものは修正しない。
stdio byte activityはJSON-RPC requestの意味解析ではないため、idle policyを全MCPへ一般化しない。

## 参照

- Microsoft Learn: Job Objects
- Model Context Protocol: stdio transport lifecycle
- OpenAI Codex: `codex-rs/utils/pty/src/win/job.rs`
- OpenAI Codex: `rmcp-client/src/stdio_server_launcher.rs`

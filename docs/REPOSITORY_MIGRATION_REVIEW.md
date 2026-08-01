# 人間レビュー: private lane移管

## 記録情報

- schema_version: `runtime-process-guard/repository-migration-review-v1`
- recorded_at: `2026-08-02T00:00:22+09:00`
- recorded_by: `codex`
- owner: `codex`
- source: `C:/Users/yas/Projects/Documents/.repos/runtime-process-guard`
- target: `C:/Users/yas/Projects/Documents/.repos/nexus_ai/private/runtime-process-guard`
- external_boundary: remote作成、push、公開、visibility変更、元履歴削除は未実行

## 実行済み

独立Git repositoryを `.git` とcommit履歴を保ったまま、private laneへ1 repositoryだけ移動した。

検証結果:

- source path: 不在
- target path: 実在
- branch: `main`
- verified HEAD: `e99192c9728f9883c9355fc2249b4615df8374a8`
- commit count: 4
- worktree: clean
- remote count: 0
- tests: 13 passed
- compileall: passed
- old path reference: 0

## repository SSOT登録案

`nexus_ai/config/repository_path_migration.json` の `repositories` へ、次のentryを追加する案。

```json
{
  "name": "runtime-process-guard",
  "source": "C:/Users/yas/Projects/Documents/.repos/runtime-process-guard",
  "target": "C:/Users/yas/Projects/Documents/.repos/nexus_ai/private/runtime-process-guard",
  "repo_class": "own_private",
  "migration_state": "moved_verified_pending_codex_project_reregistration",
  "dirty_paths": 0,
  "verified_head": "e99192c9728f9883c9355fc2249b4615df8374a8",
  "moved_at": "2026-08-02T00:00:22+09:00",
  "verified_at": "2026-08-02T00:00:22+09:00",
  "codex_project_reregistration": false,
  "notes": "Private local runtime safety tool. No Git remote. Logic review findings remain open; block mode is not approved."
}
```

現在のregistry fileにはこの作業より前の未commit差分がある。既存WIPを巻き込むため、このタスクではentryを直接追記・commitしない。

## 必要な人間判断

### 1. SSOT registryへの追記

既存の `config/repository_path_migration.json` 差分のownerと内容を確認後、上記entryを統合してよいか。

推奨: 統合する。ただし既存WIPと同じcommitへ混ぜず、owner確認後に明示pathで扱う。

### 2. Codex project再登録

保存済みproject/workspaceの参照先を、次へ変更してよいか。

`C:/Users/yas/Projects/Documents/.repos/nexus_ai/private/runtime-process-guard`

推奨: 再登録する。旧source pathは既に存在しない。

### 3. ロジック修正の新タスク開始

repository SSOTの `start_new_task_after_move=true` に従い、次の修正は新しいタスクで行う。

優先修正:

1. command identityへ正規化cwdまたは安定server IDを含める。
2. preflight判定と起動権取得をatomic leaseにする。
3. dedicated stdioにowner/generation budgetを追加する。
4. report書込み時の `read_only` / `changed` 表示と出力先制限を直す。

推奨: 採用する。修正・再レビュー完了前にblock mode、自動reuse、自動killを有効化しない。

## rollback

移管のrollbackが必要な場合は、旧sourceが不存在であることを再確認してから、repository全体を元pathへ1回だけ移動する。自動rollbackは行わない。

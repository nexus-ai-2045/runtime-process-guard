<!-- repo-preflight:review-record -->

# Repo preflightレビュー記録

## 対象と公開境界

- repository: `nexus-ai-2045/runtime-process-guard`
- visibility: private
- 対象branch: `codex/plugin-window-policy`
- public化・release・外部告知: 対象外
- 人間による最終目視とmerge判断: 未完了

## 自動確認

- working tree / diff: `git status --short --branch`, `git diff --check`
- test: `python -m pytest -q`
- CI: Ubuntu / Windows、Python 3.11 / 3.13
- secret / personal path / required documents: repo-preflight
- GitHub identity / remote owner / private visibility: github-cli-ops-guard

## 保証境界

自動検査のpassは、secret不存在の完全保証、依存脆弱性不存在、live runtimeの継続動作、
Scheduled Task再登録、公開承認を意味しない。`unknown`と人間レビュー待ちは完了へ丸めない。

## rollback

plugin cacheへのapplyは別承認とし、異常時はreceiptの `aborted` / `next_action` に従う。
`rollback-incomplete` では第三者更新を上書きせず、対象manifestを人間が確認する。

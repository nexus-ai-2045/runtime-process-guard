<!-- repo-preflight:review-record -->

# Repo preflightレビュー記録

## 対象と公開境界

- repository: `nexus-ai-2045/runtime-process-guard`
- visibility: **private**（維持。visibility 変更・公開は別承認）
- default branch: `baseline`
- ライセンス: All rights reserved
- public化・release・外部告知: 対象外
- 人間による最終目視と merge 判断: 必須（自動緑は merge 承認ではない）

## 開発保証ゲート

検査ロジックは本リポジトリへコピーしない。上流を直接呼ぶ。`engineering-brain` は埋め込まない。

| 契約 | 上流 | 設定 | 扱い |
|---|---|---|---|
| 文書・実装の宣言整合 | `nexus-ai-2045/repo-preflight`（pin SHA） | `.repo-preflight-consistency.json`（`shadow`） | `consistency_gate` + `readiness_scan`。shadow 所見は止めない。`tool_error` は fail-closed |
| tracked ∧ ignored の新規悪化 | `nexus-ai-2045/ai-ratchet-gate` v0.1.1（wheel + SHA-256） | `.ai-ratchet-gate/baseline.txt` | 既存分は grandfather。baseline に無い新規だけ deny |

整合契約は `.repo-preflight-consistency.json` を正とする。検査ロジックの複製はしない。

### トリガ方針（Actions 課金）

- 開発保証 workflow（`repository-guarantees.yml`）は **`workflow_dispatch` のみ**。`pull_request` / `push` では起動しない
- `workflow_dispatch` で BASE と HEAD が同一（空 diff）のときは差分検査を緑にしない（fail-closed）
- 既存の `ci.yml`（pytest + 薄い consistency）は別契約。本節の開発保証とは混ぜない

### 手元同等の確認手順

feature 枝で `origin/baseline` との差分がある状態で実行する（`BASE==HEAD` は意図的に失敗させる）。

```bash
# 1) ai-ratchet-gate（tracked∧ignored の新規悪化だけ deny）
python -m pip install --require-hashes -r requirements-tools.txt
python -m ai_ratchet_gate --repo .

# 2) repo-preflight（上流 clone。検査ロジックはコピーしない）
REPO_PREFLIGHT_SHA=f825268978228a3cfb2f5ecba16a74d424134b1a
git clone --no-checkout https://github.com/nexus-ai-2045/repo-preflight.git /tmp/repo-preflight
git -C /tmp/repo-preflight checkout --detach "$REPO_PREFLIGHT_SHA"
test "$(git -C /tmp/repo-preflight rev-parse HEAD)" = "$REPO_PREFLIGHT_SHA"

git fetch origin baseline
BASE="$(git rev-parse origin/baseline)"
HEAD="$(git rev-parse HEAD)"
test "$BASE" != "$HEAD"  # 空diff fail-closed

python /tmp/repo-preflight/scripts/consistency_gate.py \
  --repo . --base-ref "$BASE" --require-config --require-mode shadow --json

python /tmp/repo-preflight/scripts/readiness_scan.py \
  --repo . --release --consistency-base-ref origin/baseline
```

Actions で同等確認する場合は、feature 枝を選んで `repository-guarantees` を `workflow_dispatch` する（default branch 直上だと空 diff で fail-closed）。

## 自動確認

- working tree / diff: `git status --short --branch`, `git diff --check`
- test: `python -m pytest -q`
- CI: Ubuntu / Windows、Python 3.11 / 3.13（`ci.yml`）
- secret / personal path / required documents: repo-preflight（上流 pin）
- tracked ∧ ignored の新規悪化: ai-ratchet-gate

## 保証境界

自動検査の pass は、secret 不存在の完全保証、依存脆弱性不存在、live runtime の継続動作、
Scheduled Task 再登録、公開承認、merge 承認を意味しない。`unknown` と人間レビュー待ちは完了へ丸めない。

visibility は private のまま。merge は人間のみ。Settings / rulesets / Actions permissions の変更、
および required status checks の追加は本記録の範囲外。

## rollback

plugin cache への apply は別承認とし、異常時は receipt の `aborted` / `next_action` に従う。
`partial-write` では自動 rollback せず、適用済み件数と対象 manifest を人間が確認する。

# エージェント指示

## 対象範囲

この`repository`は、ローカルプロセスの起動前判定と終了後検証を扱う。
無効化済み`MCP`の既存`PID`を対象とする明示操作者経路は、起動前`guard`と区別する。使用状況の操作者確認、保存計画の照合、対象の完全な実体・`PID`世代検査がない停止は行わない。

## 安全境界

- 既定動作は読み取り専用、`fail-closed`とする。
- `raw` `command` `line`、環境変数、`secret`、ユーザー名、`home`絶対パスを保存・表示しない。
- 名前一致の一括`kill`を実装しない。
- プロセス停止、設定変更、`hook`有効化、`scheduled` `task`登録には現在会話での明示承認を必要とする。
- `remote`作成、`push`、公開、外部送信は別承認とする。`remote`を作る場合の既定`visibility`は`private`。
- `Windows`の補助プロセスは `CREATE_NO_WINDOW` 相当で起動する。

## 検証

- `policy`は`pure` `function`として単体テストする。
- `collector`と`launcher`を分離し、テストで実プロセスを不要にする。
- 完了報告では`local` `test`と実ランタイム統合を分ける。

## リポジトリ運用契約

- 作業前に `REPO_GOAL.md` と `.repo-operating-contracts/manifest.json` を読み、この`repository`の目的と契約を確認する。
- 変更後は `python .repo-operating-contracts/check.py` を実行し、契約からの`drift`がないことを確認する。

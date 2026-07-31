# Agent Rules

## Scope

このrepositoryは、ローカルprocessの起動前判定と終了後検証を扱う。

## Safety boundaries

- 既定動作はread-only、fail-closedとする。
- raw command line、環境変数、secret、ユーザー名、home絶対パスを保存・表示しない。
- 名前一致の一括killを実装しない。
- process停止、設定変更、hook有効化、scheduled task登録には現在会話での明示承認を必要とする。
- remote作成、push、公開、外部送信は別承認とする。remoteを作る場合の既定visibilityはprivate。
- Windowsの補助processは `CREATE_NO_WINDOW` 相当で起動する。

## Verification

- policyはpure functionとして単体テストする。
- collectorとlauncherを分離し、テストで実processを不要にする。
- 完了報告ではlocal testと実runtime統合を分ける。

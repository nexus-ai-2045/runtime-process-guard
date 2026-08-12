# コントリビューション

## セットアップ

```powershell
python -m pip install -e .
python -m pytest -q
```

## 開発ルール

- CLIの既定動作はread-onlyを保つ。書込みは明示フラグと人間承認を要する。
- `unknown`、走査不能、部分失敗を成功へ丸めない。
- process停止、自動kill、設定・hook・Scheduled Task変更をこのrepoから暗黙に実行しない。
- receiptにはcommand line、manifest path、個人絶対path、secret本文を保存しない。
- Windows / Linux、Python 3.11 / 3.13の差を考慮する。
- 挙動変更は失敗する回帰テストを先に追加する。
- `main` / `baseline`へ直接pushせず、`codex/` branchからPRを使う。

## 変更前後の確認

```powershell
git status --short --branch
git diff --check
python -m pytest -q
```

GitHubへのpush、PR、merge、visibility変更はそれぞれ別の承認境界として扱う。

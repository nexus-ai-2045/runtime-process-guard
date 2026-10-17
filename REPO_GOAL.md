# リポジトリの目的

```yaml
repo: runtime-process-guard
owner: nexus-ai-2045
current_goal: ローカルのsubprocess、MCP server、automationを起動する前に、重複・CPU・メモリ・親子関係を確認するprivacy-firstの実行前ガードを提供する。
```

明示的な運用範囲: 操作者が承認した無効化済み`MCP`について、保存設定・`manifest`・実体・親・`PID`世代を照合した限定的な手動回収を提供する。既定は読み取り専用とし、自動`idle`判定、無差別停止、既存`PID`の所有偽装は行わない。実行前ガードと既存`PID`への操作者操作を別経路として扱う。

`Mac`の管理付き起動も、起動時から所有できる接続を対象とする限定経路です。無通信による未使用判定、未管理プロセスへの所有の後付け、自動的な製品接続への適用は行いません。負荷削減の実測と接続境界が不足する場合は、製品への導入を見合わせます。

フックの観測は、呼出しの対応と起動元の所有証拠を区別する診断経路です。開始・応答の欠落や不明を保護し、イベントだけで停止権限や既存`PID`の所有を作りません。

## 完了レイヤー

- `local implementation`:
- `local verification`:
- `branch / commit`:
- `push / PR`:
- `merge / external state`:
- `cleanup`:
- `unrelated dirty state`:

## 停止線

- `publication`: 現在会話での人間レビューと明示承認まで停止
- `repository` `visibility`: `repo`単位の明示承認まで変更しない
- `external` `send` / `post` / `comment`: 明示承認まで停止
- `hook` / `automation`: 配置のみ。`install` / `enable`は明示承認まで停止
- `auth` / `secret` / `production`: 明示承認まで変更しない

## 検証記録

- `git state`:
- `changed files`:
- `verification`:
- `external actions performed`: `false`
- `remaining risks`:

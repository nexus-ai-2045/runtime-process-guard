# アーキテクチャ

## 目的

重くなった後に一括停止するのではなく、管理対象の`process`を起動する直前に `admission` 判定を行い、不要な重複起動を防ぐ。

## 判定フロー

```text
launch request
  -> privacy normalization
  -> ephemeral command identity
  -> process/resource observation
  -> admission policy
      allow   : 呼び出し側が起動してよい
      reuse   : 同一identityが存在。新規起動しない
      defer   : resource pressure。後で再試行
      deny    : policy上禁止
      unknown : 観測不能。起動しない
```

`preflight` は引き続き既定の`read-only`入口である。限定`pilot`用の `guarded-stdio --mode shadow` は、
`guard`自身が起動した`process`だけを所有する。`idle` `enforcement`は未接続で`fail-closed`とし、正本判断は
`docs/adr/ADR-0001-runtime-owned-stdio-lifecycle.md`を参照する。

## 識別値

`identity`は実行ファイル名と引数列を正規化して`SHA-256`化する。次の値は`hash`前に除去または置換する。

- `home` `directory`: `%USERPROFILE%`
- `api-key`、`token`、`password`、`secret`などの値: `<redacted>`

`raw`値は永続化しない。異なる`secret`を使う同一`server`は、同じ`process` `identity`として扱う。

## 実行環境ごとの境界

`policy`と`privacy`処理は`OS`非依存。収集は`psutil`を使い、`Windows`、`macOS`、`Linux`で共通化する。

今後`OS` `adapter`が必要になる対象:

- `Windows`: `Job` `Object`、`CREATE_NO_WINDOW`、`Processor` `Queue`
- `macOS`: `process` `group`、`launchd`、`syspolicyd` / `trustd`
- `Linux`: `process` `group`、`cgroup`、`OOM` `pressure`

## 次段階

1. [`shadow`実装済み] `lease` `registry`: `owner`、親`PID`、開始時刻、終了責任を匿名記録
2. [`shadow`実装済み] `guarded` `launcher`: `guard`所有の子`process`だけを起動
3. [`shadow`実装済み] `postflight`: 同じ`Job`の`active` `process`数が0になったことを確認後、自分の`lease`だけ解除
4. `MCP` `adapter`: `server` `identity`単位の`reuse/defer`

既存の実行環境への接続は読み取り専用の境界を越えるため、要求追跡と個別レビュー後に行います。実装した限定回収の範囲は次節のとおりです。


## 管理付き接続終了時の限定回収

`macOS`の`guarded-stdio --mode enforce --track-requests`は、起動から所有する専用接続の入力終端だけを回収契機とします。`rpc_activity.py`で双方向の要求、分割メッセージ、出力転送を追跡し、不明・未応答・転送中・明示保護では保持します。停止直前の所有世代と状態の再検査は既存所有者が実行し、メニューバーや保存済み一覧へ停止権限を渡しません。

上記は無通信による停止ではありません。既存の`idle`による回収の未接続判断は維持します。`Windows`の新しい要求追跡付き回収は起動前に拒否し、既存の読み取り観測へ戻します。例外・観測失敗の後処理は未確認の子孫へ終了信号を送りません。未読出力やサーバー内部の非同期処理は観測外のため、常時接続・自律動作するサーバーの本番接続は別途検証します。

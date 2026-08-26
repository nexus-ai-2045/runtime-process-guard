# アーキテクチャ

## 目的

重くなった後に一括停止するのではなく、管理対象のprocessを起動する直前に admission 判定を行い、不要な重複起動を防ぐ。

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

`preflight` は引き続き既定のread-only入口である。限定pilot用の `guarded-stdio --mode shadow` は、
guard自身が起動したprocessだけを所有する。idle enforcementは未接続でfail-closedとし、正本判断は
`docs/adr/ADR-0001-runtime-owned-stdio-lifecycle.md`を参照する。

## identity

identityは実行ファイル名と引数列を正規化してSHA-256化する。次の値はhash前に除去または置換する。

- home directory: `%USERPROFILE%`
- `api-key`、`token`、`password`、`secret`などの値: `<redacted>`

raw値は永続化しない。異なるsecretを使う同一serverは、同じprocess identityとして扱う。

## platform境界

policyとprivacy処理はOS非依存。収集はpsutilを使い、Windows、macOS、Linuxで共通化する。

今後OS adapterが必要になる対象:

- Windows: Job Object、`CREATE_NO_WINDOW`、Processor Queue
- macOS: process group、`launchd`、`syspolicyd` / `trustd`
- Linux: process group、cgroup、OOM pressure

## 次段階

1. lease registry: owner、親PID、開始時刻、終了責任を匿名記録
2. [shadow実装済み] guarded launcher: guard所有の子processだけを起動
3. postflight: 親終了後の子process回収を確認
4. MCP adapter: server identity単位のreuse/defer

runtime設定への接続とenforceはread-only境界を越えるため、active request追跡と個別レビュー後に実装する。

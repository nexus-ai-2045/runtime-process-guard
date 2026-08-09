# 実測レポート索引

| 時刻（JST） | ファイル | 用途 |
|---|---|---|
| 2026-08-09 12:40 | `codex-lineage-2026-08-09T1240JST.json` / `.md` | 回収前のCodex親子・孫チェーン。168プロセス |
| 2026-08-09 13:55 | `codex-lineage-2026-08-09T1355JST.json` / `.md` | 回収後スナップショット。前回比で追加38、消滅163、安定5、現存43 |
| 2026-08-01 | `codex-mcp-baseline.json` / `codex-mcp-shadow-latest.json` | 匿名MCP identityと世代幅のshadow baseline |

この表はローカル実測の索引であり、`reports/*.json` は個別runtimeのPID・時刻を含むためGitへ追跡しない。PRやCIで再現可能な証拠はテストへ固定し、実測値を運用保証として流用しない。

JSONはraw command line、ユーザー名、home絶対path、secretを含まない。Mermaidでは前回から追加されたPIDを緑で表示する。PID再利用は作成時刻との組で判定する。

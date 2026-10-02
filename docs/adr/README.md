# Architecture Decision Records

このdirectoryは、runtimeの所有権・安全境界・互換性に影響する採用判断の正本です。
連番はrepo内で採番し、既存判断の置換ではなく参照関係を明記します。

| ADR | 状態 | 判断 |
|---|---|---|
| [ADR-0001](ADR-0001-runtime-owned-stdio-lifecycle.md) | Accepted for shadow pilot | guard起動processだけをJob Objectで所有する |
| [ADR-0003](ADR-0003-bounded-connection-lifecycle.md) | ローカル実装採用・runtime受入待ち | 接続単位の必要時起動、同種2・全体4の起動上限 |

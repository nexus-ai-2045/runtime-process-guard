# 設計判断の記録

この`directory`は、`runtime`の所有権・安全境界・互換性に影響する採用判断の正本です。
連番は`repo`内で採番し、既存判断の置換ではなく参照関係を明記します。

| `ADR` | 状態 | 判断 |
|---|---|---|
| [`ADR-0001`](ADR-0001-runtime-owned-stdio-lifecycle.md) | `Accepted` `for` `shadow` `pilot` | `guard`起動`process`だけを`Job` `Object`で所有する |
| [`ADR-0003`](ADR-0003-bounded-connection-lifecycle.md) | ローカル実装採用・`runtime`受入待ち | 接続単位の必要時起動、同種2・全体4の起動上限 |
| [`ADR-0004`](ADR-0004-stdio-owner-evidence-gate.md) | 読取診断採用・`owner`実証待ち | 継承`stdio`の`peer`観測と接続`owner`の証明を分離する |
| [`ADR-0005`](ADR-0005-system-observation-and-cleanup-evidence.md) | 全体観測・表の実装済み、整理権限の拡張なし | `PC`全体の観測、チャット帰属、軽量監視、安全な整理の証拠境界 |
| [`ADR-0006`](ADR-0006-windows-resident-pressure-watch.md) | Windows限定受入中 | 低負荷常駐、単一起動、起動制御との境界 |

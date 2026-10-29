export const state = v => ({observe:"観測",review:"整理候補",protect:"保護",drain:"受付終了・排出確認",stopped:"停止結果確認へ"}[v.phase]);
export const step = a => ({Observe:"証拠を判定",Drain:"受付終了を確認",Stop:"停止を要求",EvidenceChanges:"状態やPID世代が変化",Refresh:"再観測",Done:"終了"}[a] ?? a);
export const title="所有と処理状態を再確認して整理する";

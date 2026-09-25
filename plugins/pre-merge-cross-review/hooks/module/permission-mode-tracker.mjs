// pre-merge-cross-review hooks module の permission mode 記録 (I/O を持たない状態管理)。
//
// tool.check の入力は permission mode も agent も持たないため、次の 2 つを記録して引き当てる。
// - agent ごとの最新の permission mode: permission_mode を持つ classic イベントの入力から、
//   agent_id (subagent のみ。メインは無し) ごとに記録する
// - 呼び出しごとの agent: tool.call の入力の tool_use_id と agentId (subagent のみ) の対応。
//   tool.check は同じ呼び出しの tool.call の後に評価される
//
// 引き当ての規則:
// - 呼び出しの記録が無い tool_use_id、または呼び出し元の agent に mode の記録が無い場合は
//   undefined (mode 不明) を返す。別の agent の mode を代わりに使わない
// - mode は文字列のときだけ記録する

/**
 * permission mode の記録を作る。
 *
 * @returns {{
 *   recordMode: (args: { agentId: unknown, mode: unknown }) => void,
 *   recordCall: (args: { toolUseId: unknown, agentId: unknown }) => void,
 *   forgetCall: (toolUseId: unknown) => void,
 *   modeForCall: (toolUseId: unknown) => string | undefined,
 * }}
 */
export const createPermissionModeTracker = () => ({
  recordMode: () => {},
  recordCall: () => {},
  forgetCall: () => {},
  modeForCall: () => undefined,
});

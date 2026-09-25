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
export const createPermissionModeTracker = () => {
  // メインの loop を表すキー。agent の id (空でない文字列) と衝突しない値にする。
  const MAIN = Symbol("main");
  const modesByAgent = new Map();
  const agentsByCall = new Map();

  const agentKey = (agentId) =>
    typeof agentId === "string" && agentId !== "" ? agentId : MAIN;
  const isToolUseId = (toolUseId) => typeof toolUseId === "string" && toolUseId !== "";

  return {
    recordMode: ({ agentId, mode }) => {
      if (typeof mode === "string") {
        modesByAgent.set(agentKey(agentId), mode);
      }
    },
    recordCall: ({ toolUseId, agentId }) => {
      if (isToolUseId(toolUseId)) {
        agentsByCall.set(toolUseId, agentKey(agentId));
      }
    },
    forgetCall: (toolUseId) => {
      agentsByCall.delete(toolUseId);
    },
    modeForCall: (toolUseId) =>
      isToolUseId(toolUseId) && agentsByCall.has(toolUseId)
        ? modesByAgent.get(agentsByCall.get(toolUseId))
        : undefined,
  };
};

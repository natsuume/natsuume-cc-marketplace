import type { On } from 'claude-code'

import { createPermissionModeTracker } from './permission-mode-tracker.mjs'
import { decideToolCheck } from './tool-check-policy.mjs'

/**
 * pre-merge-cross-review の hooks module (Claude Mods)。
 *
 * auto mode で、merge gate を通過した単独の `gh pr merge` と、単独の `gh pr view` /
 * `gh pr checks` を tool.check で allow に引き上げ、auto mode classifier の判定を経ずに実行させる。
 * 判定本体は tool-check-policy.mjs の純関数で、ここでは engine との接続だけを行う。
 *
 * tool.check の入力は permission mode も呼び出し元の agent も持たないため、次を記録して
 * 引き当てる (permission-mode-tracker.mjs)。
 * - agent ごとの permission mode: permission_mode を持つ classic イベント (SessionStart /
 *   UserPromptSubmit / PostToolUse / PostToolUseFailure) の入力の agent_id (subagent のみ) ごと
 * - 呼び出しごとの agent: tool.call の入力の tool_use_id と agentId。tool.check は同じ呼び出しの
 *   tool.call の後に評価される
 * 呼び出し元の agent に mode の記録が無い間は mode 不明として引き上げない。記録は直近の classic
 * イベント時点の mode なので、ターンの途中で mode を切り替えた直後の 1 回のツール呼び出しには、
 * 切り替え前の mode が使われる。classic イベントと tool.call は next(e) でそのまま下位へ渡し、
 * 内容を変えない。
 *
 * merge gate (block-pre-merge.sh) は classic PreToolUse として tool.check より先に評価され、
 * その deny は tool.check の下位判定 (next(e) の結果) として渡される。判定ロジックは deny を
 * 上書きしないため、gate が deny した merge は allow にならない。
 *
 * @param on the engine's registrar
 */
export const register = (on: On) => {
  const tracker = createPermissionModeTracker()

  const recordPermissionMode = (e: { permission_mode?: unknown; agent_id?: unknown }) => {
    tracker.recordMode({ agentId: e.agent_id, mode: e.permission_mode })
  }

  on('classic.SessionStart', ($, e, next) => {
    recordPermissionMode(e)
    return next(e)
  })
  on('classic.UserPromptSubmit', ($, e, next) => {
    recordPermissionMode(e)
    return next(e)
  })
  on('classic.PostToolUse', ($, e, next) => {
    recordPermissionMode(e)
    return next(e)
  })
  on('classic.PostToolUseFailure', ($, e, next) => {
    recordPermissionMode(e)
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    tracker.recordCall({ toolUseId: e.tool_use_id, agentId: e.agentId })
    try {
      return await next(e)
    } finally {
      tracker.forgetCall(e.tool_use_id)
    }
  })

  on('tool.check', async ($, e, next) =>
    decideToolCheck({
      tool: e.tool,
      input: e.input,
      beneath: await next(e),
      permissionMode: tracker.modeForCall(e.tool_use_id),
    }),
  )
}

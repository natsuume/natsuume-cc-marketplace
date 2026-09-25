import type { On } from 'claude-code'

import { decideToolCheck } from './tool-check-policy.mjs'

/**
 * pre-merge-cross-review の hooks module (Claude Mods)。
 *
 * auto mode で、merge gate を通過した単独の `gh pr merge` と、単独の `gh pr view` /
 * `gh pr checks` を tool.check で allow に引き上げ、auto mode classifier の判定を経ずに実行させる。
 * 判定本体は tool-check-policy.mjs の純関数で、ここでは engine との接続だけを行う。
 *
 * tool.check の入力は permission mode を持たないため、permission_mode を持つ classic イベント
 * (SessionStart / UserPromptSubmit / PostToolUse / PostToolUseFailure) の入力から最新の mode を
 * 記録して使う。記録が無い間は mode 不明として引き上げない。classic イベントは next(e) で
 * そのまま下位へ渡し、内容を変えない。記録は直近の classic イベント時点の mode なので、ターンの
 * 途中で mode を切り替えた直後の 1 回のツール呼び出しには、切り替え前の mode が使われる。
 *
 * merge gate (block-pre-merge.sh) は classic PreToolUse として tool.check より先に評価され、
 * その deny は tool.check の下位判定 (next(e) の結果) として渡される。判定ロジックは deny を
 * 上書きしないため、gate が deny した merge は allow にならない。
 *
 * @param on the engine's registrar
 */
export const register = (on: On) => {
  let permissionMode: string | undefined

  const recordPermissionMode = (e: { permission_mode?: unknown }) => {
    if (typeof e.permission_mode === 'string') {
      permissionMode = e.permission_mode
    }
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

  on('tool.check', async ($, e, next) =>
    decideToolCheck({ tool: e.tool, input: e.input, beneath: await next(e), permissionMode }),
  )
}

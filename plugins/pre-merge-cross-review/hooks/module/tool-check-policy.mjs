// pre-merge-cross-review hooks module の tool.check 判定ロジック (純関数)。
//
// register.ts が tool.check chain の下位判定 (next(e) の結果) と記録済みの permission mode を
// 渡し、本 module が最終判定を返す。I/O を持たないため、node から直接読み込んでテストできる。
//
// 不変条件:
// - 下位判定が `ask` の場合に限り `allow` へ引き上げる。`deny` / `allow` は変更しない
//   (merge gate の deny・permissions.deny を上書きしない)
// - 引き上げは permission mode が `auto` のときだけ行う。mode 不明 (undefined 等) は引き上げない
// - `deny` を自ら返さない

/** 対象コマンドの先頭 (`gh pr <subcommand>`)。 */
export const TARGET_SUBCOMMANDS = Object.freeze(["merge", "view", "checks"]);

/** `gh pr merge` で許可しないフラグ。 */
export const FORBIDDEN_MERGE_FLAGS = Object.freeze(["--admin", "--delete-branch", "-d"]);

/**
 * command が「単一の gh pr merge / view / checks 呼び出し」かを判定する。
 *
 * - 前後の空白を除いた command 全体が `gh pr <merge|view|checks>` で始まる単一の呼び出しであること
 * - `;` `&` `|` `<` `>` バッククォート `$(` `${` 改行を含まないこと
 * - `gh` の前に env 代入やラッパー (`env` / `bash -c` / `eval` / `xargs` 等) が無いこと
 * - `gh pr merge` の場合、FORBIDDEN_MERGE_FLAGS のいずれも (`--flag=value` 形を含め) 含まないこと
 *
 * @param {unknown} command Bash tool の input.command
 * @returns {boolean}
 */
export const isTargetCommand = (command) => {
  void command;
  return false;
};

/**
 * tool.check の最終判定を返す。
 *
 * @param {{ tool: unknown, input: unknown, beneath: { decision: string, reason?: string, rule?: string }, permissionMode: unknown }} args
 * @returns {{ decision: string, reason?: string, rule?: string }} 引き上げない場合は beneath をそのまま (同一オブジェクトで) 返す
 */
export const decideToolCheck = ({ tool, input, beneath, permissionMode }) => {
  void tool;
  void input;
  void permissionMode;
  return beneath;
};

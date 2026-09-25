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

// shell の連結・リダイレクト・置換・改行。quote の内側にあっても対象外とする (保守側に倒す)。
const SHELL_METACHARACTERS = /[;&|<>`\n\r]|\$\(|\$\{/;

const MERGE_REASON =
  "pre-merge-cross-review: auto mode で merge gate を通過した単独の gh pr merge を許可しました";
const READ_ONLY_REASON =
  "pre-merge-cross-review: auto mode で単独の gh pr view / gh pr checks を許可しました";

const isForbiddenMergeFlag = (token) =>
  FORBIDDEN_MERGE_FLAGS.some((flag) => token === flag || token.startsWith(`${flag}=`)) ||
  // `-sd` のような短フラグの束ね形に d が含まれるもの
  /^-[A-Za-z]*d[A-Za-z]*$/.test(token);

const tokenize = (command) => command.trim().split(/\s+/);

/**
 * command が「単一の gh pr merge / view / checks 呼び出し」かを判定する。
 *
 * - 前後の空白を除いた command 全体が `gh pr <merge|view|checks>` で始まる単一の呼び出しであること
 * - `;` `&` `|` `<` `>` バッククォート `$(` `${` 改行を含まないこと
 * - `gh` の前に env 代入やラッパー (`env` / `bash -c` / `eval` / `xargs` 等) が無いこと
 * - `gh pr merge` の場合、FORBIDDEN_MERGE_FLAGS のいずれも (`--flag=value` 形と、`-sd` のように
 *   `d` を含む短フラグの束ね形を含め) 含まないこと
 *
 * quote の解釈は行わない。quote の内側にある metacharacter も対象外の理由になる。
 *
 * @param {unknown} command Bash tool の input.command
 * @returns {boolean}
 */
export const isTargetCommand = (command) => {
  if (typeof command !== "string" || SHELL_METACHARACTERS.test(command)) {
    return false;
  }
  const [program, group, subcommand, ...rest] = tokenize(command);
  if (program !== "gh" || group !== "pr" || !TARGET_SUBCOMMANDS.includes(subcommand)) {
    return false;
  }
  return subcommand !== "merge" || !rest.some(isForbiddenMergeFlag);
};

const isObject = (value) => typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * tool.check の最終判定を返す。
 *
 * @param {{ tool: unknown, input: unknown, beneath: { decision: string, reason?: string, rule?: string }, permissionMode: unknown }} args
 * @returns {{ decision: string, reason?: string, rule?: string }} 引き上げない場合は beneath をそのまま (同一オブジェクトで) 返す
 */
export const decideToolCheck = ({ tool, input, beneath, permissionMode }) => {
  if (!isObject(beneath) || beneath.decision !== "ask" || permissionMode !== "auto") {
    return beneath;
  }
  if (tool !== "Bash" || !isObject(input) || !isTargetCommand(input.command)) {
    return beneath;
  }
  const subcommand = tokenize(input.command)[2];
  return { decision: "allow", reason: subcommand === "merge" ? MERGE_REASON : READ_ONLY_REASON };
};

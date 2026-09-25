// pre-merge-cross-review hooks module の tool.check 判定ロジック (純関数)。
//
// register.ts が tool.check chain の下位判定 (next(e) の結果) と記録済みの permission mode を
// 渡し、本 module が最終判定を返す。I/O を持たないため、node から直接読み込んでテストできる。
//
// 不変条件:
// - 下位判定が `ask` の場合に限り `allow` へ引き上げる。`deny` / `allow` は変更しない
//   (merge gate の deny・permissions.deny を上書きしない)
// - `rule` を持つ `ask` (ユーザが permissions.ask ルールで明示した確認) は引き上げない
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
 * command が次の正規形の単独呼び出しかを判定する。
 *
 * - `gh pr merge [<番号>] <--squash|--merge|--rebase>`: 番号は 1 以上の整数で任意、戦略フラグは
 *   ちょうど 1 つ。順序は問わない。それ以外の引数 (`--admin`・`--delete-branch`・`-R` 等) を含む
 *   形は対象外
 * - `gh pr view [<番号>|<branch>] [flags]`: flags は `--json <fields>` / `--jq <式>` / `-q <式>` /
 *   `--comments` / `-c`
 * - `gh pr checks [<番号>|<branch>] [flags]`: flags は `--json <fields>` / `--jq <式>` / `-q <式>` /
 *   `--watch` / `--interval <秒>` / `-i <秒>` / `--required` / `--fail-fast`
 *
 * 引数の規則:
 * - 値を取るフラグは `--flag value` と `--flag=value` の両方を受け付ける
 * - `<fields>` は英数字・`_`・`,` のみ、`<秒>` は整数のみ
 * - `<式>` はシングルクォートで囲んだ 1 語 (内側に `'` を含まない)、または英数字・`_`・`.` のみ
 * - `<branch>` は英数字・`.`・`_`・`/`・`-` のみで、`-` で始まらない (`:` を含む指定や URL は対象外)
 * - 位置引数は 1 つまで
 *
 * command 全体の規則:
 * - 前後の空白を除いた command が `gh` で始まること (env 代入・ラッパーを前置しない)
 * - `;` `&` `|` `<` `>` バッククォート `$(` `${` 改行を、quote の内側を含めて含まないこと
 * - シングルクォートの外に `$` `"` `\` を含まないこと (上記の引数規則で弾かれる)
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
 * beneath が `rule` を持たない (または空文字列の) `ask`、permissionMode が `auto`、tool が `Bash`、
 * input.command が isTargetCommand を満たす場合に限り `{ decision: "allow", reason }` を返す。
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

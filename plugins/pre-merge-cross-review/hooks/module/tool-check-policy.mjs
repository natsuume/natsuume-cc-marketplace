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

// shell の連結・リダイレクト・置換・改行。quote の内側にあっても対象外とする (保守側に倒す)。
const SHELL_METACHARACTERS = /[;&|<>`\n\r]|\$\(|\$\{/;

// タブと印字可能な ASCII 以外の文字 (制御文字・非 ASCII の空白・全角文字等)。
const NON_PRINTABLE_ASCII = /[^\t\x20-\x7e]/;

// jq の式で識別子として現れる env (`.env` / `$env` / `env_x` 等のフィールド・名前は除く)。
const JQ_ENV_IDENTIFIER = /(^|[^A-Za-z0-9_.$])env(?![A-Za-z0-9_])/;

const REASONS = {
  merge:
    "pre-merge-cross-review: auto mode で merge gate を通過した単独の gh pr merge を許可しました",
  view: "pre-merge-cross-review: auto mode で単独の gh pr view を許可しました",
  checks: "pre-merge-cross-review: auto mode で単独の gh pr checks を許可しました",
};

const MERGE_STRATEGY_FLAGS = ["--squash", "--merge", "--rebase"];

const PR_NUMBER = /^[1-9][0-9]*$/;
const BRANCH = /^[A-Za-z0-9._/][A-Za-z0-9._/-]*$/;
const FIELDS = /^[A-Za-z0-9_,]+$/;
const SECONDS = /^[0-9]+$/;
const JQ_EXPRESSION_FORM = /^'[^']*'$|^[A-Za-z0-9_.]+$/;

// jq の env / $ENV は環境変数を読み出せるため、$ と識別子 env を含む式は許可しない。
const JQ_EXPRESSION = {
  test: (value) =>
    JQ_EXPRESSION_FORM.test(value) && !value.includes("$") && !JQ_ENV_IDENTIFIER.test(value),
};

// サブコマンドごとの許可フラグ。値を取るフラグは値の形 (正規表現) を持つ。
const READ_ONLY_FLAGS = {
  view: {
    values: { "--json": FIELDS, "--jq": JQ_EXPRESSION, "-q": JQ_EXPRESSION },
    switches: ["--comments", "-c"],
  },
  checks: {
    values: {
      "--json": FIELDS,
      "--jq": JQ_EXPRESSION,
      "-q": JQ_EXPRESSION,
      "--interval": SECONDS,
      "-i": SECONDS,
    },
    switches: ["--watch", "--required", "--fail-fast"],
  },
};

/**
 * スペースとタブで語に分ける。シングルクォートの内側の空白は語を区切らず、quote は語に残す。
 * 閉じていないシングルクォートがある場合は null を返す。
 */
const splitWords = (command) => {
  const words = [];
  let current = "";
  let inWord = false;
  let inQuote = false;
  for (const character of command) {
    if (inQuote) {
      current += character;
      inQuote = character !== "'";
      continue;
    }
    if (character === " " || character === "\t") {
      if (inWord) {
        words.push(current);
        current = "";
        inWord = false;
      }
      continue;
    }
    current += character;
    inWord = true;
    inQuote = character === "'";
  }
  if (inQuote) {
    return null;
  }
  if (inWord) {
    words.push(current);
  }
  return words;
};

const isCanonicalMerge = (args) => {
  const strategies = args.filter((arg) => MERGE_STRATEGY_FLAGS.includes(arg));
  const positionals = args.filter((arg) => !MERGE_STRATEGY_FLAGS.includes(arg));
  return (
    strategies.length === 1 &&
    positionals.length <= 1 &&
    positionals.every((arg) => PR_NUMBER.test(arg))
  );
};

const isCanonicalReadOnly = (subcommand, args) => {
  const { values, switches } = READ_ONLY_FLAGS[subcommand];
  let positionals = 0;
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (!arg.startsWith("-")) {
      positionals += 1;
      if (positionals > 1 || !(PR_NUMBER.test(arg) || BRANCH.test(arg))) {
        return false;
      }
      continue;
    }
    // `=` で値を渡す形は長フラグ (`--`) に限る
    const separator = arg.startsWith("--") ? arg.indexOf("=") : -1;
    const name = separator === -1 ? arg : arg.slice(0, separator);
    const valuePattern = Object.hasOwn(values, name) ? values[name] : undefined;
    if (valuePattern !== undefined) {
      const value = separator === -1 ? args[(index += 1)] : arg.slice(separator + 1);
      if (value === undefined || !valuePattern.test(value)) {
        return false;
      }
      continue;
    }
    if (separator !== -1 || !switches.includes(arg)) {
      return false;
    }
  }
  return true;
};

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
 * - `<式>` はシングルクォートで囲んだ 1 語 (内側に `'` を含まない)、または英数字・`_`・`.` のみ。
 *   いずれの形でも `$` と、識別子としての `env` (`.env` のようなフィールド参照は除く) を含まない
 *   (jq の `env` / `$ENV` は環境変数を読み出せるため)
 * - 値付きフラグの `=` 形は長フラグ (`--`) のみ。短フラグは空白区切りのみ (`-q=.x` / `-i5` は対象外)。
 *   真偽値フラグ・merge の戦略フラグに `=` は付けない。同じ戦略フラグの重複は対象外
 * - `<branch>` は英数字・`.`・`_`・`/`・`-` のみで、`-` で始まらない (`:` を含む指定や URL は対象外)
 * - 位置引数は 1 つまで
 *
 * command 全体の規則:
 * - タブと印字可能な ASCII (0x20〜0x7E) 以外の文字を含まないこと。語の区切りはスペースとタブのみ
 * - 前後の空白を除いた command が `gh` で始まること (env 代入・ラッパーを前置しない)
 * - `;` `&` `|` `<` `>` バッククォート `$(` `${` 改行を、quote の内側を含めて含まないこと
 * - シングルクォートの外に `$` `"` `\` を含まないこと (上記の引数規則で弾かれる)
 *
 * @param {unknown} command Bash tool の input.command
 * @returns {boolean}
 */
export const isTargetCommand = (command) => {
  if (
    typeof command !== "string" ||
    NON_PRINTABLE_ASCII.test(command) ||
    SHELL_METACHARACTERS.test(command)
  ) {
    return false;
  }
  const words = splitWords(command.trim());
  if (words === null) {
    return false;
  }
  const [program, group, subcommand, ...args] = words;
  if (program !== "gh" || group !== "pr") {
    return false;
  }
  if (subcommand === "merge") {
    return isCanonicalMerge(args);
  }
  if (subcommand === "view" || subcommand === "checks") {
    return isCanonicalReadOnly(subcommand, args);
  }
  return false;
};

const isObject = (value) => typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * tool.check の最終判定を返す。
 *
 * beneath が `rule` を持たない (値が undefined または空文字列の) `ask`、permissionMode が `auto`、
 * tool が `Bash`、input.command が isTargetCommand を満たす場合に限り `{ decision: "allow", reason }` を
 * 返す。rule がそれ以外の値 (null・空白のみの文字列・object 等) の ask は引き上げない。reason は
 * 許可したコマンド名 (gh pr merge / gh pr view / gh pr checks) を含む日本語の文である。
 *
 * 制約:
 * - rule を持たない ask の由来 (core の既定・PreToolUse hook の ask・core の安全検査) は区別できず、
 *   いずれも引き上げの対象になる
 * - 検査するのは engine が tool.check に渡した input であり、PreToolUse hook の updatedInput との
 *   前後関係はこの関数では保証しない
 * - 対象リポジトリは Bash の作業ディレクトリの git remote で決まる。merge の安全性は merge gate に委ねる
 *
 * @param {{ tool: unknown, input: unknown, beneath: { decision: string, reason?: string, rule?: string }, permissionMode: unknown }} args
 * @returns {{ decision: string, reason?: string, rule?: string }} 引き上げない場合は beneath をそのまま (同一オブジェクトで) 返す
 */
export const decideToolCheck = ({ tool, input, beneath, permissionMode }) => {
  if (!isObject(beneath) || beneath.decision !== "ask" || permissionMode !== "auto") {
    return beneath;
  }
  // permissions.ask ルールでユーザが明示した確認は残す。rule が想定外の値の場合も引き上げない
  if (beneath.rule !== undefined && beneath.rule !== "") {
    return beneath;
  }
  if (tool !== "Bash" || !isObject(input) || !isTargetCommand(input.command)) {
    return beneath;
  }
  const subcommand = splitWords(input.command.trim())[2];
  return { decision: "allow", reason: REASONS[subcommand] };
};

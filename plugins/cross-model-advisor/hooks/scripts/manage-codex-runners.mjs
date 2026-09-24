#!/usr/bin/env node

/**
 * Codex runner の直接実行 gate と lifecycle state machine。
 *
 * Claude Code hook の JSON を stdin から受け取り、次を一つの session-scoped state で
 * 接続する。
 *
 * - PreToolUse: Codex model の起動を role 固有 runner だけに限定する
 * - PermissionDenied: classifier が拒否した runner 起動を record に反映する (起動を要求して
 *   いる phase は `denied` へ、`active` は `launchDenied` の印だけを付けて Stop に委ねる)
 * - SubagentStart / SubagentStop: runner の active / retry / terminal 遷移を記録する
 * - PostToolUse (SubagentHandback): hand-back された report の footer / attestation
 *   解析値を runner state に記録する (SubagentStop が one-shot で消費する)
 * - SubagentStop: codex-advisor-runner の report が review cadence attestation footer 行
 *   (`Codex-Advisor-Review-Cadence`) を欠く場合、report 契約違反として retry させる
 *
 * report の所在: Claude Code v2.1.271 以降の auto mode では、runner の最終 report は
 * `SubagentHandback` tool の `message` 引数として親へ届き、SubagentStop の
 * `last_assistant_message` には hand-back 後の締めの文しか入らない。そのため PostToolUse
 * (`tool_name` が `SubagentHandback`) で `tool_input.message` の footer / attestation を
 * 解析して state の `handback` に記録し、SubagentStop は記録があればそれを採用する
 * (`last_assistant_message` は見ない)。記録が無い場合 (SubagentHandback が提供されない
 * 非 auto mode 等) に限り `last_assistant_message` を report として解析する。同一 runner
 * の 2 回目以降の hand-back は重複 report として解析値を null にする (fail-closed)。
 * `tool_response.success` が false の hand-back (未配信: tool が active でない / 配信済み /
 * 親が受理しない等) は report ではないため記録しない (harness は未配信時に plain text での
 * 報告へ切り替えるため、`last_assistant_message` 経路で解析できる)。
 * - Stop: `background_tasks` と record を突き合わせ、稼働中の runner には待機を通知し、
 *   追跡を失った runner がある間だけ main session の終了を block する
 * - SessionStart / SessionEnd: stale runner state を掃除する
 *
 * codex-advisor-runner の attestation footer は外部 plugin (pre-push-codex-review) の review
 * cadence enforcement が消費する。
 *
 * state に prompt や Codex 出力は保存しない (hand-back された report も解析値だけを
 * 保存し本文は保存しない)。既定では UID ごとの /tmp 配下、テストでは
 * CODEX_ADVISOR_STATE_ROOT で差し替えた directory に、session + operation ごとの JSON を
 * temp file + rename で atomic に保存する。
 */

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const RUNNERS = Object.freeze({
  rescue: "cross-model-advisor:codex-rescue-runner",
  review: "cross-model-advisor:codex-review-runner",
  advisor: "cross-model-advisor:codex-advisor-runner",
});
const LEGACY_RESCUE = "codex:codex-rescue";
const RETRY_LIMIT = 1;
const REVIEW_CADENCE_ATTESTATIONS = new Set([
  "satisfied",
  "unavailable",
  "not-applicable",
]);
const VALID_STATUSES = new Set([
  "success",
  "retryable-failure",
  "terminal-failure",
  "cancelled",
]);
// 重複 hand-back (同一 runner の 2 回目以降) を表す解析値。footer が無いので
// SubagentStop では contract 違反として扱われる (fail-closed)。
const INVALID_HANDBACK = Object.freeze({ footer: null, attestation: null });

function stateRoot() {
  const overridden = process.env.CODEX_ADVISOR_STATE_ROOT;
  if (overridden) return overridden;
  const uid = typeof process.getuid === "function" ? process.getuid() : "unknown";
  return path.join(os.tmpdir(), `cross-model-advisor-${uid}`, "runner-state");
}

function keyFor(sessionId, operation) {
  return crypto
    .createHash("sha256")
    .update(`${sessionId}\0${operation}`)
    .digest("hex");
}

function recordPath(sessionId, operation) {
  return path.join(stateRoot(), `${keyFor(sessionId, operation)}.json`);
}

function ensureRoot() {
  fs.mkdirSync(stateRoot(), { recursive: true, mode: 0o700 });
  try {
    fs.chmodSync(stateRoot(), 0o700);
  } catch {
    // Existing directory permissions can be immutable on unusual filesystems.
  }
}

function readRecord(sessionId, operation) {
  try {
    const parsed = JSON.parse(
      fs.readFileSync(recordPath(sessionId, operation), "utf8"),
    );
    if (parsed.sessionId !== sessionId || parsed.operation !== operation) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function listRecords(sessionId) {
  let entries;
  try {
    entries = fs.readdirSync(stateRoot());
  } catch {
    return [];
  }
  const records = [];
  for (const entry of entries) {
    if (!entry.endsWith(".json")) continue;
    try {
      const parsed = JSON.parse(
        fs.readFileSync(path.join(stateRoot(), entry), "utf8"),
      );
      if (parsed.sessionId === sessionId && RUNNERS[parsed.operation]) {
        records.push(parsed);
      }
    } catch {
      // A partial or foreign file never becomes authority for a Stop block.
    }
  }
  return records.sort((left, right) =>
    String(left.operation).localeCompare(String(right.operation)),
  );
}

function writeRecord(record) {
  ensureRoot();
  const destination = recordPath(record.sessionId, record.operation);
  const temporary = `${destination}.${process.pid}.${crypto
    .randomBytes(6)
    .toString("hex")}.tmp`;
  const normalized = {
    sessionId: record.sessionId,
    operation: record.operation,
    runnerType: RUNNERS[record.operation],
    agentId: record.agentId ?? null,
    phase: record.phase,
    retryCount: Number.isInteger(record.retryCount) ? record.retryCount : 0,
    jobId: record.jobId ?? null,
    handback: sanitizeHandback(record.handback),
    updatedAt: new Date().toISOString(),
  };
  // 印は record インスタンスに属する。呼び出し側が明示的に true を渡したときだけ書き、
  // lifecycle の遷移 (SubagentStart / SubagentStop / PostToolUse) では引き継がない。
  if (record.launchDenied === true) normalized.launchDenied = true;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(normalized)}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    fs.renameSync(temporary, destination);
  } catch (error) {
    try {
      fs.unlinkSync(temporary);
    } catch {
      // Nothing to clean up.
    }
    throw error;
  }
  return normalized;
}

function sanitizeFooter(value) {
  if (
    !value ||
    typeof value !== "object" ||
    typeof value.operation !== "string" ||
    typeof value.status !== "string" ||
    typeof value.jobId !== "string"
  ) {
    return null;
  }
  return { operation: value.operation, status: value.status, jobId: value.jobId };
}

/**
 * runner state の `handback` (PostToolUse で記録した hand-back report の解析値)。
 * 未記録なら null、記録済みなら { footer, attestation } (それぞれ null になりうる)。
 */
function sanitizeHandback(value) {
  if (!value || typeof value !== "object") return null;
  return {
    footer: sanitizeFooter(value.footer),
    attestation: REVIEW_CADENCE_ATTESTATIONS.has(value.attestation)
      ? value.attestation
      : null,
  };
}

function removeRecord(sessionId, operation) {
  try {
    fs.unlinkSync(recordPath(sessionId, operation));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

function clearRunnerState(sessionId) {
  for (const record of listRecords(sessionId)) {
    try {
      removeRecord(sessionId, record.operation);
    } catch {
      // Cleanup is best-effort; a later SessionStart / SessionEnd can try again.
    }
  }
}

function operationForAgentType(agentType) {
  return Object.entries(RUNNERS).find(([, value]) => value === agentType)?.[0] ?? null;
}

function hasTopLevelBackgroundOrPipeline(command) {
  let quote = null;
  let escaped = false;
  for (let index = 0; index < command.length; index += 1) {
    const character = command[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = null;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    if (character === "|" || character === "&") return true;
  }
  return false;
}

/**
 * `$(...)` の閉じ括弧位置を返す (見つからなければ -1)。入れ子の括弧と quote を数える。
 */
function matchingParenIndex(text, openIndex) {
  let depth = 0;
  let quote = null;
  let escaped = false;
  for (let index = openIndex; index < text.length; index += 1) {
    const character = text[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = null;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    if (character === "(") {
      depth += 1;
      continue;
    }
    if (character === ")") {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  return -1;
}

// command substitution の再帰解析の深さ上限。実用上の入れ子はこの範囲に収まり、
// 病的に深い入力でも解析が停止する。
const SUBSTITUTION_DEPTH_LIMIT = 8;

// 外側の segment で substitution が占めていた位置に残す placeholder。置換結果は hook から
// 解決できないため、command 名の位置にあれば「変数のまま解決できない command 名」と
// 同じ扱い (fail-closed の判定対象) になる。
const SUBSTITUTION_PLACEHOLDER = "$SUBSTITUTION";

/**
 * shell の list separator で segment に分割し、`$(...)` とバッククォートの中身も
 * 独立した segment として再帰的に取り出す。substitution の中身は外側の segment から
 * 取り除いて placeholder に置き換えるため、置換結果を実行形として誤読せず、置換で
 * 作られた command 名を解決済みとも扱わない。
 *
 * heredoc (`<<` / `<<-`) の本文は行末の改行の後から終端行までを切り出し、独立した
 * コマンド列として解析する。本文を shell が実行するかどうかはここでは判定しない (本文を
 * データとして読むと構文上確定できる command は isQuotedHeredocDataCommand が別に扱う)。
 * 本文を切り出すことで、本文中の quote が終端行より後ろの行の解析に及ばない。
 */
function collectShellSegments(command, segments, depth) {
  let current = "";
  let quote = null;
  let escaped = false;
  // 現在の行で開いた heredoc。本文は行末の改行の直後から始まる。
  let lineHeredocs = [];
  const flush = () => {
    if (current.trim()) segments.push(current.trim());
    current = "";
  };
  for (let index = 0; index < command.length; index += 1) {
    const character = command[index];
    if (escaped) {
      current += character;
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      current += character;
      escaped = true;
      continue;
    }
    if (quote === "'") {
      current += character;
      if (character === quote) quote = null;
      continue;
    }
    if (depth < SUBSTITUTION_DEPTH_LIMIT) {
      if (character === "$" && command[index + 1] === "(") {
        const end = matchingParenIndex(command, index + 1);
        if (end !== -1) {
          collectShellSegments(command.slice(index + 2, end), segments, depth + 1);
          current += SUBSTITUTION_PLACEHOLDER;
          index = end;
          continue;
        }
      }
      if (character === "`") {
        const end = command.indexOf("`", index + 1);
        if (end !== -1) {
          collectShellSegments(command.slice(index + 1, end), segments, depth + 1);
          current += SUBSTITUTION_PLACEHOLDER;
          index = end;
          continue;
        }
      }
    }
    if (quote) {
      current += character;
      if (character === quote) quote = null;
      continue;
    }
    if (character === "'" || character === '"') {
      current += character;
      quote = character;
      continue;
    }
    if (character === "<" && command[index + 1] === "<") {
      // here-string (`<<<`) は heredoc ではない。
      if (command[index + 2] === "<") {
        current += "<<<";
        index += 2;
        continue;
      }
      const heredoc =
        depth < SUBSTITUTION_DEPTH_LIMIT ? parseHeredocOperator(command, index) : null;
      if (heredoc) {
        current += command.slice(index, heredoc.end);
        index = heredoc.end - 1;
        lineHeredocs.push(heredoc);
        continue;
      }
    }
    if (character === "\n") {
      flush();
      if (lineHeredocs.length > 0) {
        index = consumeHeredocBodies(command, index + 1, lineHeredocs, segments, depth) - 1;
        lineHeredocs = [];
      }
      continue;
    }
    if (character === ";" || character === "|" || character === "&") {
      flush();
      continue;
    }
    current += character;
  }
  flush();
}

// 区切り語の終わりを示す文字 (空白以外)。
const HEREDOC_DELIMITER_TERMINATORS = ";|&<>()";

/**
 * `<<` / `<<-` の直後から区切り語 (quote と backslash を除いた文字列) を読む。区切り語を
 * 読めなければ null を返す。
 */
function parseHeredocOperator(command, operatorIndex) {
  let cursor = operatorIndex + 2;
  let stripTabs = false;
  if (command[cursor] === "-") {
    stripTabs = true;
    cursor += 1;
  }
  while (command[cursor] === " " || command[cursor] === "\t") cursor += 1;
  let delimiter = "";
  let quote = null;
  for (; cursor < command.length; cursor += 1) {
    const character = command[cursor];
    if (quote) {
      if (character === quote) quote = null;
      else delimiter += character;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    if (character === "\\") {
      cursor += 1;
      if (cursor < command.length) delimiter += command[cursor];
      continue;
    }
    if (/\s/.test(character) || HEREDOC_DELIMITER_TERMINATORS.includes(character)) break;
    delimiter += character;
  }
  if (quote || delimiter === "") return null;
  return { delimiter, stripTabs, end: cursor };
}

/**
 * `start` から heredoc の本文を読み、本文と終端行の次の位置を返す。終端行が無ければ null。
 */
function readHeredocBody(command, start, heredoc) {
  let lineStart = start;
  for (;;) {
    const newline = command.indexOf("\n", lineStart);
    const lineEnd = newline === -1 ? command.length : newline;
    let line = command.slice(lineStart, lineEnd);
    if (heredoc.stripTabs) line = line.replace(/^\t+/, "");
    if (line === heredoc.delimiter) {
      return {
        body: command.slice(start, lineStart),
        next: newline === -1 ? command.length : newline + 1,
      };
    }
    if (newline === -1) return null;
    lineStart = newline + 1;
  }
}

/**
 * 改行の直後 (`start`) から、その行で開いた heredoc の本文を順に切り出してコマンド列として
 * 解析し、読み終えた位置を返す。どれか 1 つでも終端行が見つからなければ heredoc として扱わず
 * `start` を返し、以降の行をそのまま解析させる (算術式の `<<` を誤認した場合もここで戻る)。
 */
function consumeHeredocBodies(command, start, heredocs, segments, depth) {
  const bodies = [];
  let position = start;
  for (const heredoc of heredocs) {
    const read = readHeredocBody(command, position, heredoc);
    if (!read) return start;
    bodies.push(read.body);
    position = read.next;
  }
  for (const body of bodies) collectShellSegments(body, segments, depth + 1);
  return position;
}

// heredoc 本文をデータとして読む (shell として実行しない) と構文上確定できる command。
// shell 以外の言語の interpreter は本文をその言語で実行するが、`python3 -c` 等と同じく
// shell の実行形ではないため gate の分類対象外である。
const HEREDOC_DATA_COMMANDS = new Set(["cat", "tee", "python", "python3", "node"]);
// 免除する command 行の引数として許す token (展開・リダイレクト・制御演算子を含まない語)。
const HEREDOC_DATA_ARGUMENT = /^(?:[A-Za-z0-9_./:=,@%+-]+|'[^']*'|"[^"$`\\]*")$/;
// 免除する heredoc 演算子 (区切り語を quote した形のみ)。
const HEREDOC_DATA_OPERATOR = /^<<(-?)(?:'([A-Za-z0-9_]+)'|"([A-Za-z0-9_]+)")$/;

/**
 * command 全体が「本文をデータとして読む command 1 つ + 区切り語を quote した heredoc 1 つ」
 * だけで構成されるかを判定する。1 行目は HEREDOC_DATA_COMMANDS の command と、展開・制御
 * 演算子を含まない引数、heredoc 演算子 1 つ、ファイルへのリダイレクト (`>` / `>>`) 1 つまで。
 * 2 行目以降は本文で、最終行 (末尾の改行 1 つは除く) が終端行であり、それより前に終端行が
 * 無いこと。この形の本文は literal なデータで、shell も他の command も実行しない。少しでも
 * 外れる command は免除せず、本文を含めて通常どおり分類する (fail-closed)。
 */
function isQuotedHeredocDataCommand(command) {
  const newline = command.indexOf("\n");
  if (newline === -1) return false;
  const tokens = command.slice(0, newline).trim().split(/[ \t]+/);
  if (!HEREDOC_DATA_COMMANDS.has(tokens[0])) return false;
  let heredoc = null;
  let redirected = false;
  for (let index = 1; index < tokens.length; index += 1) {
    const token = tokens[index];
    const operator = HEREDOC_DATA_OPERATOR.exec(token);
    if (operator) {
      if (heredoc) return false;
      heredoc = { stripTabs: operator[1] === "-", delimiter: operator[2] ?? operator[3] };
      continue;
    }
    if (token === ">" || token === ">>") {
      const target = tokens[index + 1];
      if (redirected || !target || !HEREDOC_DATA_ARGUMENT.test(target)) return false;
      redirected = true;
      index += 1;
      continue;
    }
    if (!HEREDOC_DATA_ARGUMENT.test(token)) return false;
  }
  if (!heredoc) return false;
  const lines = command.slice(newline + 1).split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  const isTerminator = (line) =>
    (heredoc.stripTabs ? line.replace(/^\t+/, "") : line) === heredoc.delimiter;
  return (
    isTerminator(lines[lines.length - 1]) &&
    !lines.slice(0, -1).some((line) => isTerminator(line))
  );
}

function shellSegments(command) {
  const segments = [];
  collectShellSegments(command, segments, 0);
  return segments;
}

function shellWords(segment) {
  const words = [];
  let current = "";
  let quote = null;
  let escaped = false;
  const flush = () => {
    if (current) words.push(current);
    current = "";
  };
  for (const character of segment) {
    if (escaped) {
      current += character;
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = null;
      else current += character;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    if (/\s/.test(character)) {
      flush();
      continue;
    }
    if (character === "<" || character === ">") {
      flush();
      words.push(character);
      continue;
    }
    current += character;
  }
  flush();
  return words;
}

function basename(word) {
  return word.split("/").pop() ?? word;
}

// Codex を起動しうる実行形の path 断片。解析不能な command 名と同居する場合だけ
// fail-closed の判定材料になる。
const CODEX_ENTRYPOINT_FRAGMENTS = [
  "codex-companion.mjs",
  "run-codex-job.sh",
  "run-codex-advisor.sh",
];

// 後続の argv をそのまま実行する launcher。実行形の判定は剥がした後の先頭語で行う。
const COMMAND_WRAPPERS = [
  "command",
  "builtin",
  "nohup",
  "sudo",
  "setsid",
  "ionice",
  "stdbuf",
  "watch",
  "xargs",
];
// wrapper の剥がしを繰り返す回数の上限 (`sudo setsid node ...` のような多重包装を解き、
// 解けない入力でも停止する)。
const WRAPPER_STRIP_LIMIT = 8;

// wrapper の後に来る「実行形の先頭語」として認識する basename。wrapper の option は値を
// 別 token に取ることがある (`xargs -n 1` / `sudo -u user` 等) ため、option の arity を
// 個別に持たず、「option の直後にあり、かつ実行形の先頭語に見えない token」を option の値と
// して読み飛ばす。option に続かない token は実行形として扱う (`sudo -u user cat <file>` の
// `cat` を飛ばして後続の path を実行形と誤認しない)。
const EXECUTABLE_HEADS = new Set([
  "node",
  "bash",
  "sh",
  "env",
  "timeout",
  "eval",
  ...COMMAND_WRAPPERS,
]);

function looksLikeExecutableHead(word) {
  const name = basename(word);
  return (
    name.includes("$") ||
    EXECUTABLE_HEADS.has(name) ||
    CODEX_ENTRYPOINT_FRAGMENTS.includes(name)
  );
}

function commandWords(segment) {
  let words = shellWords(segment);
  for (let round = 0; round < WRAPPER_STRIP_LIMIT; round += 1) {
    while (["then", "do", "else"].includes(words[0])) words.shift();
    while (/^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0] ?? "")) words.shift();

    const head = basename(words[0] ?? "");
    if (COMMAND_WRAPPERS.includes(head)) {
      words.shift();
      let previousWasOption = false;
      while (words.length > 0) {
        if (words[0].startsWith("-")) {
          previousWasOption = true;
          words.shift();
          continue;
        }
        if (previousWasOption && !looksLikeExecutableHead(words[0])) {
          previousWasOption = false;
          words.shift();
          continue;
        }
        break;
      }
      continue;
    }
    if (head === "env") {
      words.shift();
      while (
        (words[0] ?? "").startsWith("-") ||
        /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0] ?? "")
      ) {
        words.shift();
      }
      continue;
    }
    if (head === "timeout") {
      words.shift();
      while ((words[0] ?? "").startsWith("-")) words.shift();
      if (/^[0-9]+(?:\.[0-9]+)?[smhd]?$/.test(words[0] ?? "")) words.shift();
      continue;
    }
    if (head === "eval") {
      // eval は引数を連結して再度 shell として解釈するため、quote を外した残りを
      // 1 つの command 文字列として再解析する。
      words.shift();
      words = shellWords(words.join(" "));
      continue;
    }
    break;
  }
  return words;
}

/**
 * 実行形だけを分類する。shell segment の executable 位置を要求することで、rg/grep/cat/
 * git diff の引数や quoted search text に現れただけの文字列を model 起動と誤認しない。
 */
function classifyModelLaunch(command) {
  for (const segment of shellSegments(command)) {
    const words = commandWords(segment);
    if (basename(words[0] ?? "") === "node") {
      let scriptIndex = 1;
      while ((words[scriptIndex] ?? "").startsWith("-")) scriptIndex += 1;
      if (basename(words[scriptIndex] ?? "") === "codex-companion.mjs") {
        const action = words[scriptIndex + 1];
        if (["task", "review", "adversarial-review"].includes(action)) {
          return {
            operation: action === "task" ? "rescue" : "review",
            entrypoint: `codex-companion.mjs ${action}`,
          };
        }
      }
    }

    let scriptIndex = 0;
    if (["bash", "sh"].includes(basename(words[0] ?? ""))) {
      scriptIndex = 1;
      while ((words[scriptIndex] ?? "").startsWith("-")) scriptIndex += 1;
    }
    const script = basename(words[scriptIndex] ?? "");
    if (script === "run-codex-advisor.sh") {
      return { operation: "advisor", entrypoint: "run-codex-advisor.sh" };
    }
    if (script === "run-codex-job.sh") {
      const action = words[scriptIndex + 1];
      if (["rescue", "review", "advisor"].includes(action)) {
        return {
          operation: action,
          entrypoint: `run-codex-job.sh ${action}`,
        };
      }
    }
  }
  return null;
}

/**
 * command 名が変数のまま解決できない segment に Codex entrypoint の path があるかを調べる。
 *
 * `$CMD /path/to/codex-companion.mjs task` のような形は、展開結果が何であるかを hook から
 * 判定できない。分類できないまま allow すると gate を素通りできてしまうため、この組み合わせ
 * だけを fail-closed で deny する。command 名が解決できる通常のコマンド (`cat` / `rg` 等) に
 * よる path 言及は従来どおり allow する。
 *
 * 「解決できない」は basename で判定する。`${CLAUDE_PLUGIN_ROOT}/scripts/run-codex-job.sh`
 * のように directory だけが変数で file 名が literal な先頭語は分類器が解決できるため、
 * `status` / `result` / `cancel` のような管理 subcommand をここで拒否しない。
 */
function unresolvableCodexEntrypoint(command) {
  for (const segment of shellSegments(command)) {
    const words = commandWords(segment);
    const head = basename(words[0] ?? "");
    if (!head.includes("$")) continue;
    const fragment = CODEX_ENTRYPOINT_FRAGMENTS.find((candidate) =>
      words.some((word) => basename(word) === candidate),
    );
    if (fragment) return fragment;
  }
  return null;
}

function denyResponse(reason) {
  return {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
  };
}

function handlePreToolUse(input) {
  if (input.tool_name !== "Bash") return null;
  const command = input.tool_input?.command;
  if (typeof command !== "string") return null;
  if (isQuotedHeredocDataCommand(command)) return null;
  const launch = classifyModelLaunch(command);
  if (!launch) {
    const fragment = unresolvableCodexEntrypoint(command);
    if (!fragment) return null;
    return denyResponse(
      `cross-model-advisor: command 名が変数のままで解決できない segment に ${fragment} の path があります。Codex 起動かどうかを判定できないため拒否しました。Codex を起動する場合は Agent tool で subagent_type=\"${RUNNERS.rescue}\" / \"${RUNNERS.review}\" / \"${RUNNERS.advisor}\" のいずれかを model=\"sonnet\" で起動してください。読み取り・管理操作の場合は command 名を literal で書き直してください。`,
    );
  }

  const expectedRunner = RUNNERS[launch.operation];
  const agentType = typeof input.agent_type === "string" ? input.agent_type : "";
  const requestedBackground = input.tool_input?.run_in_background === true;
  const unsafeShellShape = hasTopLevelBackgroundOrPipeline(command);
  const allowed =
    agentType === expectedRunner && !requestedBackground && !unsafeShellShape;
  if (allowed) return null;

  let stateFailure = "";
  try {
    const previous = readRecord(input.session_id, launch.operation);
    writeRecord({
      sessionId: input.session_id,
      operation: launch.operation,
      agentId: previous?.agentId ?? null,
      phase: "reroute-required",
      retryCount: previous?.retryCount ?? 0,
      jobId: previous?.jobId ?? null,
      handback: previous?.handback ?? null,
    });
  } catch (error) {
    stateFailure = ` state の永続化に失敗しました (${error?.code ?? "unknown"})。deny は有効ですが、自動復旧要求を保存できていません。`;
  }

  let mismatch = `main session または誤った role (${agentType || "agent_type missing"})`;
  if (agentType === LEGACY_RESCUE) mismatch = `legacy agent ${LEGACY_RESCUE}`;
  if (requestedBackground || unsafeShellShape) {
    mismatch = "background / pipeline を含む起動";
  }
  return denyResponse(
    `cross-model-advisor: ${launch.entrypoint} は ${expectedRunner} だけが実行できます。${mismatch} からの直接実行を拒否しました。main session は Agent tool で subagent_type=\"${expectedRunner}\", model=\"sonnet\" を指定して起動してください。起動 mode は Claude Code が決めるため指定せず、runner の report は completion notification で後続 turn に届きます。${stateFailure}`,
  );
}

/**
 * PermissionDenied (Agent / Task): classifier が runner の起動を拒否した。
 *
 * 拒否は spawn の前に起きるため、起動を要求している phase (`retry-required` /
 * `reroute-required`) の record はその要求が満たされないまま残る。これを `denied` へ
 * 移し、Stop が同じ起動を要求し続ける loop を断つ。`active` の record が稼働中 runner の
 * ものか、runner が SubagentStop 無しに消えた残骸かはこの入力では判別できないので、
 * phase は変えずに `launchDenied` の印だけを付け、判別を Stop hook へ委ねる。
 */
function handlePermissionDenied(input) {
  if (input.tool_name !== "Agent" && input.tool_name !== "Task") return null;
  const operation = operationForAgentType(input.tool_input?.subagent_type);
  if (!operation || typeof input.session_id !== "string") return null;
  const current = readRecord(input.session_id, operation);
  if (!current) return null;

  if (current.phase === "retry-required" || current.phase === "reroute-required") {
    writeRecord({
      sessionId: input.session_id,
      operation,
      agentId: current.agentId ?? null,
      phase: "denied",
      retryCount: current.retryCount ?? 0,
      jobId: current.jobId ?? null,
      handback: current.handback ?? null,
    });
    return null;
  }
  if (current.phase === "active") {
    writeRecord({
      sessionId: input.session_id,
      operation,
      agentId: current.agentId ?? null,
      phase: "active",
      retryCount: current.retryCount ?? 0,
      jobId: current.jobId ?? null,
      handback: current.handback ?? null,
      launchDenied: true,
    });
  }
  return null;
}

function handleSubagentStart(input) {
  const operation = operationForAgentType(input.agent_type);
  if (!operation || typeof input.session_id !== "string") return null;
  const previous = readRecord(input.session_id, operation);
  writeRecord({
    sessionId: input.session_id,
    operation,
    agentId: typeof input.agent_id === "string" ? input.agent_id : null,
    phase: "active",
    retryCount: previous?.retryCount ?? 0,
    jobId: previous?.jobId ?? null,
  });
  return null;
}

/**
 * footer / attestation 解析が共有する「実質末尾行」抽出 (issue #348)。
 *
 * runner が footer をコードフェンス (```/~~~) で囲んだり、footer 行間に空白行を挟んだり
 * しても誤って retry-required にならないよう、message を行分割し、末尾から走査して
 * 空白行 (/^\s*$/) とコードフェンス行 (先頭空白を除去した後に ``` または ~~~ で始まる行)
 * をスキップしながら実質行を count 行、元の並び順で収集して返す。count 行に満たない
 * 場合は null。行の内容自体は変更しない (先頭空白の除去もしない — footer 行自体の
 * 先頭空白は従来どおり照合失敗になる)。
 */
function significantTailLines(message, count) {
  const lines = message.split(/\r?\n/);
  const collected = [];
  for (
    let index = lines.length - 1;
    index >= 0 && collected.length < count;
    index -= 1
  ) {
    const line = lines[index];
    if (/^\s*$/.test(line)) continue;
    if (/^\s*(?:```|~~~)/.test(line)) continue;
    collected.push(line);
  }
  if (collected.length < count) return null;
  collected.reverse();
  return collected;
}

function parseRunnerFooter(message) {
  if (typeof message !== "string") return null;
  // 末尾のフェンス・空白行を無視した実質末尾 3 行を footer とみなす (issue #348)。
  const footer = significantTailLines(message, 3);
  if (!footer) return null;
  const labels = [
    "Codex-Runner-Operation",
    "Codex-Runner-Status",
    "Codex-Runner-Job-ID",
  ];
  const values = footer.map((line, index) => {
    const prefix = `${labels[index]}: `;
    return line.startsWith(prefix) ? line.slice(prefix.length).trim() : null;
  });
  if (values.some((value) => !value)) return null;
  return { operation: values[0], status: values[1], jobId: values[2] };
}

function parseReviewCadenceAttestation(message) {
  if (typeof message !== "string") return null;
  // footer 3 行 + その直前の実質行 (attestation) の計 4 行を、フェンス・空白行を
  // 無視した実質末尾から取り出す (issue #348)。先頭要素が attestation 行になる。
  const lines = significantTailLines(message, 4);
  if (!lines) return null;
  const prefix = "Codex-Advisor-Review-Cadence: ";
  const line = lines[0];
  if (!line?.startsWith(prefix)) return null;
  const value = line.slice(prefix.length).trim();
  return REVIEW_CADENCE_ATTESTATIONS.has(value) ? value : null;
}

/**
 * PostToolUse (SubagentHandback): hand-back された report の footer / attestation 解析値を
 * runner state に記録する。record が無い (SubagentStart を経ていない) 場合も、hand-back は
 * runner が動作中である事実なので active record として作る (直後の SubagentStop が
 * 消費する)。
 */
function handbackUndelivered(toolResponse) {
  let response = toolResponse;
  if (typeof response === "string") {
    try {
      response = JSON.parse(response);
    } catch {
      return false;
    }
  }
  return Boolean(response) && typeof response === "object" && response.success === false;
}

function handlePostToolUse(input) {
  if (input.tool_name !== "SubagentHandback") return null;
  // 未配信 (success === false) の hand-back は report ではないため記録しない。success が
  // 無い / boolean でない場合は配信済みとみなす (schema 変更での fail-closed 化を避ける)。
  if (handbackUndelivered(input.tool_response)) return null;
  const operation = operationForAgentType(input.agent_type);
  if (!operation || typeof input.session_id !== "string") return null;
  const current = readRecord(input.session_id, operation);
  if (
    current?.agentId &&
    typeof input.agent_id === "string" &&
    current.agentId !== input.agent_id
  ) {
    return null;
  }
  const message = input.tool_input?.message;
  // 同一 runner の 2 回目以降の hand-back は重複 report として無効化する。
  const handback = current?.handback
    ? INVALID_HANDBACK
    : {
        footer: parseRunnerFooter(message),
        attestation: parseReviewCadenceAttestation(message),
      };
  writeRecord({
    sessionId: input.session_id,
    operation,
    agentId:
      current?.agentId ??
      (typeof input.agent_id === "string" ? input.agent_id : null),
    phase: current?.phase ?? "active",
    retryCount: current?.retryCount ?? 0,
    jobId: current?.jobId ?? null,
    handback,
  });
  return null;
}

function scheduleRetryOrFinish(input, operation, jobId) {
  const previous = readRecord(input.session_id, operation);
  const retryCount = previous?.retryCount ?? 0;
  if (retryCount < RETRY_LIMIT) {
    writeRecord({
      sessionId: input.session_id,
      operation,
      phase: "retry-required",
      retryCount: retryCount + 1,
      jobId: jobId || previous?.jobId || null,
    });
  } else {
    removeRecord(input.session_id, operation);
  }
}

function handleSubagentStop(input) {
  if (input.stop_hook_active !== false || typeof input.session_id !== "string") {
    return null;
  }

  if (input.agent_type === LEGACY_RESCUE) {
    const previous = readRecord(input.session_id, "rescue");
    writeRecord({
      sessionId: input.session_id,
      operation: "rescue",
      phase: "reroute-required",
      retryCount: previous?.retryCount ?? 0,
      jobId: previous?.jobId ?? null,
    });
    return null;
  }

  const operation = operationForAgentType(input.agent_type);
  if (!operation) return null;
  const current = readRecord(input.session_id, operation);
  if (
    current?.agentId &&
    typeof input.agent_id === "string" &&
    current.agentId !== input.agent_id
  ) {
    return null;
  }

  // hand-back の記録があればそれを採用し、無ければ last_assistant_message を解析する。
  const footer = current?.handback
    ? current.handback.footer
    : parseRunnerFooter(input.last_assistant_message);
  const reviewCadenceAttestation = current?.handback
    ? current.handback.attestation
    : parseReviewCadenceAttestation(input.last_assistant_message);
  const reportedOperation = footer?.operation ?? null;
  const status = footer?.status ?? null;
  const jobId = footer?.jobId ?? null;
  if (
    reportedOperation !== operation ||
    !VALID_STATUSES.has(status) ||
    (operation === "advisor" && reviewCadenceAttestation === null)
  ) {
    scheduleRetryOrFinish(input, operation, jobId);
    return null;
  }

  if (status === "retryable-failure") {
    scheduleRetryOrFinish(input, operation, jobId);
  } else {
    removeRecord(input.session_id, operation);
  }

  return null;
}

/**
 * Stop 入力の `background_tasks` から、稼働中 runner の operation 集合を作る。
 *
 * 配列が無い入力 (この field を提供しない host) では null を返し、呼び出し側は record
 * だけを根拠にする従来の判定へ退避する。
 */
function inFlightRunnerOperations(input) {
  const tasks = input.background_tasks;
  if (!Array.isArray(tasks)) return null;
  const operations = new Set();
  for (const task of tasks) {
    if (!task || typeof task !== "object") continue;
    if (task.type !== "subagent") continue;
    const operation = operationForAgentType(task.agent_type);
    if (operation) operations.add(operation);
  }
  return operations;
}

function handleStop(input) {
  if (input.stop_hook_active === true || typeof input.session_id !== "string") {
    return null;
  }
  const records = listRecords(input.session_id);
  if (records.length === 0) return null;
  const inFlight = inFlightRunnerOperations(input);

  const blocking = [];
  const waiting = [];
  for (const record of records) {
    // classifier に拒否された起動要求は満たされない。同じ起動を要求し続けない。
    if (record.phase === "denied") continue;
    if (inFlight === null) {
      blocking.push(record);
      continue;
    }
    if (record.phase === "active") {
      if (inFlight.has(record.operation)) {
        waiting.push(record);
        continue;
      }
      // 起動が拒否された印のある active record に対応する task が無いなら、runner は
      // 稼働していない。追跡喪失ではなく拒否の残骸なので block しない。
      if (record.launchDenied === true) continue;
    }
    blocking.push(record);
  }

  if (blocking.length > 0) {
    const instructions = blocking.map((record) => {
      const runner = RUNNERS[record.operation];
      if (record.phase === "active" && inFlight === null) {
        // 稼働状況の証拠 (`background_tasks`) が無い host では、追跡喪失とは断定できない。
        // 重複起動を促さず、既存 runner の report を待つ従来の案内に留める。
        return `${runner} は active です (この host は background_tasks を提供しないため稼働状況を確認できません)。新しい runner を重複起動せず、既存 runner の completion notification を待って report を処理してください。`;
      }
      if (record.phase === "active") {
        return `${runner} の active record に対応する background task がありません。同じ request で ${runner} を Agent tool の subagent_type に指定し、model: "sonnet" で起動し直してください。`;
      }
      const retry = record.phase === "retry-required" ? "自動 retry (残り 1 回)" : "reroute";
      return `${runner} を Agent tool の subagent_type に指定し、model: "sonnet" で ${retry} してください。起動受理だけをユーザーへ返さず、runner の report を処理してからタスクを完了扱いにしてください。`;
    });
    return {
      decision: "block",
      reason: `cross-model-advisor の未完了 runner があるため main session の停止を拒否します。${instructions.join(" ")}`,
    };
  }

  if (waiting.length > 0) {
    const notices = waiting.map(
      (record) =>
        `${RUNNERS[record.operation]} は稼働中です。report は completion notification で後続 turn に届くため、重複起動せずに notification を待ち、受け取ってから report を処理してください。`,
    );
    return {
      hookSpecificOutput: {
        hookEventName: "Stop",
        additionalContext: `cross-model-advisor: ${notices.join(" ")}`,
      },
    };
  }

  return null;
}

function dispatch(input) {
  switch (input.hook_event_name) {
    case "SessionStart":
      if (typeof input.session_id === "string") clearRunnerState(input.session_id);
      return null;
    case "SessionEnd":
      if (typeof input.session_id === "string") clearRunnerState(input.session_id);
      return null;
    case "PreToolUse":
      return handlePreToolUse(input);
    case "PermissionDenied":
      return handlePermissionDenied(input);
    case "SubagentStart":
      return handleSubagentStart(input);
    case "PostToolUse":
      return handlePostToolUse(input);
    case "SubagentStop":
      return handleSubagentStop(input);
    case "Stop":
      return handleStop(input);
    default:
      return null;
  }
}

let input;
try {
  input = JSON.parse(fs.readFileSync(0, "utf8"));
} catch {
  process.exit(0);
}

try {
  const response = dispatch(input);
  if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
} catch (error) {
  // PreToolUse gate の state failure は handlePreToolUse 内で deny に変換する。それ以外の
  // lifecycle storage failure は hook 自体を壊さず stderr へ明示し、次の event で復旧する。
  process.stderr.write(
    `[cross-model-advisor] runner state update failed: ${error?.message ?? "unknown error"}\n`,
  );
}

#!/usr/bin/env node

/**
 * Codex review cadence の enforcement。
 *
 * 責務: Codex review 成功 5 回ごとに根本方針 advisor checkpoint を要求する review
 * cadence を、本 plugin (pre-push-codex-review) が session 単位の state で担う。
 * checkpoint の実行主体 (`codex-advisor:advisor-runner` の起動・attestation の発行) は
 * codex-advisor plugin が担い、本 script は「いつ checkpoint を要求し、いつ解除するか」
 * の enforcement のみを担う。
 *
 * 計数対象 (1 サイクル = 成功 review 1 回。session ごとに合算する):
 *   - `pre-push-codex-review:codex-reviewer` / `pre-merge-codex-review:codex-reviewer`
 *     の SubagentStop で、`last_assistant_message` に `Status: pass|findings` 行が
 *     ちょうど 1 行ある場合
 *   - `codex-advisor:review-runner` の SubagentStop で、実質末尾 3 行の footer
 *     (`Codex-Runner-Operation: review` / `Codex-Runner-Status: success` /
 *     `Codex-Runner-Job-ID: <id>`) が揃っている場合
 *   旧 `pre-push-review:codex-reviewer` (codex gate 分離前の namespace) は計数対象に
 *   含めない。本 plugin の support floor は codex gate 分離後の pre-push-review
 *   v6.0.0 以降であり、旧 namespace の review 経路はサポート対象外のため。
 *
 * カウンター reset (checkpoint 充足) の契約:
 *   - `codex-advisor:advisor-runner` の SubagentStop で、footer が
 *     `Codex-Runner-Status: success` かつ footer 直前の実質行が
 *     `Codex-Advisor-Review-Cadence: satisfied` である場合、または footer が
 *     `Codex-Runner-Status: terminal-failure` かつ同行が `unavailable` である場合
 *   - fail-open: PostToolUseFailure (`tool_name` が `Agent` または `Task` で、
 *     `tool_input.subagent_type` が `codex-advisor:advisor-runner`) が checkpoint
 *     要求中に発火した場合、`unavailable` 相当としてカウンターを reset する
 *     (codex-advisor 未 install 環境で checkpoint が解除不能な block にならないため)
 *
 * enforcement:
 *   - PreToolUse (Bash): checkpoint 要求中 (完了 review が 5 回に達している間) は、
 *     review 起動形 (`node .../codex-companion.mjs review|adversarial-review`、
 *     `run-pre-push-codex-review.sh`、`run-codex-job.sh review`) を deny する
 *   - Stop: checkpoint 要求中は main session の停止を block し、
 *     `codex-advisor:advisor-runner` を `model: "sonnet"`, `run_in_background: false`
 *     で foreground 起動することと、相談 request に含める `<review_cycle_checkpoint>`
 *     4 項目 (Goal と受入基準・制約 / 直近 5 サイクルの review 履歴 / 現在の方針と
 *     不確実性 / course-correction の問い) を案内する
 *
 * SubagentStart: `STATUS_LINE_COUNTED_REVIEWERS` の 2 reviewer のみ起動記録が必要
 * (hooks.json の SubagentStart matcher もこの 2 つのみ配送する)。起動時の agent_id を
 * `activeReviewerAgentIds` へ記録し、対応する SubagentStop がその agent_id を消費する。
 *
 * state: 既定では UID ごとの一時 directory
 * (`$TMPDIR相当/pre-push-codex-review-<uid>/cadence-state/`) 配下に、session ごとの
 * JSON を 1 ファイル (ファイル名は sessionId の sha256 hex + `.json`) として保存する。
 * root は環境変数 `PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT` で差し替えられる。state に
 * prompt や Codex 出力は保存しない。SessionEnd でこの session の state を削除する。
 * SessionStart では削除しない (resume でカウンターを保持するため。そのため本 script は
 * SessionStart イベントを扱わない)。
 *
 * 扱う hook イベント: PreToolUse, SubagentStart, SubagentStop, PostToolUseFailure,
 * Stop, SessionEnd。
 */

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const STATUS_LINE_COUNTED_REVIEWERS = new Set([
  "pre-push-codex-review:codex-reviewer",
  "pre-merge-codex-review:codex-reviewer",
]);
const FOOTER_COUNTED_REVIEWER = "codex-advisor:review-runner";
const ADVISOR_CHECKPOINT_RUNNER = "codex-advisor:advisor-runner";
const REVIEW_CADENCE_LIMIT = 5;
const REVIEW_CADENCE_ATTESTATIONS = new Set([
  "satisfied",
  "unavailable",
  "not-applicable",
]);

function stateRoot() {
  const overridden = process.env.PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT;
  if (overridden) return overridden;
  const uid = typeof process.getuid === "function" ? process.getuid() : "unknown";
  return path.join(os.tmpdir(), `pre-push-codex-review-${uid}`, "cadence-state");
}

function statePath(sessionId) {
  const hash = crypto.createHash("sha256").update(sessionId).digest("hex");
  return path.join(stateRoot(), `${hash}.json`);
}

function ensureRoot() {
  fs.mkdirSync(stateRoot(), { recursive: true, mode: 0o700 });
  try {
    fs.chmodSync(stateRoot(), 0o700);
  } catch {
    // Existing directory permissions can be immutable on unusual filesystems.
  }
}

function sanitizeAgentIds(values) {
  return Array.isArray(values)
    ? [
        ...new Set(
          values.filter(
            (value) =>
              typeof value === "string" && /^[A-Za-z0-9._-]{1,128}$/.test(value),
          ),
        ),
      ]
    : [];
}

function readState(sessionId) {
  try {
    const parsed = JSON.parse(fs.readFileSync(statePath(sessionId), "utf8"));
    if (
      parsed.sessionId !== sessionId ||
      !Number.isInteger(parsed.completedReviews) ||
      parsed.completedReviews < 0
    ) {
      return null;
    }
    const activeReviewerAgentIds = sanitizeAgentIds(parsed.activeReviewerAgentIds);
    return {
      ...parsed,
      activeReviewerAgentIds,
      checkpointRequired:
        parsed.checkpointRequired === true ||
        parsed.completedReviews >= REVIEW_CADENCE_LIMIT,
    };
  } catch {
    return null;
  }
}

function writeState(sessionId, completedReviews, activeReviewerAgentIds = []) {
  ensureRoot();
  const destination = statePath(sessionId);
  const temporary = `${destination}.${process.pid}.${crypto
    .randomBytes(6)
    .toString("hex")}.tmp`;
  const normalizedCount = Math.min(
    REVIEW_CADENCE_LIMIT,
    Math.max(0, completedReviews),
  );
  const normalized = {
    sessionId,
    completedReviews: normalizedCount,
    activeReviewerAgentIds: sanitizeAgentIds(activeReviewerAgentIds),
    checkpointRequired: normalizedCount >= REVIEW_CADENCE_LIMIT,
    updatedAt: new Date().toISOString(),
  };
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

function removeState(sessionId) {
  try {
    fs.unlinkSync(statePath(sessionId));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

function shellSegments(command) {
  const segments = [];
  let current = "";
  let quote = null;
  let escaped = false;
  for (const character of command) {
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
    if (character === ";" || character === "\n" || character === "|" || character === "&") {
      if (current.trim()) segments.push(current.trim());
      current = "";
      continue;
    }
    current += character;
  }
  if (current.trim()) segments.push(current.trim());
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

function commandWords(segment) {
  const words = shellWords(segment);
  while (["then", "do", "else"].includes(words[0])) words.shift();
  while (/^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0] ?? "")) words.shift();

  if (words[0] === "command" || words[0] === "builtin" || words[0] === "nohup") {
    words.shift();
  }
  if (basename(words[0] ?? "") === "env") {
    words.shift();
    while (
      (words[0] ?? "").startsWith("-") ||
      /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0] ?? "")
    ) {
      words.shift();
    }
  }
  if (basename(words[0] ?? "") === "timeout") {
    words.shift();
    while ((words[0] ?? "").startsWith("-")) words.shift();
    if (/^[0-9]+(?:\.[0-9]+)?[smhd]?$/.test(words[0] ?? "")) words.shift();
  }
  return words;
}

/**
 * review 起動形だけを分類する。shell segment の executable 位置を要求することで、
 * rg/grep/cat/git diff の引数や quoted search text に現れただけの文字列を review 起動と
 * 誤認しない。分類対象は (a) `node .../codex-companion.mjs review|adversarial-review`
 * (b) basename が `run-pre-push-codex-review.sh` (c) `run-codex-job.sh review`。
 */
function isReviewLaunch(command) {
  for (const segment of shellSegments(command)) {
    const words = commandWords(segment);
    if (basename(words[0] ?? "") === "node") {
      let scriptIndex = 1;
      while ((words[scriptIndex] ?? "").startsWith("-")) scriptIndex += 1;
      if (basename(words[scriptIndex] ?? "") === "codex-companion.mjs") {
        const action = words[scriptIndex + 1];
        if (["review", "adversarial-review"].includes(action)) return true;
      }
    }

    let scriptIndex = 0;
    if (["bash", "sh"].includes(basename(words[0] ?? ""))) {
      scriptIndex = 1;
      while ((words[scriptIndex] ?? "").startsWith("-")) scriptIndex += 1;
    }
    const script = basename(words[scriptIndex] ?? "");
    if (script === "run-pre-push-codex-review.sh") return true;
    if (script === "run-codex-job.sh" && words[scriptIndex + 1] === "review") {
      return true;
    }
  }
  return false;
}

/**
 * footer / attestation 解析が共有する「実質末尾行」抽出。
 *
 * runner が footer をコードフェンス (```/~~~) で囲んだり、footer 行間に空白行を挟んだり
 * しても誤って解析失敗にならないよう、message を行分割し、末尾から走査して空白行
 * (/^\s*$/) とコードフェンス行 (先頭空白を除去した後に ``` または ~~~ で始まる行) を
 * スキップしながら実質行を count 行、元の並び順で収集して返す。count 行に満たない場合は
 * null。行の内容自体は変更しない (先頭空白の除去もしない — footer 行自体の先頭空白は
 * 従来どおり照合失敗になる)。
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
  // footer 3 行 + その直前の実質行 (attestation) の計 4 行を、フェンス・空白行を無視した
  // 実質末尾から取り出す。先頭要素が attestation 行になる。
  const lines = significantTailLines(message, 4);
  if (!lines) return null;
  const prefix = "Codex-Advisor-Review-Cadence: ";
  const line = lines[0];
  if (!line?.startsWith(prefix)) return null;
  const value = line.slice(prefix.length).trim();
  return REVIEW_CADENCE_ATTESTATIONS.has(value) ? value : null;
}

function parseReviewStatus(message) {
  if (typeof message !== "string") return null;
  const statusLines = message
    .replace(/\r\n/g, "\n")
    .split("\n")
    .filter((line) => line.startsWith("Status: "));
  if (statusLines.length !== 1) return null;
  const match = statusLines[0].match(/^Status: (pass|findings)$/);
  return match?.[1] ?? null;
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
  if (typeof command !== "string" || typeof input.session_id !== "string") {
    return null;
  }
  const state = readState(input.session_id);
  if (!state?.checkpointRequired) return null;
  if (!isReviewLaunch(command)) return null;
  return denyResponse(
    `前回の根本方針 checkpoint から Codex review が ${REVIEW_CADENCE_LIMIT} 回完了しています。次の review より先に ${ADVISOR_CHECKPOINT_RUNNER} を foreground 起動 (model: "sonnet", run_in_background: false) して、元の Goal・制約・直近の review 履歴・現在の方針を材料に根本方針を壁打ちしてください。通常の advisor 相談では解除されません。`,
  );
}

function handleSubagentStart(input) {
  if (
    typeof input.session_id !== "string" ||
    typeof input.agent_id !== "string" ||
    !/^[A-Za-z0-9._-]{1,128}$/.test(input.agent_id)
  ) {
    return null;
  }
  const state = readState(input.session_id);
  const activeReviewerAgentIds = state?.activeReviewerAgentIds ?? [];
  if (!activeReviewerAgentIds.includes(input.agent_id)) {
    activeReviewerAgentIds.push(input.agent_id);
  }
  writeState(input.session_id, state?.completedReviews ?? 0, activeReviewerAgentIds);
  return null;
}

function handleStatusLineReviewerStop(input) {
  if (typeof input.agent_id !== "string") return null;
  const state = readState(input.session_id);
  if (!state?.activeReviewerAgentIds.includes(input.agent_id)) return null;

  const activeReviewerAgentIds = state.activeReviewerAgentIds.filter(
    (agentId) => agentId !== input.agent_id,
  );
  const completedReviews =
    state.completedReviews +
    (parseReviewStatus(input.last_assistant_message) === null ? 0 : 1);
  if (completedReviews === 0 && activeReviewerAgentIds.length === 0) {
    removeState(input.session_id);
  } else {
    writeState(input.session_id, completedReviews, activeReviewerAgentIds);
  }
  return null;
}

function handleFooterReviewerStop(input) {
  const footer = parseRunnerFooter(input.last_assistant_message);
  if (footer?.operation !== "review" || footer.status !== "success") return null;
  const state = readState(input.session_id);
  writeState(
    input.session_id,
    (state?.completedReviews ?? 0) + 1,
    state?.activeReviewerAgentIds ?? [],
  );
  return null;
}

function handleAdvisorCheckpointStop(input) {
  const footer = parseRunnerFooter(input.last_assistant_message);
  if (footer?.operation !== "advisor") return null;
  const attestation = parseReviewCadenceAttestation(input.last_assistant_message);
  const satisfied = footer.status === "success" && attestation === "satisfied";
  const unavailable =
    footer.status === "terminal-failure" && attestation === "unavailable";
  if (satisfied || unavailable) removeState(input.session_id);
  return null;
}

function handleSubagentStop(input) {
  if (input.stop_hook_active !== false || typeof input.session_id !== "string") {
    return null;
  }
  if (STATUS_LINE_COUNTED_REVIEWERS.has(input.agent_type)) {
    return handleStatusLineReviewerStop(input);
  }
  if (input.agent_type === FOOTER_COUNTED_REVIEWER) {
    return handleFooterReviewerStop(input);
  }
  if (input.agent_type === ADVISOR_CHECKPOINT_RUNNER) {
    return handleAdvisorCheckpointStop(input);
  }
  return null;
}

function handlePostToolUseFailure(input) {
  if (input.tool_name !== "Agent" && input.tool_name !== "Task") return null;
  if (input.tool_input?.subagent_type !== ADVISOR_CHECKPOINT_RUNNER) return null;
  if (typeof input.session_id !== "string") return null;
  const state = readState(input.session_id);
  if (!state?.checkpointRequired) return null;
  removeState(input.session_id);
  process.stderr.write(
    `[pre-push-codex-review] ${ADVISOR_CHECKPOINT_RUNNER} の起動に失敗したため、review cadence の checkpoint を fail-open で reset しました。\n`,
  );
  return null;
}

function handleStop(input) {
  if (input.stop_hook_active === true || typeof input.session_id !== "string") {
    return null;
  }
  const state = readState(input.session_id);
  if (!state?.checkpointRequired) return null;
  return {
    decision: "block",
    reason: `Codex review が前回の根本方針 checkpoint から ${REVIEW_CADENCE_LIMIT} 回完了しました。${ADVISOR_CHECKPOINT_RUNNER} を model: "sonnet", run_in_background: false で foreground 起動してください。相談 request の <review_cycle_checkpoint> には次の 4 項目を省略せず含めます: Goal と受入基準・制約 / 直近 ${REVIEW_CADENCE_LIMIT} サイクルの review 履歴 / 現在の方針と不確実性 / course-correction の問い。通常の advisor 相談では解除されません。codex-advisor 未 install 等で advisor-runner の起動自体が失敗した場合は、その起動失敗をもって解除されます (fail-open)。`,
  };
}

function handleSessionEnd(input) {
  if (typeof input.session_id === "string") removeState(input.session_id);
  return null;
}

function dispatch(input) {
  switch (input.hook_event_name) {
    case "PreToolUse":
      return handlePreToolUse(input);
    case "SubagentStart":
      return handleSubagentStart(input);
    case "SubagentStop":
      return handleSubagentStop(input);
    case "PostToolUseFailure":
      return handlePostToolUseFailure(input);
    case "Stop":
      return handleStop(input);
    case "SessionEnd":
      return handleSessionEnd(input);
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
  process.stderr.write(
    `[pre-push-codex-review] review cadence state update failed: ${error?.message ?? "unknown error"}\n`,
  );
}

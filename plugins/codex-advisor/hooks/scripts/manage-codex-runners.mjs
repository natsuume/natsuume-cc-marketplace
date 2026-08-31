#!/usr/bin/env node

/**
 * Codex runner の直接実行 gate と lifecycle state machine (issue #291)。
 *
 * Claude Code hook の JSON を stdin から受け取り、次を一つの session-scoped state で
 * 接続する。
 *
 * - PreToolUse: Codex model の起動を role 固有 runner だけに限定する
 * - SubagentStart / SubagentStop: runner の active / retry / terminal 遷移を記録する
 * - Stop: 未回収の runner がある間は main session の終了を block する
 * - review cadence: 一般 / pre-push Codex review の成功 5 回ごとに根本方針 advisor
 *   checkpoint を要求する
 * - SessionStart: stale runner state、SessionEnd: runner + cadence state を掃除する
 *
 * state に prompt や Codex 出力は保存しない。既定では UID ごとの /tmp 配下、テストでは
 * CODEX_ADVISOR_STATE_ROOT で差し替えた directory に、session + operation ごとの JSON を
 * temp file + rename で atomic に保存する。
 */

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const RUNNERS = Object.freeze({
  rescue: "codex-advisor:rescue-runner",
  review: "codex-advisor:review-runner",
  advisor: "codex-advisor:advisor-runner",
});
const LEGACY_RESCUE = "codex:codex-rescue";
const PRE_PUSH_CODEX_REVIEWER = "pre-push-codex-review:codex-reviewer";
const RETRY_LIMIT = 1;
const REVIEW_CADENCE_LIMIT = 5;
const REVIEW_CADENCE_OPERATION = "review-cadence";
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

function stateRoot() {
  const overridden = process.env.CODEX_ADVISOR_STATE_ROOT;
  if (overridden) return overridden;
  const uid = typeof process.getuid === "function" ? process.getuid() : "unknown";
  return path.join(os.tmpdir(), `codex-advisor-${uid}`, "runner-state");
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

function reviewCadencePath(sessionId) {
  return recordPath(sessionId, REVIEW_CADENCE_OPERATION);
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

function readReviewCadence(sessionId) {
  try {
    const parsed = JSON.parse(
      fs.readFileSync(reviewCadencePath(sessionId), "utf8"),
    );
    if (
      parsed.sessionId !== sessionId ||
      parsed.operation !== REVIEW_CADENCE_OPERATION ||
      !Number.isInteger(parsed.completedReviews) ||
      parsed.completedReviews < 0
    ) {
      return null;
    }
    const activePrePushAgentIds = Array.isArray(parsed.activePrePushAgentIds)
      ? [
          ...new Set(
            parsed.activePrePushAgentIds.filter(
              (value) =>
                typeof value === "string" &&
                /^[A-Za-z0-9._-]{1,128}$/.test(value),
            ),
          ),
        ]
      : [];
    return {
      ...parsed,
      activePrePushAgentIds,
      checkpointRequired:
        parsed.checkpointRequired === true ||
        parsed.completedReviews >= REVIEW_CADENCE_LIMIT,
    };
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

function writeReviewCadence(
  sessionId,
  completedReviews,
  activePrePushAgentIds = [],
) {
  ensureRoot();
  const destination = reviewCadencePath(sessionId);
  const temporary = `${destination}.${process.pid}.${crypto
    .randomBytes(6)
    .toString("hex")}.tmp`;
  const normalizedCount = Math.min(
    REVIEW_CADENCE_LIMIT,
    Math.max(0, completedReviews),
  );
  const normalized = {
    sessionId,
    operation: REVIEW_CADENCE_OPERATION,
    completedReviews: normalizedCount,
    activePrePushAgentIds: [
      ...new Set(
        activePrePushAgentIds.filter(
          (value) =>
            typeof value === "string" &&
            /^[A-Za-z0-9._-]{1,128}$/.test(value),
        ),
      ),
    ],
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

function removeRecord(sessionId, operation) {
  try {
    fs.unlinkSync(recordPath(sessionId, operation));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

function removeReviewCadence(sessionId) {
  try {
    fs.unlinkSync(reviewCadencePath(sessionId));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

function clearRunnerState(sessionId) {
  for (const record of listRecords(sessionId)) {
    try {
      removeRecord(sessionId, record.operation);
    } catch {
      // Cleanup is best-effort; a later SessionStart can try again.
    }
  }
}

function clearSession(sessionId) {
  clearRunnerState(sessionId);
  try {
    removeReviewCadence(sessionId);
  } catch {
    // Cleanup is best-effort; a later SessionEnd can try again.
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
    if (script === "run-pre-push-codex-review.sh") {
      return {
        operation: "review",
        entrypoint: "pre-push-codex-review run-pre-push-codex-review.sh",
        cadenceOnly: true,
      };
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
  const launch = classifyModelLaunch(command);
  if (!launch) return null;

  const expectedRunner = RUNNERS[launch.operation];
  const cadence =
    launch.operation === "review" && typeof input.session_id === "string"
      ? readReviewCadence(input.session_id)
      : null;
  if (cadence?.checkpointRequired) {
    return denyResponse(
      `codex-advisor: 前回の根本方針 checkpoint から Codex review が ${REVIEW_CADENCE_LIMIT} 回完了しています。次の review より先に ${RUNNERS.advisor} を foreground 起動し、元の Goal・制約・反復 findings・現在のアプローチを材料に根本方針を壁打ちしてください。通常の advisor 相談では review cadence は解除されません。`,
    );
  }
  if (launch.cadenceOnly === true) return null;
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
      phase: "reroute-required",
      retryCount: previous?.retryCount ?? 0,
      jobId: previous?.jobId ?? null,
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
    `codex-advisor: ${launch.entrypoint} は ${expectedRunner} の foreground Agent だけが実行できます。${mismatch} からの直接実行を拒否しました。main session は Agent tool で subagent_type=\"${expectedRunner}\", model=\"sonnet\", run_in_background: false を指定し、terminal report まで待ってください。${stateFailure}`,
  );
}

function handleSubagentStart(input) {
  if (input.agent_type === PRE_PUSH_CODEX_REVIEWER) {
    if (
      typeof input.session_id !== "string" ||
      typeof input.agent_id !== "string" ||
      !/^[A-Za-z0-9._-]{1,128}$/.test(input.agent_id)
    ) {
      return null;
    }
    const cadence = readReviewCadence(input.session_id);
    const activePrePushAgentIds = cadence?.activePrePushAgentIds ?? [];
    if (!activePrePushAgentIds.includes(input.agent_id)) {
      activePrePushAgentIds.push(input.agent_id);
    }
    writeReviewCadence(
      input.session_id,
      cadence?.completedReviews ?? 0,
      activePrePushAgentIds,
    );
    return null;
  }
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

function parsePrePushReviewStatus(message) {
  if (typeof message !== "string") return null;
  const statusLines = message
    .replace(/\r\n/g, "\n")
    .split("\n")
    .filter((line) => line.startsWith("Status: "));
  if (statusLines.length !== 1) return null;
  const match = statusLines[0].match(/^Status: (pass|findings)$/);
  return match?.[1] ?? null;
}

function handlePrePushSubagentStop(input) {
  if (
    input.stop_hook_active !== false ||
    typeof input.session_id !== "string" ||
    typeof input.agent_id !== "string"
  ) {
    return null;
  }
  const cadence = readReviewCadence(input.session_id);
  if (!cadence?.activePrePushAgentIds.includes(input.agent_id)) return null;

  const activePrePushAgentIds = cadence.activePrePushAgentIds.filter(
    (agentId) => agentId !== input.agent_id,
  );
  const completedReviews =
    cadence.completedReviews +
    (parsePrePushReviewStatus(input.last_assistant_message) === null ? 0 : 1);
  if (completedReviews === 0 && activePrePushAgentIds.length === 0) {
    removeReviewCadence(input.session_id);
  } else {
    writeReviewCadence(
      input.session_id,
      completedReviews,
      activePrePushAgentIds,
    );
  }
  return null;
}

function handleSubagentStop(input) {
  if (input.stop_hook_active !== false || typeof input.session_id !== "string") {
    return null;
  }

  if (input.agent_type === PRE_PUSH_CODEX_REVIEWER) {
    return handlePrePushSubagentStop(input);
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

  const footer = parseRunnerFooter(input.last_assistant_message);
  const reviewCadenceAttestation = parseReviewCadenceAttestation(
    input.last_assistant_message,
  );
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

  if (operation === "review" && status === "success") {
    const cadence = readReviewCadence(input.session_id);
    writeReviewCadence(
      input.session_id,
      (cadence?.completedReviews ?? 0) + 1,
      cadence?.activePrePushAgentIds ?? [],
    );
  }
  if (
    operation === "advisor" &&
    ((status === "success" && reviewCadenceAttestation === "satisfied") ||
      (status === "terminal-failure" &&
        reviewCadenceAttestation === "unavailable"))
  ) {
    removeReviewCadence(input.session_id);
  }
  return null;
}

function handleStop(input) {
  if (input.stop_hook_active === true || typeof input.session_id !== "string") {
    return null;
  }
  const records = listRecords(input.session_id);
  const cadence = readReviewCadence(input.session_id);
  if (records.length === 0 && !cadence?.checkpointRequired) return null;
  const instructions = records.map((record) => {
    const runner = RUNNERS[record.operation];
    if (record.phase === "active") {
      return `${runner} は active です。既存 Agent の completion notification / TaskOutput を回収し、terminal report を受け取るまで終了しないでください。新しい runner は重複起動しません。`;
    }
    const retry = record.phase === "retry-required" ? "自動 retry (残り 1 回)" : "reroute";
    return `${runner} を Agent tool の subagent_type に指定し、model: "sonnet"、run_in_background: false で ${retry} してください。起動受理だけをユーザーへ返さず、terminal report まで待ってください。`;
  });
  if (cadence?.checkpointRequired) {
    instructions.push(
      `Codex review が前回の checkpoint から ${REVIEW_CADENCE_LIMIT} 回完了しました。次の review または完了宣言より先に ${RUNNERS.advisor} を foreground 起動してください。相談 request は <review_cycle_checkpoint> に元の Goal / 受入基準・制約、${REVIEW_CADENCE_LIMIT} 回の findings と修正の傾向、現在の仮説とアプローチを含め、「局所修正を続ける前に根本方針・問題設定・設計境界を変えるべきか」を質問します。通常の相談ではこの state は解除されません。`,
    );
  }
  return {
    decision: "block",
    reason: `codex-advisor の未完了 runner または review cadence state があるため main session の停止を拒否します。${instructions.join(" ")}`,
  };
}

function dispatch(input) {
  switch (input.hook_event_name) {
    case "SessionStart":
      if (typeof input.session_id === "string") clearRunnerState(input.session_id);
      return null;
    case "SessionEnd":
      if (typeof input.session_id === "string") clearSession(input.session_id);
      return null;
    case "PreToolUse":
      return handlePreToolUse(input);
    case "SubagentStart":
      return handleSubagentStart(input);
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
    `[codex-advisor] runner state update failed: ${error?.message ?? "unknown error"}\n`,
  );
}

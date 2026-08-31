#!/usr/bin/env node

/**
 * Codex review cadence の enforcement (skeleton — ロジック本体は Phase B で実装する)。
 *
 * 責務: Codex review 成功 5 回ごとに根本方針 advisor checkpoint を要求する review
 * cadence を、本 plugin (pre-push-codex-review) が session 単位の state で担う。
 * checkpoint の実行主体 (`codex-advisor:advisor-runner` の起動・attestation の発行) は
 * codex-advisor plugin が引き続き担い、本 script は「いつ checkpoint を要求し、いつ
 * 解除するか」の enforcement のみを担う。
 *
 * 計数対象 (1 サイクル = 成功 review 1 回。session ごとに合算する):
 *   - `pre-push-codex-review:codex-reviewer` / `pre-merge-codex-review:codex-reviewer`
 *     の SubagentStop で、`last_assistant_message` に `Status: pass|findings` 行が
 *     ちょうど 1 行ある場合
 *   - `codex-advisor:review-runner` の SubagentStop で、実質末尾 3 行の footer
 *     (`Codex-Runner-Operation: review` / `Codex-Runner-Status: success` /
 *     `Codex-Runner-Job-ID: <id>`) が揃っている場合
 *   旧 `pre-push-review:codex-reviewer` (互換 alias) は計数対象に含めない。
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
 * state: 既定では UID ごとの一時 directory
 * (`$TMPDIR相当/pre-push-codex-review-<uid>/cadence-state/`) 配下に、session ごとの
 * JSON を 1 ファイルとして保存する。root は環境変数
 * `PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT` で差し替えられる。state に prompt や
 * Codex 出力は保存しない。SessionEnd でこの session の state を削除する。SessionStart
 * では削除しない (resume でカウンターを保持するため。そのため本 script は
 * SessionStart イベントを扱わない)。
 *
 * 扱う hook イベント: PreToolUse, SubagentStart, SubagentStop, PostToolUseFailure,
 * Stop, SessionEnd。
 */

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

function stateRoot() {
  const overridden = process.env.PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT;
  if (overridden) return overridden;
  const uid = typeof process.getuid === "function" ? process.getuid() : "unknown";
  return path.join(os.tmpdir(), `pre-push-codex-review-${uid}`, "cadence-state");
}

function dispatch(input) {
  switch (input.hook_event_name) {
    case "PreToolUse":
      // checkpoint 要求中は review 起動形 (codex-companion.mjs review|adversarial-review /
      // run-pre-push-codex-review.sh / run-codex-job.sh review) を deny する。
      return null;
    case "SubagentStart":
      // 計数対象 reviewer (STATUS_LINE_COUNTED_REVIEWERS / FOOTER_COUNTED_REVIEWER) の
      // 起動記録が計数に必要な場合、ここで state へ書く。
      return null;
    case "SubagentStop":
      // 計数対象 reviewer の成功 review を cadence カウンターへ加算し、
      // ADVISOR_CHECKPOINT_RUNNER の checkpoint 充足 attestation でカウンターを
      // reset する。
      return null;
    case "PostToolUseFailure":
      // ADVISOR_CHECKPOINT_RUNNER の起動失敗を fail-open で checkpoint 充足
      // (unavailable) とみなし、カウンターを reset する。
      return null;
    case "Stop":
      // checkpoint 要求中 (カウンターが REVIEW_CADENCE_LIMIT に到達) は main session の
      // 停止を block し、ADVISOR_CHECKPOINT_RUNNER の foreground 起動と
      // <review_cycle_checkpoint> 4 項目を案内する。
      return null;
    case "SessionEnd":
      // この session の cadence state を stateRoot() 配下から削除する。
      return null;
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

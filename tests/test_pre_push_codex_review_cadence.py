"""pre-push-codex-review plugin の review cadence 契約テスト。

対象は `plugins/pre-push-codex-review/hooks/scripts/manage-review-cadence.mjs`
(以下 cadence script)。cadence script の header docs が定義する契約 (計数対象・
reset 経路・enforcement) を hook JSON I/O を通じて固定する。state 隔離は環境変数
`PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT` を各テストの一時ディレクトリへ向ける
ことで行う。

private helper の構成や state ファイル名の形式には結合しない。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-push-codex-review"
HOOK = PLUGIN / "hooks" / "scripts" / "manage-review-cadence.mjs"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"

PRE_PUSH_CODEX_REVIEWER = "pre-push-codex-review:codex-reviewer"
PRE_MERGE_CODEX_REVIEWER = "pre-merge-codex-review:codex-reviewer"
# codex gate 分離前の旧 namespace。cadence script のサポート対象外であり、計数
# されないことを固定する。
PRE_PUSH_CODEX_REVIEWER_LEGACY = "pre-push-review:codex-reviewer"
FOOTER_COUNTED_REVIEWER = "codex-advisor:review-runner"
ADVISOR_CHECKPOINT_RUNNER = "codex-advisor:advisor-runner"

REVIEW_CADENCE_LIMIT = 5

# 計数対象の 4 つの review 起動形。
REVIEW_LAUNCH_COMMANDS = {
    "wrapper": "bash /x/run-pre-push-codex-review.sh",
    "companion-review": 'node "/x/codex-companion.mjs" review',
    "companion-adversarial-review": 'node "/x/codex-companion.mjs" adversarial-review',
    "job-helper-review": "bash /x/run-codex-job.sh review",
}
# 旧 pre-push-review core が所有していた codex review wrapper の basename。
# cadence script の isReviewLaunch は basename でしか判定しないため、この
# コマンドは分類対象外 (checkpoint 要求中でも deny されない) になる。
LEGACY_WRAPPER_COMMAND = "bash /x/run-codex-review.sh"


class HookHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state_root = Path(self.temporary.name) / "state"

    def run_hook(
        self, payload: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        if not HOOK.is_file():
            self.fail(f"cadence script is missing: {HOOK}")
        env = os.environ.copy()
        env["PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT"] = str(self.state_root)
        return subprocess.run(
            ["node", str(HOOK)],
            input=json.dumps(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            cwd=self.temporary.name,
            env=env,
            timeout=10,
        )

    def hook_response(
        self, payload: dict[str, object]
    ) -> dict[str, object] | None:
        result = self.run_hook(payload)
        self.assertEqual(0, result.returncode, result.stderr)
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)

    # -- state introspection -------------------------------------------------

    def state_records(self) -> list[dict[str, object]]:
        if not self.state_root.exists():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.state_root.rglob("*.json"))
        ]

    def state_for(self, session_id: str) -> dict[str, object] | None:
        matches = [
            record
            for record in self.state_records()
            if record["sessionId"] == session_id
        ]
        self.assertLessEqual(len(matches), 1, matches)
        return matches[0] if matches else None

    # -- payload builders ------------------------------------------------------

    def status_line_start(
        self,
        agent_type: str,
        *,
        session_id: str = "session-a",
        agent_id: str = "agent-a",
    ) -> dict[str, object] | None:
        return self.hook_response(
            {
                "hook_event_name": "SubagentStart",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": agent_type,
            }
        )

    def status_line_stop(
        self,
        agent_type: str,
        status: str,
        *,
        session_id: str = "session-a",
        agent_id: str = "agent-a",
        extra_status: str | None = None,
        stop_hook_active: bool = False,
    ) -> dict[str, object] | None:
        lines = ["# Codex Review", "", f"Status: {status}"]
        if extra_status is not None:
            lines.extend(["", f"Status: {extra_status}"])
        return self.hook_response(
            {
                "hook_event_name": "SubagentStop",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": agent_type,
                "last_assistant_message": "\n".join(lines),
                "stop_hook_active": stop_hook_active,
            }
        )

    def complete_status_line_review(
        self,
        agent_type: str,
        *,
        session_id: str = "session-a",
        agent_id: str,
        status: str = "pass",
    ) -> None:
        self.status_line_start(agent_type, session_id=session_id, agent_id=agent_id)
        self.status_line_stop(
            agent_type, status, session_id=session_id, agent_id=agent_id
        )

    @staticmethod
    def footer_lines(
        operation: str, status: str, job_id: str = "job-example"
    ) -> list[str]:
        return [
            f"Codex-Runner-Operation: {operation}",
            f"Codex-Runner-Status: {status}",
            f"Codex-Runner-Job-ID: {job_id}",
        ]

    @staticmethod
    def maybe_fence(lines: list[str], fence: bool) -> list[str]:
        if not fence:
            return list(lines)
        return ["```text", *lines, "```"]

    def review_runner_stop(
        self,
        *,
        session_id: str = "session-a",
        agent_id: str = "review-runner-a",
        status: str = "success",
        job_id: str = "review-job",
        fence: bool = False,
    ) -> dict[str, object] | None:
        body = self.maybe_fence(self.footer_lines("review", status, job_id), fence)
        message = "\n".join(["Codex review report", *body])
        return self.hook_response(
            {
                "hook_event_name": "SubagentStop",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": FOOTER_COUNTED_REVIEWER,
                "last_assistant_message": message,
                "stop_hook_active": False,
            }
        )

    def advisor_runner_stop(
        self,
        *,
        session_id: str = "session-a",
        agent_id: str = "advisor-runner-a",
        status: str = "success",
        attestation: str | None = "satisfied",
        job_id: str = "advisor-job",
        fence: bool = False,
    ) -> dict[str, object] | None:
        block = list(self.footer_lines("advisor", status, job_id))
        if attestation is not None:
            block = [f"Codex-Advisor-Review-Cadence: {attestation}", *block]
        body = self.maybe_fence(block, fence)
        message = "\n".join(["Codex advisor report", *body])
        return self.hook_response(
            {
                "hook_event_name": "SubagentStop",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": ADVISOR_CHECKPOINT_RUNNER,
                "last_assistant_message": message,
                "stop_hook_active": False,
            }
        )

    def bash_payload(
        self, command: str, *, session_id: str = "session-a"
    ) -> dict[str, object]:
        return {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }

    def main_stop(
        self, session_id: str = "session-a", *, stop_hook_active: bool = False
    ) -> dict[str, object] | None:
        return self.hook_response(
            {
                "hook_event_name": "Stop",
                "session_id": session_id,
                "stop_hook_active": stop_hook_active,
            }
        )

    def post_tool_use_failure(
        self,
        *,
        session_id: str = "session-a",
        subagent_type: str = ADVISOR_CHECKPOINT_RUNNER,
        prompt: str | None = None,
        tool_name: str = "Agent",
        is_interrupt: bool | None = None,
    ) -> dict[str, object] | None:
        tool_input: dict[str, object] = {"subagent_type": subagent_type}
        if prompt is not None:
            tool_input["prompt"] = prompt
        payload: dict[str, object] = {
            "hook_event_name": "PostToolUseFailure",
            "session_id": session_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        if is_interrupt is not None:
            payload["is_interrupt"] = is_interrupt
        return self.hook_response(payload)

    def session_end(self, session_id: str = "session-a") -> dict[str, object] | None:
        return self.hook_response(
            {"hook_event_name": "SessionEnd", "session_id": session_id}
        )

    def session_start(self, session_id: str = "session-a") -> dict[str, object] | None:
        return self.hook_response(
            {"hook_event_name": "SessionStart", "session_id": session_id}
        )

    # -- assertions --------------------------------------------------------

    def assert_denied(self, response: dict[str, object] | None) -> None:
        self.assertIsNotNone(response)
        assert response is not None
        hook_output = response["hookSpecificOutput"]
        assert isinstance(hook_output, dict)
        self.assertEqual("deny", hook_output["permissionDecision"])
        self.assertIn(
            ADVISOR_CHECKPOINT_RUNNER, hook_output["permissionDecisionReason"]
        )

    def assert_stop_blocked(self, response: dict[str, object] | None) -> None:
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual("block", response["decision"])
        self.assertIn(ADVISOR_CHECKPOINT_RUNNER, response["reason"])


class StatusLineReviewCadenceTest(HookHarness):
    """`pre-push-codex-review:codex-reviewer` の Status 行判定と checkpoint 起動。"""

    def test_five_pre_push_reviews_trigger_checkpoint_and_deny_review_launches(
        self,
    ) -> None:
        session_id = "session-pre-push"
        statuses = ["pass", "findings", "pass", "findings"]
        for cycle, status in enumerate(statuses, start=1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status=status,
            )
            self.assertIsNone(self.main_stop(session_id))
            self.assertIsNone(
                self.hook_response(
                    self.bash_payload(
                        REVIEW_LAUNCH_COMMANDS["wrapper"], session_id=session_id
                    )
                )
            )

        self.complete_status_line_review(
            PRE_PUSH_CODEX_REVIEWER,
            session_id=session_id,
            agent_id="reviewer-5",
            status="pass",
        )
        self.assert_stop_blocked(self.main_stop(session_id))

        for label, command in REVIEW_LAUNCH_COMMANDS.items():
            with self.subTest(command=label):
                self.assert_denied(
                    self.hook_response(
                        self.bash_payload(command, session_id=session_id)
                    )
                )

        # 旧 wrapper basename は分類対象外なので checkpoint 要求中でも deny されない。
        self.assertIsNone(
            self.hook_response(
                self.bash_payload(LEGACY_WRAPPER_COMMAND, session_id=session_id)
            )
        )

    def test_legacy_pre_push_review_namespace_is_never_counted(self) -> None:
        session_id = "session-legacy"
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER_LEGACY,
                session_id=session_id,
                agent_id=f"legacy-{cycle}",
                status="pass",
            )
        self.assertIsNone(self.main_stop(session_id))
        self.assertIsNone(
            self.hook_response(
                self.bash_payload(
                    REVIEW_LAUNCH_COMMANDS["wrapper"], session_id=session_id
                )
            )
        )
        # SubagentStart の agent_type ガードにより、旧 namespace は一切 state を
        # 作らない。
        self.assertEqual([], self.state_records())

    def test_status_line_requires_exactly_one_status_line(self) -> None:
        session_id = "session-status-exactness"
        # 2 個目の Status 行を含む report は計数されない。
        self.status_line_start(
            PRE_PUSH_CODEX_REVIEWER, session_id=session_id, agent_id="ambiguous"
        )
        self.status_line_stop(
            PRE_PUSH_CODEX_REVIEWER,
            "pass",
            session_id=session_id,
            agent_id="ambiguous",
            extra_status="findings",
        )
        state = self.state_for(session_id)
        self.assertTrue(state is None or state["completedReviews"] == 0)

        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"valid-{cycle}",
                status="pass",
            )
            if cycle < REVIEW_CADENCE_LIMIT:
                self.assertIsNone(self.main_stop(session_id))
        self.assert_stop_blocked(self.main_stop(session_id))

    def test_stop_hook_active_true_is_ignored_everywhere(self) -> None:
        session_id = "session-stop-hook-active"
        self.status_line_start(
            PRE_PUSH_CODEX_REVIEWER, session_id=session_id, agent_id="agent-a"
        )
        # stop_hook_active: true の SubagentStop は無視され、review は計数されない。
        self.status_line_stop(
            PRE_PUSH_CODEX_REVIEWER,
            "pass",
            session_id=session_id,
            agent_id="agent-a",
            stop_hook_active=True,
        )
        state = self.state_for(session_id)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(0, state["completedReviews"])
        self.assertIn("agent-a", state["activeReviewerAgentIds"])

        # checkpoint 要求中に main session の Stop が stop_hook_active: true で
        # 発火した場合も block しない。
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        self.assertIsNone(self.main_stop(session_id, stop_hook_active=True))
        self.assert_stop_blocked(self.main_stop(session_id, stop_hook_active=False))

    def test_subagent_start_requires_valid_agent_id(self) -> None:
        session_id = "session-invalid-agent-id"
        self.status_line_start(
            PRE_PUSH_CODEX_REVIEWER, session_id=session_id, agent_id="bad/agent id"
        )
        self.assertEqual([], self.state_records())


class MixedReviewerCadenceTest(HookHarness):
    """canonical reviewer と `codex-advisor:review-runner` は同一カウンターに合算する。"""

    def test_pre_push_pre_merge_and_review_runner_share_the_counter(self) -> None:
        session_id = "session-mixed"
        self.complete_status_line_review(
            PRE_PUSH_CODEX_REVIEWER,
            session_id=session_id,
            agent_id="pre-push-1",
            status="pass",
        )
        self.complete_status_line_review(
            PRE_PUSH_CODEX_REVIEWER,
            session_id=session_id,
            agent_id="pre-push-2",
            status="findings",
        )
        self.assertIsNone(self.main_stop(session_id))

        self.complete_status_line_review(
            PRE_MERGE_CODEX_REVIEWER,
            session_id=session_id,
            agent_id="pre-merge-1",
            status="pass",
        )
        self.assertIsNone(self.main_stop(session_id))

        self.review_runner_stop(
            session_id=session_id, agent_id="review-runner-1", job_id="job-1"
        )
        self.assertIsNone(self.main_stop(session_id))

        self.review_runner_stop(
            session_id=session_id, agent_id="review-runner-2", job_id="job-2"
        )
        self.assert_stop_blocked(self.main_stop(session_id))

    def test_review_runner_footer_requires_review_operation_and_success(self) -> None:
        session_id = "session-review-runner-invalid"
        # operation が review 以外、または status が success 以外なら計数しない。
        self.review_runner_stop(session_id=session_id, status="retryable-failure")
        state = self.state_for(session_id)
        self.assertTrue(state is None or state["completedReviews"] == 0)


class LegacyWrapperClassificationTest(HookHarness):
    def test_legacy_wrapper_basename_is_never_classified_as_a_review_launch(
        self,
    ) -> None:
        session_id = "session-legacy-wrapper"
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        self.assert_stop_blocked(self.main_stop(session_id))
        self.assertIsNone(
            self.hook_response(
                self.bash_payload(LEGACY_WRAPPER_COMMAND, session_id=session_id)
            )
        )


class AdvisorCheckpointResetTest(HookHarness):
    def drive_to_checkpoint(self, session_id: str) -> None:
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        self.assert_stop_blocked(self.main_stop(session_id))

    def test_satisfied_attestation_resets_and_silences_enforcement(self) -> None:
        session_id = "session-satisfied"
        self.drive_to_checkpoint(session_id)
        self.advisor_runner_stop(
            session_id=session_id, status="success", attestation="satisfied"
        )
        self.assertIsNone(self.main_stop(session_id))
        self.assertIsNone(
            self.hook_response(
                self.bash_payload(
                    REVIEW_LAUNCH_COMMANDS["wrapper"], session_id=session_id
                )
            )
        )
        self.assertIsNone(self.state_for(session_id))

    def test_unavailable_attestation_resets_and_silences_enforcement(self) -> None:
        session_id = "session-unavailable"
        self.drive_to_checkpoint(session_id)
        self.advisor_runner_stop(
            session_id=session_id,
            status="terminal-failure",
            attestation="unavailable",
        )
        self.assertIsNone(self.main_stop(session_id))
        self.assertIsNone(self.state_for(session_id))

    def test_footer_without_attestation_line_does_not_reset(self) -> None:
        session_id = "session-missing-attestation"
        self.drive_to_checkpoint(session_id)
        self.advisor_runner_stop(
            session_id=session_id, status="success", attestation=None
        )
        self.assert_stop_blocked(self.main_stop(session_id))

    def test_mismatched_status_and_attestation_combinations_do_not_reset(self) -> None:
        session_id = "session-mismatched-attestation"
        self.drive_to_checkpoint(session_id)
        combinations = [
            ("success", "unavailable"),
            ("terminal-failure", "satisfied"),
            ("success", "not-applicable"),
        ]
        for status, attestation in combinations:
            with self.subTest(status=status, attestation=attestation):
                self.advisor_runner_stop(
                    session_id=session_id, status=status, attestation=attestation
                )
                self.assert_stop_blocked(self.main_stop(session_id))


class FooterFencingTest(HookHarness):
    """コードフェンスで囲まれた footer / attestation でも計数・reset が機能する。"""

    def test_fenced_review_runner_footer_still_counts(self) -> None:
        session_id = "session-fenced-review"
        self.review_runner_stop(session_id=session_id, fence=True)
        state = self.state_for(session_id)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(1, state["completedReviews"])

    def test_fenced_advisor_attestation_and_footer_together_resets(self) -> None:
        session_id = "session-fenced-advisor"
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        self.assert_stop_blocked(self.main_stop(session_id))

        self.advisor_runner_stop(
            session_id=session_id,
            status="success",
            attestation="satisfied",
            fence=True,
        )
        self.assertIsNone(self.main_stop(session_id))
        self.assertIsNone(self.state_for(session_id))


class SessionLifecycleTest(HookHarness):
    def test_session_end_deletes_state(self) -> None:
        session_id = "session-end-cleanup"
        self.complete_status_line_review(
            PRE_PUSH_CODEX_REVIEWER,
            session_id=session_id,
            agent_id="reviewer-1",
            status="pass",
        )
        self.assertIsNotNone(self.state_for(session_id))
        self.session_end(session_id)
        self.assertIsNone(self.state_for(session_id))

    def test_session_start_preserves_state_across_resume(self) -> None:
        session_id = "session-resume"
        for cycle in range(1, REVIEW_CADENCE_LIMIT):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        before = self.state_for(session_id)
        self.assertIsNotNone(before)
        assert before is not None
        self.assertEqual(REVIEW_CADENCE_LIMIT - 1, before["completedReviews"])

        self.assertIsNone(self.session_start(session_id))
        after = self.state_for(session_id)
        self.assertEqual(before, after)

        self.complete_status_line_review(
            PRE_PUSH_CODEX_REVIEWER,
            session_id=session_id,
            agent_id=f"reviewer-{REVIEW_CADENCE_LIMIT}",
            status="pass",
        )
        self.assert_stop_blocked(self.main_stop(session_id))


class PostToolUseFailureFailOpenTest(HookHarness):
    def drive_to_checkpoint(self, session_id: str) -> None:
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        self.assert_stop_blocked(self.main_stop(session_id))

    def test_checkpoint_consultation_launch_failure_resets_during_checkpoint(
        self,
    ) -> None:
        session_id = "session-checkpoint-failure"
        self.drive_to_checkpoint(session_id)
        self.post_tool_use_failure(
            session_id=session_id,
            prompt=(
                "<task>...</task>\n<review_cycle_checkpoint>...</review_cycle_checkpoint>"
            ),
        )
        self.assertIsNone(self.main_stop(session_id))
        self.assertIsNone(self.state_for(session_id))

    def test_interrupt_failure_does_not_reset(self) -> None:
        session_id = "session-interrupt-failure"
        self.drive_to_checkpoint(session_id)
        self.post_tool_use_failure(
            session_id=session_id,
            prompt=(
                "<task>...</task>\n<review_cycle_checkpoint>...</review_cycle_checkpoint>"
            ),
            is_interrupt=True,
        )
        self.assert_stop_blocked(self.main_stop(session_id))

    def test_ordinary_consultation_launch_failure_does_not_reset(self) -> None:
        session_id = "session-ordinary-failure"
        self.drive_to_checkpoint(session_id)
        self.post_tool_use_failure(
            session_id=session_id,
            prompt="<task>ordinary advisor consult, no checkpoint marker</task>",
        )
        self.assert_stop_blocked(self.main_stop(session_id))

    def test_missing_prompt_field_does_not_reset(self) -> None:
        session_id = "session-missing-prompt"
        self.drive_to_checkpoint(session_id)
        self.post_tool_use_failure(session_id=session_id, prompt=None)
        self.assert_stop_blocked(self.main_stop(session_id))

    def test_checkpoint_prompt_failure_before_checkpoint_required_is_a_noop(
        self,
    ) -> None:
        session_id = "session-early-failure"
        for cycle in range(1, REVIEW_CADENCE_LIMIT - 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        before = self.state_for(session_id)
        self.assertIsNotNone(before)
        self.post_tool_use_failure(
            session_id=session_id, prompt="<review_cycle_checkpoint>...</review_cycle_checkpoint>"
        )
        self.assertEqual(before, self.state_for(session_id))

    def test_wrong_subagent_type_or_tool_name_does_not_reset(self) -> None:
        session_id = "session-wrong-target"
        self.drive_to_checkpoint(session_id)
        self.post_tool_use_failure(
            session_id=session_id,
            subagent_type=FOOTER_COUNTED_REVIEWER,
            prompt="<review_cycle_checkpoint>...</review_cycle_checkpoint>",
        )
        self.assert_stop_blocked(self.main_stop(session_id))

        self.post_tool_use_failure(
            session_id=session_id,
            tool_name="Bash",
            prompt="<review_cycle_checkpoint>...</review_cycle_checkpoint>",
        )
        self.assert_stop_blocked(self.main_stop(session_id))


class SubagentStartGuardTest(HookHarness):
    def test_uncounted_agent_type_is_not_recorded(self) -> None:
        session_id = "session-uncounted-start"
        for agent_type in (
            FOOTER_COUNTED_REVIEWER,
            ADVISOR_CHECKPOINT_RUNNER,
            PRE_PUSH_CODEX_REVIEWER_LEGACY,
            "codex-advisor:rescue-runner",
        ):
            with self.subTest(agent_type=agent_type):
                self.status_line_start(
                    agent_type, session_id=session_id, agent_id=f"agent-{agent_type}"
                )
        self.assertEqual([], self.state_records())

    def test_counted_agent_type_is_recorded(self) -> None:
        session_id = "session-counted-start"
        self.status_line_start(
            PRE_PUSH_CODEX_REVIEWER, session_id=session_id, agent_id="agent-a"
        )
        state = self.state_for(session_id)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertIn("agent-a", state["activeReviewerAgentIds"])


class HooksManifestContractTest(unittest.TestCase):
    @staticmethod
    def _manifest() -> dict[str, object]:
        return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]

    @staticmethod
    def _matcher_entry_for(
        hooks: dict[str, object], event: str, command_substring: str
    ) -> dict[str, object]:
        for entry in hooks[event]:
            if "matcher" not in entry:
                continue
            commands = [
                hook["command"]
                for hook in entry["hooks"]
                if hook["type"] == "command"
            ]
            if any(command_substring in command for command in commands):
                return entry
        raise AssertionError(
            f"no matcher entry for {event} references {command_substring}"
        )

    def test_subagent_start_matcher_covers_only_two_status_line_reviewers(
        self,
    ) -> None:
        hooks = self._manifest()
        entry = self._matcher_entry_for(
            hooks, "SubagentStart", "manage-review-cadence.mjs"
        )
        pattern = re.compile(entry["matcher"])
        accepted = (PRE_PUSH_CODEX_REVIEWER, PRE_MERGE_CODEX_REVIEWER)
        rejected = (
            PRE_PUSH_CODEX_REVIEWER_LEGACY,
            FOOTER_COUNTED_REVIEWER,
            ADVISOR_CHECKPOINT_RUNNER,
            "codex-advisor:rescue-runner",
            "pre-push-codex-review:code-reviewer",
        )
        for agent_type in accepted:
            with self.subTest(agent_type=agent_type):
                self.assertIsNotNone(pattern.fullmatch(agent_type))
        for agent_type in rejected:
            with self.subTest(agent_type=agent_type):
                self.assertIsNone(pattern.fullmatch(agent_type))

    def test_subagent_stop_matcher_covers_two_reviewers_and_two_runners(self) -> None:
        hooks = self._manifest()
        entry = self._matcher_entry_for(
            hooks, "SubagentStop", "manage-review-cadence.mjs"
        )
        pattern = re.compile(entry["matcher"])
        accepted = (
            PRE_PUSH_CODEX_REVIEWER,
            PRE_MERGE_CODEX_REVIEWER,
            FOOTER_COUNTED_REVIEWER,
            ADVISOR_CHECKPOINT_RUNNER,
        )
        rejected = (
            PRE_PUSH_CODEX_REVIEWER_LEGACY,
            "codex-advisor:rescue-runner",
            "pre-push-codex-review:code-reviewer",
        )
        for agent_type in accepted:
            with self.subTest(agent_type=agent_type):
                self.assertIsNotNone(pattern.fullmatch(agent_type))
        for agent_type in rejected:
            with self.subTest(agent_type=agent_type):
                self.assertIsNone(pattern.fullmatch(agent_type))

    def test_remaining_lifecycle_events_are_registered(self) -> None:
        hooks = self._manifest()
        for event in ("PreToolUse", "PostToolUseFailure", "Stop", "SessionEnd"):
            with self.subTest(event=event):
                commands = [
                    hook["command"]
                    for entry in hooks[event]
                    for hook in entry["hooks"]
                    if hook["type"] == "command"
                ]
                self.assertTrue(
                    any(
                        "manage-review-cadence.mjs" in command
                        for command in commands
                    ),
                    commands,
                )
        session_start_commands = [
            hook["command"]
            for entry in hooks["SessionStart"]
            for hook in entry["hooks"]
            if hook["type"] == "command"
        ]
        self.assertTrue(
            any(
                "inject-review-cadence-rules.sh" in command
                for command in session_start_commands
            ),
            session_start_commands,
        )


if __name__ == "__main__":
    unittest.main()

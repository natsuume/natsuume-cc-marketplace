"""pre-push-codex-review plugin の review cadence 契約テスト。

対象は `plugins/pre-push-codex-review/hooks/scripts/manage-review-cadence.mjs`
(以下 cadence script)。cadence script の header docs が定義する契約 (計数対象・
reset 経路・enforcement) を hook JSON I/O を通じて固定し、あわせて hooks.json の
event 登録と plugin README の該当節の記述を固定する。state 隔離は環境変数
`PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT` を各テストの一時ディレクトリへ向ける
ことで行う。

checkpoint 相談 (`cross-model-advisor:codex-advisor-runner` を `<review_cycle_checkpoint>` を
含む request で起動する) が成立しない経路は 2 つある。起動後の失敗は
`PostToolUseFailure` に配信され、1 回で fail-open reset の契機になる。auto mode
classifier による起動拒否は `PermissionDenied` に配信され、1 回目は retry を要求して
state を残し、同じ checkpoint 要求中の 2 回目で fail-open reset の契機になる。

private helper の構成や state ファイル名の形式には結合しない。
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-push-codex-review"
HOOK = PLUGIN / "hooks" / "scripts" / "manage-review-cadence.mjs"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
CADENCE_RULES_PROMPT = PLUGIN / "hooks" / "prompts" / "review-cadence-rules.md"
README = PLUGIN / "README.md"

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_shared_contract():
    """codex-reviewer の文言契約 helper を持つ共有 module を読み込む。

    `import` 文で書くと「sys.path 操作より前に import 文が来る」という lint 制約
    (E402) に抵触するため、既存テストと同じ importlib 経由の明示 import にする。
    """
    return importlib.import_module("test_pre_push_codex_reviewer_bg_recovery")


_shared_contract = _load_shared_contract()

agent_launch_mode_hits = _shared_contract.agent_launch_mode_hits
FORBIDDEN_EXECUTION_TOOL = _shared_contract.FORBIDDEN_EXECUTION_TOOL

PRE_PUSH_CODEX_REVIEWER = "pre-push-codex-review:codex-reviewer"
PRE_MERGE_CODEX_REVIEWER = "pre-merge-codex-review:codex-reviewer"
# codex gate 分離前の旧 namespace。cadence script のサポート対象外であり、計数
# されないことを固定する。
PRE_PUSH_CODEX_REVIEWER_LEGACY = "pre-push-review:codex-reviewer"
FOOTER_COUNTED_REVIEWER = "cross-model-advisor:codex-review-runner"
ADVISOR_CHECKPOINT_RUNNER = "cross-model-advisor:codex-advisor-runner"

REVIEW_CADENCE_LIMIT = 5

# checkpoint 相談の起動拒否が配信される event と、その matcher が受理すべき tool 名。
PERMISSION_DENIED_EVENT = "PermissionDenied"
AGENT_TOOL_NAMES = ("Agent", "Task")
# checkpoint 相談 request を表す marker (これを含む起動の失敗・拒否だけが reset の契機)。
CHECKPOINT_REQUEST_PROMPT = (
    "<task>...</task>\n<review_cycle_checkpoint>...</review_cycle_checkpoint>"
)
ORDINARY_REQUEST_PROMPT = "<task>ordinary advisor consult, no checkpoint marker</task>"
# auto mode classifier が起動を拒否したときの `reason` (判定には使わない)。
PERMISSION_DENIED_REASON = "auto mode classifier rejected this subagent launch"

# Stop の block 文言が checkpoint からの脱出手順として含む語。state file の絶対パスと
# あわせて、cross-model-advisor 未 install 時に state を削除して脱出できることを示す。
STOP_ESCAPE_KEYWORDS = ("cross-model-advisor", "install", "削除")

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


def hook_invocation(hook: dict) -> str:
    """hooks.json の command hook が起動するコマンド行 (`command` + `args`)。

    exec form では実行ファイルが `command`、script パスを含む引数が `args` に分かれる。
    """
    return " ".join([hook["command"], *hook.get("args", [])])


def markdown_section(text: str, level: int, keyword: str) -> str | None:
    """`keyword` を含む見出し (`#` が `level` 個) の行から節末尾までを返す。

    節末尾は同じか上位の階層の次の見出し。見つからなければ None。
    """
    prefix = "#" * level + " "
    child_prefix = "#" * (level + 1)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(prefix) or keyword not in line:
            continue
        body = [line]
        for following in lines[index + 1 :]:
            if following.startswith("#") and not following.startswith(child_prefix):
                break
            body.append(following)
        return "\n".join(body)
    return None


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

    def handback(
        self,
        agent_type: str,
        message: object,
        *,
        session_id: str = "session-a",
        agent_id: str = "agent-a",
        tool_name: str = "SubagentHandback",
        delivered: bool = True,
    ) -> dict[str, object] | None:
        """PostToolUse (SubagentHandback): auto mode で report が hand-back された。"""
        return self.hook_response(
            {
                "hook_event_name": "PostToolUse",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": agent_type,
                "tool_name": tool_name,
                "tool_input": {"message": message},
                "tool_response": {
                    "success": delivered,
                    "message": (
                        "Report delivered to your caller."
                        if delivered
                        else "Nothing was sent: SubagentHandback is not active for this agent."
                    ),
                },
                "tool_use_id": "toolu_test",
            }
        )

    def closing_stop(
        self,
        agent_type: str,
        *,
        session_id: str = "session-a",
        agent_id: str = "agent-a",
    ) -> dict[str, object] | None:
        """auto mode の SubagentStop: last_assistant_message は締めの文だけ。"""
        return self.hook_response(
            {
                "hook_event_name": "SubagentStop",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": agent_type,
                "last_assistant_message": (
                    "Review complete. Report delivered to the parent session."
                ),
                "stop_hook_active": False,
            }
        )

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
        agent_id: str = "codex-review-runner-a",
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
        agent_id: str = "codex-advisor-runner-a",
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

    def permission_denied(
        self,
        *,
        session_id: str = "session-a",
        subagent_type: str | None = ADVISOR_CHECKPOINT_RUNNER,
        prompt: str | None = None,
        tool_name: str = "Agent",
        reason: str = PERMISSION_DENIED_REASON,
    ) -> dict[str, object] | None:
        """PermissionDenied: 起動そのものが拒否された (`is_interrupt` は無い)。"""
        tool_input: dict[str, object] = {}
        if subagent_type is not None:
            tool_input["subagent_type"] = subagent_type
        if prompt is not None:
            tool_input["prompt"] = prompt
        return self.hook_response(
            {
                "hook_event_name": PERMISSION_DENIED_EVENT,
                "session_id": session_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": "toolu_test",
                "reason": reason,
            }
        )

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
    """canonical reviewer と `cross-model-advisor:codex-review-runner` は同一カウンターに合算する。"""

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
            session_id=session_id, agent_id="codex-review-runner-1", job_id="job-1"
        )
        self.assertIsNone(self.main_stop(session_id))

        self.review_runner_stop(
            session_id=session_id, agent_id="codex-review-runner-2", job_id="job-2"
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


class PermissionDeniedFailOpenTest(HookHarness):
    """checkpoint 相談の起動が拒否された (PermissionDenied) ときの retry と reset。

    auto mode classifier は subagent の起動そのものを拒否するため、この経路では
    PostToolUseFailure は発火しない。「checkpoint 相談の拒否」とみなす条件は
    PostToolUseFailure の fail-open reset と同じ (`tool_name` が `Agent` / `Task`、
    `tool_input.subagent_type` が `cross-model-advisor:codex-advisor-runner`、`tool_input.prompt`
    が文字列で `<review_cycle_checkpoint>` を含む、checkpoint 要求中)。

    1 回目の拒否では state を残したまま retry 応答を返し、ユーザ確認後の再起動を促す。
    同じ checkpoint 要求中の 2 回目の拒否で fail-open reset する (拒否が続く環境で
    checkpoint が解除不能な block にならないようにする)。条件に合わない拒否は state を
    変えず無応答で、この回数にも数えない。拒否回数は state とともに消えるため、reset
    後の新しい checkpoint 要求では再び 1 回目から数える。

    回数をどの state フィールドで持つかには結合せず、観測は state の有無 /
    `checkpointRequired` と stdout の応答だけで行う。
    """

    def drive_to_checkpoint(self, session_id: str, *, first_cycle: int = 1) -> None:
        for cycle in range(first_cycle, first_cycle + REVIEW_CADENCE_LIMIT):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        self.assert_stop_blocked(self.main_stop(session_id))

    # -- payload / assertions ----------------------------------------------

    def deny_checkpoint_consultation(
        self, session_id: str
    ) -> dict[str, object] | None:
        return self.permission_denied(
            session_id=session_id, prompt=CHECKPOINT_REQUEST_PROMPT
        )

    def assert_retry_requested(self, response: dict[str, object] | None) -> None:
        self.assertIsNotNone(response, "1 回目の拒否は retry 応答を返す")
        assert response is not None
        hook_output = response["hookSpecificOutput"]
        assert isinstance(hook_output, dict)
        self.assertEqual(PERMISSION_DENIED_EVENT, hook_output["hookEventName"])
        self.assertIs(True, hook_output["retry"])

    def assert_checkpoint_still_required(self, session_id: str) -> None:
        state = self.state_for(session_id)
        self.assertIsNotNone(state, "checkpoint 要求中の state が消えている")
        assert state is not None
        self.assertIs(True, state["checkpointRequired"])
        self.assert_stop_blocked(self.main_stop(session_id))

    def assert_checkpoint_released(self, session_id: str) -> None:
        self.assertIsNone(self.state_for(session_id))
        self.assertIsNone(self.main_stop(session_id))

    def assert_denial_is_a_noop(self, session_id: str, **denial: object) -> None:
        """条件に合わない拒否は state を変えず、応答も返さない。"""
        before = self.state_for(session_id)
        self.assertIsNone(
            self.permission_denied(session_id=session_id, **denial),
            "条件に合わない拒否には応答しない",
        )
        self.assertEqual(before, self.state_for(session_id))

    # -- 拒否の回数に応じた扱い --------------------------------------------

    def test_first_checkpoint_denial_requests_a_retry_without_resetting(self) -> None:
        session_id = "session-denied-first"
        self.drive_to_checkpoint(session_id)
        self.assert_retry_requested(self.deny_checkpoint_consultation(session_id))
        self.assert_checkpoint_still_required(session_id)

    def test_second_checkpoint_denial_resets_the_checkpoint(self) -> None:
        session_id = "session-denied-second"
        self.drive_to_checkpoint(session_id)
        self.assert_retry_requested(self.deny_checkpoint_consultation(session_id))
        self.assertIsNone(
            self.deny_checkpoint_consultation(session_id),
            "reset する拒否には応答しない",
        )
        self.assert_checkpoint_released(session_id)

    def test_denial_count_restarts_after_an_attestation_reset(self) -> None:
        """拒否回数は state とともに消え、次の checkpoint 要求では 1 回目から数える。"""
        session_id = "session-denied-after-attestation"
        self.drive_to_checkpoint(session_id)
        self.assert_retry_requested(self.deny_checkpoint_consultation(session_id))

        self.advisor_runner_stop(
            session_id=session_id, status="success", attestation="satisfied"
        )
        self.assert_checkpoint_released(session_id)

        self.drive_to_checkpoint(session_id, first_cycle=REVIEW_CADENCE_LIMIT + 1)
        self.assert_retry_requested(self.deny_checkpoint_consultation(session_id))
        self.assert_checkpoint_still_required(session_id)

    def test_ordinary_consultation_denial_is_not_counted(self) -> None:
        session_id = "session-denied-ordinary"
        self.drive_to_checkpoint(session_id)
        self.assert_denial_is_a_noop(session_id, prompt=ORDINARY_REQUEST_PROMPT)

        # 直後の checkpoint 相談の拒否は 1 回目として扱われる (まだ reset しない)。
        self.assert_retry_requested(self.deny_checkpoint_consultation(session_id))
        self.assert_checkpoint_still_required(session_id)
        self.assertIsNone(self.deny_checkpoint_consultation(session_id))
        self.assert_checkpoint_released(session_id)

    # -- 条件に合わない拒否 --------------------------------------------------

    def test_missing_subagent_type_is_a_noop(self) -> None:
        session_id = "session-denied-no-subagent-type"
        self.drive_to_checkpoint(session_id)
        self.assert_denial_is_a_noop(
            session_id, subagent_type=None, prompt=CHECKPOINT_REQUEST_PROMPT
        )

    def test_other_subagent_type_is_a_noop(self) -> None:
        session_id = "session-denied-other-runner"
        self.drive_to_checkpoint(session_id)
        self.assert_denial_is_a_noop(
            session_id,
            subagent_type=FOOTER_COUNTED_REVIEWER,
            prompt=CHECKPOINT_REQUEST_PROMPT,
        )

    def test_bash_tool_denial_is_a_noop(self) -> None:
        session_id = "session-denied-bash"
        self.drive_to_checkpoint(session_id)
        self.assert_denial_is_a_noop(
            session_id, tool_name="Bash", prompt=CHECKPOINT_REQUEST_PROMPT
        )

    def test_denial_before_checkpoint_required_is_a_noop(self) -> None:
        session_id = "session-denied-early"
        for cycle in range(1, REVIEW_CADENCE_LIMIT - 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        self.assertIsNotNone(self.state_for(session_id))
        self.assert_denial_is_a_noop(session_id, prompt=CHECKPOINT_REQUEST_PROMPT)


class StopBlockEscapeInstructionTest(HookHarness):
    """Stop の block 文言が、自動 reset が届かない場合の脱出手順を自己完結で示す。

    checkpoint 相談の起動が hook に到達しない形で失敗する環境 (cross-model-advisor 未 install
    による subagent_type の解決失敗等) では自動 reset が発火しないため、block 文言は
    その session の state file の絶対パスと、それを削除して脱出できることを含める。
    """

    def state_file(self) -> Path:
        paths = sorted(self.state_root.rglob("*.json"))
        self.assertEqual(1, len(paths), f"state file を一意に特定できない: {paths}")
        return paths[0]

    def test_stop_block_reason_names_the_state_file_and_the_manual_escape(self) -> None:
        session_id = "session-escape-instruction"
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
                status="pass",
            )
        response = self.main_stop(session_id)
        self.assert_stop_blocked(response)
        assert response is not None
        reason = response["reason"]
        assert isinstance(reason, str)
        self.assertIn(
            str(self.state_file()),
            reason,
            "block 文言に この session の state file の絶対パスを含める",
        )
        for keyword in STOP_ESCAPE_KEYWORDS:
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, reason)


class SubagentStartGuardTest(HookHarness):
    def test_uncounted_agent_type_is_not_recorded(self) -> None:
        session_id = "session-uncounted-start"
        for agent_type in (
            FOOTER_COUNTED_REVIEWER,
            ADVISOR_CHECKPOINT_RUNNER,
            PRE_PUSH_CODEX_REVIEWER_LEGACY,
            "cross-model-advisor:codex-rescue-runner",
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
                hook_invocation(hook)
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
            "cross-model-advisor:codex-rescue-runner",
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
            "cross-model-advisor:codex-rescue-runner",
            "pre-push-codex-review:code-reviewer",
        )
        for agent_type in accepted:
            with self.subTest(agent_type=agent_type):
                self.assertIsNotNone(pattern.fullmatch(agent_type))
        for agent_type in rejected:
            with self.subTest(agent_type=agent_type):
                self.assertIsNone(pattern.fullmatch(agent_type))

    def test_permission_denied_matcher_covers_the_agent_launch_tools(self) -> None:
        hooks = self._manifest()
        self.assertIn(
            PERMISSION_DENIED_EVENT,
            hooks,
            f"hooks.json に {PERMISSION_DENIED_EVENT} event が無い",
        )
        entry = self._matcher_entry_for(
            hooks, PERMISSION_DENIED_EVENT, "manage-review-cadence.mjs"
        )
        pattern = re.compile(entry["matcher"])
        for tool_name in AGENT_TOOL_NAMES:
            with self.subTest(tool_name=tool_name):
                self.assertIsNotNone(
                    pattern.fullmatch(tool_name),
                    f"matcher {entry['matcher']!r} が {tool_name} に一致しない",
                )

    def test_remaining_lifecycle_events_are_registered(self) -> None:
        hooks = self._manifest()
        for event in (
            "PreToolUse",
            "PostToolUseFailure",
            PERMISSION_DENIED_EVENT,
            "Stop",
            "SessionEnd",
        ):
            with self.subTest(event=event):
                commands = [
                    hook_invocation(hook)
                    for entry in hooks.get(event, [])
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
            hook_invocation(hook)
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


class SubagentHandbackReportTest(HookHarness):
    """auto mode の hand-back された report (PostToolUse) を SubagentStop が消費する。"""

    def test_handback_status_line_counts_at_closing_stop(self) -> None:
        session_id = "session-handback"
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            agent_id = f"reviewer-{cycle}"
            self.status_line_start(
                PRE_PUSH_CODEX_REVIEWER, session_id=session_id, agent_id=agent_id
            )
            self.handback(
                PRE_PUSH_CODEX_REVIEWER,
                "# Codex Review\n\nStatus: pass\nFindings: 0",
                session_id=session_id,
                agent_id=agent_id,
            )
            state = self.state_for(session_id)
            assert state is not None
            self.assertIn(agent_id, state["handbackReports"])
            self.closing_stop(
                PRE_PUSH_CODEX_REVIEWER, session_id=session_id, agent_id=agent_id
            )
            state = self.state_for(session_id)
            assert state is not None
            self.assertEqual(cycle, state["completedReviews"])
            self.assertNotIn(agent_id, state["handbackReports"])
        self.assert_stop_blocked(self.main_stop(session_id))

    def test_handback_report_takes_precedence_over_closing_text(self) -> None:
        session_id = "session-precedence"
        self.status_line_start(PRE_MERGE_CODEX_REVIEWER, session_id=session_id)
        self.handback(
            PRE_MERGE_CODEX_REVIEWER,
            "# Codex Review\n\nStatus: execution-failed\n",
            session_id=session_id,
        )
        # 締めの文に Status: pass が混じっていても hand-back の解析値が優先される。
        self.status_line_stop(
            PRE_MERGE_CODEX_REVIEWER, "pass", session_id=session_id
        )
        state = self.state_for(session_id)
        self.assertTrue(state is None or state["completedReviews"] == 0)

    def test_duplicate_handback_is_not_counted(self) -> None:
        session_id = "session-duplicate"
        report = "# Codex Review\n\nStatus: pass\nFindings: 0"
        self.status_line_start(PRE_PUSH_CODEX_REVIEWER, session_id=session_id)
        self.handback(PRE_PUSH_CODEX_REVIEWER, report, session_id=session_id)
        self.handback(PRE_PUSH_CODEX_REVIEWER, report, session_id=session_id)
        self.closing_stop(PRE_PUSH_CODEX_REVIEWER, session_id=session_id)
        state = self.state_for(session_id)
        self.assertTrue(state is None or state["completedReviews"] == 0)

    def test_handback_of_untracked_agent_or_tool_is_ignored(self) -> None:
        session_id = "session-untracked"
        report = "# Codex Review\n\nStatus: pass\nFindings: 0"
        self.handback(
            PRE_PUSH_CODEX_REVIEWER_LEGACY, report, session_id=session_id
        )
        self.handback(
            PRE_PUSH_CODEX_REVIEWER,
            report,
            session_id=session_id,
            tool_name="Write",
        )
        self.assertIsNone(self.state_for(session_id))

    def test_undelivered_handback_is_not_recorded(self) -> None:
        session_id = "session-undelivered"
        report = "# Codex Review\n\nStatus: pass\nFindings: 0"
        self.status_line_start(PRE_PUSH_CODEX_REVIEWER, session_id=session_id)
        self.handback(
            PRE_PUSH_CODEX_REVIEWER, report, session_id=session_id, delivered=False
        )
        state = self.state_for(session_id)
        assert state is not None
        self.assertEqual({}, state["handbackReports"])
        # 未配信時は plain text での報告 (last_assistant_message) が計数される。
        self.status_line_stop(PRE_PUSH_CODEX_REVIEWER, "pass", session_id=session_id)
        state = self.state_for(session_id)
        assert state is not None
        self.assertEqual(1, state["completedReviews"])

    def test_orphan_handback_record_is_consumed_when_agent_is_not_active(
        self,
    ) -> None:
        session_id = "session-orphan"
        report = "# Codex Review\n\nStatus: pass\nFindings: 0"
        # 起動記録の無い reviewer (checkpoint reset 後の stop 等) の hand-back。
        self.handback(PRE_PUSH_CODEX_REVIEWER, report, session_id=session_id)
        state = self.state_for(session_id)
        assert state is not None
        self.assertIn("agent-a", state["handbackReports"])
        self.closing_stop(PRE_PUSH_CODEX_REVIEWER, session_id=session_id)
        # 計数はされず (起動記録が無い)、記録は消費されて state も残らない。
        self.assertIsNone(self.state_for(session_id))

    def test_handback_review_runner_footer_counts_at_closing_stop(self) -> None:
        session_id = "session-runner-handback"
        message = "\n".join(
            ["Codex review report", *self.footer_lines("review", "success")]
        )
        self.handback(
            FOOTER_COUNTED_REVIEWER,
            message,
            session_id=session_id,
            agent_id="codex-review-runner-a",
        )
        self.closing_stop(
            FOOTER_COUNTED_REVIEWER,
            session_id=session_id,
            agent_id="codex-review-runner-a",
        )
        state = self.state_for(session_id)
        assert state is not None
        self.assertEqual(1, state["completedReviews"])
        self.assertEqual({}, state["handbackReports"])

    def test_review_runner_is_counted_once_per_agent_id(self) -> None:
        session_id = "session-runner-once"
        message = "\n".join(
            ["Codex review report", *self.footer_lines("review", "success")]
        )
        self.handback(
            FOOTER_COUNTED_REVIEWER,
            message,
            session_id=session_id,
            agent_id="codex-review-runner-a",
        )
        self.closing_stop(
            FOOTER_COUNTED_REVIEWER,
            session_id=session_id,
            agent_id="codex-review-runner-a",
        )
        # resume 再 stop で footer 付き plain text が来ても同じ agent_id は加算しない。
        self.review_runner_stop(session_id=session_id, agent_id="codex-review-runner-a")
        state = self.state_for(session_id)
        assert state is not None
        self.assertEqual(1, state["completedReviews"])
        self.assertEqual(["codex-review-runner-a"], state["countedRunnerAgentIds"])
        # 別 agent_id の runner は計数される。
        self.review_runner_stop(session_id=session_id, agent_id="codex-review-runner-b")
        state = self.state_for(session_id)
        assert state is not None
        self.assertEqual(2, state["completedReviews"])

    def test_handback_advisor_attestation_resets_at_closing_stop(self) -> None:
        session_id = "session-advisor-handback"
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
            )
        self.assert_stop_blocked(self.main_stop(session_id))
        message = "\n".join(
            [
                "Codex advisor report",
                "Codex-Advisor-Review-Cadence: satisfied",
                *self.footer_lines("advisor", "success"),
            ]
        )
        self.handback(
            ADVISOR_CHECKPOINT_RUNNER,
            message,
            session_id=session_id,
            agent_id="codex-advisor-runner-a",
        )
        self.closing_stop(
            ADVISOR_CHECKPOINT_RUNNER,
            session_id=session_id,
            agent_id="codex-advisor-runner-a",
        )
        self.assertIsNone(self.state_for(session_id))
        self.assertIsNone(self.main_stop(session_id))

    def test_handback_advisor_without_attestation_does_not_reset(self) -> None:
        session_id = "session-advisor-noattest"
        for cycle in range(1, REVIEW_CADENCE_LIMIT + 1):
            self.complete_status_line_review(
                PRE_PUSH_CODEX_REVIEWER,
                session_id=session_id,
                agent_id=f"reviewer-{cycle}",
            )
        message = "\n".join(
            ["Codex advisor report", *self.footer_lines("advisor", "success")]
        )
        self.handback(
            ADVISOR_CHECKPOINT_RUNNER,
            message,
            session_id=session_id,
            agent_id="codex-advisor-runner-a",
        )
        self.closing_stop(
            ADVISOR_CHECKPOINT_RUNNER,
            session_id=session_id,
            agent_id="codex-advisor-runner-a",
        )
        state = self.state_for(session_id)
        assert state is not None
        self.assertEqual(REVIEW_CADENCE_LIMIT, state["completedReviews"])
        self.assertEqual({}, state["handbackReports"])
        self.assert_stop_blocked(self.main_stop(session_id))



class HooksManifestHandbackTest(HooksManifestContractTest):
    def test_post_tool_use_handback_is_registered(self) -> None:
        hooks = self._manifest()
        entry = self._matcher_entry_for(
            hooks, "PostToolUse", "manage-review-cadence.mjs"
        )
        self.assertEqual("^SubagentHandback$", entry["matcher"])


class CheckpointLaunchInstructionTest(unittest.TestCase):
    """checkpoint runner の起動案内が現行の Agent tool の起動仕様に沿うこと。

    Agent tool は起動 mode を選ぶパラメータを受け付けないため、SessionStart 注入文と
    cadence script の deny / Stop 文言はその指定を指示せず、`model` だけを明示する。
    Bash tool の同名 option は現行仕様でも有効なので、検査対象は Agent / subagent を
    名指しする行に限る。
    """

    def assert_no_launch_mode_parameter(self, path: Path) -> None:
        hits = agent_launch_mode_hits(path.read_text(encoding="utf-8"))
        self.assertEqual(
            hits, [], f"{path}: Agent 起動指示の起動 mode 指定が残っている"
        )

    def test_injected_rules_omit_launch_mode_parameter(self) -> None:
        self.assert_no_launch_mode_parameter(CADENCE_RULES_PROMPT)

    def test_cadence_script_messages_omit_launch_mode_parameter(self) -> None:
        self.assert_no_launch_mode_parameter(HOOK)

    def test_injected_rules_declare_checkpoint_runner_model(self) -> None:
        text = CADENCE_RULES_PROMPT.read_text(encoding="utf-8")
        self.assertIn('`model: "sonnet"`', text)

    def test_injected_rules_require_user_confirmation_before_denied_retry(
        self,
    ) -> None:
        """classifier 拒否 (PermissionDenied) 後の再起動前にユーザ確認を求める指示が、
        hook の stderr ではなく注入 prompt 自体に書かれている。"""
        text = CADENCE_RULES_PROMPT.read_text(encoding="utf-8")
        for needle in ("PermissionDenied", "`AskUserQuestion`", "2 回目"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_injected_rules_never_mention_a_second_execution_tool(self) -> None:
        """subagent のコマンド実行経路は Bash tool 1 本に閉じる。"""
        hits = [
            f"L{number}: {line.strip()[:120]}"
            for number, line in enumerate(
                CADENCE_RULES_PROMPT.read_text(encoding="utf-8").splitlines(),
                start=1,
            )
            if FORBIDDEN_EXECUTION_TOOL in line
        ]
        self.assertEqual(
            hits, [], f"{FORBIDDEN_EXECUTION_TOOL} の言及が残っている"
        )


class ReviewCadenceDocumentationTest(unittest.TestCase):
    """README が 2 つの fail-open 契機と、手動での脱出手順を書く。"""

    HOOK_LIST_SECTION = ("manage-review-cadence", 4)
    CHECKPOINT_SECTION = ("checkpoint", 3)
    CODEX_ADVISOR_SECTION = ("cross-model-advisor 連携", 3)

    def section(self, keyword: str, level: int) -> str:
        body = markdown_section(
            README.read_text(encoding="utf-8"), level, keyword
        )
        if body is None:
            self.fail(f"{README}: {keyword!r} を含む level {level} の見出しが無い")
        return body

    def test_hook_list_documents_the_permission_denied_entry(self) -> None:
        section = self.section(*self.HOOK_LIST_SECTION)
        bullets = [
            line
            for line in section.splitlines()
            if line.startswith(f"- **{PERMISSION_DENIED_EVENT}**")
        ]
        self.assertTrue(
            bullets,
            f"{README}: hook 一覧に '- **{PERMISSION_DENIED_EVENT}**' の行が無い",
        )

    def test_fail_open_conditions_mention_both_delivery_events(self) -> None:
        for keyword, level in (self.CHECKPOINT_SECTION, self.CODEX_ADVISOR_SECTION):
            section = self.section(keyword, level)
            for event in ("PostToolUseFailure", PERMISSION_DENIED_EVENT):
                with self.subTest(section=keyword, event=event):
                    self.assertIn(
                        event,
                        section,
                        f"{README}: {keyword!r} 節の fail-open 条件に {event} が無い",
                    )

    def test_checkpoint_section_documents_the_manual_escape(self) -> None:
        section = self.section(*self.CHECKPOINT_SECTION)
        for keyword in ("install", "削除"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, section)


if __name__ == "__main__":
    unittest.main()

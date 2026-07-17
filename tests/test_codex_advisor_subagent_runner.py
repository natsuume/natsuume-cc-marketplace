"""codex-advisor の Codex runner 強制・復旧契約テスト (issue #291)。

Phase A では次の public seam を固定する。

1. hook JSON I/O: Codex model 起動を role 固有 runner に限定し、direct deny から
   SubagentStart / SubagentStop / Stop までの session-scoped state を遷移させる。
2. plugin artifact: rescue / review / advisor runner と consult Skill が、foreground
   subagent・companion job ID・status/result recovery の契約を明示する。

private helper の構成、poll 回数、state file 名には結合しない。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "codex-advisor"
HOOK = PLUGIN / "hooks" / "scripts" / "manage-codex-runners.mjs"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
RULES = PLUGIN / "hooks" / "prompts" / "advisor-rules.md"
CONSULT = PLUGIN / "skills" / "consult" / "SKILL.md"

RUNNERS = {
    "rescue": "codex-advisor:rescue-runner",
    "review": "codex-advisor:review-runner",
    "advisor": "codex-advisor:advisor-runner",
}

COMMANDS = {
    "rescue": (
        'node "/opt/claude/plugins/openai-codex/scripts/codex-companion.mjs" '
        "task --background --json --prompt-file /tmp/prompt.md"
    ),
    "review": (
        'node "/opt/claude/plugins/openai-codex/scripts/codex-companion.mjs" '
        "review --wait --scope branch"
    ),
    "adversarial-review": (
        'node "/opt/claude/plugins/openai-codex/scripts/codex-companion.mjs" '
        "adversarial-review --wait --scope branch"
    ),
    "advisor": (
        'bash "/opt/claude/plugins/codex-advisor/scripts/'
        'run-codex-advisor.sh" < "/tmp/prompt.md"'
    ),
}


class HookHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state_root = Path(self.temporary.name) / "state"

    def run_hook(
        self,
        payload: dict[str, object],
    ) -> subprocess.CompletedProcess[str]:
        if not HOOK.is_file():
            self.fail(f"Phase B hook is missing: {HOOK}")
        env = os.environ.copy()
        env["CODEX_ADVISOR_STATE_ROOT"] = str(self.state_root)
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
        self,
        payload: dict[str, object],
    ) -> dict[str, object] | None:
        result = self.run_hook(payload)
        self.assertEqual(0, result.returncode, result.stderr)
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)

    def bash_payload(
        self,
        command: str,
        *,
        session_id: str = "session-a",
        agent_type: str | None = None,
        run_in_background: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {
                "command": command,
                "run_in_background": run_in_background,
            },
        }
        if agent_type is not None:
            payload["agent_type"] = agent_type
        return payload

    def state_records(self) -> list[dict[str, object]]:
        if not self.state_root.exists():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.state_root.rglob("*.json"))
        ]

    def assert_denied(
        self,
        response: dict[str, object] | None,
        expected_runner: str,
    ) -> None:
        self.assertIsNotNone(response)
        assert response is not None
        hook_output = response["hookSpecificOutput"]
        assert isinstance(hook_output, dict)
        self.assertEqual("deny", hook_output["permissionDecision"])
        self.assertIn(expected_runner, hook_output["permissionDecisionReason"])

    def assert_stop_blocked(
        self,
        response: dict[str, object] | None,
        expected_runner: str,
    ) -> None:
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual("block", response["decision"])
        self.assertIn(expected_runner, response["reason"])

    def subagent_start(
        self,
        operation: str,
        *,
        session_id: str = "session-a",
        agent_id: str = "agent-a",
    ) -> dict[str, object] | None:
        return self.hook_response(
            {
                "hook_event_name": "SubagentStart",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": RUNNERS[operation],
            }
        )

    def subagent_stop(
        self,
        operation: str,
        status: str,
        *,
        session_id: str = "session-a",
        agent_id: str = "agent-a",
        job_id: str = "task-example",
    ) -> dict[str, object] | None:
        report = "\n".join(
            [
                "Codex runner report",
                f"Codex-Runner-Operation: {operation}",
                f"Codex-Runner-Status: {status}",
                f"Codex-Runner-Job-ID: {job_id}",
            ]
        )
        return self.hook_response(
            {
                "hook_event_name": "SubagentStop",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": RUNNERS[operation],
                "last_assistant_message": report,
                "stop_hook_active": False,
            }
        )

    def main_stop(self, session_id: str = "session-a") -> dict[str, object] | None:
        return self.hook_response(
            {
                "hook_event_name": "Stop",
                "session_id": session_id,
                "stop_hook_active": False,
            }
        )


class CodexRunnerDirectExecutionGateTest(HookHarness):
    def test_main_session_model_entrypoints_are_denied_and_persist_reroute(self) -> None:
        for operation, command in COMMANDS.items():
            expected_operation = (
                "review" if operation == "adversarial-review" else operation
            )
            with self.subTest(operation=operation):
                session_id = f"session-{operation}"
                response = self.hook_response(
                    self.bash_payload(command, session_id=session_id)
                )
                self.assert_denied(response, RUNNERS[expected_operation])
                records = [
                    record
                    for record in self.state_records()
                    if record["sessionId"] == session_id
                ]
                self.assertEqual(1, len(records))
                self.assertEqual("reroute-required", records[0]["phase"])
                self.assertEqual(expected_operation, records[0]["operation"])

    def test_only_matching_runner_may_start_each_operation(self) -> None:
        for operation in ("rescue", "review", "advisor"):
            command = COMMANDS[operation]
            with self.subTest(operation=operation, role="matching"):
                response = self.hook_response(
                    self.bash_payload(command, agent_type=RUNNERS[operation])
                )
                self.assertIsNone(response)
            with self.subTest(operation=operation, role="wrong"):
                wrong = RUNNERS["advisor" if operation != "advisor" else "review"]
                response = self.hook_response(
                    self.bash_payload(command, agent_type=wrong)
                )
                self.assert_denied(response, RUNNERS[operation])

    def test_legacy_rescue_agent_is_denied_and_rerouted(self) -> None:
        response = self.hook_response(
            self.bash_payload(
                COMMANDS["rescue"], agent_type="codex:codex-rescue"
            )
        )
        self.assert_denied(response, RUNNERS["rescue"])

    def test_background_or_pipeline_runner_execution_is_denied(self) -> None:
        background = self.hook_response(
            self.bash_payload(
                COMMANDS["review"],
                agent_type=RUNNERS["review"],
                run_in_background=True,
            )
        )
        self.assert_denied(background, RUNNERS["review"])

        pipeline = self.hook_response(
            self.bash_payload(
                f'{COMMANDS["advisor"]} | tee /tmp/advice.log',
                agent_type=RUNNERS["advisor"],
            )
        )
        self.assert_denied(pipeline, RUNNERS["advisor"])

    def test_management_and_pre_push_commands_are_allowed(self) -> None:
        commands = [
            (
                'node "/opt/codex-companion.mjs" status task-123 --json',
                None,
            ),
            ('node "/opt/codex-companion.mjs" result task-123', None),
            ('node "/opt/codex-companion.mjs" cancel task-123', None),
            (
                'node "/opt/codex-companion.mjs" task-resume-candidate --json',
                None,
            ),
            (
                "bash /opt/pre-push-review/hooks/scripts/run-codex-review.sh",
                "pre-push-review:codex-reviewer",
            ),
        ]
        for command, agent_type in commands:
            with self.subTest(command=command):
                response = self.hook_response(
                    self.bash_payload(command, agent_type=agent_type)
                )
                self.assertIsNone(response)

    def test_read_only_mentions_do_not_trigger_the_gate(self) -> None:
        commands = [
            "rg -n 'codex-companion.mjs task' plugins tests",
            "git diff -- plugins/codex-advisor/scripts/run-codex-advisor.sh",
            "cat plugins/codex-advisor/scripts/run-codex-advisor.sh",
            "grep -n review /opt/codex-companion.mjs | head -5",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(
                    self.hook_response(self.bash_payload(command))
                )


class CodexRunnerLifecycleTest(HookHarness):
    def test_active_runner_blocks_main_stop_until_success_consumes_state(self) -> None:
        self.subagent_start("rescue")
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertEqual("active", records[0]["phase"])
        self.assertEqual("agent-a", records[0]["agentId"])

        self.assert_stop_blocked(self.main_stop(), RUNNERS["rescue"])
        self.subagent_stop("rescue", "success")
        self.assertEqual([], self.state_records())
        self.assertIsNone(self.main_stop())

    def test_retryable_failure_requires_one_retry_then_becomes_terminal(self) -> None:
        self.subagent_start("review", agent_id="agent-first")
        self.subagent_stop(
            "review", "retryable-failure", agent_id="agent-first"
        )
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertEqual("retry-required", records[0]["phase"])
        self.assertEqual(1, records[0]["retryCount"])
        self.assert_stop_blocked(self.main_stop(), RUNNERS["review"])

        self.subagent_start("review", agent_id="agent-second")
        self.subagent_stop(
            "review", "retryable-failure", agent_id="agent-second"
        )
        self.assertEqual([], self.state_records())
        self.assertIsNone(self.main_stop())

    def test_terminal_failure_and_cancel_do_not_retry(self) -> None:
        for index, status in enumerate(("terminal-failure", "cancelled")):
            session_id = f"terminal-{index}"
            with self.subTest(status=status):
                self.subagent_start("advisor", session_id=session_id)
                self.subagent_stop(
                    "advisor", status, session_id=session_id
                )
                records = [
                    record
                    for record in self.state_records()
                    if record["sessionId"] == session_id
                ]
                self.assertEqual([], records)
                self.assertIsNone(self.main_stop(session_id))

    def test_malformed_runner_report_is_retryable_but_bounded(self) -> None:
        self.subagent_start("rescue")
        payload = {
            "hook_event_name": "SubagentStop",
            "session_id": "session-a",
            "agent_id": "agent-a",
            "agent_type": RUNNERS["rescue"],
            "last_assistant_message": "report without runner footer",
            "stop_hook_active": False,
        }
        self.hook_response(payload)
        self.assertEqual("retry-required", self.state_records()[0]["phase"])

        self.subagent_start("rescue", agent_id="agent-b")
        payload["agent_id"] = "agent-b"
        self.hook_response(payload)
        self.assertEqual([], self.state_records())

    def test_session_end_cleans_only_its_own_state(self) -> None:
        self.subagent_start("rescue", session_id="session-a")
        self.subagent_start("review", session_id="session-b")
        self.hook_response(
            {
                "hook_event_name": "SessionEnd",
                "session_id": "session-a",
            }
        )
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertEqual("session-b", records[0]["sessionId"])


class CodexRunnerArtifactContractTest(unittest.TestCase):
    def test_three_role_specific_runner_agents_are_declared(self) -> None:
        for operation, scoped_name in RUNNERS.items():
            with self.subTest(operation=operation):
                path = PLUGIN / "agents" / f"{operation}-runner.md"
                self.assertTrue(path.is_file(), path)
                contents = path.read_text(encoding="utf-8")
                self.assertIn(f"name: {operation}-runner", contents)
                self.assertIn("run_in_background: false", contents)
                self.assertIn("TaskOutput", contents)
                self.assertIn("status", contents)
                self.assertIn("result", contents)
                self.assertIn("Codex-Runner-Operation:", contents)
                self.assertIn("Codex-Runner-Status:", contents)
                self.assertIn(scoped_name, RULES.read_text(encoding="utf-8"))

    def test_rescue_and_advisor_require_prompt_file_transport(self) -> None:
        for operation in ("rescue", "advisor"):
            contents = (
                PLUGIN / "agents" / f"{operation}-runner.md"
            ).read_text(encoding="utf-8")
            with self.subTest(operation=operation):
                self.assertIn("prompt file", contents)
                self.assertIn("--background", contents)
                self.assertNotIn("<<'EOF'", contents)

    def test_review_runner_defines_job_set_recovery_without_guessing(self) -> None:
        contents = (PLUGIN / "agents" / "review-runner.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("job", contents)
        self.assertIn("差分", contents)
        self.assertIn("0 件", contents)
        self.assertIn("複数", contents)
        self.assertIn("推測", contents)

    def test_consult_routes_through_advisor_runner(self) -> None:
        contents = CONSULT.read_text(encoding="utf-8")
        self.assertIn(RUNNERS["advisor"], contents)
        self.assertIn("Agent", contents)
        self.assertIn("run_in_background: false", contents)
        self.assertNotIn(
            'bash "<plugin-root>/scripts/run-codex-advisor.sh" <', contents
        )

    def test_hook_manifest_covers_gate_and_lifecycle_events(self) -> None:
        manifest = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        hooks = manifest["hooks"]
        for event in (
            "SessionStart",
            "SessionEnd",
            "PreToolUse",
            "SubagentStart",
            "SubagentStop",
            "Stop",
        ):
            with self.subTest(event=event):
                commands = [
                    hook["command"]
                    for entry in hooks[event]
                    for hook in entry["hooks"]
                    if hook["type"] == "command"
                ]
                self.assertTrue(
                    any("manage-codex-runners.mjs" in command for command in commands),
                    commands,
                )


if __name__ == "__main__":
    unittest.main()

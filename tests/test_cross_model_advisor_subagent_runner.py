"""cross-model-advisor の Codex runner 強制・復旧契約テスト (issue #291)。

Phase A では次の public seam を固定する。

1. hook JSON I/O: Codex model 起動を role 固有 runner に限定し、direct deny から
   SubagentStart / SubagentStop / Stop までの session-scoped state を遷移させる。
2. plugin artifact: rescue / review / advisor runner と consult Skill が、subagent 内での
   model 起動・companion job ID・status/result recovery の契約を明示する。

private helper の構成、poll 回数、state file 名には結合しない。
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
PLUGIN = ROOT / "plugins" / "cross-model-advisor"
HOOK = PLUGIN / "hooks" / "scripts" / "manage-codex-runners.mjs"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
RULES = PLUGIN / "hooks" / "prompts" / "advisor-rules.md"
CONSULT = PLUGIN / "skills" / "consult" / "SKILL.md"
JOB_HELPER = PLUGIN / "scripts" / "run-codex-job.sh"

RUNNERS = {
    "rescue": "cross-model-advisor:codex-rescue-runner",
    "review": "cross-model-advisor:codex-review-runner",
    "advisor": "cross-model-advisor:codex-advisor-runner",
}
# レビュー系 subagent の namespace。review の起動・計数は pre-push-codex-review /
# pre-merge-codex-review plugin の責務であり、cross-model-advisor の SubagentStart /
# SubagentStop matcher・hooks.json はこれらに関知しない (マッチしない)。
PRE_PUSH_CODEX_REVIEWER = "pre-push-codex-review:codex-reviewer"
PRE_PUSH_CODEX_REVIEWER_LEGACY = "pre-push-review:codex-reviewer"
PRE_MERGE_CODEX_REVIEWER = "pre-merge-codex-review:codex-reviewer"
# cross-model-advisor 自身の PreToolUse gate (classifyModelLaunch) が分類しないコマンド例
# として使う、pre-push-codex-review plugin が所有する codex review wrapper。
PRE_PUSH_CODEX_WRAPPER = (
    "/opt/pre-push-codex-review/hooks/scripts/run-pre-push-codex-review.sh"
)
PRE_PUSH_CODEX_WRAPPER_COMMAND = f"bash {PRE_PUSH_CODEX_WRAPPER}"

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
        'bash "/opt/claude/plugins/cross-model-advisor/scripts/'
        'run-codex-advisor.sh" < "/tmp/prompt.md"'
    ),
}


def hook_invocation(hook: dict) -> str:
    """hooks.json の command hook が起動するコマンド行 (`command` + `args`)。

    exec form では実行ファイルが `command`、script パスを含む引数が `args` に分かれる。
    """
    return " ".join([hook["command"], *hook.get("args", [])])


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
        review_cadence: str | None = None,
    ) -> dict[str, object] | None:
        report_lines = ["Codex runner report"]
        if review_cadence is None and operation == "advisor":
            review_cadence = "not-applicable"
        if review_cadence is not None:
            report_lines.append(
                f"Codex-Advisor-Review-Cadence: {review_cadence}"
            )
        report_lines.extend(
            [
                f"Codex-Runner-Operation: {operation}",
                f"Codex-Runner-Status: {status}",
                f"Codex-Runner-Job-ID: {job_id}",
            ]
        )
        report = "\n".join(report_lines)
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

    def handback(
        self,
        operation: str,
        status: str,
        *,
        session_id: str = "session-a",
        agent_id: str = "agent-a",
        job_id: str = "task-example",
        review_cadence: str | None = None,
        tool_name: str = "SubagentHandback",
        delivered: bool = True,
    ) -> dict[str, object] | None:
        """PostToolUse (SubagentHandback): auto mode で report が hand-back された。"""
        report_lines = ["Codex runner report"]
        if review_cadence is None and operation == "advisor":
            review_cadence = "not-applicable"
        if review_cadence is not None:
            report_lines.append(
                f"Codex-Advisor-Review-Cadence: {review_cadence}"
            )
        report_lines.extend(
            [
                f"Codex-Runner-Operation: {operation}",
                f"Codex-Runner-Status: {status}",
                f"Codex-Runner-Job-ID: {job_id}",
            ]
        )
        return self.hook_response(
            {
                "hook_event_name": "PostToolUse",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": RUNNERS[operation],
                "tool_name": tool_name,
                "tool_input": {"message": "\n".join(report_lines)},
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
        operation: str,
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
                "agent_type": RUNNERS[operation],
                "last_assistant_message": (
                    "Runner finished. Report delivered to the parent session."
                ),
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
                PRE_PUSH_CODEX_WRAPPER_COMMAND,
                PRE_PUSH_CODEX_REVIEWER,
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
            "git diff -- plugins/cross-model-advisor/scripts/run-codex-advisor.sh",
            "cat plugins/cross-model-advisor/scripts/run-codex-advisor.sh",
            "grep -n review /opt/codex-companion.mjs | head -5",
            "echo 'node /opt/codex-companion.mjs task --background'",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(
                    self.hook_response(self.bash_payload(command))
                )

    def test_wrapped_executable_launch_is_still_denied(self) -> None:
        for command in (
            "CODEX_MODE=rescue node /opt/codex-companion.mjs task --background",
            "timeout 600 node /opt/codex-companion.mjs review --wait",
        ):
            with self.subTest(command=command):
                response = self.hook_response(self.bash_payload(command))
                expected = RUNNERS[
                    "rescue" if " task " in command else "review"
                ]
                self.assert_denied(response, expected)

    def test_state_write_failure_remains_a_deny_and_is_disclosed(self) -> None:
        self.state_root.parent.mkdir(parents=True, exist_ok=True)
        self.state_root.write_text("not a directory", encoding="utf-8")
        response = self.hook_response(self.bash_payload(COMMANDS["rescue"]))
        self.assert_denied(response, RUNNERS["rescue"])
        assert response is not None
        hook_output = response["hookSpecificOutput"]
        assert isinstance(hook_output, dict)
        self.assertIn(
            "自動復旧要求を保存できていません",
            hook_output["permissionDecisionReason"],
        )


class CodexRunnerHeredocGateTest(HookHarness):
    """heredoc を含む command の gate 判定 (#464)。

    command 全体が「本文をデータとして読む command (cat / tee / python / python3 / node) 1 つ
    + 区切り語を quote した heredoc 1 つ (+ ファイルへのリダイレクト 1 つまで)」だけで構成
    される場合、本文に wrapper 名が文字列として現れても起動ではない。この形から少しでも外れる
    command (shell や未知の command への heredoc、pipeline・process substitution・コマンド
    置換・compound command を伴う形、quote しない区切り語、終端行の後ろに続く行等) は免除せず、
    heredoc の本文も含めてコマンドとして判定する。免除する形でも 1 行目は通常どおり判定する。
    """

    def assert_allowed_without_state(self, command: str) -> None:
        self.assertIsNone(self.hook_response(self.bash_payload(command)), command)
        self.assertEqual([], self.state_records(), command)

    def test_quoted_heredoc_body_is_data(self) -> None:
        cases = {
            "backticks-in-python-string": (
                "python3 - <<'EOF'\n"
                'print("wrapper は `run-codex-advisor.sh` と書く")\n'
                "EOF"
            ),
            "executable-line-single-quoted-delimiter": (
                "cat <<'EOF' > notes.md\n"
                f"{COMMANDS['advisor']}\n"
                "EOF"
            ),
            "executable-line-double-quoted-delimiter": (
                'cat <<"EOF" > notes.md\n'
                f"{COMMANDS['rescue']}\n"
                "EOF"
            ),
            "substitution-in-quoted-body": (
                "cat <<'EOF'\n"
                f"$({COMMANDS['review']})\n"
                "EOF"
            ),
            "tab-stripped-terminator": (
                "cat <<-'EOF'\n"
                f"\t{COMMANDS['rescue']}\n"
                "\tEOF"
            ),
            "apostrophe-in-body": (
                "cat <<'EOF'\n"
                "don't\n"
                f"{COMMANDS['rescue']}\n"
                "EOF"
            ),
            "trailing-newline-after-terminator": (
                "tee notes.md <<'EOF'\n"
                f"{COMMANDS['review']}\n"
                "EOF\n"
            ),
        }
        for name, command in cases.items():
            with self.subTest(case=name):
                self.assert_allowed_without_state(command)

    def test_first_line_of_an_exempt_heredoc_is_still_classified(self) -> None:
        """本文を免除する形でも、1 行目の command 自体が起動なら判定する。"""
        response = self.hook_response(
            self.bash_payload(
                f"{COMMANDS['rescue']} <<'EOF'\n"
                "相談内容\n"
                "EOF"
            )
        )
        self.assert_denied(response, RUNNERS["rescue"])

    def test_heredoc_outside_the_exempt_form_is_classified(self) -> None:
        """免除する形から外れる heredoc は、本文も含めて従来どおり判定する。"""
        cases = {
            "unquoted-delimiter": (
                "cat <<EOF > notes.md\n"
                f"{COMMANDS['advisor']}\n"
                "EOF",
                "advisor",
            ),
            # git / gh は同じ command の中で shell を起動できる (alias 等) ため免除しない。
            "git-commit-message": (
                "git commit -F - <<'EOF'\n"
                f"{COMMANDS['advisor']}\n"
                "EOF",
                "advisor",
            ),
            "gh-body-file-stdin": (
                "gh issue create --title t --body-file - <<'EOF'\n"
                f"{COMMANDS['review']}\n"
                "EOF",
                "review",
            ),
            "redirect-ampersand-then-pipe": (
                "cat <<'EOF' 2>&1 | bash\n"
                f"{COMMANDS['rescue']}\n"
                "EOF",
                "rescue",
            ),
            "brace-group-piped": (
                "{ cat <<'EOF'\n"
                f"{COMMANDS['rescue']}\n"
                "EOF\n"
                "} | bash",
                "rescue",
            ),
            "input-process-substitution": (
                "bash <(cat <<'EOF'\n"
                f"{COMMANDS['rescue']}\n"
                "EOF\n"
                ")",
                "rescue",
            ),
            "output-process-substitution": (
                "cat <<'EOF' > >(bash)\n"
                f"{COMMANDS['rescue']}\n"
                "EOF",
                "rescue",
            ),
            "command-substitution": (
                "$(cat <<'EOF'\n"
                f"{COMMANDS['rescue']}\n"
                "EOF\n"
                ")",
                "rescue",
            ),
            # bash は 1 行目末尾の CR を区切り語の一部 (EOF\r) として扱い、本文は "EOF\r" の行で
            # 終わる。後続の起動行は実行されるため免除しない。
            "carriage-return-in-first-line": (
                "cat <<'EOF'\r\n"
                "EOF\r\n"
                f"{COMMANDS['rescue']}\n"
                "EOF",
                "rescue",
            ),
            "parameter-expansion-before-heredoc": (
                "cat ${x<<X} <<'EOF'\n"
                f"{COMMANDS['rescue']}\n"
                "EOF",
                "rescue",
            ),
        }
        for name, (command, operation) in cases.items():
            with self.subTest(case=name):
                self.assert_denied(
                    self.hook_response(self.bash_payload(command)),
                    RUNNERS[operation],
                )

    def test_heredoc_executed_by_a_shell_is_classified(self) -> None:
        cases = {
            "bash-quoted": (
                "bash <<'EOF'\n"
                f"{COMMANDS['rescue']}\n"
                "EOF",
                "rescue",
            ),
            "sh-s-unquoted": (
                "sh -s <<EOF\n"
                f"{COMMANDS['review']}\n"
                "EOF",
                "review",
            ),
            "wrapped-bash": (
                "sudo bash <<'EOF'\n"
                f"{COMMANDS['advisor']}\n"
                "EOF",
                "advisor",
            ),
        }
        # 本文をデータとして読むと確認できない consumer は、shell が本文を実行しうるものとして
        # 扱う (fail-closed)。
        consumers = {
            "pipe-to-bash": "cat <<'EOF' | bash",
            "tee-pipe-to-sh": "tee log.txt <<'EOF' | sh",
            "exec": "exec bash <<'EOF'",
            "time": "time bash <<'EOF'",
            "nice": "nice bash <<'EOF'",
            "ssh": "ssh host <<'EOF'",
            "fish": "fish <<'EOF'",
            "unknown-command": "some-tool <<'EOF'",
            "brace-group": "{ bash; } <<'EOF'",
            "redirect-first": "<<'EOF' bash",
        }
        for name, head in consumers.items():
            cases[name] = (f"{head}\n{COMMANDS['rescue']}\nEOF", "rescue")
        cases["subshell"] = (
            f"( bash <<'EOF'\n{COMMANDS['rescue']}\nEOF\n)",
            "rescue",
        )
        for name, (command, operation) in cases.items():
            with self.subTest(case=name):
                self.assert_denied(
                    self.hook_response(self.bash_payload(command)),
                    RUNNERS[operation],
                )

    def test_unresolvable_heredoc_consumer_is_denied(self) -> None:
        response = self.hook_response(
            self.bash_payload(
                "$SHELL <<'EOF'\n"
                f"{COMMANDS['rescue']}\n"
                "EOF"
            )
        )
        self.assertIsNotNone(response)
        assert response is not None
        hook_output = response["hookSpecificOutput"]
        assert isinstance(hook_output, dict)
        self.assertEqual("deny", hook_output["permissionDecision"])

    def test_substitution_in_unquoted_heredoc_body_is_classified(self) -> None:
        cases = {
            "dollar-paren": (
                "cat <<EOF\n"
                f"$({COMMANDS['rescue']})\n"
                "EOF",
                "rescue",
            ),
            "backticks": (
                "cat <<EOF\n"
                f"`{COMMANDS['review']}`\n"
                "EOF",
                "review",
            ),
        }
        for name, (command, operation) in cases.items():
            with self.subTest(case=name):
                self.assert_denied(
                    self.hook_response(self.bash_payload(command)),
                    RUNNERS[operation],
                )

    def test_commands_around_a_heredoc_are_classified(self) -> None:
        cases = {
            "after-terminator": (
                "cat <<'EOF'\n"
                "note\n"
                "EOF\n"
                f"{COMMANDS['rescue']}",
                "rescue",
            ),
            "same-line-segment": (
                f"cat <<'EOF'; {COMMANDS['advisor']}\n"
                "note\n"
                "EOF",
                "advisor",
            ),
            "second-heredoc-on-the-line-feeds-a-shell": (
                "cat <<'A'; bash <<'B'\n"
                "note\n"
                "A\n"
                f"{COMMANDS['rescue']}\n"
                "B",
                "rescue",
            ),
            "here-string-is-not-a-heredoc": (
                "cat <<< 'note'\n"
                f"{COMMANDS['review']}",
                "review",
            ),
            # 終端行が無い heredoc は heredoc として扱わず、後続行をコマンドとして解析する。
            "arithmetic-shift-is-not-a-heredoc": (
                "echo $(( 1 << 2 ))\n"
                f"{COMMANDS['rescue']}",
                "rescue",
            ),
            "unterminated-heredoc": (
                "cat <<'EOF'\n"
                f"{COMMANDS['advisor']}\n"
                "EOF ",
                "advisor",
            ),
            "heredoc-operator-in-comment": (
                "echo ok # cat <<'EOF'\n"
                f"{COMMANDS['rescue']}\n"
                "EOF",
                "rescue",
            ),
            "escaped-space-before-hash": (
                f"echo a\\ #; {COMMANDS['rescue']}",
                "rescue",
            ),
            "line-continuation-before-hash": (
                f"echo a\\\n#; {COMMANDS['rescue']}",
                "rescue",
            ),
        }
        for name, (command, operation) in cases.items():
            with self.subTest(case=name):
                self.assert_denied(
                    self.hook_response(self.bash_payload(command)),
                    RUNNERS[operation],
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

    def test_handback_success_report_terminates_state_at_closing_stop(self) -> None:
        for operation in ("rescue", "review", "advisor"):
            session_id = f"handback-{operation}"
            with self.subTest(operation=operation):
                self.subagent_start(operation, session_id=session_id)
                self.handback(operation, "success", session_id=session_id)
                records = [
                    record
                    for record in self.state_records()
                    if record["sessionId"] == session_id
                ]
                self.assertEqual(1, len(records))
                self.assertEqual("active", records[0]["phase"])
                self.assertEqual(
                    "success", records[0]["handback"]["footer"]["status"]
                )
                self.closing_stop(operation, session_id=session_id)
                records = [
                    record
                    for record in self.state_records()
                    if record["sessionId"] == session_id
                ]
                self.assertEqual([], records)
                self.assertIsNone(self.main_stop(session_id))

    def test_handback_retryable_failure_schedules_one_retry(self) -> None:
        self.subagent_start("review", agent_id="agent-first")
        self.handback("review", "retryable-failure", agent_id="agent-first")
        self.closing_stop("review", agent_id="agent-first")
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertEqual("retry-required", records[0]["phase"])
        self.assertIsNone(records[0]["handback"])
        self.assert_stop_blocked(self.main_stop(), RUNNERS["review"])

    def test_handback_takes_precedence_over_closing_text(self) -> None:
        self.subagent_start("rescue")
        self.handback("rescue", "terminal-failure")
        # 締めの文が success footer を装っても hand-back の解析値が優先される
        # (terminal-failure なので retry せず state を消す)。
        self.subagent_stop("rescue", "success")
        self.assertEqual([], self.state_records())

    def test_handback_advisor_without_attestation_line_is_retry_required(
        self,
    ) -> None:
        self.subagent_start("advisor")
        # helper は advisor の review_cadence を not-applicable に補完するため、
        # attestation 行を欠く report は直接組み立てる。
        self.hook_response(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "session-a",
                "agent_id": "agent-a",
                "agent_type": RUNNERS["advisor"],
                "tool_name": "SubagentHandback",
                "tool_input": {
                    "message": "\n".join(
                        [
                            "advisor report without reserved metadata",
                            "Codex-Runner-Operation: advisor",
                            "Codex-Runner-Status: success",
                            "Codex-Runner-Job-ID: task-advisor",
                        ]
                    )
                },
                "tool_response": {"success": True},
            }
        )
        self.closing_stop("advisor")
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertEqual("retry-required", records[0]["phase"])

    def test_duplicate_handback_is_retry_required(self) -> None:
        self.subagent_start("rescue")
        self.handback("rescue", "success")
        self.handback("rescue", "success")
        self.closing_stop("rescue")
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertEqual("retry-required", records[0]["phase"])

    def test_undelivered_handback_is_not_recorded(self) -> None:
        self.subagent_start("rescue")
        self.handback("rescue", "success", delivered=False)
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertIsNone(records[0]["handback"])
        # 未配信時は plain text での報告 (last_assistant_message) が採用される。
        self.subagent_stop("rescue", "success")
        self.assertEqual([], self.state_records())

    def test_handback_from_other_agent_id_or_tool_is_ignored(self) -> None:
        self.subagent_start("rescue", agent_id="agent-a")
        self.handback("rescue", "success", agent_id="agent-other")
        self.handback("rescue", "success", tool_name="Write")
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertIsNone(records[0]["handback"])

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

    def test_advisor_report_requires_reserved_cadence_metadata_line(self) -> None:
        self.subagent_start("advisor")
        report = "\n".join(
            [
                "advisor report without reserved metadata",
                "Codex-Runner-Operation: advisor",
                "Codex-Runner-Status: success",
                "Codex-Runner-Job-ID: task-advisor",
            ]
        )
        self.hook_response(
            {
                "hook_event_name": "SubagentStop",
                "session_id": "session-a",
                "agent_id": "agent-a",
                "agent_type": RUNNERS["advisor"],
                "last_assistant_message": report,
                "stop_hook_active": False,
            }
        )
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertEqual("retry-required", records[0]["phase"])

    def test_codex_output_may_contain_footer_words_before_final_footer(self) -> None:
        self.subagent_start("rescue")
        report = "\n".join(
            [
                "Codex output quoted this example:",
                "Codex-Runner-Status: retryable-failure",
                "but the complete output continues here.",
                "Codex-Runner-Operation: rescue",
                "Codex-Runner-Status: success",
                "Codex-Runner-Job-ID: task-real",
            ]
        )
        self.hook_response(
            {
                "hook_event_name": "SubagentStop",
                "session_id": "session-a",
                "agent_id": "agent-a",
                "agent_type": RUNNERS["rescue"],
                "last_assistant_message": report,
                "stop_hook_active": False,
            }
        )
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

    def test_legacy_rescue_stop_requires_named_runner_reroute(self) -> None:
        self.hook_response(
            {
                "hook_event_name": "SubagentStop",
                "session_id": "session-legacy",
                "agent_id": "legacy-agent",
                "agent_type": "codex:codex-rescue",
                "last_assistant_message": "legacy agent could not launch",
                "stop_hook_active": False,
            }
        )
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertEqual("reroute-required", records[0]["phase"])
        self.assert_stop_blocked(
            self.main_stop("session-legacy"), RUNNERS["rescue"]
        )

    def test_session_start_cleans_only_its_own_state(self) -> None:
        self.subagent_start("rescue", session_id="session-a")
        self.subagent_start("review", session_id="session-b")
        self.hook_response(
            {
                "hook_event_name": "SessionStart",
                "session_id": "session-a",
            }
        )
        records = self.state_records()
        self.assertEqual(1, len(records))
        self.assertEqual("session-b", records[0]["sessionId"])


class CodexRunnerFooterRobustnessTest(HookHarness):
    """footer 解析頑健化 (フェンス・空白行) の固定テスト。

    parseRunnerFooter / parseReviewCadenceAttestation は、footer をコード
    フェンス (``` / ~~~) で囲んだり footer 行間に空白行を挟んだりしても、
    末尾からフェンス行・空白行を無視した実質末尾の連続 3 行 (attestation は
    その直前の実質行) として照合する。本クラスはその挙動を固定する。
    """

    def stop_with_report(
        self,
        operation: str,
        report: str,
        *,
        session_id: str = "session-a",
        agent_id: str = "agent-a",
    ) -> dict[str, object] | None:
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

    @staticmethod
    def footer_lines(
        operation: str, status: str, job_id: str = "task-example"
    ) -> list[str]:
        return [
            f"Codex-Runner-Operation: {operation}",
            f"Codex-Runner-Status: {status}",
            f"Codex-Runner-Job-ID: {job_id}",
        ]

    def records_for(
        self, session_id: str, operation: str
    ) -> list[dict[str, object]]:
        return [
            record
            for record in self.state_records()
            if record["sessionId"] == session_id
            and record["operation"] == operation
        ]

    def test_fenced_footer_with_text_language_tag_is_recognized(self) -> None:
        """修正前 fail: rescue runner の footer 3 行を ```text フェンスで
        囲むと、現行実装は lines.slice(-3) が閉じフェンス行 "```" を含んで
        しまい照合に失敗し retry-required のまま残る (実測で確認)。Phase B
        後は success 終端となり state record が削除される。
        """
        self.subagent_start("rescue")
        report = "\n".join(
            [
                "Codex runner report",
                "```text",
                *self.footer_lines("rescue", "success"),
                "```",
            ]
        )
        self.stop_with_report("rescue", report)
        self.assertEqual([], self.records_for("session-a", "rescue"))

    def test_fenced_attestation_and_footer_together_is_recognized(self) -> None:
        """修正前 fail: advisor runner の attestation 行と footer 3 行を
        まとめてフェンスで囲むと、末尾の閉じフェンス行が全 tail オフセットを
        1 行ずらすため、footer 3 行照合 (lines.slice(-3) が footer の後半 2 行
        + 閉じフェンス行を指す) も attestation 参照 (lines.at(-4) が閉じ
        フェンス行ではなく Codex-Runner-Operation 行を指す) も意図した行を
        外し、現行実装では retry-required のまま残る (実測で確認)。Phase B
        後は success 終端となる。
        """
        self.subagent_start("advisor")
        report = "\n".join(
            [
                "Codex runner report",
                "```text",
                "Codex-Advisor-Review-Cadence: not-applicable",
                *self.footer_lines("advisor", "success"),
                "```",
            ]
        )
        self.stop_with_report("advisor", report)
        self.assertEqual([], self.records_for("session-a", "advisor"))

    def test_trailing_blank_lines_after_footer_are_tolerated(self) -> None:
        """保全 (現行でも pass、実測で確認): footer 3 行の後に空白行が 2 行
        続く場合、現行実装は `message.replace(/\\s+$/, "")` で末尾の空白を
        丸ごと除去するため、フェンスなしのこのケースはすでに success 終端に
        なる。Phase B 後も同じ挙動を維持することを固定する回帰テスト。
        """
        self.subagent_start("rescue")
        report = (
            "\n".join(
                [
                    "Codex runner report",
                    *self.footer_lines("rescue", "success"),
                ]
            )
            + "\n\n\n"
        )
        self.stop_with_report("rescue", report)
        self.assertEqual([], self.records_for("session-a", "rescue"))

    def test_tilde_fenced_footer_is_recognized(self) -> None:
        """修正前 fail: footer を ~~~ フェンスで囲んだ場合も ``` と同様に、
        現行実装では閉じフェンス行が lines.slice(-3) に混入し retry-required
        のまま残る (実測で確認)。Phase B 後は success 終端となる。
        """
        self.subagent_start("rescue")
        report = "\n".join(
            [
                "Codex runner report",
                "~~~",
                *self.footer_lines("rescue", "success"),
                "~~~",
            ]
        )
        self.stop_with_report("rescue", report)
        self.assertEqual([], self.records_for("session-a", "rescue"))

    def test_blank_lines_between_footer_lines_are_tolerated(self) -> None:
        """修正前 fail: footer の各行間に空白行が挟まると、現行実装は
        lines.slice(-3) が空行を含んでしまい照合に失敗し retry-required の
        まま残る (実測で確認)。Phase B 後は空白行を無視した実質末尾 3 行と
        して success 終端になる。
        """
        self.subagent_start("rescue")
        footer = self.footer_lines("rescue", "success")
        report = "\n".join(
            [
                "Codex runner report",
                "",
                footer[0],
                "",
                footer[1],
                "",
                footer[2],
            ]
        )
        self.stop_with_report("rescue", report)
        self.assertEqual([], self.records_for("session-a", "rescue"))

    def test_fenced_review_success_footer_terminates_state(self) -> None:
        """review runner の footer をフェンスで囲んでも success 終端 (record
        削除) になる。
        """
        self.subagent_start("review")
        report = "\n".join(
            [
                "Codex runner report",
                "```text",
                *self.footer_lines("review", "success"),
                "```",
            ]
        )
        self.stop_with_report("review", report)
        self.assertEqual([], self.records_for("session-a", "review"))

    def test_ordinary_text_between_footer_lines_stays_retry_required(
        self,
    ) -> None:
        """保全 (実測で確認): footer 3 行の間に非空白の通常テキスト行が
        挟まると、フェンス・空白行のみを無視する Phase B の頑健化後も実質行
        としての連続性が無いため footer と認識されず、現行実装と同じく
        retry-required のまま残ることを固定する。
        """
        self.subagent_start("rescue")
        footer = self.footer_lines("rescue", "success")
        report = "\n".join(
            [
                "Codex runner report",
                footer[0],
                "some other note",
                footer[1],
                footer[2],
            ]
        )
        self.stop_with_report("rescue", report)
        records = self.records_for("session-a", "rescue")
        self.assertEqual(1, len(records))
        self.assertEqual("retry-required", records[0]["phase"])

    def test_real_footer_after_fenced_example_footer_is_still_adopted(
        self,
    ) -> None:
        """保全 (既存 test_codex_output_may_contain_footer_words_before_final_footer
        の近縁、現行でも pass、実測で確認): footer 形式の 3 行がフェンス付き
        code block 内に「例」として現れても、実質末尾にある本物の footer 3 行
        (フェンスなし) が採用され success 終端になる。
        """
        self.subagent_start("rescue")
        report = "\n".join(
            [
                "Codex output quoted this example:",
                "```text",
                *self.footer_lines("rescue", "retryable-failure", "task-fake"),
                "```",
                "but the complete output continues here.",
                *self.footer_lines("rescue", "success", "task-real"),
            ]
        )
        self.stop_with_report("rescue", report)
        self.assertEqual([], self.records_for("session-a", "rescue"))

    def test_trailing_text_after_footer_stays_retry_required(self) -> None:
        """保全 (実測で確認): footer 3 行の後に非空白の通常テキスト行が続く
        場合、footer は実質最終行群でなければならない契約を維持し、フェンス・
        空白行のみを無視する Phase B の頑健化後も retry-required のまま残る
        ことを固定する (現行実装でも同じ結果)。
        """
        self.subagent_start("rescue")
        report = "\n".join(
            [
                "Codex runner report",
                *self.footer_lines("rescue", "success"),
                "Thanks for your patience.",
            ]
        )
        self.stop_with_report("rescue", report)
        records = self.records_for("session-a", "rescue")
        self.assertEqual(1, len(records))
        self.assertEqual("retry-required", records[0]["phase"])


class CodexRunnerArtifactContractTest(unittest.TestCase):
    def test_three_role_specific_runner_agents_are_declared(self) -> None:
        for operation, scoped_name in RUNNERS.items():
            with self.subTest(operation=operation):
                path = PLUGIN / "agents" / f"codex-{operation}-runner.md"
                self.assertTrue(path.is_file(), path)
                contents = path.read_text(encoding="utf-8")
                self.assertIn(f"name: codex-{operation}-runner", contents)
                self.assertIn("status", contents)
                self.assertIn("result", contents)
                self.assertIn("plugin cache", contents)
                self.assertIn("literal", contents)
                self.assertIn("run-codex-job.sh", contents)
                self.assertIn("Codex-Runner-Operation:", contents)
                self.assertIn("Codex-Runner-Status:", contents)
                self.assertIn(scoped_name, RULES.read_text(encoding="utf-8"))

    def test_rescue_and_advisor_require_prompt_file_transport(self) -> None:
        for operation in ("rescue", "advisor"):
            contents = (
                PLUGIN / "agents" / f"codex-{operation}-runner.md"
            ).read_text(encoding="utf-8")
            with self.subTest(operation=operation):
                self.assertIn("prompt file", contents)
                self.assertIn("--background", contents)
                self.assertNotIn("<<'EOF'", contents)

    def test_review_runner_defines_job_set_recovery_without_guessing(self) -> None:
        contents = (PLUGIN / "agents" / "codex-review-runner.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("job", contents)
        self.assertIn("差分", contents)
        self.assertIn("0 件", contents)
        self.assertIn("複数", contents)
        self.assertIn("推測", contents)

    def test_advisor_report_carries_review_cadence_attestation_contract(self) -> None:
        """codex-advisor-runner の footer は review cadence attestation 予約行を
        含む契約を維持する (enforcement 自体は pre-push-codex-review plugin
        の責務)。
        """
        advisor_runner = (PLUGIN / "agents" / "codex-advisor-runner.md").read_text(
            encoding="utf-8"
        )
        consult = CONSULT.read_text(encoding="utf-8")

        self.assertIn("Codex-Advisor-Review-Cadence", advisor_runner)
        self.assertIn("review_cycle_checkpoint", advisor_runner)
        self.assertIn("review_cycle_checkpoint", consult)

    def test_injected_advisor_rules_stay_below_inline_size_limit(self) -> None:
        contents = RULES.read_text(encoding="utf-8")
        utf16_code_units = len(contents.encode("utf-16-le")) // 2
        self.assertLessEqual(utf16_code_units, 8_000)

    def test_consult_routes_through_advisor_runner(self) -> None:
        contents = CONSULT.read_text(encoding="utf-8")
        self.assertIn(RUNNERS["advisor"], contents)
        self.assertIn("Agent", contents)
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
            "PostToolUse",
            "SubagentStop",
            "Stop",
        ):
            with self.subTest(event=event):
                commands = [
                    hook_invocation(hook)
                    for entry in hooks[event]
                    for hook in entry["hooks"]
                    if hook["type"] == "command"
                ]
                self.assertTrue(
                    any("manage-codex-runners.mjs" in command for command in commands),
                    commands,
                )
        post_tool_use_matchers = [
            entry["matcher"]
            for entry in hooks["PostToolUse"]
            if any(
                "manage-codex-runners.mjs" in hook_invocation(hook)
                for hook in entry["hooks"]
                if hook["type"] == "command"
            )
        ]
        self.assertEqual(["^SubagentHandback$"], post_tool_use_matchers)
        # review の起動・計数は pre-push-codex-review / pre-merge-codex-review
        # plugin の責務であり、cross-model-advisor の hooks.json は reviewer
        # namespace に関知しない。
        serialized = json.dumps(hooks, ensure_ascii=False)
        self.assertNotIn(PRE_PUSH_CODEX_REVIEWER, serialized)
        self.assertNotIn(PRE_PUSH_CODEX_REVIEWER_LEGACY, serialized)
        self.assertNotIn(PRE_MERGE_CODEX_REVIEWER, serialized)

    def test_reviewer_matcher_fullmatches_known_namespaces_only(self) -> None:
        # SubagentStart / SubagentStop の matcher を正規表現としてコンパイルし、
        # role 固有 runner namespace にのみ fullmatch し、review 系 reviewer
        # namespace (canonical / legacy いずれも) や類似の未承認 namespace には
        # match しないことを固定する (review の計数は pre-push-codex-review /
        # pre-merge-codex-review plugin の責務であり、cross-model-advisor は関知
        # しない)。
        manifest = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        hooks = manifest["hooks"]
        accepted = (
            *RUNNERS.values(),
            "codex:codex-rescue",
        )
        rejected = (
            "pre-push-review:code-reviewer",
            "my-pre-push-codex-review:codex-reviewer",
            "pre-push-codex-review:code-reviewer",
            "PRE-PUSH-REVIEW:codex-reviewer",
            PRE_PUSH_CODEX_REVIEWER,
            PRE_PUSH_CODEX_REVIEWER_LEGACY,
            PRE_MERGE_CODEX_REVIEWER,
        )
        for event in ("SubagentStart", "SubagentStop"):
            with self.subTest(event=event):
                matcher_entry = next(
                    entry for entry in hooks[event] if "matcher" in entry
                )
                pattern = re.compile(matcher_entry["matcher"])
                for agent_type in accepted:
                    with self.subTest(event=event, agent_type=agent_type):
                        self.assertIsNotNone(pattern.fullmatch(agent_type))
                for agent_type in rejected:
                    with self.subTest(event=event, agent_type=agent_type):
                        self.assertIsNone(pattern.fullmatch(agent_type))


class CodexJobHelperTest(unittest.TestCase):
    def fake_companion_home(self, temp: Path) -> Path:
        companion = (
            temp
            / "home/.claude/plugins/cache/openai-codex/codex/1.0.6/scripts"
            / "codex-companion.mjs"
        )
        companion.parent.mkdir(parents=True)
        companion.write_text("// fake companion\n", encoding="utf-8")
        return companion

    def test_rescue_and_advisor_launch_detached_prompt_file_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temp = Path(temporary_name)
            companion = self.fake_companion_home(temp)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            args_file = temp / "node-args"
            fake_node = fake_bin / "node"
            fake_node.write_text(
                "#!/bin/bash\n"
                "printf '%s\\n' \"$@\" > \"$NODE_ARGS_FILE\"\n"
                "printf '%s\\n' '{\"jobId\":\"task-test\"}'\n",
                encoding="utf-8",
            )
            fake_node.chmod(0o755)
            prompt = temp / "prompt with spaces.md"
            prompt.write_text("private prompt body\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(temp / "home"),
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "NODE_ARGS_FILE": str(args_file),
                }
            )

            cases = {
                "rescue": [
                    "rescue",
                    str(prompt),
                    "--fresh",
                    "--write",
                    "--effort",
                    "xhigh",
                ],
                "advisor": ["advisor", str(prompt)],
            }
            for operation, arguments in cases.items():
                with self.subTest(operation=operation):
                    result = subprocess.run(
                        ["bash", str(JOB_HELPER), *arguments],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=temp,
                        env=env,
                        check=False,
                        timeout=5,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    invoked = args_file.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(str(companion), invoked[0])
                    self.assertEqual("task", invoked[1])
                    self.assertIn("--background", invoked)
                    self.assertIn("--json", invoked)
                    self.assertIn("--prompt-file", invoked)
                    self.assertIn(str(prompt), invoked)
                    self.assertNotIn("private prompt body", " ".join(invoked))

    def resolve_real_node_path(self) -> str:
        """環境差し替え前に実 node バイナリの絶対パスを解決する。

        node が HOME 配下を参照する version manager (asdf 等) の shim の場合、
        HOME を差し替えた subprocess から起動すると shim が実体を解決できず
        失敗する。差し替え前に `node -p process.execPath` で解決した絶対パス
        を使うことで、shim を経由せず実バイナリを直接起動できるようにする。
        """
        result = subprocess.run(
            ["node", "-p", "process.execPath"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        real_node = result.stdout.strip()
        self.assertTrue(real_node)
        return real_node

    def test_status_wait_polls_the_v106_single_status_contract(self) -> None:
        real_node = self.resolve_real_node_path()
        with tempfile.TemporaryDirectory() as temporary_name:
            temp = Path(temporary_name)
            companion = self.fake_companion_home(temp)
            companion.write_text(
                "const args = process.argv.slice(2);\n"
                "if (args[0] !== 'status' || args[1] !== 'task-test' "
                "|| args[2] !== '--json' || args.length !== 3) process.exit(9);\n"
                "console.log(JSON.stringify({job:{id:'task-test',status:'completed'}}));\n",
                encoding="utf-8",
            )
            # run-codex-job.sh 内部でも `node` を PATH 経由で起動するため、
            # 解決済みの絶対パスへ転送する shim を PATH の先頭に置く。これにより
            # HOME 差し替え後の `node` 起動もすべてこの shim 経由になり、
            # version manager の shim を経由しない。
            node_shim_dir = temp / "node-shim"
            node_shim_dir.mkdir()
            node_shim = node_shim_dir / "node"
            node_shim.write_text(
                "#!/bin/bash\n"
                f'exec "{real_node}" "$@"\n',
                encoding="utf-8",
            )
            node_shim.chmod(0o755)

            env = os.environ.copy()
            env["HOME"] = str(temp / "home")
            env["PATH"] = os.pathsep.join([str(node_shim_dir), env.get("PATH", "")])
            direct = subprocess.run(
                [real_node, str(companion), "status", "task-test", "--json"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, direct.returncode, direct.stderr)
            self.assertTrue(direct.stdout, companion.read_text(encoding="utf-8"))
            result = subprocess.run(
                [
                    "bash",
                    str(JOB_HELPER),
                    "status",
                    "task-test",
                    "--wait",
                    "--timeout-ms",
                    "1000",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp,
                env=env,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                "completed",
                json.loads(result.stdout)["job"]["status"],
            )


if __name__ == "__main__":
    unittest.main()

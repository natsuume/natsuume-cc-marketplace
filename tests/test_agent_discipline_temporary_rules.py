from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "agent-discipline"
HOOKS_PATH = PLUGIN_DIR / "hooks" / "hooks.json"
INJECT_AUTO = PLUGIN_DIR / "hooks" / "scripts" / "inject-auto.sh"
CHECK_UNCOMMITTED = (
    PLUGIN_DIR / "hooks" / "scripts" / "check-uncommitted-on-session-start.sh"
)
BLOCK_FABLE = PLUGIN_DIR / "hooks" / "scripts" / "block-fable-subagent.sh"
INJECT_TEMPORARY = PLUGIN_DIR / "hooks" / "scripts" / "inject-temporary.sh"


class AgentDisciplineTemporaryRulesTest(unittest.TestCase):
    def run_hook(
        self,
        script: Path,
        payload: str | dict[str, object],
        *,
        env: dict[str, str] | None = None,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[str]:
        raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        return subprocess.run(
            ["/bin/bash", str(script)],
            cwd=ROOT,
            env=process_env,
            input=raw,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def test_hooks_keep_four_claude_agent_prompts_for_gh_body_checks(self) -> None:
        hooks = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
        bash_group = next(
            group for group in hooks["hooks"]["PreToolUse"] if group["matcher"] == "Bash"
        )
        agents = [handler for handler in bash_group["hooks"] if handler["type"] == "agent"]

        self.assertEqual(4, len(agents))
        self.assertEqual(
            {
                "Bash(gh issue create:*)",
                "Bash(gh issue edit:*)",
                "Bash(gh pr create:*)",
                "Bash(gh pr edit:*)",
            },
            {handler["if"] for handler in agents},
        )
        self.assertTrue(all(handler["prompt"].endswith("$ARGUMENTS") for handler in agents))

    def test_temporary_rules_are_caught_up_once_per_main_session(self) -> None:
        hooks = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
        temporary_command = (
            "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/inject-temporary.sh"
        )
        for event in ("SessionStart", "UserPromptSubmit"):
            commands = [
                handler["command"]
                for group in hooks["hooks"][event]
                for handler in group["hooks"]
            ]
            self.assertEqual(1, commands.count(temporary_command), event)

        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            plugin = temp / "agent-discipline"
            shutil.copytree(PLUGIN_DIR, plugin)
            prompts = plugin / "hooks" / "prompts" / "temporary"
            for prompt in prompts.glob("*.md"):
                prompt.unlink()
            (prompts / "10-initial.md").write_text("initial rule\n", encoding="utf-8")

            script = plugin / "hooks" / "scripts" / "inject-temporary.sh"
            env = {"TMPDIR": str(temp / "state-root")}
            session_id = "temporary/catch-up session"
            session_start = self.run_hook(
                script,
                {
                    "hook_event_name": "SessionStart",
                    "session_id": session_id,
                },
                env=env,
            )
            self.assertEqual(0, session_start.returncode, session_start.stderr)
            self.assertEqual(
                "initial rule",
                json.loads(session_start.stdout)["hookSpecificOutput"][
                    "additionalContext"
                ],
            )

            prompt_payload = {
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
            }
            already_delivered = self.run_hook(script, prompt_payload, env=env)
            self.assertEqual(0, already_delivered.returncode, already_delivered.stderr)
            self.assertEqual("", already_delivered.stdout)

            (prompts / "20-later.md").write_text("later rule\n", encoding="utf-8")
            catch_up = self.run_hook(script, prompt_payload, env=env)
            self.assertEqual(0, catch_up.returncode, catch_up.stderr)
            self.assertEqual(
                "later rule",
                json.loads(catch_up.stdout)["hookSpecificOutput"][
                    "additionalContext"
                ],
            )
            repeated = self.run_hook(script, prompt_payload, env=env)
            self.assertEqual("", repeated.stdout)

            (prompts / "30-main-only.md").write_text(
                "main session only\n", encoding="utf-8"
            )
            subagent = self.run_hook(
                script,
                {**prompt_payload, "agent_id": "subagent-1"},
                env=env,
            )
            self.assertEqual(0, subagent.returncode, subagent.stderr)
            self.assertEqual("", subagent.stdout)
            main_session = self.run_hook(script, prompt_payload, env=env)
            self.assertEqual(
                "main session only",
                json.loads(main_session.stdout)["hookSpecificOutput"][
                    "additionalContext"
                ],
            )

            (prompts / "20-later.md").unlink()
            (prompts / "30-main-only.md").unlink()
            after_removal = self.run_hook(script, prompt_payload, env=env)
            self.assertEqual("", after_removal.stdout)

            next_session = self.run_hook(
                script,
                {"hook_event_name": "SessionStart", "session_id": "next-session"},
                env=env,
            )
            self.assertEqual(
                "initial rule",
                json.loads(next_session.stdout)["hookSpecificOutput"][
                    "additionalContext"
                ],
            )

            (prompts / "10-initial.md").unlink()
            empty_start = self.run_hook(
                script,
                {"hook_event_name": "SessionStart", "session_id": session_id},
                env=env,
            )
            self.assertEqual(0, empty_start.returncode, empty_start.stderr)
            self.assertEqual("", empty_start.stdout)

            (prompts / "10-initial.md").write_text(
                "reintroduced rule\n", encoding="utf-8"
            )
            after_empty_start = self.run_hook(script, prompt_payload, env=env)
            self.assertEqual(
                "reintroduced rule",
                json.loads(after_empty_start.stdout)["hookSpecificOutput"][
                    "additionalContext"
                ],
            )

    def test_temporary_rule_catch_up_fails_open_when_state_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            blocked_tmpdir = temp / "not-a-directory"
            blocked_tmpdir.write_text("blocked\n", encoding="utf-8")
            result = self.run_hook(
                INJECT_TEMPORARY,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "marker-write-failure",
                },
                env={"TMPDIR": str(blocked_tmpdir)},
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stdout)

    def test_permission_mode_adapter_emits_only_for_literal_auto(self) -> None:
        base_payload = {
            "session_id": "session-mode",
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(ROOT),
        }

        for mode, should_emit in (
            ("auto", True),
            ("default", False),
            ("plan", False),
            ("acceptEdits", False),
            ("bypassPermissions", False),
        ):
            with self.subTest(mode=mode):
                result = self.run_hook(INJECT_AUTO, {**base_payload, "permission_mode": mode})
                self.assertEqual(should_emit, bool(result.stdout.strip()), result.stderr)

    def test_fable_guard_denies_bash_agent_with_fable_model_in_claude_hooks(self) -> None:
        hooks = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
        fable_group = next(
            group
            for group in hooks["hooks"]["PreToolUse"]
            if group["matcher"] == "Agent|Task"
        )
        self.assertEqual(1, len(fable_group["hooks"]))
        self.assertTrue(
            fable_group["hooks"][0]["command"].endswith("/block-fable-subagent.sh")
        )

        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "claude-fable-deny",
            "tool_input": {"model": "fable"},
        }
        claude_result = self.run_hook(
            BLOCK_FABLE,
            payload,
            env={"CLAUDE_CODE_SUBAGENT_MODEL": "fable"},
        )
        decision = json.loads(claude_result.stdout)["hookSpecificOutput"]
        self.assertEqual("deny", decision["permissionDecision"])

    def test_uncommitted_check_emits_only_for_claude_auto(self) -> None:
        with tempfile.TemporaryDirectory() as repository, tempfile.TemporaryDirectory() as tempdir:
            repo = Path(repository)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            base = {
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(repo),
            }
            env = {"TMPDIR": tempdir}

            for mode in (
                "default",
                "acceptEdits",
                "bypassPermissions",
                "plan",
            ):
                with self.subTest(mode=mode):
                    result = self.run_hook(
                        CHECK_UNCOMMITTED,
                        {
                            **base,
                            "session_id": f"non-auto-{mode}",
                            "permission_mode": mode,
                        },
                        env=env,
                    )
                    self.assertEqual("", result.stdout)

            claude_auto = self.run_hook(
                CHECK_UNCOMMITTED,
                {
                    **base,
                    "session_id": "claude-auto",
                    "permission_mode": "auto",
                },
                env=env,
            )
            self.assertTrue(claude_auto.stdout.strip(), claude_auto.stderr)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "agent-discipline"
HOOKS_PATH = PLUGIN_DIR / "hooks" / "hooks.json"
VALIDATOR = PLUGIN_DIR / "hooks" / "scripts" / "codex-semantic-validator.sh"
VALIDATOR_SCHEMA = (
    PLUGIN_DIR / "hooks" / "schemas" / "codex-semantic-validator-output.schema.json"
)
SETUP_VALIDATOR = PLUGIN_DIR / "scripts" / "setup-codex-semantic-validator.sh"
INJECT_AUTO = PLUGIN_DIR / "hooks" / "scripts" / "inject-auto.sh"
CHECK_UNCOMMITTED = (
    PLUGIN_DIR / "hooks" / "scripts" / "check-uncommitted-on-session-start.sh"
)
BLOCK_FABLE = PLUGIN_DIR / "hooks" / "scripts" / "block-fable-subagent.sh"


class AgentDisciplineCodexAdapterTest(unittest.TestCase):
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
        process_env.pop("AGENT_DISCIPLINE_CODEX_VALIDATOR_ACTIVE", None)
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

    def make_fake_codex(self, directory: Path) -> tuple[Path, Path, Path]:
        bin_dir = directory / "bin"
        bin_dir.mkdir()
        args_path = directory / "args.txt"
        prompt_path = directory / "prompt.txt"
        codex = bin_dir / "codex"
        codex.write_text(
            """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_CODEX_ARGS"
out=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-last-message)
      out=$2
      shift 2
      ;;
    *) shift ;;
  esac
done
cat > "$FAKE_CODEX_PROMPT"
case "${FAKE_CODEX_MODE:-allow}" in
  allow) printf '%s\\n' '{"ok":true,"reason":null}' > "$out" ;;
  deny) printf '%s\\n' '{"ok":false,"reason":"未承認の推奨表現"}' > "$out" ;;
  invalid) printf '%s\\n' 'not-json' > "$out" ;;
  invalid_true_reason) printf '%s\\n' '{"ok":true,"reason":"must be null"}' > "$out" ;;
  invalid_false_null) printf '%s\\n' '{"ok":false,"reason":null}' > "$out" ;;
  fail) exit 23 ;;
  timeout) sleep 10 ;;
  timeout_tree)
    printf '%s\n' "$$" > "$FAKE_CODEX_PID_FILE"
    trap 'printf received > "$FAKE_CODEX_TERM_FILE"; while :; do :; done' TERM
    (trap '' TERM; while :; do :; done) &
    descendant_pid=$!
    printf '%s\n' "$descendant_pid" > "$FAKE_CODEX_DESCENDANT_PID_FILE"
    wait "$descendant_pid"
    ;;
esac
""",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        return bin_dir, args_path, prompt_path

    def assert_process_gone(self, pid: int, label: str) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"{label} process {pid} survived validator cleanup")

    def validator_payload(
        self,
        command: str = "gh issue edit 12 --body 'safe'",
        *,
        codex: bool = True,
        cwd: Path | None = None,
    ) -> str:
        payload = {
            "session_id": "session-1",
            "hook_event_name": "PreToolUse",
            "cwd": str(cwd or ROOT),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        if codex:
            payload["turn_id"] = "turn-1"
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def initialize_repository(self, directory: Path) -> Path:
        repository = directory / "repo"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        return repository

    def run_setup(
        self,
        repository: Path,
        action: str = "inspect",
        *,
        token: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "/bin/bash",
            str(SETUP_VALIDATOR),
            action,
            "--repo",
            str(repository),
        ]
        if token is not None:
            command.extend(["--approval-token", token])
        return subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def enable_validator(self, repository: Path) -> dict[str, object]:
        inspected = self.run_setup(repository)
        self.assertEqual(0, inspected.returncode, inspected.stderr)
        enable_token = json.loads(inspected.stdout)["enableApprovalToken"]
        self.assertIsInstance(enable_token, str)
        enabled = self.run_setup(repository, "enable", token=enable_token)
        self.assertEqual(0, enabled.returncode, enabled.stderr)
        state = json.loads(enabled.stdout)
        self.assertEqual("enabled", state["status"])
        return state

    def test_hooks_keep_four_claude_prompts_and_add_one_codex_command(self) -> None:
        hooks = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
        bash_group = next(
            group for group in hooks["hooks"]["PreToolUse"] if group["matcher"] == "Bash"
        )
        agents = [handler for handler in bash_group["hooks"] if handler["type"] == "agent"]
        commands = [handler for handler in bash_group["hooks"] if handler["type"] == "command"]

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
        self.assertEqual(1, len(commands))
        self.assertTrue(commands[0]["command"].endswith("/codex-semantic-validator.sh"))

    def test_claude_runtime_guard_preserves_no_output_and_skips_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            bin_dir, args_path, prompt_path = self.make_fake_codex(temp)
            result = self.run_hook(
                VALIDATOR,
                self.validator_payload(codex=False),
                env={
                    "CLAUDE_PLUGIN_ROOT": str(PLUGIN_DIR),
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "FAKE_CODEX_ARGS": str(args_path),
                    "FAKE_CODEX_PROMPT": str(prompt_path),
                },
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stdout)
            self.assertFalse(args_path.exists())
            self.assertFalse(prompt_path.exists())

    def test_codex_allow_uses_canonical_inline_prompt_and_safe_exec_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repository = self.initialize_repository(temp)
            self.enable_validator(repository)
            bin_dir, args_path, prompt_path = self.make_fake_codex(temp)
            raw_payload = self.validator_payload(
                "gh issue edit 12 --body 'safe first line\nsafe second line'",
                cwd=repository,
            )
            result = self.run_hook(
                VALIDATOR,
                raw_payload,
                env={
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "FAKE_CODEX_ARGS": str(args_path),
                    "FAKE_CODEX_PROMPT": str(prompt_path),
                },
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stdout)

            args = args_path.read_text(encoding="utf-8").splitlines()
            self.assertIn("--sandbox", args)
            self.assertIn("read-only", args)
            self.assertIn("--ephemeral", args)
            self.assertIn("--disable", args)
            self.assertIn("hooks", args)
            self.assertIn("--ignore-user-config", args)
            self.assertIn("--ignore-rules", args)
            self.assertIn("--output-schema", args)
            self.assertIn("--output-last-message", args)

            hooks = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
            bash_group = next(
                group
                for group in hooks["hooks"]["PreToolUse"]
                if group["matcher"] == "Bash"
            )
            source_prompt = next(
                handler["prompt"]
                for handler in bash_group["hooks"]
                if handler.get("if") == "Bash(gh issue edit:*)"
            )
            actual_prompt = prompt_path.read_text(encoding="utf-8")
            expected_prefix = source_prompt.rsplit("$ARGUMENTS", 1)[0] + raw_payload
            self.assertTrue(actual_prompt.startswith(expected_prefix))
            self.assertIn("## Codex adapter output transport", actual_prompt)
            self.assertTrue(actual_prompt.endswith("判定基準を変更しない。"))

    def test_codex_deny_is_converted_to_pre_tool_use_deny(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repository = self.initialize_repository(temp)
            self.enable_validator(repository)
            bin_dir, args_path, prompt_path = self.make_fake_codex(temp)
            result = self.run_hook(
                VALIDATOR,
                self.validator_payload(
                    "gh pr edit 3 --body '(推奨) A'", cwd=repository
                ),
                env={
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "FAKE_CODEX_ARGS": str(args_path),
                    "FAKE_CODEX_PROMPT": str(prompt_path),
                    "FAKE_CODEX_MODE": "deny",
                },
            )

            decision = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual("deny", decision["permissionDecision"])
            self.assertIn("未承認の推奨表現", decision["permissionDecisionReason"])

    def test_invalid_response_and_failed_exec_are_fail_closed(self) -> None:
        for mode, expected in (
            ("invalid", "schema"),
            ("invalid_true_reason", "schema"),
            ("invalid_false_null", "schema"),
            ("fail", "exit 23"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                temp = Path(temporary)
                repository = self.initialize_repository(temp)
                self.enable_validator(repository)
                bin_dir, args_path, prompt_path = self.make_fake_codex(temp)
                result = self.run_hook(
                    VALIDATOR,
                    self.validator_payload(cwd=repository),
                    env={
                        "PATH": f"{bin_dir}:{os.environ['PATH']}",
                        "FAKE_CODEX_ARGS": str(args_path),
                        "FAKE_CODEX_PROMPT": str(prompt_path),
                        "FAKE_CODEX_MODE": mode,
                    },
                )
                decision = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual("deny", decision["permissionDecision"])
                self.assertIn(expected, decision["permissionDecisionReason"])

    def test_missing_codex_and_timeout_are_fail_closed(self) -> None:
        jq_path = shutil.which("jq")
        self.assertIsNotNone(jq_path)
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repository = self.initialize_repository(temp)
            self.enable_validator(repository)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            (bin_dir / "jq").symlink_to(jq_path)
            result = self.run_hook(
                VALIDATOR,
                self.validator_payload(cwd=repository),
                env={
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                },
            )
            decision = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual("deny", decision["permissionDecision"])
            self.assertIn("codex CLI が見つかりません", decision["permissionDecisionReason"])

        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repository = self.initialize_repository(temp)
            self.enable_validator(repository)
            bin_dir, args_path, prompt_path = self.make_fake_codex(temp)
            started = time.monotonic()
            result = self.run_hook(
                VALIDATOR,
                self.validator_payload(cwd=repository),
                env={
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "FAKE_CODEX_ARGS": str(args_path),
                    "FAKE_CODEX_PROMPT": str(prompt_path),
                    "FAKE_CODEX_MODE": "timeout",
                    "AGENT_DISCIPLINE_CODEX_TIMEOUT_SECONDS": "1",
                },
                timeout=6,
            )
            elapsed = time.monotonic() - started
            decision = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual("deny", decision["permissionDecision"])
            self.assertIn("timeout", decision["permissionDecisionReason"])
            self.assertLess(elapsed, 5)

    def test_timeout_terminates_and_reaps_nested_codex_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repository = self.initialize_repository(temp)
            self.enable_validator(repository)
            bin_dir, args_path, prompt_path = self.make_fake_codex(temp)
            pid_file = temp / "codex-pid"
            descendant_pid_file = temp / "codex-descendant-pid"
            term_file = temp / "codex-term"
            nested_pid: int | None = None
            descendant_pid: int | None = None
            try:
                result = self.run_hook(
                    VALIDATOR,
                    self.validator_payload(cwd=repository),
                    env={
                        "PATH": f"{bin_dir}:{os.environ['PATH']}",
                        "FAKE_CODEX_ARGS": str(args_path),
                        "FAKE_CODEX_PROMPT": str(prompt_path),
                        "FAKE_CODEX_MODE": "timeout_tree",
                        "FAKE_CODEX_PID_FILE": str(pid_file),
                        "FAKE_CODEX_DESCENDANT_PID_FILE": str(
                            descendant_pid_file
                        ),
                        "FAKE_CODEX_TERM_FILE": str(term_file),
                        "AGENT_DISCIPLINE_CODEX_TIMEOUT_SECONDS": "1",
                        "AGENT_DISCIPLINE_CODEX_KILL_GRACE_SECONDS": "1",
                    },
                    timeout=8,
                )
                decision = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual("deny", decision["permissionDecision"])
                self.assertIn("timeout", decision["permissionDecisionReason"])
                self.assertTrue(term_file.exists(), "validator did not send TERM")
                nested_pid = int(pid_file.read_text(encoding="utf-8").strip())
                descendant_pid = int(
                    descendant_pid_file.read_text(encoding="utf-8").strip()
                )
                self.assert_process_gone(nested_pid, "nested Codex")
                self.assert_process_gone(descendant_pid, "nested Codex descendant")
            finally:
                if nested_pid is None and pid_file.exists():
                    nested_pid = int(pid_file.read_text(encoding="utf-8").strip())
                if descendant_pid is None and descendant_pid_file.exists():
                    descendant_pid = int(
                        descendant_pid_file.read_text(encoding="utf-8").strip()
                    )
                for residual_pid in (descendant_pid, nested_pid):
                    if residual_pid is not None:
                        try:
                            os.kill(residual_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_codex_target_is_denied_without_opt_in_and_never_starts_nested_codex(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repository = self.initialize_repository(temp)
            bin_dir, args_path, prompt_path = self.make_fake_codex(temp)
            result = self.run_hook(
                VALIDATOR,
                self.validator_payload(cwd=repository),
                env={
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "FAKE_CODEX_ARGS": str(args_path),
                    "FAKE_CODEX_PROMPT": str(prompt_path),
                },
            )

            decision = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual("deny", decision["permissionDecision"])
            self.assertIn("status: disabled", decision["permissionDecisionReason"])
            self.assertIn("nested codex は起動せず", decision["permissionDecisionReason"])
            self.assertFalse(args_path.exists())
            self.assertFalse(prompt_path.exists())

    def test_opt_in_helper_requires_fresh_tokens_and_creates_owner_only_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self.initialize_repository(Path(temporary))
            inspected = self.run_setup(repository)
            self.assertEqual(0, inspected.returncode, inspected.stderr)
            disabled = json.loads(inspected.stdout)
            self.assertEqual("disabled", disabled["status"])
            self.assertIn("provider/model", disabled["disclosure"])
            self.assertIn("--body-file", disabled["disclosure"])
            enable_token = disabled["enableApprovalToken"]
            self.assertRegex(enable_token, r"^[0-9a-f]{64}$")
            self.assertIsNone(disabled["disableApprovalToken"])

            missing_token = self.run_setup(repository, "enable")
            self.assertEqual(2, missing_token.returncode)

            enabled_result = self.run_setup(repository, "enable", token=enable_token)
            self.assertEqual(0, enabled_result.returncode, enabled_result.stderr)
            enabled = json.loads(enabled_result.stdout)
            self.assertEqual("enabled", enabled["status"])
            marker = Path(enabled["marker"])
            self.assertTrue(marker.is_file())
            self.assertFalse(marker.is_symlink())
            self.assertEqual(0o600, marker.stat().st_mode & 0o777)
            self.assertEqual(os.getuid(), marker.stat().st_uid)
            self.assertRegex(enabled["disableApprovalToken"], r"^[0-9a-f]{64}$")

            stale = self.run_setup(repository, "disable", token=enable_token)
            self.assertEqual(2, stale.returncode)
            self.assertTrue(marker.exists())

            disabled_result = self.run_setup(
                repository, "disable", token=enabled["disableApprovalToken"]
            )
            self.assertEqual(0, disabled_result.returncode, disabled_result.stderr)
            self.assertEqual("disabled", json.loads(disabled_result.stdout)["status"])
            self.assertFalse(marker.exists())

    def test_opt_in_helper_rejects_symlink_nonregular_and_wrong_mode_markers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repository = self.initialize_repository(temp)
            state = json.loads(self.run_setup(repository).stdout)
            marker = Path(state["marker"])
            marker.parent.mkdir(mode=0o700)
            outside = temp / "outside"
            outside.write_text("do not change\n", encoding="utf-8")

            marker.symlink_to(outside)
            symlink_state = json.loads(self.run_setup(repository).stdout)
            self.assertEqual("unsafe-marker-symlink", symlink_state["status"])
            rejected = self.run_setup(repository, "enable", token="0" * 64)
            self.assertEqual(1, rejected.returncode)
            self.assertEqual("do not change\n", outside.read_text(encoding="utf-8"))

            marker.unlink()
            marker.mkdir()
            nonregular_state = json.loads(self.run_setup(repository).stdout)
            self.assertEqual("unsafe-marker-nonregular", nonregular_state["status"])
            rejected = self.run_setup(repository, "disable", token="0" * 64)
            self.assertEqual(1, rejected.returncode)
            self.assertTrue(marker.is_dir())

            marker.rmdir()
            marker.write_text(
                "agent-discipline-codex-semantic-validator-enabled-v1\n",
                encoding="utf-8",
            )
            marker.chmod(0o644)
            wrong_mode = json.loads(self.run_setup(repository).stdout)
            self.assertEqual("unsafe-marker-mode", wrong_mode["status"])
            rejected = self.run_setup(repository, "disable", token="0" * 64)
            self.assertEqual(1, rejected.returncode)
            self.assertTrue(marker.exists())

    def test_structured_output_schema_uses_supported_required_nullable_subset(self) -> None:
        schema = json.loads(VALIDATOR_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            {"type", "additionalProperties", "required", "properties"},
            set(schema),
        )
        self.assertEqual(set(schema["properties"]), set(schema["required"]))
        self.assertEqual(["string", "null"], schema["properties"]["reason"]["type"])
        for property_schema in schema["properties"].values():
            self.assertEqual({"type"}, set(property_schema))

        unsupported = {
            "$schema",
            "allOf",
            "anyOf",
            "oneOf",
            "not",
            "if",
            "then",
            "else",
            "minLength",
            "maxLength",
            "pattern",
        }

        def schema_keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value).union(
                    *(schema_keys(child) for child in value.values())
                )
            if isinstance(value, list):
                return set().union(*(schema_keys(child) for child in value))
            return set()

        self.assertFalse(unsupported & schema_keys(schema))

    def test_codex_runtime_skips_unrelated_commands_without_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            bin_dir, args_path, prompt_path = self.make_fake_codex(temp)
            result = self.run_hook(
                VALIDATOR,
                self.validator_payload("git status"),
                env={
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "FAKE_CODEX_ARGS": str(args_path),
                    "FAKE_CODEX_PROMPT": str(prompt_path),
                },
            )
            self.assertEqual("", result.stdout)
            self.assertFalse(args_path.exists())

    def test_permission_mode_adapter_preserves_claude_and_skips_codex_modes(self) -> None:
        base_payload = {
            "session_id": "session-mode",
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(ROOT),
        }

        for mode, should_emit in (("auto", True), ("default", False), ("plan", False)):
            with self.subTest(runtime="claude", mode=mode):
                result = self.run_hook(INJECT_AUTO, {**base_payload, "permission_mode": mode})
                self.assertEqual(should_emit, bool(result.stdout.strip()), result.stderr)

        for mode, should_emit in (
            ("auto", False),
            ("default", False),
            ("acceptEdits", False),
            ("dontAsk", False),
            ("bypassPermissions", False),
            ("plan", False),
            ("future-mode", False),
        ):
            with self.subTest(runtime="codex", mode=mode):
                result = self.run_hook(
                    INJECT_AUTO,
                    {**base_payload, "permission_mode": mode, "turn_id": "turn-mode"},
                )
                self.assertEqual(should_emit, bool(result.stdout.strip()), result.stderr)

    def test_fable_guard_keeps_claude_deny_and_codex_has_no_matching_tool(self) -> None:
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

    def test_uncommitted_check_preserves_claude_auto_and_skips_codex_modes(self) -> None:
        with tempfile.TemporaryDirectory() as repository, tempfile.TemporaryDirectory() as tempdir:
            repo = Path(repository)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            base = {
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(repo),
                "turn_id": "turn-uncommitted",
            }
            env = {"TMPDIR": tempdir}

            for mode in (
                "auto",
                "default",
                "acceptEdits",
                "dontAsk",
                "bypassPermissions",
                "plan",
            ):
                with self.subTest(runtime="codex", mode=mode):
                    result = self.run_hook(
                        CHECK_UNCOMMITTED,
                        {
                            **base,
                            "session_id": f"codex-{mode}",
                            "permission_mode": mode,
                        },
                        env=env,
                    )
                    self.assertEqual("", result.stdout)

            claude_base = {
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(repo),
            }
            claude_auto = self.run_hook(
                CHECK_UNCOMMITTED,
                {
                    **claude_base,
                    "session_id": "claude-auto",
                    "permission_mode": "auto",
                },
                env=env,
            )
            self.assertTrue(claude_auto.stdout.strip(), claude_auto.stderr)

            claude_default = self.run_hook(
                CHECK_UNCOMMITTED,
                {
                    **claude_base,
                    "session_id": "claude-default",
                    "permission_mode": "default",
                },
                env=env,
            )
            self.assertEqual("", claude_default.stdout)

    def test_codex_explicit_substitute_skills_document_scope_boundaries(self) -> None:
        readme = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
        setup_skill = (
            PLUGIN_DIR / "skills" / "setup-codex-semantic-validator" / "SKILL.md"
        ).read_text(encoding="utf-8")
        auto_skill = (PLUGIN_DIR / "skills" / "auto-codex" / "SKILL.md").read_text(
            encoding="utf-8"
        )

        for skill in (setup_skill, auto_skill):
            frontmatter = skill.split("---", 2)[1]
            keys = {
                line.split(":", 1)[0]
                for line in frontmatter.splitlines()
                if ":" in line
            }
            self.assertEqual({"name", "description"}, keys)
        self.assertIn("token proves only", setup_skill)
        self.assertIn("different from the parent", setup_skill)
        self.assertIn("Never infer activation", auto_skill)
        self.assertIn("does not expand a local-only request", auto_skill)
        self.assertIn("$agent-discipline:setup-codex-semantic-validator", readme)
        self.assertIn("$agent-discipline:auto-codex", readme)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = (
    ROOT
    / "plugins"
    / "pre-push-review"
    / "hooks"
    / "scripts"
    / "block-pre-push.sh"
)
PLUGIN_DIR = ROOT / "plugins" / "pre-push-review"
CODEX_AUTO_MARK = PLUGIN_DIR / "hooks" / "scripts" / "codex-auto-mark.sh"
SETUP = PLUGIN_DIR / "scripts" / "setup-codex-agents.sh"
AGENT_TEMPLATE_DIR = (
    PLUGIN_DIR / "skills" / "setup-pre-push-agents" / "assets" / "agents"
)
MARKER_NAMES = (
    ".claude-pre-push-code-reviewed",
    ".claude-pre-push-codex-reviewed",
    ".claude-pre-push-security-reviewed",
)
REVIEW_CASES = (
    (
        "pre_push_correctness_reviewer",
        "# Correctness Review",
        "correctness",
        MARKER_NAMES[0],
    ),
    (
        "pre_push_independent_reviewer",
        "# Independent Review",
        "independent",
        MARKER_NAMES[1],
    ),
    (
        "pre_push_security_reviewer",
        "# Security Review",
        "security",
        MARKER_NAMES[2],
    ),
)


class GitRepositoryMixin:
    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def git_output(self, cwd: Path, *args: str) -> bytes:
        return subprocess.check_output(["git", *args], cwd=cwd)

    def create_feature_repository(self, temporary: Path) -> Path:
        origin = temporary / "origin.git"
        work = temporary / "work"
        self.git(temporary, "init", "--bare", str(origin))
        self.git(temporary, "init", str(work))
        self.git(work, "config", "user.name", "Marketplace Test")
        self.git(work, "config", "user.email", "marketplace@example.invalid")
        (work / "example.txt").write_text("base\n", encoding="utf-8")
        self.git(work, "add", "example.txt")
        self.git(work, "commit", "-m", "base")
        self.git(work, "branch", "-M", "master")
        self.git(work, "remote", "add", "origin", str(origin))
        self.git(work, "push", "-u", "origin", "master")
        self.git(work, "remote", "set-head", "origin", "master")
        self.git(work, "checkout", "-b", "feature/test")
        (work / "example.txt").write_text("changed\n", encoding="utf-8")
        self.git(work, "add", "example.txt")
        self.git(work, "commit", "-m", "change")
        return work


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class PrePushCodexAdapterTest(GitRepositoryMixin, unittest.TestCase):
    def test_deny_message_names_claude_and_codex_recovery_flows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)

            result = self.run_push_hook(
                work,
                self.claude_env(temporary / "claude-plugin-data"),
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            response = json.loads(result.stdout)
            reason = response["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("/pre-push-review:review", reason)
            self.assertIn("$pre-push-review:review-codex", reason)
            self.assertIn("$pre-push-review:setup-pre-push-agents", reason)
            self.assertIn("SubagentStop hook が自動更新", reason)

    def expected_review_hash(self, work: Path) -> str:
        head = self.git_output(work, "rev-parse", "HEAD^{commit}").decode().strip()
        merge_base = (
            self.git_output(work, "merge-base", "origin/master", "HEAD")
            .decode()
            .strip()
        )
        chunks = [
            f"head {head}\n".encode(),
            f"mbase {merge_base}\n".encode(),
        ]
        for args in (
            ("diff", "--no-ext-diff", "--no-textconv", merge_base, "HEAD"),
            ("diff", "--no-ext-diff", "--no-textconv", "--cached"),
            ("diff", "--no-ext-diff", "--no-textconv"),
        ):
            # Bash command substitution in diff-hash.sh strips trailing newlines.
            chunks.append(self.git_output(work, *args).rstrip(b"\n"))
        return hashlib.sha256(b"".join(chunks)).hexdigest()

    def absolute_git_dir(self, work: Path) -> Path:
        path = self.git_output(work, "rev-parse", "--absolute-git-dir").decode().strip()
        return Path(path).resolve()

    def expected_repo_key(self, work: Path) -> str:
        git_dir = str(self.absolute_git_dir(work)).encode("utf-8")
        return hashlib.sha256(git_dir).hexdigest()

    def codex_marker_dir(self, work: Path, plugin_data: Path) -> Path:
        return (
            plugin_data
            / "pre-push-review"
            / "markers"
            / self.expected_repo_key(work)
        )

    def claude_env(self, plugin_data: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("PLUGIN_ROOT", None)
        env.pop("PLUGIN_DATA", None)
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_DIR)
        env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
        return env

    def codex_env(
        self,
        plugin_data: Path,
        *,
        bare_plugin_data: Path | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("PLUGIN_ROOT", None)
        env.pop("PLUGIN_DATA", None)
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_DIR)
        env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
        if bare_plugin_data is not None:
            env["PLUGIN_DATA"] = str(bare_plugin_data)
        return env

    def codex_missing_data_env(self, value: str | None) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("PLUGIN_ROOT", None)
        env.pop("PLUGIN_DATA", None)
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_DIR)
        if value is None:
            env.pop("CLAUDE_PLUGIN_DATA", None)
        else:
            env["CLAUDE_PLUGIN_DATA"] = value
        return env

    def write_git_markers(self, work: Path, review_hash: str) -> None:
        git_dir = self.absolute_git_dir(work)
        for marker_name in MARKER_NAMES:
            (git_dir / marker_name).write_text(review_hash, encoding="utf-8")

    def subagent_payload(
        self,
        work: Path,
        agent_type: str,
        heading: str,
        role: str,
        *,
        stop_hook_active: bool = False,
    ) -> dict[str, object]:
        transcript = work / f"{role}-agent.jsonl"
        transcript.write_text('{"type":"assistant"}\n', encoding="utf-8")
        return {
            "session_id": "session-test",
            "turn_id": "turn-test",
            "agent_id": f"agent-{role}",
            "agent_type": agent_type,
            "agent_transcript_path": str(transcript),
            "transcript_path": None,
            "cwd": str(work),
            "hook_event_name": "SubagentStop",
            "model": "gpt-test",
            "permission_mode": "default",
            "stop_hook_active": stop_hook_active,
            "last_assistant_message": (
                f"{heading}\n\nNo high-confidence findings.\n\n"
                f"<!-- pre-push-review:completed {role} -->"
            ),
        }

    def run_codex_auto_mark(
        self,
        work: Path,
        payload: dict[str, object],
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(CODEX_AUTO_MARK)],
            cwd=work.parent,
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def run_push_hook(
        self,
        work: Path,
        env: dict[str, str],
        *,
        turn_id: str | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin HEAD"},
        }
        if turn_id is not None:
            payload["turn_id"] = turn_id
        return subprocess.run(
            ["bash", str(HOOK)],
            cwd=work,
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def assert_push_denied(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotEqual(result.stdout, b"")
        response = json.loads(result.stdout)
        self.assertEqual(
            response["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_codex_subagent_stop_uses_plugin_data_and_shared_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            plugin_data = temporary / "plugin-data"
            env = self.codex_env(plugin_data)
            expected_hash = self.expected_review_hash(work)
            marker_dir = self.codex_marker_dir(work, plugin_data)
            self.assertRegex(marker_dir.name, r"^[0-9a-f]{64}$")
            for agent_type, heading, role, marker_name in REVIEW_CASES:
                payload = self.subagent_payload(work, agent_type, heading, role)
                result = self.run_codex_auto_mark(work, payload, env)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                marker = marker_dir / marker_name
                self.assertTrue(marker.exists(), result.stderr.decode())
                self.assertEqual(marker.read_text(encoding="utf-8"), expected_hash)
                self.assertIn(f"agent_id=agent-{role}", result.stderr.decode())
                self.assertFalse((self.absolute_git_dir(work) / marker_name).exists())

            self.assertEqual(
                {path.name for path in marker_dir.iterdir()}, set(MARKER_NAMES)
            )

            allowed = self.run_push_hook(work, env, turn_id="turn-test")
            self.assertEqual(allowed.returncode, 0, allowed.stderr.decode())
            self.assertEqual(allowed.stdout, b"")

            (work / "example.txt").write_text("changed again\n", encoding="utf-8")
            self.git(work, "add", "example.txt")
            self.git(work, "commit", "-m", "change state")
            self.assert_push_denied(
                self.run_push_hook(work, env, turn_id="turn-test")
            )

    def test_codex_default_subagent_fallback_uses_report_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            plugin_data = temporary / "plugin-data"
            env = self.codex_env(plugin_data)
            expected_hash = self.expected_review_hash(work)
            marker_dir = self.codex_marker_dir(work, plugin_data)

            for _, heading, role, marker_name in REVIEW_CASES:
                payload = self.subagent_payload(work, "default", heading, role)
                result = self.run_codex_auto_mark(work, payload, env)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                marker = marker_dir / marker_name
                self.assertTrue(marker.exists(), result.stderr.decode())
                self.assertEqual(marker.read_text(encoding="utf-8"), expected_hash)
                self.assertIn("agent_type=default", result.stderr.decode())

            allowed = self.run_push_hook(work, env, turn_id="turn-test")
            self.assertEqual(allowed.returncode, 0, allowed.stderr.decode())
            self.assertEqual(allowed.stdout, b"")

    def test_subagent_stop_rejects_bad_footer_unknown_agent_reentry_and_claude(self) -> None:
        marker_name = ".claude-pre-push-code-reviewed"
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            plugin_data = temporary / "plugin-data"
            env = self.codex_env(plugin_data)
            marker = self.codex_marker_dir(work, plugin_data) / marker_name
            payload = self.subagent_payload(
                work,
                "pre_push_correctness_reviewer",
                "# Correctness Review",
                "correctness",
            )

            payload["last_assistant_message"] = "# Correctness Review\n\nincomplete"
            self.assertEqual(
                self.run_codex_auto_mark(work, payload, env).returncode, 0
            )
            self.assertFalse(marker.exists())

            payload = self.subagent_payload(
                work, "another_reviewer", "# Correctness Review", "correctness"
            )
            self.assertEqual(
                self.run_codex_auto_mark(work, payload, env).returncode, 0
            )
            self.assertFalse(marker.exists())

            payload = self.subagent_payload(
                work,
                "pre_push_correctness_reviewer",
                "# Correctness Review",
                "correctness",
                stop_hook_active=True,
            )
            self.assertEqual(
                self.run_codex_auto_mark(work, payload, env).returncode, 0
            )
            self.assertFalse(marker.exists())

            payload = self.subagent_payload(
                work,
                "pre_push_correctness_reviewer",
                "# Correctness Review",
                "correctness",
            )
            payload.pop("turn_id")
            result = self.run_codex_auto_mark(
                work,
                payload,
                self.claude_env(plugin_data),
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
            self.assertFalse(marker.exists())

    def test_codex_plugin_data_separates_repositories_and_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            first_root = temporary / "first"
            second_root = temporary / "second"
            first_root.mkdir()
            second_root.mkdir()
            first = self.create_feature_repository(first_root)
            second = self.create_feature_repository(second_root)
            linked = temporary / "linked-worktree"
            self.git(
                first,
                "worktree",
                "add",
                "-b",
                "feature/linked",
                str(linked),
                "master",
            )
            (linked / "example.txt").write_text("linked change\n", encoding="utf-8")
            self.git(linked, "add", "example.txt")
            self.git(linked, "commit", "-m", "linked change")

            plugin_data = temporary / "plugin-data"
            env = self.codex_env(plugin_data)
            marker_dirs = set()
            for work in (first, second, linked):
                payload = self.subagent_payload(
                    work,
                    "pre_push_correctness_reviewer",
                    "# Correctness Review",
                    "correctness",
                )
                result = self.run_codex_auto_mark(work, payload, env)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                marker_dir = self.codex_marker_dir(work, plugin_data)
                marker_dirs.add(marker_dir)
                marker = marker_dir / MARKER_NAMES[0]
                self.assertTrue(marker.exists(), result.stderr.decode())
                self.assertEqual(
                    marker.read_text(encoding="utf-8"),
                    self.expected_review_hash(work),
                )

            self.assertEqual(len(marker_dirs), 3)
            marker_root = plugin_data / "pre-push-review" / "markers"
            self.assertEqual(
                {path.name for path in marker_root.iterdir()},
                {self.expected_repo_key(work) for work in (first, second, linked)},
            )

    def test_codex_missing_plugin_data_does_not_fallback_to_git_dir(self) -> None:
        for label, plugin_data in (("unset", None), ("empty", "")):
            with self.subTest(plugin_data=label), tempfile.TemporaryDirectory() as name:
                work = self.create_feature_repository(Path(name))
                payload = self.subagent_payload(
                    work,
                    "pre_push_correctness_reviewer",
                    "# Correctness Review",
                    "correctness",
                )
                result = self.run_codex_auto_mark(
                    work,
                    payload,
                    self.codex_missing_data_env(plugin_data),
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertFalse(
                    (self.absolute_git_dir(work) / MARKER_NAMES[0]).exists()
                )

    def test_codex_missing_plugin_data_gate_ignores_git_markers(self) -> None:
        for label, plugin_data in (("unset", None), ("empty", "")):
            with self.subTest(plugin_data=label), tempfile.TemporaryDirectory() as name:
                work = self.create_feature_repository(Path(name))
                self.write_git_markers(work, self.expected_review_hash(work))
                result = self.run_push_hook(
                    work,
                    self.codex_missing_data_env(plugin_data),
                    turn_id="turn-test",
                )
                self.assert_push_denied(result)

    def test_claude_surface_uses_git_markers(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            temporary = Path(name)
            work = self.create_feature_repository(temporary)
            plugin_data = temporary / "claude-plugin-data"
            self.write_git_markers(work, self.expected_review_hash(work))
            result = self.run_push_hook(work, self.claude_env(plugin_data))
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"")
            self.assertFalse(
                (self.codex_marker_dir(work, plugin_data) / MARKER_NAMES[0]).exists()
            )

    def test_codex_prefers_bare_plugin_data_over_compatibility_name(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            temporary = Path(name)
            work = self.create_feature_repository(temporary)
            compatibility_data = temporary / "compatibility-plugin-data"
            bare_data = temporary / "bare-plugin-data"
            env = self.codex_env(
                compatibility_data,
                bare_plugin_data=bare_data,
            )
            for agent_type, heading, role, marker_name in REVIEW_CASES:
                payload = self.subagent_payload(work, agent_type, heading, role)
                result = self.run_codex_auto_mark(work, payload, env)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                bare_marker = self.codex_marker_dir(work, bare_data) / marker_name
                self.assertTrue(bare_marker.exists(), result.stderr.decode())
                self.assertFalse(
                    (
                        self.codex_marker_dir(work, compatibility_data)
                        / marker_name
                    ).exists()
                )
                self.assertFalse(
                    (self.absolute_git_dir(work) / marker_name).exists()
                )

            allowed = self.run_push_hook(work, env, turn_id="turn-test")
            self.assertEqual(allowed.returncode, 0, allowed.stderr.decode())
            self.assertEqual(allowed.stdout, b"")


class SetupCodexAgentsTest(unittest.TestCase):
    def test_hook_config_registers_named_and_default_subagent_stop_adapter(
        self,
    ) -> None:
        config = json.loads(
            (PLUGIN_DIR / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        groups = config["hooks"]["SubagentStop"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            groups[0]["matcher"],
            "^(default|pre_push_(correctness|independent|security)_reviewer)$",
        )
        handlers = groups[0]["hooks"]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0]["type"], "command")
        self.assertIn("codex-auto-mark.sh", handlers[0]["command"])

    def test_review_skill_documents_current_spawn_agent_fallback(self) -> None:
        skill = (PLUGIN_DIR / "skills" / "review-codex" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("spawn_agent", skill)
        self.assertIn("`agent_type` selector", skill)
        self.assertIn("`default` fallback", skill)
        self.assertIn("byte-identical", skill)

    def run_setup(
        self, repo: Path, mode: str, *args: str
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(SETUP), mode, "--repo", str(repo), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def initialize_repository(self, root: Path) -> Path:
        repo = root / "repo"
        subprocess.run(
            ["git", "init", str(repo)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return repo

    def plan_token(self, output: bytes) -> str:
        match = re.search(rb"^plan-token\t([0-9a-f]{64})$", output, re.MULTILINE)
        self.assertIsNotNone(match, output.decode())
        assert match is not None
        return match.group(1).decode()

    def test_setup_requires_approved_current_plan_and_installs_valid_agents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            repo = self.initialize_repository(Path(temporary_name))
            inspect = self.run_setup(repo, "inspect")
            self.assertEqual(inspect.returncode, 0, inspect.stderr.decode())
            self.assertEqual(inspect.stdout.count(b"missing\t"), 3)
            token = self.plan_token(inspect.stdout)

            unapproved = self.run_setup(repo, "write")
            self.assertEqual(unapproved.returncode, 2)

            written = self.run_setup(repo, "write", "--plan-token", token)
            self.assertEqual(written.returncode, 0, written.stderr.decode())

            target_dir = repo / ".codex" / "agents"
            installed_names = set()
            for template in sorted(AGENT_TEMPLATE_DIR.glob("*.toml")):
                destination = target_dir / template.name
                self.assertEqual(destination.read_bytes(), template.read_bytes())
                parsed = tomllib.loads(destination.read_text(encoding="utf-8"))
                self.assertRegex(parsed["name"], r"^[a-z0-9_]+$")
                installed_names.add(parsed["name"])
                self.assertEqual(parsed["sandbox_mode"], "read-only")
                self.assertEqual(parsed["model_reasoning_effort"], "high")
                self.assertIn(
                    "<!-- pre-push-review:completed",
                    parsed["developer_instructions"],
                )
            self.assertEqual(
                installed_names,
                {
                    "pre_push_correctness_reviewer",
                    "pre_push_independent_reviewer",
                    "pre_push_security_reviewer",
                },
            )

            current = self.run_setup(repo, "inspect")
            self.assertEqual(current.returncode, 0, current.stderr.decode())
            self.assertEqual(current.stdout.count(b"current\t"), 3)

            stale_token = self.plan_token(current.stdout)
            changed = target_dir / "pre-push-correctness-reviewer.toml"
            changed.write_text("# local override\n", encoding="utf-8")
            stale = self.run_setup(repo, "write", "--plan-token", stale_token)
            self.assertEqual(stale.returncode, 1)
            self.assertEqual(changed.read_text(encoding="utf-8"), "# local override\n")

    def test_setup_refuses_symlink_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            repo = self.initialize_repository(root)
            target_dir = repo / ".codex" / "agents"
            target_dir.mkdir(parents=True)
            outside = root / "outside.toml"
            outside.write_text("outside\n", encoding="utf-8")
            destination = target_dir / "pre-push-correctness-reviewer.toml"
            destination.symlink_to(outside)

            inspect = self.run_setup(repo, "inspect")
            self.assertEqual(inspect.returncode, 0, inspect.stderr.decode())
            self.assertIn(b"unsafe-symlink\tpre-push-correctness-reviewer.toml", inspect.stdout)
            token = self.plan_token(inspect.stdout)
            written = self.run_setup(repo, "write", "--plan-token", token)
            self.assertEqual(written.returncode, 1)
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")


if __name__ == "__main__":
    unittest.main()

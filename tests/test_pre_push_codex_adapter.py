from __future__ import annotations

import hashlib
import json
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

            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin HEAD"},
            }
            result = subprocess.run(
                ["bash", str(HOOK)],
                cwd=work,
                input=json.dumps(payload).encode("utf-8"),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(CODEX_AUTO_MARK)],
            cwd=work.parent,
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_subagent_stop_updates_each_role_marker_with_shared_hash(self) -> None:
        cases = (
            (
                "pre-push-correctness-reviewer",
                "# Correctness Review",
                "correctness",
                ".claude-pre-push-code-reviewed",
            ),
            (
                "pre-push-independent-reviewer",
                "# Independent Review",
                "independent",
                ".claude-pre-push-codex-reviewed",
            ),
            (
                "pre-push-security-reviewer",
                "# Security Review",
                "security",
                ".claude-pre-push-security-reviewed",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            expected_hash = self.expected_review_hash(work)
            for agent_type, heading, role, marker_name in cases:
                payload = self.subagent_payload(work, agent_type, heading, role)
                result = self.run_codex_auto_mark(work, payload)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                marker = work / ".git" / marker_name
                self.assertTrue(marker.exists(), result.stderr.decode())
                self.assertEqual(marker.read_text(encoding="utf-8"), expected_hash)
                self.assertIn(f"agent_id=agent-{role}", result.stderr.decode())

            push_payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin HEAD"},
            }
            allowed = subprocess.run(
                ["bash", str(HOOK)],
                cwd=work,
                input=json.dumps(push_payload).encode("utf-8"),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr.decode())
            self.assertEqual(allowed.stdout, b"")

            (work / "example.txt").write_text("changed again\n", encoding="utf-8")
            invalidated = subprocess.run(
                ["bash", str(HOOK)],
                cwd=work,
                input=json.dumps(push_payload).encode("utf-8"),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(invalidated.returncode, 0, invalidated.stderr.decode())
            response = json.loads(invalidated.stdout)
            self.assertEqual(
                response["hookSpecificOutput"]["permissionDecision"], "deny"
            )

    def test_subagent_stop_rejects_bad_footer_unknown_agent_reentry_and_claude(self) -> None:
        marker_name = ".claude-pre-push-code-reviewed"
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            marker = work / ".git" / marker_name
            payload = self.subagent_payload(
                work,
                "pre-push-correctness-reviewer",
                "# Correctness Review",
                "correctness",
            )

            payload["last_assistant_message"] = "# Correctness Review\n\nincomplete"
            self.assertEqual(self.run_codex_auto_mark(work, payload).returncode, 0)
            self.assertFalse(marker.exists())

            payload = self.subagent_payload(
                work, "another-reviewer", "# Correctness Review", "correctness"
            )
            self.assertEqual(self.run_codex_auto_mark(work, payload).returncode, 0)
            self.assertFalse(marker.exists())

            payload = self.subagent_payload(
                work,
                "pre-push-correctness-reviewer",
                "# Correctness Review",
                "correctness",
                stop_hook_active=True,
            )
            self.assertEqual(self.run_codex_auto_mark(work, payload).returncode, 0)
            self.assertFalse(marker.exists())

            payload = self.subagent_payload(
                work,
                "pre-push-correctness-reviewer",
                "# Correctness Review",
                "correctness",
            )
            payload.pop("turn_id")
            result = self.run_codex_auto_mark(work, payload)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
            self.assertFalse(marker.exists())


class SetupCodexAgentsTest(unittest.TestCase):
    def test_hook_config_registers_named_codex_subagent_stop_adapter(self) -> None:
        config = json.loads(
            (PLUGIN_DIR / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        groups = config["hooks"]["SubagentStop"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            groups[0]["matcher"],
            "^pre-push-(correctness|independent|security)-reviewer$",
        )
        handlers = groups[0]["hooks"]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0]["type"], "command")
        self.assertIn("codex-auto-mark.sh", handlers[0]["command"])

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
            for template in sorted(AGENT_TEMPLATE_DIR.glob("*.toml")):
                destination = target_dir / template.name
                self.assertEqual(destination.read_bytes(), template.read_bytes())
                parsed = tomllib.loads(destination.read_text(encoding="utf-8"))
                self.assertTrue(parsed["name"].startswith("pre-push-"))
                self.assertEqual(parsed["sandbox_mode"], "read-only")
                self.assertEqual(parsed["model_reasoning_effort"], "high")
                self.assertIn(
                    "<!-- pre-push-review:completed",
                    parsed["developer_instructions"],
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

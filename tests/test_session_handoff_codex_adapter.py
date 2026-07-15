from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "session-handoff"
HOOKS = PLUGIN / "hooks" / "hooks.json"
SAVE = PLUGIN / "hooks" / "scripts" / "save-codex-handoff.sh"
INJECT = PLUGIN / "hooks" / "scripts" / "inject-pending-handoff.sh"
SETUP = PLUGIN / "scripts" / "setup-codex-summary.sh"
MARKER_CONTENT = b"session-handoff:nested-codex-summary-opt-in:v1"


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
@unittest.skipUnless(shutil.which("git"), "hook integration requires git")
@unittest.skipUnless(
    shutil.which("sha256sum") or shutil.which("shasum"),
    "opt-in helper integration requires sha256sum or shasum",
)
class SessionHandoffCodexAdapterTest(unittest.TestCase):
    def test_readme_uses_plugin_namespaced_codex_skill(self) -> None:
        readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
        self.assertIn("$session-handoff:setup", readme)
        self.assertNotIn("`$setup`", readme)
        skill = (PLUGIN / "skills" / "setup" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for required_contract in (
            "必ず read-only inspect から始める",
            "Provider/privacy 境界を説明して明示承認を得る",
            "--ignore-user-config",
            "default provider/account",
            "--plan-token",
        ):
            self.assertIn(required_contract, skill)

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        self.git(root, "init", str(repo))
        return repo

    def make_codex_stub(self, root: Path) -> tuple[Path, Path, Path, Path]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        args_log = root / "codex-args.log"
        stdin_log = root / "codex-stdin.log"
        recursion_log = root / "codex-recursion.log"
        codex = fake_bin / "codex"
        codex.write_text(
            """#!/bin/bash
output=""
last_arg=""
for arg in "$@"; do
  last_arg=$arg
done
printf '%s\\n' "$@" > "$CODEX_ARGS_LOG"
printf '%s' "${SESSION_HANDOFF_CODEX_PRECOMPACT_ACTIVE:-}" > "$CODEX_RECURSION_LOG"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-last-message|-o)
      shift
      output=${1:-}
      ;;
  esac
  shift
done
cat > "$CODEX_STDIN_LOG"
if [ "$last_arg" != "-" ]; then
  exit 44
fi
if ! grep -Fq '# session-handoff: Codex PreCompact summary contract' "$CODEX_STDIN_LOG" ||
   ! grep -Fq '<transcript>' "$CODEX_STDIN_LOG" ||
   ! grep -Fq '</transcript>' "$CODEX_STDIN_LOG"; then
  exit 45
fi
if [ "${CODEX_STUB_FAIL:-0}" = "1" ]; then
  exit 42
fi
if [ -z "$output" ]; then
  exit 43
fi
if [ "${CODEX_STUB_MALFORMED:-0}" = "1" ]; then
  printf '%s\\n' '# Session handoff' 'missing required sections' > "$output"
  exit 0
fi
printf '%s\\n' \
  '# Session handoff' \
  '## Objective and intent' \
  'fixture summary' \
  '## Completed work' \
  'fixture completed' \
  '## Current state and evidence' \
  'fixture evidence' \
  '## Decisions and constraints' \
  'fixture constraints' \
  '## Remaining work' \
  'fixture remaining' \
  '## Risks and verification' \
  'fixture risks' \
  > "$output"
""",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        return fake_bin, args_log, stdin_log, recursion_log

    def codex_env(
        self,
        fake_bin: Path,
        args_log: Path,
        stdin_log: Path,
        recursion_log: Path,
    ) -> dict[str, str]:
        env = os.environ.copy()
        isolated_tmp = fake_bin.parent / "tmp"
        isolated_tmp.mkdir(exist_ok=True)
        env.update(
            {
                "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                "TMPDIR": str(isolated_tmp),
                "CODEX_ARGS_LOG": str(args_log),
                "CODEX_STDIN_LOG": str(stdin_log),
                "CODEX_RECURSION_LOG": str(recursion_log),
            }
        )
        env.pop("SESSION_HANDOFF_CODEX_PRECOMPACT_ACTIVE", None)
        return env

    def run_setup(
        self,
        repo: Path,
        action: str,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SETUP), action, "--repo", str(repo), *args],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def inspect_opt_in(self, repo: Path) -> dict[str, str]:
        result = self.run_setup(repo, "inspect")
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("\t", 1) for line in result.stdout.splitlines())

    def enable_opt_in(self, repo: Path) -> Path:
        fields = self.inspect_opt_in(repo)
        result = self.run_setup(
            repo,
            "enable",
            "--plan-token",
            fields["enable-plan-token"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = Path(fields["target"])
        self.assertEqual(marker.read_bytes(), MARKER_CONTENT)
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        return marker

    def run_hook(
        self,
        script: Path,
        payload: object,
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(script)],
            cwd=cwd,
            env=env,
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_hook_contract_registers_codex_precompact_and_compact_delivery(self) -> None:
        hooks = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]
        producer = hooks["PreCompact"][0]
        self.assertEqual(producer["matcher"], "auto|manual")
        self.assertEqual(producer["hooks"][0]["type"], "command")
        self.assertTrue(
            producer["hooks"][0]["command"].endswith(
                "/hooks/scripts/save-codex-handoff.sh"
            )
        )
        self.assertEqual(producer["hooks"][0]["timeout"], 180)
        self.assertEqual(
            hooks["SessionStart"][0]["matcher"],
            "clear|startup|resume|compact",
        )

    def test_default_disabled_does_not_read_transcript_or_start_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            fake_bin, args_log, stdin_log, recursion_log = self.make_codex_stub(
                temporary
            )
            env = self.codex_env(fake_bin, args_log, stdin_log, recursion_log)

            result = self.run_hook(
                SAVE,
                {
                    "hook_event_name": "PreCompact",
                    "turn_id": "turn-fixture",
                    "trigger": "auto",
                    "session_id": "session",
                    # Missing on purpose: default-disabled must return before validation/read.
                    "transcript_path": str(temporary / "must-not-be-read.jsonl"),
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"")
            self.assertFalse(args_log.exists())
            self.assertFalse(stdin_log.exists())
            self.assertFalse(recursion_log.exists())
            self.assertFalse((repo / ".git" / "session-handoff").exists())

    def test_opt_in_helper_requires_fresh_action_bound_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            handoff_dir = repo / ".git" / "session-handoff"

            fields = self.inspect_opt_in(repo)
            self.assertEqual(fields["state"], "disabled")
            self.assertEqual(fields["protocol"], "v1")
            self.assertEqual(fields["exact-content"].encode(), MARKER_CONTENT)
            self.assertIn("default provider/account", fields["privacy-boundary"])
            self.assertFalse(handoff_dir.exists(), "inspect must be read-only")

            missing_token = self.run_setup(repo, "enable")
            self.assertEqual(missing_token.returncode, 2)
            self.assertFalse(handoff_dir.exists())

            wrong_action = self.run_setup(
                repo,
                "enable",
                "--plan-token",
                fields["disable-plan-token"],
            )
            self.assertNotEqual(wrong_action.returncode, 0)
            self.assertFalse(handoff_dir.exists())

            enabled = self.run_setup(
                repo,
                "enable",
                "--plan-token",
                fields["enable-plan-token"],
            )
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            marker = Path(fields["target"])
            self.assertFalse(marker.is_symlink())
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_bytes(), MARKER_CONTENT)
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

            stale = self.run_setup(
                repo,
                "enable",
                "--plan-token",
                fields["enable-plan-token"],
            )
            self.assertNotEqual(stale.returncode, 0)

            enabled_fields = self.inspect_opt_in(repo)
            self.assertEqual(enabled_fields["state"], "enabled")
            disabled = self.run_setup(
                repo,
                "disable",
                "--plan-token",
                enabled_fields["disable-plan-token"],
            )
            self.assertEqual(disabled.returncode, 0, disabled.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(self.inspect_opt_in(repo)["state"], "disabled")

    def test_opt_in_helper_rejects_symlink_and_nonregular_marker(self) -> None:
        for marker_kind in ("symlink", "directory"):
            with self.subTest(marker_kind=marker_kind), tempfile.TemporaryDirectory() as temporary_name:
                temporary = Path(temporary_name)
                repo = self.make_repo(temporary)
                handoff_dir = repo / ".git" / "session-handoff"
                handoff_dir.mkdir(mode=0o700)
                marker = handoff_dir / ".codex-summary-opt-in"
                outside = temporary / "outside"
                if marker_kind == "symlink":
                    outside.write_text("do not change", encoding="utf-8")
                    marker.symlink_to(outside)
                    expected_state = "unsafe-symlink"
                else:
                    marker.mkdir()
                    expected_state = "unsafe-nonregular"

                fields = self.inspect_opt_in(repo)
                self.assertEqual(fields["state"], expected_state)
                for action in ("enable", "disable"):
                    result = self.run_setup(
                        repo,
                        action,
                        "--plan-token",
                        fields[f"{action}-plan-token"],
                    )
                    self.assertNotEqual(result.returncode, 0)

                if marker_kind == "symlink":
                    self.assertTrue(marker.is_symlink())
                    self.assertEqual(outside.read_text(encoding="utf-8"), "do not change")
                else:
                    self.assertTrue(marker.is_dir())

    def test_hook_rejects_changed_or_unsafe_opt_in_marker(self) -> None:
        for marker_kind in ("different-content", "different-mode", "symlink"):
            with self.subTest(marker_kind=marker_kind), tempfile.TemporaryDirectory() as temporary_name:
                temporary = Path(temporary_name)
                repo = self.make_repo(temporary)
                transcript = temporary / "transcript.jsonl"
                transcript.write_text("transcript\n", encoding="utf-8")
                marker = self.enable_opt_in(repo)
                outside = temporary / "outside-marker"
                if marker_kind == "different-content":
                    marker.write_text("wrong protocol", encoding="utf-8")
                elif marker_kind == "different-mode":
                    marker.chmod(0o644)
                else:
                    marker.unlink()
                    outside.write_bytes(MARKER_CONTENT)
                    marker.symlink_to(outside)

                fake_bin, args_log, stdin_log, recursion_log = self.make_codex_stub(
                    temporary
                )
                env = self.codex_env(fake_bin, args_log, stdin_log, recursion_log)
                result = self.run_hook(
                    SAVE,
                    {
                        "hook_event_name": "PreCompact",
                        "turn_id": "turn-fixture",
                        "trigger": "manual",
                        "session_id": "session",
                        "transcript_path": str(transcript),
                        "cwd": str(repo),
                    },
                    cwd=repo,
                    env=env,
                )

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                warning = json.loads(result.stdout)
                self.assertTrue(warning["continue"])
                self.assertIn("opt-in marker", warning["systemMessage"])
                self.assertFalse(args_log.exists())
                self.assertFalse(stdin_log.exists())
                self.assertFalse(recursion_log.exists())

    def test_precompact_saves_tail_atomically_and_compact_injects_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            transcript = temporary / "transcript.jsonl"
            transcript.write_bytes(
                b'{"old":"' + (b"A" * 70000) + b'"}\n{"latest":"LATEST"}\n'
            )
            fake_bin, args_log, stdin_log, recursion_log = self.make_codex_stub(
                temporary
            )
            env = self.codex_env(fake_bin, args_log, stdin_log, recursion_log)
            self.enable_opt_in(repo)
            env["SESSION_HANDOFF_CODEX_TRANSCRIPT_MAX_BYTES"] = "65536"
            payload = {
                "hook_event_name": "PreCompact",
                "turn_id": "turn-fixture",
                "trigger": "auto",
                "session_id": "session/with unsafe chars",
                "transcript_path": str(transcript),
                "cwd": str(repo),
            }

            generated = self.run_hook(SAVE, payload, cwd=repo, env=env)
            self.assertEqual(generated.returncode, 0, generated.stderr.decode())
            self.assertEqual(generated.stdout, b"")

            handoff_dir = repo / ".git" / "session-handoff"
            pending = list(handoff_dir.glob("pending-*.md"))
            self.assertEqual(len(pending), 1)
            self.assertIn("fixture summary", pending[0].read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(pending[0].stat().st_mode), 0o600)
            raw_tail = transcript.read_bytes()[-65537:]
            expected_excerpt = raw_tail.split(b"\n", 1)[1]
            request = stdin_log.read_bytes()
            self.assertIn(
                b"# session-handoff: Codex PreCompact summary contract", request
            )
            self.assertIn(b"Runtime metadata (trusted", request)
            self.assertEqual(request.count(b"<transcript>\n"), 1)
            self.assertEqual(request.count(b"\n</transcript>\n"), 1)
            excerpt = request.split(b"<transcript>\n", 1)[1].rsplit(
                b"\n</transcript>\n", 1
            )[0]
            self.assertEqual(excerpt, expected_excerpt)
            self.assertLessEqual(len(excerpt), 65536)
            self.assertTrue(excerpt.endswith(b'{"latest":"LATEST"}\n'))
            self.assertEqual(recursion_log.read_text(encoding="utf-8"), "1")
            args = args_log.read_text(encoding="utf-8")
            for expected in (
                "--ignore-user-config",
                "--ignore-rules",
                "--disable",
                "hooks",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--output-last-message",
            ):
                self.assertIn(expected, args)
            self.assertEqual(args.splitlines()[-1], "-")
            self.assertNotIn("# session-handoff", args)
            self.assertEqual(list(handoff_dir.glob(".codex-handoff-*")), [])
            self.assertEqual(list((temporary / "tmp").glob("session-handoff-codex.*")), [])

            injected = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": "session/with unsafe chars",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(injected.returncode, 0, injected.stderr.decode())
            response = json.loads(injected.stdout)
            context = response["hookSpecificOutput"]["additionalContext"]
            self.assertIn("fixture summary", context)
            self.assertEqual(list(handoff_dir.glob("pending-*.md")), [])
            self.assertEqual(len(list(handoff_dir.glob("consumed-*.md"))), 1)

            second = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "resume",
                    "session_id": "session/with unsafe chars",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(second.returncode, 0, second.stderr.decode())
            self.assertEqual(second.stdout, b"")

    def test_hook_fields_preserve_newlines_in_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = temporary / "repo\nwith-newline"
            self.git(temporary, "init", str(repo))
            transcript = temporary / "transcript\nwith-newline.jsonl"
            transcript.write_text("latest transcript\n", encoding="utf-8")
            handoff_dir = repo / ".git" / "session-handoff"
            handoff_dir.mkdir(mode=0o700)
            marker = handoff_dir / ".codex-summary-opt-in"
            marker.write_bytes(MARKER_CONTENT)
            marker.chmod(0o600)
            fake_bin, args_log, stdin_log, recursion_log = self.make_codex_stub(
                temporary
            )
            env = self.codex_env(fake_bin, args_log, stdin_log, recursion_log)

            generated = self.run_hook(
                SAVE,
                {
                    "hook_event_name": "PreCompact",
                    "turn_id": "turn-fixture",
                    "trigger": "manual",
                    "session_id": "newline-session",
                    "transcript_path": str(transcript),
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr.decode())
            self.assertEqual(generated.stdout, b"")
            self.assertEqual(len(list(handoff_dir.glob("pending-*.md"))), 1)

            injected = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": "newline-session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(injected.returncode, 0, injected.stderr.decode())
            self.assertIn("fixture summary", injected.stdout.decode("utf-8"))

    def test_turn_id_and_recursion_guards_do_not_start_nested_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            transcript = temporary / "transcript.jsonl"
            transcript.write_text("transcript\n", encoding="utf-8")
            fake_bin, args_log, stdin_log, recursion_log = self.make_codex_stub(
                temporary
            )
            env = self.codex_env(fake_bin, args_log, stdin_log, recursion_log)
            marker = self.enable_opt_in(repo)
            payload = {
                "hook_event_name": "PreCompact",
                "trigger": "manual",
                "session_id": "session",
                "transcript_path": str(transcript),
                "cwd": str(repo),
            }

            claude_runtime = self.run_hook(SAVE, payload, cwd=repo, env=env)
            self.assertEqual(claude_runtime.returncode, 0)
            self.assertEqual(claude_runtime.stdout, b"")
            self.assertFalse(args_log.exists())

            payload["turn_id"] = "turn-fixture"
            env["SESSION_HANDOFF_CODEX_PRECOMPACT_ACTIVE"] = "1"
            recursive = self.run_hook(SAVE, payload, cwd=repo, env=env)
            self.assertEqual(recursive.returncode, 0)
            self.assertEqual(recursive.stdout, b"")
            self.assertFalse(args_log.exists())
            self.assertTrue(marker.exists())
            self.assertEqual(
                list((repo / ".git" / "session-handoff").glob("pending-*.md")),
                [],
            )

    def test_resume_leaves_generic_pending_for_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            handoff_dir = repo / ".git" / "session-handoff"
            handoff_dir.mkdir(mode=0o700)
            pending = handoff_dir / "pending-fixture.md"
            pending.write_text("# fixture handoff\n", encoding="utf-8")
            env = os.environ.copy()

            compact = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": "session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(compact.returncode, 0)
            self.assertEqual(compact.stdout, b"")
            self.assertTrue(pending.exists())

            resume = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "resume",
                    "session_id": "session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(resume.returncode, 0, resume.stderr.decode())
            self.assertEqual(resume.stdout, b"")
            self.assertTrue(pending.exists())

            clear = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "clear",
                    "session_id": "session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(clear.returncode, 0, clear.stderr.decode())
            self.assertIn("fixture handoff", clear.stdout.decode("utf-8"))
            self.assertFalse(pending.exists())

    def test_resume_consumes_only_same_session_codex_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            handoff_dir = repo / ".git" / "session-handoff"
            handoff_dir.mkdir(mode=0o700)
            same = handoff_dir / "pending-codex-session-123-ABCDEF.md"
            other = handoff_dir / "pending-codex-session-other-124-ABCDEF.md"
            same.write_text("# same session\n", encoding="utf-8")
            other.write_text("# other session\n", encoding="utf-8")

            result = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "resume",
                    "session_id": "session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=os.environ.copy(),
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn("same session", result.stdout.decode("utf-8"))
            self.assertFalse(same.exists())
            self.assertTrue(other.exists())

    def test_compact_does_not_consume_another_codex_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            handoff_dir = repo / ".git" / "session-handoff"
            handoff_dir.mkdir(mode=0o700)
            other = handoff_dir / "pending-codex-other-session-123-ABCDEF.md"
            other.write_text("# another session\n", encoding="utf-8")
            env = os.environ.copy()

            result = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": "other",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertTrue(other.exists())

    def test_wrong_event_does_not_claim_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            handoff_dir = repo / ".git" / "session-handoff"
            handoff_dir.mkdir(mode=0o700)
            pending = handoff_dir / "pending-fixture.md"
            pending.write_text("# fixture\n", encoding="utf-8")
            env = os.environ.copy()

            result = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "PreCompact",
                    "source": "clear",
                    "session_id": "session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertTrue(pending.exists())

    def test_missing_transcript_warns_and_fails_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            fake_bin, args_log, stdin_log, recursion_log = self.make_codex_stub(
                temporary
            )
            env = self.codex_env(fake_bin, args_log, stdin_log, recursion_log)
            marker = self.enable_opt_in(repo)
            result = self.run_hook(
                SAVE,
                {
                    "hook_event_name": "PreCompact",
                    "turn_id": "turn-fixture",
                    "trigger": "manual",
                    "session_id": "session",
                    "transcript_path": str(temporary / "missing.jsonl"),
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0)
            warning = json.loads(result.stdout)
            self.assertTrue(warning["continue"])
            self.assertIn("transcript", warning["systemMessage"])
            self.assertFalse(args_log.exists())
            self.assertTrue(marker.exists())
            self.assertEqual(
                list((repo / ".git" / "session-handoff").glob("pending-*.md")),
                [],
            )

    def test_nested_codex_failure_warns_cleans_up_and_fails_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            transcript = temporary / "transcript.jsonl"
            transcript.write_text("transcript\n", encoding="utf-8")
            fake_bin, args_log, stdin_log, recursion_log = self.make_codex_stub(
                temporary
            )
            env = self.codex_env(fake_bin, args_log, stdin_log, recursion_log)
            self.enable_opt_in(repo)
            env["CODEX_STUB_FAIL"] = "1"
            result = self.run_hook(
                SAVE,
                {
                    "hook_event_name": "PreCompact",
                    "turn_id": "turn-fixture",
                    "trigger": "auto",
                    "session_id": "session",
                    "transcript_path": str(transcript),
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0)
            warning = json.loads(result.stdout)
            self.assertTrue(warning["continue"])
            self.assertIn("nested codex", warning["systemMessage"])
            handoff_dir = repo / ".git" / "session-handoff"
            self.assertEqual(list(handoff_dir.glob("pending-*.md")), [])
            self.assertEqual(list(handoff_dir.glob(".codex-handoff-*")), [])

    def test_malformed_markdown_warns_and_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            transcript = temporary / "transcript.jsonl"
            transcript.write_text("transcript\n", encoding="utf-8")
            fake_bin, args_log, stdin_log, recursion_log = self.make_codex_stub(
                temporary
            )
            env = self.codex_env(fake_bin, args_log, stdin_log, recursion_log)
            self.enable_opt_in(repo)
            env["CODEX_STUB_MALFORMED"] = "1"
            result = self.run_hook(
                SAVE,
                {
                    "hook_event_name": "PreCompact",
                    "turn_id": "turn-fixture",
                    "trigger": "manual",
                    "session_id": "session",
                    "transcript_path": str(transcript),
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0)
            warning = json.loads(result.stdout)
            self.assertTrue(warning["continue"])
            self.assertIn("Markdown", warning["systemMessage"])
            handoff_dir = repo / ".git" / "session-handoff"
            self.assertEqual(list(handoff_dir.glob("pending-*.md")), [])
            self.assertEqual(list(handoff_dir.glob(".codex-handoff-*")), [])


if __name__ == "__main__":
    unittest.main()

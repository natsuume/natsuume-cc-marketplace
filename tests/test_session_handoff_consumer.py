from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "session-handoff"
INJECT = PLUGIN / "hooks" / "scripts" / "inject-pending-handoff.sh"
HOOKS = PLUGIN / "hooks" / "hooks.json"
PREAMBLE = PLUGIN / "hooks" / "prompts" / "inject-preamble.md"
HANDOFF_INSTRUCTION = PLUGIN / "hooks" / "prompts" / "handoff-instruction.md"

# additionalContext の長さ (Unicode code point 数) の閾値。これ以下なら handoff 全文を
# 注入し、超える場合は handoff ファイルの Read 指示へ縮退する。
CONTEXT_LIMIT = 8000
# 縮退時の additionalContext 全体の上限 (code point 数)。
DEGRADED_CONTEXT_LIMIT = 2000
REMAINING_HEADING = "他にも直近の handoff が残っています:"
BODY_MARKER = "HANDOFF-BODY-MARKER"
# 本文の詰め物。UTF-8 で 3 byte になる文字を使い、長さの計測が byte 数ではなく
# code point 数であることを検証する。
BODY_FILLER = "字"


def preamble_text() -> str:
    # consumer は preamble と本文を bash の $(cat ...) で読むため、末尾の改行は落ちる。
    return PREAMBLE.read_text(encoding="utf-8").rstrip("\n")


def expected_full_context(body: str, remaining: list[Path]) -> str:
    context = preamble_text() + "\n\n" + body.rstrip("\n")
    if remaining:
        listing = "".join(f"\n- {path}" for path in remaining)
        context += "\n\n" + REMAINING_HEADING + listing
    return context


def body_for_context_length(target: int, remaining: list[Path]) -> str:
    """全文注入時の additionalContext がちょうど target code point になる本文を返す。"""
    head = f"# {BODY_MARKER} 引き継ぎ\n"
    filler_length = target - len(expected_full_context("", remaining)) - len(head)
    if filler_length <= 0:
        raise ValueError(f"target {target} is too short for the fixture body")
    return head + BODY_FILLER * filler_length


def degraded_notice(consumed_path: Path) -> str:
    return (
        "(session-handoff) 直前セッションの handoff が長いため本文は注入していない。"
        f"`{consumed_path}` を Read で全文読了してから作業を開始すること。"
    )


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
@unittest.skipUnless(shutil.which("git"), "hook integration requires git")
class SessionHandoffPendingConsumerTest(unittest.TestCase):
    """inject-pending-handoff.sh (Claude 側 consumer) の検証。

    save-codex-handoff.sh・codex-summary-opt-in・setup-codex-summary 等の Codex
    producer 側と、pending-codex-* の同一 session_id マッチング分岐 (Phase B で
    inject-pending-handoff.sh から削除される) は対象外。ここでは producer を問わない
    汎用 pending ファイルの consumer 挙動のみを検証する。
    """

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

    def make_handoff_dir(self, temporary: Path) -> tuple[Path, Path]:
        """fixture repo と、consumer が解決するのと同じ絶対パスの handoff ディレクトリを返す。"""
        repo = self.make_repo(temporary)
        git_dir = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        handoff_dir = (
            Path(git_dir.stdout.decode("utf-8").rstrip("\n")) / "session-handoff"
        )
        handoff_dir.mkdir(mode=0o700)
        return repo, handoff_dir

    def consume_on_clear(self, repo: Path) -> str:
        """SessionStart(clear) で consumer を実行し、additionalContext を返す。"""
        result = self.run_hook(
            INJECT,
            {
                "hook_event_name": "SessionStart",
                "source": "clear",
                "session_id": "session",
                "cwd": str(repo),
            },
            cwd=repo,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotEqual(result.stdout, b"", result.stderr.decode())
        output = json.loads(result.stdout.decode("utf-8"))
        hook_output = output["hookSpecificOutput"]
        self.assertEqual("SessionStart", hook_output["hookEventName"])
        return hook_output["additionalContext"]

    def assert_full_injection(self, context_length: int) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            repo, handoff_dir = self.make_handoff_dir(Path(temporary_name))
            pending = handoff_dir / "pending-fixture.md"
            consumed = handoff_dir / "consumed-fixture.md"
            body = body_for_context_length(context_length, [])
            pending.write_text(body + "\n", encoding="utf-8")
            expected = expected_full_context(body, [])
            self.assertEqual(context_length, len(expected))

            context = self.consume_on_clear(repo)

            self.assertEqual(expected, context)
            self.assertFalse(pending.exists())
            self.assertTrue(consumed.exists())

    def test_context_below_limit_injects_full_handoff(self) -> None:
        self.assert_full_injection(CONTEXT_LIMIT - 1000)

    def test_context_exactly_at_limit_injects_full_handoff(self) -> None:
        self.assert_full_injection(CONTEXT_LIMIT)

    def assert_degraded_context(
        self, context: str, consumed: Path, original_body: str
    ) -> None:
        # 本文が含まれる場合に数千文字の additionalContext を失敗出力へ展開しないよう、
        # assertNotIn ではなく長さだけを示すメッセージで判定する。
        injected_body_message = (
            f"handoff 本文が additionalContext に含まれている (長さ {len(context)})"
        )
        self.assertFalse(BODY_MARKER in context, injected_body_message)
        self.assertFalse(BODY_FILLER * 20 in context, injected_body_message)
        self.assertIn(preamble_text(), context)
        self.assertIn(degraded_notice(consumed), context)
        self.assertLessEqual(len(context), DEGRADED_CONTEXT_LIMIT)
        self.assertTrue(consumed.exists())
        self.assertEqual(
            original_body.rstrip("\n"),
            consumed.read_text(encoding="utf-8").rstrip("\n"),
        )

    def test_context_over_limit_degrades_to_read_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            repo, handoff_dir = self.make_handoff_dir(Path(temporary_name))
            pending = handoff_dir / "pending-fixture.md"
            consumed = handoff_dir / "consumed-fixture.md"
            body = body_for_context_length(CONTEXT_LIMIT + 1, [])
            pending.write_text(body + "\n", encoding="utf-8")

            context = self.consume_on_clear(repo)

            self.assert_degraded_context(context, consumed, body)
            self.assertFalse(pending.exists())
            self.assertNotIn(REMAINING_HEADING, context)

    def test_context_over_limit_with_remaining_pending_lists_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            repo, handoff_dir = self.make_handoff_dir(Path(temporary_name))
            newer_pending = handoff_dir / "pending-newer.md"
            newer_consumed = handoff_dir / "consumed-newer.md"
            older_pending = handoff_dir / "pending-older.md"
            body = body_for_context_length(CONTEXT_LIMIT + 4000, [older_pending])
            newer_pending.write_text(body + "\n", encoding="utf-8")
            older_pending.write_text("# older handoff\n", encoding="utf-8")
            now = time.time()
            os.utime(older_pending, (now - 3600, now - 3600))
            os.utime(newer_pending, (now - 60, now - 60))

            context = self.consume_on_clear(repo)

            self.assert_degraded_context(context, newer_consumed, body)
            self.assertFalse(newer_pending.exists())
            self.assertTrue(older_pending.exists())
            self.assertIn(f"{REMAINING_HEADING}\n- {older_pending}", context)
            self.assertLess(
                context.index(degraded_notice(newer_consumed)),
                context.index(REMAINING_HEADING),
            )


class SessionHandoffInstructionContractTest(unittest.TestCase):
    """handoff-instruction.md (handoff 執筆指示) の記述量の目安。"""

    LENGTH_GUIDANCE = (
        "本文は 6,000 文字以内を目安とし、"
        "超える場合は参照ファイルパスの列挙を優先して残してください。"
    )

    @staticmethod
    def without_whitespace(text: str) -> str:
        return re.sub(r"\s+", "", text)

    def test_instruction_states_length_guidance(self) -> None:
        instruction = HANDOFF_INSTRUCTION.read_text(encoding="utf-8")
        self.assertIn(
            self.without_whitespace(self.LENGTH_GUIDANCE),
            self.without_whitespace(instruction),
        )


class SessionHandoffHooksContractTest(unittest.TestCase):
    """hooks/hooks.json の配送契約。

    consumer (inject-pending-handoff.sh) は SessionStart の `clear|startup` にだけ
    登録し、PreCompact には登録しない。producer (detect-context-threshold.sh) は
    ツールの成否を問わず閾値検知を行うため、matcher `*` で PostToolUse と
    PostToolUseFailure の両方に同じ引数で登録する。
    """

    DETECT_CONTEXT_THRESHOLD_COMMAND = (
        "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/detect-context-threshold.sh"
    )

    @staticmethod
    def load_hooks() -> dict:
        return json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]

    def test_precompact_entry_removed_and_session_start_matcher_narrowed(self) -> None:
        hooks = self.load_hooks()

        self.assertNotIn("PreCompact", hooks)

        session_start_groups = hooks["SessionStart"]
        self.assertEqual(1, len(session_start_groups))
        self.assertEqual("clear|startup", session_start_groups[0]["matcher"])

        commands = [handler["command"] for handler in session_start_groups[0]["hooks"]]
        self.assertTrue(
            any(
                command.endswith("/hooks/scripts/inject-pending-handoff.sh")
                for command in commands
            ),
            commands,
        )

    def test_detection_hook_is_registered_for_tool_success_and_failure(self) -> None:
        hooks = self.load_hooks()
        for event in ("PostToolUse", "PostToolUseFailure"):
            with self.subTest(event=event):
                entries = [
                    (group.get("matcher"), handler["command"], handler.get("args"))
                    for group in hooks.get(event, [])
                    for handler in group["hooks"]
                    if handler.get("type") == "command"
                ]
                self.assertIn(
                    ("*", self.DETECT_CONTEXT_THRESHOLD_COMMAND, []),
                    entries,
                    f"{event} に matcher '*' の "
                    f"{self.DETECT_CONTEXT_THRESHOLD_COMMAND} (args: []) を登録する: "
                    f"{entries!r}",
                )


if __name__ == "__main__":
    unittest.main()

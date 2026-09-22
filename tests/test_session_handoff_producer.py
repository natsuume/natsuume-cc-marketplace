"""session-handoff の検知 hook (detect-context-threshold.sh) の契約。

検知 hook は context 使用率キャッシュを読み、閾値 (既定 60%) を超えていれば handoff
作成指示を additionalContext として 1 セッション 1 回だけ注入する。判定は
`hook_event_name` に依らず同じであり、ツールが成功した `PostToolUse` でも失敗した
`PostToolUseFailure` でも同じ閾値判定を行う (`tool_response` は参照しない)。出力の
`hookSpecificOutput.hookEventName` は入力の `hook_event_name` と一致させる。

state (marker) と cache はどちらも `TMPDIR` 配下に作られるため、テストは `TMPDIR` を
一時ディレクトリへ向けて利用者の state に触れずに実行する。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "session-handoff"
HOOK = PLUGIN / "hooks" / "scripts" / "detect-context-threshold.sh"
README = PLUGIN / "README.md"

FAILURE_EVENT = "PostToolUseFailure"
SESSION_ID = "session-handoff-producer"
# 既定閾値 (60) を超える値と、下回る値。
USED_PERCENTAGE_ABOVE_THRESHOLD = 80
USED_PERCENTAGE_BELOW_THRESHOLD = 10
# PostToolUseFailure 入力の `error` (先頭行は `Exit code N`)。
FAILURE_ERROR = "Exit code 1\nfatal: some stderr"


@unittest.skipUnless(shutil.which("jq"), "検知 hook の実行には jq が要る")
@unittest.skipUnless(shutil.which("git"), "検知 hook の実行には git が要る")
class ContextThresholdDetectionTest(unittest.TestCase):
    """detect-context-threshold.sh に stdin JSON を与えたときの分岐と出力形。"""

    def setUp(self) -> None:
        if not HOOK.is_file():
            self.fail(f"detect-context-threshold script is missing: {HOOK}")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)

        self.repo = base / "repo"
        self.repo.mkdir()
        self.temporary_tmpdir = base / "tmp"
        self.temporary_tmpdir.mkdir()
        empty_git_config = base / "gitconfig"
        empty_git_config.write_text("", encoding="utf-8")

        self.env = os.environ.copy()
        self.env["TMPDIR"] = str(self.temporary_tmpdir)
        self.env["GIT_CONFIG_GLOBAL"] = str(empty_git_config)
        self.env["GIT_CONFIG_SYSTEM"] = str(empty_git_config)
        # 閾値は既定値 (60) で検査する。
        self.env.pop("SESSION_HANDOFF_THRESHOLD", None)

        subprocess.run(
            ["git", "-C", str(self.repo), "init", "-b", "main"],
            check=True,
            capture_output=True,
            env=self.env,
        )
        self.write_context_cache(USED_PERCENTAGE_ABOVE_THRESHOLD)

    # -- fixture ------------------------------------------------------------

    def write_context_cache(self, used_percentage: float) -> None:
        """natsuume-statusline の producer が書く context 使用率キャッシュを置く。

        `updated_at` は鮮度判定 (600 秒) に掛からないよう現在時刻にする。
        """
        cache_dir = self.temporary_tmpdir / f"natsuume-context-cache-{os.getuid()}"
        cache_dir.mkdir(exist_ok=True)
        (cache_dir / f"{SESSION_ID}.json").write_text(
            json.dumps(
                {"used_percentage": used_percentage, "updated_at": int(time.time())}
            ),
            encoding="utf-8",
        )

    def marker_path(self) -> Path:
        state_root = self.temporary_tmpdir / f"session-handoff-{os.getuid()}"
        return state_root / "markers" / f"{SESSION_ID}.notified"

    # -- hook 実行 ----------------------------------------------------------

    def run_hook(self, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=str(self.repo),
            env=self.env,
            timeout=60,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def post_tool_use_payload(self, *, agent_id: str = "") -> dict[str, object]:
        return {
            "hook_event_name": "PostToolUse",
            "session_id": SESSION_ID,
            "agent_id": agent_id,
            "cwd": str(self.repo),
            "tool_name": "Bash",
            "tool_input": {"command": "printf hello"},
            "tool_response": {"exit_code": 0},
            "tool_use_id": "toolu_test",
        }

    def failure_payload(self, *, agent_id: str = "") -> dict[str, object]:
        """`tool_response` を持たない PostToolUseFailure 入力。"""
        return {
            "hook_event_name": FAILURE_EVENT,
            "session_id": SESSION_ID,
            "agent_id": agent_id,
            "cwd": str(self.repo),
            "tool_name": "Bash",
            "tool_input": {"command": "printf hello"},
            "error": FAILURE_ERROR,
            "tool_use_id": "toolu_test",
            "duration_ms": 12,
        }

    # -- assertions ---------------------------------------------------------

    def assert_emits_handoff_instruction(
        self, payload: dict[str, object], expected_event: str
    ) -> None:
        result = self.run_hook(payload)
        response = json.loads(result.stdout)
        hook_output = response["hookSpecificOutput"]
        self.assertEqual(expected_event, hook_output["hookEventName"])
        self.assertTrue(
            hook_output["additionalContext"].strip(), "additionalContext が空"
        )
        self.assertTrue(
            self.marker_path().is_dir(),
            f"通知済み marker が claim されていない: {self.marker_path()}",
        )

    def assert_silent(self, payload: dict[str, object]) -> None:
        result = self.run_hook(payload)
        self.assertEqual("", result.stdout.strip(), result.stdout)

    # -- 検知 ---------------------------------------------------------------

    def test_post_tool_use_emits_the_handoff_instruction(self) -> None:
        self.assert_emits_handoff_instruction(
            self.post_tool_use_payload(), "PostToolUse"
        )

    def test_post_tool_use_failure_emits_the_handoff_instruction(self) -> None:
        self.assert_emits_handoff_instruction(self.failure_payload(), FAILURE_EVENT)

    def test_second_detection_in_the_same_session_is_silent(self) -> None:
        self.assert_emits_handoff_instruction(self.failure_payload(), FAILURE_EVENT)
        self.assert_silent(self.failure_payload())

    def test_subagent_execution_is_silent(self) -> None:
        self.assert_silent(self.failure_payload(agent_id="agent-a"))
        self.assertFalse(self.marker_path().exists())

    def test_usage_below_the_threshold_is_silent(self) -> None:
        self.write_context_cache(USED_PERCENTAGE_BELOW_THRESHOLD)
        self.assert_silent(self.failure_payload())
        self.assertFalse(self.marker_path().exists())


class ContextThresholdDocumentationTest(unittest.TestCase):
    """README が検知 hook の配送イベントとして PostToolUseFailure を書く。"""

    @staticmethod
    def readme_lines() -> list[str]:
        return README.read_text(encoding="utf-8").splitlines()

    def test_detection_hook_heading_lists_the_failure_event(self) -> None:
        headings = [
            line
            for line in self.readme_lines()
            if line.startswith("### ") and "検知 hook" in line
        ]
        self.assertTrue(headings, f"{README}: '### 検知 hook' 見出しが無い")
        for heading in headings:
            with self.subTest(heading=heading):
                self.assertIn(FAILURE_EVENT, heading)

    def test_hook_table_row_lists_the_failure_event(self) -> None:
        rows = [
            line
            for line in self.readme_lines()
            if line.startswith("|") and "`detect-context-threshold`" in line
        ]
        self.assertTrue(rows, f"{README}: hook 一覧に detect-context-threshold の行が無い")
        for row in rows:
            with self.subTest(row=row):
                self.assertIn(FAILURE_EVENT, row)


if __name__ == "__main__":
    unittest.main()

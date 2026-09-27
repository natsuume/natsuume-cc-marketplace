"""session-handoff の検知 hook (detect-context-threshold.sh) の契約。

検知 hook は context 使用率キャッシュを読み、閾値 (既定 75%) 以上であれば handoff
作成指示を additionalContext として 1 セッション 1 回だけ注入する。閾値は plugin の
userConfig `threshold` で変更でき、hook には環境変数 `CLAUDE_PLUGIN_OPTION_THRESHOLD`
として渡る。1〜99 の範囲外・非数値の場合は既定値を使う。判定は
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
PLUGIN_MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"

FAILURE_EVENT = "PostToolUseFailure"
SESSION_ID = "session-handoff-producer"
DEFAULT_THRESHOLD = 75
THRESHOLD_OPTION_ENV = "CLAUDE_PLUGIN_OPTION_THRESHOLD"
# userConfig に置き換えて受け付けなくなった環境変数。
REMOVED_THRESHOLD_ENV = "SESSION_HANDOFF_THRESHOLD"
# 既定閾値 (75) を超える値と、下回る値。
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
        # 閾値は既定値 (75) で検査する。
        self.env.pop(THRESHOLD_OPTION_ENV, None)
        self.env.pop(REMOVED_THRESHOLD_ENV, None)

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

    def reset_marker(self) -> None:
        """1 セッション 1 回の marker を消し、同じセッションで再び検知できるようにする。"""
        marker = self.marker_path()
        if marker.exists():
            marker.rmdir()

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

    def failure_payload(
        self, *, agent_id: str = "", is_interrupt: bool | None = None
    ) -> dict[str, object]:
        """`tool_response` を持たない PostToolUseFailure 入力。"""
        payload: dict[str, object] = {
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
        if is_interrupt is not None:
            payload["is_interrupt"] = is_interrupt
        return payload

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

    def test_interrupted_failure_is_silent_and_keeps_the_marker(self) -> None:
        """ユーザ中断の PostToolUseFailure では hook 出力が model に届かないため、
        1 セッション 1 回の marker を消費せず、次の検知機会を残す。"""
        self.assert_silent(self.failure_payload(is_interrupt=True))
        self.assertFalse(self.marker_path().exists())
        self.assert_emits_handoff_instruction(self.failure_payload(), FAILURE_EVENT)

    def test_usage_below_the_threshold_is_silent(self) -> None:
        self.write_context_cache(USED_PERCENTAGE_BELOW_THRESHOLD)
        self.assert_silent(self.failure_payload())
        self.assertFalse(self.marker_path().exists())

    # -- 閾値 ---------------------------------------------------------------

    def assert_threshold_boundary(self, below: float, at_or_above: float) -> None:
        """`below` では検知せず、`at_or_above` では検知する。"""
        self.write_context_cache(below)
        self.assert_silent(self.failure_payload())
        self.assertFalse(self.marker_path().exists())
        self.write_context_cache(at_or_above)
        self.assert_emits_handoff_instruction(self.failure_payload(), FAILURE_EVENT)

    def test_default_threshold_is_75_percent(self) -> None:
        self.assert_threshold_boundary(below=74.9, at_or_above=DEFAULT_THRESHOLD)

    def test_threshold_option_lowers_the_threshold(self) -> None:
        self.env[THRESHOLD_OPTION_ENV] = "50"
        self.assert_threshold_boundary(below=49, at_or_above=50)

    def test_threshold_option_raises_the_threshold(self) -> None:
        self.env[THRESHOLD_OPTION_ENV] = "90"
        self.assert_threshold_boundary(below=89, at_or_above=90)

    def test_threshold_option_accepts_a_decimal(self) -> None:
        """userConfig の number は小数も入力できるため、小数の閾値もそのまま使う。"""
        self.env[THRESHOLD_OPTION_ENV] = "72.5"
        self.assert_threshold_boundary(below=72.4, at_or_above=72.5)

    def test_invalid_threshold_option_falls_back_to_the_default(self) -> None:
        for value in ("", "abc", "0", "100", "-5", "75%", "1e2"):
            with self.subTest(value=value):
                self.reset_marker()
                self.env[THRESHOLD_OPTION_ENV] = value
                self.assert_threshold_boundary(
                    below=74.9, at_or_above=DEFAULT_THRESHOLD
                )

    def test_removed_threshold_env_is_ignored(self) -> None:
        self.env[REMOVED_THRESHOLD_ENV] = "10"
        self.assert_threshold_boundary(below=74.9, at_or_above=DEFAULT_THRESHOLD)


class ThresholdConfigurationTest(unittest.TestCase):
    """閾値の設定経路は plugin の userConfig `threshold` に統一されている。"""

    def test_manifest_declares_the_threshold_option(self) -> None:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        option = manifest["userConfig"]["threshold"]
        self.assertEqual("number", option["type"])
        self.assertEqual(DEFAULT_THRESHOLD, option["default"])
        self.assertEqual(1, option["min"])
        self.assertEqual(99, option["max"])
        for field in ("title", "description"):
            with self.subTest(field=field):
                self.assertTrue(option[field].strip())

    def test_plugin_files_do_not_mention_the_removed_env(self) -> None:
        for path in sorted(p for p in PLUGIN.rglob("*") if p.is_file()):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertNotIn(
                    REMOVED_THRESHOLD_ENV, path.read_text(encoding="utf-8")
                )


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

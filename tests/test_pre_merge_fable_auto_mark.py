"""pre-merge-cross-review の fable-reviewer が何も記録しないことの契約テスト。

`pre-merge-cross-review:fable-reviewer` の report は親 session に返るだけで、ローカル記録も
GitHub への書き込みも作らない。auto-mark.sh は codex-reviewer だけを対象にし、
fable-reviewer の lifecycle event (SubagentStart / PostToolUse (SubagentHandback) /
SubagentStop / PostToolUseFailure) では git-dir に何も書かず、codex review の記録にも
触れない。hooks.json の SubagentStart / SubagentStop matcher も codex-reviewer だけに一致する
(tests/test_pre_merge_auto_mark.py が検査する)。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_auto_mark_fixture():
    """codex-reviewer 側の auto-mark テストが持つ git repo fixture を読み込む。

    `import` 文で書くと「sys.path 操作より前に import 文が来る」という lint 制約
    (E402) に抵触するため、既存テストと同じ importlib 経由の明示 import にする。
    """
    return importlib.import_module("test_pre_merge_auto_mark")


_auto_mark_tests = _load_auto_mark_fixture()

RepositoryFixture = _auto_mark_tests.RepositoryFixture
AUTO_MARK = _auto_mark_tests.AUTO_MARK
FABLE_REVIEWER = _auto_mark_tests.FABLE_REVIEWER

FABLE_PASS_REPORT = "# Fable Review\n\nStatus: pass\nFindings: 0\nChecked sources: PR description"
FABLE_FINDINGS_REPORT = (
    "# Fable Review\n\nStatus: findings\n\n"
    "## Finding FABLE-example-acceptance-criteria\n"
    "- Category: acceptance-criteria\n"
    "- Severity: P2\n"
    "- Confidence: high"
)
HANDBACK_CLOSING_MESSAGE = "Fable review complete. Report delivered to the parent session."


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class FableReviewerRecordsNothingTest(RepositoryFixture, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        temporary_path = Path(self.temporary.name)
        self.work = self.create_feature_repository(temporary_path)
        # hook が一時ファイルやキャッシュを書く場合も、実 HOME ではなく一時ディレクトリ
        # 配下に閉じる。
        isolated_tmp = temporary_path / "tmp"
        isolated_cache = temporary_path / "cache"
        isolated_tmp.mkdir()
        isolated_cache.mkdir()
        self.hook_env = os.environ.copy()
        self.hook_env["TMPDIR"] = str(isolated_tmp)
        self.hook_env["XDG_CACHE_HOME"] = str(isolated_cache)

    def git_dir_snapshot(self) -> dict[str, bytes]:
        """git-dir 直下の通常ファイル (git 自身の管理ファイルを含む) の名前と内容。"""
        git_dir = self.git_dir(self.work)
        return {
            path.name: path.read_bytes()
            for path in sorted(git_dir.iterdir())
            if path.is_file()
        }

    def run_hook(self, payload: dict[str, object]) -> None:
        result = subprocess.run(
            ["bash", str(AUTO_MARK)],
            cwd=self.work,
            input=json.dumps(payload).encode(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.hook_env,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"", result.stdout.decode())

    def start(self, agent_id: str) -> None:
        self.run_hook(
            {
                "hook_event_name": "SubagentStart",
                "session_id": "test-session",
                "agent_id": agent_id,
                "agent_type": FABLE_REVIEWER,
            }
        )

    def handback(self, agent_id: str, message: str) -> None:
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "test-session",
                "agent_id": agent_id,
                "agent_type": FABLE_REVIEWER,
                "tool_name": "SubagentHandback",
                "tool_input": {"message": message},
                "tool_response": {
                    "success": True,
                    "message": "Report delivered to your caller.",
                },
                "tool_use_id": "toolu_test",
            }
        )

    def stop(self, agent_id: str, message: str) -> None:
        self.run_hook(
            {
                "hook_event_name": "SubagentStop",
                "session_id": "test-session",
                "agent_id": agent_id,
                "agent_type": FABLE_REVIEWER,
                "last_assistant_message": message,
                "stop_hook_active": False,
            }
        )

    def test_fable_lifecycle_writes_nothing_to_git_dir(self) -> None:
        cases = {
            "handback_pass": (FABLE_PASS_REPORT, HANDBACK_CLOSING_MESSAGE),
            "handback_findings": (FABLE_FINDINGS_REPORT, HANDBACK_CLOSING_MESSAGE),
            "last_assistant_message": (None, FABLE_FINDINGS_REPORT),
        }
        for index, (label, (handback_report, stop_message)) in enumerate(cases.items()):
            with self.subTest(case=label):
                agent_id = f"fablereviewer{index}"
                before = self.git_dir_snapshot()
                self.start(agent_id)
                if handback_report is not None:
                    self.handback(agent_id, handback_report)
                self.stop(agent_id, stop_message)
                self.assertEqual(
                    self.git_dir_snapshot(),
                    before,
                    "fable-reviewer の lifecycle は git-dir に何も書かない",
                )

    def test_fable_lifecycle_keeps_codex_records(self) -> None:
        head = self.head_sha(self.work)
        final = self.final_marker_path(self.work)
        final.write_text(self.pending_content(head), encoding="utf-8")
        pending = self.write_pending(self.work, self.pending_content(head))
        before = self.git_dir_snapshot()

        self.start("fablekeeps0")
        self.handback("fablekeeps0", FABLE_PASS_REPORT)
        self.stop("fablekeeps0", HANDBACK_CLOSING_MESSAGE)
        self.run_hook(
            {
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "Agent",
                "tool_input": {"subagent_type": FABLE_REVIEWER},
                "error": "agent failed",
                "is_interrupt": False,
            }
        )

        self.assertEqual(self.git_dir_snapshot(), before)
        self.assertTrue(final.exists())
        self.assertTrue(pending.exists())


if __name__ == "__main__":
    unittest.main()

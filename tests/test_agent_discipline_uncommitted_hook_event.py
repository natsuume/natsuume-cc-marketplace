"""check-uncommitted-on-session-start.sh の hookEventName 契約テスト。

同一 plugin の inject-always.sh / inject-auto.sh と同じく、出力の hookEventName は
入力 JSON の hook_event_name をそのまま返す。hook_event_name が空の入力では誤った
既定値の event 文脈を返さず無音終了し、session ごとの発火マーカーも消費しない。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIRS = (
    ROOT / "plugins" / "agent-discipline",
)
SCRIPT_NAME = "check-uncommitted-on-session-start.sh"


class UncommittedHookEventNameTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)
        self.marker_tmpdir = tmp_path / "tmpdir"
        self.marker_tmpdir.mkdir()
        # 未コミット変更を持つ使い捨ての git work-tree。
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(self.repo)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (self.repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

    def run_hook(
        self, plugin_dir: Path, payload: dict[str, str], session_id: str
    ) -> subprocess.CompletedProcess[str]:
        script = plugin_dir / "hooks" / "scripts" / SCRIPT_NAME
        body = {
            "session_id": session_id,
            "permission_mode": "auto",
            "cwd": str(self.repo),
            **payload,
        }
        return subprocess.run(
            ["/bin/bash", str(script)],
            cwd=ROOT,
            env={**os.environ, "TMPDIR": str(self.marker_tmpdir)},
            input=json.dumps(body),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_hook_event_name_is_echoed_from_input(self) -> None:
        for plugin_dir in PLUGIN_DIRS:
            for index, event in enumerate(("UserPromptSubmit", "SessionStart")):
                with self.subTest(plugin=plugin_dir.name, event=event):
                    session_id = f"echo-{plugin_dir.name}-{index}"
                    result = self.run_hook(
                        plugin_dir, {"hook_event_name": event}, session_id
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    output = json.loads(result.stdout)
                    self.assertEqual(
                        output["hookSpecificOutput"]["hookEventName"], event
                    )

    def test_missing_hook_event_name_is_silent_and_keeps_marker(self) -> None:
        for plugin_dir in PLUGIN_DIRS:
            with self.subTest(plugin=plugin_dir.name):
                session_id = f"missing-{plugin_dir.name}"
                silent = self.run_hook(plugin_dir, {}, session_id)
                self.assertEqual(silent.returncode, 0, silent.stderr)
                self.assertEqual(silent.stdout, "")
                self.assertEqual(silent.stderr, "")

                # 空入力で発火マーカーを消費していなければ、同じ session の
                # 正常な入力で発火する。
                fired = self.run_hook(
                    plugin_dir, {"hook_event_name": "UserPromptSubmit"}, session_id
                )
                self.assertEqual(fired.returncode, 0, fired.stderr)
                output = json.loads(fired.stdout)
                self.assertEqual(
                    output["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit"
                )


if __name__ == "__main__":
    unittest.main()

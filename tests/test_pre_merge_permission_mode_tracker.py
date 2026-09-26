"""pre-merge-cross-review hooks module の permission mode 記録 (permission-mode-tracker.mjs) の契約テスト。

公開境界は `createPermissionModeTracker()` が返す 4 関数 (recordMode / recordCall /
forgetCall / modeForCall)。操作列を node で順に実行し、modeForCall の結果を検証する。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKER_PATH = (
    REPO_ROOT
    / "plugins"
    / "pre-merge-cross-review"
    / "hooks"
    / "module"
    / "permission-mode-tracker.mjs"
)

# stdin で受けた操作列を 1 つの tracker に順に適用し、modeForCall の結果だけを配列で返す。
NODE_RUNNER = """
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const { createPermissionModeTracker } = await import(pathToFileURL(process.argv[1]).href);
const tracker = createPermissionModeTracker();
const results = [];
for (const op of JSON.parse(readFileSync(0, 'utf8'))) {
  if (op.op === 'mode') tracker.recordMode({ agentId: op.agentId, mode: op.mode });
  if (op.op === 'call') tracker.recordCall({ toolUseId: op.toolUseId, agentId: op.agentId });
  if (op.op === 'forget') tracker.forgetCall(op.toolUseId);
  if (op.op === 'query') results.push(tracker.modeForCall(op.toolUseId) ?? null);
}
process.stdout.write(JSON.stringify(results));
"""


def mode(mode_value: object, agent_id: object = None) -> dict:
    return {"op": "mode", "mode": mode_value, "agentId": agent_id}


def call(tool_use_id: object, agent_id: object = None) -> dict:
    return {"op": "call", "toolUseId": tool_use_id, "agentId": agent_id}


def query(tool_use_id: object) -> dict:
    return {"op": "query", "toolUseId": tool_use_id}


def forget(tool_use_id: object) -> dict:
    return {"op": "forget", "toolUseId": tool_use_id}


@unittest.skipUnless(
    shutil.which("node"), "permission-mode-tracker.mjs の評価には node が必要"
)
class PermissionModeTrackerTest(unittest.TestCase):
    def run_ops(self, ops: list[dict]) -> list[object]:
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", NODE_RUNNER, str(TRACKER_PATH)],
            input=json.dumps(ops),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return json.loads(completed.stdout)

    def test_main_call_uses_main_mode(self) -> None:
        self.assertEqual(
            ["auto"], self.run_ops([mode("auto"), call("t1"), query("t1")])
        )

    def test_empty_agent_id_is_main(self) -> None:
        self.assertEqual(
            ["auto", "auto"],
            self.run_ops(
                [
                    mode("auto", ""),
                    call("t1"),
                    call("t2", ""),
                    query("t1"),
                    query("t2"),
                ]
            ),
        )

    def test_subagent_without_recorded_mode_is_unknown(self) -> None:
        self.assertEqual(
            [None], self.run_ops([mode("auto"), call("t1", "a1"), query("t1")])
        )

    def test_modes_are_kept_per_agent(self) -> None:
        self.assertEqual(
            ["default", "auto", "plan"],
            self.run_ops(
                [
                    mode("auto"),
                    mode("default", "a1"),
                    mode("plan", "a2"),
                    call("t1", "a1"),
                    call("t2"),
                    call("t3", "a2"),
                    query("t1"),
                    query("t2"),
                    query("t3"),
                ]
            ),
        )

    def test_subagent_mode_does_not_overwrite_main(self) -> None:
        self.assertEqual(
            ["auto"],
            self.run_ops(
                [mode("auto"), mode("default", "a1"), call("t1"), query("t1")]
            ),
        )

    def test_latest_mode_wins(self) -> None:
        self.assertEqual(
            ["default"],
            self.run_ops([mode("auto"), mode("default"), call("t1"), query("t1")]),
        )

    def test_non_string_mode_is_ignored(self) -> None:
        for bad_mode in [None, 1, {}, ["auto"]]:
            with self.subTest(mode=bad_mode):
                self.assertEqual(
                    ["auto"],
                    self.run_ops(
                        [mode("auto"), mode(bad_mode), call("t1"), query("t1")]
                    ),
                )

    def test_no_mode_recorded_is_unknown(self) -> None:
        self.assertEqual([None], self.run_ops([call("t1"), query("t1")]))

    def test_unknown_call_is_unknown(self) -> None:
        self.assertEqual(
            [None, None],
            self.run_ops([mode("auto"), query("t9"), query(None)]),
        )

    def test_forgotten_call_is_unknown(self) -> None:
        self.assertEqual(
            ["auto", None],
            self.run_ops(
                [mode("auto"), call("t1"), query("t1"), forget("t1"), query("t1")]
            ),
        )

    def test_non_string_tool_use_id_is_not_recorded(self) -> None:
        for bad_id in [None, 1, ""]:
            with self.subTest(tool_use_id=bad_id):
                self.assertEqual(
                    [None], self.run_ops([mode("auto"), call(bad_id), query(bad_id)])
                )


if __name__ == "__main__":
    unittest.main()

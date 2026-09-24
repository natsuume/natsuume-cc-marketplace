"""廃止した experimental-agent-discipline plugin がリポジトリから削除されていることを検査する。

marketplace の renames に null の entry を残し、利用者の環境から plugin を外させる。
それ以外の tracked file には plugin 名を残さない。
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "experimental-agent-discipline"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
THIS_FILE = Path(__file__).resolve()


class ExperimentalAgentDisciplineRemovedTest(unittest.TestCase):
    def test_plugin_directory_is_removed(self) -> None:
        self.assertFalse((ROOT / "plugins" / PLUGIN_NAME).exists())

    def test_marketplace_lists_plugin_only_as_removed_rename(self) -> None:
        marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))

        names = [plugin["name"] for plugin in marketplace["plugins"]]
        self.assertNotIn(PLUGIN_NAME, names)
        self.assertIn(PLUGIN_NAME, marketplace["renames"])
        self.assertIsNone(marketplace["renames"][PLUGIN_NAME])

    def test_no_tracked_file_mentions_plugin_except_marketplace_renames(self) -> None:
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.decode().split("\0")

        offenders = []
        for relative in filter(None, tracked):
            path = ROOT / relative
            if path.resolve() == THIS_FILE or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path == MARKETPLACE:
                # renames の null entry の 1 行だけを許可する。
                lines = [line for line in text.splitlines() if PLUGIN_NAME in line]
                if lines != [f'    "{PLUGIN_NAME}": null']:
                    offenders.append(relative)
                continue
            if PLUGIN_NAME in text or "experimental_agent_discipline" in text:
                offenders.append(relative)

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

"""pre-merge-codex-review から pre-merge-cross-review への改名の契約テスト。

- plugin ディレクトリは `plugins/pre-merge-cross-review` にあり、旧ディレクトリは残らない
- plugin.json の name は新名、version は 3.0.0
- marketplace.json の entry は新名 (source・version を含む) で、top-level `renames` が
  旧名を新名に対応づける
- agents は codex-reviewer と fable-reviewer の 2 つで、frontmatter の name がファイル名と一致する
- 旧名はリポジトリ内 (git ls-files の全ファイル) に残らない。例外は codex 専用の動作名
  (wrapper basename `run-pre-merge-codex-review` と codex 記録ファイル名
  `.claude-pre-merge-codex-reviewed`)、marketplace.json の renames の 1 行、plugin README の
  「## pre-merge-codex-review からの移行」節、本テスト自身
- 移行節は renames による自動移行の条件と、利用者が行う手順を述べる

subTest は使わない: 違反をリストに集約して 1 テスト = 1 判定に保つ。
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_NAME = "pre-merge-codex-review"
NEW_NAME = "pre-merge-cross-review"
PLUGIN = ROOT / "plugins" / NEW_NAME
OLD_PLUGIN = ROOT / "plugins" / OLD_NAME
PLUGIN_JSON = PLUGIN / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
AGENTS_DIR = PLUGIN / "agents"
EXPECTED_VERSION = "3.0.0"
EXPECTED_AGENTS = {"codex-reviewer.md", "fable-reviewer.md"}

# 旧名を含んでよい codex 専用の動作名 (wrapper basename と codex 記録ファイル名)。
ALLOWED_OLD_NAME_TOKENS = ("run-pre-merge-codex-review", ".claude-pre-merge-codex-reviewed")
# grep の対象外 (本テスト自身)。
GREP_EXCLUDED_FILES = ("tests/test_pre_merge_cross_review_rename.py",)
MARKETPLACE_FILE = ".claude-plugin/marketplace.json"
MARKETPLACE_RENAME_LINE = re.compile(
    r'^\s*"pre-merge-codex-review"\s*:\s*"pre-merge-cross-review"\s*,?\s*$'
)
# 旧名を利用者向けの移行手順として書く README の節 (見出しから次の `## ` 見出しの直前まで)。
MIGRATION_SECTION_FILE = f"plugins/{NEW_NAME}/README.md"
MIGRATION_SECTION_HEADING = f"## {OLD_NAME} からの移行"
MIGRATION_SECTION_PHRASES = (
    "renames",
    "2.1.193",
    "pre-push-codex-review",
    "同時に更新",
    "新しいセッション",
    "autoMode.allow",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(text: str) -> str:
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    return match.group(1) if match else ""


def strip_allowed_tokens(text: str) -> str:
    for token in ALLOWED_OLD_NAME_TOKENS:
        text = text.replace(token, "")
    return text


class PluginRenameTest(unittest.TestCase):
    def test_plugin_directory_is_moved(self) -> None:
        self.assertTrue(PLUGIN.is_dir(), f"{PLUGIN} が無い")
        self.assertFalse(OLD_PLUGIN.exists(), f"{OLD_PLUGIN} が残っている")

    def test_plugin_json_name_and_version(self) -> None:
        manifest = json.loads(read(PLUGIN_JSON))
        self.assertEqual(NEW_NAME, manifest["name"])
        self.assertEqual(EXPECTED_VERSION, manifest["version"])

    def test_marketplace_entry_and_renames(self) -> None:
        marketplace = json.loads(read(MARKETPLACE))
        names = [plugin["name"] for plugin in marketplace["plugins"]]
        self.assertIn(NEW_NAME, names)
        self.assertNotIn(OLD_NAME, names)
        entry = next(p for p in marketplace["plugins"] if p["name"] == NEW_NAME)
        self.assertEqual(f"./plugins/{NEW_NAME}", entry["source"])
        self.assertEqual(EXPECTED_VERSION, entry["version"])
        self.assertEqual(NEW_NAME, marketplace.get("renames", {}).get(OLD_NAME))

    def test_agents_are_codex_and_fable_reviewers(self) -> None:
        names = {path.name for path in AGENTS_DIR.glob("*.md")}
        self.assertEqual(EXPECTED_AGENTS, names)
        wrong = [
            path.name
            for path in AGENTS_DIR.glob("*.md")
            if f"name: {path.stem}" not in frontmatter(read(path)).splitlines()
        ]
        self.assertEqual([], wrong, f"frontmatter の name がファイル名と一致しない: {wrong}")

    def test_readme_describes_migration_from_old_name(self) -> None:
        text = read(ROOT / MIGRATION_SECTION_FILE)
        self.assertIn(MIGRATION_SECTION_HEADING, text.splitlines())
        section = text.split(MIGRATION_SECTION_HEADING, 1)[1].split("\n## ", 1)[0]
        missing = [phrase for phrase in MIGRATION_SECTION_PHRASES if phrase not in section]
        self.assertEqual([], missing, f"移行節に無い記述: {missing}")

    def test_no_leftover_old_name_references(self) -> None:
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        offenders = []
        for relative in tracked:
            if relative in GREP_EXCLUDED_FILES:
                continue
            if OLD_NAME in strip_allowed_tokens(relative):
                offenders.append(f"{relative} (path)")
            path = ROOT / relative
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            in_migration_section = False
            for number, line in enumerate(text.splitlines(), start=1):
                if relative == MIGRATION_SECTION_FILE and line.startswith("## "):
                    in_migration_section = line == MIGRATION_SECTION_HEADING
                if in_migration_section:
                    continue
                if relative == MARKETPLACE_FILE and MARKETPLACE_RENAME_LINE.match(line):
                    continue
                if OLD_NAME in strip_allowed_tokens(line):
                    offenders.append(f"{relative}:{number}")
        self.assertEqual([], offenders, f"{OLD_NAME} が残る箇所:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()

"""pre-merge-cross-review が merge 前の cross review を codex review だけで行う契約テスト。

pre-merge-cross-review は merge 前に `pre-merge-cross-review:codex-reviewer` だけを起動し、
Fable review の agent・判定コマンド・判定 lib を提供しない。これを、ファイルの有無と
注入文・説明文の記述で観測して固定する。

- pre-merge-cross-review の Fable review 一式 (`agents/fable-reviewer.md`、
  `bin/pre-merge-cross-review-fable-usage`、`hooks/scripts/lib/fable-weekly-usage.sh`) が存在しない
- `plugins/` 配下の全ファイルとリポジトリ直下 README に `fable-reviewer` /
  `pre-merge-cross-review-fable-usage` が含まれない
- `plugins/pre-merge-cross-review/` 配下の全ファイル (パスと内容) に `fable` が含まれない
  (大文字小文字を問わない)。marketplace.json の entry の description と、リポジトリ直下
  README の一覧表の行・plugin 説明の本文も Fable に言及しない
- hooks.json の description は配送経路の各 script と hooks module を述べる
- 注入文 `hooks/prompts/merge-order-rules.md` は 3 つの rule ID マーカー、codex-reviewer を
  `model: "sonnet"` で起動する指示、codex-reviewer の起動 prompt の定型文、merge コマンドの
  単独正規形と `--delete-branch` を付けない規律を持つ
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = ROOT / "plugins"
REPO_README = ROOT / "README.md"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"

MERGE_PLUGIN_NAME = "pre-merge-cross-review"
MERGE_PLUGIN = PLUGINS_DIR / MERGE_PLUGIN_NAME
MERGE_ORDER_RULES = MERGE_PLUGIN / "hooks" / "prompts" / "merge-order-rules.md"
MERGE_HOOKS_JSON = MERGE_PLUGIN / "hooks" / "hooks.json"

# 提供しなくなった Fable review 一式。
REMOVED_FILES = (
    MERGE_PLUGIN / "agents" / "fable-reviewer.md",
    MERGE_PLUGIN / "bin" / "pre-merge-cross-review-fable-usage",
    MERGE_PLUGIN / "hooks" / "scripts" / "lib" / "fable-weekly-usage.sh",
)

# plugins/ 配下とリポジトリ直下 README に残らない名前。
REMOVED_NAMES = ("fable-reviewer", "pre-merge-cross-review-fable-usage")

FABLE_PATTERN = re.compile(r"fable", re.IGNORECASE)

# merge-order-rules.md が維持する rule ID マーカー。
MERGE_ORDER_MARKERS = (
    "<!-- rule:merge-order -->",
    "<!-- rule:merge-command-form -->",
    "<!-- rule:classifier-denied -->",
)
CODEX_REVIEWER_TYPE = "pre-merge-cross-review:codex-reviewer"
CODEX_REVIEWER_MODEL = 'model: "sonnet"'
CODEX_FIXED_PROMPT_SENTENCE = (
    "current branch の PR (#<番号>) の merge-base..head 差分に対して、"
    "agent body の契約に従い codex review を 1 回実行し、"
    "parent-safe な markdown report を返してください。"
)
# merge コマンドの単独正規形と `--delete-branch` を付けない規律。
MERGE_COMMAND_FORM_PHRASES = ("単独正規形", "`--delete-branch` を付けない")

# hooks.json の description が述べる配送経路の script と hooks module。
HOOKS_DESCRIPTION_REQUIRED = (
    "inject-merge-order-rules.sh",
    "block-pre-merge.sh",
    "block-bg-codex-wrapper.sh",
    "auto-mark.sh",
    "module/register.ts",
)

# リポジトリ直下 README の plugin 一覧表の行と、plugin 説明の節の見出し。
REPO_README_TABLE_ROW_PREFIX = f"| [{MERGE_PLUGIN_NAME}](#{MERGE_PLUGIN_NAME}) |"
REPO_README_SECTION_HEADING = f"## {MERGE_PLUGIN_NAME}"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def files_under(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*") if path.is_file())


def repo_readme_section() -> str:
    """直下 README の pre-merge-cross-review 節 (キーワード小節を含む) の本文。"""
    lines = read(REPO_README).splitlines()
    try:
        start = lines.index(REPO_README_SECTION_HEADING)
    except ValueError:
        return ""
    section = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        section.append(line)
    return "\n".join(section)


class RemovedFilesTest(unittest.TestCase):
    """pre-merge-cross-review の Fable review 一式が存在しない。"""

    def test_fable_review_files_do_not_exist(self) -> None:
        remaining = [
            path.relative_to(ROOT).as_posix() for path in REMOVED_FILES if path.exists()
        ]
        self.assertEqual([], remaining, f"削除されていない Fable review のファイル: {remaining}")


class RemovedNamesTest(unittest.TestCase):
    """提供しなくなった agent / コマンドの名前が plugins/ 配下と直下 README に残らない。"""

    def test_plugins_and_repo_readme_do_not_mention_removed_names(self) -> None:
        offenders = [
            f"{path.relative_to(ROOT).as_posix()}:{number}: {name}"
            for path in [*files_under(PLUGINS_DIR), REPO_README]
            for number, line in enumerate(read(path).splitlines(), start=1)
            for name in REMOVED_NAMES
            if name in line
        ]
        self.assertEqual([], offenders, "削除した名前が残る箇所:\n" + "\n".join(offenders))


class MergePluginHasNoFableTest(unittest.TestCase):
    """pre-merge-cross-review とその説明が Fable に言及しない。"""

    def test_plugin_files_do_not_mention_fable(self) -> None:
        offenders = []
        for path in files_under(MERGE_PLUGIN):
            relative = path.relative_to(ROOT).as_posix()
            if FABLE_PATTERN.search(relative):
                offenders.append(f"{relative} (path)")
            offenders.extend(
                f"{relative}:{number}"
                for number, line in enumerate(read(path).splitlines(), start=1)
                if FABLE_PATTERN.search(line)
            )
        self.assertEqual([], offenders, "Fable への言及が残る箇所:\n" + "\n".join(offenders))

    def test_marketplace_entry_does_not_mention_fable(self) -> None:
        """description と keywords を含むエントリ全体が Fable に言及しない。"""
        marketplace = json.loads(read(MARKETPLACE))
        entries = [
            plugin for plugin in marketplace["plugins"] if plugin["name"] == MERGE_PLUGIN_NAME
        ]
        self.assertEqual(1, len(entries))
        entry_text = json.dumps(entries[0], ensure_ascii=False)
        self.assertIsNone(FABLE_PATTERN.search(entry_text), entries[0])

    def test_repo_readme_table_row_does_not_mention_fable(self) -> None:
        rows = [
            line
            for line in read(REPO_README).splitlines()
            if line.startswith(REPO_README_TABLE_ROW_PREFIX)
        ]
        self.assertEqual(1, len(rows), rows)
        self.assertIsNone(FABLE_PATTERN.search(rows[0]), rows[0])

    def test_repo_readme_section_does_not_mention_fable(self) -> None:
        """plugin 説明の節 (キーワード小節を含む) が Fable に言及しない。"""
        section = repo_readme_section()
        self.assertTrue(section.strip(), f"直下 README に {REPO_README_SECTION_HEADING} 節が無い")
        hits = [line for line in section.splitlines() if FABLE_PATTERN.search(line)]
        self.assertEqual([], hits, "直下 README の plugin 説明に Fable への言及が残っている")

    def test_hooks_description_names_the_delivery_scripts(self) -> None:
        description = json.loads(read(MERGE_HOOKS_JSON)).get("description", "")
        missing = [name for name in HOOKS_DESCRIPTION_REQUIRED if name not in description]
        self.assertEqual([], missing, f"hooks.json の description に無い記述: {missing}")


class MergeOrderRulesTest(unittest.TestCase):
    """注入文が codex-reviewer だけの起動手順と merge コマンドの規律を維持する。"""

    def test_rule_markers_are_kept(self) -> None:
        text = read(MERGE_ORDER_RULES)
        missing = [marker for marker in MERGE_ORDER_MARKERS if marker not in text]
        self.assertEqual([], missing, f"merge-order-rules.md に無いマーカー: {missing}")

    def test_codex_reviewer_is_launched_with_sonnet(self) -> None:
        lines = [
            line
            for line in read(MERGE_ORDER_RULES).splitlines()
            if CODEX_REVIEWER_TYPE in line and CODEX_REVIEWER_MODEL in line
        ]
        self.assertTrue(
            lines,
            f"{CODEX_REVIEWER_TYPE} を {CODEX_REVIEWER_MODEL} で起動する指示が無い",
        )

    def test_codex_fixed_prompt_sentence_is_kept(self) -> None:
        self.assertIn(CODEX_FIXED_PROMPT_SENTENCE, read(MERGE_ORDER_RULES))

    def test_merge_command_form_rules_are_kept(self) -> None:
        text = read(MERGE_ORDER_RULES)
        missing = [phrase for phrase in MERGE_COMMAND_FORM_PHRASES if phrase not in text]
        self.assertEqual([], missing, f"merge-order-rules.md に無い記述: {missing}")


if __name__ == "__main__":
    unittest.main()

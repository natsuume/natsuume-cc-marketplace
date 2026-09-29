"""cross-model-advisor と agent-discipline が Fable advisor を持たない契約テスト。

cross-model-advisor は Codex 側の advisor / rescue / review runner だけを提供し、Fable 側の
advisor とその週次枠の判定コマンド・判定 lib を持たない。agent-discipline はサブエージェントの
Fable 実行経路を判定する PreToolUse hook を持たない。これを、ファイルの有無・名前の出現・
hooks.json と lint script の設定・注入文と説明文の記述で観測して固定する。

- Fable advisor 一式 (``agents/fable-advisor-runner.md``、``bin/`` ディレクトリとその中の
  ``cross-model-advisor-fable-usage``、``scripts/lib/fable-weekly-usage.sh``) と agent-discipline の
  ``hooks/scripts/block-fable-subagent.sh`` が存在しない
- ``plugins/`` 配下の全ファイル・リポジトリ直下 README・marketplace.json に、撤去した agent /
  コマンド / lib / env / hook の名前 (``REMOVED_NAMES``) が含まれない
- cross-model-advisor / agent-discipline / pre-push-codex-review の配下の全ファイル (パスと内容)
  に ``fable`` が含まれない (大文字小文字を問わない)。marketplace.json の cross-model-advisor と
  agent-discipline の entry 全体 (description・keywords を含む)、直下 README の plugin 一覧表の
  両 plugin の行と両 plugin の節 (次の ``## `` 見出しまで。キーワード小節を含む) も Fable に
  言及しない
- agent-discipline の hooks.json は PreToolUse に matcher ``Agent|Task`` の entry を持たず、
  lint-payload-size.sh の ``EXCLUDED_SCRIPTS`` は空文字列である
- Codex 側の runner 3 体と consult skill は存在し、consult skill と advisor-rules.md は
  ``cross-model-advisor:codex-advisor-runner`` を ``model: "sonnet"`` で起動する指示を持つ
- natsuume-statusline README の公式経路の cache 書き出しの説明は、週次枠の使用率で Fable の
  利用可否を判定する消費者に言及しない

本ファイルのテストは hook を実行せず、リポジトリ内のファイルを読むだけである。
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

ADVISOR_PLUGIN_NAME = "cross-model-advisor"
ADVISOR_PLUGIN = PLUGINS_DIR / ADVISOR_PLUGIN_NAME
ADVISOR_AGENTS = ADVISOR_PLUGIN / "agents"
ADVISOR_BIN = ADVISOR_PLUGIN / "bin"
CONSULT_SKILL = ADVISOR_PLUGIN / "skills" / "consult" / "SKILL.md"
ADVISOR_RULES = ADVISOR_PLUGIN / "hooks" / "prompts" / "advisor-rules.md"

DISCIPLINE_PLUGIN_NAME = "agent-discipline"
DISCIPLINE_PLUGIN = PLUGINS_DIR / DISCIPLINE_PLUGIN_NAME
DISCIPLINE_HOOKS_JSON = DISCIPLINE_PLUGIN / "hooks" / "hooks.json"
LINT_PAYLOAD_SIZE_SH = DISCIPLINE_PLUGIN / "scripts" / "lint-payload-size.sh"

CODEX_REVIEW_PLUGIN = PLUGINS_DIR / "pre-push-codex-review"

STATUSLINE_README = PLUGINS_DIR / "natsuume-statusline" / "README.md"

# 存在しないファイル。
REMOVED_FILES = (
    ADVISOR_AGENTS / "fable-advisor-runner.md",
    ADVISOR_BIN / "cross-model-advisor-fable-usage",
    ADVISOR_PLUGIN / "scripts" / "lib" / "fable-weekly-usage.sh",
    DISCIPLINE_PLUGIN / "hooks" / "scripts" / "block-fable-subagent.sh",
)

# plugins/ 配下・直下 README・marketplace.json に残らない名前。
REMOVED_NAMES = (
    "fable-advisor-runner",
    "cross-model-advisor-fable-usage",
    "fable-weekly-usage",
    "FABLE_WEEKLY_MAX_PERCENT",
    "block-fable-subagent",
)

FABLE_PATTERN = re.compile(r"fable", re.IGNORECASE)

# 配下の全ファイル (パスと内容) が Fable に言及しない plugin。
PLUGINS_WITHOUT_FABLE = (ADVISOR_PLUGIN, DISCIPLINE_PLUGIN, CODEX_REVIEW_PLUGIN)

# marketplace.json の entry と直下 README の一覧表の行・節が Fable に言及しない plugin。
PLUGIN_NAMES_WITHOUT_FABLE = (ADVISOR_PLUGIN_NAME, DISCIPLINE_PLUGIN_NAME)

# agent-discipline の hooks.json に登録しない PreToolUse の matcher。
REMOVED_PRE_TOOL_USE_MATCHER = "Agent|Task"
EMPTY_EXCLUDED_SCRIPTS_LINE = 'EXCLUDED_SCRIPTS=""'
EXCLUDED_SCRIPTS_ASSIGNMENT = re.compile(r"^EXCLUDED_SCRIPTS=.*$", re.MULTILINE)

# 残る Codex 側の runner と consult skill。
CODEX_FILES = (
    ADVISOR_AGENTS / "codex-advisor-runner.md",
    ADVISOR_AGENTS / "codex-rescue-runner.md",
    ADVISOR_AGENTS / "codex-review-runner.md",
    CONSULT_SKILL,
)
CODEX_ADVISOR_RUNNER_TYPE = "cross-model-advisor:codex-advisor-runner"
CODEX_ADVISOR_RUNNER_MODEL = 'model: "sonnet"'

# natsuume-statusline README に書かない、cache の消費者の記述と、残す cache 書き出しの説明。
STATUSLINE_REMOVED_CONSUMER_PHRASE = "週次枠の使用率で Fable の利用可否を判定するもの"
STATUSLINE_CACHE_WRITE_PHRASE = "公式経路の値の cache 書き出し"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def files_under(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*") if path.is_file())


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def repo_readme_section(plugin_name: str) -> str:
    """直下 README の plugin 説明の節 (キーワード小節を含む) の本文。無ければ空文字。"""
    lines = read(REPO_README).splitlines()
    heading = f"## {plugin_name}"
    try:
        start = lines.index(heading)
    except ValueError:
        return ""
    section = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        section.append(line)
    return "\n".join(section)


class RemovedFilesTest(unittest.TestCase):
    """Fable advisor 一式と Fable サブエージェント判定 hook が存在しない。"""

    def test_removed_files_do_not_exist(self) -> None:
        remaining = [relative(path) for path in REMOVED_FILES if path.exists()]
        self.assertEqual([], remaining, f"削除されていないファイル: {remaining}")

    def test_advisor_bin_directory_does_not_exist(self) -> None:
        self.assertFalse(ADVISOR_BIN.exists(), f"{relative(ADVISOR_BIN)} が残っている")


class RemovedNamesTest(unittest.TestCase):
    """撤去した名前が plugins/ 配下・直下 README・marketplace.json に残らない。"""

    def test_removed_names_are_not_mentioned(self) -> None:
        offenders = [
            f"{relative(path)}:{number}: {name}"
            for path in [*files_under(PLUGINS_DIR), REPO_README, MARKETPLACE]
            for number, line in enumerate(read(path).splitlines(), start=1)
            for name in REMOVED_NAMES
            if name in line
        ]
        self.assertEqual([], offenders, "撤去した名前が残る箇所:\n" + "\n".join(offenders))


class NoFableMentionTest(unittest.TestCase):
    """対象 plugin の配下と、その配布メタデータ・説明が Fable に言及しない。"""

    def test_plugin_files_do_not_mention_fable(self) -> None:
        offenders = []
        for plugin in PLUGINS_WITHOUT_FABLE:
            for path in files_under(plugin):
                path_text = relative(path)
                if FABLE_PATTERN.search(path_text):
                    offenders.append(f"{path_text} (path)")
                offenders.extend(
                    f"{path_text}:{number}"
                    for number, line in enumerate(read(path).splitlines(), start=1)
                    if FABLE_PATTERN.search(line)
                )
        self.assertEqual([], offenders, "Fable への言及が残る箇所:\n" + "\n".join(offenders))

    def test_marketplace_entries_do_not_mention_fable(self) -> None:
        """description と keywords を含む entry 全体が Fable に言及しない。"""
        plugins = json.loads(read(MARKETPLACE))["plugins"]
        offenders = []
        for name in PLUGIN_NAMES_WITHOUT_FABLE:
            entries = [plugin for plugin in plugins if plugin["name"] == name]
            if len(entries) != 1:
                offenders.append(f"{name}: entry が 1 件でない ({len(entries)} 件)")
                continue
            entry_text = json.dumps(entries[0], ensure_ascii=False)
            if FABLE_PATTERN.search(entry_text):
                offenders.append(f"{name}: {entry_text}")
        self.assertEqual([], offenders, "\n".join(offenders))

    def test_repo_readme_table_rows_do_not_mention_fable(self) -> None:
        lines = read(REPO_README).splitlines()
        offenders = []
        for name in PLUGIN_NAMES_WITHOUT_FABLE:
            prefix = f"| [{name}](#{name}) |"
            rows = [line for line in lines if line.startswith(prefix)]
            if len(rows) != 1:
                offenders.append(f"{name}: 一覧表の行が 1 件でない ({len(rows)} 件)")
                continue
            if FABLE_PATTERN.search(rows[0]):
                offenders.append(rows[0])
        self.assertEqual([], offenders, "\n".join(offenders))

    def test_repo_readme_sections_do_not_mention_fable(self) -> None:
        """plugin 説明の節 (キーワード小節を含む) が Fable に言及しない。"""
        offenders = []
        for name in PLUGIN_NAMES_WITHOUT_FABLE:
            section = repo_readme_section(name)
            if not section.strip():
                offenders.append(f"{name}: 直下 README に ## {name} 節が無い")
                continue
            offenders.extend(
                f"{name}: {line}" for line in section.splitlines() if FABLE_PATTERN.search(line)
            )
        self.assertEqual([], offenders, "\n".join(offenders))


class AgentDisciplineHookRegistrationTest(unittest.TestCase):
    """agent-discipline が Agent / Task の PreToolUse hook を登録しない。"""

    def test_hooks_json_has_no_agent_task_pre_tool_use_entry(self) -> None:
        hooks = json.loads(read(DISCIPLINE_HOOKS_JSON))["hooks"]
        matchers = [group.get("matcher") for group in hooks.get("PreToolUse", [])]
        self.assertNotIn(REMOVED_PRE_TOOL_USE_MATCHER, matchers)

    def test_lint_payload_size_excludes_no_script(self) -> None:
        assignments = EXCLUDED_SCRIPTS_ASSIGNMENT.findall(read(LINT_PAYLOAD_SIZE_SH))
        self.assertEqual([EMPTY_EXCLUDED_SCRIPTS_LINE], assignments)


class CodexAdvisorKeptTest(unittest.TestCase):
    """Codex 側の runner と consult skill は残り、codex-advisor-runner を sonnet で起動する。"""

    def test_codex_files_exist(self) -> None:
        missing = [relative(path) for path in CODEX_FILES if not path.is_file()]
        self.assertEqual([], missing, f"Codex 側のファイルが無い: {missing}")

    def test_launch_guidance_uses_sonnet_for_codex_advisor_runner(self) -> None:
        missing = [
            f"{relative(path)}: {phrase}"
            for path in (CONSULT_SKILL, ADVISOR_RULES)
            for phrase in (CODEX_ADVISOR_RUNNER_TYPE, CODEX_ADVISOR_RUNNER_MODEL)
            if phrase not in read(path)
        ]
        self.assertEqual(
            [], missing, "codex-advisor-runner の起動指示に無い記述:\n" + "\n".join(missing)
        )


class StatuslineReadmeTest(unittest.TestCase):
    """natsuume-statusline README の cache 書き出しの説明が Fable の判定に言及しない。"""

    def test_cache_write_is_still_described(self) -> None:
        self.assertIn(STATUSLINE_CACHE_WRITE_PHRASE, read(STATUSLINE_README))

    def test_cache_consumer_is_not_described_as_fable_gate(self) -> None:
        self.assertNotIn(STATUSLINE_REMOVED_CONSUMER_PHRASE, read(STATUSLINE_README))


if __name__ == "__main__":
    unittest.main()

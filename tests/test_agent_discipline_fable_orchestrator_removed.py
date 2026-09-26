"""agent-discipline: Fable をメインセッションで使う運用を前提にしない契約テスト。

メインセッションは Opus 5.5 で、Fable は advisor (cross-model-advisor の fable-advisor-runner) の
起動にのみ使う。
agent-discipline は Fable 専用の規律 (常時適用ルール・分業規律) を持たない。これを、prompt
ファイルの有無と plugins/ 配下・README の記述で観測して固定する。

- Fable 専用の prompt ファイル (常時適用ルール・分業規律・分業規律前置き) が存在せず、
  plugin 配下と CI workflow のどのファイルもその名前を参照しない
- plugins/ 配下に Fable メインセッション向けの記述が残らない (lint-payload-size.sh の
  Fable メイン専用ケース、README の Fable メイン前提の記述と sonnet pin の根拠を含む)

配送内容がモデルに依らないことは tests/test_agent_discipline_unified_discipline.py、
block-fable-subagent.sh の判定は tests/test_agent_discipline_model_resolution.py と
tests/test_agent_discipline_fable_weekly_gate.py が検査する。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "agent-discipline"
PROMPTS_DIR = PLUGIN_DIR / "hooks" / "prompts"
LINT_PAYLOAD_SIZE_SH = PLUGIN_DIR / "scripts" / "lint-payload-size.sh"

PLUGINS_DIR = ROOT / "plugins"
AGENT_DISCIPLINE_README = PLUGIN_DIR / "README.md"
REPO_README = ROOT / "README.md"
UI_DISCIPLINE_README = PLUGINS_DIR / "ui-discipline" / "README.md"

# 存在してはならない Fable 専用の prompt ファイル。
FABLE_ONLY_PROMPT_FILES = (
    "always-fable.md",
    "discipline-fable.md",
    "discipline-preamble-fable.md",
)

# plugins/ 配下のどのファイルにも含まれてはならない、Fable メインセッション向けの記述。
FABLE_MAIN_SESSION_PHRASES = ("Fable メインのセッション", "Fable メインセッション")

# lint-payload-size.sh の CASE_TABLE に含めない Fable メイン専用ケースの ID。
FABLE_MAIN_ONLY_LINT_CASES = (
    "always.fable",
    "part2.fable",
    "part3.fable",
    "discipline.fable",
    "discipline.correct-fable",
    "switch.pending-to-fable",
    "switch.to-fable",
)

# sonnet pin を「実装系メインセッションと同系列に pin する」とする根拠の記述。
SUPERSEDED_PIN_RATIONALE_PHRASE = "実装系メインセッション"

# ui-discipline README の prompt 構成の記述と、書かない記述。
UI_SINGLE_PROMPT_PHRASE = "モデルに依らない 1 prompt"
UI_SUPERSEDED_SINGLE_PROMPT_PHRASE = "Fable / Sonnet 共通の 1 prompt"


class FableOnlyPromptFilesTest(unittest.TestCase):
    """Fable 専用の prompt ファイルが存在しない。"""

    def test_fable_only_prompt_files_do_not_exist(self) -> None:
        for name in FABLE_ONLY_PROMPT_FILES:
            with self.subTest(file=name):
                self.assertFalse(
                    (PROMPTS_DIR / name).exists(), f"{name} が存在している"
                )

    def test_no_file_references_fable_only_prompt_files(self) -> None:
        """plugin 配下と CI workflow のどのファイルも、削除した prompt ファイル名を参照しない。"""
        targets = [
            path
            for base in (PLUGIN_DIR, ROOT / ".github" / "workflows")
            for path in base.rglob("*")
            if path.is_file()
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8", errors="replace")
            for name in FABLE_ONLY_PROMPT_FILES:
                with self.subTest(path=path.relative_to(ROOT).as_posix(), file=name):
                    self.assertNotIn(name, text)


class FableMainSessionDocumentsRemovedTest(unittest.TestCase):
    """plugins/ 配下と README に Fable メインセッション前提の記述が無い。"""

    def test_plugins_do_not_mention_fable_main_session(self) -> None:
        offenders = [
            f"{path.relative_to(ROOT).as_posix()}: {phrase}"
            for path in sorted(PLUGINS_DIR.rglob("*"))
            if path.is_file()
            for phrase in FABLE_MAIN_SESSION_PHRASES
            if phrase in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual([], offenders, f"Fable メインセッション向けの記述: {offenders}")

    def test_lint_payload_size_has_no_fable_main_only_cases(self) -> None:
        text = LINT_PAYLOAD_SIZE_SH.read_text(encoding="utf-8")
        start = text.index("CASE_TABLE=$(cat <<EOF")
        end = text.index("\nEOF\n", start)
        case_ids = {line.split("|", 1)[0] for line in text[start:end].splitlines()[1:]}
        present = sorted(case_ids & set(FABLE_MAIN_ONLY_LINT_CASES))
        self.assertEqual([], present, f"CASE_TABLE の Fable メイン専用ケース: {present}")

    def test_readmes_drop_the_superseded_pin_rationale(self) -> None:
        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in (AGENT_DISCIPLINE_README, REPO_README)
            if SUPERSEDED_PIN_RATIONALE_PHRASE in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            [], offenders, f"sonnet pin の根拠を実装系メインセッションとする記述: {offenders}"
        )

    def test_ui_discipline_readme_describes_a_model_independent_prompt(self) -> None:
        text = UI_DISCIPLINE_README.read_text(encoding="utf-8")
        with self.subTest(check="モデルに依らない 1 prompt と書く"):
            self.assertIn(UI_SINGLE_PROMPT_PHRASE, text)
        with self.subTest(check="Fable / Sonnet 共通と書かない"):
            self.assertNotIn(UI_SUPERSEDED_SINGLE_PROMPT_PHRASE, text)


if __name__ == "__main__":
    unittest.main()

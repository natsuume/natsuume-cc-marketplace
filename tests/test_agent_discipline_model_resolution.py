"""サブエージェントのモデル解決順序に関する agent-discipline の契約を固定する。

Claude Code はサブエージェントのモデルを
`明示 model > agent 定義の frontmatter > CLAUDE_CODE_SUBAGENT_MODEL > メインセッション継承`
の順に解決し、`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` が設定されているときだけ env
(未設定なら main model) が全てを上書きする。本ファイルは、この前提に立つ 2 つの契約を固定する。

- 配送文言 (``DisciplinePromptResolutionOrderTest``): 分業規律 (discipline.md) の
  `rule:delegation-rules` 節が ``MODEL_RESOLUTION_CANONICAL_SENTENCE`` を持ち、
  ``FORBIDDEN_PROMPT_PHRASES`` を持たないこと。
- 文書 (``AgentDisciplineReadmeResolutionOrderTest`` / ``HookCommentCurrencyTest``):
  agent-discipline README に誤った解決順序の説明が無いこと、分業規律と
  `inject-subagent-rules.sh` に経緯記述が無いこと。

観測点はリポジトリ内のファイル内容に限る。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_PLUGIN = ROOT / "plugins" / "agent-discipline"

BASE_README = BASE_PLUGIN / "README.md"

# plugin ごとの対応ファイル。
INJECT_SUBAGENT_RULES_SCRIPTS = {
    "agent-discipline": BASE_PLUGIN / "hooks" / "scripts" / "inject-subagent-rules.sh",
}

# 分業規律。モデル解決順序の記述は canonical 文で固定する。
DISCIPLINE_PROMPTS = {
    "agent-discipline/discipline.md": BASE_PLUGIN / "hooks" / "prompts" / "discipline.md",
}

# 分業規律の rule:delegation-rules 節に必須の canonical 文 (空白を無視して照合)。
MODEL_RESOLUTION_CANONICAL_SENTENCE = (
    "サブエージェントのモデルは 明示 model > agent 定義の frontmatter >"
    " `CLAUDE_CODE_SUBAGENT_MODEL` > メインセッション継承 の順に解決される。"
    "`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` が設定されている場合のみ、"
    "env (未設定なら main model) が全てを上書きする"
)

# 分業規律の全文に含めない、誤った解決順序の説明 (空白を無視して照合)。
FORBIDDEN_PROMPT_PHRASES = (
    "model の明示指定や agent 定義の frontmatter より優先され",
    "env が `sonnet` の間は opus を指定しても sonnet で走る",
    "全サブエージェント (Workflow 内部の `agent()` 含む) がその値で実行される",
)

# agent-discipline README 全文から消えていること (空白を無視して照合)。
FORBIDDEN_README_PHRASES = (
    "主防御はあくまで `CLAUDE_CODE_SUBAGENT_MODEL` env 設定",
    "env 側でカバー",
    "`CLAUDE_CODE_SUBAGENT_MODEL` env > `tool_input.model` 明示指定",
)

# 説明文書に書かない経緯記述 (契約対象ファイル全体を対象に、空白を無視して照合)。
FORBIDDEN_HISTORY_PHRASES = ("以前は", "かつては", "旧順序", "2.1.251 で反転")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def squeeze(text: str) -> str:
    """行頭の comment 記号 (`#` の並び) と空白をすべて除去した文字列を返す。

    soft line-wrap や行頭の comment インデントで文が分断されていても、同じ文言なら
    一致すると判定するための正規化。shell comment の途中で折り返された文言は各行の
    `#` を挟んで連結されるため、行頭の `#` も除いてから空白を落とす。照合する両辺に
    同じ正規化を掛けて使う。
    """
    without_comment_markers = re.sub(r"(?m)^[ \t]*#+", "", text)
    return re.sub(r"\s+", "", without_comment_markers)


def delegation_rules_section(body: str) -> str:
    """`<!-- rule:delegation-rules -->` から次の rule マーカー直前までを返す。"""
    match = re.search(
        r"<!--\s*rule:delegation-rules\s*-->(.*?)(?=<!--\s*rule:|\Z)", body, re.DOTALL
    )
    return "" if match is None else match.group(1)


class DisciplinePromptResolutionOrderTest(unittest.TestCase):
    """分業規律のモデル解決順序の記述を固定する。"""

    def test_delegation_rules_state_the_canonical_resolution_order(self) -> None:
        """rule:delegation-rules 節に canonical 文がある (空白を無視して照合)。"""
        needle = squeeze(MODEL_RESOLUTION_CANONICAL_SENTENCE)
        for label, path in DISCIPLINE_PROMPTS.items():
            with self.subTest(prompt=label):
                section = delegation_rules_section(read(path))
                self.assertTrue(section, f"{label}: rule:delegation-rules 節が無い")
                self.assertIn(
                    needle,
                    squeeze(section),
                    f"{label}: MODEL_RESOLUTION_CANONICAL_SENTENCE が無い",
                )

    def test_superseded_priority_claims_are_absent(self) -> None:
        """env が明示指定・frontmatter より優先されるという誤った記述が無い。"""
        for label, path in DISCIPLINE_PROMPTS.items():
            body = squeeze(read(path))
            for phrase in FORBIDDEN_PROMPT_PHRASES:
                with self.subTest(prompt=label, phrase=phrase):
                    self.assertNotIn(squeeze(phrase), body, label)


class AgentDisciplineReadmeResolutionOrderTest(unittest.TestCase):
    """agent-discipline README が誤った解決順序の説明を持たない。"""

    def test_superseded_claims_are_absent(self) -> None:
        """誤った解決順序・「主防御は env」・「env 側でカバー」の記述が無い。"""
        body = squeeze(read(BASE_README))
        for phrase in FORBIDDEN_README_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(squeeze(phrase), body)


class HookCommentCurrencyTest(unittest.TestCase):
    """契約対象ファイルの説明文が現在の解決順序だけを書いている。"""

    def test_contract_files_have_no_history_notes(self) -> None:
        """契約対象ファイルに経緯記述が書かれていない。"""
        targets: dict[str, Path] = {
            **DISCIPLINE_PROMPTS,
            **{f"inject-subagent/{k}": v for k, v in INJECT_SUBAGENT_RULES_SCRIPTS.items()},
        }
        for label, path in targets.items():
            if not path.is_file():
                continue
            body = squeeze(read(path))
            for phrase in FORBIDDEN_HISTORY_PHRASES:
                with self.subTest(target=label, phrase=phrase):
                    self.assertNotIn(squeeze(phrase), body, label)


if __name__ == "__main__":
    unittest.main()

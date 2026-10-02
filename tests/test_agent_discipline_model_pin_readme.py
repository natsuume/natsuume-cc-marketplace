"""agent-discipline README が検知層の model pin を正しく説明していることの契約テスト。

検知層 (PreToolUse の type:agent hook 4 本) の `model` には完全なモデル ID を pin する。
hook の `model` に存在しないモデル名を指定すると hook はエラーにならずに通過扱いになり、
検知層が警告なしに止まる。README はこの挙動と、pin を更新したときの確認手順を述べる。

本ファイルが検査するのは次の点:

- (A) README 全文に、退役しうる旧 pin `claude-sonnet-5` (直後が `-5` でないもの) が残っていない
- (B) README の `## 既知の制約` 節の箇条書きに、次の 3 項目がある (語の有無で判定する)
  - 存在しないモデル名を指定すると hook が通過扱いになる (404 で確認済み)
  - hook の `model` には alias を使えず、完全なモデル ID を書く
  - pin の更新後に、4 本の hook それぞれが禁止表現を含む本文で block され、問題の無い
    本文で通過することを実機で確認する

照合は needle と本文の両方から空白 (改行を含む) を全除去した文字列で行う。折り返しや
空白の入れ方の違いでは契約を回避できない。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_DISCIPLINE_README = ROOT / "plugins" / "agent-discipline" / "README.md"

# 旧 pin。`claude-sonnet-5-5` は対象外にする。
STALE_MODEL_PIN_PATTERN = re.compile(r"claude-sonnet-5(?!-5)")

KNOWN_LIMITATIONS_HEADING = "## 既知の制約"

# 既知の制約の 1 項目にそろうべき語の組。
UNKNOWN_MODEL_PASSES_KEYWORDS = ("存在しない", "通過", "404")
FULL_MODEL_ID_KEYWORDS = ("alias", "完全なモデル ID")
PIN_UPDATE_CHECK_KEYWORDS = ("4 本", "禁止表現", "block", "通過", "実機")

LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+\.) ")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def repo_relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def strip_whitespace(text: str) -> str:
    """空白文字 (改行を含む) をすべて除去する。"""
    return "".join(text.split())


def contains(haystack: str, needle: str) -> bool:
    return strip_whitespace(needle) in strip_whitespace(haystack)


def markdown_h2_section(text: str, heading: str) -> str:
    """`heading` 行から次の `## ` 見出しまたは EOF までを返す (見出し行を含む)。"""
    lines = text.splitlines(keepends=True)
    start: int | None = None
    for index, line in enumerate(lines):
        if start is None:
            if line.rstrip("\n") == heading:
                start = index
            continue
        if line.startswith("## "):
            return "".join(lines[start:index])
    if start is None:
        return ""
    return "".join(lines[start:])


def list_items(text: str) -> list[str]:
    """箇条書き 1 項目 (入れ子の項目は別項目) を単位とする列を返す。

    空行と箇条書き項目の開始で区切る。箇条書きの記号で始まらない継続行は直前の
    項目に含める。
    """
    units: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                units.append("\n".join(current))
                current = []
            continue
        if LIST_ITEM_PATTERN.match(line) and current:
            units.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        units.append("\n".join(current))
    return [unit for unit in units if LIST_ITEM_PATTERN.match(unit)]


class StaleModelPinAbsenceTest(unittest.TestCase):
    """(A) README に旧 pin `claude-sonnet-5` が残っていない。"""

    def test_readme_has_no_stale_model_pin(self) -> None:
        hits = [
            f"L{number}: {line.strip()[:120]}"
            for number, line in enumerate(
                read(AGENT_DISCIPLINE_README).splitlines(), start=1
            )
            if STALE_MODEL_PIN_PATTERN.search(line)
        ]
        self.assertEqual(
            [],
            hits,
            f"{repo_relative(AGENT_DISCIPLINE_README)}: 旧 pin `claude-sonnet-5` の記述が残っている",
        )


class KnownLimitationsModelPinTest(unittest.TestCase):
    """(B) 既知の制約が、存在しないモデル名の挙動・完全なモデル ID・pin 更新後の確認を述べる。"""

    def known_limitation_items(self) -> list[str]:
        section = markdown_h2_section(
            read(AGENT_DISCIPLINE_README), KNOWN_LIMITATIONS_HEADING
        )
        if not section.strip():
            self.fail(
                f"{repo_relative(AGENT_DISCIPLINE_README)}: "
                f"`{KNOWN_LIMITATIONS_HEADING}` 見出しが無い"
            )
        return list_items(section)

    def assert_keywords_cooccur_in_one_item(self, keywords: tuple[str, ...]) -> None:
        label = (
            f"{repo_relative(AGENT_DISCIPLINE_README)} の "
            f"`{KNOWN_LIMITATIONS_HEADING}` 節"
        )
        counted = [
            (sum(contains(item, keyword) for keyword in keywords), item)
            for item in self.known_limitation_items()
        ]
        if any(count == len(keywords) for count, _ in counted):
            return
        best_count, best_item = max(counted, default=(0, ""))
        missing = [keyword for keyword in keywords if not contains(best_item, keyword)]
        closest = " ".join(best_item.split())[:160] if best_count else "該当なし"
        self.fail(
            f"{label}: 語 ({', '.join(keywords)}) が同一の箇条書き項目にそろっていない。"
            f" 最も近い項目に欠けている語: {', '.join(missing) or 'なし'}"
            f" / その項目: {closest}"
        )

    def test_unknown_model_name_passes_silently(self) -> None:
        self.assert_keywords_cooccur_in_one_item(UNKNOWN_MODEL_PASSES_KEYWORDS)

    def test_model_requires_full_model_id(self) -> None:
        self.assert_keywords_cooccur_in_one_item(FULL_MODEL_ID_KEYWORDS)

    def test_pin_update_requires_manual_check_of_four_hooks(self) -> None:
        self.assert_keywords_cooccur_in_one_item(PIN_UPDATE_CHECK_KEYWORDS)


if __name__ == "__main__":
    unittest.main()

"""説明文書の経緯記述禁止ルール (rule:comment-currency) の配送契約を検証する。

このルールは、コードコメント・docstring・README 等の説明文書には現在の内容に
対する説明のみを書き、過去の経緯・変更履歴の解説を書かないことを規定する。
配送 3 面 (Fable 向け常時適用ルール / Sonnet 向け常時適用ルール / subagent 向け
常時適用ルール) それぞれについて、次を検証する:

1. ルールマーカーが規定回数だけ存在すること
2. ルールを識別できる canonical 文言が含まれること
3. 各配送ファイルが UTF-16 code units 8,000 以下のサイズ予算に収まること
4. ルール本文ブロック自身が禁止対象の経緯記述 (issue/PR 番号・年月日) を含まない
   こと (自己準拠)
5. ルールマーカーを追加する対象ファイルの冒頭 HTML コメントヘッダが、同様に
   issue/PR 番号・年月日を含まないこと (自己準拠)
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "prompts"

FABLE_MD = PROMPTS / "always-fable.md"
SONNET_MD = {
    "always-sonnet-1.md": PROMPTS / "always-sonnet-1.md",
    "always-sonnet-2.md": PROMPTS / "always-sonnet-2.md",
    "always-sonnet-3.md": PROMPTS / "always-sonnet-3.md",
}
SUBAGENT_MD = PROMPTS / "subagent-rules.md"

SIZE_BUDGET_FILES = {
    "always-fable.md": FABLE_MD,
    **SONNET_MD,
    "subagent-rules.md": SUBAGENT_MD,
}
SIZE_BUDGET_UNITS = 8000

MARKER = "<!-- rule:comment-currency -->"

# 配送 3 面それぞれで検査する canonical 識別文言 (部分文字列照合)。
# Fable / Sonnet は同一文言、subagent 版のみ 3 番目の文言が異なる (「履歴は」接頭辞)。
CANONICAL_PHRASES = {
    "always-fable.md": (
        "過去の経緯・変更履歴の解説",
        "出典としての issue/PR 番号参照",
        "commit message・PR 説明・issue に置く",
        "touch-time",
        "変更履歴節",
    ),
    "always-sonnet-{1,2,3}.md (union)": (
        "過去の経緯・変更履歴の解説",
        "出典としての issue/PR 番号参照",
        "commit message・PR 説明・issue に置く",
        "touch-time",
        "変更履歴節",
    ),
    "subagent-rules.md": (
        "過去の経緯・変更履歴の解説",
        "出典としての issue/PR 番号参照",
        "履歴は commit message・PR 説明・issue に置く",
    ),
}

# 例外 2 要素 (撤去条件付き暫定措置・検証日の付記) が同一文内に共存することを
# 検査するためのキーワード。
EXCEPTION_KEYWORDS = ("撤去条件", "導入日", "検証日")

FORBIDDEN_ISSUE_NUMBER_PATTERN = re.compile(r"#[0-9]")
FORBIDDEN_ISSUE_PREFIX = "issue #"
FORBIDDEN_DATE_PATTERN = re.compile(r"20[0-9]{2}-")

# ヘッダの自己準拠を検査する対象ファイル (ルールマーカーを新規追加する 3 ファイル)。
HEADER_TARGETS = {
    "always-fable.md": FABLE_MD,
    "always-sonnet-2.md": SONNET_MD["always-sonnet-2.md"],
    "subagent-rules.md": SUBAGENT_MD,
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def utf16_length(text: str) -> int:
    """UTF-16 code unit 数を返す (配送予算の計測単位)。"""
    return len(text.encode("utf-16-le")) // 2


def sonnet_union_text() -> str:
    """always-sonnet-{1,2,3}.md 3 ファイルの本文を連結して返す。"""
    return "\n".join(read(path) for path in SONNET_MD.values())


def sonnet_file_with_marker() -> Path | None:
    """マーカーを保持する sonnet part ファイルを返す。無ければ None。"""
    for path in SONNET_MD.values():
        if MARKER in read(path):
            return path
    return None


def leading_html_comment(text: str) -> str | None:
    """ファイル冒頭の HTML コメントブロック (`<!--` 〜 `-->`) を返す。無ければ None。"""
    match = re.match(r"\A<!--.*?-->", text, re.DOTALL)
    if match is None:
        return None
    return match.group(0)


def rule_block(text: str, marker: str = MARKER) -> str | None:
    """marker の直後から、次の `<!--` マーカー・`---` 行・EOF 手前までを返す。

    marker が本文中に存在しない場合は None を返す。
    """
    start = text.find(marker)
    if start < 0:
        return None
    content_start = start + len(marker)
    boundary_candidates = []
    next_marker = text.find("<!--", content_start)
    if next_marker >= 0:
        boundary_candidates.append(next_marker)
    for m in re.finditer(r"(?m)^---\s*$", text[content_start:]):
        boundary_candidates.append(content_start + m.start())
        break
    end = min(boundary_candidates) if boundary_candidates else len(text)
    return text[content_start:end]


def sentence_contains_all(text: str, keywords: tuple[str, ...]) -> bool:
    """「。」区切りのいずれかの文が keywords を全て含むか。"""
    for sentence in re.findall(r"[^。]*。", text):
        if all(keyword in sentence for keyword in keywords):
            return True
    return False


class MarkerPresenceTests(unittest.TestCase):
    """rule:comment-currency マーカーが各配送面に規定回数だけ存在すること。"""

    def test_fable_marker_appears_exactly_once(self) -> None:
        count = read(FABLE_MD).count(MARKER)
        self.assertEqual(1, count, f"always-fable.md 内のマーカー出現数: {count}")

    def test_sonnet_marker_appears_exactly_once_across_parts(self) -> None:
        total = sum(read(path).count(MARKER) for path in SONNET_MD.values())
        self.assertEqual(
            1, total, f"always-sonnet-{{1,2,3}}.md 合計のマーカー出現数: {total}"
        )

    def test_subagent_marker_appears_exactly_once(self) -> None:
        count = read(SUBAGENT_MD).count(MARKER)
        self.assertEqual(1, count, f"subagent-rules.md 内のマーカー出現数: {count}")


class CanonicalBodyTests(unittest.TestCase):
    """各配送面に、ルールを識別できる canonical 文言・例外文が含まれること。"""

    def _face_texts(self) -> dict[str, str]:
        return {
            "always-fable.md": read(FABLE_MD),
            "always-sonnet-{1,2,3}.md (union)": sonnet_union_text(),
            "subagent-rules.md": read(SUBAGENT_MD),
        }

    def test_canonical_phrases_present(self) -> None:
        violations = []
        for face, text in self._face_texts().items():
            missing = [p for p in CANONICAL_PHRASES[face] if p not in text]
            if missing:
                violations.append(f"{face}: {missing}")
        self.assertEqual([], violations, f"canonical 文言が無い面: {violations}")

    def test_exception_sentence_present(self) -> None:
        violations = [
            face
            for face, text in self._face_texts().items()
            if not sentence_contains_all(text, EXCEPTION_KEYWORDS)
        ]
        self.assertEqual([], violations, f"例外 2 要素を含む文が無い面: {violations}")


class SizeBudgetTests(unittest.TestCase):
    """各配送ファイルが UTF-16 code units 8,000 以下に収まること。"""

    def test_all_files_within_budget(self) -> None:
        violations = []
        for name, path in SIZE_BUDGET_FILES.items():
            length = utf16_length(read(path))
            if length > SIZE_BUDGET_UNITS:
                violations.append(f"{name}: {length}")
        self.assertEqual(
            [], violations, f"配送予算 ({SIZE_BUDGET_UNITS} UTF-16 units) を超過: {violations}"
        )


class RuleBlockSelfComplianceTests(unittest.TestCase):
    """ルール本文ブロック自身が禁止対象の経緯記述を含まないこと (自己準拠)。"""

    @staticmethod
    def _forbidden_matches(block: str) -> list[str]:
        found = []
        if FORBIDDEN_ISSUE_NUMBER_PATTERN.search(block):
            found.append("#数字")
        if FORBIDDEN_ISSUE_PREFIX in block:
            found.append("issue #")
        if FORBIDDEN_DATE_PATTERN.search(block):
            found.append("年月日形式")
        return found

    def test_rule_blocks_free_of_history_references(self) -> None:
        targets: dict[str, Path | None] = {
            "always-fable.md": FABLE_MD,
            "always-sonnet-{1,2,3}.md": sonnet_file_with_marker(),
            "subagent-rules.md": SUBAGENT_MD,
        }
        violations = []
        for label, path in targets.items():
            if path is None:
                violations.append(f"{label} (マーカーを保持するファイルが見つからない)")
                continue
            block = rule_block(read(path))
            if block is None:
                violations.append(f"{label} (マーカーが見つからずブロックを抽出できない)")
                continue
            found = self._forbidden_matches(block)
            if found:
                violations.append(f"{label} ({', '.join(found)} を含む)")
        self.assertEqual([], violations, f"ルールブロックの自己準拠違反: {violations}")


class HeaderSelfComplianceTests(unittest.TestCase):
    """ルールマーカーを追加する対象ファイルの冒頭ヘッダが経緯記述を含まないこと。"""

    def test_headers_free_of_issue_numbers_and_dates(self) -> None:
        violations = []
        for name, path in HEADER_TARGETS.items():
            header = leading_html_comment(read(path))
            if header is None:
                violations.append(f"{name} (冒頭 HTML コメントが見つからない)")
                continue
            found = []
            if FORBIDDEN_ISSUE_NUMBER_PATTERN.search(header):
                found.append("#数字")
            if FORBIDDEN_DATE_PATTERN.search(header):
                found.append("年月日形式")
            if found:
                violations.append(f"{name} ({', '.join(found)} を含む)")
        self.assertEqual([], violations, f"ヘッダの自己準拠違反: {violations}")


if __name__ == "__main__":
    unittest.main()

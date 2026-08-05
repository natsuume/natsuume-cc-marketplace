"""説明文書の経緯記述禁止ルール (rule:comment-currency) の配送契約を検証する。

このルールは、コードコメント・docstring・README 等の説明文書には現在の内容に
対する説明のみを書き、過去の経緯・変更履歴の解説を書かないことを規定する。
配送 3 面 (fable 向け常時適用ルール / sonnet 向け常時適用ルール / subagent 向け
常時適用ルール) それぞれについて、次を検証する:

1. ルールマーカーが規定回数だけ存在すること
2. マーカーを保持するファイルを面ごとに一意に解決したうえで、そのルール本文
   ブロック内に canonical 文言・例外文が含まれること (ファイル全文や複数
   ファイルの連結への部分文字列一致では検査しない)
3. 各配送経路 (SessionStart / UserPromptSubmit / SubagentStart の各 hook) が
   実際に生成する additionalContext の最大構成が UTF-16 code units 8,000 未満に
   収まること
4. ルール本文ブロック自身、および面ごとに解決した冒頭 HTML コメントヘッダが、
   禁止対象の経緯記述 (issue/PR 番号・年月日) を含まないこと (自己準拠)

面の解決・ブロック抽出・正規化などの共有ロジックは、実ファイルに依存しない
synthetic な入力での自己テストも別途持つ。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "prompts"
SCRIPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "scripts"

FABLE_MD = PROMPTS / "always-fable.md"
SONNET_MD = {
    "always-sonnet-1.md": PROMPTS / "always-sonnet-1.md",
    "always-sonnet-2.md": PROMPTS / "always-sonnet-2.md",
    "always-sonnet-3.md": PROMPTS / "always-sonnet-3.md",
}
SUBAGENT_MD = PROMPTS / "subagent-rules.md"

INJECT_ALWAYS_SH = SCRIPTS / "inject-always.sh"
INJECT_RULES_PART_SH = SCRIPTS / "inject-rules-part.sh"
INJECT_SUBAGENT_RULES_SH = SCRIPTS / "inject-subagent-rules.sh"

MARKER = "<!-- rule:comment-currency -->"
SIZE_BUDGET_UNITS = 8000

# 配送面のラベル。fable / subagent は固定ファイル、sonnet はマーカーを保持する
# part を動的に解決する (resolve_face_path 参照)。
FACES = (
    "always-fable.md",
    "always-sonnet-{1,2,3}.md",
    "subagent-rules.md",
)

# 各面のルールブロック内で検査する canonical 識別文言 (部分文字列照合)。
# fable / sonnet は同一文言、subagent 版のみ 3 番目の文言が異なる (「履歴は」接頭辞)。
CANONICAL_PHRASES = {
    "always-fable.md": (
        "過去の経緯・変更履歴の解説",
        "出典としての issue/PR 番号参照",
        "commit message・PR 説明・issue に置く",
        "touch-time",
        "変更履歴節",
    ),
    "always-sonnet-{1,2,3}.md": (
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

# 例外 2 要素の canonical 文 (逐語)。段落分割を跨がず、soft-wrap (単一改行) には
# 頑健な正規化のうえで部分文字列一致を検査する。
CANONICAL_EXCEPTION_SENTENCE = {
    "always-fable.md": "例外は 2 つ — (1) 撤去条件付き暫定措置は「現在の不具合・撤去条件・確認方法」の 3 要素で書く (導入日は書かない) (2) 現行の主張への検証日・検証環境の付記は証拠の鮮度情報として許可する。",
    "always-sonnet-{1,2,3}.md": "例外は 2 つ — (1) 撤去条件付き暫定措置は「現在の不具合・撤去条件・確認方法」の 3 要素で書く (導入日は書かない) (2) 現行の主張への検証日・検証環境の付記 (「YYYY-MM-DD 実測」「バージョン X で確認」等) は証拠の鮮度情報として許可する。",
    "subagent-rules.md": "例外: 撤去条件付き暫定措置の「現在の不具合・撤去条件・確認方法」(導入日なし) と、現行の主張への検証日・検証環境の付記。",
}

FORBIDDEN_ISSUE_NUMBER_PATTERN = re.compile(r"#[0-9]")
FORBIDDEN_ISSUE_PREFIX = "issue #"
# ISO 形式 (20XX-) に加え、20XX/ ・ 20XX. ・ 20XX年 の表記も禁止対象として明文化する。
FORBIDDEN_DATE_PATTERN = re.compile(r"20[0-9]{2}[-/.年]")

# ルールブロックの終端は、行頭の構造マーカー (`<!-- rule:` / `<!-- subagent-rule:`) に
# 限定する。任意の `<!--` を境界にすると、ブロック内の説明用 HTML コメント (構造
# マーカーではないもの) で意図せず切断されうるため。
STRUCTURAL_MARKER_PATTERN = re.compile(r"(?m)^<!-- (?:rule|subagent-rule):")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def utf16_length(text: str) -> int:
    """UTF-16 code unit 数を返す (配送予算の計測単位)。"""
    return len(text.encode("utf-16-le")) // 2


def leading_html_comment(text: str) -> str | None:
    """ファイル冒頭の HTML コメントブロック (`<!--` 〜 `-->`) を返す。無ければ None。"""
    match = re.match(r"\A<!--.*?-->", text, re.DOTALL)
    if match is None:
        return None
    return match.group(0)


def rule_block(text: str, marker: str = MARKER) -> str | None:
    """marker の直後から、次の行頭構造マーカー・`---` 行・EOF 手前までを返す。

    marker が本文中に存在しない場合は None を返す。
    """
    start = text.find(marker)
    if start < 0:
        return None
    content_start = start + len(marker)
    boundary_candidates = []
    next_marker_match = STRUCTURAL_MARKER_PATTERN.search(text, content_start)
    if next_marker_match is not None:
        boundary_candidates.append(next_marker_match.start())
    for m in re.finditer(r"(?m)^---\s*$", text[content_start:]):
        boundary_candidates.append(content_start + m.start())
        break
    end = min(boundary_candidates) if boundary_candidates else len(text)
    return text[content_start:end]


def normalize_soft_wrapped(text: str) -> str:
    """行末の改行のみを吸収して連結する (行頭インデントは保持し、区切り文字を挟まない)。"""
    return "".join(line.rstrip() for line in text.splitlines())


def block_contains_paragraph_with(block: str, canonical: str) -> bool:
    """block を空行区切りの段落に分割し、いずれかの段落を正規化した文字列が
    canonical を部分文字列として含むかを判定する。

    段落ごとに個別に正規化してから照合するため、空行を挟んで 2 段落にまたがる
    canonical 文字列は (各段落を正規化しても) 一致しない。
    """
    return any(canonical in normalize_soft_wrapped(p) for p in block.split("\n\n"))


def resolve_marker_holder(named_texts: dict[str, str]) -> str | None:
    """{ラベル: 本文} からマーカーを保持する唯一のラベルを返す。

    マーカーを保持するラベルが 0 件または複数件の場合は fail-closed で None を返す。
    """
    holders = [label for label, text in named_texts.items() if MARKER in text]
    if len(holders) != 1:
        return None
    return holders[0]


def resolve_face_path(face: str) -> Path | None:
    """配送面から、実際にマーカーを保持するファイルの Path を解決する。

    fable / subagent は固定ファイル。sonnet はマーカーを保持する part がちょうど
    1 つの場合のみそのファイルを返し、0 件・複数件は None (fail-closed) を返す。
    """
    if face == "always-fable.md":
        return FABLE_MD
    if face == "subagent-rules.md":
        return SUBAGENT_MD
    if face == "always-sonnet-{1,2,3}.md":
        holder = resolve_marker_holder(
            {name: read(path) for name, path in SONNET_MD.items()}
        )
        return SONNET_MD[holder] if holder is not None else None
    raise ValueError(f"unknown face: {face}")


def resolve_face(face: str) -> tuple[Path, str, str] | None:
    """配送面から (path, ルールブロック, 冒頭ヘッダ) を解決する。

    path が解決できない、またはルールブロックが抽出できない場合は None
    (fail-closed) を返す。冒頭ヘッダを持たないファイルは空文字列を返す。
    """
    path = resolve_face_path(face)
    if path is None:
        return None
    text = read(path)
    block = rule_block(text)
    if block is None:
        return None
    header = leading_html_comment(text) or ""
    return path, block, header


def run_hook(
    script: Path,
    payload: dict[str, str],
    tmp_dir: str,
    args: list[str] | None = None,
) -> str:
    """hook script を隔離 TMPDIR で実行し、additionalContext を返す。

    実リポジトリの ``${TMPDIR:-/tmp}/agent-discipline-state`` を汚さないよう、
    呼び出し側が用意した一時ディレクトリを TMPDIR として渡す。
    """
    env = os.environ.copy()
    env["TMPDIR"] = tmp_dir
    result = subprocess.run(
        ["/bin/bash", str(script), *(args or [])],
        cwd=REPO_ROOT,
        env=env,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{script.name} 異常終了 (code={result.returncode}): {result.stderr}"
        )
    if not result.stdout.strip():
        raise AssertionError(
            f"{script.name} が additionalContext を出力しなかった (stderr: {result.stderr})"
        )
    data = json.loads(result.stdout)
    return data["hookSpecificOutput"]["additionalContext"]


def delivery_fable(tmp_dir: str) -> str:
    """SessionStart (inject-always.sh) が fable 判定時に配送する additionalContext。"""
    payload = {
        "session_id": "size-budget-fable",
        "hook_event_name": "SessionStart",
        "model": "claude-fable-5",
    }
    return run_hook(INJECT_ALWAYS_SH, payload, tmp_dir)


def delivery_sonnet_part1_self_gate(tmp_dir: str) -> str:
    """SessionStart (inject-always.sh) がモデル判定不能時に配送する、self-gate
    前置き + always-sonnet-1.md (part 1 の最大構成)。
    """
    payload = {"session_id": "size-budget-sonnet-1", "hook_event_name": "SessionStart"}
    return run_hook(INJECT_ALWAYS_SH, payload, tmp_dir)


def delivery_sonnet_part_self_gate(part: str, tmp_dir: str) -> str:
    """UserPromptSubmit (inject-rules-part.sh <part>) が self-gate 付きで配送する、
    part-self-gate.md + always-sonnet-<part>.md (part 2/3 の最大構成)。
    """
    session_id = f"size-budget-sonnet-{part}"
    state_dir = Path(tmp_dir) / "agent-discipline-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"pending-model-{session_id}").write_text("", encoding="utf-8")
    payload = {"session_id": session_id, "hook_event_name": "UserPromptSubmit"}
    return run_hook(INJECT_RULES_PART_SH, payload, tmp_dir, args=[part])


def delivery_subagent(tmp_dir: str) -> str:
    """SubagentStart (inject-subagent-rules.sh) が配送する subagent-rules.md 全文。"""
    return run_hook(INJECT_SUBAGENT_RULES_SH, {}, tmp_dir)


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
    """各配送面のルールブロック内に canonical 文言・例外文が含まれること。"""

    def test_canonical_phrases_present_in_rule_block(self) -> None:
        violations = []
        for face in FACES:
            resolved = resolve_face(face)
            if resolved is None:
                violations.append(
                    f"{face} (面が解決できない: マーカー 0 件/複数件、またはブロック抽出不可)"
                )
                continue
            _path, block, _header = resolved
            missing = [p for p in CANONICAL_PHRASES[face] if p not in block]
            if missing:
                violations.append(f"{face}: {missing}")
        self.assertEqual(
            [], violations, f"ルールブロック内に canonical 文言が無い面: {violations}"
        )

    def test_exception_sentence_present_in_rule_block(self) -> None:
        violations = []
        for face in FACES:
            resolved = resolve_face(face)
            if resolved is None:
                violations.append(f"{face} (面が解決できない)")
                continue
            _path, block, _header = resolved
            canonical = CANONICAL_EXCEPTION_SENTENCE[face]
            if not block_contains_paragraph_with(block, canonical):
                violations.append(face)
        self.assertEqual(
            [], violations, f"例外 2 要素の canonical 文が段落内に無い面: {violations}"
        )


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class SizeBudgetTests(unittest.TestCase):
    """各配送経路が実際に生成する additionalContext が配送予算未満に収まること。"""

    DELIVERY_BUILDERS = {
        "fable 向け always 配送 (inject-always.sh, model=fable)": delivery_fable,
        "sonnet part 1 配送 (inject-always.sh, self-gate)": delivery_sonnet_part1_self_gate,
        "sonnet part 2 配送 (inject-rules-part.sh 2, self-gate)": (
            lambda tmp_dir: delivery_sonnet_part_self_gate("2", tmp_dir)
        ),
        "sonnet part 3 配送 (inject-rules-part.sh 3, self-gate)": (
            lambda tmp_dir: delivery_sonnet_part_self_gate("3", tmp_dir)
        ),
        "subagent-rules 配送 (inject-subagent-rules.sh)": delivery_subagent,
    }

    def test_all_delivery_paths_within_strict_budget(self) -> None:
        violations = []
        for label, builder in self.DELIVERY_BUILDERS.items():
            with tempfile.TemporaryDirectory() as tmp_dir:
                context = builder(tmp_dir)
            length = utf16_length(context)
            if not (length < SIZE_BUDGET_UNITS):
                violations.append(f"{label}: {length}")
        self.assertEqual(
            [],
            violations,
            f"配送予算 ({SIZE_BUDGET_UNITS} UTF-16 units 未満) を超過: {violations}",
        )


class RuleBlockSelfComplianceTests(unittest.TestCase):
    """ルール本文ブロック自身が禁止対象の経緯記述を含まないこと (自己準拠)。"""

    @staticmethod
    def _forbidden_matches(text: str) -> list[str]:
        found = []
        if FORBIDDEN_ISSUE_NUMBER_PATTERN.search(text):
            found.append("#数字")
        if FORBIDDEN_ISSUE_PREFIX in text:
            found.append("issue #")
        if FORBIDDEN_DATE_PATTERN.search(text):
            found.append("年月日形式")
        return found

    def test_rule_blocks_free_of_history_references(self) -> None:
        violations = []
        for face in FACES:
            resolved = resolve_face(face)
            if resolved is None:
                violations.append(f"{face} (面が解決できない)")
                continue
            _path, block, _header = resolved
            found = self._forbidden_matches(block)
            if found:
                violations.append(f"{face} ({', '.join(found)} を含む)")
        self.assertEqual([], violations, f"ルールブロックの自己準拠違反: {violations}")


class HeaderSelfComplianceTests(unittest.TestCase):
    """面ごとに解決した冒頭 HTML コメントヘッダが経緯記述を含まないこと。"""

    def test_headers_free_of_issue_numbers_and_dates(self) -> None:
        violations = []
        for face in FACES:
            resolved = resolve_face(face)
            if resolved is None:
                violations.append(f"{face} (面が解決できない)")
                continue
            _path, _block, header = resolved
            found = []
            if FORBIDDEN_ISSUE_NUMBER_PATTERN.search(header):
                found.append("#数字")
            if FORBIDDEN_DATE_PATTERN.search(header):
                found.append("年月日形式")
            if found:
                violations.append(f"{face} ({', '.join(found)} を含む)")
        self.assertEqual([], violations, f"ヘッダの自己準拠違反: {violations}")


class HelperSyntheticContractTests(unittest.TestCase):
    """実ファイルに依存しない synthetic 入力で、共有 helper 自身の挙動を固定する。"""

    def test_canonical_phrase_outside_block_is_not_matched(self) -> None:
        """ブロック外にのみ存在する文言は、ブロック抽出後の照合では見つからない
        こと (ファイル全文/連結検索への回帰を防ぐ)。
        """
        phrase = "synthetic-canonical-phrase"
        text = (
            f"{phrase} (before marker, must not count)\n\n"
            f"{MARKER}\n"
            "## synthetic heading\n\n"
            "block body without the phrase\n\n"
            "---\n\n"
            f"{phrase} (after boundary, must not count)\n"
        )
        block = rule_block(text)
        self.assertIsNotNone(block)
        self.assertNotIn(phrase, block)

    def test_non_structural_comment_does_not_truncate_block(self) -> None:
        """ブロック内の非構造 HTML コメント (`<!-- rule:` / `<!-- subagent-rule:`
        以外) はブロック境界にならないこと (行頭の構造マーカー限定への変更の固定)。
        """
        text = (
            f"{MARKER}\n"
            "## synthetic heading\n\n"
            "before comment\n"
            "<!-- explanatory note, not a structural marker -->\n"
            "after comment (must remain inside the block)\n\n"
            "---\n"
        )
        block = rule_block(text)
        self.assertIsNotNone(block)
        self.assertIn("after comment (must remain inside the block)", block)

    def test_marker_holder_resolution_is_fail_closed(self) -> None:
        """マーカー保持ラベルが 0 件・複数件のときは解決が fail-closed (None) に
        なり、ちょうど 1 件のときのみそのラベルを返すこと。
        """
        self.assertIsNone(
            resolve_marker_holder({"a": "no marker here", "b": "still none"})
        )
        self.assertIsNone(
            resolve_marker_holder(
                {"a": f"has {MARKER} once", "b": f"also has {MARKER}"}
            )
        )
        self.assertEqual(
            "a", resolve_marker_holder({"a": f"has {MARKER}", "b": "no marker"})
        )

    def test_exception_sentence_paragraph_boundary(self) -> None:
        """canonical 文が段落分割 (空行) を跨ぐ場合は不一致、単一改行の soft-wrap
        は一致すること。
        """
        sentence = "AはBでありCである。"
        split_by_paragraph = "AはB\n\nでありCである。"
        soft_wrapped = "AはB\nでありCである。"

        self.assertFalse(block_contains_paragraph_with(split_by_paragraph, sentence))
        self.assertTrue(block_contains_paragraph_with(soft_wrapped, sentence))

    def test_exact_budget_boundary_fails_strict_less_than(self) -> None:
        """ちょうど 8,000 UTF-16 units の文字列は strict 未満 (`< 8000`) 判定で
        fail すること。
        """
        text = "あ" * SIZE_BUDGET_UNITS
        self.assertEqual(SIZE_BUDGET_UNITS, utf16_length(text))
        self.assertFalse(utf16_length(text) < SIZE_BUDGET_UNITS)


if __name__ == "__main__":
    unittest.main()

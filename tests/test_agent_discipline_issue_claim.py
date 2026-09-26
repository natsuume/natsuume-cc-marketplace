"""agent-discipline の `rule:issue-claim` (連続 issue 解決時の排他制御) の契約テスト。

確保は claim comment の先着判定だけで確定し、確定後にラベルを付けてから作業 branch を
作る。検査対象は always-3.md の `<!-- rule:issue-claim -->` から次の `<!-- rule:`
マーカーの手前までの節と、その手順を要約する issue-start skill・README である。

- 着手手順 (``IssueClaimStartProcedureTest``): 早期判定 → claim comment の投稿 →
  3 秒待機 → REST GET による comment の再取得と先着判定 → ラベル付与 → 作業 branch の
  作成を、この順に書く。branch push で確保を確定する段階 (空 commit・即 push) と、
  排他基盤としての branch 名 uniqueness の説明が無い。
- 先着判定 (``IssueClaimArbitrationTest``): claim comment の書式、`(created_at, 数値 id)`
  の辞書順最小を先着とする規則、REST GET の取得失敗・自分の claim が無い場合の
  fail-closed 停止とユーザーへの報告。
- 後片付け (``IssueClaimCleanupTest``): 撤退は claim comment の削除とユーザーへの 1 行報告
  だけ。着手中断は自分の claim comment の削除だけで、branch・draft PR・ラベルは残す。
  節のどこにも branch を削除するコマンドを置かない。
- 削除規律 (``IssueClaimDeletionDisciplineTest``): 他 session の claim comment / branch /
  ラベルを削除しない規律、`session=` による自他判別、撤退・着手中断のどちらでもラベルを
  削除しないこと、廃止した手順番号を参照しないこと、claim の反映を保証として書かないこと。
- 明示指示による再開 (``IssueClaimExplicitResumeTest``): ユーザのメッセージまたは handoff の
  文書が issue 番号か branch 名を挙げて継続を指示した場合 (明示指示) だけ step 6 から再開し、
  ラベルや他 session の claim comment があっても撤退も削除もしない。明示指示が無ければ、
  また明示指示があっても対応する branch が無ければ step 1 から実行する。
- 作業 branch の用意 (``IssueClaimWorkBranchTest``): step 6 は `git fetch origin` の後 (失敗
  したら停止して報告)、同名 branch の有無で作成・switch・fast-forward・停止を分ける。draft PR
  は `rule:tdd-two-phase` に合わせ、Phase A の commit を push した後に作る。
- 関連文書 (``IssueClaimRelatedDocumentTest``): issue-start skill と README が branch push
  による確定・二段構成を説明しない。
- issue-start skill の pick-up 分岐 (``IssueStartPickUpTest``): 明示指示の有無で 2 つに分け、
  明示指示が無ければ既存の branch / PR があっても排他制御に進む。
- 評価基準 (``IssueClaimEvaluationTest``): `docs/discipline-evaluation.md` の issue-claim の
  評価基準が、明示指示による再開を Pass として扱う。

文章全体の一致は検査しない。手順を識別するコマンド・識別子の有無と出現順序を検査し、
言い回しは実装側で選べる。always-3.md・issue-start skill・評価手順書のパスは
``IssueClaimTestCase`` のクラス属性で差し替えられる (置換予定の本文を一時ファイルで検査するため)。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "agent-discipline"
ALWAYS_3 = PLUGIN_DIR / "hooks" / "prompts" / "always-3.md"
ISSUE_START_SKILL = PLUGIN_DIR / "skills" / "issue-start" / "SKILL.md"
PLUGIN_README = PLUGIN_DIR / "README.md"
REPO_README = ROOT / "README.md"
EVALUATION_DOC = ROOT / "docs" / "discipline-evaluation.md"

ISSUE_CLAIM_MARKER = "<!-- rule:issue-claim -->"
RULE_MARKER_PREFIX = "<!-- rule:"
START_PROCEDURE_HEADING = "### 着手手順"
REPO_README_PLUGIN_HEADING = "## agent-discipline"

# claim comment の書式。既存の claim comment と互換を保つため変えない。
CLAIM_COMMENT_FORMAT = (
    "🔒 ai:claim branch=<prefix>/issue-<N>-<slug> "
    "session=<セッションID> ts=<UTC ISO 8601>"
)

# 着手手順の各段階と、その段階を識別する語 (手順に書く順)。
START_STEPS = (
    ("早期判定", "早期判定"),
    ("claim comment の投稿", "gh issue comment"),
    ("3 秒待機", "sleep 3"),
    ("REST GET による comment の再取得", "gh api --paginate"),
    ("先着判定", "(created_at, 数値 id)"),
    ("ラベル付与", "--add-label ai:in-progress"),
    ("作業 branch の作成", "git switch -c"),
)

# branch push で確保を確定する段階と、それを排他基盤とする説明の語。
BRANCH_PUSH_CONFIRMATION_PHRASES = (
    "--allow-empty",
    "git push -u",
    "二段",
    "branch 名 uniqueness",
    "排他基盤 2",
)

# 撤退・着手中断の段落の先頭に置く太字ラベル。
WITHDRAWAL_LABEL = "**撤退**"
INTERRUPTION_LABEL = "**着手中断**"

# branch を削除するコマンド。撤退・着手中断のどちらの後片付けにも置かない。
BRANCH_DELETE_COMMANDS = (
    "git push origin --delete",
    "git push origin :",
    "git branch -D",
)

# 着手中断で残すもの。
INTERRUPTION_KEPT_ARTIFACTS = ("branch", "draft PR", "ラベル", "残す")

# 削除規律で branch も削除対象に含めていた語。
MERGED_CLEANUP_PHRASE = "自分の claim comment と branch のみ削除"

# 撤退・着手中断のどちらでもラベルを削除しないことを示す語。
LABEL_KEPT_PHRASE = "ラベルは削除しない"

# 「よくある誤操作と回避」が参照してはならない、確定段階を含んでいた手順番号。
STALE_STEP_REFERENCES = ("step 2-5", "step 1-3", "step 1, 4, 5")

# 他 session の claim が一覧に反映されることを保証として書く語。
VISIBILITY_GUARANTEE_PHRASE = "必ず観測できる"

OTHER_SESSION_TARGETS = "他 session の claim comment / branch / ラベル"
OWNERSHIP_CRITERION = "「自分の claim か」の判定基準"

# issue-start skill が排他制御の手順として書かない語。
SKILL_BRANCH_PUSH_PHRASE = "branch push による確定"

# README が排他系の仕組みとして書かない語。
README_BRANCH_PUSH_PHRASES = (
    "branch push (確定的排他)",
    "二段構成",
    "即 push で確定的排他",
    "branch push 排他",
    "push 成功時のみラベル付与",
)

# 着手手順の step 6 (作業 branch の用意) の項目の先頭。下位項目を含めて 1 項目とする。
WORK_BRANCH_STEP_MARKER = "6. **作業 branch"

# 明示指示の定義を書く段落を識別する語 (両方を含む段落を定義の段落とする)。
EXPLICIT_INSTRUCTION = "明示指示"
EXPLICIT_DEFINITION_MARKERS = (EXPLICIT_INSTRUCTION, "handoff")

# 明示指示の定義に書く要素。
EXPLICIT_DEFINITION_PHRASES = (
    "ユーザ",
    "メッセージ",
    "handoff",
    "文書",
    "issue 番号",
    "branch 名",
    "継続",
)

# 明示指示が無いことを示す表記 (空白を除去した文に照合する)。
NO_EXPLICIT_INSTRUCTION = re.compile(r"明示指示が(?:無|な)(?:い|く|けれ)")

# 他 session の claim comment を指す表記 (空白を除去した文に照合する)。
OTHER_SESSION = re.compile(r"他(?:セッション|session)")

# step 6 の分岐時の停止で使ってはならない、local / remote の branch を変更するコマンド。
DIVERGENCE_FORBIDDEN_COMMANDS = (
    "git reset",
    "--force",
    "push -f",
    "git branch -D",
    "git push origin --delete",
)

# step 6 にあった、draft PR を実装より先に作る旧い順序の記述。
DRAFT_PR_FIRST_PHRASE = "draft PR 作成 → 実装"

ISSUE_START_PICK_UP_HEADING = "## 1. pick-up 分岐"
ISSUE_START_CLAIM_HEADING = "## 2. 排他制御の参照"

# issue-start skill の pick-up 分岐にあった、明示指示に触れない再開の分岐の語。
UNCONDITIONAL_RESUME_PHRASE = "branch / open PR が既に存在し"

# 排他制御の参照が、新規着手の場合だけに排他制御を限っていた語。
NEW_START_ONLY_PHRASE = "新規着手と判定した場合"

EVALUATION_ISSUE_CLAIM_ROW = "| issue-claim 手順の遵守 |"
EVALUATION_ISSUE_CLAIM_NOTE = "※ issue-claim 手順の遵守における経路別 Pass 定義"
# 評価基準の表の列 (指標名 | 対応 rule | 適用機会 | Pass | Violation | grep anchor) の Pass 列。
EVALUATION_PASS_COLUMN = 3

HEADING_PATTERN = re.compile(r"^#{1,6} ")
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+\.) ")
TOP_LEVEL_LIST_ITEM_PATTERN = re.compile(r"^(?:[-*+]|\d+\.) ")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def display_path(path: Path) -> str:
    """失敗メッセージ用のパス (リポジトリ内ならリポジトリ直下からの相対パス)。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def strip_whitespace(text: str) -> str:
    """空白文字をすべて除去する (行の折り返しで分断された出現も照合するため)。"""
    return "".join(text.split())


def rule_block(text: str, marker: str) -> str:
    """`marker` から次の `<!-- rule:` マーカーの手前までを返す (マーカー行を除く)。"""
    start = text.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    end = text.find(RULE_MARKER_PREFIX, body_start)
    return text[body_start:] if end < 0 else text[body_start:end]


def markdown_section(text: str, heading: str) -> str:
    """見出し行 `heading` の次の行から、同じか上位レベルの次の見出しの手前までを返す。"""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines(keepends=True)
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if start is None:
            if stripped == heading or stripped.startswith(heading + " "):
                start = index
            continue
        if HEADING_PATTERN.match(stripped):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level:
                return "".join(lines[start + 1 : index])
    if start is None:
        return ""
    return "".join(lines[start + 1 :])


def labeled_paragraph(text: str, label: str) -> str:
    """行頭が `label` の行から、次の行頭太字ラベル・見出しの手前までを返す。"""
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(label)), None
    )
    if start is None:
        return ""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if HEADING_PATTERN.match(line) or line.startswith("**"):
            break
        end += 1
    return "\n".join(lines[start:end])


def list_item_containing(text: str, marker: str) -> str:
    """`marker` を含む箇条書き 1 項目分 (次の項目・空行の手前まで) を返す。"""
    lines = text.splitlines()
    target = next(
        (index for index, line in enumerate(lines) if marker in line), None
    )
    if target is None:
        return ""
    start = target
    while start > 0 and not LIST_ITEM_PATTERN.match(lines[start]):
        if not lines[start - 1].strip():
            break
        start -= 1
    end = target + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip() or LIST_ITEM_PATTERN.match(line):
            break
        end += 1
    return "\n".join(lines[start:end])


def top_level_list_item_containing(text: str, marker: str) -> str:
    """`marker` を含む行から、次のトップレベル箇条書き項目・空行の手前までを返す。

    インデントされた下位項目は同じ項目の一部として含める。
    """
    lines = text.splitlines()
    target = next(
        (index for index, line in enumerate(lines) if marker in line), None
    )
    if target is None:
        return ""
    end = target + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip() or TOP_LEVEL_LIST_ITEM_PATTERN.match(line):
            break
        end += 1
    return "\n".join(lines[target:end])


def top_level_list_items(text: str) -> list[str]:
    """トップレベルの箇条書き項目を、インデントされた下位項目・継続行を含めて返す。"""
    items: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if TOP_LEVEL_LIST_ITEM_PATTERN.match(line):
            if current is not None:
                items.append("\n".join(current))
            current = [line]
        elif current is not None and line.strip() and line[:1].isspace():
            current.append(line)
        elif current is not None:
            items.append("\n".join(current))
            current = None
    if current is not None:
        items.append("\n".join(current))
    return items


def paragraph_containing(text: str, markers: tuple[str, ...]) -> str:
    """空行で区切った段落のうち、`markers` をすべて含む最初の段落を返す。"""
    for paragraph in re.split(r"\n\s*\n", text):
        stripped = strip_whitespace(paragraph)
        if all(strip_whitespace(marker) in stripped for marker in markers):
            return paragraph
    return ""


def sentences(text: str) -> list[str]:
    """「。」・箇条書き項目の境目・空行で区切った文を返す。"""
    parts = re.split(r"。|\n(?=\s*(?:[-*+]|\d+\.)\s)|\n\s*\n", text)
    return [part for part in parts if part.strip()]


def list_items_following(text: str, lead: str) -> str:
    """行頭が `lead` の行の後に続く箇条書き (空行を挟んでよい) を、次の空行の手前まで返す。"""
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(lead)), None
    )
    if start is None:
        return ""
    index = start + 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    end = index
    while end < len(lines) and lines[end].strip():
        end += 1
    return "\n".join(lines[index:end])


def table_cells(row: str) -> list[str]:
    """Markdown の表の行を、前後の `|` を除いてセルに分ける。"""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


Requirement = str | re.Pattern[str]


def satisfies(sentence: str, requirement: Requirement) -> bool:
    """空白を除去した `sentence` が、語を含むか正規表現に一致するか。"""
    stripped = strip_whitespace(sentence)
    if isinstance(requirement, str):
        return strip_whitespace(requirement) in stripped
    return requirement.search(stripped) is not None


def describe(requirement: Requirement) -> str:
    return requirement if isinstance(requirement, str) else requirement.pattern


class IssueClaimTestCase(unittest.TestCase):
    """`rule:issue-claim` の節を取り出す helper と、失敗時に該当箇所を示す assert。"""

    always_3_path: Path = ALWAYS_3
    issue_start_skill_path: Path = ISSUE_START_SKILL
    evaluation_doc_path: Path = EVALUATION_DOC

    def label(self, scope: str = "") -> str:
        base = f"{display_path(self.always_3_path)} の rule:issue-claim 節"
        return f"{base} の {scope}" if scope else base

    def issue_claim_block(self) -> str:
        block = rule_block(read(self.always_3_path), ISSUE_CLAIM_MARKER)
        self.assert_scope_found(self.label(), block, f"`{ISSUE_CLAIM_MARKER}` が無い")
        return block

    def assert_scope_found(self, label: str, scope: str, hint: str) -> None:
        if not scope.strip():
            self.fail(f"{label}: 検査対象の箇所が見つからない ({hint})")

    def assert_phrase_present(self, label: str, scope: str, phrase: str) -> None:
        if strip_whitespace(phrase) not in strip_whitespace(scope):
            self.fail(f"{label}: 「{phrase}」が無い")

    def assert_phrase_absent(self, label: str, scope: str, phrase: str) -> None:
        """空白を除去した全文で `phrase` の不在を確認し、残っていれば該当行を示す。"""
        if strip_whitespace(phrase) not in strip_whitespace(scope):
            return
        hits = [
            line.strip()[:120] for line in scope.splitlines() if phrase in line
        ]
        joined = " / ".join(hits) if hits else "改行をまたいで出現"
        self.fail(f"{label}: 「{phrase}」が残っている: {joined}")

    def assert_some_sentence(
        self, label: str, scope: str, requirements: tuple[Requirement, ...]
    ) -> None:
        """`requirements` (語または正規表現) をすべて満たす文が `scope` にあることを確認する。"""
        if any(
            all(satisfies(sentence, requirement) for requirement in requirements)
            for sentence in sentences(scope)
        ):
            return
        wanted = "」「".join(describe(requirement) for requirement in requirements)
        self.fail(f"{label}: 「{wanted}」をすべて満たす文が無い")

    def work_branch_step(self) -> str:
        """着手手順の step 6 (作業 branch の用意) の項目を、下位項目を含めて返す。"""
        section = markdown_section(self.issue_claim_block(), START_PROCEDURE_HEADING)
        item = top_level_list_item_containing(section, WORK_BRANCH_STEP_MARKER)
        self.assert_scope_found(
            self.label("step 6 の項目"),
            item,
            f"`{START_PROCEDURE_HEADING}` 節に「{WORK_BRANCH_STEP_MARKER}」で始まる項目が無い",
        )
        return item


class IssueClaimStartProcedureTest(IssueClaimTestCase):
    """着手手順の段階と順序。"""

    def start_procedure(self) -> str:
        section = markdown_section(self.issue_claim_block(), START_PROCEDURE_HEADING)
        self.assert_scope_found(
            self.label(START_PROCEDURE_HEADING),
            section,
            f"`{START_PROCEDURE_HEADING}` 節が無い",
        )
        return section

    def test_marker_appears_exactly_once(self) -> None:
        """`<!-- rule:issue-claim -->` マーカーが always-3.md に 1 つだけある。"""
        self.assertEqual(
            1,
            read(self.always_3_path).count(ISSUE_CLAIM_MARKER),
            f"{display_path(self.always_3_path)}: {ISSUE_CLAIM_MARKER} の出現回数",
        )

    def test_steps_appear_in_order(self) -> None:
        """早期判定 → claim comment の投稿 → 3 秒待機 → REST GET による再取得 →
        先着判定 → ラベル付与 → 作業 branch の作成、の順に書く。

        各段階を識別する語の、着手手順節での最初の出現位置で順序を比べる。
        """
        section = self.start_procedure()
        label = self.label(START_PROCEDURE_HEADING)
        positions = []
        for step, phrase in START_STEPS:
            with self.subTest(step=step):
                self.assert_phrase_present(label, section, phrase)
            positions.append((step, section.find(phrase)))
        for (earlier, earlier_at), (later, later_at) in zip(positions, positions[1:]):
            with self.subTest(earlier=earlier, later=later):
                # 語が改行で分かれていると存在の検査は通り、位置は取れない。順序を判定
                # できないまま skip すると誤った順序が見逃されるため、失敗させる。
                missing = [step for step, at in ((earlier, earlier_at), (later, later_at)) if at < 0]
                self.assertEqual(
                    [],
                    missing,
                    f"{label}: 段階を識別する語がそのままの形で無く、順序を判定できない",
                )
                self.assertLess(
                    earlier_at,
                    later_at,
                    f"{label}: 「{earlier}」が「{later}」より前に無い",
                )

    def test_early_check_withdraws_on_label_or_claim_comment(self) -> None:
        """早期判定は `ai:in-progress` ラベルか claim comment があれば撤退する。"""
        item = top_level_list_item_containing(self.start_procedure(), "早期判定")
        label = self.label("早期判定の項目")
        self.assert_scope_found(label, item, "「早期判定」を含む項目が無い")
        for phrase in ("ai:in-progress", "claim comment", "撤退"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, item, phrase)

    def test_work_branch_starts_from_the_latest_default_branch(self) -> None:
        """作業 branch は最新の default branch を起点に作る。中断した別 issue の branch に
        残る commit を、次の issue の branch が引き継がないようにするため。

        step 6 の項目は `6. **作業 branch` の行から下位項目までとする。
        """
        item = self.work_branch_step()
        label = self.label("step 6 の項目")
        for phrase in ("git fetch origin", "git switch -c", "origin/<default-branch>"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, item, phrase)

    def test_no_branch_push_confirmation_stage(self) -> None:
        """branch push で確保を確定する段階 (空 commit・即 push) と、branch 名
        uniqueness を排他基盤とする説明が無い。"""
        block = self.issue_claim_block()
        for phrase in BRANCH_PUSH_CONFIRMATION_PHRASES:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(self.label(), block, phrase)


class IssueClaimArbitrationTest(IssueClaimTestCase):
    """claim comment の書式と先着判定。"""

    def test_claim_comment_format_is_kept(self) -> None:
        """claim comment の書式 (`branch=` / `session=` / `ts=`) を変えない。"""
        self.assert_phrase_present(
            self.label(), self.issue_claim_block(), CLAIM_COMMENT_FORMAT
        )

    def test_first_claim_is_the_lexicographic_minimum(self) -> None:
        """`(created_at, 数値 id)` の辞書順最小の claim comment を先着とする。"""
        item = list_item_containing(self.issue_claim_block(), "辞書順最小")
        label = self.label("先着判定の項目")
        self.assert_scope_found(label, item, "「辞書順最小」を含む項目が無い")
        for phrase in ("(created_at, 数値 id)", "先着"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, item, phrase)

    def test_refetch_failure_stops_fail_closed(self) -> None:
        """REST GET の取得失敗と、取得結果に自分の claim が無い場合は停止してユーザーに
        報告する (fail-closed)。"""
        item = list_item_containing(self.issue_claim_block(), "fail-closed")
        label = self.label("fail-closed の項目")
        self.assert_scope_found(label, item, "「fail-closed」を含む項目が無い")
        for phrase in ("REST GET", "自分の claim", "停止", "報告"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, item, phrase)


class IssueClaimCleanupTest(IssueClaimTestCase):
    """撤退と着手中断の後片付け。"""

    def paragraph(self, label_text: str) -> str:
        paragraph = labeled_paragraph(self.issue_claim_block(), label_text)
        self.assert_scope_found(
            self.label(f"{label_text} の段落"),
            paragraph,
            f"行頭が `{label_text}` の段落が無い",
        )
        return paragraph

    def test_withdrawal_deletes_the_claim_comment_and_reports(self) -> None:
        """撤退の後片付けは自分の claim comment の削除とユーザーへの 1 行報告。"""
        paragraph = self.paragraph(WITHDRAWAL_LABEL)
        label = self.label(f"{WITHDRAWAL_LABEL} の段落")
        for phrase in ("claim comment", "削除", "1 行"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, paragraph, phrase)

    def test_block_has_no_branch_deletion(self) -> None:
        """撤退・着手中断のどちらの後片付けでも branch を削除しない。確保の判定に
        branch を使わないため、削除する理由が無い。"""
        block = self.issue_claim_block()
        for phrase in BRANCH_DELETE_COMMANDS:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(self.label(), block, phrase)

    def test_interruption_deletes_only_the_claim_comment(self) -> None:
        """着手中断の後片付けは自分の claim comment の削除だけで、branch・draft PR・
        ラベルは残す。"""
        paragraph = self.paragraph(INTERRUPTION_LABEL)
        label = self.label(f"{INTERRUPTION_LABEL} の段落")
        for phrase in ("claim comment", "削除", *INTERRUPTION_KEPT_ARTIFACTS):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, paragraph, phrase)


class IssueClaimDeletionDisciplineTest(IssueClaimTestCase):
    """他 session の成果物を消さない規律と自他判別。"""

    def test_other_sessions_artifacts_are_never_deleted(self) -> None:
        """他 session の claim comment / branch / ラベルを削除しない。"""
        item = list_item_containing(self.issue_claim_block(), OTHER_SESSION_TARGETS)
        label = self.label("他 session の削除禁止の項目")
        self.assert_scope_found(label, item, f"「{OTHER_SESSION_TARGETS}」を含む項目が無い")
        self.assert_phrase_present(label, item, "削除しない")

    def test_ownership_is_judged_by_session_id(self) -> None:
        """「自分の claim か」は claim comment の `session=` 値と自分のセッション ID の
        一致で判別し、一致しない・`session=` が無い claim は削除しない。"""
        item = top_level_list_item_containing(
            self.issue_claim_block(), OWNERSHIP_CRITERION
        )
        label = self.label("自他判別の項目")
        self.assert_scope_found(label, item, f"「{OWNERSHIP_CRITERION}」を含む項目が無い")
        for phrase in ("session=", "セッション ID と一致", "削除禁止"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, item, phrase)

    def test_cleanup_discipline_keeps_branches_and_labels(self) -> None:
        """削除規律は branch を削除対象に含めず、撤退・着手中断のどちらでもラベルを
        削除しない。"""
        block = self.issue_claim_block()
        self.assert_phrase_absent(self.label(), block, MERGED_CLEANUP_PHRASE)
        self.assert_phrase_present(self.label(), block, LABEL_KEPT_PHRASE)

    def test_pitfalls_do_not_reference_removed_steps(self) -> None:
        """「よくある誤操作と回避」などが、確定段階を含んでいた手順番号を参照しない。"""
        block = self.issue_claim_block()
        for phrase in STALE_STEP_REFERENCES:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(self.label(), block, phrase)

    def test_visibility_is_written_as_a_premise(self) -> None:
        """他 session の claim が一覧に反映されることを保証として書かない。"""
        self.assert_phrase_absent(
            self.label(), self.issue_claim_block(), VISIBILITY_GUARANTEE_PHRASE
        )


class IssueClaimExplicitResumeTest(IssueClaimTestCase):
    """明示指示による再開の規定。"""

    def explicit_resume_paragraph(self) -> str:
        paragraph = paragraph_containing(
            self.issue_claim_block(), EXPLICIT_DEFINITION_MARKERS
        )
        self.assert_scope_found(
            self.label("明示指示の段落"),
            paragraph,
            "「明示指示」と「handoff」を含む段落が無い",
        )
        return paragraph

    def test_explicit_instruction_is_defined(self) -> None:
        """明示指示を、ユーザのメッセージまたは handoff の文書が issue 番号か branch 名を
        挙げて継続を指示していること、と定義する。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            (EXPLICIT_INSTRUCTION, *EXPLICIT_DEFINITION_PHRASES),
        )

    def test_explicit_instruction_resumes_from_step_6(self) -> None:
        """明示指示がある場合は、claim の手順を経ずに step 6 から再開する。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            ("step 6", "再開"),
        )

    def test_explicit_instruction_neither_withdraws_nor_deletes(self) -> None:
        """明示指示による再開では、ラベルや他 session の claim comment が残っていても撤退
        せず、それらを削除もしない。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            (
                "ラベル",
                "claim comment",
                OTHER_SESSION,
                re.compile(r"撤退(?:せず|しない)"),
                re.compile(r"削除(?:も)?(?:せず|しない)"),
            ),
        )

    def test_without_explicit_instruction_starts_from_step_1(self) -> None:
        """明示指示が無ければ、既存の branch / draft PR があっても step 1 から実行する。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            (NO_EXPLICIT_INSTRUCTION, "branch", "step 1"),
        )

    def test_explicit_instruction_without_branch_starts_from_step_1(self) -> None:
        """明示指示があっても対応する branch が無ければ、step 1 から実行する。branch を
        新しく作るには claim を要するため。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            (
                EXPLICIT_INSTRUCTION,
                re.compile(r"branchが(?:無|な|存在しな)"),
                "step 1",
            ),
        )


class IssueClaimWorkBranchTest(IssueClaimTestCase):
    """step 6 の作業 branch の用意と draft PR の順序。"""

    def assert_step_sentence(self, requirements: tuple[Requirement, ...]) -> None:
        self.assert_some_sentence(
            self.label("step 6 の項目"), self.work_branch_step(), requirements
        )

    def test_fetch_failure_stops_and_reports(self) -> None:
        """`git fetch origin` が失敗したら、remote の状態を判定できないので停止して
        ユーザに報告する。"""
        self.assert_step_sentence(("git fetch origin", "失敗", "停止", "報告"))

    def test_missing_branch_is_created_from_the_default_branch(self) -> None:
        """同名の branch がどこにも無ければ、最新の default branch を起点に作る。"""
        self.assert_step_sentence(
            (
                re.compile(r"無い|無け|ない:|存在しない"),
                "git switch -c",
                "--no-track",
                "origin/<default-branch>",
            )
        )

    def test_remote_only_branch_is_switched(self) -> None:
        """同名の branch が remote だけにあれば、`git switch <branch>` で再開する。"""
        self.assert_step_sentence(("remote だけ", "git switch <branch>"))

    def test_local_branch_is_switched(self) -> None:
        """同名の branch が local にあれば、`git switch <branch>` で再開する。"""
        self.assert_step_sentence(("local にある", "git switch <branch>"))

    def test_older_local_branch_is_fast_forwarded(self) -> None:
        """local の branch が remote より古ければ、fast-forward で追いつく。"""
        self.assert_step_sentence(("local が古", "git merge --ff-only"))

    def test_newer_local_branch_is_kept(self) -> None:
        """local の branch が remote より新しければ、未 push の commit を保ったまま再開する。"""
        self.assert_step_sentence(("local が新し",))

    def test_diverged_branch_stops_without_changes(self) -> None:
        """local と remote が分岐していれば、どちらも変更せずに停止してユーザに報告する。"""
        self.assert_step_sentence(
            (
                "分岐",
                "local",
                "remote",
                re.compile(r"変更(?:せず|しない)"),
                "停止",
                "報告",
            )
        )

    def test_step_has_no_branch_rewriting_commands(self) -> None:
        """step 6 に local / remote の branch を書き換える・消すコマンドを置かない。"""
        item = self.work_branch_step()
        for phrase in DIVERGENCE_FORBIDDEN_COMMANDS:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(self.label("step 6 の項目"), item, phrase)

    def test_draft_pr_follows_tdd_two_phase(self) -> None:
        """draft PR は `rule:tdd-two-phase` に合わせ、Phase A の commit を push した後に
        作る。既存の draft PR があればそれを使う。"""
        item = self.work_branch_step()
        self.assert_phrase_present(self.label("step 6 の項目"), item, "rule:tdd-two-phase")
        self.assert_step_sentence((re.compile(r"PhaseA.*push.*後.*draftPR"),))
        self.assert_step_sentence(("既存", "draft PR"))

    def test_draft_pr_first_order_is_absent(self) -> None:
        """draft PR を実装より先に作る旧い順序 (「draft PR 作成 → 実装」) を書かない。"""
        self.assert_phrase_absent(
            self.label(), self.issue_claim_block(), DRAFT_PR_FIRST_PHRASE
        )


class IssueClaimRelatedDocumentTest(IssueClaimTestCase):
    """rule:issue-claim の手順を要約する skill・README。"""

    def test_issue_start_skill_has_no_branch_push_confirmation(self) -> None:
        """issue-start skill の排他制御の説明に「branch push による確定」が無い。"""
        text = read(self.issue_start_skill_path)
        label = display_path(self.issue_start_skill_path)
        self.assert_phrase_present(label, text, "rule:issue-claim")
        self.assert_phrase_absent(label, text, SKILL_BRANCH_PUSH_PHRASE)

    def test_readmes_do_not_describe_branch_push_exclusion(self) -> None:
        """plugin README と、ルート README の agent-discipline 節が、branch push を
        排他の仕組みとして説明しない。"""
        repo_section = markdown_section(read(REPO_README), REPO_README_PLUGIN_HEADING)
        repo_label = f"{display_path(REPO_README)} の {REPO_README_PLUGIN_HEADING} 節"
        self.assert_scope_found(
            repo_label, repo_section, f"`{REPO_README_PLUGIN_HEADING}` 節が無い"
        )
        scopes = (
            (display_path(PLUGIN_README), read(PLUGIN_README)),
            (repo_label, repo_section),
        )
        for label, scope in scopes:
            for phrase in README_BRANCH_PUSH_PHRASES:
                with self.subTest(file=label, phrase=phrase):
                    self.assert_phrase_absent(label, scope, phrase)


class IssueStartPickUpTest(IssueClaimTestCase):
    """issue-start skill の pick-up 分岐と排他制御の参照。"""

    def skill_label(self, heading: str) -> str:
        return f"{display_path(self.issue_start_skill_path)} の {heading} 節"

    def skill_section(self, heading: str) -> str:
        section = markdown_section(read(self.issue_start_skill_path), heading)
        self.assert_scope_found(
            self.skill_label(heading), section, f"`{heading}` 節が無い"
        )
        return section

    def pick_up_items(self) -> list[str]:
        items = top_level_list_items(self.skill_section(ISSUE_START_PICK_UP_HEADING))
        if not items:
            self.fail(f"{self.skill_label(ISSUE_START_PICK_UP_HEADING)}: 分岐の箇条書きが無い")
        return items

    def test_explicit_instruction_branch_resumes_from_the_unfinished_phase(self) -> None:
        """明示指示がある場合の分岐は、Phase A が完了済みなら Phase B から、未完了なら
        Phase A から再開する。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        explicit_items = [
            item
            for item in self.pick_up_items()
            if satisfies(item, EXPLICIT_INSTRUCTION)
            and not satisfies(item, NO_EXPLICIT_INSTRUCTION)
        ]
        requirements: tuple[Requirement, ...] = (
            re.compile(r"完了済み.*PhaseB"),
            re.compile(r"未完了.*PhaseAから"),
        )
        if not any(
            all(satisfies(item, requirement) for requirement in requirements)
            for item in explicit_items
        ):
            self.fail(
                f"{label}: 明示指示がある場合の分岐に、完了済みなら Phase B から・未完了なら"
                f" Phase A から再開する記述が無い (明示指示がある場合の項目: {len(explicit_items)} 件)"
            )

    def test_no_explicit_instruction_branch_goes_to_the_claim(self) -> None:
        """明示指示が無い場合の分岐は、既存の branch / PR の有無に依らずセクション 2 の
        排他制御に進む。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        requirements: tuple[Requirement, ...] = (
            NO_EXPLICIT_INSTRUCTION,
            "branch",
            re.compile(r"有無に(?:依|よ)らず|あっても"),
            "セクション 2",
            "排他制御",
        )
        if not any(
            all(satisfies(item, requirement) for requirement in requirements)
            for item in self.pick_up_items()
        ):
            wanted = "」「".join(describe(requirement) for requirement in requirements)
            self.fail(f"{label}: 「{wanted}」をすべて満たす分岐が無い")

    def test_every_branch_depends_on_explicit_instruction(self) -> None:
        """pick-up 分岐のどの項目も明示指示の有無を条件にし、既存の branch / PR だけを
        条件に Phase B から再開する分岐が無い。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        self.assert_phrase_absent(
            label,
            self.skill_section(ISSUE_START_PICK_UP_HEADING),
            UNCONDITIONAL_RESUME_PHRASE,
        )
        for item in self.pick_up_items():
            with self.subTest(item=item.splitlines()[0][:60]):
                self.assert_phrase_present(label, item, EXPLICIT_INSTRUCTION)

    def test_claim_reference_is_not_limited_to_new_starts(self) -> None:
        """排他制御の参照が、排他制御を新規着手の場合だけに限らない (既存の branch / PR が
        あっても、明示指示が無ければ排他制御を実行するため)。"""
        self.assert_phrase_absent(
            self.skill_label(ISSUE_START_CLAIM_HEADING),
            self.skill_section(ISSUE_START_CLAIM_HEADING),
            NEW_START_ONLY_PHRASE,
        )


class IssueClaimEvaluationTest(IssueClaimTestCase):
    """`docs/discipline-evaluation.md` の issue-claim の評価基準。"""

    def test_explicit_resume_is_a_pass(self) -> None:
        """明示指示による再開で claim を経ないことを、表の Pass 列か表下の経路別 Pass
        定義のどちらかで Pass として扱う。"""
        text = read(self.evaluation_doc_path)
        label = f"{display_path(self.evaluation_doc_path)} の issue-claim 手順の遵守"
        row = next(
            (line for line in text.splitlines() if line.startswith(EVALUATION_ISSUE_CLAIM_ROW)),
            "",
        )
        self.assert_scope_found(label, row, f"「{EVALUATION_ISSUE_CLAIM_ROW}」の行が無い")
        cells = table_cells(row)
        pass_cell = cells[EVALUATION_PASS_COLUMN] if len(cells) > EVALUATION_PASS_COLUMN else ""
        notes = list_items_following(text, EVALUATION_ISSUE_CLAIM_NOTE)
        in_pass_cell = satisfies(pass_cell, EXPLICIT_INSTRUCTION)
        in_notes = any(
            satisfies(item, EXPLICIT_INSTRUCTION) and satisfies(item, "再開")
            for item in top_level_list_items(notes)
        )
        if not (in_pass_cell or in_notes):
            self.fail(
                f"{label}: 明示指示による再開が、表の Pass 列にも「{EVALUATION_ISSUE_CLAIM_NOTE}」"
                "の箇条書きにも無い"
            )


if __name__ == "__main__":
    unittest.main()

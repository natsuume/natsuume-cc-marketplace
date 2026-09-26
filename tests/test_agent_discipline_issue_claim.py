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
  fail-closed 停止。
- 後片付け (``IssueClaimCleanupTest``): 撤退は claim comment の削除とユーザーへの 1 行報告
  だけで branch を操作しない。着手中断で自分の branch を削除するときは、remote 削除 →
  default branch への switch → local 削除の順に独立した Bash 呼び出しで実行する。
- 削除規律 (``IssueClaimDeletionDisciplineTest``): 他 session の claim comment / branch /
  ラベルを削除しない規律と、`session=` による自他判別。
- 関連文書 (``IssueClaimRelatedDocumentTest``): issue-start skill と README が branch push
  による確定・二段構成を説明しない。

文章全体の一致は検査しない。手順を識別するコマンド・識別子の有無と出現順序を検査し、
言い回しは実装側で選べる。always-3.md のパスは ``IssueClaimTestCase.always_3_path`` で
差し替えられる (置換予定の本文を一時ファイルで検査するため)。
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

REMOTE_BRANCH_DELETE = "git push origin --delete"
DEFAULT_BRANCH_SWITCH = "git switch"
LOCAL_BRANCH_DELETE = "git branch -D"
BRANCH_DELETE_COMMANDS = (REMOTE_BRANCH_DELETE, LOCAL_BRANCH_DELETE)

# 撤退と着手中断を区別せず「claim comment と branch を削除する」とした削除規律の語。
MERGED_CLEANUP_PHRASE = "自分の claim comment と branch のみ削除"

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


def occurrence_offsets(text: str, phrase: str) -> list[int]:
    """`text` 中の `phrase` の出現位置 (先頭からの文字数) をすべて返す。"""
    offsets = []
    start = text.find(phrase)
    while start >= 0:
        offsets.append(start)
        start = text.find(phrase, start + 1)
    return offsets


class IssueClaimTestCase(unittest.TestCase):
    """`rule:issue-claim` の節を取り出す helper と、失敗時に該当箇所を示す assert。"""

    always_3_path: Path = ALWAYS_3

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
        """REST GET の取得失敗と、取得結果に自分の claim が無い場合は停止する
        (fail-closed)。"""
        item = list_item_containing(self.issue_claim_block(), "fail-closed")
        label = self.label("fail-closed の項目")
        self.assert_scope_found(label, item, "「fail-closed」を含む項目が無い")
        for phrase in ("REST GET", "自分の claim", "停止"):
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

    def test_withdrawal_does_not_delete_branches(self) -> None:
        """撤退時点では branch を作っていないので、撤退の手順に branch 削除が無い。"""
        paragraph = self.paragraph(WITHDRAWAL_LABEL)
        label = self.label(f"{WITHDRAWAL_LABEL} の段落")
        for phrase in BRANCH_DELETE_COMMANDS:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(label, paragraph, phrase)

    def test_branch_deletion_appears_only_in_interruption(self) -> None:
        """節の中の branch 削除コマンドは、すべて着手中断の段落にある。"""
        block = self.issue_claim_block()
        interruption = self.paragraph(INTERRUPTION_LABEL)
        interruption_start = block.find(interruption)
        interruption_end = interruption_start + len(interruption)
        for phrase in BRANCH_DELETE_COMMANDS:
            for offset in occurrence_offsets(block, phrase):
                with self.subTest(phrase=phrase, offset=offset):
                    line = block[: offset].count("\n") + 1
                    self.assertTrue(
                        interruption_start <= offset < interruption_end,
                        f"{self.label()}: 「{phrase}」が着手中断の段落の外 "
                        f"(節の {line} 行目) にある",
                    )

    def test_interruption_deletes_remote_then_switches_then_deletes_local(
        self,
    ) -> None:
        """着手中断で自分の branch を削除するときは、remote 削除 → default branch への
        switch → local 削除の順に、独立した Bash 呼び出しで実行する。"""
        paragraph = self.paragraph(INTERRUPTION_LABEL)
        label = self.label(f"{INTERRUPTION_LABEL} の段落")
        for phrase in (
            "claim comment",
            "default branch",
            "独立した Bash 呼び出し",
            "前段が失敗したら後段に進まない",
        ):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, paragraph, phrase)
        order = (REMOTE_BRANCH_DELETE, DEFAULT_BRANCH_SWITCH, LOCAL_BRANCH_DELETE)
        for phrase in order:
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, paragraph, phrase)
        positions = [paragraph.find(phrase) for phrase in order]
        if min(positions) < 0:
            self.fail(f"{label}: branch 削除のコマンドが揃っていない")
        self.assertEqual(
            sorted(positions),
            positions,
            f"{label}: {' → '.join(order)} の順に書かれていない",
        )
        self.assertNotIn("&&", paragraph, f"{label}: コマンドを `&&` で連結している")


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

    def test_withdrawal_and_interruption_are_not_merged(self) -> None:
        """削除規律が撤退と着手中断をまとめて「claim comment と branch を削除する」と
        書かない (撤退時は branch を削除しない)。"""
        self.assert_phrase_absent(
            self.label(), self.issue_claim_block(), MERGED_CLEANUP_PHRASE
        )


class IssueClaimRelatedDocumentTest(IssueClaimTestCase):
    """rule:issue-claim の手順を要約する skill・README。"""

    def test_issue_start_skill_has_no_branch_push_confirmation(self) -> None:
        """issue-start skill の排他制御の説明に「branch push による確定」が無い。"""
        text = read(ISSUE_START_SKILL)
        label = display_path(ISSUE_START_SKILL)
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


if __name__ == "__main__":
    unittest.main()

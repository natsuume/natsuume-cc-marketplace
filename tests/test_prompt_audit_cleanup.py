"""plugin の prompt・文書に、旧い書き方と古くなった記述を置かないことの契約テスト。

旧い書き方とは、数値による出力上限、再検証の抑止文、思考量を文章で指示する文、
`tools` に無いツールを禁じる文、旧ツール名 (Task) を指す。古くなった記述とは、
固定のモデル名、検証日の無い外部仕様の記述、実在しない名前への参照を指す。

文章全体の一致は検査しない。規則を識別する語の組み合わせを、節・段落・文・箇条書き
項目の単位で検査し、言い回しは実装側で選べる。語の照合は、行の折り返しで分断された
出現も拾うため、原則として空白を除去した文字列で行う。

各テストクラスが検査する規則:

- ``ReviewerConstraintsTest`` (pre-push-review の reviewer 2 体): `tools` に無い
  `Agent` / `Skill` を禁じる文と「No tool use」が無く、Constraints 節の終了の指示が
  report 契約 (SubagentHandback による報告を含む) どおりに報告だけを返して終了する形である。
- ``CodexReviewerConstraintsTest`` (pre-merge-cross-review と pre-push-codex-review の
  codex-reviewer): 旧ツール名 Task を書かず、Constraints 節の終了の指示が reviewer 2 体と
  同じ形である。
- ``AgentToolNameTest`` (pre-push-review 全体と pre-push-codex-review の review command):
  旧ツール名 Task を書かない。
- ``CrossModelAdvisorOutputLengthTest`` (cross-model-advisor): 助言と不足の報告に語数・
  行数の上限を置かず、定性的な長さの指示を置く。advisor-rules は相談回数の目安を持たない。
  README の既知の制約は、ユーザが rescue の本文を直接指定した場合の扱いを、外部 plugin の
  rescue.md ではなく routing flag の規定と codex-rescue-runner を根拠に説明する。
- ``LeadtimeSummaryTest`` (repo-analytics): ターミナルサマリの結論に文数の上限を置かず、
  主指標の推移を冒頭で要約する。
- ``AgentDisciplinePromptTest`` (agent-discipline の注入文): auto-mode は「導入済みの他
  プラグイン」を参照する。always-2 は issue body を契約書とみなす文を太字・「絶対に」なしの
  平叙で書き、節番号 3.1 を保つ。plan ファイルの置き場所を固定しない。closing keyword の
  節は作者の規約 3 点・適用範囲・例だけを書き、GitHub の一般知識を書かない。always-3 の
  3 秒待機は、harness が foreground の sleep を拒否する場合に background 実行で待つ手順を
  持つ。
- ``AgentDisciplineDocumentTest`` (agent-discipline の skill・README・暫定ルール):
  issue-start は closing keyword の詳細として有効なキーワード一覧を挙げない。issue-plan の
  GitHub 仕様の記述 2 箇所に確認日がある。README は always-3 の思考量の文を説明しない。
  暫定ルールの先頭コメントに確認方法と確認した Claude Code のバージョンがある。
- ``UiDisciplineAvoidDefaultsTest`` (ui-discipline): 「避ける既定」の一覧が ui-rules.md の
  rule 10 と ui-patterns skill で同じ文言であり、指定の既定スタイルを含む。skill の視覚方向の
  例示はこれらの既定に該当しない。注入文は SessionStart (ui-rules.md 単体) と SubagentStart
  (前置き注記との連結後) の両方で 8,000 字以内に収まる。
- ``CodexStatusModelNameTest`` (rate-limit): 独立枠の説明に固定のモデル名を書かない。
- ``RebaseWorkflowTest`` (git-guardrails): コンフリクト解消を 1 文で書き、戦略助言を
  持たず、デフォルトブランチ名は `git symbolic-ref` を単独で実行して読み取りで得る。
- ``StatuslineCommandBugNoteTest`` (natsuume-statusline / rate-limit / session-handoff /
  リポジトリ直下 README): Claude Code の bug #52079 の記述は、現在の不具合・撤去条件・
  確認方法の 3 要素で書く。
- ``ReviewCadenceRulesTest`` (pre-push-codex-review): 境界段落は同じ趣旨を 1 回だけ述べ、
  注入先に無い「本 script」と hook 内部の判定キーを書かず、挙動の規定を保つ。consult skill
  は実在する節名で参照する。
- ``ReviewCommandParallelWordingTest`` (pre-push-review / pre-push-codex-review の review
  command): 冒頭の説明は、並列発出を唯一の正解とせず、並列発出が成立しない場合の節と
  整合する。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

# pre-push-review
PRE_PUSH_REVIEW = PLUGINS / "pre-push-review"
CODE_REVIEWER = PRE_PUSH_REVIEW / "agents" / "code-reviewer.md"
SECURITY_REVIEWER = PRE_PUSH_REVIEW / "agents" / "security-reviewer.md"
PRE_PUSH_REVIEW_COMMAND = PRE_PUSH_REVIEW / "commands" / "review.md"

# pre-push-codex-review
PRE_PUSH_CODEX_REVIEW = PLUGINS / "pre-push-codex-review"
PRE_PUSH_CODEX_REVIEWER = PRE_PUSH_CODEX_REVIEW / "agents" / "codex-reviewer.md"
PRE_PUSH_CODEX_REVIEW_COMMAND = PRE_PUSH_CODEX_REVIEW / "commands" / "review.md"
PRE_PUSH_CODEX_README = PRE_PUSH_CODEX_REVIEW / "README.md"
REVIEW_CADENCE_RULES = (
    PRE_PUSH_CODEX_REVIEW / "hooks" / "prompts" / "review-cadence-rules.md"
)

# pre-merge-cross-review
PRE_MERGE_CODEX_REVIEWER = (
    PLUGINS / "pre-merge-cross-review" / "agents" / "codex-reviewer.md"
)

# cross-model-advisor
CROSS_MODEL_ADVISOR = PLUGINS / "cross-model-advisor"
FABLE_ADVISOR_RUNNER = CROSS_MODEL_ADVISOR / "agents" / "fable-advisor-runner.md"
CONSULT_SKILL = CROSS_MODEL_ADVISOR / "skills" / "consult" / "SKILL.md"
ADVISOR_RULES = CROSS_MODEL_ADVISOR / "hooks" / "prompts" / "advisor-rules.md"
CROSS_MODEL_ADVISOR_README = CROSS_MODEL_ADVISOR / "README.md"

# repo-analytics
LEADTIME_SKILL = PLUGINS / "repo-analytics" / "skills" / "leadtime" / "SKILL.md"

# agent-discipline
AGENT_DISCIPLINE = PLUGINS / "agent-discipline"
AGENT_DISCIPLINE_PROMPTS = AGENT_DISCIPLINE / "hooks" / "prompts"
ALWAYS_2 = AGENT_DISCIPLINE_PROMPTS / "always-2.md"
ALWAYS_3 = AGENT_DISCIPLINE_PROMPTS / "always-3.md"
AUTO_MODE = AGENT_DISCIPLINE_PROMPTS / "auto-mode.md"
PREVIEW_WORKAROUND = (
    AGENT_DISCIPLINE_PROMPTS / "temporary" / "askuserquestion-preview-workaround.md"
)
ISSUE_START_SKILL = AGENT_DISCIPLINE / "skills" / "issue-start" / "SKILL.md"
ISSUE_PLAN_SKILL = AGENT_DISCIPLINE / "skills" / "issue-plan" / "SKILL.md"
AGENT_DISCIPLINE_README = AGENT_DISCIPLINE / "README.md"

# ui-discipline
UI_DISCIPLINE = PLUGINS / "ui-discipline"
UI_RULES = UI_DISCIPLINE / "hooks" / "prompts" / "ui-rules.md"
UI_SUBAGENT_INJECTOR = UI_DISCIPLINE / "hooks" / "scripts" / "inject-ui-rules-subagent.sh"
UI_PATTERNS_SKILL = UI_DISCIPLINE / "skills" / "ui-patterns" / "SKILL.md"

# rate-limit
CODEX_STATUS_SKILL = PLUGINS / "rate-limit" / "skills" / "codex-status" / "SKILL.md"

# git-guardrails
REBASE_SKILL = PLUGINS / "git-guardrails" / "skills" / "rebase-workflow" / "SKILL.md"

# bug #52079 の記述がある文書 (Markdown) と shell script。
STATUSLINE_BUG_NOTE_DOCUMENTS = (
    PLUGINS / "natsuume-statusline" / "commands" / "setup.md",
    PLUGINS / "rate-limit" / "commands" / "setup.md",
    ROOT / "README.md",
    PLUGINS / "natsuume-statusline" / "README.md",
)
STATUSLINE_BUG_NOTE_SCRIPTS = (
    PLUGINS / "natsuume-statusline" / "scripts" / "setup.sh",
    PLUGINS / "rate-limit" / "scripts" / "setup.sh",
    PLUGINS / "rate-limit" / "statusline" / "cache-write-wrapper.sh",
    PLUGINS / "session-handoff" / "scripts" / "setup-wrapper.sh",
)
STATUSLINE_BUG_NUMBER = "52079"

RULE_MARKER_PREFIX = "<!-- rule:"
HEADING_PATTERN = re.compile(r"^#{1,6} ")
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+\.) ")
TOP_LEVEL_LIST_ITEM_PATTERN = re.compile(r"^(?:[-*+]|\d+\.) ")
FENCE_PATTERN = re.compile(r"^\s*(?:```|~~~)")

# 旧ツール名 Task (TaskOutput 等の別名のツールには一致しない)。
TASK_TOOL_NAME = re.compile(r"\bTask\b")

# 確認日の表記 (例: 2026-09-26 / 2026年9月26日)。
CONFIRMATION_DATE = re.compile(r"\d{4}(?:-\d{1,2}-\d{1,2}|年\d{1,2}月\d{1,2}日)")

Requirement = str | re.Pattern[str]


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


def satisfies(text: str, requirement: Requirement) -> bool:
    """空白を除去した `text` が、語を含むか正規表現に一致するか。

    語は大文字小文字を区別して照合する。正規表現は空白を除去した文字列に照合する。
    """
    stripped = strip_whitespace(text)
    if isinstance(requirement, str):
        return strip_whitespace(requirement) in stripped
    return requirement.search(stripped) is not None


def describe(requirement: Requirement) -> str:
    return requirement if isinstance(requirement, str) else requirement.pattern


def rule_block(text: str, rule_id: str) -> str:
    """`<!-- rule:<rule_id> -->` から次の `<!-- rule:` マーカーの手前までを返す。"""
    marker = f"{RULE_MARKER_PREFIX}{rule_id} -->"
    start = text.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    end = text.find(RULE_MARKER_PREFIX, body_start)
    return text[body_start:] if end < 0 else text[body_start:end]


def markdown_section(text: str, heading: str) -> str:
    """`heading` で始まる見出し行の次の行から、同じか上位レベルの次の見出しの手前までを返す。

    fence 内の `#` 始まりの行は見出しとして扱わない。
    """
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines(keepends=True)
    start: int | None = None
    in_fence = False
    for index, line in enumerate(lines):
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.rstrip("\n")
        if start is None:
            if stripped.startswith(heading):
                start = index
            continue
        if HEADING_PATTERN.match(stripped):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level:
                return "".join(lines[start + 1 : index])
    if start is None:
        return ""
    return "".join(lines[start + 1 :])


def enclosing_section(text: str, position: int) -> str:
    """`position` を含む節 (直前の見出しの次の行から、次の見出しの手前まで) を返す。

    見出しのレベルは問わない。fence 内の `#` 始まりの行は見出しとして扱わない。
    """
    lines = text.splitlines(keepends=True)
    offset = 0
    start = 0
    end = len(lines)
    in_fence = False
    target_line: int | None = None
    for index, line in enumerate(lines):
        if target_line is None and offset + len(line) > position:
            target_line = index
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
        elif not in_fence and HEADING_PATTERN.match(line):
            if target_line is None:
                start = index + 1
            elif index > target_line:
                end = index
                break
        offset += len(line)
    return "".join(lines[start:end])


def paragraphs(text: str) -> list[str]:
    """空行で区切った段落 (空でないもの) の列を返す。"""
    return [block for block in re.split(r"\n\s*\n", text) if block.strip()]


def japanese_sentences(text: str) -> list[str]:
    """「。」・箇条書き項目の境目・空行で区切った文を返す。"""
    parts = re.split(r"。|\n(?=\s*(?:[-*+]|\d+\.)\s)|\n\s*\n", text)
    return [part for part in parts if part.strip()]


def list_items(text: str) -> list[str]:
    """箇条書き項目 (次の項目・空行・見出しの手前までの継続行を含む) の列を返す。"""
    items: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if LIST_ITEM_PATTERN.match(line):
            if current is not None:
                items.append("\n".join(current))
            current = [line]
        elif current is not None and line.strip() and not HEADING_PATTERN.match(line):
            current.append(line)
        elif current is not None:
            items.append("\n".join(current))
            current = None
    if current is not None:
        items.append("\n".join(current))
    return items


def top_level_list_item_containing(text: str, marker: str) -> str:
    """`marker` を含むトップレベルの箇条書き項目を、インデントされた下位項目を含めて返す。"""
    lines = text.splitlines()
    target = next((index for index, line in enumerate(lines) if marker in line), None)
    if target is None:
        return ""
    start = target
    while start > 0 and not TOP_LEVEL_LIST_ITEM_PATTERN.match(lines[start]):
        start -= 1
    end = target + 1
    while end < len(lines):
        line = lines[end]
        if TOP_LEVEL_LIST_ITEM_PATTERN.match(line) or HEADING_PATTERN.match(line):
            break
        if not line.strip():
            following = next(
                (later for later in lines[end + 1 :] if later.strip()), ""
            )
            if not following[:1].isspace():
                break
        end += 1
    return "\n".join(lines[start:end])


def header_comment(text: str) -> str:
    """ファイル先頭の HTML コメント (`<!--` 〜 最初の `-->`) を返す (無ければ空文字)。"""
    if not text.startswith("<!--"):
        return ""
    end = text.find("-->")
    return text[: end + len("-->")] if end >= 0 else ""


def shell_comment_text(text: str) -> str:
    """shell script のコメント (`#` 行と、`printf` が書き出す `#` 行) を連結して返す。

    shebang 行は含めない。setup script が生成するファイルに書き出すコメントも、同じ
    script に書かれた説明として扱う。
    """
    comments: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#!"):
            continue
        if stripped.startswith("#"):
            comments.append(stripped.lstrip("#").strip())
            continue
        for match in re.finditer(r"printf\s+'#([^']*)'", stripped):
            comments.append(match.group(1).replace("\\n", "").strip())
    return "\n".join(comments)


def utf16_length(text: str) -> int:
    """UTF-16 code unit 数 (Claude Code の注入上限の計測単位) を返す。"""
    return len(text.encode("utf-16-le")) // 2


class ContractTestCase(unittest.TestCase):
    """失敗時に、どのファイルのどの規則が満たされないかを示す assert。"""

    def assert_scope_found(self, label: str, scope: str, hint: str) -> None:
        if not scope.strip():
            self.fail(f"{label}: 検査対象の箇所が見つからない ({hint})")

    def assert_phrase_absent(self, label: str, scope: str, phrase: str) -> None:
        """空白を除去した `scope` で `phrase` の不在を確認し、残っていれば該当行を示す。"""
        if strip_whitespace(phrase) not in strip_whitespace(scope):
            return
        hits = [line.strip()[:120] for line in scope.splitlines() if phrase in line]
        joined = " / ".join(hits) if hits else "改行をまたいで出現"
        self.fail(f"{label}: 「{phrase}」が残っている: {joined}")

    def assert_phrase_present(self, label: str, scope: str, phrase: str) -> None:
        if strip_whitespace(phrase) not in strip_whitespace(scope):
            self.fail(f"{label}: 「{phrase}」が無い")

    def assert_pattern_absent(
        self, label: str, scope: str, pattern: re.Pattern[str], meaning: str
    ) -> None:
        hits = [
            line.strip()[:120] for line in scope.splitlines() if pattern.search(line)
        ]
        if hits:
            self.fail(f"{label}: {meaning}が残っている: {' / '.join(hits)}")

    def assert_requirements(
        self, label: str, scope: str, requirements: tuple[Requirement, ...], rule: str
    ) -> None:
        """`scope` が `requirements` (語または正規表現) をすべて満たすことを確認する。"""
        missing = [describe(item) for item in requirements if not satisfies(scope, item)]
        if missing:
            self.fail(f"{label}: {rule} の要素が無い: 「{'」「'.join(missing)}」")

    def assert_some_unit(
        self,
        label: str,
        units: list[str],
        requirements: tuple[Requirement, ...],
        rule: str,
    ) -> None:
        """`requirements` をすべて満たす単位 (文・段落・項目) が `units` にあることを確認する。"""
        if any(all(satisfies(unit, item) for item in requirements) for unit in units):
            return
        wanted = "」「".join(describe(item) for item in requirements)
        self.fail(f"{label}: {rule} (「{wanted}」をすべて含む箇所) が無い")


class ReviewerConstraintsTest(ContractTestCase):
    """pre-push-review の reviewer 2 体の Constraints 節。"""

    REVIEWERS = (CODE_REVIEWER, SECURITY_REVIEWER)

    # `tools` に無いツールを禁じる文と、終了の指示にあった「No tool use」。
    RETIRED_PHRASES = (
        "Do not spawn subagents",
        "Do not invoke `/",
        "No tool use",
    )

    def test_reviewers_do_not_forbid_tools_they_lack(self) -> None:
        for path in self.REVIEWERS:
            for phrase in self.RETIRED_PHRASES:
                with self.subTest(agent=path.name, phrase=phrase):
                    self.assert_phrase_absent(display_path(path), read(path), phrase)

    def test_reviewers_end_by_returning_only_the_contracted_report(self) -> None:
        for path in self.REVIEWERS:
            with self.subTest(agent=path.name):
                assert_ends_with_contracted_report(self, path)


def assert_ends_with_contracted_report(test: ContractTestCase, path: Path) -> None:
    """Constraints 節に、report 契約 (SubagentHandback による報告を含む) どおりに報告だけを
    返して終了する旨の項目があることを確認する。

    report 契約の節にも SubagentHandback の説明があるため、Constraints 節の 1 項目の中で
    report 契約 (contract) と SubagentHandback を併記することを求める。
    """
    label = f"{display_path(path)} の ## Constraints 節"
    section = markdown_section(read(path), "## Constraints")
    test.assert_scope_found(label, section, "`## Constraints` 節が無い")
    test.assert_some_unit(
        label,
        list_items(section),
        (re.compile(r"(?i)contract"), "SubagentHandback", re.compile(r"(?i)report")),
        "report 契約 (SubagentHandback による報告を含む) どおりに報告だけを返して終了する指示",
    )


class CodexReviewerConstraintsTest(ContractTestCase):
    """pre-merge-cross-review と pre-push-codex-review の codex-reviewer。"""

    REVIEWERS = (PRE_MERGE_CODEX_REVIEWER, PRE_PUSH_CODEX_REVIEWER)

    def test_codex_reviewers_do_not_name_the_task_tool(self) -> None:
        for path in self.REVIEWERS:
            with self.subTest(agent=display_path(path)):
                self.assert_pattern_absent(
                    display_path(path), read(path), TASK_TOOL_NAME, "旧ツール名 Task"
                )

    def test_codex_reviewers_end_by_returning_only_the_contracted_report(self) -> None:
        for path in self.REVIEWERS:
            with self.subTest(agent=display_path(path)):
                assert_ends_with_contracted_report(self, path)


class AgentToolNameTest(ContractTestCase):
    """pre-push-review 全体と pre-push-codex-review の review command が旧ツール名を書かない。"""

    TEXT_SUFFIXES = (".md", ".sh", ".json", ".py")

    def test_pre_push_review_files_do_not_name_the_task_tool(self) -> None:
        paths = sorted(
            path
            for path in PRE_PUSH_REVIEW.rglob("*")
            if path.is_file() and path.suffix in self.TEXT_SUFFIXES
        )
        self.assertTrue(paths, f"{display_path(PRE_PUSH_REVIEW)} にファイルが無い")
        for path in paths:
            with self.subTest(file=display_path(path)):
                self.assert_pattern_absent(
                    display_path(path), read(path), TASK_TOOL_NAME, "旧ツール名 Task"
                )

    def test_codex_review_command_does_not_name_the_task_tool(self) -> None:
        self.assert_pattern_absent(
            display_path(PRE_PUSH_CODEX_REVIEW_COMMAND),
            read(PRE_PUSH_CODEX_REVIEW_COMMAND),
            TASK_TOOL_NAME,
            "旧ツール名 Task",
        )


class CrossModelAdvisorOutputLengthTest(ContractTestCase):
    """cross-model-advisor の出力の長さの指示・相談回数・既知の制約の根拠。"""

    ADVICE_LENGTH_FILES = (FABLE_ADVISOR_RUNNER, CONSULT_SKILL)

    def test_advice_has_no_word_limit(self) -> None:
        for path in self.ADVICE_LENGTH_FILES:
            with self.subTest(file=display_path(path)):
                self.assert_phrase_absent(display_path(path), read(path), "500 語")

    def test_advice_is_concise_within_what_the_decision_needs(self) -> None:
        for path in self.ADVICE_LENGTH_FILES:
            with self.subTest(file=display_path(path)):
                self.assert_phrase_present(
                    f"{display_path(path)} の助言の長さの指示",
                    read(path),
                    "採否判断に必要な範囲で簡潔に",
                )

    def test_insufficient_request_reply_has_no_line_limit(self) -> None:
        label = display_path(FABLE_ADVISOR_RUNNER)
        text = read(FABLE_ADVISOR_RUNNER)
        self.assert_phrase_absent(label, text, "1〜3 行")
        self.assert_phrase_present(f"{label} の不足の報告", text, "簡潔に返して終了")

    def test_advisor_rules_have_no_consultation_count_target(self) -> None:
        label = display_path(ADVISOR_RULES)
        text = read(ADVISOR_RULES)
        for phrase in ("数ステップを超えるタスク", "完了を宣言する前に 1 回"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(label, text, phrase)

    def test_known_limitation_cites_routing_flag_rule_and_rescue_runner(self) -> None:
        """既知の制約のうち、ユーザが rescue の本文を直接指定した場合の項目の根拠。"""
        section = markdown_section(read(CROSS_MODEL_ADVISOR_README), "## 既知の制約")
        label = f"{display_path(CROSS_MODEL_ADVISOR_README)} の ## 既知の制約 節"
        self.assert_scope_found(label, section, "`## 既知の制約` 節が無い")
        items = [item for item in list_items(section) if satisfies(item, "直接指定")]
        self.assert_scope_found(
            label, "\n".join(items), "rescue の本文を直接指定した場合の項目が無い"
        )
        for item in items:
            self.assert_phrase_absent(f"{label} の直接指定の項目", item, "rescue.md")
            self.assert_requirements(
                f"{label} の直接指定の項目",
                item,
                ("routing flag", "codex-rescue-runner"),
                "routing flag の規定と codex-rescue-runner を根拠にした説明",
            )


class LeadtimeSummaryTest(ContractTestCase):
    """repo-analytics の leadtime skill のターミナルサマリの構成テンプレート。"""

    def test_conclusion_summarizes_the_main_metric_first_without_sentence_limit(
        self,
    ) -> None:
        label = f"{display_path(LEADTIME_SKILL)} の ### サマリの構成テンプレート 節"
        section = markdown_section(read(LEADTIME_SKILL), "### サマリの構成テンプレート")
        self.assert_scope_found(label, section, "`### サマリの構成テンプレート` 節が無い")
        self.assert_phrase_absent(label, section, "1〜3 文")
        self.assert_some_unit(
            label,
            list_items(section),
            ("結論", "主指標", "冒頭", "要約"),
            "結論で主指標の推移を冒頭で要約する項目",
        )


class AgentDisciplinePromptTest(ContractTestCase):
    """agent-discipline の注入文 (auto-mode / always-2 / always-3)。"""

    def test_auto_mode_refers_to_installed_plugins(self) -> None:
        label = display_path(AUTO_MODE)
        text = read(AUTO_MODE)
        self.assert_phrase_absent(label, text, "このリポジトリの他プラグイン")
        self.assert_phrase_present(label, text, "導入済みの他プラグイン")

    def test_issue_body_contract_sentence_is_plain(self) -> None:
        """issue body を契約書とみなす文は太字と「絶対に」を使わずに書き、節番号 3.1 を保つ。"""
        label = f"{display_path(ALWAYS_2)} の rule:issue-body 節"
        block = rule_block(read(ALWAYS_2), "issue-body")
        self.assert_scope_found(label, block, "`<!-- rule:issue-body -->` が無い")
        contract_sentences = [
            sentence
            for sentence in japanese_sentences(block)
            if satisfies(sentence, "契約書")
        ]
        self.assert_scope_found(
            label, "\n".join(contract_sentences), "issue body を契約書とみなす文が無い"
        )
        for sentence in contract_sentences:
            with self.subTest(sentence=sentence.strip()[:60]):
                for emphasis in ("**", "絶対に"):
                    if emphasis in sentence:
                        self.fail(
                            f"{label}: 契約書の文に「{emphasis}」が残っている: "
                            f"{sentence.strip()[:120]}"
                        )
                self.assert_phrase_present(label, sentence, "混入させない")
        self.assertIn(
            "### 3.1 ", block, f"{label}: 節番号 3.1 の見出しが無い"
        )

    def test_plan_files_are_not_tied_to_a_directory(self) -> None:
        label = display_path(ALWAYS_2)
        text = read(ALWAYS_2)
        self.assert_phrase_absent(label, text, ".claude/plans/")
        self.assert_phrase_present(label, text, "plan ファイル")

    def test_closing_keyword_rule_keeps_only_the_author_conventions(self) -> None:
        """closing keyword の節は作者の規約 3 点・適用範囲・例を書き、GitHub の一般知識
        (有効キーワードの列挙・colon の有無・cross-repo の書式) を書かない。"""
        label = f"{display_path(ALWAYS_2)} の rule:closing-keyword 節"
        block = rule_block(read(ALWAYS_2), "closing-keyword")
        self.assert_scope_found(label, block, "`<!-- rule:closing-keyword -->` が無い")
        for phrase in (
            "`resolved`",
            "case-insensitive",
            "9 種",
            "colon",
            "cross-repo",
            "owner/repo",
        ):
            with self.subTest(general_knowledge=phrase):
                self.assert_phrase_absent(label, block, phrase)
        for requirement, rule in (
            (("PR body", "title", "Closes #"), "PR body (title ではない) に Closes #N で統一する規約"),
            (("Refs #", "Part of #"), "部分対応では Refs #N / Part of #N とする規約"),
            (("default branch",), "auto-close は default branch 向けの PR でのみ機能する規約"),
            (("**適用範囲**",), "適用範囲"),
            (("悪い例", "良い例"), "例"),
        ):
            with self.subTest(rule=rule):
                self.assert_requirements(label, block, requirement, rule)

    def test_claim_wait_has_a_background_fallback(self) -> None:
        """3 秒待機は残し、harness が foreground の sleep を拒否する場合に Bash の background
        実行で `sleep 3` を走らせ、完了通知を待つ手順を同じ手順項目に書く。"""
        label = f"{display_path(ALWAYS_3)} の rule:issue-claim 節の 3 秒待機の項目"
        section = markdown_section(rule_block(read(ALWAYS_3), "issue-claim"), "### 着手手順")
        item = top_level_list_item_containing(section, "sleep 3")
        self.assert_scope_found(label, item, "`sleep 3` を含む手順項目が無い")
        self.assert_requirements(
            label,
            item,
            (
                "3 秒",
                re.compile(r"(?i)foreground|フォアグラウンド"),
                re.compile(r"拒否|拒ま|拒む"),
                re.compile(r"(?i)background|バックグラウンド"),
                "通知",
            ),
            "foreground の sleep が拒否された場合に background 実行で待つ手順",
        )


class AgentDisciplineDocumentTest(ContractTestCase):
    """agent-discipline の skill・README・暫定ルール。"""

    def test_issue_start_does_not_defer_keyword_list(self) -> None:
        label = display_path(ISSUE_START_SKILL)
        text = read(ISSUE_START_SKILL)
        self.assert_phrase_absent(label, text, "有効なキーワード一覧")
        self.assert_phrase_present(label, text, "rule:closing-keyword")

    def test_issue_plan_github_facts_have_confirmation_dates(self) -> None:
        """GitHub 仕様の記述 2 箇所 (issue types の利用範囲・親 issue が自動 close されない
        こと) の段落に確認日がある。"""
        text = read(ISSUE_PLAN_SKILL)
        for fact, markers in (
            ("issue types は組織配下の repository でのみ利用できる", ("issue types", "組織")),
            ("親 issue は自動では close されない", ("親 issue", "自動", "close")),
        ):
            with self.subTest(fact=fact):
                label = f"{display_path(ISSUE_PLAN_SKILL)} の「{fact}」の段落"
                blocks = [
                    block
                    for block in paragraphs(text)
                    if all(satisfies(block, marker) for marker in markers)
                ]
                self.assert_scope_found(label, "\n".join(blocks), "該当する段落が無い")
                for block in blocks:
                    self.assert_requirements(label, block, (CONFIRMATION_DATE,), "確認日")

    def test_readme_does_not_describe_the_thinking_sentence(self) -> None:
        self.assert_phrase_absent(
            display_path(AGENT_DISCIPLINE_README),
            read(AGENT_DISCIPLINE_README),
            "単純な作業での思考量を増やす理由",
        )

    def test_preview_workaround_header_states_how_to_confirm(self) -> None:
        """暫定ルールの先頭コメントに、確認方法 (preview に 20 行以上を載せた
        AskUserQuestion で hidden 表示が出るかを見る) と確認した Claude Code のバージョンがある。"""
        label = f"{display_path(PREVIEW_WORKAROUND)} の先頭コメント"
        header = header_comment(read(PREVIEW_WORKAROUND))
        self.assert_scope_found(label, header, "先頭の HTML コメントが無い")
        self.assert_requirements(
            label,
            header,
            ("撤去条件", "確認", "preview", re.compile(r"20行"), "hidden"),
            "確認方法",
        )
        self.assert_requirements(
            label,
            header,
            (re.compile(r"(?i)claudecode"), re.compile(r"\d+\.\d+\.\d+")),
            "確認した Claude Code のバージョン",
        )


class UiDisciplineAvoidDefaultsTest(ContractTestCase):
    """ui-discipline の「避ける既定」の一覧・視覚方向の例示・注入上限。"""

    AVOID_DEFAULTS_LABEL = "避ける既定"
    INJECTION_LIMIT = 8000

    # 一覧に含める既定スタイルと、それを識別する語 (空白を除去した項目に照合する)。
    REQUIRED_DEFAULTS: tuple[tuple[str, tuple[Requirement, ...]], ...] = (
        ("無検討の定番フォント (Inter / Roboto / system fonts)", ("Inter", "Roboto")),
        ("紫系グラデーション", ("紫", "グラデーション")),
        ("文脈と無関係な装飾", ("装飾",)),
        ("クリーム・生成り系の背景", (re.compile(r"クリーム|生成り"), "背景")),
        ("見出し内のイタリックのアクセント語", ("見出し", "イタリック")),
        ("「01 / 02 / 03」形式の番号付きセクションラベル", (re.compile(r"01/02"), "番号")),
        ("等幅フォントのラベル", ("等幅", "ラベル")),
        ("pill 形のボタン", (re.compile(r"(?i)pill|ピル"), "ボタン")),
    )

    # 視覚方向の例示に使わない語 (避ける既定に該当する要素)。
    DEFAULT_STYLE_WORDS: tuple[Requirement, ...] = (
        "ウォームエディトリアル",
        "生成り",
        "クリーム",
        "イタリック",
        "等幅",
        re.compile(r"(?i)pill"),
        "ピル",
        "紫",
        "グラデーション",
        re.compile(r"\bInter\b"),
        "Roboto",
        re.compile(r"0\d/0\d"),
    )

    @classmethod
    def avoid_defaults_items(cls, scope: str) -> list[str]:
        """`scope` の「避ける既定」の一覧を、空白を除去した項目の列で返す。

        ラベルの後に同じ行で続く場合は最初の「。」(または段落末) までを「、」で区切り、
        ラベルの行がラベルだけで終わる場合は直後の箇条書きの項目を一覧とする。
        """
        start = scope.find(cls.AVOID_DEFAULTS_LABEL)
        if start < 0:
            return []
        rest = scope[start + len(cls.AVOID_DEFAULTS_LABEL) :]
        rest = re.sub(r"^\**\s*[:：]\s*\**", "", rest)
        first_line, _, following = rest.partition("\n")
        if first_line.strip():
            inline = re.split(r"。|\n\s*\n", rest, maxsplit=1)[0]
            raw_items = inline.split("、")
        else:
            raw_items = []
            for line in following.splitlines():
                if not line.strip():
                    if raw_items:
                        break
                    continue
                if not LIST_ITEM_PATTERN.match(line):
                    break
                raw_items.append(LIST_ITEM_PATTERN.sub("", line, count=1))
        items = [strip_whitespace(item).rstrip("。") for item in raw_items]
        return [item for item in items if item]

    def ui_rules_visual_direction(self) -> str:
        block = rule_block(read(UI_RULES), "visual-direction")
        self.assert_scope_found(
            display_path(UI_RULES), block, "`<!-- rule:visual-direction -->` が無い"
        )
        return block

    def skill_visual_direction(self) -> str:
        section = markdown_section(read(UI_PATTERNS_SKILL), "## 10. rule:visual-direction")
        self.assert_scope_found(
            display_path(UI_PATTERNS_SKILL), section, "`## 10. rule:visual-direction` 節が無い"
        )
        return section

    def test_avoid_defaults_lists_are_identical(self) -> None:
        rules_items = self.avoid_defaults_items(self.ui_rules_visual_direction())
        skill_items = self.avoid_defaults_items(self.skill_visual_direction())
        for path, items in ((UI_RULES, rules_items), (UI_PATTERNS_SKILL, skill_items)):
            self.assert_scope_found(
                f"{display_path(path)} の視覚方向の節",
                "\n".join(items),
                f"「{self.AVOID_DEFAULTS_LABEL}」の一覧が無い",
            )
        self.assertEqual(
            rules_items,
            skill_items,
            f"「{self.AVOID_DEFAULTS_LABEL}」の一覧が {display_path(UI_RULES)} の rule 10 と "
            f"{display_path(UI_PATTERNS_SKILL)} で一致しない",
        )

    def test_avoid_defaults_list_contains_the_required_defaults(self) -> None:
        for path, scope in (
            (UI_RULES, self.ui_rules_visual_direction()),
            (UI_PATTERNS_SKILL, self.skill_visual_direction()),
        ):
            items = self.avoid_defaults_items(scope)
            for name, requirements in self.REQUIRED_DEFAULTS:
                with self.subTest(file=display_path(path), default=name):
                    if not any(
                        all(satisfies(item, requirement) for requirement in requirements)
                        for item in items
                    ):
                        self.fail(
                            f"{display_path(path)}: 「{self.AVOID_DEFAULTS_LABEL}」の一覧に"
                            f"「{name}」の項目が無い (一覧: {items})"
                        )

    def test_visual_direction_example_avoids_the_defaults(self) -> None:
        """skill の視覚方向の提案例 (引用ブロック) は 2 案以上あり、避ける既定に該当しない。"""
        label = f"{display_path(UI_PATTERNS_SKILL)} の視覚方向の提案例"
        example_lines = [
            line
            for line in self.skill_visual_direction().splitlines()
            if line.startswith(">")
        ]
        proposals = [line for line in example_lines if re.match(r">\s*\d+\.\s*\*\*", line)]
        self.assertGreaterEqual(
            len(proposals), 2, f"{label}: 配色・書体・トーンを示す提案が 2 案以上無い"
        )
        example = "\n".join(example_lines)
        for word in self.DEFAULT_STYLE_WORDS:
            with self.subTest(word=describe(word)):
                if satisfies(example, word):
                    self.fail(f"{label}: 避ける既定に該当する「{describe(word)}」を含む")

    def test_session_start_injection_fits_the_limit(self) -> None:
        # inject-ui-rules.sh は `$(cat ...)` で読むため、末尾の改行は注入されない。
        injected = read(UI_RULES).rstrip("\n")
        self.assertLessEqual(
            utf16_length(injected),
            self.INJECTION_LIMIT,
            f"{display_path(UI_RULES)}: SessionStart の注入文が {self.INJECTION_LIMIT} 字を超える",
        )

    @unittest.skipUnless(shutil.which("jq"), "SubagentStart の注入文の生成には jq が必要")
    def test_subagent_start_injection_fits_the_limit(self) -> None:
        """SubagentStart の注入文 (前置き注記 + 空行 + ui-rules.md) を hook を実行して計測する。"""
        result = subprocess.run(
            ["/bin/sh", str(UI_SUBAGENT_INJECTOR)],
            input="{}",
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(
            result.stdout.strip(),
            f"{display_path(UI_SUBAGENT_INJECTOR)}: 注入文が出力されない",
        )
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(
            strip_whitespace(read(UI_RULES)),
            strip_whitespace(context),
            "SubagentStart の注入文に ui-rules.md の本文が含まれない",
        )
        self.assertLessEqual(
            utf16_length(context),
            self.INJECTION_LIMIT,
            f"{display_path(UI_SUBAGENT_INJECTOR)}: SubagentStart の注入文 (前置き注記との"
            f"連結後) が {self.INJECTION_LIMIT} 字を超える",
        )


class CodexStatusModelNameTest(ContractTestCase):
    """rate-limit の codex-status skill が独立枠の説明に固定のモデル名を書かない。"""

    def test_independent_limit_is_described_without_model_names(self) -> None:
        label = display_path(CODEX_STATUS_SKILL)
        text = read(CODEX_STATUS_SKILL)
        for phrase in ("bengalfox", "GPT-5", "Spark"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(label, text, phrase)
        rows = [
            line
            for line in text.splitlines()
            if line.startswith("|") and "`rateLimitsByLimitId`" in line
        ]
        self.assert_scope_found(label, "\n".join(rows), "`rateLimitsByLimitId` の行が無い")
        self.assert_some_unit(
            f"{label} の `rateLimitsByLimitId` の行",
            rows,
            ("独立枠", re.compile(r"`?codex`?以外")),
            "limitId が `codex` 以外の独立枠という説明",
        )


class RebaseWorkflowTest(ContractTestCase):
    """git-guardrails の rebase-workflow skill。"""

    CONFLICT_HEADING = "### 3. コンフリクト"

    def test_conflict_resolution_is_one_sentence(self) -> None:
        """解決して `git add` → `git rebase --continue`、断念する場合は `git rebase --abort`
        で rebase 前に戻す、を 1 文で書き、手順の段 (状況確認・マーカーの説明) を持たない。"""
        label = f"{display_path(REBASE_SKILL)} のコンフリクト発生時の節"
        section = markdown_section(read(REBASE_SKILL), self.CONFLICT_HEADING)
        self.assert_scope_found(label, section, f"`{self.CONFLICT_HEADING}` で始まる節が無い")
        self.assert_some_unit(
            label,
            japanese_sentences(section),
            ("git add", "git rebase --continue", "git rebase --abort"),
            "コンフリクト解消 (git add → continue、断念時は abort) を述べる 1 文",
        )
        for phrase in ("<<<<<<<", "状況確認"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(label, section, phrase)

    def test_no_strategy_advice_for_many_conflicts(self) -> None:
        label = display_path(REBASE_SKILL)
        text = read(REBASE_SKILL)
        for phrase in ("小さな単位で rebase", "マージを検討"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(label, text, phrase)

    def test_symbolic_ref_runs_alone_and_prefix_is_read_off(self) -> None:
        """`git symbolic-ref refs/remotes/origin/HEAD` を単独のコマンドとして実行し、出力の
        `refs/remotes/origin/` を読み取りで除く。"""
        label = display_path(REBASE_SKILL)
        text = read(REBASE_SKILL)
        command_lines = [
            line.strip() for line in text.splitlines() if "git symbolic-ref" in line
        ]
        self.assertIn(
            "git symbolic-ref refs/remotes/origin/HEAD",
            command_lines,
            f"{label}: `git symbolic-ref refs/remotes/origin/HEAD` を単独で実行する行が無い",
        )
        for line in command_lines:
            with self.subTest(line=line):
                for fragment in ("| sed", "2>/dev/null"):
                    if fragment in line:
                        self.fail(f"{label}: symbolic-ref の行に「{fragment}」が残っている: {line}")
        self.assert_some_unit(
            label,
            japanese_sentences(text),
            ("refs/remotes/origin/", re.compile(r"読み取"), re.compile(r"除")),
            "出力から refs/remotes/origin/ を読み取りで除く説明",
        )


class StatuslineCommandBugNoteTest(ContractTestCase):
    """Claude Code の bug #52079 の記述を、現在の不具合・撤去条件・確認方法の 3 要素で書く。"""

    # 3 要素を識別する語 (空白を除去した範囲に照合する)。
    CURRENT_BUG: tuple[Requirement, ...] = (
        "statusLine.command",
        "CLAUDE_PLUGIN_ROOT",
        "展開",
        "not planned",
        CONFIRMATION_DATE,
    )
    REMOVAL_CONDITION: tuple[Requirement, ...] = (re.compile(r"撤去"),)
    HOW_TO_CONFIRM: tuple[Requirement, ...] = (re.compile(r"確認(?:方法|手順)|確かめ"),)

    def assert_three_elements(self, label: str, scope: str) -> None:
        for rule, requirements in (
            ("現在の不具合 (upstream の状態と確認日を含む)", self.CURRENT_BUG),
            ("撤去条件", self.REMOVAL_CONDITION),
            ("確認方法", self.HOW_TO_CONFIRM),
        ):
            with self.subTest(file=label, element=rule):
                self.assert_requirements(label, scope, requirements, rule)

    def test_documents_describe_the_bug_with_three_elements(self) -> None:
        """Markdown では、bug 番号を含む節ごとに 3 要素を書く。"""
        for path in STATUSLINE_BUG_NOTE_DOCUMENTS:
            text = read(path)
            positions = [match.start() for match in re.finditer(STATUSLINE_BUG_NUMBER, text)]
            self.assert_scope_found(
                display_path(path),
                "\n".join(str(position) for position in positions),
                f"bug #{STATUSLINE_BUG_NUMBER} の記述が無い",
            )
            for position in positions:
                self.assert_three_elements(
                    f"{display_path(path)} の bug #{STATUSLINE_BUG_NUMBER} を含む節",
                    enclosing_section(text, position),
                )

    def test_scripts_describe_the_bug_with_three_elements(self) -> None:
        """shell script では、コメント (生成するファイルに書き出すコメントを含む) に 3 要素を書く。"""
        for path in STATUSLINE_BUG_NOTE_SCRIPTS:
            comments = shell_comment_text(read(path))
            label = f"{display_path(path)} のコメント"
            self.assert_phrase_present(label, comments, STATUSLINE_BUG_NUMBER)
            self.assert_three_elements(label, comments)


class ReviewCadenceRulesTest(ContractTestCase):
    """pre-push-codex-review の review cadence 規律の注入文と README。"""

    CHECKPOINT_SECTION_NAME = "Codex review 5 サイクルごとの根本方針 checkpoint"

    def boundary_paragraph(self) -> str:
        label = display_path(REVIEW_CADENCE_RULES)
        blocks = [
            block
            for block in paragraphs(read(REVIEW_CADENCE_RULES))
            if block.startswith("**境界**")
        ]
        self.assert_scope_found(label, "\n".join(blocks), "`**境界**` で始まる段落が無い")
        return blocks[0]

    def test_boundary_states_the_non_reset_targets_once(self) -> None:
        """通常の advisor 相談・cancel 等でカウンターを変えない規定を 1 文だけで述べる。"""
        label = f"{display_path(REVIEW_CADENCE_RULES)} の境界段落"
        repeated = [
            sentence.strip()[:80]
            for sentence in japanese_sentences(self.boundary_paragraph())
            if satisfies(sentence, "通常の advisor 相談") and satisfies(sentence, "cancel")
        ]
        self.assertEqual(
            1,
            len(repeated),
            f"{label}: 通常の advisor 相談・cancel でカウンターを変えない規定を述べる文の数が "
            f"1 でない: {repeated}",
        )

    def test_boundary_does_not_mention_internals(self) -> None:
        label = display_path(REVIEW_CADENCE_RULES)
        text = read(REVIEW_CADENCE_RULES)
        for phrase in ("本 script", "is_interrupt"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(label, text, phrase)

    def test_boundary_keeps_the_behavior_rules(self) -> None:
        label = f"{display_path(REVIEW_CADENCE_RULES)} の境界段落"
        paragraph = self.boundary_paragraph()
        for requirement, rule in (
            (("increment",), "reset・increment しない対象"),
            ((re.compile(r"(?i)interrupt|Esc|中断"),), "ユーザ interrupt による abort の扱い"),
            (("PostToolUseFailure",), "PostToolUseFailure での自動 reset"),
            (
                ("PermissionDenied", "1 回目", "2 回目", "`AskUserQuestion`"),
                "PermissionDenied の 1 回目 retry・2 回目 reset",
            ),
            (("作業報告",), "fail-open 時の報告"),
            (("手動",), "手動解除"),
        ):
            with self.subTest(rule=rule):
                self.assert_requirements(label, paragraph, requirement, rule)

    def test_consult_is_referenced_by_its_section_name(self) -> None:
        for path, command in (
            (REVIEW_CADENCE_RULES, "/cross-model-advisor:consult"),
            (PRE_PUSH_CODEX_README, "cross-model-advisor:consult"),
        ):
            with self.subTest(file=display_path(path)):
                text = read(path)
                self.assert_phrase_absent(display_path(path), text, "review cadence mode")
                body_paragraphs = [
                    block for block in paragraphs(text) if not HEADING_PATTERN.match(block)
                ]
                self.assert_some_unit(
                    display_path(path),
                    body_paragraphs,
                    (command, self.CHECKPOINT_SECTION_NAME),
                    "consult skill を実在する節名で参照する記述",
                )


class ReviewCommandParallelWordingTest(ContractTestCase):
    """review command の冒頭の説明が、並列発出が成立しない場合の節と整合する。"""

    COMMANDS = (PRE_PUSH_REVIEW_COMMAND, PRE_PUSH_CODEX_REVIEW_COMMAND)

    def test_intro_does_not_make_parallel_launch_the_only_answer(self) -> None:
        for path in self.COMMANDS:
            with self.subTest(file=display_path(path)):
                label = f"{display_path(path)} の冒頭の説明"
                text = read(path)
                self.assert_phrase_absent(label, text, "のみが正解")
                intro = [
                    block
                    for block in paragraphs(text)
                    if not HEADING_PATTERN.match(block) and satisfies(block, "確定的フロー")
                ]
                self.assert_scope_found(label, "\n".join(intro), "「確定的フロー」を述べる段落が無い")
                self.assert_requirements(
                    label,
                    intro[0],
                    ("並列", re.compile(r"成立しない|例外|困難")),
                    "並列発出を既定とし、成立しない場合の節と整合する説明",
                )


if __name__ == "__main__":
    unittest.main()

"""pre-push-review の reviewer 設計根拠と tool grant を固定する契約テスト。

pre-push-review は push 前の 2 レビューを標準 skill ではなく専用 subagent
(`pre-push-review:code-reviewer` / `pre-push-review:security-reviewer`) で実行する。
その理由は次の 3 点であり、両 agent の description / body、
`/pre-push-review:review` command、plugin README、auto-mark.sh のコメントは
この 3 点で説明を揃える:

1. confidence 付きの parent-safe report 契約を reviewer 側に固定できる
2. SubagentStart / SubagentHandback / SubagentStop の lifecycle hook が reviewer の
   実行完了を marker として検知できる
3. `tools` から `Agent` を除外することで reviewer を read-only に保てる

本ファイルが検査するのは次の 4 点:

- 両 agent の tool grant が `tools: Bash, Read, Glob, Grep` であること
- 上記 3 点と両立しない説明語 (harness が nested subagent 起動を禁止している /
  標準 skill を呼ぶと turn が終了する / 標準 skill が degraded mode になる /
  存在しない `LS` tool の名指し / `CLAUDE_CODE_SUBAGENT_MODEL` が明示 model や
  agent frontmatter より優先される) を、plugin の 5 ファイル、リポジトリ直下
  README、pre-push-codex-review の command と auto-mark.sh が含まないこと
- 上記 3 点の説明が、agent description / agent body / command の理由節 /
  plugin README の `### Agents` 節 (3 点それぞれが同一の箇条書き項目・段落に
  共起する) / auto-mark.sh の Skill 検知コメントにあること
- README の `### マーカーファイル` 節末尾が subagent の model 解決順序
  (明示 model・agent frontmatter が env より優先され、
  `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` 設定時のみ env が全てを上書きする) を
  述べていること
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-push-review"
CODE_REVIEWER = PLUGIN / "agents" / "code-reviewer.md"
SECURITY_REVIEWER = PLUGIN / "agents" / "security-reviewer.md"
REVIEW_COMMAND = PLUGIN / "commands" / "review.md"
PLUGIN_README = PLUGIN / "README.md"
AUTO_MARK = PLUGIN / "hooks" / "scripts" / "auto-mark.sh"
ROOT_README = ROOT / "README.md"

REVIEWER_AGENTS = (CODE_REVIEWER, SECURITY_REVIEWER)

# 自前 reviewer を使う理由を述べる plugin 側の 5 ファイル。説明はこの 5 ファイルで
# 揃える (理由 3 点の必須語もこの 5 ファイルに課す)。
RATIONALE_FILES = (
    CODE_REVIEWER,
    SECURITY_REVIEWER,
    REVIEW_COMMAND,
    PLUGIN_README,
    AUTO_MARK,
)

# pre-push-codex-review も標準 skill ではなく専用 subagent でレビューするため、
# その command と auto-mark.sh のコメントも同じ禁止語の不在検査の対象にする。
CODEX_PLUGIN = ROOT / "plugins" / "pre-push-codex-review"
CODEX_REVIEW_COMMAND = CODEX_PLUGIN / "commands" / "review.md"
CODEX_AUTO_MARK = CODEX_PLUGIN / "hooks" / "scripts" / "auto-mark.sh"

# 禁止語の不在検査の対象。リポジトリ直下 README も pre-push-review の節で同じ理由を
# 述べるため、失効した説明が残らないよう検査対象に含める。
EXPIRED_RATIONALE_SCANNED_FILES = RATIONALE_FILES + (
    ROOT_README,
    CODEX_REVIEW_COMMAND,
    CODEX_AUTO_MARK,
)

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
SECTION_BOUNDARY_PATTERN = re.compile(r"^#{1,3} ")
FENCE_PATTERN = re.compile(r"^\s*(?:```|~~~)")
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+\.) ")

# reviewer に許す tool と順序。`Agent` / `Task` / `Skill` / `Edit` / `Write` を
# 含めないことで reviewer を read-only に保つ。
TOOLS_LINE = "tools: Bash, Read, Glob, Grep"

# nested subagent を起動しないのは reviewer 側の tool grant による選択であり、
# harness 側の禁止ではない。禁止の主張に使われる語は 5 ファイルのどこにも置かない。
PHRASES_CLAIMING_NESTED_SUBAGENT_IS_IMPOSSIBLE = (
    "spawn できない",
    "cannot spawn",
    "cannot spawn nested",
    "No nested sub-tasks",
    "nested subagent 起動を禁止",
    "nested 制約",
    "nested subagent 制約",
    "Claude Code の制約で動かない",
    "subagents cannot spawn other subagents",
    "impossible from this subagent context",
)

# 標準 skill を直接呼ばない理由は report 契約と lifecycle 検知であり、
# 「呼ぶと turn が終了する」ではない。
PHRASES_CLAIMING_STANDARD_SKILL_ENDS_THE_TURN = (
    "turn が終了",
    "turn を終了",
)

# 標準 skill が subagent 内で機能低下するという主張は置かない。
PHRASES_CLAIMING_STANDARD_SKILL_RUNS_DEGRADED = ("degraded mode",)

# `LS` tool は Claude Code に存在しないため、tool 名として名指ししない
# (バッククォート付きの言及と、tools 列挙の末尾に並べる形の両方)。
PHRASES_NAMING_THE_NONEXISTENT_LS_TOOL = (
    "`LS`",
    "Grep, LS",
)

# README の model 解決順序は「明示 model / agent frontmatter が env より優先される」
# 形で書く。env が agent frontmatter より優先されるという向きの説明は README に置かない。
PHRASE_CLAIMING_ENV_PRECEDES_AGENT_FRONTMATTER = "agent frontmatter より優先されるため"

# reviewer が nested subagent を起動しない旨を agent body に書く一文 (完全一致)。
NO_SPAWN_CLAUSE = (
    "Do not spawn subagents; the `Agent` tool is intentionally omitted "
    "from your tools."
)

RATIONALE_KEYWORDS_PARENT_SAFE_REPORT = ("parent-safe", "confidence")
RATIONALE_KEYWORDS_LIFECYCLE_MARKER = (
    "SubagentStart",
    "SubagentStop",
    "SubagentHandback",
)
RATIONALE_KEYWORDS_READ_ONLY_TOOL_GRANT = ("Agent", "read-only")

# 自前 reviewer を使う理由の 3 点。各 scope に 3 点すべての語が現れることを求める。
RATIONALE_KEYWORD_GROUPS = (
    ("parent-safe report 契約", RATIONALE_KEYWORDS_PARENT_SAFE_REPORT),
    ("lifecycle hook による marker 検知", RATIONALE_KEYWORDS_LIFECYCLE_MARKER),
    ("`Agent` 除外による read-only 維持", RATIONALE_KEYWORDS_READ_ONLY_TOOL_GRANT),
)

# command 側の理由節と auto-mark.sh の Skill 検知コメントを特定する目印。
# 検査対象の位置を一意に決めるため、この文言自体も契約の一部として固定する。
REVIEW_COMMAND_RATIONALE_MARKER = "標準 skill を直接呼ばない理由"
AUTO_MARK_SKILL_DETECTION_MARKER = "Skill 検知は行わない"

# auto-mark.sh が Skill 検知を行わない理由は lifecycle hook で完了を検知する設計に
# あるため、そのコメントは 2 つの lifecycle event を名指しする。
AUTO_MARK_SKILL_DETECTION_KEYWORDS = ("SubagentStart", "SubagentStop")

README_AGENTS_HEADING = "### Agents"
README_MARKER_FILE_HEADING = "### マーカーファイル"

# README の `### マーカーファイル` 節末尾が述べる model 解決順序の語。
README_MODEL_RESOLUTION_KEYWORDS = (
    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE",
    "frontmatter",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def repo_relative(path: Path) -> str:
    """リポジトリ直下からの相対パスを返す (同名ファイルを subTest で区別する)。"""
    return str(path.relative_to(ROOT))


def normalize(text: str) -> str:
    """空白・改行を単一スペースに正規化する (md の折り返し差異を吸収する)。"""
    return " ".join(text.split())


def strip_whitespace(text: str) -> str:
    """空白文字をすべて除去する。

    禁止語の照合に使う。日本語の禁止語は語中に空白を持たないため、行の折り返しで
    分断された出現は単一スペースへの正規化では一致しない。
    """
    return "".join(text.split())


def frontmatter_lines(text: str) -> list[str] | None:
    """frontmatter の行の列を返す。frontmatter が無ければ None を返す。"""
    match = FRONTMATTER_PATTERN.match(text)
    if match is None:
        return None
    return match.group(1).splitlines()


def agent_body(text: str) -> str:
    """frontmatter を除いた本文を返す。"""
    match = FRONTMATTER_PATTERN.match(text)
    return text[match.end() :] if match else text


def description_line(text: str) -> str:
    """frontmatter の `description:` 行を返す。1 行に確定しなければ空文字を返す。"""
    lines = frontmatter_lines(text) or []
    matches = [line for line in lines if line.startswith("description:")]
    return matches[0] if len(matches) == 1 else ""


def markdown_section(text: str, heading: str) -> str:
    """`heading` の次の行から次の `#`〜`###` 見出しの手前までを返す。

    見出し行そのものは含めない (見出しの語が本文のキーワード検査を満たさないように
    する)。fence 内の `#` 始まりの行は見出しとして扱わない。
    """
    lines = text.splitlines(keepends=True)
    start: int | None = None
    in_fence = False
    for index, line in enumerate(lines):
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.rstrip("\n") == heading:
            start = index
            continue
        if start is not None and SECTION_BOUNDARY_PATTERN.match(line):
            return "".join(lines[start + 1 : index])
    if start is None:
        return ""
    return "".join(lines[start + 1 :])


def paragraphs(text: str) -> list[str]:
    """空行で区切った段落 (空でないもの) の列を返す。"""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line)
            continue
        if current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def cooccurrence_units(text: str) -> list[str]:
    """キーワードの共起を判定する単位 (箇条書き 1 項目 / 段落) の列を返す。

    空行と箇条書き項目の開始で区切る。箇条書きの記号で始まらない継続行
    (インデントされた行など) は直前の単位に含める。
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
    return units


def list_item_containing(text: str, marker: str) -> str:
    """`marker` を含む箇条書き 1 項目分 (次の項目・空行の手前まで) を返す。"""
    lines = text.splitlines()
    target: int | None = None
    for index, line in enumerate(lines):
        if marker in line:
            target = index
            break
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


def shell_comment_block_containing(text: str, marker: str) -> str:
    """`marker` を含むコメント段落 (内容のある `#` 行の連続) を 1 行に連結して返す。"""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        content = stripped.lstrip("#").strip() if stripped.startswith("#") else ""
        if content:
            current.append(content)
            continue
        if current:
            blocks.append(" ".join(current))
            current = []
    if current:
        blocks.append(" ".join(current))
    for block in blocks:
        if marker in block:
            return block
    return ""


class ContractTestCase(unittest.TestCase):
    """対象ファイルが数百行あるため、失敗時に全文ではなく該当箇所だけを示す helper。"""

    def assert_phrase_absent(self, path: Path, phrase: str) -> None:
        """空白を除去した全文で禁止語の不在を確認する (折り返しでの分断も検出する)。

        hit 一覧は行単位の診断のため、行内に収まらない出現はその旨だけを示す。
        """
        body = read(path)
        if strip_whitespace(phrase) not in strip_whitespace(body):
            return
        hits = [
            f"L{number}: {line.strip()[:120]}"
            for number, line in enumerate(body.splitlines(), start=1)
            if phrase in line
        ]
        joined = " / ".join(hits) if hits else "改行をまたいで出現"
        self.fail(f"{path}: 「{phrase}」の言及が残っている: {joined}")

    def assert_scope_found(self, label: str, scope: str, hint: str) -> None:
        if not scope.strip():
            self.fail(f"{label}: 検査対象の箇所が見つからない ({hint})")

    def assert_keywords_present(
        self, label: str, scope: str, group: str, keywords: tuple[str, ...]
    ) -> None:
        missing = [keyword for keyword in keywords if keyword not in scope]
        if missing:
            self.fail(f"{label}: 「{group}」の語が無い: {', '.join(missing)}")

    def assert_keywords_cooccur(
        self,
        label: str,
        units: list[str],
        group: str,
        keywords: tuple[str, ...],
    ) -> None:
        """1 つの箇条書き項目・段落の中に group の語がすべて現れることを確認する。

        節全体での散在を許すと、別々の文が偶然すべての語を埋めて green になる。
        """
        matched = [
            (sum(keyword in unit for keyword in keywords), unit) for unit in units
        ]
        if any(count == len(keywords) for count, _ in matched):
            return
        best_count, best_unit = max(matched, default=(0, ""))
        missing = [keyword for keyword in keywords if keyword not in best_unit]
        closest = normalize(best_unit)[:120] if best_count else "該当なし"
        self.fail(
            f"{label}: 「{group}」の語 ({', '.join(keywords)}) が同一の"
            f"箇条書き項目・段落にそろっていない。最も近い箇所に欠けている語: "
            f"{', '.join(missing) or 'なし'} / その箇所: {closest}"
        )


class ReviewerToolGrantTest(ContractTestCase):
    """reviewer subagent の tool grant (frontmatter の tools 行)。"""

    def test_reviewer_agents_grant_exactly_the_read_only_tools(self) -> None:
        """両 agent の tools 行が `Bash, Read, Glob, Grep` と順序込みで一致する。"""
        for path in REVIEWER_AGENTS:
            with self.subTest(agent=path.name):
                lines = frontmatter_lines(read(path))
                if lines is None:
                    self.fail(f"{path}: frontmatter が無い")
                    return
                tools_lines = [line for line in lines if line.startswith("tools:")]
                self.assertEqual(1, len(tools_lines), f"{path}: {tools_lines}")
                self.assertEqual(TOOLS_LINE, tools_lines[0], str(path))


class ExpiredRationaleAbsenceTest(ContractTestCase):
    """自前 reviewer の理由として成立しない説明語が対象ファイルに無いこと。"""

    def test_no_file_claims_nested_subagent_is_impossible(self) -> None:
        """nested subagent 起動が harness に禁じられている、という説明が無い。"""
        for path in EXPIRED_RATIONALE_SCANNED_FILES:
            for phrase in PHRASES_CLAIMING_NESTED_SUBAGENT_IS_IMPOSSIBLE:
                with self.subTest(file=repo_relative(path), phrase=phrase):
                    self.assert_phrase_absent(path, phrase)

    def test_no_file_claims_standard_skill_ends_the_turn(self) -> None:
        """標準 skill を呼ぶと turn が終了する、という説明が無い。"""
        for path in EXPIRED_RATIONALE_SCANNED_FILES:
            for phrase in PHRASES_CLAIMING_STANDARD_SKILL_ENDS_THE_TURN:
                with self.subTest(file=repo_relative(path), phrase=phrase):
                    self.assert_phrase_absent(path, phrase)

    def test_no_file_claims_standard_skill_runs_degraded(self) -> None:
        """標準 skill が degraded mode に倒れる、という説明が無い。"""
        for path in EXPIRED_RATIONALE_SCANNED_FILES:
            for phrase in PHRASES_CLAIMING_STANDARD_SKILL_RUNS_DEGRADED:
                with self.subTest(file=repo_relative(path), phrase=phrase):
                    self.assert_phrase_absent(path, phrase)

    def test_no_file_names_the_nonexistent_ls_tool(self) -> None:
        """存在しない `LS` tool を tool 名として名指しする箇所が無い。"""
        for path in EXPIRED_RATIONALE_SCANNED_FILES:
            for phrase in PHRASES_NAMING_THE_NONEXISTENT_LS_TOOL:
                with self.subTest(file=repo_relative(path), phrase=phrase):
                    self.assert_phrase_absent(path, phrase)

    def test_readme_does_not_claim_env_precedence_over_explicit_model(self) -> None:
        """`CLAUDE_CODE_SUBAGENT_MODEL` が明示指定より優先される、という説明が無い。"""
        self.assert_phrase_absent(
            PLUGIN_README, PHRASE_CLAIMING_ENV_PRECEDES_AGENT_FRONTMATTER
        )


class CurrentRationalePresenceTest(ContractTestCase):
    """自前 reviewer を使う現在の理由 3 点が所定の位置に書かれていること。"""

    def test_reviewer_agent_bodies_state_the_no_spawn_clause(self) -> None:
        """両 agent body が `Agent` 除外を理由とする一文をそのまま含む。"""
        for path in REVIEWER_AGENTS:
            with self.subTest(agent=path.name):
                if normalize(NO_SPAWN_CLAUSE) not in normalize(agent_body(read(path))):
                    self.fail(
                        f"{path}: 次の一文を (太字等を挿入せず) そのまま含めること: "
                        f"{NO_SPAWN_CLAUSE}"
                    )

    def test_reviewer_agent_descriptions_state_the_three_reasons(self) -> None:
        """両 agent の description 1 行に理由 3 点の語がそろう。"""
        for path in REVIEWER_AGENTS:
            description = description_line(read(path))
            self.assert_scope_found(
                f"{path}", description, "frontmatter の `description:` 行が 1 行で無い"
            )
            for group, keywords in RATIONALE_KEYWORD_GROUPS:
                with self.subTest(agent=path.name, group=group):
                    self.assert_keywords_present(
                        f"{path} の description", description, group, keywords
                    )

    def test_review_command_rationale_states_the_three_reasons(self) -> None:
        """command の「標準 skill を直接呼ばない理由」の項目に理由 3 点の語がそろう。"""
        item = list_item_containing(
            read(REVIEW_COMMAND), REVIEW_COMMAND_RATIONALE_MARKER
        )
        self.assert_scope_found(
            f"{REVIEW_COMMAND}",
            item,
            f"目印「{REVIEW_COMMAND_RATIONALE_MARKER}」を含む箇条書きが無い",
        )
        for group, keywords in RATIONALE_KEYWORD_GROUPS:
            with self.subTest(group=group):
                self.assert_keywords_present(
                    f"{REVIEW_COMMAND} の理由節", item, group, keywords
                )

    def test_readme_agents_section_states_the_three_reasons(self) -> None:
        """README の `### Agents` 節で理由 3 点が項目・段落単位にそろう。

        3 点はそれぞれ 1 つの箇条書き項目 (または段落) の中で完結して書く。
        3 点が別々の項目に分かれているのは構わない。`Task` / `Agent` を tools から
        外す理由もこの 3 点目 (`Agent` 除外による read-only 維持) として書く。
        """
        section = markdown_section(read(PLUGIN_README), README_AGENTS_HEADING)
        self.assert_scope_found(
            f"{PLUGIN_README}", section, f"`{README_AGENTS_HEADING}` 節の本文が無い"
        )
        units = cooccurrence_units(section)
        for group, keywords in RATIONALE_KEYWORD_GROUPS:
            with self.subTest(group=group):
                self.assert_keywords_cooccur(
                    f"{PLUGIN_README} の {README_AGENTS_HEADING} 節",
                    units,
                    group,
                    keywords,
                )

    def test_auto_mark_skill_detection_comment_cites_lifecycle_hooks(self) -> None:
        """auto-mark.sh の Skill 検知コメントが lifecycle event 2 つを名指しする。"""
        block = shell_comment_block_containing(
            read(AUTO_MARK), AUTO_MARK_SKILL_DETECTION_MARKER
        )
        self.assert_scope_found(
            f"{AUTO_MARK}",
            block,
            f"目印「{AUTO_MARK_SKILL_DETECTION_MARKER}」を含むコメント段落が無い",
        )
        self.assert_keywords_present(
            f"{AUTO_MARK} の Skill 検知コメント",
            block,
            "lifecycle hook による完了検知",
            AUTO_MARK_SKILL_DETECTION_KEYWORDS,
        )


class ReadmeModelResolutionTest(ContractTestCase):
    """README が述べる subagent の model 解決順序。

    env が明示指定より優先されるという説明の不在は
    `ExpiredRationaleAbsenceTest` が README 全体で検査する。
    """

    def test_readme_marker_section_states_the_model_resolution_order(self) -> None:
        """`### マーカーファイル` 節末尾の段落が env の位置づけを述べる。"""
        section = markdown_section(read(PLUGIN_README), README_MARKER_FILE_HEADING)
        self.assert_scope_found(
            f"{PLUGIN_README}",
            section,
            f"`{README_MARKER_FILE_HEADING}` 節の本文が無い",
        )
        blocks = paragraphs(section)
        last = blocks[-1] if blocks else ""
        self.assert_keywords_present(
            f"{PLUGIN_README} の {README_MARKER_FILE_HEADING} 節末尾の段落",
            last,
            "model 解決順序",
            README_MODEL_RESOLUTION_KEYWORDS,
        )


if __name__ == "__main__":
    unittest.main()

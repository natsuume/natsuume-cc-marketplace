"""pre-push-codex-review:codex-reviewer の background-move 回収契約テスト。

Bash tool は timeout 時に wrapper 実行を kill せず background へ移行させ、その実行の
出力先 file path を結果に載せる。codex-reviewer subagent は移行後の出力をこの output
file の `Read` だけで回収し、既存の parent-safe report 契約へ normalize する。

本ファイルは、その回収契約を成す一文 (canonical 文) を module 定数として固定し、
`## Background-move recovery` セクション内に空白正規化した上で存在することを検証する。
pre-merge-codex-review 側の codex-reviewer も同じ回収契約に従うため、両 agent で共通の
一文は `SHARED_RECOVERY_CLAUSES` として公開し、pre-merge 側の契約テストが再利用する
(gate 名を含む resume 時の一文だけが plugin ごとに異なる)。
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-push-codex-review"
CODEX_REVIEWER = PLUGIN / "agents" / "codex-reviewer.md"
PLUGIN_README = PLUGIN / "README.md"
ROOT_README = ROOT / "README.md"

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

TOOLS_LINE = "tools: Bash, Read"
TOOL_GRANT_LITERAL = "`Bash, Read`"

# background 移行の分類 (回収経路と非ゼロ exit 経路の排他).
PRECEDENCE_SENTENCE = (
    "A background move is not by itself a wrapper failure: when the Bash "
    "result reports the run was moved to the background, follow this "
    "recovery section instead of the non-zero-exit path."
)
# 移行検知の直後に何を記録するか (出所付きの完全命令文).
RECORD_SENTENCE = (
    "When the Bash result reports that the wrapper run was moved to the "
    "background, immediately record the output file path that the "
    "background-move result surfaces."
)
SECOND_RUN_SENTENCE = "Do not start a second wrapper run"
# 回収手段 (Read による recorded output file の再読).
READ_RECOVERY_SENTENCE = (
    "Recover by Reading the recorded output file of that same background "
    "run, repeating until the file reaches a terminal state or the recovery "
    "budget is exhausted."
)
# path の出所要件 (同一 run 由来であれば、どの step が surface した path でもよい).
PATH_PROVENANCE_SENTENCE = (
    "Any step of this recovery may surface the output file path; use it as "
    "long as it belongs to the same background run."
)
# 回収 report 本文の正本 (独立した re-review で補完しない).
SOURCE_OF_TRUTH_SENTENCE = (
    "The recorded output file is the sole source of the recovered report "
    "body: normalize what you Read and never complete it by re-reviewing "
    "the diff yourself."
)
# terminal 判定の基準 (wrapper の終了マーカー).
TERMINAL_MARKER_SENTENCE = (
    "The output file has reached a terminal state only when its tail "
    "carries the wrapper's completion marker — the wrapper's final status "
    "line, which reports either the review record it wrote or the failure "
    "that stopped it."
)
# 回収予算 (Read 回数の上限と再読の間隔).
BUDGET_DEFINITION_SENTENCE = (
    "For the initial automatic recovery, make at most five Read calls on "
    "that output file, and wait between two consecutive Reads with a single "
    "Bash `sleep 60` command."
)
# 回収成功後の report 契約への handoff.
HANDOFF_SENTENCE = (
    "Once the recovered output reaches a terminal state, normalize it "
    "through the report contract below: a successful review yields "
    "`Status: pass` or `Status: findings`, and a failed wrapper run yields "
    "`Status: execution-failed`."
)
# background 移行が起きなかった場合 (Read 経路に入らない).
NO_MOVE_SENTENCE = (
    "If the Bash result did not report a background move, its stdout is the "
    "report body and this recovery section does not apply; do not Read the "
    "output file in that case."
)
# Read の適用範囲.
TOOL_SCOPE_SENTENCE = (
    "Read only the recorded output file of this same background run; do not "
    "read other files or independently re-review the diff."
)
# 境界: output file path が得られなかった.
NO_PATH_SENTENCE = (
    "If the background-move result surfaced no output file path, return "
    "`Status: execution-failed` (failure class `other`)."
)
# 境界: recorded output file が存在しない.
MISSING_FILE_SENTENCE = (
    "If the recorded output file does not exist when you Read it, return "
    "`Status: execution-failed` (failure class `other`) without further "
    "Reads."
)
# 境界: recorded output file が空 (終了マーカー未到達として再読継続).
EMPTY_FILE_SENTENCE = (
    "If the recorded output file is empty (zero bytes), it has not reached "
    "the completion marker: keep Reading it within the recovery budget."
)
# 境界: 回収予算の超過.
BUDGET_SENTENCE = (
    "If the recovery budget is exhausted before the output file reaches a "
    "terminal state, return `Status: execution-failed` (failure class "
    "`other`), state in the recovery direction that the codex review is "
    "likely still running in the background, and note that the parent may "
    "resume this same subagent for a diagnostic status check only."
)
# 境界: 回収対象の run を見失った.
LOST_RUN_SENTENCE = (
    "If you can no longer tell which wrapper run the recorded output file "
    "belongs to, return `Status: execution-failed` (failure class `other`)."
)
# resume 後の status check の位置づけ (plugin ごとに gate 名が異なる).
RESUME_CHECK_SENTENCE = (
    "A resumed status check is a single bounded Read of the recorded output "
    "file outside the initial recovery budget; it is diagnostic only and "
    "can never promote the codex-reviewed marker — satisfying the push gate "
    "requires a fresh reviewer run."
)

# 両 plugin の codex-reviewer が共有する回収契約の一文。pre-merge 側の契約テストが
# import して同じ一文を検証する。
SHARED_RECOVERY_CLAUSES = {
    "precedence": PRECEDENCE_SENTENCE,
    "record-output-file-path": RECORD_SENTENCE,
    "no-second-run": SECOND_RUN_SENTENCE,
    "read-recovery": READ_RECOVERY_SENTENCE,
    "path-provenance": PATH_PROVENANCE_SENTENCE,
    "source-of-truth": SOURCE_OF_TRUTH_SENTENCE,
    "terminal-marker": TERMINAL_MARKER_SENTENCE,
    "budget-definition": BUDGET_DEFINITION_SENTENCE,
    "report-contract-handoff": HANDOFF_SENTENCE,
    "no-background-move": NO_MOVE_SENTENCE,
    "read-scope": TOOL_SCOPE_SENTENCE,
    "boundary-no-output-path": NO_PATH_SENTENCE,
    "boundary-missing-output-file": MISSING_FILE_SENTENCE,
    "boundary-empty-output-file": EMPTY_FILE_SENTENCE,
    "boundary-budget-exhausted": BUDGET_SENTENCE,
    "boundary-lost-run": LOST_RUN_SENTENCE,
}

RECOVERY_HEADING = "## Background-move recovery"

FENCE_OPEN_PATTERN = re.compile(r"^(`{3,}|~{3,})")
# 回収セクションを終端する見出し: 同レベル (`## `) と上位 (`# `)。
SECTION_END_PATTERN = re.compile(r"^#{1,2} ")


class UnclosedFenceError(ValueError):
    """markdown 本文に閉じていないコード fence があることを示す。"""


def recovery_section(body: str) -> str:
    """`## Background-move recovery` セクションを fence 追跡 scanner で抽出する。

    行単位で走査し、行頭の空白を除去した後に ``` または ~~~ 以上の run で始まる行を
    fence の開始とみなし、その delimiter 文字 (backtick/tilde) と run 長を記録する。
    fence の終了は、同一の delimiter 文字が開始 run 長以上連続し、その後が空白のみで
    終わる行に限定する。fence 外で行全体が `## Background-move recovery` に一致する行を
    起点、それ以降の fence 外で `# ` または `## ` で始まる最初の行を終点とし、その間
    (起点行含む・終点行含まず) を返す。fence 内の見出し様の行は起点・終点の判定から
    除外される。

    本文全体を走査し終えても閉じていない fence が残る場合は `UnclosedFenceError` を
    送出する (閉じ忘れた fence が以降の見出しを飲み込み、セクションが実際より長く
    切り出されるのを検出不能なまま通さないため)。見出しが無ければ空文字列を返す。
    """
    lines = body.splitlines(keepends=True)
    in_fence = False
    fence_char = ""
    fence_len = 0
    start_index: int | None = None
    end_index: int | None = None
    for index, line in enumerate(lines):
        content = line.rstrip("\n")
        stripped = content.lstrip()
        if in_fence:
            run_len = 0
            while run_len < len(stripped) and stripped[run_len] == fence_char:
                run_len += 1
            if run_len >= fence_len and stripped[run_len:].strip() == "":
                in_fence = False
            continue
        open_match = FENCE_OPEN_PATTERN.match(stripped)
        if open_match:
            run = open_match.group(1)
            fence_char = run[0]
            fence_len = len(run)
            in_fence = True
            continue
        if start_index is None:
            if content == RECOVERY_HEADING:
                start_index = index
            continue
        if end_index is None and SECTION_END_PATTERN.match(content):
            end_index = index
    if in_fence:
        raise UnclosedFenceError("unclosed code fence in markdown body")
    if start_index is None:
        return ""
    if end_index is None:
        return "".join(lines[start_index:])
    return "".join(lines[start_index:end_index])


def normalize(text: str) -> str:
    """空白・改行を単一スペースに正規化する (md の 80 桁前後の折り返し差異を吸収する)。"""
    return " ".join(text.split())


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalized_recovery_section(path: Path) -> str:
    """agent file の回収セクションを空白正規化して返す。セクション不在なら失敗する。"""
    section = recovery_section(read(path))
    if not section:
        raise AssertionError(
            f"{path}: '{RECOVERY_HEADING}' section not found"
        )
    return normalize(section)


class ContractTestCase(unittest.TestCase):
    """md / mjs の文言契約を、失敗時に全文を出力せずに検証する assertion helper。

    対象ファイルは数百行あるため、`assertIn` / `assertNotIn` の既定メッセージでは
    失敗理由 (どの一文が欠けているか / どの行が残っているか) が全文に埋もれる。
    """

    def assert_text_contains(self, path: Path, needle: str) -> None:
        if needle not in read(path):
            self.fail(f"{path}: 期待する文字列が無い: {needle}")

    def assert_text_absent(self, path: Path, needle: str) -> None:
        hits = [
            f"L{number}: {line.strip()[:120]}"
            for number, line in enumerate(read(path).splitlines(), start=1)
            if needle in line
        ]
        if hits:
            joined = " / ".join(hits)
            self.fail(f"{path}: {needle} の言及が残っている: {joined}")

    def assert_recovery_clause(self, path: Path, sentence: str) -> None:
        expected = normalize(sentence)
        if expected not in normalized_recovery_section(path):
            self.fail(f"{path}: 回収契約の一文が欠けている: {expected}")

    def assert_tools_line(self, path: Path) -> None:
        match = FRONTMATTER_PATTERN.match(read(path))
        if match is None:
            self.fail(f"{path}: frontmatter が無い")
        tools_lines = [
            line
            for line in match.group(1).splitlines()
            if line.startswith("tools:")
        ]
        self.assertEqual(len(tools_lines), 1, f"{path}: {tools_lines}")
        self.assertEqual(tools_lines[0], TOOLS_LINE, str(path))


class CodexReviewerToolGrantTest(ContractTestCase):
    """回収に使うツールの公開契約 (frontmatter の tools 行)。"""

    def test_tools_frontmatter_grants_bash_and_read(self) -> None:
        self.assert_tools_line(CODEX_REVIEWER)

    def test_agent_body_never_mentions_task_output(self) -> None:
        self.assert_text_absent(CODEX_REVIEWER, "TaskOutput")

    def test_agent_body_does_not_deny_read(self) -> None:
        """Read を許可しない旨に読める文言が本文に無い (tools 行との矛盾を防ぐ)。"""
        normalized_body = normalize(read(CODEX_REVIEWER))
        for wording in (
            "**Do not invoke other tools.** Only the `Bash` tool to start the "
            "wrapper.",
            "This subagent's `tools` field grants `Bash` only — Read / Edit / "
            "Write / Skill / Task are all disallowed.",
            "Do not read files or independently analyze the diff",
        ):
            with self.subTest(wording=wording):
                if normalize(wording) in normalized_body:
                    self.fail(
                        f"{CODEX_REVIEWER}: Read 不可に読める文言が残っている: "
                        f"{wording}"
                    )


class CodexReviewerBackgroundMoveRecoveryTest(ContractTestCase):
    """`## Background-move recovery` セクションが固定すべき回収契約の一文。"""

    def assert_clause(self, sentence: str) -> None:
        self.assert_recovery_clause(CODEX_REVIEWER, sentence)

    def test_background_move_takes_precedence_over_error_path(self) -> None:
        self.assert_clause(PRECEDENCE_SENTENCE)

    def test_recovery_section_records_output_file_path(self) -> None:
        self.assert_clause(RECORD_SENTENCE)

    def test_recovery_forbids_second_wrapper_run(self) -> None:
        self.assert_clause(SECOND_RUN_SENTENCE)

    def test_recovery_reads_recorded_output_file(self) -> None:
        self.assert_clause(READ_RECOVERY_SENTENCE)

    def test_output_file_path_from_any_step_of_same_run_is_usable(self) -> None:
        self.assert_clause(PATH_PROVENANCE_SENTENCE)

    def test_recovered_report_body_comes_from_the_output_file(self) -> None:
        self.assert_clause(SOURCE_OF_TRUTH_SENTENCE)

    def test_terminal_state_is_defined_by_completion_marker(self) -> None:
        self.assert_clause(TERMINAL_MARKER_SENTENCE)

    def test_recovery_budget_bounds_reads_and_sleep_interval(self) -> None:
        self.assert_clause(BUDGET_DEFINITION_SENTENCE)

    def test_recovered_terminal_state_routes_through_report_contract(self) -> None:
        self.assert_clause(HANDOFF_SENTENCE)

    def test_absent_background_move_keeps_bash_stdout_as_report(self) -> None:
        self.assert_clause(NO_MOVE_SENTENCE)

    def test_read_scope_limited_to_recorded_output_file(self) -> None:
        self.assert_clause(TOOL_SCOPE_SENTENCE)

    def test_boundary_no_output_file_path_ends_execution_failed(self) -> None:
        self.assert_clause(NO_PATH_SENTENCE)

    def test_boundary_missing_output_file_ends_execution_failed(self) -> None:
        self.assert_clause(MISSING_FILE_SENTENCE)

    def test_boundary_empty_output_file_keeps_reading(self) -> None:
        self.assert_clause(EMPTY_FILE_SENTENCE)

    def test_boundary_budget_exhausted_reports_still_running(self) -> None:
        self.assert_clause(BUDGET_SENTENCE)

    def test_boundary_lost_run_ends_execution_failed(self) -> None:
        self.assert_clause(LOST_RUN_SENTENCE)

    def test_resumed_status_check_is_bounded_and_diagnostic_only(self) -> None:
        self.assert_clause(RESUME_CHECK_SENTENCE)


class CodexReviewerDocumentationTest(ContractTestCase):
    """README が現在の tool grant と起動仕様を説明する。"""

    def test_plugin_readme_documents_current_tool_grant(self) -> None:
        self.assert_text_absent(PLUGIN_README, "TaskOutput")
        self.assert_text_contains(PLUGIN_README, TOOL_GRANT_LITERAL)

    def test_plugin_readme_omits_agent_launch_parameter(self) -> None:
        self.assert_text_absent(PLUGIN_README, "run_in_background")

    def test_root_readme_documents_current_tool_grant(self) -> None:
        self.assert_text_absent(ROOT_README, "Bash, TaskOutput, Read")
        self.assert_text_contains(ROOT_README, TOOL_GRANT_LITERAL)


class RecoverySectionHelperTest(unittest.TestCase):
    """`recovery_section` の抽出規則 (fence 追跡・終端見出し・未閉 fence)。"""

    def test_helper_ignores_headings_inside_fences(self) -> None:
        fake_body = (
            "# Fake agent\n\n"
            "## Intro\n\n"
            "```markdown\n"
            f"{RECOVERY_HEADING}\n"
            "this fenced heading must not be treated as the real section start\n"
            "```\n\n"
            f"{RECOVERY_HEADING}\n\n"
            "Real section content.\n\n"
            "```text\n"
            "## fenced heading inside the real section must not end it\n"
            "```\n\n"
            "More real content after the fenced block.\n\n"
            "1. A list item with an indented fenced block:\n\n"
            "   ```text\n"
            "## indented fence must stay open despite this unindented "
            "heading-like line\n"
            "   ```\n\n"
            "Content after the indented fenced block.\n\n"
            "````text\n"
            "```\n"
            "## a heading-like line inside the four-backtick fence must "
            "not end it\n"
            "````\n\n"
            "Content after the four-backtick fenced block.\n\n"
            "~~~text\n"
            "```\n"
            "## a heading-like line inside the tilde fence must not end it\n"
            "~~~\n\n"
            "Content after the tilde fenced block.\n\n"
            "## Next section\n\n"
            "Unrelated trailing content.\n"
        )
        section = recovery_section(fake_body)
        self.assertNotEqual(section, "")
        self.assertNotIn(
            "this fenced heading must not be treated as the real section start",
            section,
        )
        self.assertIn("Real section content.", section)
        self.assertIn(
            "fenced heading inside the real section must not end it", section
        )
        self.assertIn("More real content after the fenced block.", section)
        self.assertIn(
            "indented fence must stay open despite this unindented "
            "heading-like line",
            section,
        )
        self.assertIn("Content after the indented fenced block.", section)
        self.assertIn(
            "a heading-like line inside the four-backtick fence must not "
            "end it",
            section,
        )
        self.assertIn("Content after the four-backtick fenced block.", section)
        self.assertIn(
            "a heading-like line inside the tilde fence must not end it",
            section,
        )
        self.assertIn("Content after the tilde fenced block.", section)
        self.assertNotIn("Unrelated trailing content.", section)

    def test_helper_ends_section_at_same_level_heading(self) -> None:
        fake_body = (
            f"{RECOVERY_HEADING}\n\n"
            "Real section content.\n\n"
            "## Parent-safe report contract\n\n"
            "Unrelated trailing content.\n"
        )
        section = recovery_section(fake_body)
        self.assertIn("Real section content.", section)
        self.assertNotIn("Unrelated trailing content.", section)

    def test_helper_ends_section_at_higher_level_heading(self) -> None:
        fake_body = (
            f"{RECOVERY_HEADING}\n\n"
            "Real section content.\n\n"
            "# Appendix\n\n"
            "Unrelated trailing content.\n"
        )
        section = recovery_section(fake_body)
        self.assertIn("Real section content.", section)
        self.assertNotIn("Unrelated trailing content.", section)

    def test_helper_keeps_deeper_headings_inside_the_section(self) -> None:
        fake_body = (
            f"{RECOVERY_HEADING}\n\n"
            "Real section content.\n\n"
            "### Recovery boundaries\n\n"
            "Boundary content belongs to the section.\n"
        )
        section = recovery_section(fake_body)
        self.assertIn("Boundary content belongs to the section.", section)

    def test_helper_rejects_unclosed_fence(self) -> None:
        fake_body = (
            f"{RECOVERY_HEADING}\n\n"
            "Real section content.\n\n"
            "```text\n"
            "an unterminated fenced block swallows the rest of the document\n\n"
            "## Parent-safe report contract\n\n"
            "Unrelated trailing content.\n"
        )
        with self.assertRaises(UnclosedFenceError):
            recovery_section(fake_body)

    def test_helper_returns_empty_string_when_section_is_absent(self) -> None:
        fake_body = "# Fake agent\n\n## Intro\n\nNo recovery section here.\n"
        self.assertEqual(recovery_section(fake_body), "")

    def test_missing_section_fails_with_identifying_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            agent = Path(temporary) / "codex-reviewer.md"
            agent.write_text(
                "# Fake agent\n\n## Intro\n\nNo recovery section here.\n",
                encoding="utf-8",
            )
            with self.assertRaises(AssertionError) as raised:
                normalized_recovery_section(agent)
        message = str(raised.exception)
        self.assertIn(RECOVERY_HEADING, message)
        self.assertIn("not found", message)
        self.assertIn(str(agent), message)


if __name__ == "__main__":
    unittest.main()

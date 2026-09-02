"""pre-merge-codex-review の findings 判定契約テスト。

判定ロジックは `plugins/pre-merge-codex-review/hooks/scripts/lib/review-status.sh` の
`detect_review_status` が担う。

固定する契約:

- 関数: `detect_review_status <report-file>`。stdout に `pass` または `findings` を
  改行なしで出力し、常に return 0 する。ファイルが存在しない・読めない場合も
  `findings` を出力する (判定できない場合は findings に倒す)。
- 判定対象は report の末尾 10 行。
- 末尾 10 行に `## Finding` / `- Severity:` / 前後が英数字でない `P0`〜`P3` のいずれか
  が 1 行でも含まれれば、他の条件に関わらず findings。
- finding 記述が無い場合、各行を (1) 行頭の markdown 装飾除去、(2) 強調記号
  `*`/`_` 除去、(3) 行末の `.`/`!`/空白除去、(4) 小文字化、の順で正規化し、
  「no findings」「no material findings」「no actionable findings」
  「no issues found/identified」「no regression(s) found/identified」といった表現に
  行全体が一致するか、行の末尾がそれらに一致すれば (直前が行頭または非英字) pass。
- 空 report・末尾 10 行が空白のみ・上記に一致しない結論はすべて findings。

テストは一時ディレクトリに report ファイルを書き、
`bash -c 'source <lib>; detect_review_status "$1"' _ <report-file>` の形で関数を単体
実行し、stdout を検証する。
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = (
    ROOT
    / "plugins"
    / "pre-merge-codex-review"
    / "hooks"
    / "scripts"
    / "lib"
    / "review-status.sh"
)


@unittest.skipUnless(shutil.which("bash"), "requires bash")
class DetectReviewStatusContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.work = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def write_report(self, text: str, *, name: str = "report.md") -> Path:
        path = self.work / name
        path.write_text(text, encoding="utf-8")
        return path

    def run_detect(self, report_path: Path) -> str:
        script = f"source {shlex.quote(str(LIB))}; detect_review_status \"$1\""
        result = subprocess.run(
            ["bash", "-c", script, "_", str(report_path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout.decode()

    def assert_status(self, text: str, expected: str, *, name: str = "report.md") -> None:
        report = self.write_report(text, name=name)
        self.assertEqual(self.run_detect(report), expected)

    # ------------------------------------------------------------------
    # (a) lib の骨格契約
    # ------------------------------------------------------------------

    def test_lib_file_exists(self) -> None:
        self.assertTrue(LIB.is_file(), f"missing lib: {LIB}")

    # ------------------------------------------------------------------
    # (b) 実 report fixture: 2026-09-02 に投稿された report の末尾文を模す
    # ------------------------------------------------------------------

    def test_real_report_fixture_trailing_actionable_regression_clause(self) -> None:
        report = (
            "# Codex Review\n"
            "\n"
            "## Summary\n"
            "\n"
            "This PR bumps the plugin manifest version and updates the hook "
            "scripts for pre-merge-codex-review.\n"
            "\n"
            "## Analysis\n"
            "\n"
            "The version bump in `plugin.json` and `marketplace.json` matches, "
            "and the new hook wiring is consistent with the existing "
            "`hooks.json` schema.\n"
            "\n"
            "## Conclusion\n"
            "\n"
            "The changes to the plugin manifest and hook scripts are "
            "internally consistent. Syntax and JSON validation also passed, "
            "with no actionable regression identified.\n"
        )
        self.assert_status(report, "pass")

    # ------------------------------------------------------------------
    # (c) 既存 3 表現が単独行として末尾にある
    # ------------------------------------------------------------------

    def test_legacy_single_line_expressions(self) -> None:
        cases = {
            "no_material_findings": "No material findings.",
            "no_findings": "No findings",
            "no_issues_found": "No issues found.",
        }
        for label, last_line in cases.items():
            with self.subTest(case=label):
                report = (
                    "# Codex Review\n"
                    "\n"
                    "Reviewed the diff for correctness issues.\n"
                    "\n"
                    f"{last_line}\n"
                )
                self.assert_status(report, "pass", name=f"{label}.md")

    # ------------------------------------------------------------------
    # (d) markdown 装飾付きの表現
    # ------------------------------------------------------------------

    def test_decorated_expressions(self) -> None:
        cases = {
            "bold_bullet": "- **No actionable findings.**",
            "blockquote": "> No regressions identified",
            "numbered_list": "1. No issues identified!",
        }
        for label, last_line in cases.items():
            with self.subTest(case=label):
                report = f"# Codex Review\n\n{last_line}\n"
                self.assert_status(report, "pass", name=f"{label}.md")

    # ------------------------------------------------------------------
    # (e) 末尾節形 (行末が結論表現に一致)
    # ------------------------------------------------------------------

    def test_trailing_clause_forms(self) -> None:
        cases = {
            "overall_clause": (
                "Overall the diff looks correct with no material issues found."
            ),
            "semicolon_clause": "Reviewed all files; no findings.",
        }
        for label, last_line in cases.items():
            with self.subTest(case=label):
                report = f"# Codex Review\n\n{last_line}\n"
                self.assert_status(report, "pass", name=f"{label}.md")

    def test_negated_or_uncertain_trailing_clause_is_not_pass(self) -> None:
        """否定・不確実な結論は末尾が「no ...」形でも pass にしない。"""
        cases = {
            "cannot_establish": (
                "We cannot establish that there are no regressions."
            ),
            "could_not_verify": "I could not verify that there are no findings.",
            "contraction": "Couldn't confirm there are no issues found.",
            "unclear_whether": "It is unclear whether there are no issues.",
            "conditional": "This is fine if there are no material findings.",
            "not_confident": "I am not confident there are no regressions.",
            "modal_may": "There may be no regressions.",
            "modal_should": "This should have no issues.",
            "pending_verification": (
                "Further testing is needed to confirm no regressions."
            ),
            "seems": "It seems there are no findings.",
        }
        for label, last_line in cases.items():
            with self.subTest(case=label):
                report = f"# Codex Review\n\n{last_line}\n"
                self.assert_status(report, "findings", name=f"{label}.md")

    # ------------------------------------------------------------------
    # (f) 末尾から 6〜10 行目にある場合 (旧 5 行では取りこぼす位置) → pass
    # ------------------------------------------------------------------

    def test_no_finding_line_within_extended_tail_window(self) -> None:
        report = (
            "# Codex Review\n"
            "\n"
            "## Summary\n"
            "\n"
            "This PR only touches documentation and version bump metadata.\n"
            "\n"
            "## Analysis\n"
            "\n"
            "Reviewed all changed files line by line for correctness and "
            "regression risk.\n"
            "\n"
            "No material findings.\n"
            "\n"
            "## Verdict\n"
            "\n"
            "Nothing else to add here.\n"
            "\n"
            "Thanks for reviewing.\n"
        )
        self.assert_status(report, "pass")

    # ------------------------------------------------------------------
    # (g) 「指摘なし」 行が末尾 11 行目以前にしかない場合 → findings
    # ------------------------------------------------------------------

    def test_no_finding_line_before_extended_tail_window(self) -> None:
        report = (
            "# Codex Review\n"
            "\n"
            "## Analysis\n"
            "\n"
            "No material findings.\n"
            "\n"
            "## Additional Notes\n"
            "\n"
            "This section discusses something unrelated to the verdict.\n"
            "\n"
            "It elaborates further without concluding anything.\n"
            "\n"
            "## Verdict\n"
            "\n"
            "Still reviewing edge cases before finalizing.\n"
            "End of report.\n"
        )
        self.assert_status(report, "findings")

    # ------------------------------------------------------------------
    # (h) finding 記述 (## Finding / - Severity:) が末尾 10 行にあれば
    #     「指摘なし」 結論が続いても findings が優先する
    # ------------------------------------------------------------------

    def test_finding_section_overrides_no_finding_conclusion(self) -> None:
        report = (
            "# Codex Review\n"
            "\n"
            "## Finding CODEX-1\n"
            "\n"
            "- Severity: P2\n"
            "- Description: potential null dereference in the parser.\n"
            "\n"
            "No other issues found.\n"
        )
        self.assert_status(report, "findings")

    # ------------------------------------------------------------------
    # (i) P[0-3] 優先度表記だけがあっても findings が優先する
    # ------------------------------------------------------------------

    def test_priority_marker_alone_overrides_no_finding_conclusion(self) -> None:
        report = (
            "# Codex Review\n"
            "\n"
            "**P1** Null dereference in parser\n"
            "\n"
            "No further findings.\n"
        )
        self.assert_status(report, "findings")

    # ------------------------------------------------------------------
    # (j) P2P のように英数字が続く表記は優先度表記とみなさない → pass
    # ------------------------------------------------------------------

    def test_p_digit_followed_by_alnum_is_not_priority_marker(self) -> None:
        report = "Uses P2P transport; no issues found.\n"
        self.assert_status(report, "pass")

    # ------------------------------------------------------------------
    # (k) 空 report・空白のみ・非該当結論 → findings
    # ------------------------------------------------------------------

    def test_empty_report_falls_back_to_findings(self) -> None:
        self.assert_status("", "findings", name="empty.md")

    def test_whitespace_only_report_falls_back_to_findings(self) -> None:
        self.assert_status("   \n\n\t\n", "findings", name="whitespace.md")

    def test_non_matching_conclusion_falls_back_to_findings(self) -> None:
        self.assert_status("Looks good to me.\n", "findings", name="no-match.md")

    # ------------------------------------------------------------------
    # (l) 存在しないファイル path → findings
    # ------------------------------------------------------------------

    def test_missing_report_file_falls_back_to_findings(self) -> None:
        missing = self.work / "does-not-exist.md"
        self.assertEqual(self.run_detect(missing), "findings")


if __name__ == "__main__":
    unittest.main()

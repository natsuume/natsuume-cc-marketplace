"""pre-push-codex-review:codex-reviewer の background-move 回収契約テスト (issue #337)。

`pre-push-codex-review:codex-reviewer` subagent は tools が `Bash` のみのため、Claude Code
の Bash tool が timeout 時にプロセスを kill せず background へ自動移行させる現仕様下
では、移行後の出力を回収する手段が構造的に無い。Codex review 本体は完走している
のに subagent は正規 report (`Status: pass|findings`) を返せない。

修正は (1) tools を `Bash, TaskOutput, Read` に拡張、(2) background 移行時の回収
手順を手順書に明記、(3) 境界 4 種 (task ID 喪失・truncated かつ補完不能・回収予算
超過・task 見失い) で `Status: execution-failed` 終了、(4) 既存 report 契約と
single-run 契約は不変。

本テストは spec-first Phase A で実装前の red テストとして追加する。Phase B で
`plugins/pre-push-codex-review/agents/codex-reviewer.md` が改訂されると green になる。

本改訂は、初版に対するレビュー指摘 (assert が非識別的で無関係な既存文言にも
誤マッチしうる、境界 3 種が個別に固定されておらず 1 つの assert で束ねられていた、
回収手順の意味論 — poll 継続条件・truncated 時の Read 補完・境界時の report 記述 —
が一文単位で契約化されていなかった) に対応し、Phase B で md に書かれるべき canonical
な一文をモジュール定数として固定した上で、`## Background-move recovery` セクション
内に空白正規化した上でその一文が存在することを個別の assert で検証する形に再構成した。

さらに 2 回目のレビュー指摘 (poll 継続条件が回収予算の超過を終了条件として含まず
無限 poll を許す抜け穴になっていた、予算超過時の report 記述に手動確認への案内が
欠けていた) に対応し、初回自動回収の予算 (TaskOutput call 回数の上限 × 各 call の
timeout 上限) と、resume 後に行う単発の bounded な status check を canonical な
一文として固定した。POLL_SENTENCE の終了条件に予算超過を明記し、BUDGET_SENTENCE
に手動確認経路 (この同じ subagent を focused な status-check question で resume
する) の案内を統合した。

3 回目のレビュー指摘 (rescue 壁打ちの結果を含む) は次の 3 点だった: (i) output
file path 単独の喪失を task ID 喪失と同列の即時 terminal 条件にしていたのは
過剰で、path は truncated 時の補完手段が失敗した場合にのみ terminal 化すべき
(path の optional 降格)、(ii) 回収予算の定義が 60 分という background-shell の
全体制限からの逆算算術になっており、TaskOutput の実際の制約 (1 call あたりの
最大 timeout) から独立して固定すべき、(iii) `recovery_section` の見出し検出が
単純な文字列探索で、コード fence 内に偶然含まれる `## Background-move recovery`
や `## ` 行を誤って起点・終点と扱いうる。これに対応し、LOST_SENTENCE を task ID
単独喪失に限定し、TRUNCATED_SENTENCE に「truncated かつ output file path が
未捕捉または読み取り不能」の組合せのみを terminal とする分岐を統合し、
BUDGET_DEFINITION_SENTENCE を TaskOutput の実際の per-call 最大 timeout (10 分)
基準の固定値に置き換え、`recovery_section` を fence 追跡 scanner に書き換えて
合成 markdown による回帰テストを追加した。

4 回目のレビュー指摘 (rescue 壁打ちの結果を含む) は次の 3 点だった: (i) tools
frontmatter を `Bash, TaskOutput, Read` へ拡張したにもかかわらず、`Read` の
使途を「truncated 時の回収 artifact の補完」に限定する契約が本文に無く、
無制限の Read 権限に読めてしまう、(ii) 旧 Bash-only 設計時代の「他ツール禁止」
「`Bash` のみが許可ツール」を示す複数の言い回しが個別に潰されておらず、
tools 拡張後の本文に矛盾する残存文言が生き残りうる、(iii) `recovery_section`
の fence 判定がインデント無しの ``` 前提で、リスト項目内などインデント付き
fence を fence として認識できず、fence 内の見出し様の行を誤って起点・終点と
扱いうる。これに対応し、TOOL_SCOPE_SENTENCE で Read の使途を「同じ background
task の回収済み output file path のみ・truncated 時のみ」に限定する契約を新設し、
test_bash_only_wording_removed の negative assert を 5 本 (旧来の 2 本 + 新規 3 本)
に拡充し、`recovery_section` の fence 判定を行頭空白除去後の ``` 判定に変更して
インデント付き fence にも対応した上で、合成 markdown の回帰テストにリスト項目内の
インデント付き fence ケースを追加した。

5 回目のレビュー指摘と advisor checkpoint (rescue 壁打ちに加え、Codex advisor
への根本方針確認) は次の 4 点だった: (i) BUDGET_SENTENCE / RESUME_CHECK_SENTENCE
の「手動確認経路」が、resume 後の TaskOutput 呼び出しを codex-reviewed marker
昇格の代替経路であるかのように読める余地があり、auto-mark.sh の attestation
lifecycle (単一 run の hash-bound pending attestation を、単一の SubagentStop
hook 呼び出しが正規 report を条件に昇格させる) と整合しない、(ii) background
移行と wrapper failure (non-zero exit) の優先順位が本文で固定されておらず、
両者が排他的に扱われるべきことが自明でない、(iii) tools frontmatter の検査が
`tools:` 行の完全一致 1 行のみを想定しており複数行や部分一致に対して脆弱、
(iv) 旧 Bash-only 文言の不在検査がフラグメント単位で、正当な絞り込み文言
(例: 新設した TOOL_SCOPE_SENTENCE の "do not read other files") に過剰マッチ
しうる。これに対応し、BUDGET_SENTENCE と RESUME_CHECK_SENTENCE を「resume は
診断専用であり marker を昇格できず、push gate を満たすには新規 reviewer run
が必要」と明記する文に改め、PRECEDENCE_SENTENCE で background 移行が
wrapper failure の non-zero-exit path と扱われないことを固定し、
test_tools_frontmatter_grants_recovery_tools を `tools:` 行の個数 1 + 完全
一致の 2 段検査に強化し、test_bash_only_wording_removed を normalize 済みの
旧文言 3 種 (完全文 2 種 + 7 語の連続句 1 種) の verbatim 不在検査に置き換えた。

6 回目のレビュー指摘 (rescue approve 済み) は次の 4 点だった: (i) background
移行の検知直後に「何を記録するか」を指す substring assert (`"moved to the
background"` / `"record the task ID and the output file path"`) は緩く、
記録指示がどの契機に紐づくかという出所 (Bash 結果が background 移行を報告
した直後、という条件) まで一文で固定していなかった、(ii) 回収予算超過後の
report と異なり、回収が成功して task が terminal state に達した後の出力を
既存の parent-safe report 契約 (`Status: pass` / `Status: findings` /
`Status: execution-failed`) へどう normalize するかという handoff が本文に
明記されていなかった、(iii) BUDGET_DEFINITION_SENTENCE が「10 分」という
具体的な分数を含んでおり、ツール仕様側の実際の上限値と将来ずれた場合に
テストと実装の二重管理になる、(iv) `recovery_section` の fence 追跡が
delimiter 文字と run 長を区別しておらず、4-backtick fence 内の 3-backtick
行や、tilde (`~~~`) fence 内の backtick 行を閉じ fence と誤認しうる。これに
対応し、RECORD_SENTENCE で記録指示を「Bash 結果が background 移行を報告した
直後」という出所付きの完全命令文として固定し (test_recovery_section_documents_
background_move を空でないこと + RECORD_SENTENCE 包含の 2 assert に単純化)、
HANDOFF_SENTENCE で回収後 terminal state からの report 契約 handoff を固定し、
PRECEDENCE_SENTENCE を「background 移行はそれ自体では wrapper failure ではない」
という move の分類のみに限定する文に改め、BUDGET_DEFINITION_SENTENCE から
分数の数値を除去し、`recovery_section` の fence 追跡を delimiter 文字
(backtick/tilde) と開始 run 長を記録し、閉じ fence を「同一文字・開始長以上の
run・run 後は空白のみ」に限定する厳密な scanner に書き換え、合成 markdown の
回帰テストに 4-backtick fence と tilde fence の 2 ケースを追加した。

この改訂をもって、issue #337 Phase A の red テスト契約を freeze する。以降の
変更は Phase B (`codex-reviewer.md` の実装) で green 化することを前提とし、
本ファイルへのさらなる contract 変更は新たな issue として起票する。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODEX_REVIEWER = (
    ROOT / "plugins" / "pre-push-codex-review" / "agents" / "codex-reviewer.md"
)

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

POLL_SENTENCE = (
    "Recover with TaskOutput (block=true) against the same task ID, "
    "repeating until the task reaches a terminal state "
    "or the recovery budget is exhausted"
)
SECOND_RUN_SENTENCE = "Do not start a second wrapper run"
TRUNCATED_SENTENCE = (
    "If the recovered report is truncated, complete it by Reading the "
    "recorded output file path; if that path was not captured or cannot "
    "be read, return `Status: execution-failed`."
)
LOST_SENTENCE = (
    "If you lost the task ID, return `Status: execution-failed`"
)
BUDGET_DEFINITION_SENTENCE = (
    "For the initial automatic recovery, make at most five TaskOutput "
    "calls, each with the tool's maximum timeout."
)
RECORD_SENTENCE = (
    "When the Bash result reports that the wrapper run was moved to the "
    "background, immediately record the task ID and the output file path "
    "that the background-move result surfaces."
)
HANDOFF_SENTENCE = (
    "Once the recovered run reaches a terminal state, normalize its "
    "output through the existing report contract: a successful review "
    "yields `Status: pass` or `Status: findings`, and a failed wrapper "
    "run yields `Status: execution-failed`."
)
BUDGET_SENTENCE = (
    "If the recovery budget is exhausted before the task reaches a terminal "
    "state, return `Status: execution-failed`, state in the recovery "
    "direction that the codex review is likely still running in the "
    "background, and note that the parent may resume this same subagent "
    "for a diagnostic status check only."
)
RESUME_CHECK_SENTENCE = (
    "A resumed status check is a single bounded TaskOutput call outside "
    "the initial recovery budget; it is diagnostic only and can never "
    "promote the codex-reviewed marker — satisfying the push gate "
    "requires a fresh reviewer run."
)
NOT_FOUND_SENTENCE = (
    "If TaskOutput reports that the task can no longer be found, "
    "return `Status: execution-failed`"
)
TOOL_SCOPE_SENTENCE = (
    "Use Read only when the recovered report is truncated, and only on "
    "the same background task's recorded output file path; do not read "
    "other files or independently re-review the diff."
)
PRECEDENCE_SENTENCE = (
    "A background move is not by itself a wrapper failure: when the Bash "
    "result reports the run was moved to the background, follow this "
    "recovery section instead of the non-zero-exit path."
)


FENCE_OPEN_PATTERN = re.compile(r"^(`{3,}|~{3,})")


def recovery_section(body: str) -> str:
    """`## Background-move recovery` セクションを fence 追跡 scanner で抽出する。

    行単位で走査し、行の先頭空白を除去した後に ``` または ~~~ 以上の run で
    始まる行を fence の開始とみなし、その delimiter 文字 (backtick/tilde) と
    run 長を記録する。fence の終了は、同一の delimiter 文字が開始 run 長以上
    連続し、その後が空白のみで終わる行に限定する (delimiter 文字が異なる、
    または run 長が開始未満の行では閉じない)。fence 外で行全体が
    `## Background-move recovery` に一致する行を起点、それ以降の fence 外で
    `## ` で始まる次の行を終点とし、その間 (起点行含む・終点行含まず) を返す。
    コード fence 内に偶然含まれる同名見出しや `## ` 行は起点・終点の判定から
    除外される。見出しが無ければ空文字列を返す。
    """
    marker = "## Background-move recovery"
    lines = body.splitlines(keepends=True)
    in_fence = False
    fence_char = ""
    fence_len = 0
    start_index: int | None = None
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
            if content == marker:
                start_index = index
            continue
        if content.startswith("## "):
            return "".join(lines[start_index:index])
    if start_index is None:
        return ""
    return "".join(lines[start_index:])


def normalize(text: str) -> str:
    """空白・改行を単一スペースに正規化する (md の 80 桁前後の折り返し差異を吸収する)。"""
    return " ".join(text.split())


class CodexReviewerBackgroundMoveRecoveryTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def frontmatter_lines(self, body: str) -> list[str]:
        match = FRONTMATTER_PATTERN.match(body)
        if match is None:
            return []
        return match.group(1).splitlines()

    def test_tools_frontmatter_grants_recovery_tools(self) -> None:
        body = self.read(CODEX_REVIEWER)
        lines = self.frontmatter_lines(body)
        tools_lines = [line for line in lines if line.startswith("tools:")]
        self.assertEqual(len(tools_lines), 1)
        self.assertEqual(tools_lines[0], "tools: Bash, TaskOutput, Read")

    def test_recovery_section_documents_background_move(self) -> None:
        body = self.read(CODEX_REVIEWER)
        section = recovery_section(body)
        self.assertNotEqual(section, "")
        normalized = normalize(section)
        self.assertIn(normalize(RECORD_SENTENCE), normalized)

    def test_recovered_terminal_state_routes_through_report_contract(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(HANDOFF_SENTENCE), normalized)

    def test_background_move_takes_precedence_over_error_path(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(PRECEDENCE_SENTENCE), normalized)

    def test_recovery_polls_same_task_to_terminal(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(POLL_SENTENCE), normalized)

    def test_recovery_forbids_second_wrapper_run(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(SECOND_RUN_SENTENCE), normalized)

    def test_truncated_output_recovered_via_read(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(TRUNCATED_SENTENCE), normalized)

    def test_read_scope_limited_to_recovered_output(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(TOOL_SCOPE_SENTENCE), normalized)

    def test_boundary_lost_task_id_ends_execution_failed(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(LOST_SENTENCE), normalized)

    def test_boundary_budget_exhausted_reports_still_running(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(BUDGET_SENTENCE), normalized)

    def test_recovery_budget_is_bounded(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(BUDGET_DEFINITION_SENTENCE), normalized)

    def test_resumed_check_is_bounded(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(RESUME_CHECK_SENTENCE), normalized)

    def test_boundary_task_not_found_ends_execution_failed(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(NOT_FOUND_SENTENCE), normalized)

    def test_bash_only_wording_removed(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized_body = normalize(body)
        self.assertNotIn(
            normalize(
                "**Do not invoke other tools.** Only the `Bash` tool to "
                "start the wrapper."
            ),
            normalized_body,
        )
        self.assertNotIn(
            normalize(
                "This subagent's `tools` field grants `Bash` only — "
                "Read / Edit / Write / Skill / Task are all disallowed."
            ),
            normalized_body,
        )
        self.assertNotIn(
            normalize("Do not read files or independently analyze the diff"),
            normalized_body,
        )

    def test_recovery_section_helper_ignores_fenced_headings(self) -> None:
        fake_body = (
            "# Fake agent\n\n"
            "## Intro\n\n"
            "```markdown\n"
            "## Background-move recovery\n"
            "this fenced heading must not be treated as the real section start\n"
            "```\n\n"
            "## Background-move recovery\n\n"
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


if __name__ == "__main__":
    unittest.main()

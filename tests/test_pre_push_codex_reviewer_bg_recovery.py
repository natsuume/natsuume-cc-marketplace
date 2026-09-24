"""pre-push-codex-review:codex-reviewer の background-move 回収契約テスト。

Bash tool は timeout 時に wrapper 実行を kill せず background へ移行させ、その実行の
出力先 file path を結果に載せる。codex-reviewer subagent は待機前に recorded output
file の先頭にある wrapper の案内行から run id を取り、その run id を持つ terminal
sentinel が git-dir 直下に現れるまで Bash tool の polling loop で待つ。sentinel の
`status=ok` / `status=failed` で終了状態を判定してから recorded output file を `Read`
して回収し、既存の parent-safe report 契約へ normalize する。終端信号を run id 付きの
sentinel ファイルに置くことで、判定は出力ストリームのテキストにも別 run の残骸にも
依存せず、待機を含むコマンド実行は `Bash` matcher の PreToolUse gate が観測できる
1 本に閉じる。

本ファイルは、その回収契約を成す一文 (canonical 文) を module 定数として固定し、
`## Background-move recovery` セクション内に空白正規化した上で存在することを検証する。
pre-merge-codex-review 側の codex-reviewer も同じ回収契約に従うため、両 agent で共通の
一文は `SHARED_RECOVERY_CLAUSES` として公開し、pre-merge 側の契約テストが再利用する
(sentinel 名を含む一文と gate 名を含む resume 時の一文だけが plugin ごとに異なる)。
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
BLOCK_BG_CODEX_WRAPPER = (
    PLUGIN / "hooks" / "scripts" / "block-bg-codex-wrapper.sh"
)

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

TOOLS_LINE = "tools: Bash, Read"
TOOL_GRANT_LITERAL = "`Bash, Read`"
# 待機を含むコマンド実行は Bash tool に閉じる。別のコマンド実行経路を持つ tool を
# 案内すると、`Bash` matcher の PreToolUse gate が観測しない実行経路が増える。
# README の `## 既知の制約` 節だけは、この tool 経由のコマンドを gate が観測しない
# (サポート外) ことを利用者へ明記するために例外とする。
FORBIDDEN_EXECUTION_TOOL = "Monitor"
KNOWN_CONSTRAINTS_HEADING = "## 既知の制約"
# 終端判定は sentinel ファイルで行う。出力ストリーム中のテキストを終端信号にすると、
# wrapper の進捗行と接頭辞を共有して一意に判定できない。
FORBIDDEN_TERMINAL_CONCEPT = "completion marker"

# Agent tool は起動 mode を選ぶパラメータを受け付けず、foreground 起動を求めることも
# できない。一方 Bash tool の同名 option と foreground 実行は現行仕様でも有効なので、
# 禁止対象は「Agent / subagent の起動を指示する文」に限る。
AGENT_LAUNCH_MODE_PARAMETER = "run_in_background: false"
AGENT_LAUNCH_FOREGROUND_PHRASES = ("foreground 起動", "foreground で起動")
# agent の名指しと起動 mode の語がこの文字数以内に並ぶときだけ起動指示とみなす。
AGENT_LAUNCH_MODE_DISTANCE = 80
AGENT_LAUNCH_MARKERS = (
    "codex-reviewer",
    "codex-advisor-runner",
    "ADVISOR_CHECKPOINT_RUNNER",
    "subagent_type",
    "Agent tool",
)

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
# 待機前の前提確認 (recorded output file の存在).
PRECHECK_SENTENCE = (
    "Before the first wait, confirm with the Bash tool that the recorded "
    "output file exists."
)
# run の同一性 (待機前に 1 回だけ、output file の先頭行の案内行から run id を取る).
RUN_ID_SENTENCE = (
    "Before the first wait, Read the head of the recorded output file once "
    "and take the run id from the wrapper's `terminal sentinel: <path> "
    "run=<id>` announcement line, which is the first line of that file; "
    "reuse that one run id for the rest of this recovery."
)
# shell へ補間する前の形状検証。path の deny-list は double quote 内でも特別な意味を
# 持つ文字だけに絞る (空白や記号を含む repo パスを回収不能にしない)。空白等の
# shell 安全性は quoted 代入の clause が担う.
ANNOUNCEMENT_VALIDATION_SENTENCE = (
    "Before interpolating them into a shell command, check that the run id "
    "matches `^[0-9]+-[0-9]+-[0-9a-f]{8}$` and that the sentinel path and the "
    "recorded output file path are absolute — each starts with `/` and "
    "carries no double quote, `$`, backtick or backslash, the only characters "
    "that stay special inside double quotes; whitespace and other punctuation "
    "are ordinary path characters."
)
# shell への補間形 (double-quoted な変数代入と double quote 内の展開に限る)。形状
# 検証は deny-list なので、検証をすり抜けた文字が unquoted な位置に届く経路を塞ぐ.
QUOTED_ASSIGNMENT_SENTENCE = (
    'Interpolate the run id, the sentinel path and the recorded output file '
    'path only through double-quoted shell variable assignments '
    '(`RUN_ID="<id>"`, `SENTINEL="<path>"`, `OUT="<path>"`) and expand them '
    'only inside double quotes, so a character those checks do not reject '
    'still reaches no unquoted position of the command.'
)
# Bash tool 呼び出し間で shell 状態は残らない。毎回同じコマンド内で 3 値を代入し
# 直さないと、未代入の `$OUT` が output file 不存在の分岐に見えてしまう.
REASSIGN_PER_CALL_SENTENCE = (
    "Shell state does not survive between Bash calls, so every Bash call in "
    "this recovery — each loop run, each grace wait and each exit check — "
    "starts by re-establishing, in the same command, each of those "
    "assignments the call reads (`OUT` alone before the run is identified, "
    "all three afterwards) and setting that call's own deadline `$end` from "
    "`$SECONDS`; a loop must never run with an unset `$OUT`, which would look "
    "like a missing output file, or an unset `$end`, which would never end "
    "the loop."
)
# sentinel path の正本 (案内行の絶対パス。run id 入りの run ごとのファイル)。切り出しは
# 先頭の固定接頭辞と末尾の ` run=<id>` を境界にし、空白を含むパスも丸ごと取る.
SENTINEL_PATH_SOURCE_SENTENCE = (
    "Take the sentinel path from that same announcement line as everything "
    "between `terminal sentinel: ` and the trailing ` run=<id>` field, so a "
    "path containing spaces is recovered whole; the wrapper writes one "
    "sentinel per run, so that path already carries this run id."
)
# 案内行がまだ出ていないときの猶予待ち (回収予算には数えない).
GRACE_WAIT_SENTENCE = (
    "If that head Read finds no announcement line, wait once with a short "
    "Bash until-loop `until grep -q 'terminal sentinel:' \"$OUT\" || "
    "[ $SECONDS -ge $end ]; do sleep 5; done` whose deadline is 30 seconds, "
    "then Read the head again; this grace wait is not one of the recovery "
    "budget's loop runs."
)
# 案内行・終了行は wrapper 自身の通知であり report 本文ではない。background 移行の
# 有無に依らず適用されるため、recovery 節ではなく report 正規化の共通部分に置く.
NOTICE_LINES_NOT_FINDINGS_SENTENCE = (
    "The `terminal sentinel:` announcement line and the `terminal sentinel "
    "end` line are the wrapper's own startup and completion notices, so never "
    "treat them as findings and never include them in the parent-safe report."
)
# 待機手段 (Bash tool 1 回の until ポーリングループ。述語は run id 一致まで含む).
WAIT_SENTENCE = (
    "Wait for that same background run with a single Bash call that runs the "
    'until-loop `until { [ -e "$SENTINEL" ] && grep -qE " run=${RUN_ID}$" '
    '"$SENTINEL"; } || [ ! -e "$OUT" ] || [ $SECONDS -ge $end ]; do sleep 10; '
    "done`, where `$SENTINEL` is the wrapper's terminal sentinel, `$RUN_ID` "
    "is the run id you took before waiting, `$OUT` is the recorded output "
    "file, and `$end` is the loop's own deadline."
)
# ループ内 deadline と Bash tool timeout の関係 (auto-background を避ける).
LOOP_DEADLINE_SENTENCE = (
    "Give that loop a 9-minute deadline measured with `$SECONDS` and set the "
    "Bash call's `timeout` to 600000 ms, so the loop always ends on its own "
    "before the tool timeout could move it to the background."
)
# 単独 foreground sleep の禁止 (Bash tool が拒否する)。許される待機は、メインの
# ポーリングループと 2 つの猶予ループの内側にある sleep に限る.
NO_STANDALONE_SLEEP_SENTENCE = (
    "Never call a standalone foreground `sleep`; the Bash tool rejects it, "
    "and every wait in this recovery is a `sleep` inside one of these bounded "
    "until loops — the polling loop and the two grace loops."
)
# 回収予算 (ループの総実行回数と合計時間).
BUDGET_DEFINITION_SENTENCE = (
    "For the initial automatic recovery, run that loop at most five times in "
    "total — the initial run plus four reruns, roughly a 45-minute recovery "
    "budget — rerunning it only when it ended at its deadline without a "
    "matching sentinel."
)
# ループ終了理由の判別 (rerun か境界かを決める前の明示確認).
LOOP_EXIT_SENTENCE = (
    "When the loop returns, check with Bash which of the three exits "
    "happened: a sentinel carrying this run id means the run ended, so go on "
    "to recovery; a missing output file is the missing-output boundary; and "
    "neither means the loop reached its deadline, so decide there whether to "
    "rerun it."
)
# 述語が run id 一致を含むことの帰結 (別 run の sentinel は待機も予算も動かさない).
SENTINEL_MATCH_SENTENCE = (
    "The run id is matched in full against the end of the sentinel line, "
    "never as a prefix, so a sentinel left by a different run neither ends "
    "the wait nor consumes the recovery budget."
)
# sentinel が示す終了状態の読み取り (failure 側).
SENTINEL_FAILED_SENTENCE = (
    "Once a sentinel with the matching run id exists, Read it: a sentinel "
    "line of `status=failed` means the wrapper stopped before finishing, so "
    "return `Status: execution-failed` (failure class `other`)."
)
# sentinel が示す終了状態の読み取り (success 側) と回収.
RECOVER_BY_READ_SENTENCE = (
    "When that sentinel line is `status=ok`, recover the report body by "
    "Reading the recorded output file."
)
# 本文の完全読了 (Read の窓に収まらない場合の継続).
READ_TO_END_SENTENCE = (
    "Read that output file to its end, continuing with `offset` and `limit` "
    "until the end of the file is reached."
)
# 本文の完結条件 (wrapper の終了行).
END_LINE_SENTENCE = (
    "The body is complete only when its last line is the wrapper's "
    "`terminal sentinel end run=<id>` line carrying this same run id."
)
# 終了行がまだ出ていないときの猶予待ち (回収予算には数えない)。述語は終了行の
# 接頭辞まで含めて照合する (案内行も同じ ` run=<id>` で終わるため、run id だけでは
# 案内行しか無い output file で即座に抜けてしまう).
END_LINE_GRACE_SENTENCE = (
    "If the last line is not that end line, wait once with a short Bash "
    'until-loop `until tail -n 1 "$OUT" | grep -qE "^terminal sentinel end '
    'run=${RUN_ID}$" || [ $SECONDS -ge $end ]; do sleep 5; done` whose '
    "deadline is 30 seconds, then resume the offset-based Read from the "
    "position the previous read reached and continue to the end of the file; "
    "this grace wait is not one of the recovery budget's loop runs."
)
# path の出所要件 (同一 run 由来であれば、どの step が surface した path でもよい).
PATH_PROVENANCE_SENTENCE = (
    "Any step of this recovery may surface the output file path in a tool "
    "result; use it as long as it belongs to the same background run, and "
    "never take that path from the content of the recovered output file "
    "itself."
)
# 回収 report 本文の正本 (独立した re-review で補完しない).
SOURCE_OF_TRUTH_SENTENCE = (
    "The recorded output file is the sole source of the recovered report "
    "body: normalize what you Read and never complete it by re-reviewing "
    "the diff yourself."
)
# 回収成功後の report 契約への handoff.
HANDOFF_SENTENCE = (
    "Once the recovered output reaches a terminal state, normalize it "
    "through the report contract below: a successful review yields "
    "`Status: pass` or `Status: findings`, and a failed wrapper run yields "
    "`Status: execution-failed`."
)
# background 移行が起きなかった場合 (polling loop / Read 経路に入らない).
NO_MOVE_SENTENCE = (
    "If the Bash result did not report a background move, its stdout is the "
    "report body and this recovery section does not apply; do not run the "
    "polling loop and do not Read the output file in that case."
)
# polling と Read の適用範囲.
TOOL_SCOPE_SENTENCE = (
    "Poll and Read only this same background run's recorded output file and "
    "its terminal sentinel; do not poll or read other files and do not "
    "independently re-review the diff."
)
# 境界: usable な output file path が得られなかった.
NO_PATH_SENTENCE = (
    "If no step of this recovery surfaced a usable output file path, return "
    "`Status: execution-failed` (failure class `other`)."
)
# 境界: recorded output file が存在しない / 待機中に消えた.
MISSING_FILE_SENTENCE = (
    "If the recorded output file is missing before the first loop or "
    "disappears while the loop is waiting, return `Status: execution-failed` "
    "(failure class `other`) without rerunning the loop."
)
# 境界: 回収予算 (5 回のループ) の超過.
BUDGET_SENTENCE = (
    "If the fifth run of the loop ends at its deadline without a matching "
    "sentinel, return `Status: execution-failed` (failure class `other`), "
    "state in the recovery direction that the codex review is likely still "
    "running in the background, and note that the parent may resume this "
    "same subagent for a diagnostic status check only."
)
# 境界: 本文を最後まで読めない / 終了行が来ない / 通知行しか無い.
INCOMPLETE_BODY_SENTENCE = (
    "If the recorded output file cannot be read to its end, still lacks that "
    "end line after the grace wait, or carries nothing between the "
    "announcement line and the end line, return `Status: execution-failed` "
    "(failure class `other`) instead of normalizing a partial body."
)
# 境界: 猶予待ちの後も run を同定できない (空ファイル・形状検証の失敗も含む).
UNIDENTIFIABLE_RUN_SENTENCE = (
    "If the first line after that grace wait is still not an announcement "
    "line — including a recorded output file that is still empty — or its "
    "run id, or a sentinel path it does carry, fails those checks, this run "
    "cannot be identified: return `Status: execution-failed` (failure class "
    "`other`) without entering the wait loop."
)

# sentinel path の fallback (案内行に path が全く無い場合に限る。prefix は plugin
# ごと)。path があって形状検証に落ちる状態は run 同定不能境界であり、fallback の
# 対象ではない。合成に使う git directory は cwd に依存しない絶対形にする.
SENTINEL_NAME_PREFIX = "pre-push-codex-review-terminal"
SENTINEL_PATH_SENTENCE = (
    "Only when the announcement line carries a run id but no path at all, "
    "compose the sentinel path from the absolute git directory that "
    "`git rev-parse --absolute-git-dir` prints, the fixed prefix "
    f"`{SENTINEL_NAME_PREFIX}-` and this run id, and put the composed path "
    "through the same shape check before interpolating it; a path that is "
    "present but fails those checks is the unidentifiable-run boundary, not a "
    "case for this fallback."
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
    "output-file-precheck": PRECHECK_SENTENCE,
    "run-id-from-announcement": RUN_ID_SENTENCE,
    "announcement-validation": ANNOUNCEMENT_VALIDATION_SENTENCE,
    "quoted-shell-assignment": QUOTED_ASSIGNMENT_SENTENCE,
    "assignments-per-bash-call": REASSIGN_PER_CALL_SENTENCE,
    "sentinel-path-from-announcement": SENTINEL_PATH_SOURCE_SENTENCE,
    "announcement-grace-wait": GRACE_WAIT_SENTENCE,
    "polling-loop-wait": WAIT_SENTENCE,
    "loop-deadline": LOOP_DEADLINE_SENTENCE,
    "no-standalone-sleep": NO_STANDALONE_SLEEP_SENTENCE,
    "budget-definition": BUDGET_DEFINITION_SENTENCE,
    "loop-exit-check": LOOP_EXIT_SENTENCE,
    "sentinel-run-id-match": SENTINEL_MATCH_SENTENCE,
    "sentinel-failed": SENTINEL_FAILED_SENTENCE,
    "recover-by-read": RECOVER_BY_READ_SENTENCE,
    "read-body-to-end": READ_TO_END_SENTENCE,
    "end-line-completes-the-body": END_LINE_SENTENCE,
    "end-line-grace-wait": END_LINE_GRACE_SENTENCE,
    "path-provenance": PATH_PROVENANCE_SENTENCE,
    "source-of-truth": SOURCE_OF_TRUTH_SENTENCE,
    "report-contract-handoff": HANDOFF_SENTENCE,
    "no-background-move": NO_MOVE_SENTENCE,
    "poll-and-read-scope": TOOL_SCOPE_SENTENCE,
    "boundary-no-output-path": NO_PATH_SENTENCE,
    "boundary-missing-output-file": MISSING_FILE_SENTENCE,
    "boundary-incomplete-body": INCOMPLETE_BODY_SENTENCE,
    "boundary-budget-exhausted": BUDGET_SENTENCE,
    "boundary-unidentifiable-run": UNIDENTIFIABLE_RUN_SENTENCE,
}

# background 移行の有無に依らず適用されるため、回収節の中だけに置いてはならない一文。
SHARED_BODY_CLAUSES = {
    "notice-lines-not-findings": NOTICE_LINES_NOT_FINDINGS_SENTENCE,
}

RECOVERY_HEADING = "## Background-move recovery"

FENCE_OPEN_PATTERN = re.compile(r"^(`{3,}|~{3,})")
# 回収セクションを終端する見出し: 同レベル (`## `) と上位 (`# `)。
SECTION_END_PATTERN = re.compile(r"^#{1,2} ")
# 指示の単位を区切る list item の開始 (markdown の箇条書きと番号付きリスト)。
LIST_ITEM_PATTERN = re.compile(r"^(?:[-*+]\s|\d+[.)]\s)")


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


def without_known_constraints_section(text: str) -> str:
    """README 本文から `## 既知の制約` 節 (次の `## ` 見出しの直前まで) を除いて返す。"""
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    skipping = False
    for line in lines:
        if line.rstrip("\n") == KNOWN_CONSTRAINTS_HEADING:
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            kept.append(line)
    return "".join(kept)


def normalized_recovery_section(path: Path) -> str:
    """agent file の回収セクションを空白正規化して返す。セクション不在なら失敗する。"""
    section = recovery_section(read(path))
    if not section:
        raise AssertionError(
            f"{path}: '{RECOVERY_HEADING}' section not found"
        )
    return normalize(section)


def instruction_units(text: str) -> list[tuple[int, str]]:
    """本文を指示の単位に分割し、(開始行番号, 空白正規化した本文) の列を返す。

    単位は空行で区切り、list item の開始でも区切る。1 つの指示が改行で分断されていても
    同じ単位にまとまるため、行単位では見えない組み合わせを検査できる。隣接する別々の
    箇条書きが 1 単位に混ざることはない。
    """
    units: list[tuple[int, str]] = []
    current: list[str] = []
    start = 0
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            if current:
                units.append((start, " ".join(current)))
                current = []
            continue
        if LIST_ITEM_PATTERN.match(stripped) and current:
            units.append((start, " ".join(current)))
            current = []
        if not current:
            start = number
        current.append(stripped)
    if current:
        units.append((start, " ".join(current)))
    return units


def sentences(unit: str) -> list[str]:
    """指示の単位を文に分割する (句点区切り。句点が無ければ単位全体を 1 文とする)。"""
    parts = [part.strip() for part in unit.split("。") if part.strip()]
    return parts or ([unit] if unit.strip() else [])


def demands_agent_launch_mode(sentence: str) -> bool:
    """1 文が Agent の起動 mode を指示しているかを判定する。

    起動 mode の指示は、パラメータ (`run_in_background: false`) と自然言語の
    foreground 要求の両方を対象にする。ただし Bash tool の同名 option と wrapper の
    foreground 実行は現行仕様でも有効なので、「Agent / subagent を名指しした直後に
    その起動 mode を述べている」形だけを指示とみなす: 起動 mode の語が agent の
    名指しより後ろの近い位置にあり、その間に別の起動対象 (wrapper) が挟まらないこと
    を要求する。
    """
    mode_tokens = (
        AGENT_LAUNCH_MODE_PARAMETER,
        *AGENT_LAUNCH_FOREGROUND_PHRASES,
    )
    for marker in AGENT_LAUNCH_MARKERS:
        start = sentence.find(marker)
        while start != -1:
            end = start + len(marker)
            for token in mode_tokens:
                index = sentence.find(token, end)
                if index == -1 or index - end > AGENT_LAUNCH_MODE_DISTANCE:
                    continue
                if "wrapper" in sentence[end:index]:
                    continue
                return True
            start = sentence.find(marker, end)
    return False


def agent_launch_mode_hits(text: str) -> list[str]:
    """Agent の起動 mode を指示している文を、開始行番号付きで返す。"""
    return [
        f"L{number}: {sentence[:120]}"
        for number, unit in instruction_units(text)
        for sentence in sentences(unit)
        if demands_agent_launch_mode(sentence)
    ]


class ContractTestCase(unittest.TestCase):
    """md / mjs の文言契約を、失敗時に全文を出力せずに検証する assertion helper。

    対象ファイルは数百行あるため、`assertIn` / `assertNotIn` の既定メッセージでは
    失敗理由 (どの一文が欠けているか / どの行が残っているか) が全文に埋もれる。
    """

    def assert_text_contains(self, path: Path, needle: str) -> None:
        if needle not in read(path):
            self.fail(f"{path}: 期待する文字列が無い: {needle}")

    def assert_text_absent(self, path: Path, needle: str) -> None:
        """空白正規化した全文で禁止語の不在を確認する (改行をまたぐ出現も検出する)。

        行番号付きの hit 一覧は診断用で、行内に収まらない出現はその旨だけを示す。
        """
        body = read(path)
        if normalize(needle) not in normalize(body):
            return
        hits = [
            f"L{number}: {line.strip()[:120]}"
            for number, line in enumerate(body.splitlines(), start=1)
            if needle in line
        ]
        joined = " / ".join(hits) if hits else "改行をまたいで出現"
        self.fail(f"{path}: {needle} の言及が残っている: {joined}")

    def assert_text_absent_outside_known_constraints(
        self, path: Path, needle: str
    ) -> None:
        """`## 既知の制約` 節を除いた本文で禁止語の不在を確認する。"""
        body = without_known_constraints_section(read(path))
        if normalize(needle) not in normalize(body):
            return
        hits = [
            f"L{number}: {line.strip()[:120]}"
            for number, line in enumerate(body.splitlines(), start=1)
            if needle in line
        ]
        joined = " / ".join(hits) if hits else "改行をまたいで出現"
        self.fail(
            f"{path}: {needle} の言及が {KNOWN_CONSTRAINTS_HEADING} 節の外に残っている: "
            f"{joined}"
        )

    def assert_no_agent_launch_mode_parameter(self, path: Path) -> None:
        hits = agent_launch_mode_hits(read(path))
        if hits:
            joined = " / ".join(hits)
            self.fail(f"{path}: Agent 起動指示の起動 mode 指定が残っている: {joined}")

    def assert_recovery_clause(self, path: Path, sentence: str) -> None:
        expected = normalize(sentence)
        if expected not in normalized_recovery_section(path):
            self.fail(f"{path}: 回収契約の一文が欠けている: {expected}")

    def assert_body_clause_outside_recovery(
        self, path: Path, sentence: str
    ) -> None:
        """経路非依存の契約が、回収節の外にも書かれていることを確認する。"""
        body = read(path)
        expected = normalize(sentence)
        if expected not in normalize(body):
            self.fail(f"{path}: 契約の一文が本文に無い: {expected}")
        section = recovery_section(body)
        outside = body.replace(section, " ", 1) if section else body
        if expected not in normalize(outside):
            self.fail(
                f"{path}: 契約の一文が '{RECOVERY_HEADING}' 節の中だけにある: "
                f"{expected}"
            )

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

    def test_agent_body_never_mentions_a_second_execution_tool(self) -> None:
        """待機を含むコマンド実行は Bash tool に閉じる (実行経路を 1 本に保つ)。"""
        self.assert_text_absent(CODEX_REVIEWER, FORBIDDEN_EXECUTION_TOOL)

    def test_agent_body_never_uses_output_text_as_terminal_signal(self) -> None:
        """終端判定は sentinel ファイルで行い、出力テキストに依存しない。"""
        self.assert_text_absent(CODEX_REVIEWER, FORBIDDEN_TERMINAL_CONCEPT)

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

    def test_agent_body_omits_agent_launch_mode_parameter(self) -> None:
        self.assert_no_agent_launch_mode_parameter(CODEX_REVIEWER)


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

    def test_sentinel_path_falls_back_to_the_fixed_name(self) -> None:
        self.assert_clause(SENTINEL_PATH_SENTENCE)

    def test_recovery_checks_the_output_file_before_waiting(self) -> None:
        self.assert_clause(PRECHECK_SENTENCE)

    def test_recovery_takes_the_run_id_from_the_announcement_line(self) -> None:
        self.assert_clause(RUN_ID_SENTENCE)

    def test_announcement_values_are_validated_before_interpolation(
        self,
    ) -> None:
        self.assert_clause(ANNOUNCEMENT_VALIDATION_SENTENCE)

    def test_values_are_interpolated_only_as_quoted_assignments(self) -> None:
        self.assert_clause(QUOTED_ASSIGNMENT_SENTENCE)

    def test_every_bash_call_reestablishes_the_assignments(self) -> None:
        self.assert_clause(REASSIGN_PER_CALL_SENTENCE)

    def test_recovery_takes_the_sentinel_path_from_the_announcement_line(
        self,
    ) -> None:
        self.assert_clause(SENTINEL_PATH_SOURCE_SENTENCE)

    def test_missing_announcement_line_gets_one_grace_wait(self) -> None:
        self.assert_clause(GRACE_WAIT_SENTENCE)

    def test_recovery_waits_with_a_bash_until_loop(self) -> None:
        self.assert_clause(WAIT_SENTENCE)

    def test_loop_deadline_precedes_the_bash_tool_timeout(self) -> None:
        self.assert_clause(LOOP_DEADLINE_SENTENCE)

    def test_recovery_forbids_a_standalone_foreground_sleep(self) -> None:
        self.assert_clause(NO_STANDALONE_SLEEP_SENTENCE)

    def test_recovery_budget_bounds_polling_loop_runs(self) -> None:
        self.assert_clause(BUDGET_DEFINITION_SENTENCE)

    def test_loop_exit_reason_is_checked_before_deciding(self) -> None:
        self.assert_clause(LOOP_EXIT_SENTENCE)

    def test_other_runs_sentinel_neither_ends_the_wait_nor_costs_budget(
        self,
    ) -> None:
        self.assert_clause(SENTINEL_MATCH_SENTENCE)

    def test_failed_sentinel_ends_execution_failed(self) -> None:
        self.assert_clause(SENTINEL_FAILED_SENTENCE)

    def test_ok_sentinel_recovers_the_body_by_reading_the_output_file(self) -> None:
        self.assert_clause(RECOVER_BY_READ_SENTENCE)

    def test_recovered_body_is_read_to_the_end(self) -> None:
        self.assert_clause(READ_TO_END_SENTENCE)

    def test_end_line_completes_the_recovered_body(self) -> None:
        self.assert_clause(END_LINE_SENTENCE)

    def test_missing_end_line_gets_one_grace_wait(self) -> None:
        self.assert_clause(END_LINE_GRACE_SENTENCE)

    def test_output_file_path_from_any_step_of_same_run_is_usable(self) -> None:
        self.assert_clause(PATH_PROVENANCE_SENTENCE)

    def test_recovered_report_body_comes_from_the_output_file(self) -> None:
        self.assert_clause(SOURCE_OF_TRUTH_SENTENCE)

    def test_recovered_terminal_state_routes_through_report_contract(self) -> None:
        self.assert_clause(HANDOFF_SENTENCE)

    def test_absent_background_move_keeps_bash_stdout_as_report(self) -> None:
        self.assert_clause(NO_MOVE_SENTENCE)

    def test_poll_and_read_scope_limited_to_the_same_run(self) -> None:
        self.assert_clause(TOOL_SCOPE_SENTENCE)

    def test_boundary_no_output_file_path_ends_execution_failed(self) -> None:
        self.assert_clause(NO_PATH_SENTENCE)

    def test_boundary_missing_output_file_ends_execution_failed(self) -> None:
        self.assert_clause(MISSING_FILE_SENTENCE)

    def test_boundary_incomplete_body_ends_execution_failed(self) -> None:
        self.assert_clause(INCOMPLETE_BODY_SENTENCE)

    def test_boundary_budget_exhausted_reports_still_running(self) -> None:
        self.assert_clause(BUDGET_SENTENCE)

    def test_boundary_unidentifiable_run_ends_execution_failed(self) -> None:
        self.assert_clause(UNIDENTIFIABLE_RUN_SENTENCE)

    def test_resumed_status_check_is_bounded_and_diagnostic_only(self) -> None:
        self.assert_clause(RESUME_CHECK_SENTENCE)


class CodexReviewerReportNormalizationTest(ContractTestCase):
    """background 移行の有無に依らず適用される report 正規化の契約。"""

    def test_wrapper_notice_lines_are_never_findings(self) -> None:
        self.assert_body_clause_outside_recovery(
            CODEX_REVIEWER, NOTICE_LINES_NOT_FINDINGS_SENTENCE
        )


class CodexReviewerDocumentationTest(ContractTestCase):
    """README が現在の tool grant と起動仕様を説明する。"""

    def test_plugin_readme_documents_current_tool_grant(self) -> None:
        self.assert_text_absent(PLUGIN_README, "TaskOutput")
        self.assert_text_absent_outside_known_constraints(
            PLUGIN_README, FORBIDDEN_EXECUTION_TOOL
        )
        self.assert_text_contains(PLUGIN_README, TOOL_GRANT_LITERAL)

    def test_plugin_readme_omits_agent_launch_mode_parameter(self) -> None:
        self.assert_no_agent_launch_mode_parameter(PLUGIN_README)

    def test_root_readme_documents_current_tool_grant(self) -> None:
        self.assert_text_absent(ROOT_README, "Bash, TaskOutput, Read")
        self.assert_text_contains(ROOT_README, TOOL_GRANT_LITERAL)


class CodexReviewerLaunchInstructionTest(ContractTestCase):
    """deny メッセージの subagent 起動案内が起動 mode を指示しないこと。"""

    def test_block_bg_wrapper_deny_omits_agent_launch_mode(self) -> None:
        self.assert_no_agent_launch_mode_parameter(BLOCK_BG_CODEX_WRAPPER)


class AgentLaunchModeHelperTest(unittest.TestCase):
    """`agent_launch_mode_hits` が Agent 起動指示だけを検出すること。"""

    def test_agent_launch_line_is_detected(self) -> None:
        text = (
            '`pre-merge-codex-review:codex-reviewer` を Agent tool で '
            '`model: "sonnet"`、foreground (`run_in_background: false`) で起動する\n'
        )
        self.assertEqual(len(agent_launch_mode_hits(text)), 1)

    def test_agent_launch_split_across_lines_is_detected(self) -> None:
        text = (
            "**指示**: `pre-merge-codex-review:codex-reviewer` を Agent tool で\n"
            '`model: "sonnet"`、foreground (`run_in_background: false`) で\n'
            "起動し、report を受け取る\n"
        )
        self.assertEqual(len(agent_launch_mode_hits(text)), 1)

    def test_natural_language_foreground_demand_is_detected(self) -> None:
        text = (
            'Agent / Task tool で subagent_type="pre-merge-codex-review:'
            'codex-reviewer", model="sonnet" を foreground 起動してください。\n'
        )
        self.assertEqual(len(agent_launch_mode_hits(text)), 1)

    def test_bash_level_description_is_not_detected(self) -> None:
        text = (
            "- subagent body は wrapper を `run_in_background: false` で 1 回起動する\n"
            "- exact detail の確認が必要なら同一 codex-reviewer を resume する\n"
        )
        self.assertEqual(agent_launch_mode_hits(text), [])

    def test_bash_level_foreground_sentence_is_not_detected(self) -> None:
        """同じ段落に Agent への言及があっても、別の文の Bash レベル説明は対象外。"""
        text = (
            "対応: wrapper は内部で codex companion を `--wait` で foreground 起動"
            "するため、 Bash 呼び出し自体が review 完了まで block します。"
            " この deny を見た場合は `pre-push-codex-review:codex-reviewer` "
            "subagent を再起動してください。\n"
        )
        self.assertEqual(agent_launch_mode_hits(text), [])

    def test_agent_launch_line_without_the_parameter_is_not_detected(self) -> None:
        text = (
            '`pre-merge-codex-review:codex-reviewer` を Agent tool で '
            '`model: "sonnet"` を指定して起動する\n'
        )
        self.assertEqual(agent_launch_mode_hits(text), [])


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

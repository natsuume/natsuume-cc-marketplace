"""codex-advisor の runner 規律 (background 実行前提) の契約テスト。

対話セッションの subagent は background で実行され、Agent tool には起動 mode を選ぶ
parameter が無い。runner の terminal report は completion notification として後続 turn
に届き、`TaskOutput` tool は存在しない。本ファイルは、この前提の下で codex-advisor が
満たすべき契約を 4 層で固定する。

1. 文言契約: 3 runner / 配送 prompt / consult skill / README / lifecycle hook が起動
   mode を指示せず、canonical 文で background 前提の回収手順と poll 予算を規定する
2. hooks.json の構造契約: SessionStart の matcher、PermissionDenied entry、既存 entry
3. lifecycle hook (manage-codex-runners.mjs) の JSON I/O: Stop の `background_tasks`
   照合、PermissionDenied による `denied` 遷移、PreToolUse gate の command 解析
4. companion adapter (run-codex-job.sh) の cancel 経路 timeout

canonical 文は module 定数として持ち、空白を全て除去した文字列の包含で照合する
(md の行折り返し位置に結合しないため)。hook の state は環境変数
`CODEX_ADVISOR_STATE_ROOT` で一時ディレクトリへ隔離する。
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "codex-advisor"

ADVISOR_RUNNER = PLUGIN / "agents" / "advisor-runner.md"
RESCUE_RUNNER = PLUGIN / "agents" / "rescue-runner.md"
REVIEW_RUNNER = PLUGIN / "agents" / "review-runner.md"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
MANAGE_RUNNERS = PLUGIN / "hooks" / "scripts" / "manage-codex-runners.mjs"
ADVISOR_RULES = PLUGIN / "hooks" / "prompts" / "advisor-rules.md"
ADVISOR_RULES_SUBAGENT = PLUGIN / "hooks" / "prompts" / "advisor-rules-subagent.md"
CONSULT_SKILL = PLUGIN / "skills" / "consult" / "SKILL.md"
PLUGIN_README = PLUGIN / "README.md"
ROOT_README = ROOT / "README.md"
JOB_HELPER = PLUGIN / "scripts" / "run-codex-job.sh"

RUNNER_AGENTS = {
    "advisor": ADVISOR_RUNNER,
    "rescue": RESCUE_RUNNER,
    "review": REVIEW_RUNNER,
}

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_test_module(name: str):
    """同じ tests directory の module を読み込む。

    `import` 文で書くと「sys.path 操作より前に import 文が来る」という lint 制約
    (E402) に抵触するため、既存テストと同じ importlib 経由の明示 import にする。
    """
    return importlib.import_module(name)


_runner_contract = _load_test_module("test_codex_advisor_subagent_runner")

HookHarness = _runner_contract.HookHarness
RUNNERS = _runner_contract.RUNNERS

# ---------------------------------------------------------------------------
# 文言契約の定数
# ---------------------------------------------------------------------------

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

# 3 runner が公開する tool 集合。Bash で companion を起動し、Write で prompt file を
# 作り、background へ移行した実行の output file を Read で回収する。
TOOLS_LINE = "tools: Bash, Write, Read"

# 廃止された tool。配送 prompt・agent 本文・README・hook 文言のいずれにも残さない。
REMOVED_TOOL = "TaskOutput"

# Agent tool は起動 mode を選ぶ parameter を受け付けない。Bash tool の同名 option は
# 現行仕様でも有効なため、禁止するのは Agent / subagent の起動を指示する文言に限る。
AGENT_LAUNCH_MODE_PARAMETER = "run_in_background: false"
AGENT_LAUNCH_FOREGROUND_PHRASES = ("foreground 起動", "foreground で起動")
AGENT_LAUNCH_MODE_DISTANCE = 80
AGENT_LAUNCH_MARKERS = (
    "rescue-runner",
    "review-runner",
    "advisor-runner",
    "subagent_type",
    "Agent tool",
    "Agent call",
)

# Agent / subagent の起動 mode を表す語。文脈解析に依らず不在を要求する。
FORBIDDEN_AGENT_MODE_PHRASES = (
    AGENT_LAUNCH_MODE_PARAMETER,
    "foreground Agent",
    "foreground subagent",
)
# runner 内部の Bash 呼び出しも起動 mode を指定しない (timeout で background へ移行
# しうるため、foreground であることを前提にした手順を書かない)。
FORBIDDEN_RUNNER_BASH_PHRASE = "foreground Bash"
# 同一 turn 内で terminal report を待つことはできない。
FORBIDDEN_WAIT_IN_TURN_PHRASES = ("turn を終了しない", "ターンを終了しない")
# 経緯記述 (過去の仕様の説明) を配送 prompt・README・コードコメントに残さない。
FORBIDDEN_HISTORY_PHRASES = (
    "以前は",
    "かつては",
    "旧仕様",
    "廃止に伴い",
    "だった時代",
)

# 起動規律 (advisor-rules.md と consult skill が共有する canonical 文)。
LAUNCH_MODEL_CLAUSE = 'Agent call は `model: "sonnet"` を明示する'
LAUNCH_MODE_OWNER_CLAUSE = (
    "起動 mode は Claude Code が決めるため、Agent call で起動 mode を指定しない"
)
COMPLETION_NOTIFICATION_CLAUSE = (
    "runner の terminal report は completion notification "
    "(auto mode では SubagentHandback、それ以外では SubagentStop) で後続 turn に届く"
)
NOTIFICATION_FIRST_CLAUSE = (
    "main session は completion notification を受け取ってから runner の report を"
    "処理する"
)
# report を待つ間に turn は終わるが、report を処理する前に作業を完了扱いにはしない。
REPORT_BEFORE_COMPLETION_CLAUSE = (
    "runner の terminal report が返るまでタスクを完了扱いにしない"
)
LAUNCH_DISCIPLINE_CLAUSES = {
    "launch-model": LAUNCH_MODEL_CLAUSE,
    "launch-mode-owner": LAUNCH_MODE_OWNER_CLAUSE,
    "completion-notification": COMPLETION_NOTIFICATION_CLAUSE,
    "notification-first": NOTIFICATION_FIRST_CLAUSE,
    "report-before-completion": REPORT_BEFORE_COMPLETION_CLAUSE,
}

# classifier に拒否されたときの手順 (advisor-rules.md の rule:codex-runner 節)。
CLASSIFIER_DENIAL_CLAUSE = (
    "runner の起動が classifier に拒否された場合は、同じ起動を繰り返さず、"
    "`AskUserQuestion` でユーザの許可を得てから再起動する"
)

# runner 内部の companion 呼び出しと job 回収 (3 runner 本文が共有する canonical 文)。
RUNNER_BASH_MODE_CLAUSE = (
    "`run-codex-job.sh` / `poll-codex-job.sh` の呼び出しは、`run_in_background` を"
    "指定しない Bash 呼び出しとして実行する"
)
RUNNER_BACKGROUND_MOVE_CLAUSE = (
    "Bash tool の timeout で実行が background へ移行した場合は、Bash の結果が示す "
    "output file path を `Read` で読み、そこから job ID を取得する"
)
RUNNER_RESUME_TRACKING_CLAUSE = (
    "取得した job ID で `status` / `result` を再実行して同じ job の回収を続け、"
    "新しい job を起動しない"
)
POLL_SLICE_CLAUSE = (
    "`status --wait` は 1 回の Bash 呼び出しあたり `--timeout-ms` を 90000 以下に"
    "した slice として実行し、job が terminal になるまで slice を繰り返す"
)
POLL_BUDGET_CLAUSE = (
    "slice の繰り返しは合計 10 分相当 (`--timeout-ms 90000` なら 7 回) を上限とする"
)
POLL_BUDGET_EXCEEDED_CLAUSE = (
    "上限を超えても job が terminal にならない場合は、`cancel` で job を terminal 化"
    "してから `Codex-Runner-Status: terminal-failure` として報告する"
)
RUNNER_BODY_CLAUSES = {
    "bash-launch-mode": RUNNER_BASH_MODE_CLAUSE,
    "background-move-recovery": RUNNER_BACKGROUND_MOVE_CLAUSE,
    "resume-same-job": RUNNER_RESUME_TRACKING_CLAUSE,
    "poll-slice": POLL_SLICE_CLAUSE,
    "poll-budget": POLL_BUDGET_CLAUSE,
    "poll-budget-exceeded": POLL_BUDGET_EXCEEDED_CLAUSE,
}

# 1 回の status poll slice が使える上限 (ms)。runner 本文の `--timeout-ms` 実引数は
# すべてこの値以下でなければならない。
POLL_SLICE_TIMEOUT_MS = 90_000
TIMEOUT_MS_ARGUMENT_PATTERN = re.compile(r"--timeout-ms\s+(\d+)")

# README の auto mode 節 (classifier の allow 設定を案内する)。
AUTO_MODE_HEADING = "## auto mode での利用"
AUTO_MODE_REQUIRED_LITERALS = (
    "~/.claude/settings.json",
    "autoMode",
    '"allow"',
    '"$defaults"',
    "AskUserQuestion",
    *RUNNERS.values(),
)

# 根本 README の plugin 一覧・codex-advisor 節。
ROOT_README_SECTION_HEADING = "## codex-advisor"

# ---------------------------------------------------------------------------
# hooks.json / hook I/O の定数
# ---------------------------------------------------------------------------

HOOK_SCRIPT_MARKER = "manage-codex-runners.mjs"
SESSION_START_MATCHER = "startup|clear"
SESSION_START_ACCEPTED_SOURCES = ("startup", "clear")
SESSION_START_REJECTED_SOURCES = ("compact", "resume", "fork")
PERMISSION_DENIED_ACCEPTED_TOOLS = ("Agent", "Task")
PERMISSION_DENIED_REJECTED_TOOLS = ("Bash", "SubagentHandback", "Write")
LIFECYCLE_EVENTS = (
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PermissionDenied",
    "SubagentStart",
    "PostToolUse",
    "SubagentStop",
    "Stop",
)

# PreToolUse gate の command 解析に使うパス。
COMPANION_PATH = "/opt/claude/plugins/openai-codex/scripts/codex-companion.mjs"
JOB_HELPER_PATH = "/opt/claude/plugins/codex-advisor/scripts/run-codex-job.sh"
ADVISOR_WRAPPER_PATH = (
    "/opt/claude/plugins/codex-advisor/scripts/run-codex-advisor.sh"
)

# ---------------------------------------------------------------------------
# cancel 経路の timeout
# ---------------------------------------------------------------------------

CANCEL_TIMEOUT_ENV = "CODEX_JOB_CANCEL_TIMEOUT_SECONDS"
CANCEL_TIMEOUT_DEFAULT_SECONDS = 30
CANCEL_TIMEOUT_DEFAULT_PATTERN = re.compile(
    rf"\$\{{{CANCEL_TIMEOUT_ENV}:-{CANCEL_TIMEOUT_DEFAULT_SECONDS}\}}"
)


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact(text: str) -> str:
    """空白 (改行を含む) を全て除去する。

    日本語の説明文は行折り返し位置が自由なので、canonical 文の照合を折り返しに
    結合させないために空白を落として比較する。改行をまたいで現れる禁止語も
    この正規化で検出できる。
    """
    return "".join(text.split())


FENCE_OPEN_PATTERN = re.compile(r"^(`{3,}|~{3,})")


def markdown_section(body: str, heading: str) -> str:
    """`heading` 行から、同じか上位の見出しが現れる直前までを返す。

    コードフェンス (``` / ~~~) の内側にある見出し様の行は見出しとして扱わない
    (README の bash ブロック内のコメント行を見出しと誤認しないため)。見出しが
    見つからなければ空文字列を返す。
    """
    level = len(heading) - len(heading.lstrip("#"))
    end_pattern = re.compile(rf"^#{{1,{level}}} ")
    lines = body.splitlines(keepends=True)
    in_fence = False
    fence_char = ""
    fence_length = 0
    start_index: int | None = None
    end_index: int | None = None
    for index, line in enumerate(lines):
        content = line.rstrip("\n")
        stripped = content.lstrip()
        if in_fence:
            run_length = 0
            while run_length < len(stripped) and stripped[run_length] == fence_char:
                run_length += 1
            if run_length >= fence_length and stripped[run_length:].strip() == "":
                in_fence = False
            continue
        opening = FENCE_OPEN_PATTERN.match(stripped)
        if opening:
            fence_char = opening.group(1)[0]
            fence_length = len(opening.group(1))
            in_fence = True
            continue
        if start_index is None:
            if content.strip() == heading:
                start_index = index
            continue
        if end_index is None and end_pattern.match(content):
            end_index = index
    if start_index is None:
        return ""
    if end_index is None:
        return "".join(lines[start_index:])
    return "".join(lines[start_index:end_index])


RULE_MARKER_PATTERN = re.compile(r"^<!--\s*rule:")


def rule_section(body: str, rule_id: str) -> str:
    """`<!-- rule:xxx -->` マーカー行から次のルールマーカー行の直前までを返す。

    見出し文言ではなくルール ID を境界にするため、見出しの言い換えに結合しない。
    """
    marker = f"rule:{rule_id}"
    lines = body.splitlines(keepends=True)
    start_index: int | None = None
    end_index: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if start_index is None:
            if RULE_MARKER_PATTERN.match(stripped) and marker in stripped:
                start_index = index
            continue
        if end_index is None and RULE_MARKER_PATTERN.match(stripped):
            end_index = index
    if start_index is None:
        return ""
    if end_index is None:
        return "".join(lines[start_index:])
    return "".join(lines[start_index:end_index])


LIST_ITEM_PATTERN = re.compile(r"^(?:[-*+]\s|\d+[.)]\s)")


def instruction_units(text: str) -> list[tuple[int, str]]:
    """本文を指示の単位に分割し、(開始行番号, 空白正規化した本文) の列を返す。

    単位は空行と list item の開始で区切る。1 つの指示が改行で分断されていても
    同じ単位にまとまるため、行単位では見えない組み合わせを検査できる。
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

    起動 mode の指示は parameter (`run_in_background: false`) と自然言語の
    foreground 要求の両方を対象にする。ただし Bash tool の同名 option と wrapper の
    foreground 実行は現行仕様でも有効なので、「Agent / subagent を名指しした直後に
    その起動 mode を述べている」形だけを指示とみなす。
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
    """md / mjs / json の文言契約を、失敗時に全文を出力せずに検証する helper。"""

    def assert_contains(self, label: str, body: str, needle: str) -> None:
        if compact(needle) not in compact(body):
            self.fail(f"{label}: 期待する文字列が無い: {needle}")

    def assert_absent(self, label: str, body: str, needle: str) -> None:
        if compact(needle) not in compact(body):
            return
        hits = [
            f"L{number}: {line.strip()[:120]}"
            for number, line in enumerate(body.splitlines(), start=1)
            if needle in line
        ]
        joined = " / ".join(hits) if hits else "改行をまたいで出現"
        self.fail(f"{label}: {needle} の言及が残っている: {joined}")

    def assert_no_agent_launch_mode(self, label: str, body: str) -> None:
        hits = agent_launch_mode_hits(body)
        if hits:
            self.fail(f"{label}: Agent 起動指示の起動 mode 指定が残っている: " + " / ".join(hits))

    def assert_tools_line(self, path: Path) -> None:
        match = FRONTMATTER_PATTERN.match(read(path))
        if match is None:
            self.fail(f"{path}: frontmatter が無い")
        tools_lines = [
            line for line in match.group(1).splitlines() if line.startswith("tools:")
        ]
        self.assertEqual(1, len(tools_lines), f"{path}: {tools_lines}")
        self.assertEqual(TOOLS_LINE, tools_lines[0], str(path))


# ---------------------------------------------------------------------------
# A. 文言契約
# ---------------------------------------------------------------------------


class RunnerToolGrantTest(ContractTestCase):
    """3 runner の frontmatter が公開する tool 集合。"""

    def test_each_runner_grants_bash_write_and_read(self) -> None:
        for operation, path in RUNNER_AGENTS.items():
            with self.subTest(runner=operation):
                self.assert_tools_line(path)


class ObsoleteLaunchVocabularyTest(ContractTestCase):
    """廃止 tool と起動 mode 指定が、配送される全ての文言から消えていること。"""

    def documents(self) -> dict[str, str]:
        documents = {
            str(path.relative_to(ROOT)): read(path)
            for path in (
                ADVISOR_RUNNER,
                RESCUE_RUNNER,
                REVIEW_RUNNER,
                ADVISOR_RULES,
                ADVISOR_RULES_SUBAGENT,
                CONSULT_SKILL,
                PLUGIN_README,
                MANAGE_RUNNERS,
            )
        }
        documents["README.md (codex-advisor 節)"] = markdown_section(
            read(ROOT_README), ROOT_README_SECTION_HEADING
        )
        return documents

    def test_root_readme_keeps_a_codex_advisor_section(self) -> None:
        """節が消える / 改名されると、以下の不在検査が素通りになるため先に固定する。"""
        section = markdown_section(read(ROOT_README), ROOT_README_SECTION_HEADING)
        self.assertTrue(
            section.strip(),
            f"README.md: '{ROOT_README_SECTION_HEADING}' 節が見つからない",
        )

    def test_removed_tool_is_never_mentioned(self) -> None:
        for label, body in self.documents().items():
            with self.subTest(document=label):
                self.assert_absent(label, body, REMOVED_TOOL)

    def test_agent_launch_mode_is_never_specified(self) -> None:
        for label, body in self.documents().items():
            with self.subTest(document=label):
                self.assert_no_agent_launch_mode(label, body)
                for phrase in FORBIDDEN_AGENT_MODE_PHRASES:
                    self.assert_absent(label, body, phrase)

    def test_root_readme_never_calls_runners_foreground_subagents(self) -> None:
        """plugin 一覧の 1 行説明も含め、root README 全体に foreground 前提を残さない。"""
        self.assert_absent("README.md", read(ROOT_README), "foreground subagent")

    def test_runner_bodies_never_require_foreground_bash(self) -> None:
        for operation, path in RUNNER_AGENTS.items():
            with self.subTest(runner=operation):
                self.assert_absent(
                    str(path.relative_to(ROOT)),
                    read(path),
                    FORBIDDEN_RUNNER_BASH_PHRASE,
                )

    def test_no_document_requires_waiting_inside_the_same_turn(self) -> None:
        for label, body in self.documents().items():
            for phrase in FORBIDDEN_WAIT_IN_TURN_PHRASES:
                with self.subTest(document=label, phrase=phrase):
                    self.assert_absent(label, body, phrase)

    def test_no_document_explains_past_behaviour(self) -> None:
        for label, body in self.documents().items():
            for phrase in FORBIDDEN_HISTORY_PHRASES:
                with self.subTest(document=label, phrase=phrase):
                    self.assert_absent(label, body, phrase)


class LaunchDisciplineClauseTest(ContractTestCase):
    """起動規律の canonical 文が配送 prompt と consult skill にあること。"""

    def test_advisor_rules_states_the_launch_discipline(self) -> None:
        body = read(ADVISOR_RULES)
        for name, clause in LAUNCH_DISCIPLINE_CLAUSES.items():
            with self.subTest(clause=name):
                self.assert_contains("advisor-rules.md", body, clause)

    def test_consult_skill_states_the_launch_discipline(self) -> None:
        body = read(CONSULT_SKILL)
        for name, clause in LAUNCH_DISCIPLINE_CLAUSES.items():
            with self.subTest(clause=name):
                self.assert_contains("consult/SKILL.md", body, clause)

    def test_codex_runner_rule_handles_a_classifier_denial(self) -> None:
        section = rule_section(read(ADVISOR_RULES), "codex-runner")
        if not section:
            self.fail("advisor-rules.md: rule:codex-runner の節が見つからない")
        self.assert_contains(
            "advisor-rules.md (rule:codex-runner)", section, CLASSIFIER_DENIAL_CLAUSE
        )


class RunnerJobTrackingClauseTest(ContractTestCase):
    """3 runner 本文が共有する job 回収・poll 予算の canonical 文。"""

    def test_each_runner_states_the_job_tracking_contract(self) -> None:
        for operation, path in RUNNER_AGENTS.items():
            body = read(path)
            for name, clause in RUNNER_BODY_CLAUSES.items():
                with self.subTest(runner=operation, clause=name):
                    self.assert_contains(str(path.relative_to(ROOT)), body, clause)

    def test_each_runner_keeps_status_slices_within_the_budget(self) -> None:
        for operation, path in RUNNER_AGENTS.items():
            with self.subTest(runner=operation):
                oversized = [
                    value
                    for value in TIMEOUT_MS_ARGUMENT_PATTERN.findall(read(path))
                    if int(value) > POLL_SLICE_TIMEOUT_MS
                ]
                self.assertEqual(
                    [],
                    oversized,
                    f"{path}: --timeout-ms が slice 上限 {POLL_SLICE_TIMEOUT_MS} を超えている",
                )


class AutoModeDocumentationTest(ContractTestCase):
    """README の auto mode 節が classifier の allow 設定を案内すること。"""

    def test_plugin_readme_documents_the_auto_mode_allow_rule(self) -> None:
        section = markdown_section(read(PLUGIN_README), AUTO_MODE_HEADING)
        if not section:
            self.fail(f"{PLUGIN_README}: '{AUTO_MODE_HEADING}' 節が見つからない")
        for literal in AUTO_MODE_REQUIRED_LITERALS:
            with self.subTest(literal=literal):
                self.assert_contains("README.md (auto mode 節)", section, literal)


# ---------------------------------------------------------------------------
# B. hooks.json の構造契約
# ---------------------------------------------------------------------------


class HookManifestStructureTest(unittest.TestCase):
    """hooks.json の event ごとの entry と matcher。"""

    def setUp(self) -> None:
        self.hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]

    def runner_hook_entries(self, event: str) -> list[dict[str, object]]:
        return [
            entry
            for entry in self.hooks.get(event, [])
            if any(
                hook["type"] == "command" and HOOK_SCRIPT_MARKER in hook["command"]
                for hook in entry["hooks"]
            )
        ]

    def runner_hook_entry(self, event: str) -> dict[str, object]:
        """`event` の manage-codex-runners.mjs entry を 1 件だけ取り出す。"""
        entries = self.runner_hook_entries(event)
        if len(entries) != 1:
            self.fail(
                f"{event} の {HOOK_SCRIPT_MARKER} entry は 1 件であること "
                f"(実際: {len(entries)} 件)"
            )
        return entries[0]

    def test_every_lifecycle_event_runs_the_runner_hook(self) -> None:
        for event in LIFECYCLE_EVENTS:
            with self.subTest(event=event):
                self.assertEqual(
                    1,
                    len(self.runner_hook_entries(event)),
                    f"{event} の manage-codex-runners.mjs entry は 1 件であること",
                )

    def test_session_start_runs_the_runner_hook_only_on_a_fresh_context(self) -> None:
        entry = self.runner_hook_entry("SessionStart")
        self.assertEqual(SESSION_START_MATCHER, entry.get("matcher"))
        pattern = re.compile(entry["matcher"])
        for source in SESSION_START_ACCEPTED_SOURCES:
            with self.subTest(source=source):
                self.assertIsNotNone(pattern.fullmatch(source))
        for source in SESSION_START_REJECTED_SOURCES:
            with self.subTest(source=source):
                self.assertIsNone(pattern.fullmatch(source))

    def test_session_start_rule_injection_stays_unmatched(self) -> None:
        """規律注入は全ての session source で行う (matcher を付けない)。"""
        injection_entries = [
            entry
            for entry in self.hooks["SessionStart"]
            if any(
                "inject-advisor-rules.sh" in hook["command"]
                for hook in entry["hooks"]
                if hook["type"] == "command"
            )
        ]
        self.assertEqual(1, len(injection_entries))
        self.assertNotIn("matcher", injection_entries[0])

    def test_permission_denied_entry_matches_agent_launch_tools(self) -> None:
        entry = self.runner_hook_entry("PermissionDenied")
        if "matcher" not in entry:
            self.fail("PermissionDenied entry に matcher が無い")
        pattern = re.compile(entry["matcher"])
        for tool_name in PERMISSION_DENIED_ACCEPTED_TOOLS:
            with self.subTest(tool_name=tool_name):
                self.assertIsNotNone(pattern.fullmatch(tool_name))
        for tool_name in PERMISSION_DENIED_REJECTED_TOOLS:
            with self.subTest(tool_name=tool_name):
                self.assertIsNone(pattern.fullmatch(tool_name))

    def test_existing_entries_keep_their_matchers(self) -> None:
        expected_matchers = {
            "PreToolUse": "Bash",
            "PostToolUse": "^SubagentHandback$",
        }
        for event, matcher in expected_matchers.items():
            with self.subTest(event=event):
                self.assertEqual(matcher, self.runner_hook_entry(event).get("matcher"))
        for event in ("SubagentStart", "SubagentStop"):
            with self.subTest(event=event):
                pattern = re.compile(self.runner_hook_entry(event)["matcher"])
                for runner in RUNNERS.values():
                    self.assertIsNotNone(pattern.fullmatch(runner), runner)
        for event in ("Stop", "SessionEnd"):
            with self.subTest(event=event):
                self.assertNotIn("matcher", self.runner_hook_entry(event))


# ---------------------------------------------------------------------------
# C. manage-codex-runners.mjs の挙動契約
# ---------------------------------------------------------------------------


class BackgroundRunnerHarness(HookHarness):
    """Stop の `background_tasks` と PermissionDenied を送れるようにした harness。"""

    @staticmethod
    def subagent_task(
        operation: str, *, task_id: str = "bg-1", status: str = "running"
    ) -> dict[str, object]:
        return {
            "id": task_id,
            "type": "subagent",
            "status": status,
            "description": "codex runner",
            "agent_type": RUNNERS[operation],
        }

    @staticmethod
    def shell_task(command: str, *, task_id: str = "bg-shell") -> dict[str, object]:
        return {
            "id": task_id,
            "type": "shell",
            "status": "running",
            "description": "shell task",
            "command": command,
        }

    def stop(
        self,
        *,
        session_id: str = "session-a",
        background_tasks: list[dict[str, object]] | None = None,
    ) -> dict[str, object] | None:
        payload: dict[str, object] = {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "stop_hook_active": False,
        }
        if background_tasks is not None:
            payload["background_tasks"] = background_tasks
        return self.hook_response(payload)

    def permission_denied(
        self,
        *,
        subagent_type: str,
        session_id: str = "session-a",
        tool_name: str = "Agent",
    ) -> subprocess.CompletedProcess[str]:
        return self.run_hook(
            {
                "hook_event_name": "PermissionDenied",
                "session_id": session_id,
                "tool_name": tool_name,
                "tool_input": {
                    "subagent_type": subagent_type,
                    "description": "codex runner",
                    "prompt": "run the codex job",
                },
                "tool_use_id": "toolu_denied",
                "reason": "Blocked by classifier",
            }
        )

    def records_for(self, session_id: str = "session-a") -> list[dict[str, object]]:
        return [
            record
            for record in self.state_records()
            if record["sessionId"] == session_id
        ]

    def assert_stop_waits(
        self, response: dict[str, object] | None, expected_runner: str
    ) -> None:
        self.assertIsNotNone(response)
        assert response is not None
        self.assertNotIn("decision", response)
        hook_output = response["hookSpecificOutput"]
        assert isinstance(hook_output, dict)
        self.assertEqual("Stop", hook_output["hookEventName"])
        context = hook_output["additionalContext"]
        assert isinstance(context, str)
        self.assertIn(expected_runner, context)
        self.assertIn("notification", context)

    def assert_not_blocked(self, response: dict[str, object] | None) -> None:
        if response is None:
            return
        self.assertNotEqual("block", response.get("decision"), response)


class StopGateBackgroundTaskTest(BackgroundRunnerHarness):
    """Stop hook が `background_tasks` と active record を突き合わせること。"""

    def test_in_flight_runner_is_awaited_instead_of_blocked(self) -> None:
        self.subagent_start("rescue")
        response = self.stop(background_tasks=[self.subagent_task("rescue")])
        self.assert_stop_waits(response, RUNNERS["rescue"])
        # 待機の通知は state を消費しない (report 到着後の遷移は SubagentStop が行う)。
        self.assertEqual(1, len(self.records_for()))

    def test_missing_background_task_blocks_as_lost_tracking(self) -> None:
        self.subagent_start("rescue")
        self.assert_stop_blocked(self.stop(background_tasks=[]), RUNNERS["rescue"])

    def test_other_agent_type_or_task_type_does_not_count_as_in_flight(self) -> None:
        cases = {
            "other-subagent": [
                {
                    "id": "bg-explore",
                    "type": "subagent",
                    "status": "running",
                    "description": "explore",
                    "agent_type": "Explore",
                }
            ],
            "shell-task": [
                self.shell_task(f"bash {JOB_HELPER_PATH} advisor /tmp/prompt.md")
            ],
        }
        for name, background_tasks in cases.items():
            with self.subTest(case=name):
                session_id = f"session-{name}"
                self.subagent_start("advisor", session_id=session_id)
                self.assert_stop_blocked(
                    self.stop(
                        session_id=session_id, background_tasks=background_tasks
                    ),
                    RUNNERS["advisor"],
                )

    def test_absent_background_tasks_field_falls_back_to_records(self) -> None:
        self.subagent_start("review")
        self.assert_stop_blocked(self.stop(), RUNNERS["review"])

    def test_two_parallel_runners_report_only_the_untracked_one(self) -> None:
        self.subagent_start("rescue", agent_id="agent-rescue")
        self.subagent_start("review", agent_id="agent-review")
        response = self.stop(
            background_tasks=[self.subagent_task("review", task_id="bg-review")]
        )
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual("block", response["decision"])
        reason = response["reason"]
        assert isinstance(reason, str)
        self.assertIn(RUNNERS["rescue"], reason)
        self.assertNotIn(RUNNERS["review"], reason)

    def test_no_records_and_no_background_tasks_allows_the_stop(self) -> None:
        self.assertIsNone(self.stop(background_tasks=[]))

    def test_background_task_without_a_record_allows_the_stop(self) -> None:
        self.assertIsNone(self.stop(background_tasks=[self.subagent_task("advisor")]))

    def test_retry_required_record_still_blocks(self) -> None:
        self.subagent_start("review", agent_id="agent-first")
        self.subagent_stop("review", "retryable-failure", agent_id="agent-first")
        self.assert_stop_blocked(self.stop(background_tasks=[]), RUNNERS["review"])


class PermissionDeniedTest(BackgroundRunnerHarness):
    """classifier に拒否された runner 起動が Stop の block ループを残さないこと。"""

    def assert_denied_record(self, operation: str) -> None:
        records = self.records_for()
        self.assertEqual(1, len(records))
        self.assertEqual(operation, records[0]["operation"])
        self.assertEqual("denied", records[0]["phase"])

    def test_denied_launch_moves_an_active_record_to_denied(self) -> None:
        self.subagent_start("rescue")
        result = self.permission_denied(subagent_type=RUNNERS["rescue"])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout.strip())
        self.assert_denied_record("rescue")
        self.assert_not_blocked(self.stop(background_tasks=[]))

    def test_denied_launch_moves_a_retry_required_record_to_denied(self) -> None:
        self.subagent_start("review", agent_id="agent-first")
        self.subagent_stop("review", "retryable-failure", agent_id="agent-first")
        self.permission_denied(subagent_type=RUNNERS["review"])
        self.assert_denied_record("review")
        self.assert_not_blocked(self.stop(background_tasks=[]))

    def test_task_tool_denial_is_handled_like_the_agent_tool(self) -> None:
        self.subagent_start("advisor")
        self.permission_denied(subagent_type=RUNNERS["advisor"], tool_name="Task")
        self.assert_denied_record("advisor")

    def test_denial_of_another_subagent_or_tool_keeps_the_record(self) -> None:
        cases = {
            "other-subagent": {"subagent_type": "Explore", "tool_name": "Agent"},
            "other-tool": {
                "subagent_type": RUNNERS["rescue"],
                "tool_name": "Bash",
            },
        }
        for name, arguments in cases.items():
            with self.subTest(case=name):
                session_id = f"session-{name}"
                self.subagent_start("rescue", session_id=session_id)
                result = self.permission_denied(session_id=session_id, **arguments)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("", result.stdout.strip())
                records = self.records_for(session_id)
                self.assertEqual(1, len(records))
                self.assertEqual("active", records[0]["phase"])


class PreToolUseGateHardeningTest(BackgroundRunnerHarness):
    """PreToolUse gate が command substitution と wrapper を解析すること。"""

    def assert_denied_for_any_runner(self, command: str) -> None:
        response = self.hook_response(self.bash_payload(command))
        self.assertIsNotNone(response, command)
        assert response is not None
        hook_output = response["hookSpecificOutput"]
        assert isinstance(hook_output, dict)
        self.assertEqual("deny", hook_output["permissionDecision"], command)

    def test_command_substitution_is_classified(self) -> None:
        cases = {
            "dollar-paren": (
                f'echo $(bash "{JOB_HELPER_PATH}" advisor /tmp/prompt.md)',
                "advisor",
            ),
            "backticks": (
                f'echo `bash "{JOB_HELPER_PATH}" advisor /tmp/prompt.md`',
                "advisor",
            ),
            "nested": (
                f'echo $(echo $(node "{COMPANION_PATH}" task --background --json))',
                "rescue",
            ),
        }
        for name, (command, operation) in cases.items():
            with self.subTest(case=name):
                self.assert_denied(
                    self.hook_response(self.bash_payload(command)),
                    RUNNERS[operation],
                )

    def test_wrappers_are_stripped_before_classification(self) -> None:
        cases = {
            "sudo": (f'sudo node "{COMPANION_PATH}" task --background', "rescue"),
            "watch": (f'watch bash "{JOB_HELPER_PATH}" review --scope branch', "review"),
            "setsid": (f'setsid node "{COMPANION_PATH}" review --wait', "review"),
            "ionice": (f'ionice node "{COMPANION_PATH}" task --background', "rescue"),
            "stdbuf": (
                f'stdbuf -oL node "{COMPANION_PATH}" task --background',
                "rescue",
            ),
            "eval-quoted": (
                f'eval "bash {JOB_HELPER_PATH} advisor /tmp/prompt.md"',
                "advisor",
            ),
            "eval-bare": (
                f'eval bash "{JOB_HELPER_PATH}" advisor /tmp/prompt.md',
                "advisor",
            ),
            "xargs": (
                f'printf "%s\\n" /tmp/prompt.md | xargs bash "{JOB_HELPER_PATH}" advisor',
                "advisor",
            ),
        }
        for name, (command, operation) in cases.items():
            with self.subTest(case=name):
                self.assert_denied(
                    self.hook_response(self.bash_payload(command)),
                    RUNNERS[operation],
                )

    def test_unresolvable_command_name_with_a_codex_path_is_denied(self) -> None:
        commands = {
            "dollar-var-companion": f'$CMD "{COMPANION_PATH}" task --background --json',
            "braced-var-job-helper": (
                f'${{CMD}} bash "{JOB_HELPER_PATH}" advisor /tmp/prompt.md'
            ),
            "braced-var-advisor-wrapper": f'${{RUNNER}} "{ADVISOR_WRAPPER_PATH}"',
        }
        for name, command in commands.items():
            with self.subTest(case=name):
                self.assert_denied_for_any_runner(command)

    def test_fail_closed_stays_scoped_to_codex_paths(self) -> None:
        """解析不能な command 名でも、codex の path 断片が無ければ deny しない。"""
        for command in ("$CMD --help", "${CMD} status"):
            with self.subTest(command=command):
                self.assertIsNone(self.hook_response(self.bash_payload(command)))

    def test_reading_and_managing_commands_stay_allowed(self) -> None:
        commands = (
            f"cat {JOB_HELPER_PATH}",
            f"rg -n 'run-codex-job.sh advisor' {JOB_HELPER_PATH}",
            f'echo $(node "{COMPANION_PATH}" status task-123 --json)',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(self.hook_response(self.bash_payload(command)))


# ---------------------------------------------------------------------------
# D. companion adapter の cancel timeout
# ---------------------------------------------------------------------------


class CancelTimeoutTest(unittest.TestCase):
    """`run-codex-job.sh cancel` が無期限にブロックしないこと。"""

    def test_cancel_timeout_defaults_to_thirty_seconds(self) -> None:
        body = read(JOB_HELPER)
        self.assertIsNotNone(
            CANCEL_TIMEOUT_DEFAULT_PATTERN.search(body),
            f"{JOB_HELPER}: cancel の timeout 既定値 "
            f"(${{{CANCEL_TIMEOUT_ENV}:-{CANCEL_TIMEOUT_DEFAULT_SECONDS}}}) が無い",
        )

    def test_cancel_gives_up_when_the_companion_does_not_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temp = Path(temporary_name)
            companion = (
                temp
                / "home/.claude/plugins/cache/openai-codex/codex/1.0.6/scripts"
                / "codex-companion.mjs"
            )
            companion.parent.mkdir(parents=True)
            companion.write_text("// fake companion\n", encoding="utf-8")
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fake_node = fake_bin / "node"
            # cancel が返らない companion。wrapper 側の timeout だけが実行を終わらせる。
            fake_node.write_text("#!/bin/bash\nsleep 5\n", encoding="utf-8")
            fake_node.chmod(0o755)
            env = {
                "HOME": str(temp / "home"),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                CANCEL_TIMEOUT_ENV: "1",
            }

            started = time.monotonic()
            try:
                result = subprocess.run(
                    ["bash", str(JOB_HELPER), "cancel", "task-test"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=temp,
                    env=env,
                    check=False,
                    timeout=15,
                )
            except subprocess.TimeoutExpired:
                self.fail("cancel が timeout 設定を超えても終了しなかった")
            elapsed = time.monotonic() - started
            self.assertNotEqual(0, result.returncode, result.stdout)
            self.assertLess(elapsed, 10)
            self.assertTrue(result.stderr.strip(), "timeout の理由が報告されていない")


# ---------------------------------------------------------------------------
# helper 自体の自己テスト
# ---------------------------------------------------------------------------


class MarkdownSectionHelperTest(unittest.TestCase):
    """`markdown_section` / `rule_section` の抽出規則。"""

    def test_section_ends_at_the_next_same_level_heading(self) -> None:
        body = "## target\n\nkeep this\n\n## next\n\ndrop this\n"
        section = markdown_section(body, "## target")
        self.assertIn("keep this", section)
        self.assertNotIn("drop this", section)

    def test_deeper_headings_stay_inside_the_section(self) -> None:
        body = "## target\n\nkeep this\n\n### detail\n\nkeep this too\n"
        section = markdown_section(body, "## target")
        self.assertIn("keep this too", section)

    def test_headings_inside_a_fence_are_ignored(self) -> None:
        body = (
            "## target\n\n"
            "```bash\n"
            "# comment that looks like a heading\n"
            "```\n\n"
            "keep this\n\n"
            "## next\n\n"
            "drop this\n"
        )
        section = markdown_section(body, "## target")
        self.assertIn("keep this", section)
        self.assertNotIn("drop this", section)

    def test_missing_heading_returns_an_empty_string(self) -> None:
        self.assertEqual("", markdown_section("# other\n", "## target"))

    def test_rule_section_is_bounded_by_the_next_rule_marker(self) -> None:
        body = (
            "<!-- rule:first -->\n## 1\n\nfirst body\n\n"
            "<!-- rule:second -->\n## 2\n\nsecond body\n"
        )
        section = rule_section(body, "first")
        self.assertIn("first body", section)
        self.assertNotIn("second body", section)


class AgentLaunchModeHelperTest(unittest.TestCase):
    """`agent_launch_mode_hits` が Agent 起動指示だけを検出すること。"""

    def test_launch_instruction_with_the_parameter_is_detected(self) -> None:
        text = (
            '`codex-advisor:rescue-runner` を Agent tool で `model: "sonnet"`、'
            "`run_in_background: false` で起動する\n"
        )
        self.assertEqual(1, len(agent_launch_mode_hits(text)))

    def test_launch_instruction_split_across_lines_is_detected(self) -> None:
        text = (
            '親はこの agent を `subagent_type: "codex-advisor:advisor-runner"`、\n'
            '`model: "sonnet"`、`run_in_background: false` で\n'
            "起動する。\n"
        )
        self.assertEqual(1, len(agent_launch_mode_hits(text)))

    def test_natural_language_foreground_demand_is_detected(self) -> None:
        text = (
            "`codex-advisor:review-runner` を Agent tool で foreground 起動する。\n"
        )
        self.assertEqual(1, len(agent_launch_mode_hits(text)))

    def test_bash_level_option_description_is_not_detected(self) -> None:
        text = (
            "- snapshot は Bash tool の `run_in_background: false` で 1 回だけ実行する\n"
            "- wrapper は foreground 起動のまま結果を観察する\n"
        )
        self.assertEqual([], agent_launch_mode_hits(text))

    def test_launch_instruction_without_a_mode_is_not_detected(self) -> None:
        text = (
            '`codex-advisor:advisor-runner` を Agent tool で `model: "sonnet"` を'
            "指定して起動する\n"
        )
        self.assertEqual([], agent_launch_mode_hits(text))


if __name__ == "__main__":
    unittest.main()

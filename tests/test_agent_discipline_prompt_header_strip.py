"""agent-discipline: 注入スクリプトは prompt ファイル先頭の保守者向け HTML コメントを除いて配送する。

対象の配送 (``DELIVERIES``) は、次の 7 スクリプト・8 配送経路が読む prompt ファイルである。

- inject-always.sh (SessionStart): delivery-note.md と always-1.md
- inject-rules-part.sh 2 / 3 (UserPromptSubmit): always-2.md / always-3.md
- inject-discipline.sh (UserPromptSubmit): discipline.md
- inject-subagent-rules.sh (SubagentStart): subagent-rules.md
- inject-temporary.sh (SessionStart): temporary/*.md
- inject-auto.sh (UserPromptSubmit、permission_mode が auto): auto-mode.md
- check-uncommitted-on-session-start.sh (UserPromptSubmit、permission_mode が auto): uncommitted-check.md

「先頭コメントを除いた本文」(``body_without_leading_comment``) は次のとおりに定める。

- ファイルが ``<!--`` で始まり、その後ろに ``-->`` がある場合は、最初の ``-->`` までの
  コメントと、その直後に続く空行を除く
- ファイルが ``<!--`` で始まらない場合は、内容をそのまま使う (先頭の空行も残す)
- ``-->`` が無い (先頭コメントが閉じていない) 場合は、内容をそのまま使う
- 先頭のコメントが rule マーカー (``<!-- rule:<id> -->`` / ``<!-- subagent-rule:<id> -->``)
  の場合は、内容をそのまま使う
- 先頭コメントより後ろにある HTML コメント (``<!-- rule:<id> -->`` マーカーを含む) と、
  先頭の空行以外の本文は変えない
- hook は prompt ファイルを ``$(...)`` で読むため、末尾の改行は落ちる

各テストクラスが検査する規則:

- ``BodyHelperTest``: ``body_without_leading_comment`` が、境界ケースの fixture
  (``BOUNDARY_CASES``) それぞれで期待する本文を返す。
- ``RealPromptDeliveryTest``: 実際の prompt ファイルで各スクリプトを実行すると、
  (1) 出力 JSON は ``{"hookSpecificOutput": {"hookEventName": <event>, "additionalContext":
  <文字列>}}`` だけを持ち、(2) additionalContext に prompt ファイルの先頭コメントが含まれず、
  (3) additionalContext は先頭コメントを除いた本文を各スクリプトの組み立て方で並べたものに
  一致し、(4) prompt ファイルの rule マーカー (``<!-- rule:<id> -->`` /
  ``<!-- subagent-rule:<id> -->``) が同じ順序ですべて残る。
- ``BoundaryCaseDeliveryTest``: plugin の hooks/ を一時ディレクトリへ複製し、対象の prompt
  ファイルを境界ケースの fixture に差し替えて各スクリプトを実行すると、additionalContext は
  fixture の期待する本文を各スクリプトの組み立て方で並べたものに一致する。境界ケースは、
  先頭にコメントが無いファイル (先頭の空行も残す)、先頭コメントが閉じていないファイル、
  本文の途中に HTML コメントがあるファイル、先頭コメントの直後に rule マーカーがあるファイル、
  1 行目が rule マーカーのファイル、先頭コメントの後に空行が続くファイルである。

hook は hooks.json と同じく shebang で直接実行し、tempfile で作った一時ディレクトリを
``TMPDIR`` / ``HOME`` / ``XDG_CACHE_HOME`` として env で渡す (親プロセスの env を継承しない)。
複製した plugin と一時ファイルはリポジトリの外に置く。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "agent-discipline"
PLUGIN_DIR = ROOT / "plugins" / PLUGIN_NAME
HOOKS_DIR = PLUGIN_DIR / "hooks"

SESSION_ID = "prompt-header-strip-session"

# inject-always.sh が additionalContext の先頭に置く自己修復指示と、prompts ディレクトリの行。
SELF_HEAL = (
    "(自己修復) このメッセージが persisted-output として退避されている場合は、"
    "スタブに記載されたパスの退避ファイルを Read で全文読了してから作業を開始すること。"
)
PATH_LINE_PREFIX = "(参照パス) "
DISCIPLINE_HEADING = "# agent-discipline: 分業規律"

# check-uncommitted-on-session-start.sh が穴埋めするプレースホルダ。
CWD_PLACEHOLDER = "{{CWD}}"
DIRTY_PLACEHOLDER = "{{DIRTY}}"

LEADING_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
LEADING_BLANK_LINES_PATTERN = re.compile(r"\A(?:[ \t]*\n)+")
RULE_MARKER_PATTERN = re.compile(r"<!--\s*(?:subagent-)?rule:[A-Za-z0-9_-]+\s*-->")


def body_without_leading_comment(text: str) -> str:
    """prompt ファイルの内容から、配送される本文 (先頭の保守者向けコメントを除いたもの) を求める。

    規則は module docstring の「先頭コメントを除いた本文」のとおり。
    """
    match = LEADING_COMMENT_PATTERN.match(text)
    if match is None or RULE_MARKER_PATTERN.fullmatch(match.group(0)):
        return text.rstrip("\n")
    rest = LEADING_BLANK_LINES_PATTERN.sub("", text[match.end() :], count=1)
    return rest.rstrip("\n")


def leading_comment(text: str) -> str | None:
    """ファイル先頭の閉じた HTML コメント (保守者向けメモ)。無い場合と、先頭が rule マーカーの
    場合は None。"""
    match = LEADING_COMMENT_PATTERN.match(text)
    if match is None or RULE_MARKER_PATTERN.fullmatch(match.group(0)):
        return None
    return match.group(0)


def rule_markers(text: str) -> list[str]:
    return RULE_MARKER_PATTERN.findall(text)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def detect_utf8_locale() -> str | None:
    """`locale -a` から UTF-8 系 locale を 1 つ選ぶ (見つからなければ None)。

    inject-always.sh は ``wc -m`` で文字数を数えるため、UTF-8 locale でないと日本語を
    バイト数で数え、8K ガードで delivery-note を落とす。
    """
    try:
        result = subprocess.run(
            ["locale", "-a"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    candidates = []
    for name in (line.strip() for line in result.stdout.splitlines()):
        normalized = name.lower().replace("-", "")
        if "utf8" not in normalized:
            continue
        if normalized.startswith("c."):
            candidates.append((0, name))
        elif normalized.startswith("en_us."):
            candidates.append((1, name))
        else:
            candidates.append((2, name))
    return min(candidates)[1] if candidates else None


UTF8_LOCALE = detect_utf8_locale()


# ---------------------------------------------------------------------------
# 配送経路
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Delivery:
    """1 つの配送経路: 実行するスクリプト・入力・読む prompt ファイル・組み立て方。

    ``assemble`` は、各 prompt ファイルの先頭コメントを除いた本文 (``targets`` の順) と
    prompts ディレクトリの絶対パスから、期待する additionalContext を組み立てる。
    """

    label: str
    script: str
    event: str
    targets: tuple[str, ...]
    assemble: Callable[[list[str], Path], str]
    args: tuple[str, ...] = ()
    extra_input: dict[str, str] = field(default_factory=dict)
    needs_dirty_repository: bool = False
    needs_utf8_locale: bool = False


def assemble_always(bodies: list[str], prompts_dir: Path) -> str:
    note, core = bodies
    return f"{SELF_HEAL}\n\n{note}\n{PATH_LINE_PREFIX}{prompts_dir}\n\n{core}"


def assemble_single(bodies: list[str], _prompts_dir: Path) -> str:
    (body,) = bodies
    return body


def assemble_discipline(bodies: list[str], _prompts_dir: Path) -> str:
    (body,) = bodies
    return f"{DISCIPLINE_HEADING}\n\n{body}"


def assemble_temporary(bodies: list[str], _prompts_dir: Path) -> str:
    return "\n\n".join(body for body in bodies if body)


AUTO_INPUT = {"permission_mode": "auto"}

DELIVERIES = (
    Delivery(
        label="inject-always.sh (delivery-note.md + always-1.md)",
        script="inject-always.sh",
        event="SessionStart",
        targets=("delivery-note.md", "always-1.md"),
        assemble=assemble_always,
        needs_utf8_locale=True,
    ),
    Delivery(
        label="inject-rules-part.sh 2 (always-2.md)",
        script="inject-rules-part.sh",
        args=("2",),
        event="UserPromptSubmit",
        targets=("always-2.md",),
        assemble=assemble_single,
    ),
    Delivery(
        label="inject-rules-part.sh 3 (always-3.md)",
        script="inject-rules-part.sh",
        args=("3",),
        event="UserPromptSubmit",
        targets=("always-3.md",),
        assemble=assemble_single,
    ),
    Delivery(
        label="inject-discipline.sh (discipline.md)",
        script="inject-discipline.sh",
        event="UserPromptSubmit",
        targets=("discipline.md",),
        assemble=assemble_discipline,
    ),
    Delivery(
        label="inject-subagent-rules.sh (subagent-rules.md)",
        script="inject-subagent-rules.sh",
        event="SubagentStart",
        targets=("subagent-rules.md",),
        assemble=assemble_single,
        extra_input={"agent_id": "prompt-header-strip-agent", "agent_type": "general-purpose"},
    ),
    Delivery(
        label="inject-temporary.sh (temporary/*.md)",
        script="inject-temporary.sh",
        event="SessionStart",
        # 実ファイルの検査では temporary/ 配下の md を辞書順で読む (real_targets 参照)。
        # 境界ケースでは temporary/ を空にしてこの 2 ファイルを置く。
        targets=("temporary/10-first.md", "temporary/20-second.md"),
        assemble=assemble_temporary,
    ),
    Delivery(
        label="inject-auto.sh (auto-mode.md)",
        script="inject-auto.sh",
        event="UserPromptSubmit",
        targets=("auto-mode.md",),
        assemble=assemble_single,
        extra_input=AUTO_INPUT,
    ),
    Delivery(
        label="check-uncommitted-on-session-start.sh (uncommitted-check.md)",
        script="check-uncommitted-on-session-start.sh",
        event="UserPromptSubmit",
        targets=("uncommitted-check.md",),
        assemble=assemble_single,
        extra_input=AUTO_INPUT,
        needs_dirty_repository=True,
    ),
)


def real_targets(delivery: Delivery, prompts_dir: Path) -> tuple[str, ...]:
    """実際の prompt ファイルで検査するときに delivery が読むファイル (prompts_dir からの相対)。"""
    if delivery.script != "inject-temporary.sh":
        return delivery.targets
    names = sorted(path.name for path in (prompts_dir / "temporary").glob("*.md"))
    return tuple(f"temporary/{name}" for name in names)


# ---------------------------------------------------------------------------
# 境界ケースの fixture
# ---------------------------------------------------------------------------

HEADER_SENTINEL = "B22-HEADER-SENTINEL"
HEADER = (
    "<!--\n"
    f"  保守者向けメモ: この行は配送しない ({HEADER_SENTINEL})\n"
    "  2 行目のメモ\n"
    "-->\n"
)
# check-uncommitted-on-session-start.sh のテンプレートとしても成り立つよう、どの fixture も
# プレースホルダを 1 回ずつ持つ (他のスクリプトにとってはただの文字列である)。
PLACEHOLDER_BLOCK = f"`{CWD_PLACEHOLDER}` の変更:\n\n```\n{DIRTY_PLACEHOLDER}\n```\n"
BODY_WITH_COMMENTS = (
    "# 見出し\n"
    "\n"
    "本文の 1 段落目。\n"
    "\n"
    "<!-- rule:sample-rule -->\n"
    "## 1. サンプル規則\n"
    "\n"
    "行の途中のコメント <!-- 本文途中の 1 行コメント --> を含む段落。\n"
    "\n"
    "<!--\n"
    "  本文途中の複数行コメント\n"
    "-->\n"
    "\n"
    "<!-- subagent-rule:sample-subagent-rule -->\n"
    + PLACEHOLDER_BLOCK
)
BODY_STARTING_WITH_RULE_MARKER = (
    "<!-- rule:first-rule -->\n"
    "## 1. 先頭の規則\n"
    "\n"
    "本文。\n"
    "\n"
    + PLACEHOLDER_BLOCK
)
BODY_WITH_BLANK_LINE_RUNS = (
    "# 見出し\n"
    "\n"
    "\n"
    "\n"
    "空行 3 行の後の段落。\n"
    "\n"
    + PLACEHOLDER_BLOCK
    + "\n"
    "\n"
)
UNCLOSED_TEXT = (
    "<!--\n"
    "  閉じていない保守者向けメモ\n"
    "\n"
    "# 見出し\n"
    "\n"
    "本文。\n"
    "\n"
    + PLACEHOLDER_BLOCK
)


@dataclass(frozen=True)
class BoundaryCase:
    """prompt ファイルの内容 (``text``) と、配送される本文の期待値 (``expected_body``)。"""

    label: str
    text: str
    expected_body: str


NO_HEADER_CASES = (
    BoundaryCase(
        label="先頭にコメントが無い",
        text=BODY_WITH_COMMENTS,
        expected_body=BODY_WITH_COMMENTS.rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭にコメントが無く、先頭に空行がある",
        text="\n\n" + BODY_WITH_COMMENTS,
        expected_body=("\n\n" + BODY_WITH_COMMENTS).rstrip("\n"),
    ),
)
UNCLOSED_CASES = (
    BoundaryCase(
        label="先頭コメントが閉じていない",
        text=UNCLOSED_TEXT,
        expected_body=UNCLOSED_TEXT.rstrip("\n"),
    ),
)
BODY_COMMENT_CASES = (
    BoundaryCase(
        label="本文の途中に HTML コメントがある",
        text=HEADER + "\n" + BODY_WITH_COMMENTS,
        expected_body=BODY_WITH_COMMENTS.rstrip("\n"),
    ),
)
RULE_MARKER_AFTER_HEADER_CASES = (
    BoundaryCase(
        label="先頭コメントの直後の行に rule マーカーがある",
        text=HEADER + BODY_STARTING_WITH_RULE_MARKER,
        expected_body=BODY_STARTING_WITH_RULE_MARKER.rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭コメントと rule マーカーの間に空行がある",
        text=HEADER + "\n" + BODY_STARTING_WITH_RULE_MARKER,
        expected_body=BODY_STARTING_WITH_RULE_MARKER.rstrip("\n"),
    ),
)
RULE_MARKER_FIRST_CASES = (
    BoundaryCase(
        label="ファイルの 1 行目が rule マーカー",
        text=BODY_STARTING_WITH_RULE_MARKER,
        expected_body=BODY_STARTING_WITH_RULE_MARKER.rstrip("\n"),
    ),
)
BLANK_LINE_CASES = (
    BoundaryCase(
        label="先頭コメントの後に空行が 3 行ある",
        text=HEADER + "\n\n\n" + BODY_WITH_BLANK_LINE_RUNS,
        expected_body=BODY_WITH_BLANK_LINE_RUNS.rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭コメントの直後に本文がある",
        text=HEADER + BODY_WITH_BLANK_LINE_RUNS,
        expected_body=BODY_WITH_BLANK_LINE_RUNS.rstrip("\n"),
    ),
)
BOUNDARY_CASES = (
    *NO_HEADER_CASES,
    *UNCLOSED_CASES,
    *BODY_COMMENT_CASES,
    *RULE_MARKER_AFTER_HEADER_CASES,
    *RULE_MARKER_FIRST_CASES,
    *BLANK_LINE_CASES,
)


# ---------------------------------------------------------------------------
# hook の実行
# ---------------------------------------------------------------------------


def isolated_env(temp: Path) -> dict[str, str]:
    """親プロセスの env を継承せず、PATH・locale と隔離ディレクトリだけを渡す env を作る。"""
    env = {"PATH": os.environ["PATH"]}
    for key, name in (("HOME", "home"), ("TMPDIR", "tmp"), ("XDG_CACHE_HOME", "cache")):
        directory = temp / name
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    if UTF8_LOCALE is not None:
        env["LC_ALL"] = UTF8_LOCALE
        env["LANG"] = UTF8_LOCALE
    return env


def create_dirty_repository(temp: Path) -> Path:
    """未コミットの変更を持つ git work tree を作る。"""
    repository = temp / "dirty-repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(repository)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (repository / "uncommitted.txt").write_text("uncommitted\n", encoding="utf-8")
    return repository


def porcelain_status(repository: Path) -> str:
    """check-uncommitted-on-session-start.sh が埋め込む git status の行 (末尾改行なし)。"""
    return subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")


def fill_placeholders(body: str, repository: Path) -> str:
    return body.replace(CWD_PLACEHOLDER, str(repository), 1).replace(
        DIRTY_PLACEHOLDER, porcelain_status(repository), 1
    )


@dataclass(frozen=True)
class HookRun:
    result: subprocess.CompletedProcess[str]
    repository: Path | None


def run_delivery(delivery: Delivery, hooks_dir: Path, temp: Path) -> HookRun:
    """hooks_dir 配下のスクリプトを shebang で直接実行する。"""
    env = isolated_env(temp)
    body: dict[str, str] = {"hook_event_name": delivery.event, "session_id": SESSION_ID}
    body.update(delivery.extra_input)
    repository = None
    if delivery.needs_dirty_repository:
        repository = create_dirty_repository(temp)
        body["cwd"] = str(repository)
    result = subprocess.run(
        [str(hooks_dir / "scripts" / delivery.script), *delivery.args],
        cwd=ROOT,
        env=env,
        input=json.dumps(body, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
        check=False,
    )
    return HookRun(result=result, repository=repository)


def copy_hooks(temp: Path) -> Path:
    """plugin の hooks/ を一時ディレクトリへ複製し、複製した hooks/ を返す。"""
    hooks_dir = temp / "plugin" / PLUGIN_NAME / "hooks"
    shutil.copytree(HOOKS_DIR, hooks_dir)
    return hooks_dir


class DeliveryTestCase(unittest.TestCase):
    """hook の出力 JSON を読むための共通 assertion。"""

    def context_of(self, run: HookRun, delivery: Delivery) -> str:
        """出力 JSON の形を検査し、additionalContext を返す。"""
        result = run.result
        label = delivery.label
        self.assertEqual(
            0, result.returncode, f"{label}: exit code が 0 でない (stderr: {result.stderr})"
        )
        self.assertTrue(result.stdout.strip(), f"{label}: 出力が無い (stderr: {result.stderr})")
        output = json.loads(result.stdout)
        self.assertEqual(
            {"hookSpecificOutput"}, set(output), f"{label}: 出力 JSON の最上位のキーが違う"
        )
        hook_output = output["hookSpecificOutput"]
        self.assertEqual(
            {"hookEventName", "additionalContext"},
            set(hook_output),
            f"{label}: hookSpecificOutput のキーが違う",
        )
        self.assertEqual(
            delivery.event, hook_output["hookEventName"], f"{label}: hookEventName が違う"
        )
        self.assertIsInstance(
            hook_output["additionalContext"], str, f"{label}: additionalContext が文字列でない"
        )
        return hook_output["additionalContext"]

    def skip_without_utf8_locale(self, delivery: Delivery) -> bool:
        """UTF-8 locale が無いと組み立てを比較できない配送経路なら True。"""
        return delivery.needs_utf8_locale and UTF8_LOCALE is None


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------


class BodyHelperTest(unittest.TestCase):
    """``body_without_leading_comment`` は境界ケースの fixture で期待する本文を返す。"""

    def test_helper_returns_the_expected_body_for_each_boundary_case(self) -> None:
        for case in BOUNDARY_CASES:
            with self.subTest(case=case.label):
                self.assertEqual(
                    case.expected_body,
                    body_without_leading_comment(case.text),
                    f"{case.label}: helper の本文が fixture の期待値と違う",
                )


@unittest.skipUnless(shutil.which("jq"), "hook の実行には jq が必要")
class RealPromptDeliveryTest(DeliveryTestCase):
    """実際の prompt ファイルを読む各スクリプトの出力。"""

    PROMPTS_DIR = HOOKS_DIR / "prompts"

    def deliver(self, delivery: Delivery) -> tuple[str, str]:
        """スクリプトを実行し、(additionalContext, 期待する additionalContext) を返す。

        期待値は、各 prompt ファイルの先頭コメントを除いた本文を delivery の組み立て方で
        並べたもの (check-uncommitted-on-session-start.sh はプレースホルダを穴埋めしたもの)。
        """
        with tempfile.TemporaryDirectory() as temporary:
            run = run_delivery(delivery, HOOKS_DIR, Path(temporary))
            context = self.context_of(run, delivery)
            bodies = [body_without_leading_comment(text) for _, text in self.sources(delivery)]
            if run.repository is not None:
                bodies = [fill_placeholders(body, run.repository) for body in bodies]
            expected = delivery.assemble(bodies, self.PROMPTS_DIR)
        return context, expected

    def sources(self, delivery: Delivery) -> list[tuple[str, str]]:
        """(prompts からの相対パス, ファイル内容) の組。"""
        return [
            (name, read(self.PROMPTS_DIR / name))
            for name in real_targets(delivery, self.PROMPTS_DIR)
        ]

    def test_output_keeps_the_hook_json_shape(self) -> None:
        for delivery in DELIVERIES:
            with self.subTest(delivery=delivery.label):
                self.deliver(delivery)

    def test_leading_comment_of_the_prompt_is_not_delivered(self) -> None:
        for delivery in DELIVERIES:
            with self.subTest(delivery=delivery.label):
                context, _ = self.deliver(delivery)
                for name, text in self.sources(delivery):
                    header = leading_comment(text)
                    if header is None:
                        continue
                    self.assertNotIn(
                        header,
                        context,
                        f"{delivery.label}: {name} の先頭コメントが additionalContext に含まれる",
                    )

    def test_body_without_the_leading_comment_is_delivered_unchanged(self) -> None:
        for delivery in DELIVERIES:
            with self.subTest(delivery=delivery.label):
                if self.skip_without_utf8_locale(delivery):
                    self.skipTest("UTF-8 locale が無いと 8K ガードが働くため組み立てを比較できない")
                context, expected = self.deliver(delivery)
                self.assertEqual(
                    expected,
                    context,
                    f"{delivery.label}: additionalContext が先頭コメントを除いた本文の"
                    "組み立てと一致しない",
                )

    def test_rule_markers_of_the_prompt_are_all_delivered(self) -> None:
        for delivery in DELIVERIES:
            with self.subTest(delivery=delivery.label):
                if self.skip_without_utf8_locale(delivery):
                    self.skipTest("UTF-8 locale が無いと 8K ガードが働くため組み立てを比較できない")
                context, _ = self.deliver(delivery)
                expected = [
                    marker for _, text in self.sources(delivery) for marker in rule_markers(text)
                ]
                self.assertEqual(
                    expected,
                    rule_markers(context),
                    f"{delivery.label}: prompt ファイルの rule マーカーが additionalContext に"
                    "同じ順序で残っていない",
                )


@unittest.skipUnless(shutil.which("jq"), "hook の実行には jq が必要")
class BoundaryCaseDeliveryTest(DeliveryTestCase):
    """複製した plugin の prompt ファイルを境界ケースの fixture に差し替えた各スクリプトの出力。"""

    def check_cases(self, cases: tuple[BoundaryCase, ...]) -> None:
        for case in cases:
            for delivery in DELIVERIES:
                with self.subTest(case=case.label, delivery=delivery.label):
                    self.check_case(case, delivery)

    def check_case(self, case: BoundaryCase, delivery: Delivery) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            hooks_dir = copy_hooks(temp)
            prompts_dir = hooks_dir / "prompts"
            if delivery.script == "inject-temporary.sh":
                for existing in (prompts_dir / "temporary").glob("*.md"):
                    existing.unlink()
            for name in delivery.targets:
                (prompts_dir / name).write_text(case.text, encoding="utf-8")
            run = run_delivery(delivery, hooks_dir, temp)
            context = self.context_of(run, delivery)
            body = case.expected_body
            if run.repository is not None:
                body = fill_placeholders(body, run.repository)
            expected = delivery.assemble([body] * len(delivery.targets), prompts_dir)
        self.assertEqual(
            expected,
            context,
            f"{delivery.label}: {case.label}: additionalContext が期待する本文の組み立てと"
            "一致しない",
        )
        if HEADER_SENTINEL in case.text:
            self.assertNotIn(
                HEADER_SENTINEL,
                context,
                f"{delivery.label}: {case.label}: 先頭コメントが additionalContext に含まれる",
            )

    def test_file_without_leading_comment_is_delivered_as_is(self) -> None:
        self.check_cases(NO_HEADER_CASES)

    def test_file_with_unclosed_leading_comment_is_delivered_as_is(self) -> None:
        self.check_cases(UNCLOSED_CASES)

    def test_html_comments_in_the_body_are_kept(self) -> None:
        self.check_cases(BODY_COMMENT_CASES)

    def test_rule_marker_right_after_the_leading_comment_is_kept(self) -> None:
        self.check_cases(RULE_MARKER_AFTER_HEADER_CASES)

    def test_file_starting_with_a_rule_marker_is_delivered_as_is(self) -> None:
        self.check_cases(RULE_MARKER_FIRST_CASES)

    def test_blank_lines_after_the_leading_comment_are_removed(self) -> None:
        self.check_cases(BLANK_LINE_CASES)


if __name__ == "__main__":
    unittest.main()

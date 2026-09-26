"""agent-discipline: 全モデル共通の規律配送 (常時適用ルール 3 part + 分業規律 1 版) の契約テスト。

agent-discipline はメインセッションのモデルを判定せず、全モデルへ同じ規律を配送する。

- ファイル構成 (``FileLayoutTest`` / ``HooksJsonTest`` / ``RemovedNameReferenceTest``):
  常時適用ルールは always-{1,2,3}.md、分業規律は discipline.md の 1 版である。モデル別の版・
  自己ゲート前置き・モデル判定スクリプトは存在せず、hooks.json は ``PostModelSwitch`` を登録
  しない。``UserPromptSubmit`` は ``USER_PROMPT_SUBMIT_COMMANDS`` の順に command hook を
  実行する。hooks.json の ``description`` は現行のファイル名だけを書き、モデル判定の仕組みに
  言及しない。リポジトリのファイル (git の追跡対象と、.gitignore に該当しない未追跡ファイル。
  本ファイルを除く) に、存在しないファイル・hook event の名前 (``REMOVED_NAME_FRAGMENTS``)
  への参照が無い。
- inject-always.sh (``InjectAlwaysTest``、SessionStart): stdin の ``.model`` と
  ``transcript_path`` を読まず、出力はモデルに依らず同一である。state file
  (``model-<sid>``) と pending マーカー (``pending-model-<sid>``) を作成も削除もしない。
  session_id が非空なら UserPromptSubmit 側の配送済みマーカー 3 種を削除する。
  additionalContext は ``SELF_HEAL`` + 空行 + delivery-note.md + 改行 + ``(参照パス) <prompts
  dir>`` + 空行 + always-1.md である。jq 不在・hook_event_name が空・always-1.md が読めない
  (空を含む) 場合は出力なしで exit 0 になる。
- inject-rules-part.sh <N> (``InjectRulesPartTest``、UserPromptSubmit): N が 2 / 3 のとき
  だけ動く。マーカー ``delivered-rules-<N>-<sid>`` が無ければ always-<N>.md の本文そのものを
  配送してマーカーを書き、マーカーがあれば出力しない。state file / pending マーカーは出力に
  影響しない。入力が不正・always-<N>.md が読めない場合は出力もマーカーも無い。
- inject-discipline.sh (``InjectDisciplineTest``、UserPromptSubmit): マーカー
  ``delivered-discipline-<sid>`` が無ければ ``# agent-discipline: 分業規律`` + 空行 +
  discipline.md を配送してマーカー (内容 ``delivered``) を書き、マーカーがあれば内容に依らず
  出力しない。state file / pending マーカーは出力に影響しない。入力が不正・discipline.md が
  読めない場合は出力もマーカーも無い。
- 3 スクリプトの出力 JSON は ``{"hookSpecificOutput": {"hookEventName": <入力の
  hook_event_name>, "additionalContext": ...}}`` だけを持つ。
- プロンプト本文 (``PromptBodyContractTest``): 配送するプロンプトは ``sonnet`` / ``haiku`` を
  含まない。always-<N>.md の見出し・always-3.md の思考量の文・rule ID 集合・discipline.md の
  必須 / 禁止文言と、見出し込みで 8,000 字以下のサイズを固定する。
- lint (``LintContractTest``): lint-prompt-sync.sh が現行ファイルを対象にし、分業規律の期待
  rule ID 集合を定数 ``EXPECTED_DISCIPLINE_RULE_IDS`` に持つ。lint-payload-size.sh の
  CASE_TABLE は state / pending を用意するケースと存在しないスクリプトのケースを持たず、
  配送スクリプトごとに初回配送とマーカー存在時の無出力を実測する。CI workflow の paths が
  現行ファイルを 1 回ずつ持つ。lint-prompt-sync.sh の実行結果は
  tests/test_agent_discipline_step0_guard.py が検査する。
- version (``VersionConsistencyTest``): plugin.json / marketplace.json / リポジトリ直下
  README の plugin 一覧 / plugin README の ``## バージョン`` 節が ``PLUGIN_VERSION`` で一致する。

hook を実行するテストは tempfile で作った一時ディレクトリを ``TMPDIR`` / ``HOME`` /
``XDG_CACHE_HOME`` として env で渡し、親プロセスの env を継承しない。実環境の
``${TMPDIR:-/tmp}/agent-discipline-state`` には触れない。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "agent-discipline"
PROMPTS_DIR = PLUGIN_DIR / "hooks" / "prompts"
SCRIPTS_DIR = PLUGIN_DIR / "hooks" / "scripts"
HOOKS_JSON = PLUGIN_DIR / "hooks" / "hooks.json"
LINT_PROMPT_SYNC_SH = PLUGIN_DIR / "scripts" / "lint-prompt-sync.sh"
LINT_PAYLOAD_SIZE_SH = PLUGIN_DIR / "scripts" / "lint-payload-size.sh"
WORKFLOW_YML = ROOT / ".github" / "workflows" / "agent-discipline-prompt-lint.yml"
PLUGIN_JSON = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
REPO_README = ROOT / "README.md"
PLUGIN_README = PLUGIN_DIR / "README.md"

PLUGIN_NAME = "agent-discipline"
PLUGIN_VERSION = "3.0.4"

INJECT_ALWAYS = "inject-always.sh"
INJECT_RULES_PART = "inject-rules-part.sh"
INJECT_DISCIPLINE = "inject-discipline.sh"

# 配送するプロンプトファイル。
ALWAYS_PARTS = ("always-1.md", "always-2.md", "always-3.md")
DISCIPLINE_MD = "discipline.md"
PRESENT_PROMPTS = (
    *ALWAYS_PARTS,
    DISCIPLINE_MD,
    "subagent-rules.md",
    "delivery-note.md",
    "auto-mode.md",
    "uncommitted-check.md",
)
PRESENT_SCRIPTS = (
    "block-fable-subagent.sh",
    "check-uncommitted-on-session-start.sh",
    INJECT_ALWAYS,
    "inject-auto.sh",
    INJECT_DISCIPLINE,
    INJECT_RULES_PART,
    "inject-subagent-rules.sh",
    "inject-temporary.sh",
    "lib/permission-mode.sh",
)

# 存在してはならないファイル (モデル別の版・自己ゲート前置き・モデル判定スクリプト)。
ABSENT_PROMPTS = (
    "always-sonnet-1.md",
    "always-sonnet-2.md",
    "always-sonnet-3.md",
    "discipline-opus.md",
    "discipline-sonnet.md",
    "discipline-preamble-self-gate.md",
    "preamble-self-gate.md",
    "part-self-gate.md",
)
ABSENT_SCRIPTS = ("resolve-model-on-prompt.sh", "update-model-on-switch.sh")

# リポジトリ内のどのファイルも参照してはならない名前 (部分文字列で照合する)。
REMOVED_NAME_FRAGMENTS = (
    "always-sonnet-",
    "discipline-sonnet",
    "discipline-opus",
    "discipline-preamble-self-gate",
    "preamble-self-gate",
    "part-self-gate",
    "resolve-model-on-prompt",
    "update-model-on-switch",
    "PostModelSwitch",
)

PLUGIN_ROOT_PLACEHOLDER = "${CLAUDE_PLUGIN_ROOT}"


def script_command(basename: str) -> str:
    return f"{PLUGIN_ROOT_PLACEHOLDER}/hooks/scripts/{basename}"


# UserPromptSubmit の command hook (command, args) の実行順。
USER_PROMPT_SUBMIT_COMMANDS = (
    (script_command("inject-temporary.sh"), []),
    (script_command(INJECT_RULES_PART), ["2"]),
    (script_command(INJECT_RULES_PART), ["3"]),
    (script_command(INJECT_DISCIPLINE), []),
    (script_command("inject-auto.sh"), []),
    (script_command("check-uncommitted-on-session-start.sh"), []),
)

# hooks.json の description が名指しする現行のファイル名と、書いてはならない語。
DESCRIPTION_REQUIRED_NAMES = (*ALWAYS_PARTS, DISCIPLINE_MD)
DESCRIPTION_FORBIDDEN_PHRASES = (
    "判定不能",
    "one-shot 補正",
    "(Opus 系)",
    "それ以外のモデル",
    "session model state",
)

# inject-always.sh が additionalContext の先頭に置く自己修復指示。
SELF_HEAL = (
    "(自己修復) このメッセージが persisted-output として退避されている場合は、"
    "スタブに記載されたパスの退避ファイルを Read で全文読了してから作業を開始すること。"
)
PATH_LINE_PREFIX = "(参照パス) "
DISCIPLINE_HEADING = "# agent-discipline: 分業規律"
DISCIPLINE_MARKER_CONTENT = "delivered"
SIZE_LIMIT_CHARS = 8000

STATE_DIR_NAME = "agent-discipline-state"
SESSION_ID = "unified-discipline-session"

OPUS_MODEL = "claude-opus-5-5"
SONNET_MODEL = "claude-sonnet-5"
FABLE_MODEL = "claude-fable-5-1"

# stdin の .model の値の組 (UNSET は .model キー自体を書かない)。
UNSET = object()
STDIN_MODEL_VARIANTS = (
    ("opus", OPUS_MODEL),
    ("sonnet", SONNET_MODEL),
    ("fable", FABLE_MODEL),
    ("empty", ""),
    ("absent", UNSET),
)

# state file / pending マーカーの組 (出力に影響しないことを検査する)。
STATE_VARIANTS = (
    ("state-opus", OPUS_MODEL, False),
    ("state-sonnet", SONNET_MODEL, False),
    ("state-fable", FABLE_MODEL, False),
    ("pending", None, True),
    ("pending-with-state-opus", OPUS_MODEL, True),
)

# 配送プロンプトに含めない語 (大文字小文字を無視して照合する)。
MODEL_NAMES_ABSENT_FROM_PROMPTS = ("sonnet", "haiku")
PROMPTS_WITHOUT_MODEL_NAMES = (
    *ALWAYS_PARTS,
    DISCIPLINE_MD,
    "subagent-rules.md",
    "delivery-note.md",
    "auto-mode.md",
)

ALWAYS_THINKING_SENTENCE = "本文書の規律は、単純な作業での思考量を増やす理由にはならない。"
ALWAYS_THINKING_FORBIDDEN = "考え抜く"

EXPECTED_DISCIPLINE_RULE_IDS = {
    "role-split",
    "delegation-rules",
    "delegation-instruction",
    "escalation",
}
DISCIPLINE_REQUIRED_PHRASES = (
    "cross-model-advisor:fable-advisor-runner",
    "**ワーカーは Opus 5.5 で動かす**",
    "メインセッションが Opus 系以外のモデルで動いている場合",
    '`model: "opus"` を明示する',
    "**起動仕様で model が定められた runner には effort を指定しない**",
    "**起動したサブエージェントの完了前にタスクを完了扱いにしない**",
    "完了通知を受け取り",
    "変更に着手する前に関連するファイル・文書・既存実装を",
    "**委任指示にはスコープ制限の 1 文を必ず含める**",
    "**委任では汎用的な再確認指示を加えない**",
    "**Opus 5.5 への委任では effort の既定 `medium` を基準にする**",
)
DISCIPLINE_FORBIDDEN_PHRASES = (
    "Opus 版",
    "Sonnet 版",
    "discipline-sonnet",
    "Opus 5 / Opus 5.5",
    # 提供されていない merge 前の Fable review
    "fable-reviewer",
    "pre-merge review",
)

RULE_ID_PATTERN = re.compile(r"<!--\s*rule:([a-zA-Z0-9_-]+)\s*-->")

# CI workflow の paths に 1 回ずつ置く現行ファイル。
WORKFLOW_REQUIRED_PATHS = tuple(
    f"plugins/agent-discipline/hooks/prompts/{name}" for name in (*ALWAYS_PARTS, DISCIPLINE_MD)
)

# jq 不在を再現する PATH に置く外部コマンド (jq 以外)。
NON_JQ_COMMANDS = (
    "cat",
    "dirname",
    "tr",
    "rm",
    "mkdir",
    "mv",
    "wc",
    "head",
    "tail",
    "grep",
    "sed",
    "printf",
    "ls",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_prompt(name: str) -> str:
    """prompt ファイルを hook と同じ形で読む (``$(cat ...)`` は末尾の改行を落とす)。"""
    return read(PROMPTS_DIR / name).rstrip("\n")


def rule_ids(text: str) -> list[str]:
    return RULE_ID_PATTERN.findall(text)


def detect_utf8_locale() -> str | None:
    """`locale -a` から UTF-8 系 locale を 1 つ選ぶ (見つからなければ None)。

    inject-always.sh は ``wc -m`` で文字数を数えるため、UTF-8 locale でないと日本語を
    バイト数で数えて 8K ガードが働く。組み立て順を検査するテストだけがこの値を使う。
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


def isolated_env(temp: Path, *, locale: str | None = None) -> dict[str, str]:
    """親プロセスの env を継承せず、PATH と隔離ディレクトリだけを渡す env を作る。"""
    home = temp / "home"
    tmpdir = temp / "tmp"
    cache_home = temp / "cache"
    for directory in (home, tmpdir, cache_home):
        directory.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
        "XDG_CACHE_HOME": str(cache_home),
    }
    if locale is not None:
        env["LC_ALL"] = locale
        env["LANG"] = locale
    return env


def path_without_jq(temp: Path) -> str:
    """jq 以外の外部コマンドだけを symlink したディレクトリを PATH として返す。"""
    shims = temp / "no-jq-bin"
    shims.mkdir(exist_ok=True)
    for name in NON_JQ_COMMANDS:
        found = shutil.which(name)
        if found is not None and not (shims / name).exists():
            (shims / name).symlink_to(found)
    return str(shims)


def state_dir_of(env: dict[str, str]) -> Path:
    return Path(env["TMPDIR"]) / STATE_DIR_NAME


def prepare_state(
    env: dict[str, str],
    *,
    model: str | None = None,
    pending: bool = False,
    files: dict[str, str] | None = None,
) -> Path:
    """隔離した TMPDIR 配下に state file / pending マーカー / 任意のマーカーを置く。"""
    state_dir = state_dir_of(env)
    state_dir.mkdir(parents=True, exist_ok=True)
    if model is not None:
        (state_dir / f"model-{SESSION_ID}").write_text(model, encoding="utf-8")
    if pending:
        (state_dir / f"pending-model-{SESSION_ID}").write_text("", encoding="utf-8")
    for name, content in (files or {}).items():
        (state_dir / name).write_text(content, encoding="utf-8")
    return state_dir


def snapshot(directory: Path) -> dict[str, str]:
    """ディレクトリ直下のファイル名と内容の対応 (ディレクトリが無ければ空)。"""
    if not directory.is_dir():
        return {}
    return {
        entry.name: entry.read_text(encoding="utf-8")
        for entry in sorted(directory.iterdir())
        if entry.is_file()
    }


def copy_plugin(temp: Path) -> Path:
    """plugin の hooks/ を一時ディレクトリへ複製し、複製した plugin root を返す。"""
    plugin_root = temp / "plugin" / PLUGIN_NAME
    shutil.copytree(PLUGIN_DIR / "hooks", plugin_root / "hooks")
    return plugin_root


def run_hook(
    script: Path, env: dict[str, str], stdin: str, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        cwd=ROOT,
        env=env,
        input=stdin,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
        check=False,
    )


def payload(event: str, session_id: str = SESSION_ID, **extra: object) -> str:
    body: dict[str, object] = {"hook_event_name": event, "session_id": session_id}
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


class HookTestCase(unittest.TestCase):
    """hook の実行結果を読むための共通 assertion。"""

    def context_of(self, result: subprocess.CompletedProcess[str], event: str) -> str:
        """出力 JSON の形を検査し、additionalContext を返す。"""
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(result.stdout.strip(), f"出力が無い (stderr: {result.stderr})")
        output = json.loads(result.stdout)
        self.assertEqual({"hookSpecificOutput"}, set(output))
        hook_output = output["hookSpecificOutput"]
        self.assertEqual({"hookEventName", "additionalContext"}, set(hook_output))
        self.assertEqual(event, hook_output["hookEventName"])
        self.assertIsInstance(hook_output["additionalContext"], str)
        return hook_output["additionalContext"]

    def assert_silent(self, result: subprocess.CompletedProcess[str], label: str) -> None:
        self.assertEqual(0, result.returncode, f"{label}: {result.stderr}")
        self.assertEqual("", result.stdout, f"{label}: 出力がある")


class FileLayoutTest(unittest.TestCase):
    """配送するプロンプトとスクリプトの構成。"""

    def test_current_prompts_exist(self) -> None:
        for name in PRESENT_PROMPTS:
            with self.subTest(prompt=name):
                self.assertTrue((PROMPTS_DIR / name).is_file(), name)

    def test_removed_prompts_do_not_exist(self) -> None:
        for name in ABSENT_PROMPTS:
            with self.subTest(prompt=name):
                self.assertFalse((PROMPTS_DIR / name).exists(), name)

    def test_current_scripts_exist(self) -> None:
        for name in PRESENT_SCRIPTS:
            with self.subTest(script=name):
                self.assertTrue((SCRIPTS_DIR / name).is_file(), name)

    def test_removed_scripts_do_not_exist(self) -> None:
        for name in ABSENT_SCRIPTS:
            with self.subTest(script=name):
                self.assertFalse((SCRIPTS_DIR / name).exists(), name)


class HooksJsonTest(unittest.TestCase):
    """hooks.json の event 登録と description。"""

    def setUp(self) -> None:
        self.manifest = json.loads(read(HOOKS_JSON))

    def test_post_model_switch_is_not_registered(self) -> None:
        self.assertNotIn("PostModelSwitch", self.manifest["hooks"])

    def test_user_prompt_submit_runs_the_commands_in_order(self) -> None:
        actual = [
            (hook.get("command"), hook.get("args"))
            for group in self.manifest["hooks"]["UserPromptSubmit"]
            for hook in group["hooks"]
        ]
        self.assertEqual(list(USER_PROMPT_SUBMIT_COMMANDS), actual)

    def test_description_names_the_current_files(self) -> None:
        description = self.manifest["description"]
        missing = [name for name in DESCRIPTION_REQUIRED_NAMES if name not in description]
        self.assertEqual([], missing, f"description に無いファイル名: {missing}")

    def test_description_does_not_describe_model_resolution(self) -> None:
        description = self.manifest["description"]
        present = [
            phrase
            for phrase in (*DESCRIPTION_FORBIDDEN_PHRASES, *REMOVED_NAME_FRAGMENTS)
            if phrase in description
        ]
        self.assertEqual([], present, f"description に残る語: {present}")


class RemovedNameReferenceTest(unittest.TestCase):
    """リポジトリのファイルに、存在しないファイル・hook event の名前への参照が無い。

    本ファイルは存在しないことの期待値としてそれらの名前を持つため走査から除く。
    """

    def test_repository_has_no_reference_to_removed_names(self) -> None:
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.decode("utf-8").split("\0")
        this_file = Path(__file__).resolve()
        offenders = []
        for relative in sorted(set(filter(None, listed))):
            path = ROOT / relative
            if path.resolve() == this_file or path.is_symlink() or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for fragment in REMOVED_NAME_FRAGMENTS:
                if fragment in text:
                    offenders.append(f"{relative}: {fragment}")
        self.assertEqual([], offenders, "\n".join(offenders))


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class InjectAlwaysTest(HookTestCase):
    """inject-always.sh (SessionStart) はモデル判定をせず part 1/3 を配送する。"""

    SCRIPT = SCRIPTS_DIR / INJECT_ALWAYS

    def run_always(
        self,
        stdin: str,
        *,
        model: str | None = None,
        pending: bool = False,
        files: dict[str, str] | None = None,
        locale: str | None = UTF8_LOCALE,
        script: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        """hook を 1 回実行し、(結果, 実行後の state dir の内容) を返す。"""
        with tempfile.TemporaryDirectory() as temporary:
            env = isolated_env(Path(temporary), locale=locale)
            if model is not None or pending or files:
                prepare_state(env, model=model, pending=pending, files=files)
            result = run_hook(script or self.SCRIPT, env, stdin)
            return result, snapshot(state_dir_of(env))

    def session_start(self, **extra: object) -> str:
        return payload("SessionStart", **extra)

    def baseline_context(self) -> str:
        result, _ = self.run_always(self.session_start())
        context = self.context_of(result, "SessionStart")
        self.assertIn(read_prompt("always-1.md"), context)
        return context

    def test_output_does_not_depend_on_stdin_model(self) -> None:
        baseline = self.baseline_context()
        for label, model in STDIN_MODEL_VARIANTS:
            with self.subTest(model=label):
                extra = {} if model is UNSET else {"model": model}
                result, _ = self.run_always(self.session_start(**extra))
                self.assertEqual(baseline, self.context_of(result, "SessionStart"))

    def test_output_does_not_depend_on_transcript(self) -> None:
        """transcript_path を読まない (最後の assistant 行のモデルに依らない)。"""
        baseline = self.baseline_context()
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "transcript.jsonl"
            lines = [
                {"type": "user", "message": {"content": "hello"}},
                {"type": "assistant", "isSidechain": False, "message": {"model": SONNET_MODEL}},
            ]
            transcript.write_text(
                "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
            )
            result, _ = self.run_always(self.session_start(transcript_path=str(transcript)))
        self.assertEqual(baseline, self.context_of(result, "SessionStart"))

    def test_output_does_not_depend_on_state_or_pending_files(self) -> None:
        baseline = self.baseline_context()
        for label, model, pending in STATE_VARIANTS:
            with self.subTest(state=label):
                result, _ = self.run_always(self.session_start(), model=model, pending=pending)
                self.assertEqual(baseline, self.context_of(result, "SessionStart"))

    def test_state_and_pending_files_are_not_created(self) -> None:
        for label, model in STDIN_MODEL_VARIANTS:
            with self.subTest(model=label):
                extra = {} if model is UNSET else {"model": model}
                result, state = self.run_always(self.session_start(**extra))
                self.context_of(result, "SessionStart")
                self.assertNotIn(f"model-{SESSION_ID}", state)
                self.assertNotIn(f"pending-model-{SESSION_ID}", state)

    def test_existing_state_and_pending_files_are_left_untouched(self) -> None:
        result, state = self.run_always(
            self.session_start(model=OPUS_MODEL), model=SONNET_MODEL, pending=True
        )
        self.context_of(result, "SessionStart")
        self.assertEqual(SONNET_MODEL, state.get(f"model-{SESSION_ID}"))
        self.assertEqual("", state.get(f"pending-model-{SESSION_ID}"))

    def test_delivered_markers_are_removed(self) -> None:
        markers = {
            f"delivered-rules-2-{SESSION_ID}": "",
            f"delivered-rules-3-{SESSION_ID}": "",
            f"delivered-discipline-{SESSION_ID}": DISCIPLINE_MARKER_CONTENT,
        }
        result, state = self.run_always(self.session_start(), files=markers)
        self.context_of(result, "SessionStart")
        self.assertEqual([], sorted(set(markers) & set(state)))

    def test_empty_session_id_still_delivers_part_one(self) -> None:
        baseline = self.baseline_context()
        result, _ = self.run_always(payload("SessionStart", session_id=""))
        self.assertEqual(baseline, self.context_of(result, "SessionStart"))

    @unittest.skipIf(UTF8_LOCALE is None, "UTF-8 locale が無いと 8K ガードが働くため組み立てを比較できない")
    def test_additional_context_is_self_heal_note_path_and_part_one(self) -> None:
        expected = (
            f"{SELF_HEAL}\n\n{read_prompt('delivery-note.md')}\n"
            f"{PATH_LINE_PREFIX}{PROMPTS_DIR}\n\n{read_prompt('always-1.md')}"
        )
        result, _ = self.run_always(self.session_start(model=OPUS_MODEL))
        self.assertEqual(expected, self.context_of(result, "SessionStart"))

    def test_hook_event_name_is_echoed(self) -> None:
        result, _ = self.run_always(payload("UserPromptSubmit"))
        self.context_of(result, "UserPromptSubmit")

    def test_fail_open_without_output(self) -> None:
        with self.subTest(case="jq 不在"):
            with tempfile.TemporaryDirectory() as temporary:
                env = isolated_env(Path(temporary))
                env["PATH"] = path_without_jq(Path(temporary))
                result = run_hook(self.SCRIPT, env, self.session_start())
            self.assert_silent(result, "jq 不在")
        with self.subTest(case="hook_event_name が空"):
            result, _ = self.run_always(payload(""))
            self.assert_silent(result, "hook_event_name が空")
        for label, prepare in (
            ("always-1.md が空", lambda path: path.write_text("", encoding="utf-8")),
            ("always-1.md が無い", lambda path: path.unlink()),
        ):
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory() as temporary:
                    plugin_root = copy_plugin(Path(temporary))
                    prepare(plugin_root / "hooks" / "prompts" / "always-1.md")
                    result, _ = self.run_always(
                        self.session_start(),
                        script=plugin_root / "hooks" / "scripts" / INJECT_ALWAYS,
                    )
                self.assert_silent(result, label)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class InjectRulesPartTest(HookTestCase):
    """inject-rules-part.sh <N> (UserPromptSubmit) は always-<N>.md を at-most-once で配送する。"""

    SCRIPT = SCRIPTS_DIR / INJECT_RULES_PART
    PARTS = ("2", "3")

    def run_part(
        self,
        stdin: str,
        *args: str,
        model: str | None = None,
        pending: bool = False,
        files: dict[str, str] | None = None,
        without_jq: bool = False,
        script: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        with tempfile.TemporaryDirectory() as temporary:
            env = isolated_env(Path(temporary))
            if without_jq:
                env["PATH"] = path_without_jq(Path(temporary))
            if model is not None or pending or files:
                prepare_state(env, model=model, pending=pending, files=files)
            result = run_hook(script or self.SCRIPT, env, stdin, *args)
            return result, snapshot(state_dir_of(env))

    @staticmethod
    def marker(part: str) -> str:
        return f"delivered-rules-{part}-{SESSION_ID}"

    def test_first_delivery_is_the_part_body_and_writes_the_marker(self) -> None:
        for part in self.PARTS:
            with self.subTest(part=part):
                result, state = self.run_part(payload("UserPromptSubmit"), part)
                context = self.context_of(result, "UserPromptSubmit")
                self.assertEqual(read_prompt(f"always-{part}.md"), context)
                self.assertEqual([self.marker(part)], sorted(state))

    def test_output_does_not_depend_on_state_or_pending_files(self) -> None:
        for part in self.PARTS:
            expected = read_prompt(f"always-{part}.md")
            for label, model, pending in STATE_VARIANTS:
                with self.subTest(part=part, state=label):
                    result, state = self.run_part(
                        payload("UserPromptSubmit"), part, model=model, pending=pending
                    )
                    self.assertEqual(expected, self.context_of(result, "UserPromptSubmit"))
                    self.assertIn(self.marker(part), state)

    def test_existing_marker_suppresses_output(self) -> None:
        for part in self.PARTS:
            with self.subTest(part=part):
                result, _ = self.run_part(
                    payload("UserPromptSubmit"), part, files={self.marker(part): ""}
                )
                self.assert_silent(result, f"part {part}")

    def test_invalid_part_argument_produces_no_output(self) -> None:
        for label, args in (
            ("欠落", ()),
            ("1", ("1",)),
            ("4", ("4",)),
            ("空", ("",)),
            ("非数値", ("two",)),
        ):
            with self.subTest(argument=label):
                result, state = self.run_part(payload("UserPromptSubmit"), *args)
                self.assert_silent(result, label)
                self.assertEqual({}, state)

    def test_invalid_input_produces_no_output_and_no_marker(self) -> None:
        cases = (
            ("jq 不在", payload("UserPromptSubmit"), True),
            ("不正 JSON", '{"hook_event_name": "UserPromptSubmit",', False),
            ("hook_event_name が空", payload(""), False),
            ("session_id が空", payload("UserPromptSubmit", session_id=""), False),
        )
        for part in self.PARTS:
            for label, stdin, without_jq in cases:
                with self.subTest(part=part, case=label):
                    result, state = self.run_part(stdin, part, without_jq=without_jq)
                    self.assert_silent(result, label)
                    self.assertEqual({}, state)

    def test_unreadable_part_produces_no_output_and_no_marker(self) -> None:
        for part in self.PARTS:
            for label, prepare in (
                ("空", lambda path: path.write_text("", encoding="utf-8")),
                ("無い", lambda path: path.unlink()),
            ):
                with self.subTest(part=part, case=label):
                    with tempfile.TemporaryDirectory() as temporary:
                        plugin_root = copy_plugin(Path(temporary))
                        prepare(plugin_root / "hooks" / "prompts" / f"always-{part}.md")
                        result, state = self.run_part(
                            payload("UserPromptSubmit"),
                            part,
                            script=plugin_root / "hooks" / "scripts" / INJECT_RULES_PART,
                        )
                    self.assert_silent(result, label)
                    self.assertNotIn(self.marker(part), state)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class InjectDisciplineTest(HookTestCase):
    """inject-discipline.sh (UserPromptSubmit) は分業規律 1 版を at-most-once で配送する。"""

    SCRIPT = SCRIPTS_DIR / INJECT_DISCIPLINE
    MARKER = f"delivered-discipline-{SESSION_ID}"

    def run_discipline(
        self,
        stdin: str,
        *,
        model: str | None = None,
        pending: bool = False,
        files: dict[str, str] | None = None,
        without_jq: bool = False,
        script: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        with tempfile.TemporaryDirectory() as temporary:
            env = isolated_env(Path(temporary))
            if without_jq:
                env["PATH"] = path_without_jq(Path(temporary))
            if model is not None or pending or files:
                prepare_state(env, model=model, pending=pending, files=files)
            result = run_hook(script or self.SCRIPT, env, stdin)
            return result, snapshot(state_dir_of(env))

    @staticmethod
    def expected_context() -> str:
        return f"{DISCIPLINE_HEADING}\n\n{read_prompt(DISCIPLINE_MD)}"

    def test_first_delivery_is_heading_and_discipline_and_writes_the_marker(self) -> None:
        result, state = self.run_discipline(payload("UserPromptSubmit"))
        self.assertEqual(self.expected_context(), self.context_of(result, "UserPromptSubmit"))
        self.assertEqual({self.MARKER: DISCIPLINE_MARKER_CONTENT}, state)

    def test_output_does_not_depend_on_state_or_pending_files(self) -> None:
        for label, model, pending in STATE_VARIANTS:
            with self.subTest(state=label):
                result, state = self.run_discipline(
                    payload("UserPromptSubmit"), model=model, pending=pending
                )
                self.assertEqual(
                    self.expected_context(), self.context_of(result, "UserPromptSubmit")
                )
                self.assertEqual(DISCIPLINE_MARKER_CONTENT, state.get(self.MARKER))

    def test_existing_marker_suppresses_output_regardless_of_content(self) -> None:
        for content in (DISCIPLINE_MARKER_CONTENT, "final", "sonnet-gate", ""):
            for label, model, pending in (("no-state", None, False), *STATE_VARIANTS):
                with self.subTest(marker=content, state=label):
                    result, state = self.run_discipline(
                        payload("UserPromptSubmit"),
                        model=model,
                        pending=pending,
                        files={self.MARKER: content},
                    )
                    self.assert_silent(result, f"marker={content!r} {label}")
                    self.assertEqual(content, state.get(self.MARKER))

    def test_invalid_input_produces_no_output_and_no_marker(self) -> None:
        cases = (
            ("jq 不在", payload("UserPromptSubmit"), True),
            ("不正 JSON", '{"hook_event_name": "UserPromptSubmit",', False),
            ("hook_event_name が空", payload(""), False),
            ("session_id が空", payload("UserPromptSubmit", session_id=""), False),
        )
        for label, stdin, without_jq in cases:
            with self.subTest(case=label):
                result, state = self.run_discipline(stdin, without_jq=without_jq)
                self.assert_silent(result, label)
                self.assertEqual({}, state)

    def test_unreadable_discipline_produces_no_output_and_no_marker(self) -> None:
        for label, prepare in (
            ("空", lambda path: path.write_text("", encoding="utf-8")),
            ("無い", lambda path: path.unlink()),
        ):
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory() as temporary:
                    plugin_root = copy_plugin(Path(temporary))
                    prepare(plugin_root / "hooks" / "prompts" / DISCIPLINE_MD)
                    result, state = self.run_discipline(
                        payload("UserPromptSubmit"),
                        script=plugin_root / "hooks" / "scripts" / INJECT_DISCIPLINE,
                    )
                self.assert_silent(result, label)
                self.assertNotIn(self.MARKER, state)


class PromptBodyContractTest(unittest.TestCase):
    """配送するプロンプト本文の契約。"""

    def test_prompts_do_not_name_sonnet_or_haiku(self) -> None:
        present = [
            f"{name}: {word}"
            for name in PROMPTS_WITHOUT_MODEL_NAMES
            for word in MODEL_NAMES_ABSENT_FROM_PROMPTS
            if word in read(PROMPTS_DIR / name).lower()
        ]
        self.assertEqual([], present)

    def test_always_part_headings(self) -> None:
        for index, name in enumerate(ALWAYS_PARTS, start=1):
            with self.subTest(prompt=name):
                self.assertIn(
                    f"# agent-discipline: 常時適用ルール — part {index}/3",
                    read(PROMPTS_DIR / name).splitlines(),
                )

    def test_always_part_three_does_not_add_thinking(self) -> None:
        text = read(PROMPTS_DIR / "always-3.md")
        self.assertNotIn(ALWAYS_THINKING_FORBIDDEN, text)
        self.assertIn(ALWAYS_THINKING_SENTENCE, text)

    def test_always_rule_id_union_matches_the_lint_expectation(self) -> None:
        match = re.search(
            r'^EXPECTED_ALWAYS_RULE_IDS="([^"]*)"', read(LINT_PROMPT_SYNC_SH), re.MULTILINE
        )
        self.assertIsNotNone(match, "lint-prompt-sync.sh に EXPECTED_ALWAYS_RULE_IDS が無い")
        assert match is not None
        expected = sorted(match.group(1).split())
        actual = sorted(
            rule_id for name in ALWAYS_PARTS for rule_id in rule_ids(read(PROMPTS_DIR / name))
        )
        self.assertEqual(expected, actual)

    def test_discipline_rule_ids(self) -> None:
        ids = rule_ids(read(PROMPTS_DIR / DISCIPLINE_MD))
        self.assertEqual(sorted(EXPECTED_DISCIPLINE_RULE_IDS), sorted(ids))

    def test_discipline_contains_the_required_phrases(self) -> None:
        text = read(PROMPTS_DIR / DISCIPLINE_MD)
        missing = [phrase for phrase in DISCIPLINE_REQUIRED_PHRASES if phrase not in text]
        self.assertEqual([], missing)

    def test_discipline_does_not_contain_the_forbidden_phrases(self) -> None:
        text = read(PROMPTS_DIR / DISCIPLINE_MD)
        present = [phrase for phrase in DISCIPLINE_FORBIDDEN_PHRASES if phrase in text]
        self.assertEqual([], present)

    def test_discipline_delivery_fits_the_size_limit(self) -> None:
        delivered = f"{DISCIPLINE_HEADING}\n\n{read_prompt(DISCIPLINE_MD)}"
        self.assertLessEqual(len(delivered), SIZE_LIMIT_CHARS)


class LintContractTest(unittest.TestCase):
    """lint スクリプトと CI workflow の対象。"""

    def test_lint_prompt_sync_targets_the_current_files(self) -> None:
        text = read(LINT_PROMPT_SYNC_SH)
        missing = [path for path in WORKFLOW_REQUIRED_PATHS if path not in text]
        self.assertEqual([], missing)

    def test_lint_prompt_sync_declares_the_discipline_rule_ids(self) -> None:
        match = re.search(
            r'^EXPECTED_DISCIPLINE_RULE_IDS="([^"]*)"', read(LINT_PROMPT_SYNC_SH), re.MULTILINE
        )
        self.assertIsNotNone(match, "lint-prompt-sync.sh に EXPECTED_DISCIPLINE_RULE_IDS が無い")
        assert match is not None
        self.assertEqual(sorted(EXPECTED_DISCIPLINE_RULE_IDS), sorted(match.group(1).split()))

    def case_table(self) -> list[list[str]]:
        """lint-payload-size.sh の CASE_TABLE を行ごとの列 (id|script|arg|setup|expect|input) で返す。"""
        text = read(LINT_PAYLOAD_SIZE_SH)
        start = text.index("CASE_TABLE=$(cat <<EOF")
        end = text.index("\nEOF\n", start)
        return [
            line.split("|", 5)
            for line in text[start:end].splitlines()[1:]
            if line.strip()
        ]

    def test_payload_size_cases_do_not_set_up_model_state(self) -> None:
        offenders = [
            row[0]
            for row in self.case_table()
            for token in row[3].split()
            if token.startswith("state=") or token == "pending"
        ]
        self.assertEqual([], offenders)

    def test_payload_size_cases_measure_first_and_repeated_delivery(self) -> None:
        rows = self.case_table()
        for script, arg in (
            (INJECT_RULES_PART, "2"),
            (INJECT_RULES_PART, "3"),
            (INJECT_DISCIPLINE, "-"),
        ):
            with self.subTest(script=script, arg=arg):
                targets = [row for row in rows if row[1] == script and row[2] == arg]
                self.assertTrue(
                    any(row[3] == "-" and row[4] == "output" for row in targets),
                    f"{script} {arg}: 初回配送のケースが無い",
                )
                self.assertTrue(
                    any(row[3] != "-" and row[4] == "none" for row in targets),
                    f"{script} {arg}: マーカー存在時の無出力のケースが無い",
                )
        for script in (
            INJECT_ALWAYS,
            "inject-temporary.sh",
            "inject-auto.sh",
            "inject-subagent-rules.sh",
        ):
            with self.subTest(script=script):
                self.assertTrue(any(row[1] == script for row in rows), script)

    def test_workflow_paths_list_the_current_files_once_per_trigger(self) -> None:
        text = read(WORKFLOW_YML)
        pull_request_start = text.index("\n  pull_request:")
        push_start = text.index("\n  push:")
        permissions_start = text.index("\npermissions:")
        blocks = {
            "pull_request": text[pull_request_start:push_start],
            "push": text[push_start:permissions_start],
        }
        for trigger, block in blocks.items():
            for path in WORKFLOW_REQUIRED_PATHS:
                with self.subTest(trigger=trigger, path=path):
                    self.assertEqual(1, block.count(f'"{path}"'))


class VersionConsistencyTest(unittest.TestCase):
    """version が 4 箇所で ``PLUGIN_VERSION`` に一致する。"""

    def test_plugin_json(self) -> None:
        manifest = json.loads(read(PLUGIN_JSON))
        self.assertEqual(PLUGIN_NAME, manifest["name"])
        self.assertEqual(PLUGIN_VERSION, manifest["version"])

    def test_marketplace_entry(self) -> None:
        entries = [
            entry
            for entry in json.loads(read(MARKETPLACE_JSON))["plugins"]
            if entry["name"] == PLUGIN_NAME
        ]
        self.assertEqual(1, len(entries))
        self.assertEqual(PLUGIN_VERSION, entries[0]["version"])

    def test_repository_readme_table(self) -> None:
        self.assertIn(f"[{PLUGIN_NAME}](#{PLUGIN_NAME}) | {PLUGIN_VERSION} |", read(REPO_README))

    def test_plugin_readme_version_section(self) -> None:
        lines = read(PLUGIN_README).splitlines()
        self.assertIn("## バージョン", lines)
        following = [
            line.strip() for line in lines[lines.index("## バージョン") + 1 :] if line.strip()
        ]
        self.assertTrue(following, "## バージョン の後に本文が無い")
        self.assertEqual(f"v{PLUGIN_VERSION}", following[0])


if __name__ == "__main__":
    unittest.main()

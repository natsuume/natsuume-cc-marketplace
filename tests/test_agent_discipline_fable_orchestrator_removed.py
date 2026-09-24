"""agent-discipline: メインセッションが Fable のときも Opus と同じ規律を配送する契約テスト。

agent-discipline はメインセッションのモデルごとに規律テキストを配送する。メインセッションが
Fable の場合に専用の規律 (常時適用ルール・分業規律) を持たず、Opus 系と同じ版を配送することを、
hook を隔離した TMPDIR 上の subprocess として起動し、stdout JSON・state ファイル・prompt
ファイルの有無と本文で観測して固定する。

- Fable 専用の prompt ファイル (常時適用ルール・分業規律・分業規律前置き) が存在しない
- inject-always.sh (SessionStart) は Fable 判定時に Opus 判定時と同じ part 1/3 を配送する
- inject-rules-part.sh (UserPromptSubmit) は state が Fable でも part 2/3・part 3/3 を配送する
- inject-discipline.sh (UserPromptSubmit) は state が Fable のとき Opus 版の分業規律
  (直接配送・one-shot 補正とも) を配送する。判定不能時と Sonnet 確定時の配送は Sonnet 版
- resolve-model-on-prompt.sh (UserPromptSubmit) は判定不能から Fable に確定しても state を
  書いて pending を消すだけで、追加の配送をしない
- update-model-on-switch.sh (PostModelSwitch) は Opus と Fable の間の切替では通知せず、
  pending を消す通知では Fable 専用ファイルを案内しない
- 自己ゲート前置き (常時適用ルール用・part 用・分業規律用) の本文に Fable 専用の読み方指示が無い
- block-fable-subagent.sh は Fable メインセッションから Fable を実行する subagent の起動
  (model 未指定の継承・model: fable の明示) を deny する

各テストは tempfile.TemporaryDirectory で TMPDIR / HOME / XDG_CACHE_HOME を隔離し、実環境の
``${TMPDIR:-/tmp}/agent-discipline-state`` を読み書きしない。
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

INJECT_ALWAYS_SH = SCRIPTS_DIR / "inject-always.sh"
INJECT_RULES_PART_SH = SCRIPTS_DIR / "inject-rules-part.sh"
INJECT_DISCIPLINE_SH = SCRIPTS_DIR / "inject-discipline.sh"
RESOLVE_MODEL_ON_PROMPT_SH = SCRIPTS_DIR / "resolve-model-on-prompt.sh"
UPDATE_MODEL_ON_SWITCH_SH = SCRIPTS_DIR / "update-model-on-switch.sh"
BLOCK_FABLE_SUBAGENT_SH = SCRIPTS_DIR / "block-fable-subagent.sh"

STATE_DIR_NAME = "agent-discipline-state"
SESSION_ID = "fable-orchestrator-removed-session"

FABLE_MODEL = "claude-fable-5-1"
OPUS_MODEL = "claude-opus-5"
SONNET_MODEL = "claude-sonnet-5"

# 存在してはならない Fable 専用の prompt ファイル。
FABLE_ONLY_PROMPT_FILES = (
    "always-fable.md",
    "discipline-fable.md",
    "discipline-preamble-fable.md",
)

# Fable 専用の常時適用ルールの見出し (SessionStart の配送に含まれてはならない)。
FABLE_ALWAYS_HEADING = "# agent-discipline: 常時適用ルール (Fable)"

OPUS_DISCIPLINE_HEADING = "# agent-discipline: 分業規律 (Opus)"
SONNET_DISCIPLINE_HEADING = "# agent-discipline: 分業規律 (Sonnet)"

# 判定不能時の分業規律配送に含まれてはならない、Fable 専用の版を指す語。
FABLE_VERSION_WORDS = ("Fable 版", "discipline-fable")

# 自己ゲート前置きの本文に含まれてはならない、Fable 専用の読み方指示。
SELF_GATE_PROMPT_FILES = (
    "preamble-self-gate.md",
    "part-self-gate.md",
    "discipline-preamble-self-gate.md",
)
FABLE_READING_INSTRUCTIONS = (
    "Fable の場合",
    "Fable または Opus",
    "自分が Fable である場合",
)

HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)


def read_prompt(name: str) -> str:
    """prompt ファイルを読む。hook は ``$(cat ...)`` で読むため末尾の改行を落とす。"""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").rstrip("\n")


def strip_html_comments(text: str) -> str:
    """HTML コメント ``<!-- ... -->`` を取り除いた本文を返す。"""
    return HTML_COMMENT_PATTERN.sub("", text)


def first_heading_line(text: str) -> str:
    """HTML コメントを除いた本文の、最初の見出し行 (``# `` で始まる行) を返す。"""
    for line in strip_html_comments(text).splitlines():
        if line.startswith("# "):
            return line
    raise AssertionError("見出し行が見つからない")


def isolated_env(temp: Path) -> dict[str, str]:
    """親プロセスの env を継承せず、PATH と隔離ディレクトリだけを渡す env を作る。"""
    home = temp / "home"
    tmpdir = temp / "tmp"
    cache_home = temp / "cache"
    for directory in (home, tmpdir, cache_home):
        directory.mkdir(exist_ok=True)
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
        "XDG_CACHE_HOME": str(cache_home),
        "CLAUDE_PLUGIN_ROOT": str(PLUGIN_DIR),
    }


def state_dir_of(env: dict[str, str]) -> Path:
    return Path(env["TMPDIR"]) / STATE_DIR_NAME


def prepare_state(
    env: dict[str, str],
    *,
    model: str | None = None,
    pending: bool = False,
    discipline_marker: str | None = None,
) -> Path:
    """隔離した TMPDIR 配下に model / pending / 分業規律配送マーカーを用意する。"""
    state_dir = state_dir_of(env)
    state_dir.mkdir(parents=True, exist_ok=True)
    if model is not None:
        (state_dir / f"model-{SESSION_ID}").write_text(model, encoding="utf-8")
    if pending:
        (state_dir / f"pending-model-{SESSION_ID}").write_text("", encoding="utf-8")
    if discipline_marker is not None:
        (state_dir / f"delivered-discipline-{SESSION_ID}").write_text(
            discipline_marker, encoding="utf-8"
        )
    return state_dir


def run_hook(
    script: Path,
    env: dict[str, str],
    payload: dict[str, object],
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        cwd=ROOT,
        env=env,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def read_optional(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


class HookTestCase(unittest.TestCase):
    def additional_context(
        self, result: subprocess.CompletedProcess[str], event: str
    ) -> str | None:
        """stdout から additionalContext を取り出す (出力が無ければ None)。"""
        self.assertEqual(0, result.returncode, result.stderr)
        if not result.stdout.strip():
            return None
        hook_output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(event, hook_output.get("hookEventName"))
        return hook_output.get("additionalContext")


class FableOnlyPromptFilesTest(unittest.TestCase):
    """契約 1: Fable 専用の prompt ファイルが存在しない。"""

    def test_fable_only_prompt_files_do_not_exist(self) -> None:
        for name in FABLE_ONLY_PROMPT_FILES:
            with self.subTest(file=name):
                self.assertFalse(
                    (PROMPTS_DIR / name).exists(), f"{name} が存在している"
                )

    def test_no_file_references_fable_only_prompt_files(self) -> None:
        """plugin 配下と CI workflow のどのファイルも、削除した prompt ファイル名を参照しない。"""
        targets = [
            path
            for base in (PLUGIN_DIR, ROOT / ".github" / "workflows")
            for path in base.rglob("*")
            if path.is_file()
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8", errors="replace")
            for name in FABLE_ONLY_PROMPT_FILES:
                with self.subTest(path=path.relative_to(ROOT).as_posix(), file=name):
                    self.assertNotIn(name, text)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class InjectAlwaysFableTest(HookTestCase):
    """契約 2: SessionStart で Fable 判定時に Opus 判定時と同じ part 1/3 を配送する。"""

    def session_start_context(self, model: str) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            env = isolated_env(Path(temporary))
            payload = {
                "hook_event_name": "SessionStart",
                "session_id": SESSION_ID,
                "model": model,
            }
            result = run_hook(INJECT_ALWAYS_SH, env, payload)
        context = self.additional_context(result, "SessionStart")
        self.assertIsNotNone(context, f"{model}: additionalContext が出力されていない")
        # モデル ID に依存する差分は比較から除く。
        return str(context).replace(model, "<MODEL>")

    def test_fable_session_start_delivers_the_opus_part_one(self) -> None:
        fable_context = self.session_start_context(FABLE_MODEL)
        opus_context = self.session_start_context(OPUS_MODEL)
        sonnet_heading = first_heading_line(read_prompt("always-sonnet-1.md"))

        with self.subTest(check="always-sonnet-1.md の見出しを含む"):
            self.assertIn(sonnet_heading, fable_context)
        with self.subTest(check="Fable 版の見出しを含まない"):
            self.assertNotIn(FABLE_ALWAYS_HEADING, fable_context)
        with self.subTest(check="Opus 判定時と同じ additionalContext"):
            self.assertEqual(opus_context, fable_context)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class InjectRulesPartFableTest(HookTestCase):
    """契約 3: state が Fable でも part 2/3・part 3/3 を Opus と同じく配送する。"""

    def rules_part_context(self, model: str, part: str) -> str | None:
        with tempfile.TemporaryDirectory() as temporary:
            env = isolated_env(Path(temporary))
            prepare_state(env, model=model)
            payload = {"hook_event_name": "UserPromptSubmit", "session_id": SESSION_ID}
            result = run_hook(INJECT_RULES_PART_SH, env, payload, part)
        return self.additional_context(result, "UserPromptSubmit")

    def test_fable_state_receives_parts_two_and_three(self) -> None:
        for part in ("2", "3"):
            with self.subTest(part=part):
                body = read_prompt(f"always-sonnet-{part}.md")
                fable_context = self.rules_part_context(FABLE_MODEL, part)
                opus_context = self.rules_part_context(OPUS_MODEL, part)
                self.assertIsNotNone(
                    fable_context, f"state fable で part {part} が配送されない"
                )
                self.assertIn(body, str(fable_context))
                self.assertEqual(opus_context, fable_context)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class InjectDisciplineFableTest(HookTestCase):
    """契約 4: 分業規律は Fable 確定時に Opus 版、判定不能・Sonnet 確定時に Sonnet 版。"""

    def run_discipline(
        self,
        *,
        model: str | None = None,
        pending: bool = False,
        discipline_marker: str | None = None,
    ) -> tuple[str | None, str | None]:
        """hook を 1 回実行し、(additionalContext, 実行後のマーカー内容) を返す。"""
        with tempfile.TemporaryDirectory() as temporary:
            env = isolated_env(Path(temporary))
            state_dir = prepare_state(
                env, model=model, pending=pending, discipline_marker=discipline_marker
            )
            payload = {"hook_event_name": "UserPromptSubmit", "session_id": SESSION_ID}
            result = run_hook(INJECT_DISCIPLINE_SH, env, payload)
            marker = read_optional(state_dir / f"delivered-discipline-{SESSION_ID}")
        return self.additional_context(result, "UserPromptSubmit"), marker

    def test_fable_state_without_marker_delivers_the_opus_discipline(self) -> None:
        fable_context, fable_marker = self.run_discipline(model=FABLE_MODEL)
        opus_context, _ = self.run_discipline(model=OPUS_MODEL)
        self.assertIsNotNone(fable_context, "state fable で分業規律が配送されない")
        self.assertEqual(
            f"{OPUS_DISCIPLINE_HEADING}\n\n{read_prompt('discipline-opus.md')}",
            opus_context,
        )
        self.assertEqual(opus_context, fable_context)
        self.assertEqual("final", fable_marker)

    def test_fable_state_after_sonnet_gate_delivers_the_opus_correction(self) -> None:
        fable_context, fable_marker = self.run_discipline(
            model=FABLE_MODEL, discipline_marker="sonnet-gate"
        )
        opus_context, _ = self.run_discipline(
            model=OPUS_MODEL, discipline_marker="sonnet-gate"
        )
        self.assertIsNotNone(fable_context, "state fable で one-shot 補正が配送されない")
        self.assertIsNotNone(opus_context, "state opus で one-shot 補正が配送されない")
        self.assertIn(OPUS_DISCIPLINE_HEADING, str(opus_context))
        self.assertIn(read_prompt("discipline-opus.md"), str(opus_context))
        self.assertEqual(opus_context, fable_context)
        self.assertEqual("final", fable_marker)

    def test_pending_delivers_the_self_gated_sonnet_discipline(self) -> None:
        preamble = strip_html_comments(
            read_prompt("discipline-preamble-self-gate.md")
        ).strip()
        sonnet_body = read_prompt("discipline-sonnet.md")
        for label, model in (("state 無し", None), ("state fable (stale)", FABLE_MODEL)):
            with self.subTest(case=label):
                context, marker = self.run_discipline(model=model, pending=True)
                self.assertIsNotNone(context, "pending 時に分業規律が配送されない")
                text = str(context)
                self.assertTrue(text.startswith(SONNET_DISCIPLINE_HEADING), text[:200])
                self.assertIn(preamble, text)
                self.assertIn(sonnet_body, text)
                self.assertEqual("sonnet-gate", marker)
                visible = strip_html_comments(text)
                for word in FABLE_VERSION_WORDS:
                    self.assertNotIn(word, visible)

    def test_sonnet_state_delivers_the_sonnet_discipline(self) -> None:
        context, marker = self.run_discipline(model=SONNET_MODEL)
        self.assertEqual(
            f"{SONNET_DISCIPLINE_HEADING}\n\n{read_prompt('discipline-sonnet.md')}",
            context,
        )
        self.assertEqual("final", marker)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class ResolveModelOnPromptFableTest(HookTestCase):
    """契約 5: 判定不能から Fable / Opus に確定しても state 更新のみで配送しない。"""

    def test_confirmed_model_is_recorded_without_additional_context(self) -> None:
        for model in (FABLE_MODEL, OPUS_MODEL):
            with self.subTest(model=model):
                with tempfile.TemporaryDirectory() as temporary:
                    temp = Path(temporary)
                    env = isolated_env(temp)
                    state_dir = prepare_state(env, pending=True)
                    transcript = temp / "transcript.jsonl"
                    lines = [
                        {"type": "user", "message": {"content": "hello"}},
                        {
                            "type": "assistant",
                            "isSidechain": False,
                            "message": {"model": model},
                        },
                    ]
                    transcript.write_text(
                        "".join(json.dumps(line) + "\n" for line in lines),
                        encoding="utf-8",
                    )
                    payload = {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": SESSION_ID,
                        "transcript_path": str(transcript),
                    }
                    result = run_hook(RESOLVE_MODEL_ON_PROMPT_SH, env, payload)
                    state = read_optional(state_dir / f"model-{SESSION_ID}")
                    pending_left = (state_dir / f"pending-model-{SESSION_ID}").exists()
                self.assertEqual(model, state)
                self.assertFalse(pending_left, "pending マーカーが残っている")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("", result.stdout, "additionalContext が出力された")


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class UpdateModelOnSwitchFableTest(HookTestCase):
    """契約 6: Opus → Fable の切替は通知せず、pending 解消の通知は Opus 版を案内する。"""

    def run_switch(
        self, *, from_model: str, to_model: str, state: str | None, pending: bool
    ) -> tuple[subprocess.CompletedProcess[str], str | None, bool]:
        with tempfile.TemporaryDirectory() as temporary:
            env = isolated_env(Path(temporary))
            state_dir = prepare_state(env, model=state, pending=pending)
            payload = {
                "hook_event_name": "PostModelSwitch",
                "session_id": SESSION_ID,
                "from_model": from_model,
                "to_model": to_model,
            }
            result = run_hook(UPDATE_MODEL_ON_SWITCH_SH, env, payload)
            new_state = read_optional(state_dir / f"model-{SESSION_ID}")
            pending_left = (state_dir / f"pending-model-{SESSION_ID}").exists()
        return result, new_state, pending_left

    def test_opus_to_fable_switch_updates_state_silently(self) -> None:
        result, state, _ = self.run_switch(
            from_model=OPUS_MODEL, to_model=FABLE_MODEL, state=OPUS_MODEL, pending=False
        )
        self.assertEqual(FABLE_MODEL, state)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout, "Opus → Fable の切替で通知が出力された")

    def test_pending_notice_for_fable_points_at_the_opus_discipline(self) -> None:
        result, state, pending_left = self.run_switch(
            from_model=SONNET_MODEL, to_model=FABLE_MODEL, state=None, pending=True
        )
        self.assertEqual(FABLE_MODEL, state)
        self.assertFalse(pending_left, "pending マーカーが残っている")
        context = self.additional_context(result, "PostModelSwitch")
        self.assertIsNotNone(context, "pending 解消時に通知が出力されない")
        for name in ("always-fable.md", "discipline-fable.md"):
            with self.subTest(check=f"{name} に言及しない"):
                self.assertNotIn(name, str(context))
        with self.subTest(check="discipline-opus.md に言及する"):
            self.assertIn("discipline-opus.md", str(context))


class SelfGatePromptsTest(unittest.TestCase):
    """契約 7: 自己ゲート前置きの本文に Fable 専用の読み方指示が無い。"""

    def test_self_gate_prompts_have_no_fable_specific_reading_instruction(
        self,
    ) -> None:
        for name in SELF_GATE_PROMPT_FILES:
            body = strip_html_comments(read_prompt(name))
            for phrase in FABLE_READING_INSTRUCTIONS:
                with self.subTest(file=name, phrase=phrase):
                    self.assertNotIn(phrase, body)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockFableSubagentRegressionTest(unittest.TestCase):
    """契約 8: Fable メインセッションから Fable を実行する subagent の起動を deny する。"""

    def test_fable_main_session_denies_fable_subagents(self) -> None:
        cases = (
            ("model 未指定 (継承)", {"subagent_type": "general-purpose"}),
            (
                "model: fable 明示",
                {"subagent_type": "general-purpose", "model": "fable"},
            ),
        )
        for label, tool_input in cases:
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory() as temporary:
                    env = isolated_env(Path(temporary))
                    prepare_state(env, model=FABLE_MODEL)
                    payload = {
                        "hook_event_name": "PreToolUse",
                        "session_id": SESSION_ID,
                        "tool_name": "Agent",
                        "tool_input": tool_input,
                    }
                    result = run_hook(BLOCK_FABLE_SUBAGENT_SH, env, payload)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertTrue(result.stdout.strip(), "deny JSON が出力されていない")
                output = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual("PreToolUse", output["hookEventName"])
                self.assertEqual("deny", output["permissionDecision"])


if __name__ == "__main__":
    unittest.main()

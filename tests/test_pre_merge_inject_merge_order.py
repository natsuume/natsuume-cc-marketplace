"""pre-merge-codex-review: merge 前に codex review を起動する規律を SessionStart hook
で additionalContext として注入する契約テスト。

対象:

- `hooks/hooks.json` の SessionStart 配線 (matcher 無し、
  `inject-merge-order-rules.sh` を呼ぶ group が 1 つだけ存在する)
- `hooks/scripts/inject-merge-order-rules.sh`: SessionStart hook input を stdin から
  受け取り、`hooks/prompts/merge-order-rules.md` の全文 (末尾改行を除去したもの) を
  `additionalContext` として持つ JSON を stdout に出力する。jq 不在・prompt ファイルの
  欠落 / 空 / 読み取り不能のいずれでも無出力のまま exit 0 とする (fail-open)
- `hooks/prompts/merge-order-rules.md`: 先頭行が固定の見出しであること、UTF-16 code
  unit 換算で 6,000 以下のサイズであること、必須文字列 (agent 起動条件・定型 prompt 文
  等) を含むこと、issue/PR 番号参照や日付を含まないこと
- `hooks/scripts/block-pre-merge.sh`: 上記の定型 prompt 文をそのまま含む (subagent 起動
  を案内する文言と注入文を同一の 1 文に統一する契約)
- `agents/codex-reviewer.md` の YAML frontmatter `description`: merge 実行権限を示唆する
  語 (`gh pr merge` / `merge gate` / `deny` / `投稿`) を含まず、`read-only` と
  `parent-safe` を含む
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
PLUGIN_DIR = ROOT / "plugins" / "pre-merge-codex-review"
HOOKS_DIR = PLUGIN_DIR / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"
SCRIPT = HOOKS_DIR / "scripts" / "inject-merge-order-rules.sh"
PROMPT = HOOKS_DIR / "prompts" / "merge-order-rules.md"
BLOCK_PRE_MERGE = HOOKS_DIR / "scripts" / "block-pre-merge.sh"
CODEX_REVIEWER_AGENT = PLUGIN_DIR / "agents" / "codex-reviewer.md"

DEFAULT_PAYLOAD = {"hook_event_name": "SessionStart"}

PROMPT_SIZE_LIMIT = 6000

# hooks/prompts/merge-order-rules.md (Phase B で作成) が含むべき定型 prompt 文。
# hooks/scripts/block-pre-merge.sh の subagent 起動案内文もこれと同じ 1 文に統一する
# 契約なので、両方のテストで共有する。
FIXED_PROMPT_SENTENCE = (
    "current branch の PR (#<番号>) の merge-base..head 差分に対して、"
    "agent body の契約に従い codex review を 1 回実行し、"
    "parent-safe な markdown report を返してください。"
)

REQUIRED_PROMPT_SUBSTRINGS = [
    "pre-merge-codex-review:codex-reviewer",
    'model: "sonnet"',
    "run_in_background: false",
    "gh pr merge",
    "--delete-branch",
    "AskUserQuestion",
    FIXED_PROMPT_SENTENCE,
]

FORBIDDEN_DESCRIPTION_SUBSTRINGS = ["gh pr merge", "merge gate", "deny", "投稿"]
REQUIRED_DESCRIPTION_SUBSTRINGS = ["read-only", "parent-safe"]

SH = shutil.which("sh")
HAS_JQ = shutil.which("jq") is not None


class HooksJsonSessionStartWiringTest(unittest.TestCase):
    """テストケース 1: hooks.json の SessionStart 配線。"""

    def test_session_start_has_single_unmatched_group_calling_script(self) -> None:
        config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        session_start_groups = config["hooks"]["SessionStart"]
        self.assertEqual(len(session_start_groups), 1, session_start_groups)

        group = session_start_groups[0]
        self.assertNotIn("matcher", group, group)

        commands = [
            hook["command"]
            for hook in group["hooks"]
            if hook.get("type") == "command"
        ]
        self.assertTrue(
            any("inject-merge-order-rules.sh" in command for command in commands),
            commands,
        )

    def test_existing_lifecycle_wiring_is_preserved(self) -> None:
        # SessionStart 追加が既存の PreToolUse / SubagentStart / SubagentStop /
        # PostToolUseFailure の配線を壊していないことを固定する (回帰防止)。
        config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        hooks = config["hooks"]

        pre_tool_use_commands = [
            hook["command"]
            for group in hooks["PreToolUse"]
            if group.get("matcher") == "Bash"
            for hook in group["hooks"]
        ]
        self.assertTrue(
            any("block-pre-merge.sh" in c for c in pre_tool_use_commands)
        )
        self.assertTrue(
            any("block-bg-codex-wrapper.sh" in c for c in pre_tool_use_commands)
        )

        for event in ("SubagentStart", "SubagentStop"):
            groups = [
                group
                for group in hooks[event]
                if group.get("matcher") == "^pre-merge-codex-review:codex-reviewer$"
            ]
            self.assertEqual(len(groups), 1, (event, groups))
            self.assertIn("auto-mark.sh", groups[0]["hooks"][0]["command"])

        failure_groups = hooks["PostToolUseFailure"]
        self.assertEqual(len(failure_groups), 1)
        self.assertEqual(failure_groups[0]["matcher"], "Agent|Task")
        self.assertIn("auto-mark.sh", failure_groups[0]["hooks"][0]["command"])


class ScriptSkeletonTest(unittest.TestCase):
    """テストケース 2: script の存在・実行可能ビット・shebang。"""

    def test_script_exists_is_executable_and_has_posix_sh_shebang(self) -> None:
        self.assertTrue(SCRIPT.is_file(), f"missing hook script: {SCRIPT}")
        mode = SCRIPT.stat().st_mode
        self.assertTrue(mode & 0o111, "script is not executable")
        first_line = SCRIPT.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, "#!/bin/sh")


class ScriptRuntimeContractTest(unittest.TestCase):
    """テストケース 3〜6: script の I/O 契約と fail-open 挙動。"""

    def _run_script(
        self,
        script_path: Path,
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        payload: dict[str, object] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert SH is not None
        body = json.dumps(
            payload if payload is not None else DEFAULT_PAYLOAD
        ).encode("utf-8")
        return subprocess.run(
            [SH, str(script_path)],
            input=body,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _copy_hooks_tree(self, tmp_path: Path) -> tuple[Path, Path]:
        """hooks/ 全体を一時ディレクトリへ複製し、複製内の (script, prompt) の
        パスを返す。実リポジトリの状態は変更しない。"""
        copied_hooks = tmp_path / "hooks"
        shutil.copytree(HOOKS_DIR, copied_hooks)
        copied_script = copied_hooks / "scripts" / "inject-merge-order-rules.sh"
        copied_prompt = copied_hooks / "prompts" / "merge-order-rules.md"
        return copied_script, copied_prompt

    def _minimal_shims(self, tmp_path: Path) -> Path | None:
        # jq だけを PATH から外すため、jq 以外に本 script が使う外部コマンド
        # (cat / dirname) だけを symlink した最小 PATH を作る。他の script の
        # jq 不在テストと同じ手法 (tests/test_pre_merge_codex_gate.py 参照)。
        cat = shutil.which("cat")
        dirname = shutil.which("dirname")
        if cat is None or dirname is None:
            return None
        shims = tmp_path / "bin"
        shims.mkdir()
        (shims / "cat").symlink_to(cat)
        (shims / "dirname").symlink_to(dirname)
        return shims

    @unittest.skipUnless(SH and HAS_JQ, "requires sh and jq")
    def test_normal_run_emits_prompt_content_as_additional_context(self) -> None:
        self.assertTrue(
            PROMPT.is_file(), f"missing prompt file (Phase B contract): {PROMPT}"
        )
        result = self._run_script(SCRIPT, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        response = json.loads(result.stdout)
        hook_output = response["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "SessionStart")
        expected = PROMPT.read_text(encoding="utf-8").rstrip("\n")
        self.assertEqual(hook_output["additionalContext"], expected)

    @unittest.skipUnless(SH, "requires sh")
    def test_missing_jq_produces_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp_path = Path(tmp_name)
            shims = self._minimal_shims(tmp_path)
            if shims is None:
                self.skipTest("requires cat and dirname")
            env = os.environ.copy()
            env["PATH"] = str(shims)
            result = self._run_script(SCRIPT, cwd=tmp_path, env=env)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"")

    @unittest.skipUnless(SH, "requires sh")
    def test_missing_prompt_file_produces_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp_path = Path(tmp_name)
            script_path, prompt_path = self._copy_hooks_tree(tmp_path)
            prompt_path.unlink(missing_ok=True)
            result = self._run_script(script_path, cwd=tmp_path)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"")

    @unittest.skipUnless(SH, "requires sh")
    def test_empty_prompt_file_produces_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp_path = Path(tmp_name)
            script_path, prompt_path = self._copy_hooks_tree(tmp_path)
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text("", encoding="utf-8")
            result = self._run_script(script_path, cwd=tmp_path)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"")


class PromptContractTest(unittest.TestCase):
    """テストケース 7〜9: merge-order-rules.md (Phase B で作成) の内容契約。"""

    def _read_prompt(self) -> str:
        self.assertTrue(
            PROMPT.is_file(), f"missing prompt file (Phase B contract): {PROMPT}"
        )
        return PROMPT.read_text(encoding="utf-8")

    def test_prompt_first_line_is_expected_heading(self) -> None:
        text = self._read_prompt()
        first_line = text.splitlines()[0] if text else ""
        self.assertEqual(
            first_line,
            "# pre-merge-codex-review: merge 前 codex review の起動順",
        )

    def test_prompt_size_is_within_utf16_code_unit_limit(self) -> None:
        text = self._read_prompt()
        length = len(text.encode("utf-16-le")) // 2
        self.assertLessEqual(length, PROMPT_SIZE_LIMIT)

    def test_prompt_contains_all_required_substrings(self) -> None:
        text = self._read_prompt()
        for substring in REQUIRED_PROMPT_SUBSTRINGS:
            with self.subTest(substring=substring):
                self.assertIn(substring, text)

    def test_prompt_has_no_issue_pr_number_or_date_references(self) -> None:
        text = self._read_prompt()
        self.assertIsNone(
            re.search(r"#[0-9]+", text), "prompt に issue/PR 番号参照が含まれている"
        )
        self.assertIsNone(
            re.search(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", text),
            "prompt に日付が含まれている",
        )


class BlockPreMergeFixedSentenceTest(unittest.TestCase):
    """テストケース 10: block-pre-merge.sh の定型 prompt 文の統一。"""

    def test_block_pre_merge_source_contains_fixed_prompt_sentence(self) -> None:
        source = BLOCK_PRE_MERGE.read_text(encoding="utf-8")
        self.assertIn(FIXED_PROMPT_SENTENCE, source)


class CodexReviewerAgentDescriptionTest(unittest.TestCase):
    """テストケース 11: agents/codex-reviewer.md frontmatter description の語彙契約。"""

    def _description(self) -> str:
        lines = CODEX_REVIEWER_AGENT.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines, "agent file is empty")
        self.assertEqual(lines[0], "---", "frontmatter start marker (---) missing")
        end_index = None
        for index in range(1, len(lines)):
            if lines[index] == "---":
                end_index = index
                break
        self.assertIsNotNone(end_index, "frontmatter end marker (---) missing")
        assert end_index is not None
        description_line = None
        for line in lines[1:end_index]:
            if line.startswith("description:"):
                description_line = line[len("description:") :].strip()
                break
        self.assertIsNotNone(description_line, "description field missing")
        assert description_line is not None
        return description_line

    def test_description_forbids_merge_authority_language(self) -> None:
        description = self._description()
        for forbidden in FORBIDDEN_DESCRIPTION_SUBSTRINGS:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, description)

    def test_description_requires_read_only_and_parent_safe(self) -> None:
        description = self._description()
        for required in REQUIRED_DESCRIPTION_SUBSTRINGS:
            with self.subTest(required=required):
                self.assertIn(required, description)


if __name__ == "__main__":
    unittest.main()

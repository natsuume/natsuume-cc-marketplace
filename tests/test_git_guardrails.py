"""git-guardrails の異常終了可視化に関する受入テスト。

issue #139 の spec-first Phase A で固定する公開契約:

- jq が対象コマンドを含む入力の解析に失敗した場合、hook は非ゼロで終了し、
  hook 固有タグを含む診断を stderr に出す。
- 必須 library の source 失敗、または source 後に必須関数が存在しない場合も同じ。
- 粗フィルタ対象外の入力と、正常な対象コマンドでは診断を出さない。

テストは hook を subprocess として起動し、内部 helper ではなく PreToolUse hook の
process boundary から exit status / stdout / stderr を観測する。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "git-guardrails"


@dataclass(frozen=True)
class HookCase:
    script: str
    tag: str
    command: str
    malformed_target_input: str


HOOK_CASES = (
    HookCase(
        "block-default-branch-commit.sh",
        "block-default-branch-commit",
        "git commit -m test",
        '{"tool_input":{"command":"git commit',
    ),
    HookCase(
        "block-default-branch-push.sh",
        "block-default-branch-push",
        "git push origin feature",
        '{"tool_input":{"command":"git push',
    ),
    HookCase(
        "block-default-branch-pr.sh",
        "block-default-branch-pr",
        "gh pr create --head feature --title t --body b",
        '{"tool_input":{"command":"gh pr create',
    ),
)


def run_hook(plugin: Path, case: HookCase, raw_input: str) -> subprocess.CompletedProcess[bytes]:
    hook = plugin / "hooks" / "scripts" / case.script
    return subprocess.run(
        ["bash", str(hook)],
        input=raw_input.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class GitGuardrailsFailureVisibilityTest(unittest.TestCase):
    def assert_failure_visible(
        self, result: subprocess.CompletedProcess[bytes], case: HookCase
    ) -> None:
        stderr = result.stderr.decode("utf-8")
        self.assertNotEqual(result.returncode, 0, stderr)
        self.assertIn(f"[git-guardrails/{case.tag}]", stderr)
        self.assertIn("予期せぬエラー", stderr)
        self.assertEqual(result.stdout, b"")

    def copied_plugin(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        plugin = Path(temporary.name) / "git-guardrails"
        shutil.copytree(PLUGIN, plugin)
        return temporary, plugin

    def test_jq_parse_failure_is_visible_for_all_hooks(self) -> None:
        for case in HOOK_CASES:
            with self.subTest(hook=case.tag):
                result = run_hook(PLUGIN, case, case.malformed_target_input)
                self.assert_failure_visible(result, case)

    def test_missing_exit_trap_library_is_visible_for_all_hooks(self) -> None:
        temporary, plugin = self.copied_plugin()
        with temporary:
            (plugin / "hooks" / "scripts" / "lib" / "exit-trap.sh").unlink()
            for case in HOOK_CASES:
                with self.subTest(hook=case.tag):
                    payload = json.dumps({"tool_input": {"command": case.command}})
                    self.assert_failure_visible(run_hook(plugin, case, payload), case)

    def test_missing_runtime_libraries_are_visible_for_all_hooks(self) -> None:
        for library in ("cmd-parser.sh", "default-branch.sh"):
            temporary, plugin = self.copied_plugin()
            with temporary, self.subTest(library=library):
                (plugin / "hooks" / "scripts" / "lib" / library).unlink()
                for case in HOOK_CASES:
                    with self.subTest(hook=case.tag):
                        payload = json.dumps({"tool_input": {"command": case.command}})
                        self.assert_failure_visible(run_hook(plugin, case, payload), case)

    def test_missing_required_functions_are_visible_for_all_hooks(self) -> None:
        for library in ("cmd-parser.sh", "default-branch.sh"):
            temporary, plugin = self.copied_plugin()
            with temporary, self.subTest(library=library):
                (plugin / "hooks" / "scripts" / "lib" / library).write_text(
                    "#!/bin/bash\n", encoding="utf-8"
                )
                for case in HOOK_CASES:
                    with self.subTest(hook=case.tag):
                        payload = json.dumps({"tool_input": {"command": case.command}})
                        self.assert_failure_visible(run_hook(plugin, case, payload), case)

    def test_non_target_and_healthy_paths_do_not_emit_diagnostic(self) -> None:
        for case in HOOK_CASES:
            with self.subTest(hook=case.tag, path="coarse-filter"):
                result = run_hook(PLUGIN, case, "not valid json")
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"[git-guardrails/", result.stderr)

            with self.subTest(hook=case.tag, path="healthy-target"):
                payload = json.dumps({"tool_input": {"command": case.command}})
                result = run_hook(PLUGIN, case, payload)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
                self.assertNotIn(b"[git-guardrails/", result.stderr)


if __name__ == "__main__":
    unittest.main()

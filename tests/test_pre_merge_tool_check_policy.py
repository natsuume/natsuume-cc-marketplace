"""pre-merge-cross-review hooks module の tool.check 判定ロジック (tool-check-policy.mjs) の契約テスト。

判定ロジックは I/O を持たない ES module なので、node で直接 import して評価する。
公開境界は `isTargetCommand(command)` と `decideToolCheck({tool, input, beneath, permissionMode})`
の 2 関数で、engine との接続 (register.ts) はここでは扱わない。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    REPO_ROOT
    / "plugins"
    / "pre-merge-cross-review"
    / "hooks"
    / "module"
    / "tool-check-policy.mjs"
)

# stdin で受けた呼び出し列を順に評価し、結果を JSON 配列で返す node script。
# decideToolCheck が beneath を同一オブジェクトのまま返したかを `same` で報告する。
NODE_RUNNER = """
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const policy = await import(pathToFileURL(process.argv[1]).href);
const calls = JSON.parse(readFileSync(0, 'utf8'));
const results = calls.map((call) => {
  if (call.fn === 'isTargetCommand') {
    return { value: policy.isTargetCommand(call.command) };
  }
  const beneath = call.beneath;
  const value = policy.decideToolCheck({
    tool: call.tool, input: call.input, beneath, permissionMode: call.permissionMode,
  });
  return { value, same: value === beneath };
});
process.stdout.write(JSON.stringify(results));
"""

TARGET_COMMANDS = [
    # merge: 番号 (任意) と戦略フラグ 1 つ
    "gh pr merge 123 --squash",
    "gh pr merge --squash",
    "gh pr merge 1 --merge",
    "gh pr merge 1 --rebase",
    "gh pr merge --squash 1",
    "  gh pr merge 1 --squash  ",
    "gh  pr  merge\t1 --squash",
    # view: 番号または branch 名 (任意) と許可したフラグ
    "gh pr view",
    "gh pr view 469 --json state,mergedAt,mergeCommit",
    "gh pr view 472 --json state,mergeCommit --jq '.state + \" \" + .mergeCommit.oid'",
    "gh pr view 413 --json state,mergedAt,mergeCommit --jq '{state, mergedAt, merge: .mergeCommit.oid}'",
    "gh pr view 474 --json state,mergedAt,mergeCommit -q '\"\\(.state) \\(.mergedAt)\"'",
    "gh pr view 1 --json=state --jq=.state",
    "gh pr view 1 --json state -q .state",
    "gh pr view 1 --comments",
    "gh pr view 1 -c",
    "gh pr view feat/issue-477-pre-merge-mod-tool-check",
    "gh pr view 1 --json body --jq '.env'",
    "gh pr view 1 --jq .environment",
    "gh pr view 1 --jq='.state'",
    # checks: 番号または branch 名 (任意) と許可したフラグ
    "gh pr checks 469",
    "gh pr checks 469 --watch",
    "gh pr checks --watch --interval 10",
    "gh pr checks 1 -i 5",
    "gh pr checks 1 --required --fail-fast",
    "gh pr checks 1 --json name,state --jq '.[].state'",
]

NON_TARGET_COMMANDS = [
    # merge の正規形から外れるもの
    "gh pr merge 1",
    "gh pr merge",
    "gh pr merge 1 --squash --merge",
    "gh pr merge 1 2 --squash",
    "gh pr merge 0 --squash",
    "gh pr merge feat/x --squash",
    "gh pr merge 1 -s",
    "gh pr merge 1 --squash --subject x",
    "gh pr merge 1 --squash -R owner/repo",
    "gh pr merge 1 --squash --delete-branch",
    "gh pr merge 1 --squash --delete-branch=true",
    "gh pr merge 1 --squash -d",
    "gh pr merge 1 -sd",
    "gh pr merge 1 --squash --admin",
    "gh pr merge 1 --squash --auto",
    # view / checks の正規形から外れるもの (別ホスト・変数展開・未許可のフラグ等)
    "gh pr view -R evil.example/owner/repo 1",
    "gh pr view 1 --repo evil.example/owner/repo",
    "gh pr view 1 --repo=evil.example/owner/repo",
    "gh pr view https://evil.example/owner/repo/pull/1",
    "gh pr view owner:branch",
    "gh pr view $GH_TOKEN",
    "gh pr view 1 --json $HOME",
    "gh pr view 1 --jq $X",
    'gh pr view "1"',
    'gh pr view 1 --jq ".state"',
    "gh pr view \\$HOME",
    "gh pr view 1 --jq '.state",
    "gh pr view 1 --jq",
    "gh pr view 1 --json",
    "gh pr view 1 2",
    "gh pr view -1",
    "gh pr view 1 --web",
    "gh pr view 1 --watch",
    "gh pr checks 1 --comments",
    "gh pr checks 1 --interval x",
    "gh pr checks 1 --web",
    "gh pr view 1 --jq '.a'x",
    "gh pr view 1 '--repo=evil.example/owner/repo'",
    # quote の内側でも metacharacter を含むものは対象外
    "gh pr view 1 --jq '.body | length'",
    # 連結・リダイレクト・置換・改行
    "gh pr merge 1 --squash && echo ok",
    "gh pr merge 1 --squash; echo ok",
    "gh pr view 1 | head",
    "gh pr view 1 > out.txt",
    "gh pr view 1 2>&1",
    "gh pr checks 1 &",
    "gh pr view $(echo 1)",
    "gh pr view ${N}",
    "gh pr view `echo 1`",
    "gh pr view 1\ngh pr merge 1 --squash",
    "gh pr view < in.txt",
    # quote の内側の metacharacter
    "gh pr view 1 --jq '.a; .b'",
    "gh pr view 1 --jq '.a > 1'",
    "gh pr view 1 --jq '$(x)'",
    "gh pr view 1 --jq '${x}'",
    "gh pr view 1 --jq '`x`'",
    "gh pr view 1 --jq '.a & .b'",
    "gh pr view 1 --jq '.a\n.b'",
    # jq の式での環境変数・変数の参照
    "gh pr view 1 --json number -q env",
    "gh pr view 1 --json number --jq env.GH_TOKEN",
    "gh pr view 1 --json number --jq '$ENV.ANTHROPIC_API_KEY'",
    "gh pr view 1 --json state --jq '{state, t: env.GH_TOKEN}'",
    "gh pr view 1 --json state --jq '(env)'",
    "gh pr checks 1 --json name --jq 'env'",
    "gh pr view 1 --jq '$x'",
    "gh pr view 1 --jq=env",
    # スペース・タブ以外の空白、印字可能な ASCII 以外の文字
    "gh pr merge 1 --squash",
    "gh pr view 1\r",
    "gh pr view 1\v",
    "gh pr view\f1",
    "﻿gh pr view 1",
    "gh pr view 1 --jq '.タイトル'",
    # 値付きフラグ・真偽値フラグ・番号の細かい形
    "gh pr view 1 -q=.state",
    "gh pr checks 1 -i5",
    "gh pr merge 1 --squash=true",
    "gh pr checks 1 --watch=false",
    "gh pr merge 1 --squash --squash",
    "gh pr merge 01 --squash",
    # env 代入・ラッパー
    "GH_TOKEN=x gh pr merge 1 --squash",
    "env gh pr view 1",
    "bash -c 'gh pr view 1'",
    "sh -c 'gh pr view 1'",
    "eval gh pr view 1",
    "xargs gh pr view",
    # 対象外のサブコマンド・コマンド
    "gh pr create --title t --body b",
    "gh pr edit 1 --body b",
    "gh pr viewer 1",
    "gh pr",
    "gh issue view 1",
    "git pr merge 1",
    "",
    "   ",
]

NON_STRING_COMMANDS = [None, 123, ["gh", "pr", "view"], {"command": "gh pr view"}]

ASK = {"decision": "ask", "reason": "core ask"}
DENY = {"decision": "deny", "reason": "マージをブロックしました。"}
ALLOW = {"decision": "allow", "rule": "Bash(gh pr view:*)"}


@unittest.skipUnless(
    shutil.which("node"), "tool-check-policy.mjs の評価には node が必要"
)
class ToolCheckPolicyTest(unittest.TestCase):
    def run_calls(self, calls: list[dict]) -> list[dict]:
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", NODE_RUNNER, str(POLICY_PATH)],
            input=json.dumps(calls),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return json.loads(completed.stdout)

    def decide(self, **call: object) -> dict:
        return self.run_calls([{"fn": "decideToolCheck", **call}])[0]

    # --- isTargetCommand -------------------------------------------------

    def test_single_merge_view_checks_are_targets(self) -> None:
        results = self.run_calls(
            [{"fn": "isTargetCommand", "command": c} for c in TARGET_COMMANDS]
        )
        for command, result in zip(TARGET_COMMANDS, results):
            with self.subTest(command=command):
                self.assertIs(True, result["value"])

    def test_forbidden_forms_are_not_targets(self) -> None:
        results = self.run_calls(
            [{"fn": "isTargetCommand", "command": c} for c in NON_TARGET_COMMANDS]
        )
        for command, result in zip(NON_TARGET_COMMANDS, results):
            with self.subTest(command=command):
                self.assertIs(False, result["value"])

    def test_non_string_commands_are_not_targets(self) -> None:
        results = self.run_calls(
            [{"fn": "isTargetCommand", "command": c} for c in NON_STRING_COMMANDS]
        )
        for command, result in zip(NON_STRING_COMMANDS, results):
            with self.subTest(command=command):
                self.assertIs(False, result["value"])

    # --- decideToolCheck: 引き上げる場合 -----------------------------------

    def test_auto_mode_ask_on_target_is_raised_to_allow(self) -> None:
        for command in ["gh pr merge 1 --squash", "gh pr view 1", "gh pr checks 1"]:
            with self.subTest(command=command):
                result = self.decide(
                    tool="Bash",
                    input={"command": command},
                    beneath=ASK,
                    permissionMode="auto",
                )
                self.assertEqual("allow", result["value"]["decision"])
                reason = result["value"].get("reason")
                self.assertIsInstance(reason, str)
                self.assertTrue(reason.startswith("pre-merge-cross-review:"), reason)

    def test_merge_and_read_only_reasons_differ(self) -> None:
        merge = self.decide(
            tool="Bash",
            input={"command": "gh pr merge 1 --squash"},
            beneath=ASK,
            permissionMode="auto",
        )
        view = self.decide(
            tool="Bash",
            input={"command": "gh pr view 1"},
            beneath=ASK,
            permissionMode="auto",
        )
        self.assertIn("gh pr merge", merge["value"]["reason"])
        self.assertIn("gh pr view", view["value"]["reason"])

    def test_reason_names_the_command_in_japanese(self) -> None:
        for command, name in [
            ("gh pr merge 1 --squash", "gh pr merge"),
            ("gh pr view 1", "gh pr view"),
            ("gh pr checks 1", "gh pr checks"),
        ]:
            with self.subTest(command=command):
                reason = self.decide(
                    tool="Bash",
                    input={"command": command},
                    beneath=ASK,
                    permissionMode="auto",
                )["value"]["reason"]
                self.assertIn(name, reason)
                self.assertRegex(reason, r"[぀-ヿ一-鿿]")

    # --- decideToolCheck: 下位判定をそのまま返す場合 ----------------------

    def assert_unchanged(self, result: dict) -> None:
        self.assertIs(True, result["same"], result)

    def test_deny_beneath_is_never_overridden(self) -> None:
        self.assert_unchanged(
            self.decide(
                tool="Bash",
                input={"command": "gh pr merge 1 --squash"},
                beneath=DENY,
                permissionMode="auto",
            )
        )

    def test_allow_beneath_is_returned_as_is(self) -> None:
        self.assert_unchanged(
            self.decide(
                tool="Bash",
                input={"command": "gh pr view 1"},
                beneath=ALLOW,
                permissionMode="auto",
            )
        )

    def test_non_auto_modes_are_unchanged(self) -> None:
        for mode in ["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk"]:
            with self.subTest(mode=mode):
                self.assert_unchanged(
                    self.decide(
                        tool="Bash",
                        input={"command": "gh pr merge 1 --squash"},
                        beneath=ASK,
                        permissionMode=mode,
                    )
                )

    def test_unknown_mode_is_unchanged(self) -> None:
        for mode in [None, "", "AUTO", 1]:
            with self.subTest(mode=mode):
                self.assert_unchanged(
                    self.decide(
                        tool="Bash",
                        input={"command": "gh pr merge 1 --squash"},
                        beneath=ASK,
                        permissionMode=mode,
                    )
                )

    def test_non_bash_tools_are_unchanged(self) -> None:
        for tool in ["Read", "PowerShell", "mcp__github__merge", "bash", None]:
            with self.subTest(tool=tool):
                self.assert_unchanged(
                    self.decide(
                        tool=tool,
                        input={"command": "gh pr merge 1 --squash"},
                        beneath=ASK,
                        permissionMode="auto",
                    )
                )

    def test_malformed_input_is_unchanged(self) -> None:
        for tool_input in [None, "gh pr view 1", {}, {"command": 1}, ["gh pr view 1"]]:
            with self.subTest(input=tool_input):
                self.assert_unchanged(
                    self.decide(
                        tool="Bash",
                        input=tool_input,
                        beneath=ASK,
                        permissionMode="auto",
                    )
                )

    def test_non_target_commands_are_unchanged(self) -> None:
        for command in [
            "gh pr merge 1 --squash --delete-branch",
            "gh pr view 1 | head",
            "ls",
        ]:
            with self.subTest(command=command):
                self.assert_unchanged(
                    self.decide(
                        tool="Bash",
                        input={"command": command},
                        beneath=ASK,
                        permissionMode="auto",
                    )
                )

    def test_ask_from_explicit_rule_is_unchanged(self) -> None:
        for command in ["gh pr merge 1 --squash", "gh pr view 1"]:
            with self.subTest(command=command):
                self.assert_unchanged(
                    self.decide(
                        tool="Bash",
                        input={"command": command},
                        beneath={"decision": "ask", "rule": "Bash(gh pr merge:*)"},
                        permissionMode="auto",
                    )
                )

    def test_ask_with_non_string_or_blank_rule_is_unchanged(self) -> None:
        for rule in [None, {}, [], 0, "  "]:
            with self.subTest(rule=rule):
                self.assert_unchanged(
                    self.decide(
                        tool="Bash",
                        input={"command": "gh pr view 1"},
                        beneath={"decision": "ask", "rule": rule},
                        permissionMode="auto",
                    )
                )

    def test_ask_with_empty_rule_is_raised(self) -> None:
        result = self.decide(
            tool="Bash",
            input={"command": "gh pr view 1"},
            beneath={"decision": "ask", "rule": ""},
            permissionMode="auto",
        )
        self.assertEqual("allow", result["value"]["decision"])

    def test_unexpected_beneath_decision_is_unchanged(self) -> None:
        self.assert_unchanged(
            self.decide(
                tool="Bash",
                input={"command": "gh pr view 1"},
                beneath={"decision": "maybe"},
                permissionMode="auto",
            )
        )


if __name__ == "__main__":
    unittest.main()

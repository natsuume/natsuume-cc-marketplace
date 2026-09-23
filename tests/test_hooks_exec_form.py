"""hook の起動形 (exec form) と、それに同期する配布メタデータの契約を検査する。

このファイルが固定する契約:

- A. `plugins/*/hooks/hooks.json` の `type: "command"` entry は exec form
  (`command` + `args`) で登録される。`command` は shell が解釈する文字 (空白・引用符・
  `$(`・バッククォート) を含まず、`${CLAUDE_PLUGIN_ROOT}/hooks/scripts/<script>` 形
  (実行可能な実ファイルを指す) か `node` (script パスは `args[0]`) のどちらかである。
  install path に空白を含む環境でも hook が起動することを、git-guardrails の全 command
  hook を実際に spawn して確認する
- B. `.claude-plugin/marketplace.json` の top-level `renames` が、配布から取り下げた
  plugin 名を `null` に対応づける (利用者に削除通知が届く)
- C. gate 系 4 plugin の README の `## 既知の制約` 節が、PowerShell tool / Monitor tool
  経由で発行されたコマンドを gate が観測しないことを明記する
- D. リポジトリ直下 README の移行節が、廃止 plugin の削除通知が自動で出ることを
  必要な Claude Code 版数とともに案内する
- E. 16 plugin の version が 4 箇所 (plugin.json / marketplace.json / 直下 README の
  一覧テーブル / plugin README の `## バージョン` 節) で一致する

exec form が必要な理由: shell form (`args` 無し) では `command` 全体が shell で
tokenize されるため、plugin の install path に空白があると `${CLAUDE_PLUGIN_ROOT}` の
展開結果が単語分割されて hook の起動に失敗する。hook の起動失敗はほとんどの event で
non-blocking 扱いになり、gate が無音で外れる。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = ROOT / "plugins"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
REPO_README = ROOT / "README.md"

# hooks.json の `command` / `args` に書く plugin root placeholder。Claude Code は
# `command` と `args` の各要素に対してこの文字列を単純置換する。
PLUGIN_ROOT_PLACEHOLDER = "${CLAUDE_PLUGIN_ROOT}"
SCRIPTS_PLACEHOLDER_PREFIX = f"{PLUGIN_ROOT_PLACEHOLDER}/hooks/scripts/"
NODE_COMMAND = "node"

# exec form で許可する `command` の形 (script 直接起動)。
SCRIPT_COMMAND_PATTERN = re.compile(
    r"^\$\{CLAUDE_PLUGIN_ROOT\}/hooks/scripts/(?P<basename>[A-Za-z0-9._-]+)$"
)
# node 実行形で `args[0]` に置く script の形。
NODE_SCRIPT_ARG_PATTERN = re.compile(
    r"^\$\{CLAUDE_PLUGIN_ROOT\}/hooks/scripts/(?P<basename>[A-Za-z0-9._-]+\.mjs)$"
)


def script_path(basename: str) -> str:
    """hook script の placeholder パス (期待値の組み立てにのみ使う)。"""
    return f"{SCRIPTS_PLACEHOLDER_PREFIX}{basename}"


CODEX_RUNNERS_SCRIPT = script_path("manage-codex-runners.mjs")
REVIEW_CADENCE_SCRIPT = script_path("manage-review-cadence.mjs")

# agent-discipline と experimental-agent-discipline は同一の hooks 構造を持つ。
AGENT_DISCIPLINE_COMMAND_HOOKS = (
    ("PostModelSwitch", script_path("update-model-on-switch.sh"), ()),
    ("SessionStart", script_path("inject-always.sh"), ()),
    ("SessionStart", script_path("inject-temporary.sh"), ()),
    ("SubagentStart", script_path("inject-subagent-rules.sh"), ()),
    ("UserPromptSubmit", script_path("resolve-model-on-prompt.sh"), ()),
    ("UserPromptSubmit", script_path("inject-temporary.sh"), ()),
    ("UserPromptSubmit", script_path("inject-rules-part.sh"), ("2",)),
    ("UserPromptSubmit", script_path("inject-rules-part.sh"), ("3",)),
    ("UserPromptSubmit", script_path("inject-discipline.sh"), ()),
    ("UserPromptSubmit", script_path("inject-auto.sh"), ()),
    ("UserPromptSubmit", script_path("check-uncommitted-on-session-start.sh"), ()),
    ("PreToolUse", script_path("block-fable-subagent.sh"), ()),
)

# plugin ごとの command hook の多重集合 (event, command, args)。
# 起動形だけを exec form に揃えるため、event と実行される script の対応は固定する。
EXPECTED_COMMAND_HOOKS: dict[str, tuple[tuple[str, str, tuple[str, ...]], ...]] = {
    "agent-discipline": AGENT_DISCIPLINE_COMMAND_HOOKS,
    "experimental-agent-discipline": AGENT_DISCIPLINE_COMMAND_HOOKS,
    "auto-lint-check": (
        ("PreToolUse", script_path("block-ignore-lint-comment.sh"), ()),
        ("PreToolUse", script_path("block-commit-lint.sh"), ()),
        ("PostToolUse", script_path("code-format.sh"), ()),
        ("PostToolUse", script_path("post-commit-lint.sh"), ()),
        ("PostToolUseFailure", script_path("post-commit-lint.sh"), ()),
    ),
    "codex-advisor": (
        ("SessionStart", script_path("inject-advisor-rules.sh"), ()),
        ("SessionStart", NODE_COMMAND, (CODEX_RUNNERS_SCRIPT,)),
        ("SessionEnd", NODE_COMMAND, (CODEX_RUNNERS_SCRIPT,)),
        ("PreToolUse", NODE_COMMAND, (CODEX_RUNNERS_SCRIPT,)),
        ("PermissionDenied", NODE_COMMAND, (CODEX_RUNNERS_SCRIPT,)),
        ("SubagentStart", script_path("inject-advisor-rules-subagent.sh"), ()),
        ("SubagentStart", NODE_COMMAND, (CODEX_RUNNERS_SCRIPT,)),
        ("PostToolUse", NODE_COMMAND, (CODEX_RUNNERS_SCRIPT,)),
        ("SubagentStop", NODE_COMMAND, (CODEX_RUNNERS_SCRIPT,)),
        ("Stop", NODE_COMMAND, (CODEX_RUNNERS_SCRIPT,)),
    ),
    "enforce-draft-pr": (
        ("PreToolUse", script_path("enforce-draft-pr.sh"), ()),
    ),
    "git-guardrails": (
        ("PreToolUse", script_path("block-default-branch-commit.sh"), ()),
        ("PreToolUse", script_path("block-default-branch-push.sh"), ()),
        ("PreToolUse", script_path("block-default-branch-pr.sh"), ()),
    ),
    "natsuume-writing": (
        ("SessionStart", script_path("inject-core.sh"), ()),
    ),
    "pre-merge-codex-review": (
        ("SessionStart", script_path("inject-merge-order-rules.sh"), ()),
        ("PreToolUse", script_path("block-pre-merge.sh"), ()),
        ("PreToolUse", script_path("block-bg-codex-wrapper.sh"), ()),
        ("SubagentStart", script_path("auto-mark.sh"), ()),
        ("PostToolUse", script_path("auto-mark.sh"), ()),
        ("SubagentStop", script_path("auto-mark.sh"), ()),
        ("PostToolUseFailure", script_path("auto-mark.sh"), ()),
    ),
    "pre-push-codex-review": (
        ("SessionStart", script_path("inject-review-cadence-rules.sh"), ()),
        ("SessionEnd", NODE_COMMAND, (REVIEW_CADENCE_SCRIPT,)),
        ("PreToolUse", script_path("block-pre-push-codex.sh"), ()),
        ("PreToolUse", script_path("block-bg-codex-wrapper.sh"), ()),
        ("PreToolUse", NODE_COMMAND, (REVIEW_CADENCE_SCRIPT,)),
        ("SubagentStart", script_path("auto-mark.sh"), ()),
        ("SubagentStart", NODE_COMMAND, (REVIEW_CADENCE_SCRIPT,)),
        ("PostToolUse", script_path("auto-mark.sh"), ()),
        ("PostToolUse", NODE_COMMAND, (REVIEW_CADENCE_SCRIPT,)),
        ("PostToolUseFailure", script_path("auto-mark.sh"), ()),
        ("PostToolUseFailure", NODE_COMMAND, (REVIEW_CADENCE_SCRIPT,)),
        ("PermissionDenied", NODE_COMMAND, (REVIEW_CADENCE_SCRIPT,)),
        ("SubagentStop", script_path("auto-mark.sh"), ()),
        ("SubagentStop", NODE_COMMAND, (REVIEW_CADENCE_SCRIPT,)),
        ("Stop", NODE_COMMAND, (REVIEW_CADENCE_SCRIPT,)),
    ),
    "pre-push-review": (
        ("PreToolUse", script_path("block-pre-push.sh"), ()),
        ("SubagentStart", script_path("auto-mark.sh"), ()),
        ("PostToolUse", script_path("auto-mark.sh"), ()),
        ("SubagentStop", script_path("auto-mark.sh"), ()),
    ),
    "session-handoff": (
        ("PostToolUse", script_path("detect-context-threshold.sh"), ()),
        ("PostToolUseFailure", script_path("detect-context-threshold.sh"), ()),
        ("SessionStart", script_path("inject-pending-handoff.sh"), ()),
    ),
    "ui-discipline": (
        ("SessionStart", script_path("inject-ui-rules.sh"), ()),
        ("SubagentStart", script_path("inject-ui-rules-subagent.sh"), ()),
    ),
}

# command hook が持ってよいキー。exec form では `args` が必須で、command hook に
# `timeout` は付けない (timeout を持つのは agent entry だけ)。
COMMAND_HOOK_KEYS = {"type", "command", "args"}

# agent entry のキー集合と値。`args` は command hook 専用のため持たない。
AGENT_HOOK_KEYS = {"type", "if", "model", "prompt", "timeout"}
AGENT_HOOK_MODEL = "claude-sonnet-5"
AGENT_HOOK_TIMEOUT = 60
EXPECTED_AGENT_HOOKS: dict[str, tuple[tuple[str, str], ...]] = {
    # plugin -> (event, `if` 条件) の多重集合
    "agent-discipline": (
        ("PreToolUse", "Bash(gh issue create:*)"),
        ("PreToolUse", "Bash(gh issue edit:*)"),
        ("PreToolUse", "Bash(gh pr create:*)"),
        ("PreToolUse", "Bash(gh pr edit:*)"),
    ),
    "experimental-agent-discipline": (
        ("PreToolUse", "Bash(gh issue create:*)"),
        ("PreToolUse", "Bash(gh issue edit:*)"),
        ("PreToolUse", "Bash(gh pr create:*)"),
        ("PreToolUse", "Bash(gh pr edit:*)"),
    ),
}

# hooks.json top-level の `description` は起動形の変更で改変しない。
# 値は (文字数, UTF-8 本文の SHA-256)。差分が出た場合は git から元の本文を戻す。
EXPECTED_DESCRIPTION_DIGESTS: dict[str, tuple[int, str]] = {
    "agent-discipline": (
        1471,
        "216eb545309735a44d4358a80fabe5d11ce38115c0901c21b2a615ee3cc042a9",
    ),
    "auto-lint-check": (
        122,
        "d5f67fa6168994de85a34b28699d61a42a03c318c77668f839c9ac58d2fd37cb",
    ),
    "codex-advisor": (
        708,
        "17610e3281ce8c5ae52a6940a6f478991cd0bdca64fdd32be02b16890d395960",
    ),
    "experimental-agent-discipline": (
        1719,
        "c0a1150c9871c5d5f3c93d4a63b1bc57d3bbd043a85cc8e4b8aaaef806ff2047",
    ),
    "git-guardrails": (
        114,
        "8e55e12971027e180cddba7a861d505310ef4bf37c7753926cdfa232cac0d02a",
    ),
    "natsuume-writing": (
        194,
        "63c76f4494d06962cec7372e968340b865078f1e6c5e780543651fb169b0bbf2",
    ),
    "pre-merge-codex-review": (
        735,
        "380cba665caf3ad3af0b04186276cb02707333e4b357e45ff19a0cd2c98ff62d",
    ),
    "pre-push-codex-review": (
        1277,
        "20d547778da5f636c5d0e519fb64773be4a602cb2d35777f928a95c91a37c08b",
    ),
    "pre-push-review": (
        326,
        "a8f4f094d367b1b103dd24debba4609df7f9bc95a1f17074ce37d1beb2973f0f",
    ),
    "session-handoff": (
        220,
        "90626da44b2df5eeae7466555da039d3d02912b2ecd145ec8ad1297ce0e93d72",
    ),
    "ui-discipline": (
        442,
        "3a6785133e2803b6e7e33105ca0c835106a66fb06bee411dc3bf22586ab84419",
    ),
}

# marketplace.json の renames が削除通知の対象にする plugin 名。
REMOVED_PLUGIN_NAME = "fable-risk-labeler"

# gate 系 plugin の README が制約を書く節と、その節に必須のキーワード。
CONSTRAINT_HEADING = "## 既知の制約"
GATE_PLUGIN_NAMES = (
    "pre-push-review",
    "pre-push-codex-review",
    "pre-merge-codex-review",
    "codex-advisor",
)
UNOBSERVED_TOOL_KEYWORDS = (
    "CLAUDE_CODE_USE_POWERSHELL_TOOL=1",
    "PowerShell",
    "Monitor",
    "観測しない",
)
# 説明文書に書かない経緯記述の語。
HISTORY_NARRATIVE_WORDS = ("以前は", "かつては", "#398")

# 直下 README の移行案内の節と、そこに必須の語 (同一文に両方が現れること)。
MIGRATION_HEADING = "## 旧 Codex 配布からの移行"
AUTO_REMOVAL_NOTICE_WORDS = ("2.1.193", "自動")

# 4 箇所 (plugin.json / marketplace.json / 直下 README の一覧テーブル /
# plugin README の `## バージョン` 節) で一致させる version。
EXPECTED_PLUGIN_VERSIONS: dict[str, str] = {
    "git-guardrails": "0.7.1",
    "enforce-draft-pr": "0.5.5",
    "auto-lint-check": "0.8.1",
    "pre-push-review": "6.1.2",
    "pre-push-codex-review": "2.2.3",
    "pre-merge-codex-review": "2.2.1",
    "update-default-branch": "0.4.4",
    "natsuume-statusline": "0.10.6",
    "agent-discipline": "0.29.0",
    "experimental-agent-discipline": "0.5.0",
    "ui-discipline": "0.4.4",
    "natsuume-writing": "0.7.0",
    "codex-advisor": "4.0.1",
    "rate-limit": "0.5.2",
    "session-handoff": "0.5.0",
    "repo-analytics": "0.2.6",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(read_text(path))


def hooks_manifest_path(plugin: str) -> Path:
    return PLUGINS_DIR / plugin / "hooks" / "hooks.json"


def plugins_with_hooks() -> list[str]:
    """`hooks/hooks.json` を持つ plugin 名 (実ファイルから列挙する)。"""
    return sorted(path.parents[1].name for path in PLUGINS_DIR.glob("*/hooks/hooks.json"))


def iter_hooks(manifest: dict):
    """(event, group index, hook index, hook) を列挙する。"""
    for event, groups in manifest["hooks"].items():
        for group_index, group in enumerate(groups):
            for hook_index, hook in enumerate(group["hooks"]):
                yield event, group_index, hook_index, hook


def markdown_section(text: str, heading: str) -> str | None:
    """`heading` 行から次の `## ` 見出しの直前までの本文を返す。見出しが無ければ None。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != heading:
            continue
        body: list[str] = []
        for following in lines[index + 1 :]:
            if following.startswith("## "):
                break
            body.append(following)
        return "\n".join(body)
    return None


def substitute_plugin_root(value: str, plugin_root: str) -> str:
    """Claude Code と同じ規則で `${CLAUDE_PLUGIN_ROOT}` を単純置換する。"""
    return value.replace(PLUGIN_ROOT_PLACEHOLDER, plugin_root)


class HooksManifestInventoryTest(unittest.TestCase):
    """hook を持つ plugin の集合を固定する。"""

    def test_expected_table_covers_every_plugin_with_hooks(self) -> None:
        self.assertEqual(sorted(EXPECTED_COMMAND_HOOKS), plugins_with_hooks())


class CommandHookExecFormTest(unittest.TestCase):
    """全 plugin の command hook が exec form (`command` + `args`) である。"""

    def command_hooks(self, plugin: str):
        """(event, group index, hook index, hook) のうち command hook だけを返す。"""
        manifest = load_json(hooks_manifest_path(plugin))
        return [
            entry for entry in iter_hooks(manifest) if entry[3].get("type") == "command"
        ]

    def args_of(self, hook: dict, label: str) -> list:
        """exec form の `args` を取り出す (欠落は明示の失敗にする)。"""
        self.assertIn(
            "args",
            hook,
            f"{label}: exec form には `args` が必要 (引数が無い場合も `\"args\": []`)",
        )
        args = hook["args"]
        self.assertIsInstance(args, list, f"{label}: `args` は配列")
        for index, element in enumerate(args):
            self.assertIsInstance(
                element, str, f"{label}: args[{index}] は文字列"
            )
        return args

    def test_every_command_hook_declares_args(self) -> None:
        """`args` キーの存在が exec form と shell form を分ける。"""
        for plugin in sorted(EXPECTED_COMMAND_HOOKS):
            for event, group_index, hook_index, hook in self.command_hooks(plugin):
                label = f"{plugin} {event}[{group_index}][{hook_index}]"
                with self.subTest(plugin=plugin, event=event, hook=label):
                    self.args_of(hook, label)

    def test_command_has_no_shell_syntax(self) -> None:
        """`command` は shell を介さず spawn されるため、shell 構文を含めない。"""
        for plugin in sorted(EXPECTED_COMMAND_HOOKS):
            for event, group_index, hook_index, hook in self.command_hooks(plugin):
                label = f"{plugin} {event}[{group_index}][{hook_index}]"
                command = hook["command"]
                with self.subTest(plugin=plugin, event=event, hook=label):
                    self.assertIsNone(
                        re.search(r"\s", command),
                        f"{label}: `command` に空白を含めない (引数は `args` に分ける): {command!r}",
                    )
                    for forbidden in ('"', "'", "$(", "`"):
                        self.assertNotIn(
                            forbidden,
                            command,
                            f"{label}: `command` に {forbidden!r} を含めない: {command!r}",
                        )

    def test_command_is_a_script_path_or_node(self) -> None:
        """`command` は script の placeholder パスか `node` のどちらかである。"""
        for plugin in sorted(EXPECTED_COMMAND_HOOKS):
            for event, group_index, hook_index, hook in self.command_hooks(plugin):
                label = f"{plugin} {event}[{group_index}][{hook_index}]"
                command = hook["command"]
                with self.subTest(plugin=plugin, event=event, hook=label):
                    self.assertTrue(
                        command == NODE_COMMAND
                        or SCRIPT_COMMAND_PATTERN.fullmatch(command),
                        f"{label}: `command` は "
                        f"'{SCRIPTS_PLACEHOLDER_PREFIX}<script>' か '{NODE_COMMAND}': {command!r}",
                    )

    def test_script_commands_point_at_an_executable_file(self) -> None:
        """script 直接起動形の `command` は実行ビットの立った実ファイルを指す。"""
        for plugin in sorted(EXPECTED_COMMAND_HOOKS):
            for event, group_index, hook_index, hook in self.command_hooks(plugin):
                label = f"{plugin} {event}[{group_index}][{hook_index}]"
                matched = SCRIPT_COMMAND_PATTERN.fullmatch(hook["command"])
                if matched is None:
                    continue
                script = (
                    PLUGINS_DIR
                    / plugin
                    / "hooks"
                    / "scripts"
                    / matched.group("basename")
                )
                with self.subTest(plugin=plugin, event=event, hook=label):
                    self.assertTrue(script.is_file(), f"{label}: {script} が無い")
                    self.assertTrue(
                        os.access(script, os.X_OK),
                        f"{label}: {script} に実行ビットが無い",
                    )

    def test_node_commands_put_the_script_in_args(self) -> None:
        """`node` 起動形は `args[0]` に .mjs の placeholder パスだけを置く。"""
        for plugin in sorted(EXPECTED_COMMAND_HOOKS):
            for event, group_index, hook_index, hook in self.command_hooks(plugin):
                label = f"{plugin} {event}[{group_index}][{hook_index}]"
                if hook["command"] != NODE_COMMAND:
                    continue
                with self.subTest(plugin=plugin, event=event, hook=label):
                    args = self.args_of(hook, label)
                    self.assertEqual(
                        1,
                        len(args),
                        f"{label}: `node` 起動形の `args` は script パス 1 要素のみ: {args!r}",
                    )
                    matched = NODE_SCRIPT_ARG_PATTERN.fullmatch(args[0])
                    self.assertIsNotNone(
                        matched,
                        f"{label}: args[0] は "
                        f"'{SCRIPTS_PLACEHOLDER_PREFIX}<script>.mjs' の形: {args[0]!r}",
                    )
                    assert matched is not None
                    script = (
                        PLUGINS_DIR
                        / plugin
                        / "hooks"
                        / "scripts"
                        / matched.group("basename")
                    )
                    self.assertTrue(script.is_file(), f"{label}: {script} が無い")

    def test_args_expand_only_the_plugin_root_placeholder(self) -> None:
        """`args` の要素は shell 展開されないため、plugin root 以外の展開・引用を書かない。"""
        for plugin in sorted(EXPECTED_COMMAND_HOOKS):
            for event, group_index, hook_index, hook in self.command_hooks(plugin):
                label = f"{plugin} {event}[{group_index}][{hook_index}]"
                with self.subTest(plugin=plugin, event=event, hook=label):
                    for index, element in enumerate(self.args_of(hook, label)):
                        residue = element.replace(PLUGIN_ROOT_PLACEHOLDER, "")
                        for forbidden in ("$", '"', "'", "`"):
                            self.assertNotIn(
                                forbidden,
                                residue,
                                f"{label}: args[{index}] に {forbidden!r} を含めない: "
                                f"{element!r}",
                            )

    def test_command_hooks_have_no_extra_fields(self) -> None:
        """command hook のキーは `type` / `command` / `args` だけである。"""
        for plugin in sorted(EXPECTED_COMMAND_HOOKS):
            for event, group_index, hook_index, hook in self.command_hooks(plugin):
                label = f"{plugin} {event}[{group_index}][{hook_index}]"
                with self.subTest(plugin=plugin, event=event, hook=label):
                    self.assertEqual(COMMAND_HOOK_KEYS, set(hook), label)

    def test_command_hook_table_matches_the_expected_entries(self) -> None:
        """plugin ごとの (event, command, args) の多重集合が期待表と一致する。"""
        for plugin, expected in sorted(EXPECTED_COMMAND_HOOKS.items()):
            with self.subTest(plugin=plugin):
                actual = [
                    (event, hook["command"], tuple(hook.get("args", ())))
                    for event, _group, _index, hook in self.command_hooks(plugin)
                ]
                self.assertEqual(sorted(expected), sorted(actual))


class AgentHookEntryTest(unittest.TestCase):
    """`type: "agent"` entry は exec form の対象外で、構造をそのまま保つ。"""

    def agent_hooks(self, plugin: str):
        manifest = load_json(hooks_manifest_path(plugin))
        return [
            entry for entry in iter_hooks(manifest) if entry[3].get("type") == "agent"
        ]

    def test_agent_entries_match_the_expected_conditions(self) -> None:
        for plugin, expected in sorted(EXPECTED_AGENT_HOOKS.items()):
            with self.subTest(plugin=plugin):
                actual = [
                    (event, hook["if"])
                    for event, _group, _index, hook in self.agent_hooks(plugin)
                ]
                self.assertEqual(sorted(expected), sorted(actual))

    def test_agent_entries_keep_their_structure(self) -> None:
        """agent entry は `args` を持たず、model / timeout / prompt を保つ。"""
        for plugin in sorted(EXPECTED_AGENT_HOOKS):
            for event, group_index, hook_index, hook in self.agent_hooks(plugin):
                label = f"{plugin} {event}[{group_index}][{hook_index}]"
                with self.subTest(plugin=plugin, event=event, hook=label):
                    self.assertEqual(AGENT_HOOK_KEYS, set(hook), label)
                    self.assertNotIn(
                        "args", hook, f"{label}: agent entry に `args` を付けない"
                    )
                    self.assertEqual(AGENT_HOOK_MODEL, hook["model"], label)
                    self.assertEqual(AGENT_HOOK_TIMEOUT, hook["timeout"], label)
                    self.assertIsInstance(hook["prompt"], str, label)
                    self.assertTrue(hook["prompt"].strip(), f"{label}: prompt が空")

    def test_no_other_plugin_registers_an_agent_entry(self) -> None:
        for plugin in plugins_with_hooks():
            if plugin in EXPECTED_AGENT_HOOKS:
                continue
            with self.subTest(plugin=plugin):
                self.assertEqual([], self.agent_hooks(plugin))


class HooksManifestDescriptionTest(unittest.TestCase):
    """hooks.json top-level の `description` を保つ。"""

    def test_description_presence_matches_the_expected_set(self) -> None:
        with_description = sorted(
            plugin
            for plugin in plugins_with_hooks()
            if "description" in load_json(hooks_manifest_path(plugin))
        )
        self.assertEqual(sorted(EXPECTED_DESCRIPTION_DIGESTS), with_description)

    def test_description_text_is_unchanged(self) -> None:
        for plugin, (length, digest) in sorted(EXPECTED_DESCRIPTION_DIGESTS.items()):
            with self.subTest(plugin=plugin):
                description = load_json(hooks_manifest_path(plugin))["description"]
                self.assertIsInstance(description, str)
                self.assertEqual(
                    (
                        len(description),
                        hashlib.sha256(description.encode("utf-8")).hexdigest(),
                    ),
                    (length, digest),
                    f"{plugin}: hooks.json の description は起動形の変更で改変しない",
                )


class SpacedInstallPathLaunchTest(unittest.TestCase):
    """空白を含む install path でも command hook が起動する (代表: git-guardrails)。"""

    PLUGIN = "git-guardrails"
    # hook が「対象外」と判定して exit 0 / 出力無しで抜ける最小 payload。
    IRRELEVANT_PAYLOAD = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "printf hello"},
    }

    def test_hooks_launch_from_a_path_containing_a_space(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            plugin_root = temp / "plugin root with space" / self.PLUGIN
            shutil.copytree(PLUGINS_DIR / self.PLUGIN, plugin_root)
            home = temp / "home"
            home.mkdir()

            manifest = load_json(plugin_root / "hooks" / "hooks.json")
            environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(home),
                "TMPDIR": str(temp / "state"),
                "CLAUDE_PLUGIN_ROOT": str(plugin_root),
            }
            Path(environment["TMPDIR"]).mkdir()

            launched = 0
            for event, group_index, hook_index, hook in iter_hooks(manifest):
                if hook.get("type") != "command":
                    continue
                label = f"{self.PLUGIN} {event}[{group_index}][{hook_index}]"
                with self.subTest(event=event, hook=label):
                    self.assertIn(
                        "args",
                        hook,
                        f"{label}: exec form には `args` が必要 "
                        '(引数が無い場合も `"args": []`)',
                    )
                    argv = [
                        substitute_plugin_root(hook["command"], str(plugin_root)),
                        *(
                            substitute_plugin_root(element, str(plugin_root))
                            for element in hook["args"]
                        ),
                    ]
                    result = subprocess.run(
                        argv,
                        shell=False,
                        input=json.dumps(self.IRRELEVANT_PAYLOAD),
                        capture_output=True,
                        text=True,
                        cwd=str(temp),
                        env=environment,
                        timeout=60,
                    )
                    self.assertEqual(
                        0,
                        result.returncode,
                        f"{label}: 起動に失敗した (argv={argv!r}) stderr={result.stderr}",
                    )
                    for marker in ("No such file", "not found"):
                        self.assertNotIn(
                            marker,
                            result.stderr,
                            f"{label}: stderr に起動失敗の徴候: {result.stderr}",
                        )
                    launched += 1
            self.assertEqual(
                len(EXPECTED_COMMAND_HOOKS[self.PLUGIN]),
                launched,
                "git-guardrails の全 command hook を起動する",
            )


class MarketplaceRenamesTest(unittest.TestCase):
    """配布から取り下げた plugin を `renames` で削除通知に載せる。"""

    def setUp(self) -> None:
        self.marketplace = load_json(MARKETPLACE_JSON)

    def test_renames_maps_the_removed_plugin_to_null(self) -> None:
        self.assertIn(
            "renames",
            self.marketplace,
            "marketplace.json の top-level に `renames` を置く",
        )
        renames = self.marketplace["renames"]
        self.assertIsInstance(renames, dict)
        self.assertIn(REMOVED_PLUGIN_NAME, renames)
        self.assertIsNone(
            renames[REMOVED_PLUGIN_NAME],
            f"{REMOVED_PLUGIN_NAME} は削除済みのため値は null",
        )

    def test_rename_values_are_null_or_a_new_name(self) -> None:
        """`renames` は追記のみで増える (値は削除なら null、改名なら新しい名前)。"""
        renames = self.marketplace.get("renames")
        self.assertIsInstance(renames, dict, "marketplace.json に `renames` が無い")
        for old_name, new_name in sorted(renames.items()):
            with self.subTest(plugin=old_name):
                self.assertIsInstance(old_name, str)
                self.assertTrue(
                    new_name is None or isinstance(new_name, str),
                    f"{old_name}: 値は null か新しい plugin 名: {new_name!r}",
                )

    def test_removed_plugin_is_absent_from_the_distribution(self) -> None:
        names = [entry["name"] for entry in self.marketplace["plugins"]]
        self.assertNotIn(REMOVED_PLUGIN_NAME, names)
        self.assertFalse((PLUGINS_DIR / REMOVED_PLUGIN_NAME).exists())


class GateConstraintDocumentationTest(unittest.TestCase):
    """gate 系 plugin の README が観測範囲外の実行経路を明記する。"""

    def constraint_section(self, plugin: str) -> str:
        readme = PLUGINS_DIR / plugin / "README.md"
        section = markdown_section(read_text(readme), CONSTRAINT_HEADING)
        if section is None:
            self.fail(f"{readme}: '{CONSTRAINT_HEADING}' 節が無い")
        return section

    def test_every_gate_plugin_has_a_constraint_section(self) -> None:
        for plugin in GATE_PLUGIN_NAMES:
            with self.subTest(plugin=plugin):
                self.assertTrue(self.constraint_section(plugin).strip())

    def test_constraint_section_states_the_unobserved_tool_paths(self) -> None:
        """PowerShell tool / Monitor tool 経由のコマンドを観測しないことを書く。"""
        for plugin in GATE_PLUGIN_NAMES:
            with self.subTest(plugin=plugin):
                section = self.constraint_section(plugin)
                for keyword in UNOBSERVED_TOOL_KEYWORDS:
                    self.assertTrue(
                        keyword in section,
                        f"{plugin}: '{CONSTRAINT_HEADING}' 節に {keyword!r} が無い",
                    )

    def test_constraint_section_has_no_history_narrative(self) -> None:
        for plugin in GATE_PLUGIN_NAMES:
            with self.subTest(plugin=plugin):
                section = self.constraint_section(plugin)
                for word in HISTORY_NARRATIVE_WORDS:
                    self.assertFalse(
                        word in section,
                        f"{plugin}: '{CONSTRAINT_HEADING}' 節に経緯記述 {word!r} を書かない",
                    )


class RepositoryReadmeMigrationTest(unittest.TestCase):
    """直下 README の移行案内が、削除通知が自動で出ることを版数付きで書く。"""

    def migration_section(self) -> str:
        section = markdown_section(read_text(REPO_README), MIGRATION_HEADING)
        if section is None:
            self.fail(f"{REPO_README}: '{MIGRATION_HEADING}' 節が無い")
        return section

    def test_section_states_the_automatic_removal_notice(self) -> None:
        section = self.migration_section()
        sentences = [part for part in re.split(r"[。\n]", section) if part.strip()]
        self.assertTrue(
            any(
                all(word in sentence for word in AUTO_REMOVAL_NOTICE_WORDS)
                for sentence in sentences
            ),
            f"{MIGRATION_HEADING} 節に {AUTO_REMOVAL_NOTICE_WORDS} を同じ文で含める",
        )


class PluginVersionSyncTest(unittest.TestCase):
    """16 plugin の version が 4 箇所で一致する。"""

    def test_expected_versions_cover_every_distributed_plugin(self) -> None:
        marketplace_names = sorted(
            entry["name"] for entry in load_json(MARKETPLACE_JSON)["plugins"]
        )
        directory_names = sorted(
            path.name for path in PLUGINS_DIR.iterdir() if path.is_dir()
        )
        self.assertEqual(sorted(EXPECTED_PLUGIN_VERSIONS), marketplace_names)
        self.assertEqual(sorted(EXPECTED_PLUGIN_VERSIONS), directory_names)

    def test_plugin_manifest_declares_the_version(self) -> None:
        for plugin, version in sorted(EXPECTED_PLUGIN_VERSIONS.items()):
            with self.subTest(plugin=plugin):
                manifest = load_json(
                    PLUGINS_DIR / plugin / ".claude-plugin" / "plugin.json"
                )
                self.assertEqual(version, manifest["version"])

    def test_marketplace_entry_declares_the_version(self) -> None:
        entries = {
            entry["name"]: entry for entry in load_json(MARKETPLACE_JSON)["plugins"]
        }
        for plugin, version in sorted(EXPECTED_PLUGIN_VERSIONS.items()):
            with self.subTest(plugin=plugin):
                self.assertIn(plugin, entries)
                self.assertEqual(version, entries[plugin]["version"])

    def test_repository_readme_table_lists_the_version(self) -> None:
        readme = read_text(REPO_README)
        for plugin, version in sorted(EXPECTED_PLUGIN_VERSIONS.items()):
            with self.subTest(plugin=plugin):
                self.assertIn(f"[{plugin}](#{plugin}) | {version} |", readme)

    def test_plugin_readme_version_heading_declares_the_version(self) -> None:
        for plugin, version in sorted(EXPECTED_PLUGIN_VERSIONS.items()):
            with self.subTest(plugin=plugin):
                lines = read_text(PLUGINS_DIR / plugin / "README.md").splitlines()
                self.assertIn("## バージョン", lines)
                index = lines.index("## バージョン")
                following = [line.strip() for line in lines[index + 1 :] if line.strip()]
                self.assertTrue(following, f"{plugin}: '## バージョン' の後に本文が無い")
                self.assertEqual(f"v{version}", following[0])


if __name__ == "__main__":
    unittest.main()

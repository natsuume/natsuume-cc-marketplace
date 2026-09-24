"""pre-push-review: code-reviewer / security-reviewer を常に Opus で起動する契約テスト。

背景 (spec-first Phase A):
- pre-push-review の 2 reviewer は、Fable 週次枠の使用率に依らず常に `model: "opus"` を
  明示して起動する。agent 定義 frontmatter の model も opus とし、明示・未指定のどちらの
  経路でも同じ model で走る。
- Fable 週次枠の使用率判定 (判定コマンド `bin/pre-push-review-reviewer-model` と共通 lib
  `hooks/scripts/lib/fable-weekly-usage.sh`) は持たない。deny 文と `/pre-push-review:review`
  は判定を経ずに `opus` を案内する。
- Fable 週次枠に余裕がある使用率 cache を置いても、deny 文は `model="fable"` を案内しない。

hook を実行するテストは ``HOME`` / ``XDG_CACHE_HOME`` を一時ディレクトリへ向け、
親プロセスの env を継承しない。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-push-review"
BLOCK_PRE_PUSH = PLUGIN / "hooks" / "scripts" / "block-pre-push.sh"
REVIEW_COMMAND = PLUGIN / "commands" / "review.md"
REMOVED_FILES = (
    PLUGIN / "bin" / "pre-push-review-reviewer-model",
    PLUGIN / "hooks" / "scripts" / "lib" / "fable-weekly-usage.sh",
)
AGENTS = {
    "code-reviewer": PLUGIN / "agents" / "code-reviewer.md",
    "security-reviewer": PLUGIN / "agents" / "security-reviewer.md",
}
REVIEWERS = ("code-reviewer", "security-reviewer")

CACHE_RELATIVE = Path("natsuume-statusline") / "weekly-scoped.json"


def fable_available_cache() -> dict[str, object]:
    """Fable 週次枠に余裕がある (10%) 新鮮な weekly-scoped.json。"""
    return {
        "consecutive_failures": 0,
        "next_attempt_at": 0,
        "fetched_at": int(time.time()),
        "weekly_scoped": [
            {"display_name": "Fable", "percent": 10, "resets_at": "2026-09-28T00:00:00Z"}
        ],
    }


class FableSelectionRemovedTest(unittest.TestCase):
    """Fable 週次枠の判定処理と Fable への言及が plugin に残っていない。"""

    def test_fable_selection_files_are_removed(self) -> None:
        remaining = [str(path.relative_to(ROOT)) for path in REMOVED_FILES if path.exists()]
        self.assertEqual([], remaining, f"削除されていない判定処理: {remaining}")

    def test_plugin_does_not_mention_fable(self) -> None:
        offenders = [
            str(path.relative_to(ROOT))
            for path in sorted(PLUGIN.rglob("*"))
            if path.is_file()
            and re.search(r"fable", path.read_text(encoding="utf-8", errors="replace"), re.I)
        ]
        self.assertEqual([], offenders, f"Fable への言及が残るファイル: {offenders}")

    def test_other_plugins_do_not_reference_reviewer_fable_usage(self) -> None:
        # 他 plugin が pre-push-review を Fable の利用者・判定仕様の共有先として参照しない
        offenders = [
            f"{path.relative_to(ROOT)}:{number}"
            for path in sorted((ROOT / "plugins").rglob("*"))
            if path.is_file() and PLUGIN not in path.parents
            for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            )
            if "pre-push-review" in line and re.search(r"fable|判定仕様", line, re.I)
        ]
        self.assertEqual([], offenders, f"pre-push-review の Fable 利用を参照する行: {offenders}")


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockPrePushReviewerModelTest(unittest.TestCase):
    """block-pre-push.sh の deny 文が 2 reviewer の起動を model="opus" で案内する。"""

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def create_repo(self, temporary: Path) -> Path:
        origin = temporary / "origin.git"
        repo = temporary / "repo"
        self.git(temporary, "init", "--bare", str(origin))
        self.git(temporary, "init", str(repo))
        self.git(repo, "config", "user.name", "Marketplace Test")
        self.git(repo, "config", "user.email", "marketplace@example.invalid")
        (repo / "example.txt").write_text("base\n", encoding="utf-8")
        self.git(repo, "add", "example.txt")
        self.git(repo, "commit", "-m", "base")
        self.git(repo, "branch", "-M", "master")
        self.git(repo, "remote", "add", "origin", str(origin))
        self.git(repo, "push", "-u", "origin", "master")
        self.git(repo, "remote", "set-head", "origin", "master")
        self.git(repo, "switch", "-c", "feature")
        (repo / "example.txt").write_text("change\n", encoding="utf-8")
        self.git(repo, "add", "example.txt")
        self.git(repo, "commit", "-m", "change")
        return repo

    def deny_reason(self) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repo = self.create_repo(temp)
            home = temp / "home"
            xdg_cache = temp / "xdg-cache"
            home.mkdir()
            cache_path = xdg_cache / CACHE_RELATIVE
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text(json.dumps(fable_available_cache()), encoding="utf-8")
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(home),
                "XDG_CACHE_HOME": str(xdg_cache),
                "TMPDIR": str(temp),
            }
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin HEAD"},
            }
            result = subprocess.run(
                ["bash", str(BLOCK_PRE_PUSH)],
                cwd=repo,
                env=env,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            output = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual("deny", output["permissionDecision"])
            return str(output["permissionDecisionReason"])

    def test_deny_guides_opus_even_when_fable_usage_is_available(self) -> None:
        reason = self.deny_reason()
        missing = [
            name
            for name in REVIEWERS
            if f'subagent_type="pre-push-review:{name}", model="opus"' not in reason
        ]
        self.assertEqual([], missing, reason)
        self.assertNotRegex(reason, re.compile(r"fable", re.I))


class ReviewCommandContractTest(unittest.TestCase):
    """`/pre-push-review:review` が判定コマンドを経ずに 2 reviewer を opus で起動する。"""

    def test_review_command_launches_reviewers_on_opus(self) -> None:
        body = REVIEW_COMMAND.read_text(encoding="utf-8")
        missing = [
            name
            for name in REVIEWERS
            if f'`subagent_type: "pre-push-review:{name}"`、`model: "opus"`' not in body
        ]
        self.assertEqual([], missing, f"model: opus の起動仕様が無い reviewer: {missing}")

    def test_review_command_has_no_model_placeholder_or_selection_command(self) -> None:
        body = REVIEW_COMMAND.read_text(encoding="utf-8")
        self.assertNotIn("{{REVIEWER_MODEL}}", body)
        self.assertNotIn("pre-push-review-reviewer-model", body)


class AgentFrontmatterModelTest(unittest.TestCase):
    """reviewer 2 体の agent 定義 frontmatter の model は opus。"""

    def test_frontmatter_model_is_opus(self) -> None:
        wrong = [
            name
            for name, path in AGENTS.items()
            if not re.search(r"(?m)^model: opus$", path.read_text(encoding="utf-8").split("---")[1])
        ]
        self.assertEqual([], wrong, f"frontmatter の model が opus でない: {wrong}")


if __name__ == "__main__":
    unittest.main()

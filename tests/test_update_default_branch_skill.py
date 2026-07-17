"""update-default-branch の remote HEAD 更新契約テスト (issue #164)。

remote の default branch が変更されても local の origin/HEAD は自動更新されない。
Skill が remote-tracking refs を取得し、origin/HEAD を再検出してから名前を読む順序を
Phase A の red test として固定する。
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "update-default-branch"
SKILL = PLUGIN / "skills" / "update-default-branch" / "SKILL.md"
README = PLUGIN / "README.md"


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class UpdateDefaultBranchSkillTest(unittest.TestCase):
    def test_skill_refreshes_remote_head_before_reading_it(self) -> None:
        body = SKILL.read_text(encoding="utf-8")
        step = body.split("### 3. デフォルトブランチ名の取得", 1)[1].split(
            "### 4.", 1
        )[0]

        fetch_index = step.index("git fetch --prune origin")
        refresh_index = step.index("git remote set-head origin --auto")
        read_index = step.index("git symbolic-ref refs/remotes/origin/HEAD")

        self.assertLess(fetch_index, refresh_index)
        self.assertLess(refresh_index, read_index)
        self.assertEqual(1, body.count("git fetch --prune origin"))
        self.assertIn("失敗した場合は中断", step)

    def test_readme_describes_unconditional_remote_head_refresh(self) -> None:
        body = README.read_text(encoding="utf-8")
        self.assertIn(
            "`git fetch --prune origin` → `git remote set-head origin --auto` "
            "→ `git symbolic-ref refs/remotes/origin/HEAD`",
            body,
        )
        self.assertNotIn("失敗時は `git remote set-head origin --auto`", body)

    def test_refresh_sequence_updates_a_stale_origin_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            seed = root / "seed"
            checkout = root / "checkout"

            run_git(root, "init", "--bare", str(remote))
            run_git(root, "init", str(seed))
            run_git(seed, "symbolic-ref", "HEAD", "refs/heads/master")
            run_git(seed, "config", "user.name", "Test User")
            run_git(seed, "config", "user.email", "test@example.com")
            (seed / "README.md").write_text("fixture\n", encoding="utf-8")
            run_git(seed, "add", "README.md")
            run_git(seed, "commit", "-m", "initial")
            run_git(seed, "remote", "add", "origin", str(remote))
            run_git(seed, "push", "origin", "master")
            run_git(remote, "symbolic-ref", "HEAD", "refs/heads/master")
            run_git(root, "clone", str(remote), str(checkout))

            self.assertEqual(
                "refs/remotes/origin/master",
                run_git(checkout, "symbolic-ref", "refs/remotes/origin/HEAD"),
            )

            run_git(seed, "branch", "main")
            run_git(seed, "push", "origin", "main")
            run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

            # The old default still exists, so the stale local symbolic ref remains valid
            # and would silently return master without the explicit refresh sequence.
            self.assertEqual(
                "refs/remotes/origin/master",
                run_git(checkout, "symbolic-ref", "refs/remotes/origin/HEAD"),
            )

            run_git(checkout, "fetch", "--prune", "origin")
            run_git(checkout, "remote", "set-head", "origin", "--auto")

            self.assertEqual(
                "refs/remotes/origin/main",
                run_git(checkout, "symbolic-ref", "refs/remotes/origin/HEAD"),
            )


if __name__ == "__main__":
    unittest.main()

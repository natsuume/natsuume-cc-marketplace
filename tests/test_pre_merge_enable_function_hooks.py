"""pre-merge-cross-review の hooks module 有効化コマンドの契約テスト。

`bin/pre-merge-cross-review-enable-function-hooks` は user settings の `env` に
`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS: "1"` を書き込む。

- 出力は常に 2 行 (1 行目 = created / updated / already-set / aborted、2 行目 = 説明)
- exit code は aborted のみ 1、それ以外は 0
- 既存の他のキー・env の他のキーを保持する
- aborted・already-set ではファイルを変更しない
- settings.json の場所は `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json`
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND = (
    ROOT
    / "plugins"
    / "pre-merge-cross-review"
    / "bin"
    / "pre-merge-cross-review-enable-function-hooks"
)
ENV_KEY = "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"

# jq を除いた PATH を作るときに symlink で用意するコマンド。
BASE_TOOLS = [
    "bash",
    "cat",
    "mkdir",
    "dirname",
    "mktemp",
    "rm",
    "mv",
    "cp",
    "chmod",
    "ls",
    "tr",
]


@unittest.skipUnless(shutil.which("jq"), "有効化コマンドの検証には jq が必要")
class EnableFunctionHooksCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.settings = self.home / ".claude" / "settings.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_command(
        self, *, extra_env: dict[str, str] | None = None, path: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "HOME": str(self.home),
            "PATH": path if path is not None else os.environ.get("PATH", ""),
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(COMMAND)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def write_settings(self, text: str) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(text, encoding="utf-8")

    def assert_result(
        self, completed: subprocess.CompletedProcess[str], status: str
    ) -> None:
        lines = completed.stdout.splitlines()
        self.assertEqual(2, len(lines), completed.stdout)
        self.assertEqual(status, lines[0])
        self.assertTrue(lines[1].strip(), "2 行目の説明が空")
        self.assertEqual(
            1 if status == "aborted" else 0, completed.returncode, completed.stderr
        )

    def load_settings(self) -> object:
        return json.loads(self.settings.read_text(encoding="utf-8"))

    # --- 書き込む場合 -------------------------------------------------------

    def test_creates_settings_when_missing(self) -> None:
        completed = self.run_command()
        self.assert_result(completed, "created")
        self.assertEqual({"env": {ENV_KEY: "1"}}, self.load_settings())

    def test_adds_env_and_keeps_other_keys(self) -> None:
        self.write_settings(
            json.dumps({"language": "日本語", "permissions": {"defaultMode": "auto"}})
        )
        completed = self.run_command()
        self.assert_result(completed, "updated")
        self.assertEqual(
            {
                "language": "日本語",
                "permissions": {"defaultMode": "auto"},
                "env": {ENV_KEY: "1"},
            },
            self.load_settings(),
        )

    def test_keeps_other_env_keys(self) -> None:
        self.write_settings(json.dumps({"env": {"BASH_DEFAULT_TIMEOUT_MS": "1800000"}}))
        completed = self.run_command()
        self.assert_result(completed, "updated")
        self.assertEqual(
            {"env": {"BASH_DEFAULT_TIMEOUT_MS": "1800000", ENV_KEY: "1"}},
            self.load_settings(),
        )

    def test_overwrites_other_values(self) -> None:
        for value in ["0", "", "true", 1]:
            with self.subTest(value=value):
                self.write_settings(json.dumps({"env": {ENV_KEY: value}}))
                completed = self.run_command()
                self.assert_result(completed, "updated")
                self.assertEqual({"env": {ENV_KEY: "1"}}, self.load_settings())

    def test_respects_claude_config_dir(self) -> None:
        config_dir = Path(self._tmp.name) / "config"
        completed = self.run_command(extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)})
        self.assert_result(completed, "created")
        self.assertEqual(
            {"env": {ENV_KEY: "1"}},
            json.loads((config_dir / "settings.json").read_text(encoding="utf-8")),
        )
        self.assertFalse(self.settings.exists())

    def test_symlinked_settings_stays_a_symlink(self) -> None:
        target = Path(self._tmp.name) / "dotfiles" / "settings.json"
        target.parent.mkdir()
        target.write_text(json.dumps({"language": "日本語"}), encoding="utf-8")
        self.settings.parent.mkdir(parents=True)
        self.settings.symlink_to(target)
        completed = self.run_command()
        self.assert_result(completed, "updated")
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(
            {"language": "日本語", "env": {ENV_KEY: "1"}},
            json.loads(target.read_text(encoding="utf-8")),
        )

    # --- 書き込まない場合 ---------------------------------------------------

    def assert_file_unchanged(self, before: str) -> None:
        self.assertEqual(before, self.settings.read_text(encoding="utf-8"))

    def test_already_set_does_not_write(self) -> None:
        original = '{\n  "env": { "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1" }\n}\n'
        self.write_settings(original)
        completed = self.run_command()
        self.assert_result(completed, "already-set")
        self.assert_file_unchanged(original)

    def test_aborts_on_invalid_json(self) -> None:
        original = '{"env": {'
        self.write_settings(original)
        completed = self.run_command()
        self.assert_result(completed, "aborted")
        self.assert_file_unchanged(original)

    def test_aborts_when_top_level_is_not_object(self) -> None:
        for original in ["[]", '"text"', "null", "1"]:
            with self.subTest(original=original):
                self.write_settings(original)
                completed = self.run_command()
                self.assert_result(completed, "aborted")
                self.assert_file_unchanged(original)

    def test_aborts_when_env_is_not_object(self) -> None:
        for env_value in ['"x"', "[]", "null", "1"]:
            original = '{"env": ' + env_value + "}"
            with self.subTest(original=original):
                self.write_settings(original)
                completed = self.run_command()
                self.assert_result(completed, "aborted")
                self.assert_file_unchanged(original)

    def test_aborts_on_dangling_symlink(self) -> None:
        target = Path(self._tmp.name) / "dotfiles" / "settings.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.symlink_to(target)
        completed = self.run_command()
        self.assert_result(completed, "aborted")
        self.assertIn("symlink", completed.stdout.splitlines()[1])
        self.assertTrue(self.settings.is_symlink())
        self.assertFalse(target.exists())

    def test_aborts_on_cyclic_symlink(self) -> None:
        other = self.settings.parent / "other.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.symlink_to(other)
        other.symlink_to(self.settings)
        completed = self.run_command()
        self.assert_result(completed, "aborted")
        self.assertIn("symlink", completed.stdout.splitlines()[1])
        self.assertTrue(self.settings.is_symlink())

    def test_aborts_without_jq(self) -> None:
        bin_dir = Path(self._tmp.name) / "bin-without-jq"
        bin_dir.mkdir()
        for tool in BASE_TOOLS:
            found = shutil.which(tool)
            if found:
                (bin_dir / tool).symlink_to(found)
        original = json.dumps({"language": "日本語"})
        self.write_settings(original)
        completed = self.run_command(path=str(bin_dir))
        self.assert_result(completed, "aborted")
        self.assert_file_unchanged(original)


if __name__ == "__main__":
    unittest.main()

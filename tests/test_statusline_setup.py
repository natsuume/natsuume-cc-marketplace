from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = ROOT / "plugins" / "natsuume-statusline" / "scripts" / "setup.sh"
ENTRYPOINT = ROOT / "plugins" / "natsuume-statusline" / "statusline" / "entrypoint.sh"

COMMAND_PATTERN = re.compile(r"^bash '(.*)'$")


class StatuslineSetupMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="statusline-setup-test-"))
        self.addCleanup(self._rmtree, tmp_dir)

        home = tmp_dir / "home"
        settings_dir = home / ".claude"
        settings_dir.mkdir(parents=True)

        self.settings_path = settings_dir / "settings.json"
        self.env = os.environ.copy()
        self.env["HOME"] = str(home)
        self.env["CLAUDE_CONFIG_DIR"] = str(settings_dir)

    @staticmethod
    def _rmtree(path: Path) -> None:
        subprocess.run(["rm", "-rf", str(path)], check=False)

    def write_settings(self, content: str) -> None:
        self.settings_path.write_text(content, encoding="utf-8")

    def run_setup(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SETUP_SCRIPT)],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )

    def load_settings(self) -> dict:
        return json.loads(self.settings_path.read_text(encoding="utf-8"))

    def assert_command_targets_entrypoint(self, command: str) -> None:
        match = COMMAND_PATTERN.match(command)
        self.assertIsNotNone(match, f"unexpected statusLine.command shape: {command!r}")
        registered_path = Path(match.group(1))
        self.assertEqual(registered_path.resolve(), ENTRYPOINT.resolve())

    def test_existing_sibling_keys_are_preserved(self) -> None:
        self.write_settings(
            json.dumps(
                {
                    "otherTopLevelKey": "unchanged",
                    "statusLine": {
                        "type": "command",
                        "command": "bash '/old/entrypoint.sh'",
                        "padding": 0,
                        "refreshInterval": 300,
                        "hideVimModeIndicator": True,
                    },
                }
            )
        )

        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)

        settings = self.load_settings()
        status_line = settings["statusLine"]
        self.assert_command_targets_entrypoint(status_line["command"])
        self.assertEqual(status_line["type"], "command")
        self.assertEqual(status_line["padding"], 0)
        self.assertEqual(status_line["refreshInterval"], 300)
        self.assertEqual(status_line["hideVimModeIndicator"], True)
        self.assertEqual(settings["otherTopLevelKey"], "unchanged")

    def test_missing_status_line_key_creates_command_entry(self) -> None:
        self.write_settings(json.dumps({}))

        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)

        settings = self.load_settings()
        status_line = settings["statusLine"]
        self.assertEqual(set(status_line.keys()), {"type", "command"})
        self.assertEqual(status_line["type"], "command")
        self.assert_command_targets_entrypoint(status_line["command"])

    def test_non_object_status_line_becomes_command_only_object(self) -> None:
        for original_value in ("some-legacy-string-command", None, ["not", "an", "object"]):
            with self.subTest(original_value=original_value):
                self.write_settings(json.dumps({"statusLine": original_value}))

                result = self.run_setup()
                self.assertEqual(result.returncode, 0, result.stderr)

                settings = self.load_settings()
                status_line = settings["statusLine"]
                self.assertEqual(set(status_line.keys()), {"type", "command"})
                self.assertEqual(status_line["type"], "command")
                self.assert_command_targets_entrypoint(status_line["command"])

    def test_non_command_type_is_overwritten(self) -> None:
        self.write_settings(
            json.dumps(
                {"statusLine": {"type": "static", "command": "bash '/old/entrypoint.sh'"}}
            )
        )

        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)

        settings = self.load_settings()
        status_line = settings["statusLine"]
        self.assertEqual(status_line["type"], "command")
        self.assert_command_targets_entrypoint(status_line["command"])

    def test_invalid_json_settings_are_left_unchanged_and_fail(self) -> None:
        original_content = "{ this is not valid json"
        self.write_settings(original_content)

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), original_content)

    def test_backup_is_created_with_pre_setup_content(self) -> None:
        original = {"statusLine": {"type": "command", "command": "bash '/old/entrypoint.sh'"}}
        self.write_settings(json.dumps(original))

        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)

        backups = list(self.settings_path.parent.glob("settings.natsuume-statusline-backup.*.json"))
        self.assertEqual(len(backups), 1, backups)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), original)


if __name__ == "__main__":
    unittest.main()

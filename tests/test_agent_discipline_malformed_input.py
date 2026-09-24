from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIRS = (
    ROOT / "plugins" / "agent-discipline",
)
# stdin の hook 入力 JSON を jq で解析する配送 script。hook の stderr は利用者に
# 見えるため、解析失敗時は stderr を出さずに無音終了する (fail-open) ことを検査する。
STDIN_PARSING_SCRIPTS = (
    "inject-always.sh",
    "inject-auto.sh",
    "check-uncommitted-on-session-start.sh",
)
MALFORMED_PAYLOAD = '{"hook_event_name": "UserPromptSubmit", "permission_mode": "auto",'


class AgentDisciplineMalformedInputTest(unittest.TestCase):
    def test_stdin_parsing_scripts_stay_silent_on_malformed_json(self) -> None:
        for plugin_dir in PLUGIN_DIRS:
            for script_name in STDIN_PARSING_SCRIPTS:
                script = plugin_dir / "hooks" / "scripts" / script_name
                with self.subTest(plugin=plugin_dir.name, script=script_name):
                    with tempfile.TemporaryDirectory() as tempdir:
                        result = subprocess.run(
                            ["/bin/bash", str(script)],
                            cwd=ROOT,
                            env={**os.environ, "TMPDIR": tempdir},
                            input=MALFORMED_PAYLOAD,
                            text=True,
                            capture_output=True,
                            timeout=10,
                            check=False,
                        )
                    self.assertEqual(0, result.returncode)
                    self.assertEqual("", result.stdout)
                    self.assertEqual("", result.stderr)


if __name__ == "__main__":
    unittest.main()

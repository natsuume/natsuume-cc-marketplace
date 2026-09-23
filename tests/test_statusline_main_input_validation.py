from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUSLINE_MAIN = ROOT / "plugins" / "natsuume-statusline" / "statusline" / "main.sh"


class StatuslineMainInputValidationTest(unittest.TestCase):
    """main.sh が stdin の JSON を事前検証する契約を検証する。

    トップレベルが JSON object でない入力 (不正 JSON・object 以外の JSON・空入力) では、
    jq のエラーを stderr に出さず、空出力のまま exit 0 で終了する。
    """

    def setUp(self) -> None:
        # HOME / XDG_CACHE_HOME / TMPDIR を一時ディレクトリへ向け、キャッシュ書き込みと
        # 認証情報の参照を実環境から隔離する。
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(tmp_path / "home"),
                "XDG_CACHE_HOME": str(tmp_path / "cache"),
                "TMPDIR": str(tmp_path / "tmp"),
                "COLUMNS": "200",
            }
        )
        for name in ("home", "cache", "tmp", "work"):
            (tmp_path / name).mkdir()
        self.work_dir = tmp_path / "work"

    def run_main(self, stdin: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(STATUSLINE_MAIN)],
            input=stdin.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.work_dir,
            env=self.env,
            check=False,
            timeout=30,
        )

    def assert_silent_exit(self, stdin: str) -> None:
        result = self.run_main(stdin)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode(), "")
        self.assertEqual(result.stderr.decode(), "")

    def test_malformed_json_exits_silently(self) -> None:
        for stdin in ("{not json", '{"model": ', "}"):
            with self.subTest(stdin=stdin):
                self.assert_silent_exit(stdin)

    def test_non_object_json_exits_silently(self) -> None:
        for stdin in ("[]", '"text"', "42", "null"):
            with self.subTest(stdin=stdin):
                self.assert_silent_exit(stdin)

    def test_empty_input_exits_silently(self) -> None:
        for stdin in ("", "\n"):
            with self.subTest(stdin=repr(stdin)):
                self.assert_silent_exit(stdin)

    def test_valid_object_is_rendered(self) -> None:
        # model_scoped を非空にして OAuth usage API の background fetch を起動させない。
        payload = {
            "workspace": {"current_dir": str(self.work_dir)},
            "model": {"display_name": "TestModel"},
            "rate_limits": {
                "model_scoped": [
                    {"display_name": "Scoped", "utilization": 10, "resets_at": ""}
                ]
            },
        }

        result = self.run_main(json.dumps(payload))

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr.decode(), "")
        self.assertIn("TestModel", result.stdout.decode())


if __name__ == "__main__":
    unittest.main()

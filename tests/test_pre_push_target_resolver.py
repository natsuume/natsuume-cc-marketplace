"""target-resolver の push segment index 契約テスト (issue #127)。

Phase A の seam は ``resolve_push_target <push-index> <segments...>`` です。caller が
token ベースで確定した 0 始まり index を唯一の真実源とし、resolver は push segment を
再探索しません。これにより、手前の quote 内にある ``push`` という文字列と実際の push
segment が混同されない契約を固定します。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESOLVER = (
    ROOT
    / "plugins"
    / "pre-push-review"
    / "hooks"
    / "scripts"
    / "lib"
    / "target-resolver.sh"
)

DRIVER = r"""
source "$1"
shift
resolve_push_target "$@"
"""


class ResolvePushTargetIndexContractTest(unittest.TestCase):
    def run_resolver(
        self, work: Path, push_index: str, *segments: str
    ) -> subprocess.CompletedProcess[bytes]:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("target-resolver integration requires bash")
        return subprocess.run(
            [bash, "-s", "--", str(RESOLVER), push_index, *segments],
            input=DRIVER.encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work,
        )

    def assert_resolves_to_subdirectory(
        self, push_index: str, *segments: str
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            expected = work / "sub"
            expected.mkdir()

            result = self.run_resolver(work, push_index, *segments)

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout.decode(), str(expected))

    def test_quoted_push_reference_before_real_push_uses_caller_index(self) -> None:
        self.assert_resolves_to_subdirectory(
            "2",
            'git commit -m "let\'s push it"',
            "cd sub",
            "git push",
        )

    def test_supported_target_overrides_still_resolve(self) -> None:
        cases = (
            ("1", ("cd sub", "git push")),
            ("0", ("git -C sub push",)),
            ("0", ("GIT_DIR=sub/.git git push",)),
        )
        for push_index, segments in cases:
            with self.subTest(push_index=push_index, segments=segments):
                self.assert_resolves_to_subdirectory(push_index, *segments)

    def test_invalid_index_is_rejected_before_segment_processing(self) -> None:
        for push_index in ("", "word", "-1", "01", "2"):
            with self.subTest(push_index=push_index):
                with tempfile.TemporaryDirectory() as name:
                    result = self.run_resolver(
                        Path(name), push_index, "cd sub", "git push"
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, b"")

    def test_index_that_points_to_non_push_segment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            result = self.run_resolver(
                Path(name), "0", "git commit -m x", "git push"
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()

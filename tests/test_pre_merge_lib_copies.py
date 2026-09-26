"""pre-merge-cross-review が保持する共有 lib の同一性契約。

pre-merge-cross-review は `codex-companion-resolver.sh` だけを、canonical の byte-identical な
コピーとして `hooks/scripts/lib/` に保つ。canonical は `pre-push-codex-review`
(`plugins/pre-push-codex-review/hooks/scripts/lib/`) で、codex review の実行機構が両 plugin で
同一のため。それ以外の lib コピーは持たない (`lib/` に置く共有 lib 以外のファイルは、pre-merge
専用の `markers.sh` だけ)。

reviewer 一式 (wrapper / subagent 定義 / hook script 群) は pre-merge 専用の
実装であり、pre-push 系との文字列同一性契約は設けない。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PUSH_CODEX_LIB = (
    ROOT / "plugins" / "pre-push-codex-review" / "hooks" / "scripts" / "lib"
)
MERGE_LIB = (
    ROOT / "plugins" / "pre-merge-cross-review" / "hooks" / "scripts" / "lib"
)

# pre-merge-cross-review の lib/ に置くファイルの全集合 (共有 lib のコピー + pre-merge 専用 lib)。
EXPECTED_MERGE_LIB_FILES = {"codex-companion-resolver.sh", "markers.sh"}


class SharedLibCopiesTest(unittest.TestCase):
    def assert_byte_identical_copy(self, canonical: Path, copy: Path) -> None:
        self.assertTrue(
            canonical.is_file(), f"canonical lib が見つかりません: {canonical}"
        )
        self.assertTrue(copy.is_file(), f"コピー先 lib が見つかりません: {copy}")
        self.assertEqual(
            canonical.read_bytes(),
            copy.read_bytes(),
            f"{canonical} と {copy} の内容が乖離しています (byte-identical "
            "コピーの契約に違反)",
        )

    def test_codex_companion_resolver_is_byte_identical_in_merge_plugin(
        self,
    ) -> None:
        self.assert_byte_identical_copy(
            PUSH_CODEX_LIB / "codex-companion-resolver.sh",
            MERGE_LIB / "codex-companion-resolver.sh",
        )

    def test_merge_lib_holds_no_other_copies(self) -> None:
        names = {path.name for path in MERGE_LIB.iterdir() if path.is_file()}
        self.assertEqual(EXPECTED_MERGE_LIB_FILES, names)


if __name__ == "__main__":
    unittest.main()

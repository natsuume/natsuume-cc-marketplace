"""pre-merge-cross-review が保持する共有 lib の同一性契約。

pre-merge-cross-review は `codex-companion-resolver.sh` と `stale-broker-guard.mjs` と
`text-paths.sh` の 3 つを、canonical の byte-identical なコピーとして `hooks/scripts/lib/` に
保つ。`codex-companion-resolver.sh` と `stale-broker-guard.mjs` の canonical は
`pre-push-codex-review` (`plugins/pre-push-codex-review/hooks/scripts/lib/`) で、codex review の
実行機構が両 plugin で同一のため。`text-paths.sh` (テキスト系文書のみの変更の判定) の
canonical は `pre-push-review` (`plugins/pre-push-review/hooks/scripts/lib/`) で、push gate と
同じ定義で判定するため。この 3 つ以外の lib コピーは持たない (`lib/` に置く共有 lib 以外の
ファイルは、pre-merge 専用の `markers.sh` だけ)。

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
PUSH_CORE_LIB = ROOT / "plugins" / "pre-push-review" / "hooks" / "scripts" / "lib"
MERGE_LIB = (
    ROOT / "plugins" / "pre-merge-cross-review" / "hooks" / "scripts" / "lib"
)

# pre-merge-cross-review の lib/ に置くファイルの全集合 (共有 lib のコピー + pre-merge 専用 lib)。
EXPECTED_MERGE_LIB_FILES = {
    "codex-companion-resolver.sh",
    "stale-broker-guard.mjs",
    "text-paths.sh",
    "markers.sh",
}


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

    def test_stale_broker_guard_is_byte_identical_in_merge_plugin(
        self,
    ) -> None:
        self.assert_byte_identical_copy(
            PUSH_CODEX_LIB / "stale-broker-guard.mjs",
            MERGE_LIB / "stale-broker-guard.mjs",
        )

    def test_text_paths_is_byte_identical_in_merge_plugin(self) -> None:
        self.assert_byte_identical_copy(
            PUSH_CORE_LIB / "text-paths.sh",
            MERGE_LIB / "text-paths.sh",
        )

    def test_merge_lib_holds_no_other_copies(self) -> None:
        names = {path.name for path in MERGE_LIB.iterdir() if path.is_file()}
        self.assertEqual(EXPECTED_MERGE_LIB_FILES, names)


if __name__ == "__main__":
    unittest.main()

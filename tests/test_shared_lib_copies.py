"""pre-push-review / pre-push-codex-review / codex-advisor 間の共有 lib 同一性契約
(issue #378 Phase A)。

pre-push-review core と pre-push-codex-review は、それぞれ単独 install でも自立
動作するため push gate の共通ロジック (`cmd-parser.sh` / `target-resolver.sh` /
`diff-hash.sh`) を独立に保持する。正本 (canonical) は pre-push-review 側で、
pre-push-codex-review 側は byte-identical なコピーを保つ契約とする。

`codex-companion-resolver.sh` は逆方向で、pre-push-codex-review 側が正本、
codex-advisor 側がそのコピーを保つ。

`markers.sh` は plugin ごとにマーカー集合が異なる (pre-push-review core は
code / security の 2 マーカー、pre-push-codex-review は codex マーカーのみ) ため、
本テストの同一性検査の対象外とする。

存在チェックをバイト比較より先に行うのは、失敗時に「コピー元/コピー先のどちらの
ファイルが欠けているか」と「両方存在するが内容が乖離しているか」を区別できる
ようにするため。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_LIB = ROOT / "plugins" / "pre-push-review" / "hooks" / "scripts" / "lib"
SPLIT_LIB = (
    ROOT / "plugins" / "pre-push-codex-review" / "hooks" / "scripts" / "lib"
)
CODEX_ADVISOR_RESOLVER = (
    ROOT / "plugins" / "codex-advisor" / "scripts" / "lib"
    / "codex-companion-resolver.sh"
)

# pre-push-review core が canonical。pre-push-codex-review はこの byte-identical
# コピーを保持する。
CORE_CANONICAL_LIB_NAMES = (
    "cmd-parser.sh",
    "target-resolver.sh",
    "diff-hash.sh",
)


class SharedLibCopiesTest(unittest.TestCase):
    def assert_byte_identical(self, canonical: Path, copy: Path) -> None:
        self.assertTrue(
            canonical.is_file(), f"canonical lib が見つかりません: {canonical}"
        )
        self.assertTrue(
            copy.is_file(), f"コピー先 lib が見つかりません: {copy}"
        )
        self.assertEqual(
            canonical.read_bytes(),
            copy.read_bytes(),
            f"{canonical} と {copy} の内容が乖離しています (byte-identical "
            "コピーの契約に違反)",
        )

    def test_core_libs_are_byte_identical_in_split_plugin(self) -> None:
        for name in CORE_CANONICAL_LIB_NAMES:
            with self.subTest(lib=name):
                self.assert_byte_identical(CORE_LIB / name, SPLIT_LIB / name)

    def test_codex_companion_resolver_is_byte_identical_in_codex_advisor(
        self,
    ) -> None:
        self.assert_byte_identical(
            SPLIT_LIB / "codex-companion-resolver.sh", CODEX_ADVISOR_RESOLVER
        )


if __name__ == "__main__":
    unittest.main()

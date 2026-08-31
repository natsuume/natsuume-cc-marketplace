"""pre-merge-codex-review が保持する共有 lib の同一性契約 (Phase A: spec-first, red)。

`hooks/scripts/lib/codex-companion-resolver.sh` は `pre-push-codex-review`
(`plugins/pre-push-codex-review/hooks/scripts/lib/`) が canonical で、
pre-merge-codex-review 側はその byte-identical なコピーを保つ (codex review の
実行機構は両 plugin で同一のため)。それ以外の lib コピーは持たない。

reviewer 一式 (wrapper / subagent 定義 / hook script 群) は pre-merge 専用の
実装であり、pre-push 系との文字列同一性契約は設けない。

pre-merge-codex-review 側の lib ファイルは Phase A 時点でまだ存在しないため、
本テストは意図した失敗で red になる。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PUSH_CODEX_LIB = (
    ROOT / "plugins" / "pre-push-codex-review" / "hooks" / "scripts" / "lib"
)
MERGE_LIB = (
    ROOT / "plugins" / "pre-merge-codex-review" / "hooks" / "scripts" / "lib"
)


class SharedLibCopiesTest(unittest.TestCase):
    def test_codex_companion_resolver_is_byte_identical_in_merge_plugin(
        self,
    ) -> None:
        canonical = PUSH_CODEX_LIB / "codex-companion-resolver.sh"
        copy = MERGE_LIB / "codex-companion-resolver.sh"
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


if __name__ == "__main__":
    unittest.main()

"""pre-merge-cross-review が保持する共有 lib の同一性契約。

pre-merge-cross-review は次の 2 つの lib を、canonical の byte-identical なコピーとして
`hooks/scripts/lib/` に保つ。それ以外の lib コピーは持たない。

- `codex-companion-resolver.sh`: canonical は `pre-push-codex-review`
  (`plugins/pre-push-codex-review/hooks/scripts/lib/`)。codex review の実行機構は
  両 plugin で同一のため
- `fable-weekly-usage.sh`: canonical は `cross-model-advisor`
  (`plugins/cross-model-advisor/scripts/lib/`)。Fable 週次枠の使用率判定は、Fable を
  使う subagent を起動するかを決める plugin 間で同一のため

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
ADVISOR_LIB = ROOT / "plugins" / "cross-model-advisor" / "scripts" / "lib"
MERGE_LIB = (
    ROOT / "plugins" / "pre-merge-cross-review" / "hooks" / "scripts" / "lib"
)


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

    def test_fable_weekly_usage_is_byte_identical_in_merge_plugin(self) -> None:
        self.assert_byte_identical_copy(
            ADVISOR_LIB / "fable-weekly-usage.sh",
            MERGE_LIB / "fable-weekly-usage.sh",
        )


if __name__ == "__main__":
    unittest.main()

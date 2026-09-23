"""pre-merge-codex-review が保持する共有 lib の同一性契約。

pre-merge-codex-review の `hooks/scripts/lib/` は、他 plugin を canonical とする
共有 lib のコピーを次の 2 つだけ持ち、いずれも canonical と byte-identical に保つ:

- `codex-companion-resolver.sh`: canonical は `pre-push-codex-review`
  (`plugins/pre-push-codex-review/hooks/scripts/lib/`)。codex review の実行機構は
  両 plugin で同一のため。
- `cmd-parser.sh`: canonical は `pre-push-review`
  (`plugins/pre-push-review/hooks/scripts/lib/`)。hook script が Bash コマンド文字列の
  行継続 `\\<改行>` を正規化する helper (`normalize_line_continuations`) を、
  bash のバージョンに依らない単一の実装として共有するため。

この 2 つ以外に他 plugin の lib のコピーは持たない。

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
PUSH_REVIEW_LIB = (
    ROOT / "plugins" / "pre-push-review" / "hooks" / "scripts" / "lib"
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

    def test_cmd_parser_is_byte_identical_in_merge_plugin(
        self,
    ) -> None:
        canonical = PUSH_REVIEW_LIB / "cmd-parser.sh"
        copy = MERGE_LIB / "cmd-parser.sh"
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

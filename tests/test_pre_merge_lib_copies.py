"""pre-merge-codex-review が保持する共有 lib の同一性契約 (Phase A: spec-first, red)。

pre-merge-codex-review は単独 install でも自立動作するため、push gate 系
プラグインが持つ共通ロジックの一部を自前で保持する:

- `hooks/scripts/lib/cmd-parser.sh` / `diff-hash.sh` は `pre-push-review`
  (`plugins/pre-push-review/hooks/scripts/lib/`) が canonical で、
  pre-merge-codex-review 側はその byte-identical なコピーを保つ。
- `hooks/scripts/lib/codex-companion-resolver.sh` は `pre-push-codex-review`
  (`plugins/pre-push-codex-review/hooks/scripts/lib/`) が canonical で、
  pre-merge-codex-review 側はその byte-identical なコピーを保つ。

reviewer 一式 (`hooks/scripts/block-bg-codex-wrapper.sh` /
`hooks/scripts/auto-mark.sh` / `hooks/scripts/run-pre-merge-codex-review.sh` /
`agents/codex-reviewer.md`) は同一性検査の対象外である。これらは attestation の
内容 (単一 hash ではなく repo / pr / merge_base / head / diff_hash の 5 key)・
review 対象の決定 (default base 検出ではなく実 PR base)・gate の単位 (branch
push ではなく PR merge) が pre-push 系と本質的に異なるため、pre-merge 専用に
独立実装する (実質差分を持つ部品を無理に共通化しない)。drift 防止は文字列
同一性ではなく挙動契約テスト (tests/test_pre_merge_codex_gate.py ほか) が担う。

`target-resolver.sh` は git push コマンドの解析を公開契約とする lib のため、
merge gate の lib 構成には含めない (gate は単一 invocation のみを判定対象と
するため、対象 repo の解決は cwd 基準で足りる)。

pre-merge-codex-review 側の lib ファイルは Phase A 時点でまだ存在しないため、
本ファイルの全テストは意図した失敗で red になる。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PUSH_REVIEW_LIB = ROOT / "plugins" / "pre-push-review" / "hooks" / "scripts" / "lib"
PUSH_CODEX_LIB = (
    ROOT / "plugins" / "pre-push-codex-review" / "hooks" / "scripts" / "lib"
)
MERGE_LIB = (
    ROOT / "plugins" / "pre-merge-codex-review" / "hooks" / "scripts" / "lib"
)

# pre-push-review が canonical。pre-merge-codex-review はこの byte-identical
# コピーを保持する。
CORE_CANONICAL_LIB_NAMES = (
    "cmd-parser.sh",
    "diff-hash.sh",
)


class SharedLibCopiesTest(unittest.TestCase):
    def assert_byte_identical(self, canonical: Path, copy: Path) -> None:
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

    def test_push_review_libs_are_byte_identical_in_merge_plugin(self) -> None:
        for name in CORE_CANONICAL_LIB_NAMES:
            with self.subTest(lib=name):
                self.assert_byte_identical(PUSH_REVIEW_LIB / name, MERGE_LIB / name)

    def test_codex_companion_resolver_is_byte_identical_in_merge_plugin(
        self,
    ) -> None:
        self.assert_byte_identical(
            PUSH_CODEX_LIB / "codex-companion-resolver.sh",
            MERGE_LIB / "codex-companion-resolver.sh",
        )


if __name__ == "__main__":
    unittest.main()

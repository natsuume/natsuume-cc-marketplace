"""pre-merge-codex-review が保持する共有 lib / reviewer ファイルの同一性契約
(Phase A: spec-first, red)。

pre-merge-codex-review は単独 install でも自立動作するため、push gate 系
プラグインが持つ共通ロジックを自前で保持する:

- `hooks/scripts/lib/cmd-parser.sh` / `target-resolver.sh` / `diff-hash.sh` は
  `pre-push-review` (`plugins/pre-push-review/hooks/scripts/lib/`) が canonical
  で、pre-merge-codex-review 側はその byte-identical なコピーを保つ。
- `hooks/scripts/lib/codex-companion-resolver.sh` は `pre-push-codex-review`
  (`plugins/pre-push-codex-review/hooks/scripts/lib/`) が canonical で、
  pre-merge-codex-review 側はその byte-identical なコピーを保つ。
- reviewer 一式 3 ファイル (`hooks/scripts/block-bg-codex-wrapper.sh` /
  `hooks/scripts/auto-mark.sh` / `agents/codex-reviewer.md`) は
  `pre-push-codex-review` の対応ファイルと、namespace 文字列・wrapper basename・
  marker prefix の置換を除き同型を保つ。
- wrapper (`hooks/scripts/run-pre-merge-codex-review.sh`) は、review 対象の決定
  (default base 検出ではなく実 PR base) と attestation の内容 (単一 hash ではなく
  5 key) が pre-push 系と本質的に異なるため、同一性検査の対象外として pre-merge
  専用に独立実装する (実質差分を持つ部品を無理に共通化しない)。

pre-merge-codex-review 側の対象ファイルは Phase A 時点でまだ存在しない
(plugin.json / hooks.json / README.md の骨格 3 ファイルのみが存在する) ため、
本ファイルの全テストは意図した失敗で red になる。

正規化同一性検査 (`ReviewerFileNormalizedIdentityTest`) は、両ファイルのテキストに
対して `str.replace` で次の 3 組の文字列を共通 placeholder へ双方向に寄せてから
比較する: namespace 文字列 (`pre-push-codex-review` ↔ `pre-merge-codex-review`)・
wrapper basename (`run-pre-push-codex-review.sh` ↔ `run-pre-merge-codex-review.sh`)・
marker prefix (`.claude-pre-push-` ↔ `.claude-pre-merge-`)。merge gate 固有の
挙動 (例: `--auto` 拒否や `--match-head-commit` 付与に関する記述) が reviewer 系
ファイルに必要になり、正規化後もなお pre-push-codex-review 側と一致しない差分が
生じた場合は、「同型」契約そのものを緩めるのではなく、その差分が両 plugin で
本質的に異なる概念(push gate に無く merge gate にだけ存在する概念)であることを
確認したうえで、`NORMALIZATION_PAIRS` に対応する専用の置換ペアを追加して
正規化の対象に含める。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PUSH_REVIEW_LIB = ROOT / "plugins" / "pre-push-review" / "hooks" / "scripts" / "lib"
PUSH_CODEX_ROOT = ROOT / "plugins" / "pre-push-codex-review"
PUSH_CODEX_LIB = PUSH_CODEX_ROOT / "hooks" / "scripts" / "lib"

MERGE_ROOT = ROOT / "plugins" / "pre-merge-codex-review"
MERGE_LIB = MERGE_ROOT / "hooks" / "scripts" / "lib"

# pre-push-review が canonical。pre-merge-codex-review はこの byte-identical
# コピーを保持する。
CORE_CANONICAL_LIB_NAMES = (
    "cmd-parser.sh",
    "target-resolver.sh",
    "diff-hash.sh",
)

# reviewer 系ファイルの対応関係: (pre-merge-codex-review 側パス, pre-push-codex-review 側パス)
REVIEWER_FILE_PAIRS = (
    (
        MERGE_ROOT / "hooks" / "scripts" / "block-bg-codex-wrapper.sh",
        PUSH_CODEX_ROOT / "hooks" / "scripts" / "block-bg-codex-wrapper.sh",
    ),
    (
        MERGE_ROOT / "hooks" / "scripts" / "auto-mark.sh",
        PUSH_CODEX_ROOT / "hooks" / "scripts" / "auto-mark.sh",
    ),
    (
        MERGE_ROOT / "agents" / "codex-reviewer.md",
        PUSH_CODEX_ROOT / "agents" / "codex-reviewer.md",
    ),
)

# 双方向正規化ペア: (merge 側の文字列, push 側の文字列, placeholder)。
# より長く具体的なパターンを先に適用することで、短いパターン (namespace) が
# 先に部分文字列を消費してしまう順序依存を避ける。
# wrapper basename ペアは、wrapper ファイル自体が検査対象外 (独立実装) でも、
# 検査対象の他ファイル (block-bg-codex-wrapper.sh 等) 内の wrapper 名への言及を
# 正規化するために必要である。
NORMALIZATION_PAIRS = (
    (
        "run-pre-merge-codex-review.sh",
        "run-pre-push-codex-review.sh",
        "<<WRAPPER_BASENAME>>",
    ),
    (
        "pre-merge-codex-review",
        "pre-push-codex-review",
        "<<PLUGIN_NAMESPACE>>",
    ),
    (
        ".claude-pre-merge-",
        ".claude-pre-push-",
        "<<MARKER_PREFIX>>",
    ),
)


def _normalize(text: str) -> str:
    for merge_token, push_token, placeholder in NORMALIZATION_PAIRS:
        text = text.replace(merge_token, placeholder)
        text = text.replace(push_token, placeholder)
    return text


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


class ReviewerFileNormalizedIdentityTest(unittest.TestCase):
    def test_reviewer_files_match_pre_push_codex_review_after_normalization(
        self,
    ) -> None:
        for merge_path, push_path in REVIEWER_FILE_PAIRS:
            with self.subTest(file=merge_path.name):
                self.assertTrue(
                    push_path.is_file(),
                    f"対応する pre-push-codex-review 側ファイルが見つかりません: "
                    f"{push_path}",
                )
                self.assertTrue(
                    merge_path.is_file(),
                    f"pre-merge-codex-review 側ファイルが見つかりません: {merge_path}",
                )
                merge_normalized = _normalize(merge_path.read_text(encoding="utf-8"))
                push_normalized = _normalize(push_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    merge_normalized,
                    push_normalized,
                    f"{merge_path} と {push_path} は正規化後も一致しません "
                    "(namespace / wrapper basename / marker prefix 以外の差分が"
                    "あります)",
                )


if __name__ == "__main__":
    unittest.main()

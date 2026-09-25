"""agent-discipline: Opus 5.5 メイン + Fable Advisor パターン向け分業規律・自走方針の契約テスト。

背景:
- メインセッションを Opus 5.5 にし、ワーカー (実装・調査・一括修正等) のサブエージェントも
  model 未指定でメインセッションの Opus 5.5 を継承して動かす。Fable は cross-model-advisor の
  fable-advisor-runner と pre-merge-cross-review の fable-reviewer の起動にのみ使う。
  pre-push-review の reviewer 2 体は常に Opus で起動するため Fable の用途に含めない。
- Fable をメインセッションで使う運用は無いため、分業規律に Fable メイン向けの記述を持たない。
- discipline-opus.md / discipline-sonnet.md の rule:delegation-rules 節は、「advisor と
  pre-merge review の起動に限り `model: "fable"` を明示して使い、週次枠ガードで deny
  されたら再起動せずスキップ」と記述し、pre-push-review の reviewer には言及しない。
- discipline-opus.md は、ワーカーをメインセッションと同じモデルで動かし Sonnet / Haiku へ
  下げない旨を記述する。ヘッダコメントと冒頭文の対象は Opus 系のメインセッションのみで、
  Fable を含めない。discipline-sonnet.md のヘッダコメントの対象読者は「Opus 系以外のモデル」。
- discipline-opus.md の effort 規律は公式ガイド「Prompting Claude Opus 5.5」
  (prompting-claude-opus-5-5、既定 effort は medium) を基準に書き換える。Opus 5 向けの
  3 規律 (委任しない作業・スコープ制限の 1 文・汎用再確認指示の禁止) は Opus 5 /
  Opus 5.5 の両方に適用する記述にする。
- auto-mode.md (全モデル共通で配送) に、作業が残っている間の 4 種類の止まり方の
  禁止、止まってよい場合、既存の禁止 / 要確認事項を不要にしない旨を追加する。

subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が SUBFAILED として
分離報告される一方、親テストノード自体は PASSED と表示され判定が曖昧になるため、
リスト集約 + 1 テスト = 1 判定に保つ (tests/test_opus5_effort_unpin.py と同じ慣行)。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "prompts"

DISCIPLINE_OPUS = PROMPTS / "discipline-opus.md"
DISCIPLINE_SONNET = PROMPTS / "discipline-sonnet.md"

# Fable の用途を限定して記述する分業規律 2 ファイル。
DISCIPLINES = {
    "discipline-opus.md": DISCIPLINE_OPUS,
    "discipline-sonnet.md": DISCIPLINE_SONNET,
}

AUTO_MODE_FILES = {
    "agent-discipline/auto-mode.md": PROMPTS / "auto-mode.md",
}

# 節スコープ検査で切り出すセクション境界 (rule ID マーカー)。
DELEGATION_RULES_MARKER = "<!-- rule:delegation-rules -->"
DELEGATION_INSTRUCTION_MARKER = "<!-- rule:delegation-instruction -->"

# 全面禁止の bullet 見出し (opus / sonnet に含まれない)。
FABLE_PROHIBITION_PHRASE = "Fable をサブエージェントに使わない"

# rule:delegation-rules 節に必須の Fable 用途の記述 (opus / sonnet 共通の canonical 文言)。
FABLE_USAGE_PHRASES = (
    # 用途を fable-advisor-runner と fable-reviewer の起動に限る
    (
        "**Fable は advisor と pre-merge review の起動に限る**: Fable は "
        "`cross-model-advisor:fable-advisor-runner` と "
        "`pre-merge-cross-review:fable-reviewer` の起動にだけ使い、"
        "ワーカー (実装・調査・一括修正等) やそれ以外の reviewer には使わない。"
    ),
    # hook (PreToolUse の Agent|Task) が捕捉しない Workflow の agent() では使わない
    "Workflow の `agent()` では Fable を使わない",
    # model の明示 (未指定・frontmatter 経由の Fable 実行は使わない)
    'model を `"fable"` と明示する',
    "model 未指定・agent 定義 frontmatter による Fable 実行は使わない",
    # 週次枠ガードによる deny と deny 後の振る舞い (両 agent 共通)
    "Fable 週次枠の使用率が閾値を超えた場合・確認できない場合",
    "再起動せずスキップする",
)

# rule:delegation-rules 節から消えていること (旧方針の記述)。
FABLE_REMOVED_PHRASES = (
    # advisor のみに限定していた旧見出し
    "**Fable は advisor の起動に限る**",
    # スキップ対象を fable-advisor-runner だけに書いた旧文言
    "fable-advisor-runner は再起動せずスキップする",
    # Fable メインのセッション向けの記述
    "Fable メインのセッション",
)

# discipline-opus.md に必須の委任先モデルの記述 (bullet の書き出し)。
WORKER_MODEL_PHRASE = (
    "**ワーカーはメインセッションと同じモデルで動かす**: 実装・調査・一括修正の"
    "サブエージェントは model 未指定 (メインセッションの Opus 5.5 を継承) で起動し、"
    "Sonnet / Haiku へ下げない"
)

# discipline-sonnet.md のヘッダコメントの対象読者の記述と、消えていること (旧記述)。
SONNET_HEADER_AUDIENCE_PHRASE = "Opus 系以外のモデル"
SONNET_HEADER_REMOVED_PHRASE = "Opus 系・Fable 以外"

# rule:delegation-rules 節に含めない記述 (pre-push-review の reviewer は Fable の用途ではない)。
FABLE_EXCLUDED_PHRASES = (
    "pre-push-review:",
    "reviewer / advisor",
)

# discipline-opus.md の effort 規律 (Opus 5.5 基準)。
OPUS55_EFFORT_PHRASES = (
    "Opus 5.5 への委任では effort の既定 `medium` を基準にする",
    "境界が明確な機械的作業では `low` を検討し",
    "`xhigh` / `max` は品質向上を確認できた作業に限る",
    "Opus 5 で使っていた effort をそのまま持ち込まない",
)
# 置き換えで消える Opus 5 基準の effort 見出し。
OPUS5_EFFORT_PHRASE = "Opus 5 を使う委任では effort を固定しない"

# Opus 5 向けの 3 規律を Opus 5 / Opus 5.5 の両方に適用する記述。
OPUS_BOTH_MODEL_PHRASES = (
    "Opus 5 / Opus 5.5 とも直接行う",
    "Opus 5 / Opus 5.5 への委任指示にはスコープ制限の 1 文を必ず含める",
    "Opus 5 / Opus 5.5 への委任では汎用的な再確認指示を加えない",
)

# ヘッダコメントで参照する公式ガイド。
OPUS55_GUIDE_SLUG = "prompting-claude-opus-5-5"

# auto-mode.md の追加記述 (4 種類の止まり方・止まってよい場合・継続方法・既存確認との関係)。
AUTO_MODE_PHRASES = (
    "実施内容の要約の末尾で次の手順を宣言し、ツール呼び出しをせずに turn を終える",
    "続行の可否を尋ねて止まる",
    "残作業を妨げない判断事項を列挙して止まる",
    "区切りがよい・turn が長くなったという理由で報告のために止まる",
    "ユーザの入力なしに進められる作業が無い場合",
    "進行を妨げているものが意図的に保護されたものである場合",
    "次のツール呼び出しと同じメッセージに含めて作業を続ける",
    "禁止 / 要確認事項やマージ前提条件の確認を不要にするものではない",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def delegation_rules_section(text: str) -> str:
    """rule:delegation-rules 〜 rule:delegation-instruction の範囲を返す (無ければ空文字)。"""
    start = text.find(DELEGATION_RULES_MARKER)
    end = text.find(DELEGATION_INSTRUCTION_MARKER, max(start, 0))
    return text[start:end] if 0 <= start < end else ""


def header_comment(text: str) -> str:
    """ファイル先頭の HTML コメント (`<!--` 〜 最初の `-->`) を返す (無ければ空文字)。"""
    if not text.startswith("<!--"):
        return ""
    end = text.find("-->")
    return text[: end + len("-->")] if end >= 0 else ""


def intro_paragraph(text: str) -> str:
    """ヘッダコメント直後の冒頭段落 (最初の rule ID マーカーより前の本文) を返す。"""
    body = text[len(header_comment(text)) :]
    end = body.find("<!-- rule:")
    return (body[:end] if end >= 0 else body).strip()


class FableUsageDelegationRulesTests(unittest.TestCase):
    """opus / sonnet 版の rule:delegation-rules 節が Fable の用途を限定して許可すること。"""

    def test_prohibition_phrase_absent_from_disciplines(self) -> None:
        offenders = [
            name
            for name, path in DISCIPLINES.items()
            if FABLE_PROHIBITION_PHRASE in read(path)
        ]
        self.assertEqual([], offenders, f"全面禁止の旧記述が残るファイル: {offenders}")

    def test_fable_usage_phrases_present_in_delegation_rules(self) -> None:
        missing = [
            f"{name}: {phrase!r}"
            for name, path in DISCIPLINES.items()
            for phrase in FABLE_USAGE_PHRASES
            if phrase not in delegation_rules_section(read(path))
        ]
        self.assertEqual(
            [], missing, f"rule:delegation-rules 節に無い Fable 用途の記述: {missing}"
        )

    def test_superseded_fable_phrases_absent_from_delegation_rules(self) -> None:
        present = [
            f"{name}: {phrase!r}"
            for name, path in DISCIPLINES.items()
            for phrase in FABLE_REMOVED_PHRASES
            if phrase in delegation_rules_section(read(path))
        ]
        self.assertEqual(
            [], present, f"rule:delegation-rules 節に残る旧方針の Fable 記述: {present}"
        )

    def test_reviewers_are_not_fable_usage_in_delegation_rules(self) -> None:
        present = [
            f"{name}: {phrase!r}"
            for name, path in DISCIPLINES.items()
            for phrase in FABLE_EXCLUDED_PHRASES
            if phrase in delegation_rules_section(read(path))
        ]
        self.assertEqual(
            [], present, f"rule:delegation-rules 節に残る reviewer の Fable 用途: {present}"
        )


class WorkerModelTests(unittest.TestCase):
    """ワーカーをメインセッションと同じモデルで動かす規律と、対象モデルの記述。"""

    def test_opus_discipline_keeps_workers_on_the_main_session_model(self) -> None:
        self.assertIn(WORKER_MODEL_PHRASE, read(DISCIPLINE_OPUS))

    def test_opus_header_and_intro_do_not_target_fable(self) -> None:
        text = read(DISCIPLINE_OPUS)
        present = [
            part
            for part, body in (
                ("ヘッダコメント", header_comment(text)),
                ("冒頭文", intro_paragraph(text)),
            )
            if "Fable" in body
        ]
        self.assertEqual([], present, f"discipline-opus.md で Fable を対象に含む箇所: {present}")

    def test_sonnet_header_audience_is_models_other_than_opus(self) -> None:
        header = header_comment(read(DISCIPLINE_SONNET))
        self.assertIn(SONNET_HEADER_AUDIENCE_PHRASE, header)
        self.assertNotIn(SONNET_HEADER_REMOVED_PHRASE, header)


class Opus55EffortTests(unittest.TestCase):
    """discipline-opus.md の effort 規律が Opus 5.5 基準であること。"""

    def test_opus55_effort_phrases_present_in_delegation_rules(self) -> None:
        section = delegation_rules_section(read(DISCIPLINE_OPUS))
        missing = [phrase for phrase in OPUS55_EFFORT_PHRASES if phrase not in section]
        self.assertEqual([], missing, f"Opus 5.5 基準の effort 規律に無い文言: {missing}")

    def test_opus5_effort_heading_absent(self) -> None:
        self.assertNotIn(OPUS5_EFFORT_PHRASE, read(DISCIPLINE_OPUS))

    def test_opus5_rules_apply_to_both_models(self) -> None:
        text = read(DISCIPLINE_OPUS)
        missing = [phrase for phrase in OPUS_BOTH_MODEL_PHRASES if phrase not in text]
        self.assertEqual(
            [], missing, f"Opus 5 / Opus 5.5 の両方に適用する記述が無い: {missing}"
        )

    def test_header_comment_references_opus55_guide(self) -> None:
        self.assertIn(OPUS55_GUIDE_SLUG, header_comment(read(DISCIPLINE_OPUS)))


class AutoModeStopPatternTests(unittest.TestCase):
    """auto-mode.md が止まり方の禁止と止まってよい場合を明記すること。"""

    def test_auto_mode_contains_stop_pattern_phrases(self) -> None:
        missing = [
            f"{name}: {phrase!r}"
            for name, path in AUTO_MODE_FILES.items()
            for phrase in AUTO_MODE_PHRASES
            if phrase not in read(path)
        ]
        self.assertEqual([], missing, f"auto-mode.md に無い記述: {missing}")

    def test_stop_pattern_block_precedes_existing_prohibitions(self) -> None:
        """追加記述は既存の禁止 / 要確認事項より前に置く (「下記の」で参照するため)。"""
        violations = []
        for name, path in AUTO_MODE_FILES.items():
            text = read(path)
            stop_index = text.find(AUTO_MODE_PHRASES[0])
            prohibition_index = text.find("ただし以下は引き続き禁止 / 要確認")
            if stop_index < 0 or prohibition_index < 0 or stop_index > prohibition_index:
                violations.append(name)
        self.assertEqual([], violations, f"配置順が不成立: {violations}")


if __name__ == "__main__":
    unittest.main()

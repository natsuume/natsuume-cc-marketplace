"""Claude 5 世代公式プロンプトガイド準拠のプロンプト是正 (6 plugin) の契約テスト。

背景:
- Anthropic 公式ガイド (claude-prompting-best-practices / prompting-claude-fable-5 /
  prompting-claude-opus-5 / prompting-claude-sonnet-5 / context engineering blog) に
  照らした監査で確定した是正内容を、実行可能仕様 (spec-first Phase A) として固定する。
- agent-discipline: 3-way 分業規律 (fable / opus / sonnet) 間の意味的 drift の修復。
  lint-prompt-sync.sh は rule ID 集合の一致のみを検査し本文の表現差分を見ないため
  (同スクリプトのスコープ注記参照)、共有 canonical 文の存在を本テストで固定する。
- git-guardrails: rebase-workflow skill に残る bash-decompose 規律違反 (コマンド置換 +
  変数連結の一括スクリプト例) の除去と、origin/HEAD stale 対策の追加。
- pre-push-review: Opus 5 が消費する reviewer report への長さ較正 (公式ガイドの
  「Opus 5 の書く文書は長くなりがちで明示較正が必要」への対応)。
- codex-advisor: 8,000 文字注入予算の advisor-rules.md と consult/SKILL.md 間の
  逐語重複の参照化 (checkpoint 4 項目・async_launched 回収手順)。
- ui-discipline: 完了前チェックリストの位置づけ緩和 (Opus 5 の過剰検証誘発対策と
  Sonnet 系の見落とし防止の両立)。
- natsuume-writing: draft skill 一括生成の分量較正。

Phase A で先行 commit され red になり、Phase B のプロンプト修正で green になる。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS = REPO_ROOT / "plugins"

DISCIPLINE_FABLE = PLUGINS / "agent-discipline" / "hooks" / "prompts" / "discipline-fable.md"
DISCIPLINE_OPUS = PLUGINS / "agent-discipline" / "hooks" / "prompts" / "discipline-opus.md"
DISCIPLINE_SONNET = PLUGINS / "agent-discipline" / "hooks" / "prompts" / "discipline-sonnet.md"
ALWAYS_FABLE = PLUGINS / "agent-discipline" / "hooks" / "prompts" / "always-fable.md"
SUBAGENT_RULES = PLUGINS / "agent-discipline" / "hooks" / "prompts" / "subagent-rules.md"
REBASE_SKILL = PLUGINS / "git-guardrails" / "skills" / "rebase-workflow" / "SKILL.md"
CODE_REVIEWER = PLUGINS / "pre-push-review" / "agents" / "code-reviewer.md"
SECURITY_REVIEWER = PLUGINS / "pre-push-review" / "agents" / "security-reviewer.md"
ADVISOR_RULES = PLUGINS / "codex-advisor" / "hooks" / "prompts" / "advisor-rules.md"
CONSULT_SKILL = PLUGINS / "codex-advisor" / "skills" / "consult" / "SKILL.md"
UI_PATTERNS_SKILL = PLUGINS / "ui-discipline" / "skills" / "ui-patterns" / "SKILL.md"
DRAFT_SKILL = PLUGINS / "natsuume-writing" / "skills" / "draft" / "SKILL.md"

THREE_WAY = {
    "discipline-fable.md": DISCIPLINE_FABLE,
    "discipline-opus.md": DISCIPLINE_OPUS,
    "discipline-sonnet.md": DISCIPLINE_SONNET,
}

# 3 ファイルすべてに存在すべき共有 canonical 文 (モデル固有差分ではない委任規律の本体)。
SHARED_CANONICAL_PHRASES = (
    # 並列委任は単一メッセージ内の複数 Agent 呼び出しでのみ成立する (fable 版で欠落していた)
    "同一メッセージで並列に委任し",
    # 全件報告要求の対象カテゴリ (fable 版は「検証」が欠落していた)
    "調査・レビュー・検証系",
    # fork 禁止の正の代替 (fable 版で欠落していた)
    "代わりに、必要な文脈を指示文に埋め込んだ新規起動 (セクション 2 の self-contained 要件) を使う",
    # 特定ツール使用意図の明示の具体例 (opus 版で欠落していた)
    "(例: 必ず WebSearch で最新情報を確認させる)",
    # 目的・背景を渡す意図の限定 (opus 版で欠落していた)
    "目的・背景は判断を委ねる根拠ではなく",
    # 副作用手順固定の例示 (fable 版で欠落していた)
    "state を書くテストは隔離した TMPDIR を env 指定",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class AgentDisciplineThreeWayParityTest(unittest.TestCase):
    """3-way 分業規律間で共有 canonical 文の存在と環境値ハードコードの不在を固定する。"""

    def test_shared_canonical_phrases_exist_in_all_three_files(self) -> None:
        for phrase in SHARED_CANONICAL_PHRASES:
            missing = [
                name for name, path in THREE_WAY.items() if phrase not in read(path)
            ]
            self.assertEqual(
                [], missing, f"共有 canonical 文 {phrase!r} を含まないファイル: {missing}"
            )

    def test_no_environment_effort_value_hardcode(self) -> None:
        """セッション既定 effort の環境固有値 (xhigh) をハードコードしない。

        環境設定の変更でプロンプトが陳腐化し、opus 版の条件形記述と矛盾して
        読めるため (公式ガイドの「矛盾する指示の併存は性能を下げる」)。
        表現の言い換えで値だけ残る経路を塞ぐため、文単位ではなく値トークン
        そのものの不在を検査する (3 ファイルに xhigh の正当な用例は無い)。
        """
        offenders = [
            name for name, path in THREE_WAY.items() if "xhigh" in read(path)
        ]
        self.assertEqual([], offenders, f"環境値ハードコードが残るファイル: {offenders}")


class AlwaysFableAskUserQuestionScopeTest(unittest.TestCase):
    """always-fable.md の AskUserQuestion 必須化ルールに scope 限定 caveat があること。

    always-sonnet-3.md には「質問を作り出さない」caveat があるが fable 版で欠落しており、
    質問過多方向の overtrigger 源になっていた (公式ガイドの強調語 overtrigger 対応)。
    """

    def test_always_fable_contains_question_scope_caveat(self) -> None:
        text = read(ALWAYS_FABLE)
        self.assertIn("質問を作り出さない", text)
        self.assertIn("質問するかどうか」の判断そのものを変えない", text)


class SubagentRulesPipeAllowanceTest(unittest.TestCase):
    """subagent-rules.md の bash-decompose に単一論理操作パイプの許容規定があること。

    main session 側 (always-sonnet-1.md) には許容規定があるが subagent 版に無く、
    literal に従う subagent が正当なパイプまで過剰分解する非対称があった。
    """

    def test_subagent_rules_allow_single_logical_operation_pipes(self) -> None:
        text = read(SUBAGENT_RULES)
        self.assertIn("単一論理操作", text)


class RebaseWorkflowSkillTest(unittest.TestCase):
    """rebase-workflow skill が bash-decompose 規律と整合し stale 対策を持つこと。"""

    def test_no_command_substitution_example(self) -> None:
        """コマンド置換 + 変数連結の一括スクリプト例 (bash-decompose 違反) が無いこと。

        定義行 (`DEFAULT_BRANCH=$(...)`) だけでなく消費側 (`origin/$DEFAULT_BRANCH`)
        の残骸も塞ぐため、変数名トークン自体の不在を検査する。
        """
        text = read(REBASE_SKILL)
        self.assertNotIn("DEFAULT_BRANCH", text)
        self.assertNotIn("$(git", text)
        self.assertNotIn("## 一連のコマンド例", text)

    def test_stale_origin_head_mitigation_precedes_symbolic_ref_read(self) -> None:
        """symbolic-ref の最初の参照より前に fetch --prune / set-head --auto を
        この順で先行させること (update-default-branch skill と同じ stale 対策)。
        存在検査だけでは後段 (トラブルシューティング等) への出現でも green に
        なってしまうため、最初の出現位置の相対順序を固定する。
        """
        text = read(REBASE_SKILL)
        fetch_cmd = "git fetch --prune origin"
        set_head_cmd = "git remote set-head origin --auto"
        symref_cmd = "git symbolic-ref refs/remotes/origin/HEAD"
        for command in (fetch_cmd, set_head_cmd, symref_cmd):
            self.assertIn(command, text)
        self.assertLess(
            text.index(fetch_cmd),
            text.index(set_head_cmd),
            "fetch --prune が set-head --auto より前に無い",
        )
        self.assertLess(
            text.index(set_head_cmd),
            text.index(symref_cmd),
            "set-head --auto が最初の symbolic-ref 参照より前に無い",
        )


class PrePushReviewerLengthCalibrationTest(unittest.TestCase):
    """Opus 5 固定の reviewer 2 体の report contract に長さ較正があること。"""

    def test_reviewers_calibrate_report_length(self) -> None:
        """report contract の自由記述フィールドに 1 文較正を課す文の存在を、
        意図を特定できる長いフレーズで検査する (短い汎用句だと無関係な出現でも
        green になるため)。subTest は使わない (runner による報告差異を避ける)。
        """
        phrase = (
            "each free-text field (Cause class, Violated invariant, Impact, "
            "Fix direction) as a single sentence"
        )
        missing = [
            name
            for name, path in (
                ("code-reviewer.md", CODE_REVIEWER),
                ("security-reviewer.md", SECURITY_REVIEWER),
            )
            if phrase not in read(path)
        ]
        self.assertEqual([], missing, f"長さ較正文を含まない reviewer: {missing}")


class CodexAdvisorDeduplicationTest(unittest.TestCase):
    """advisor-rules.md (8K 注入予算) と consult/SKILL.md の逐語重複が参照化されたこと。"""

    def test_checkpoint_items_not_duplicated_verbatim(self) -> None:
        """checkpoint 4 項目の箇条書きは consult/SKILL.md を正本とし、
        advisor-rules.md 側は逐語複製しない。1 項目だけ消して 3 項目の
        中途半端な列挙が残る経路を塞ぐため、4 項目すべての不在を検査する。
        """
        text = read(ADVISOR_RULES)
        leftovers = [
            bullet
            for bullet in (
                "- 元の Goal、受入基準、変えてはならない制約",
                "- 直近 5 サイクルの主要 findings、施した修正、反復している傾向",
                "- 現在の問題設定・仮説・アプローチと、残っている不確実性",
                "- 「局所修正を続けるべきか、根本方針・設計境界・検証戦略を変えるべきか」という 1 つの質問",
            )
            if bullet in text
        ]
        self.assertEqual([], leftovers, f"advisor-rules.md に残る重複 bullet: {leftovers}")

    def test_async_recovery_narrative_not_duplicated_but_residual_kept(self) -> None:
        """async_launched 時の結果回収手順の詳細は consult/SKILL.md の
        Claude Code host 節を正本とし、advisor-rules.md 側は逐語複製しない。
        ただし advisor-rules.md は consult skill を経由しない直接 runner 起動
        (rescue / review) も認めるため、常時注入側に「terminal report が返るまで
        完了報告しない」という義務の残余文を必ず残す (除去のみだと skill 未ロード
        経路で回収義務が context から消える)。
        """
        text = read(ADVISOR_RULES)
        self.assertNotIn("completion notification または TaskOutput を回収", text)
        self.assertIn("terminal report が返るまで", text)

    def test_advisor_rules_points_to_consult_definition(self) -> None:
        """advisor-rules.md は checkpoint 項目の定義を consult skill へ、正本の
        識別子 (`/codex-advisor:consult`) 付きの参照で委ねる。
        """
        text = read(ADVISOR_RULES)
        self.assertIn("が定義する 4 項目", text)
        self.assertIn("省略せず含める", text)
        self.assertIn("`/codex-advisor:consult` が定義する", text)

    def test_consult_skill_remains_canonical_for_checkpoint(self) -> None:
        """正本側 (consult/SKILL.md) の checkpoint テンプレート全フィールドと
        async 回収手順が維持される (advisor-rules.md 側を参照化した後に正本側が
        縮退すると、参照先の定義ごと消える事故を塞ぐ positive assertion)。
        """
        text = read(CONSULT_SKILL)
        missing = [
            element
            for element in (
                "<review_cycle_checkpoint>",
                "<goal_and_acceptance>",
                "<constraints>",
                "<review_history>",
                "<current_strategy>",
                "<question>",
                "TaskOutput",
                "terminal report",
            )
            if element not in text
        ]
        self.assertEqual([], missing, f"consult/SKILL.md から欠落した正本要素: {missing}")

    def test_advisor_rules_keeps_launch_safety_essentials(self) -> None:
        """参照化後も安全上必須の起動指定 (model / run_in_background) は本文に残す。"""
        text = read(ADVISOR_RULES)
        self.assertIn('`model: "sonnet"`', text)
        self.assertIn('`run_in_background: false`', text)


class UiPatternsChecklistReframingTest(unittest.TestCase):
    """完了前チェックリストが「該当ルールのみの再確認」に位置づけ直されたこと。

    「実装完了前の総合チェックリスト」は Opus 5 に過剰検証 (公式が除去推奨する
    「最後に検証ステップ」型) を誘発しうるため、全項目の機械的確認を求めない形にする。
    """

    def test_checklist_heading_reframed(self) -> None:
        text = read(UI_PATTERNS_SKILL)
        self.assertNotIn("実装完了前の総合チェックリスト", text)
        self.assertIn("見落としやすい項目の再確認", text)

    def test_checklist_scopes_to_touched_rules_only(self) -> None:
        text = read(UI_PATTERNS_SKILL)
        self.assertIn("触れたルールに対応する項目だけ", text)


class NatsuumeWritingDraftCalibrationTest(unittest.TestCase):
    """draft skill の一括生成に分量較正があること (Opus 5 の文書肥大対策)。"""

    def test_draft_skill_calibrates_generated_length(self) -> None:
        text = read(DRAFT_SKILL)
        self.assertIn("分量はコメントの指示量に比例", text)


if __name__ == "__main__":
    unittest.main()

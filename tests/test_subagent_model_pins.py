"""Claude 5 世代モデル対応: subagent の model 固定と起動仕様の model 明示の契約テスト。

pre-push-review / codex-advisor の各 subagent は現在 `model: inherit` で起動されており、
親セッションのモデルをそのまま継承する。Claude 5 世代対応として、レビュー品質が重要な
code-reviewer / security-reviewer は `model: opus` に固定し (effort は Claude Opus 5
System Card 準拠の撤廃により pin せずセッション既定を継承する)、
wrapper 起動や job tracking が主目的の codex-reviewer / codex-advisor の 3 runner は
`model: sonnet` に固定する。あわせて、起動仕様 (両 plugin の review.md の Agent 起動、
block-pre-push.sh / block-pre-push-codex.sh / block-bg-codex-wrapper.sh の deny メッセージ、
manage-codex-runners.mjs の deny メッセージ、codex-advisor のドキュメント群) にも
正準文字列 `model: "opus"` / `model: "sonnet"` を明示し、親セッションや利用者が
起動時に期待すべきモデルを迷わず判断できるようにする。

reviewer の自己フィルタ (`Identify ONLY` / `Drop anything`) も廃止し、code-reviewer /
security-reviewer は検出した finding を confidence 込みで全件報告し、選別は親セッションの
分類パスに委ねる契約へ変更する。review.md の修正フローには low confidence finding の
分類指針 (`Confidence: low`) を追加する。

codex review 経路は plugin `pre-push-codex-review` が所有するため、codex-reviewer の
agent 定義・起動仕様・deny メッセージの検証対象はそちらの plugin を指す。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRE_PUSH = ROOT / "plugins" / "pre-push-review"
PRE_PUSH_CODEX = ROOT / "plugins" / "pre-push-codex-review"
CODEX_ADVISOR = ROOT / "plugins" / "codex-advisor"

CODE_REVIEWER = PRE_PUSH / "agents" / "code-reviewer.md"
SECURITY_REVIEWER = PRE_PUSH / "agents" / "security-reviewer.md"
CODEX_REVIEWER = PRE_PUSH_CODEX / "agents" / "codex-reviewer.md"
REVIEW_COMMAND = PRE_PUSH / "commands" / "review.md"
CODEX_REVIEW_COMMAND = PRE_PUSH_CODEX / "commands" / "review.md"
BLOCK_PRE_PUSH = PRE_PUSH / "hooks" / "scripts" / "block-pre-push.sh"
BLOCK_PRE_PUSH_CODEX = PRE_PUSH_CODEX / "hooks" / "scripts" / "block-pre-push-codex.sh"
BLOCK_BG_CODEX_WRAPPER = (
    PRE_PUSH_CODEX / "hooks" / "scripts" / "block-bg-codex-wrapper.sh"
)

ADVISOR_RUNNER = CODEX_ADVISOR / "agents" / "advisor-runner.md"
RESCUE_RUNNER = CODEX_ADVISOR / "agents" / "rescue-runner.md"
REVIEW_RUNNER = CODEX_ADVISOR / "agents" / "review-runner.md"
MANAGE_CODEX_RUNNERS = CODEX_ADVISOR / "hooks" / "scripts" / "manage-codex-runners.mjs"
CONSULT_SKILL = CODEX_ADVISOR / "skills" / "consult" / "SKILL.md"
ADVISOR_RULES = CODEX_ADVISOR / "hooks" / "prompts" / "advisor-rules.md"
ADVISOR_RULES_SUBAGENT = CODEX_ADVISOR / "hooks" / "prompts" / "advisor-rules-subagent.md"

# 変更仕様 1: agent frontmatter の model 固定対象。effort pin は v5.3.0 で撤廃 (System Card 準拠)。
OPUS_AGENTS = {
    "code-reviewer": CODE_REVIEWER,
    "security-reviewer": SECURITY_REVIEWER,
}
SONNET_AGENTS = {
    "codex-reviewer": CODEX_REVIEWER,
    "advisor-runner": ADVISOR_RUNNER,
    "rescue-runner": RESCUE_RUNNER,
    "review-runner": REVIEW_RUNNER,
}
ALL_PINNED_AGENTS = {**OPUS_AGENTS, **SONNET_AGENTS}

# 変更仕様 2: 各 review.md の起動仕様が近傍に明示すべき model。core の
# `/pre-push-review:review` は 2 起動仕様、codex 経路を持つ
# `/pre-push-codex-review:review` は core 併用時の 3 起動仕様を発出する。
CORE_LAUNCH_MODELS = {
    "pre-push-review:code-reviewer": "opus",
    "pre-push-review:security-reviewer": "opus",
}
CODEX_LAUNCH_MODELS = {
    "pre-push-review:code-reviewer": "opus",
    "pre-push-review:security-reviewer": "opus",
    "pre-push-codex-review:codex-reviewer": "sonnet",
}
LAUNCH_SPEC_COMMANDS = {
    REVIEW_COMMAND: CORE_LAUNCH_MODELS,
    CODEX_REVIEW_COMMAND: CODEX_LAUNCH_MODELS,
}

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

# effort キーの負の検査 (不在確認) 用。正の検査 (model: opus の存在) は正準表記を
# リポジトリ側が所有するためリテラル一致でよいが、不在確認は YAML の書式揺れ
# (キー前後の空白・引用符付きキー) を伴う同義の pin を見逃さないようキー検出の
# 正規表現で行う (完全な YAML 意味解析ではなく、平坦な agent frontmatter の
# 通常の書式揺れを対象とする契約)。
EFFORT_KEY_PATTERN = re.compile(r"^\s*[\"']?effort[\"']?\s*:")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter_lines(body: str) -> list[str]:
    """最初の `---` から次の `---` までを frontmatter として抽出し、行のリストで返す。"""
    match = FRONTMATTER_PATTERN.match(body)
    if match is None:
        return []
    return match.group(1).splitlines()


def body_after_frontmatter(body: str) -> str:
    """frontmatter (2 つ目の `---` まで) を除いた本文を返す。"""
    match = FRONTMATTER_PATTERN.match(body)
    if match is None:
        return body
    return body[match.end() :]


def exclude_frontmatter_description_line(body: str) -> str:
    """frontmatter の `description:` 行 (marketplace 向け説明文) だけを除いた全文を返す。

    code-reviewer.md の frontmatter description は「検出された high-confidence bug を
    markdown report として親 session に返す。」という既存のマーケティング文言を含む。
    これは変更仕様 1 (frontmatter の `model` 固定) の対象であって変更仕様 3
    (reviewer 本文の自己フィルタ廃止) の対象ではないため、confidence-gating パターン検査
    からこの 1 行だけを除外する (この行以外の frontmatter・本文は検査対象に残す)。
    """
    return "\n".join(
        line for line in body.splitlines() if not line.startswith("description:")
    )


# 変更仕様 3 拡張 (codex review P2 網羅性指摘): `Identify ONLY` / `Drop anything` という
# 直接的な言い回し以外の confidence-gating 表現。大文字小文字を無視して検索する。
CONFIDENCE_GATING_PATTERNS = (
    "high-confidence",
    "8/10",
    "only report",
    "report only",
    "below the threshold",
)


class AgentFrontmatterModelPinTest(unittest.TestCase):
    """変更仕様 1: 6 agent の frontmatter model 固定。"""

    def test_opus_reviewers_pin_model_opus_without_effort_pin(self) -> None:
        """model: opus は維持し、effort は pin せずセッション既定を継承させる
        (Claude Opus 5 System Card の effort スケーリング実測に基づく撤廃)。
        """
        for name, path in OPUS_AGENTS.items():
            with self.subTest(agent=name):
                lines = frontmatter_lines(read(path))
                self.assertIn("model: opus", lines, path)
                effort_lines = [line for line in lines if EFFORT_KEY_PATTERN.match(line)]
                self.assertEqual([], effort_lines, path)

    def test_sonnet_agents_pin_model_sonnet(self) -> None:
        for name, path in SONNET_AGENTS.items():
            with self.subTest(agent=name):
                lines = frontmatter_lines(read(path))
                self.assertIn("model: sonnet", lines, path)

    def test_no_pinned_agent_frontmatter_retains_model_inherit(self) -> None:
        for name, path in ALL_PINNED_AGENTS.items():
            with self.subTest(agent=name):
                lines = frontmatter_lines(read(path))
                self.assertNotIn("model: inherit", lines, path)


class ReviewCommandLaunchSpecModelAnnotationTest(unittest.TestCase):
    """変更仕様 2: 両 plugin の review.md の起動仕様が同一行内 60 文字以内で model を明示する。"""

    def test_launch_spec_declares_model_within_60_chars_of_subagent_type(self) -> None:
        for command, launch_models in LAUNCH_SPEC_COMMANDS.items():
            body = read(command)
            for subagent_type, model in launch_models.items():
                with self.subTest(
                    command=str(command.relative_to(ROOT)),
                    subagent_type=subagent_type,
                    model=model,
                ):
                    pattern = (
                        rf'subagent_type: "{re.escape(subagent_type)}".{{0,60}}'
                        rf'model: "{model}"'
                    )
                    self.assertIsNotNone(
                        re.search(pattern, body),
                        f"{subagent_type} launch spec is missing a nearby "
                        f'model: "{model}" in {command}',
                    )


class BlockPrePushMarkerTableModelAnnotationTest(unittest.TestCase):
    """変更仕様 2: 各 push gate の deny メッセージが reviewer ごとの model を明示する。

    core の block-pre-push.sh は code / security の 2 マーカー対応表を、codex 経路の
    block-pre-push-codex.sh は codex-reviewer の単独再走案内を持ち、どちらも起動時に
    指定すべき model をその場で明示する。
    """

    def test_marker_correspondence_table_declares_model_per_reviewer(self) -> None:
        body = read(BLOCK_PRE_PUSH)
        expected_literals = (
            'subagent_type="pre-push-review:code-reviewer", model="opus"',
            'subagent_type="pre-push-review:security-reviewer", model="opus"',
        )
        for literal in expected_literals:
            with self.subTest(literal=literal):
                self.assertIn(literal, body)

    def test_codex_gate_deny_message_declares_codex_reviewer_model(self) -> None:
        self.assertIn(
            'subagent_type="pre-push-codex-review:codex-reviewer", model="sonnet"',
            read(BLOCK_PRE_PUSH_CODEX),
        )


class BlockBgCodexWrapperModelAnnotationTest(unittest.TestCase):
    """変更仕様 2: block-bg-codex-wrapper.sh の deny メッセージが codex-reviewer の model を明示する。"""

    def test_agent_type_gate_deny_message_declares_codex_reviewer_model(self) -> None:
        body = read(BLOCK_BG_CODEX_WRAPPER)
        self.assertIn(
            'subagent_type="pre-push-codex-review:codex-reviewer", model="sonnet"', body
        )

    def test_restart_guidance_lines_naming_codex_reviewer_declare_model(self) -> None:
        body = read(BLOCK_BG_CODEX_WRAPPER)
        matching_lines = [
            line
            for line in body.splitlines()
            if "codex-reviewer" in line and "を再起動してください" in line
        ]
        self.assertTrue(
            matching_lines,
            "no line mentions both codex-reviewer and を再起動してください",
        )
        for line in matching_lines:
            with self.subTest(line=line):
                self.assertIn("model", line)

    def test_both_deny_paths_declare_codex_reviewer_model(self) -> None:
        """codex review P2 網羅性指摘: agent_type 欠落経路と不一致経路の両方が model を持つ。

        両 deny メッセージ (agent_type 欠落 / agent_type 不一致) はそれぞれ独立した
        REASON heredoc であり、どちらも
        `subagent_type="pre-push-codex-review:codex-reviewer", model="sonnet"` の
        案内を含む必要がある。1 回だけの出現では欠落経路のみ・
        不一致経路のみのいずれかが model 注記を欠いたまま残っている可能性を見逃すため、
        出現回数を 2 以上で assert する。
        """
        body = read(BLOCK_BG_CODEX_WRAPPER)
        literal = 'subagent_type="pre-push-codex-review:codex-reviewer", model="sonnet"'
        self.assertGreaterEqual(body.count(literal), 2)

    def test_no_model_less_legacy_restart_guidance_remains(self) -> None:
        """model 注記の無い旧形式の起動案内が残っていないことを確認する。"""
        self.assertNotIn(
            'subagent_type="pre-push-codex-review:codex-reviewer" を起動',
            read(BLOCK_BG_CODEX_WRAPPER),
        )


class ManageCodexRunnersModelAnnotationTest(unittest.TestCase):
    """変更仕様 2: manage-codex-runners.mjs の deny メッセージが sonnet model を明示する。"""

    def test_escaped_template_literal_declares_sonnet_model(self) -> None:
        body = read(MANAGE_CODEX_RUNNERS)
        # JS template literal (backtick文字列) 内のエスケープされた二重引用符付き literal。
        self.assertIn(r'model=\"sonnet\"', body)

    def test_unescaped_literal_declares_sonnet_model(self) -> None:
        body = read(MANAGE_CODEX_RUNNERS)
        self.assertIn('model: "sonnet"', body)

    def test_no_model_less_escaped_legacy_literal_remains(self) -> None:
        """codex review P2 網羅性指摘: model 注記の無い旧形式 (escaped) が残っていない。"""
        body = read(MANAGE_CODEX_RUNNERS)
        self.assertNotIn(r'subagent_type=\"${expectedRunner}\", run_in_background', body)

    def test_no_model_less_unescaped_legacy_literal_remains(self) -> None:
        """codex review P2 網羅性指摘: model 注記の無い旧形式 (unescaped) が残っていない。"""
        body = read(MANAGE_CODEX_RUNNERS)
        self.assertNotIn("subagent_type に指定し、run_in_background: false で", body)

    def test_user_facing_run_in_background_instruction_lines_declare_model(
        self,
    ) -> None:
        """`run_in_background: false` のユーザ向け指示文行は全て model を含む。

        行の判別は元指定の 2 部分文字列 (`run_in_background: false を指定` /
        `run_in_background: false で`) の有無で行う。コード内の Boolean 比較行
        (`input.tool_input?.run_in_background === true`) はこの部分文字列を含まない
        (`false` を含まない) ため自動的に除外され、追加の判別ロジックは不要だった。
        """
        body = read(MANAGE_CODEX_RUNNERS)
        instruction_markers = (
            "run_in_background: false を指定",
            "run_in_background: false で",
        )
        matching_lines = [
            line
            for line in body.splitlines()
            if any(marker in line for marker in instruction_markers)
        ]
        self.assertTrue(
            matching_lines,
            "no user-facing run_in_background instruction line was found",
        )
        for line in matching_lines:
            with self.subTest(line=line):
                self.assertIn("model", line)


class CodexAdvisorDocsModelAnnotationTest(unittest.TestCase):
    """変更仕様 2: codex-advisor のドキュメント群と 3 runner 本文が sonnet model を明示する。"""

    def test_consult_skill_declares_sonnet_model_bullet(self) -> None:
        body = read(CONSULT_SKILL)
        self.assertIn('- model: "sonnet"', body)

    def test_advisor_rules_declares_sonnet_model(self) -> None:
        body = read(ADVISOR_RULES)
        self.assertIn('model: "sonnet"', body)

    def test_advisor_rules_subagent_declares_sonnet_model(self) -> None:
        body = read(ADVISOR_RULES_SUBAGENT)
        self.assertIn('model: "sonnet"', body)

    def test_runner_agent_bodies_declare_parent_facing_sonnet_model_contract(
        self,
    ) -> None:
        for name, path in (
            ("advisor-runner", ADVISOR_RUNNER),
            ("rescue-runner", RESCUE_RUNNER),
            ("review-runner", REVIEW_RUNNER),
        ):
            with self.subTest(runner=name):
                body = body_after_frontmatter(read(path))
                self.assertIn('model: "sonnet"', body, path)


class ReviewerSelfFilterRemovalTest(unittest.TestCase):
    """変更仕様 3: reviewer の自己フィルタ廃止 (全件報告 + confidence 較正)。"""

    def test_code_and_security_reviewers_report_all_findings_without_self_filtering(
        self,
    ) -> None:
        for name, path in OPUS_AGENTS.items():
            with self.subTest(reviewer=name):
                body = read(path)
                self.assertIn(
                    "Do not self-filter by confidence or severity; selection "
                    "happens in the parent session's classification pass.",
                    body,
                )
                self.assertNotIn("Identify ONLY", body)
                self.assertNotIn("Drop anything", body)

    def test_reviewers_do_not_use_confidence_gating_language(self) -> None:
        """codex review P2 網羅性指摘: `Identify ONLY`/`Drop anything` 以外の

        confidence-gating 表現 (`high-confidence`/`8/10`/`only report`/`report only`/
        `below the threshold`、大文字小文字無視) も残っていないことを確認する。

        code-reviewer.md の frontmatter description 行は変更仕様 3 の対象外の
        マーケティング文言 (`high-confidence bug`) を含むため、
        `exclude_frontmatter_description_line` でその 1 行のみ除外する。本文冒頭の
        「Your job is to find HIGH-CONFIDENCE ... bugs/vulnerabilities」という導入文は
        検査対象に残るため、この行も含めて自己フィルタ廃止の趣旨に沿った書き換えが
        Phase B に要求される (既存の変更仕様 3 が列挙する `Identify ONLY`/`Drop anything`
        の削除だけでは、本チェックは green にならない)。
        """
        for name, path in OPUS_AGENTS.items():
            body = exclude_frontmatter_description_line(read(path))
            for pattern in CONFIDENCE_GATING_PATTERNS:
                with self.subTest(reviewer=name, pattern=pattern):
                    match = re.search(re.escape(pattern), body, re.IGNORECASE)
                    self.assertIsNone(
                        match,
                        f"{name} still uses confidence-gating language: {pattern!r}",
                    )

    def test_review_command_fix_flow_defines_low_confidence_classification(
        self,
    ) -> None:
        body = read(REVIEW_COMMAND)
        match = re.search(
            r"## レビュー指摘の修正フロー.*?(?:\n## |\Z)", body, flags=re.DOTALL
        )
        self.assertIsNotNone(match, "レビュー指摘の修正フロー section is missing")
        assert match is not None
        self.assertIn("Confidence: low", match.group(0))


if __name__ == "__main__":
    unittest.main()

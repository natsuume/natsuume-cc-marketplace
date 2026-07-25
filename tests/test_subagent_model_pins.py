"""Claude 5 世代モデル対応: subagent の model 固定と起動仕様の model 明示の契約テスト。

pre-push-review / codex-advisor の各 subagent は現在 `model: inherit` で起動されており、
親セッションのモデルをそのまま継承する。Claude 5 世代対応として、レビュー品質が重要な
code-reviewer / security-reviewer は `model: opus` + `effort: medium` に固定し、
wrapper 起動や job tracking が主目的の codex-reviewer / codex-advisor の 3 runner は
`model: sonnet` に固定する。あわせて、起動仕様 (review.md の Agent 起動、
block-pre-push.sh / block-bg-codex-wrapper.sh の deny メッセージ、
manage-codex-runners.mjs の deny メッセージ、codex-advisor のドキュメント群) にも
正準文字列 `model: "opus"` / `model: "sonnet"` を明示し、親セッションや利用者が
起動時に期待すべきモデルを迷わず判断できるようにする。

reviewer の自己フィルタ (`Identify ONLY` / `Drop anything`) も廃止し、code-reviewer /
security-reviewer は検出した finding を confidence 込みで全件報告し、選別は親セッションの
分類パスに委ねる契約へ変更する。review.md の修正フローには low confidence finding の
分類指針 (`Confidence: low`) を追加する。

本テストは spec-first Phase A で実装前の red テストとして追加する。Phase B で上記の
frontmatter・起動仕様・reviewer 本文が実装されると green になる。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRE_PUSH = ROOT / "plugins" / "pre-push-review"
CODEX_ADVISOR = ROOT / "plugins" / "codex-advisor"

CODE_REVIEWER = PRE_PUSH / "agents" / "code-reviewer.md"
SECURITY_REVIEWER = PRE_PUSH / "agents" / "security-reviewer.md"
CODEX_REVIEWER = PRE_PUSH / "agents" / "codex-reviewer.md"
REVIEW_COMMAND = PRE_PUSH / "commands" / "review.md"
BLOCK_PRE_PUSH = PRE_PUSH / "hooks" / "scripts" / "block-pre-push.sh"
BLOCK_BG_CODEX_WRAPPER = PRE_PUSH / "hooks" / "scripts" / "block-bg-codex-wrapper.sh"

ADVISOR_RUNNER = CODEX_ADVISOR / "agents" / "advisor-runner.md"
RESCUE_RUNNER = CODEX_ADVISOR / "agents" / "rescue-runner.md"
REVIEW_RUNNER = CODEX_ADVISOR / "agents" / "review-runner.md"
MANAGE_CODEX_RUNNERS = CODEX_ADVISOR / "hooks" / "scripts" / "manage-codex-runners.mjs"
CONSULT_SKILL = CODEX_ADVISOR / "skills" / "consult" / "SKILL.md"
ADVISOR_RULES = CODEX_ADVISOR / "hooks" / "prompts" / "advisor-rules.md"
ADVISOR_RULES_SUBAGENT = CODEX_ADVISOR / "hooks" / "prompts" / "advisor-rules-subagent.md"

# 変更仕様 1: agent frontmatter の model 固定対象。
OPUS_EFFORT_AGENTS = {
    "code-reviewer": CODE_REVIEWER,
    "security-reviewer": SECURITY_REVIEWER,
}
SONNET_AGENTS = {
    "codex-reviewer": CODEX_REVIEWER,
    "advisor-runner": ADVISOR_RUNNER,
    "rescue-runner": RESCUE_RUNNER,
    "review-runner": REVIEW_RUNNER,
}
ALL_PINNED_AGENTS = {**OPUS_EFFORT_AGENTS, **SONNET_AGENTS}

# 変更仕様 2: review.md の起動仕様が近傍に明示すべき model。
REVIEWER_LAUNCH_MODELS = {
    "code-reviewer": "opus",
    "security-reviewer": "opus",
    "codex-reviewer": "sonnet",
}

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


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


class AgentFrontmatterModelPinTest(unittest.TestCase):
    """変更仕様 1: 6 agent の frontmatter model 固定。"""

    def test_opus_reviewers_pin_model_opus_with_medium_effort(self) -> None:
        for name, path in OPUS_EFFORT_AGENTS.items():
            with self.subTest(agent=name):
                lines = frontmatter_lines(read(path))
                self.assertIn("model: opus", lines, path)
                self.assertIn("effort: medium", lines, path)

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
    """変更仕様 2: review.md の 3 起動仕様が同一行内 60 文字以内で model を明示する。"""

    def test_launch_spec_declares_model_within_60_chars_of_subagent_type(self) -> None:
        body = read(REVIEW_COMMAND)
        for reviewer, model in REVIEWER_LAUNCH_MODELS.items():
            with self.subTest(reviewer=reviewer, model=model):
                pattern = (
                    rf'subagent_type: "pre-push-review:{reviewer}".{{0,60}}'
                    rf'model: "{model}"'
                )
                self.assertIsNotNone(
                    re.search(pattern, body),
                    f"{reviewer} launch spec is missing a nearby model: \"{model}\"",
                )


class BlockPrePushMarkerTableModelAnnotationTest(unittest.TestCase):
    """変更仕様 2: block-pre-push.sh の deny メッセージ内マーカー対応表が model を明示する。"""

    def test_marker_correspondence_table_declares_model_per_reviewer(self) -> None:
        body = read(BLOCK_PRE_PUSH)
        expected_literals = (
            'subagent_type="pre-push-review:code-reviewer", model="opus"',
            'subagent_type="pre-push-review:codex-reviewer", model="sonnet"',
            'subagent_type="pre-push-review:security-reviewer", model="opus"',
        )
        for literal in expected_literals:
            with self.subTest(literal=literal):
                self.assertIn(literal, body)


class BlockBgCodexWrapperModelAnnotationTest(unittest.TestCase):
    """変更仕様 2: block-bg-codex-wrapper.sh の deny メッセージが codex-reviewer の model を明示する。"""

    def test_agent_type_gate_deny_message_declares_codex_reviewer_model(self) -> None:
        body = read(BLOCK_BG_CODEX_WRAPPER)
        self.assertIn(
            'subagent_type="pre-push-review:codex-reviewer", model="sonnet"', body
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


class ManageCodexRunnersModelAnnotationTest(unittest.TestCase):
    """変更仕様 2: manage-codex-runners.mjs の deny メッセージが sonnet model を明示する。"""

    def test_escaped_template_literal_declares_sonnet_model(self) -> None:
        body = read(MANAGE_CODEX_RUNNERS)
        # JS template literal (backtick文字列) 内のエスケープされた二重引用符付き literal。
        self.assertIn(r'model=\"sonnet\"', body)

    def test_unescaped_literal_declares_sonnet_model(self) -> None:
        body = read(MANAGE_CODEX_RUNNERS)
        self.assertIn('model: "sonnet"', body)


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
        for name, path in OPUS_EFFORT_AGENTS.items():
            with self.subTest(reviewer=name):
                body = read(path)
                self.assertIn(
                    "Do not self-filter by confidence or severity; selection "
                    "happens in the parent session's classification pass.",
                    body,
                )
                self.assertNotIn("Identify ONLY", body)
                self.assertNotIn("Drop anything", body)

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

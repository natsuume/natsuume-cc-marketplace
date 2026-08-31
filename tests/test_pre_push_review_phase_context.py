"""pre-push-review / pre-push-codex-review の Phase 文脈伝達契約テスト。

spec-first 2 段階の Phase A を通常の実装 diff として reviewer が解釈すると、
テスト一括先行そのものを finding にするノイズが生じる。呼び出し側が判定した
Phase 文脈を code / security reviewer にだけ渡し、判定不能時と Codex review は
従来の prompt を保つ契約を固定する。

core の `/pre-push-review:review` は code / security の 2 subagent を発出し、
codex 経路を持つ `/pre-push-codex-review:review` は core 併用時に codex-reviewer を
加えた 3 subagent を発出する。Phase 文脈は両 command で code / security にのみ渡り、
codex-reviewer の prompt には渡らない。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_COMMAND = ROOT / "plugins" / "pre-push-review" / "commands" / "review.md"
CODEX_COMMAND = ROOT / "plugins" / "pre-push-codex-review" / "commands" / "review.md"
PHASE_PLACEHOLDER = "{{PHASE_CONTEXT}}"


class ReviewPhaseContextContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bodies = {
            "pre-push-review": CORE_COMMAND.read_text(encoding="utf-8"),
            "pre-push-codex-review": CODEX_COMMAND.read_text(encoding="utf-8"),
        }

    def prompt_line(self, body: str, subagent_type: str) -> str:
        match = re.search(
            rf'^\d+\. \*\*Agent / Task tool\*\*:.*'
            rf'subagent_type: "{re.escape(subagent_type)}".*$',
            body,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match, f"{subagent_type} launch instruction is missing")
        return match.group(0)

    def test_caller_determines_phase_without_blocking_unknown_work(self) -> None:
        for plugin, body in self.bodies.items():
            with self.subTest(plugin=plugin):
                self.assertIn("呼び出し側", body)
                self.assertIn("Phase A / Phase B / 判定不能", body)
                self.assertIn("判定不能の場合", body)
                self.assertIn("空文字列", body)
                self.assertIn("AskUserQuestion", body)

    def test_phase_a_context_excludes_test_first_workflow_from_findings(self) -> None:
        for plugin, body in self.bodies.items():
            with self.subTest(plugin=plugin):
                self.assertIn("Phase A", body)
                self.assertIn("spec-first 2 段階", body)
                self.assertIn("テスト一括先行", body)
                self.assertIn("ワークフローの仕様", body)
                self.assertIn("指摘対象外", body)

    def test_only_code_and_security_prompts_receive_phase_context(self) -> None:
        for plugin, body in self.bodies.items():
            for reviewer in ("code-reviewer", "security-reviewer"):
                with self.subTest(plugin=plugin, reviewer=reviewer):
                    self.assertIn(
                        PHASE_PLACEHOLDER,
                        self.prompt_line(body, f"pre-push-review:{reviewer}"),
                    )

        codex_body = self.bodies["pre-push-codex-review"]
        self.assertNotIn(
            PHASE_PLACEHOLDER,
            self.prompt_line(codex_body, "pre-push-codex-review:codex-reviewer"),
        )
        self.assertNotIn("codex-reviewer", self.bodies["pre-push-review"])

    def test_phase_b_context_is_defined(self) -> None:
        for plugin, body in self.bodies.items():
            with self.subTest(plugin=plugin):
                self.assertIn("Phase B", body)
                self.assertIn("実装本体", body)


if __name__ == "__main__":
    unittest.main()

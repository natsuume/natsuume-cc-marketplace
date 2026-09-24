"""reviewer / runner の起動案内が、agent-discipline の内部挙動を理由に引用せずに model の明示を求めることを検査する。

pre-push-review / pre-push-codex-review / pre-merge-codex-review / cross-model-advisor の起動案内は、
「この model で起動する」という自 plugin の契約として model の明示を求める。model 未指定の
起動が Fable セッションで agent-discipline の hook に deny されることを理由として書かない。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

# 起動案内のファイルと、残すべき model 明示の指示文。
MODEL_INSTRUCTIONS = {
    PLUGINS / "pre-push-review" / "hooks" / "scripts" / "block-pre-push.sh": (
        "上記の model を常に明示してください。"
    ),
    PLUGINS / "pre-push-codex-review" / "hooks" / "scripts" / "block-pre-push-codex.sh": (
        "上記の model を常に明示してください。"
    ),
    PLUGINS / "pre-merge-codex-review" / "hooks" / "scripts" / "block-pre-merge.sh": (
        "上記の model を常に明示してください。"
    ),
    PLUGINS / "cross-model-advisor" / "hooks" / "prompts" / "advisor-rules.md": (
        'Codex 側 runner の Agent call は `model: "sonnet"` を明示する'
    ),
    PLUGINS / "pre-push-review" / "commands" / "review.md": (
        "単独再起動時も上記 2 起動仕様と同じ model 指定を必ず添えてください。"
    ),
    PLUGINS / "pre-push-codex-review" / "commands" / "review.md": (
        "単独再起動時も上記の起動仕様と同じ model 指定を必ず添えてください。"
    ),
}

# model 未指定の起動が Fable セッションで deny されることを理由にした記述。
FORBIDDEN_RATIONALES = (
    "Fable セッションでは agent-discipline の hook に deny",
    "未指定の継承は Fable セッションで deny",
    "model 未指定の起動は Fable セッション",
    "model 未指定の Agent 起動は Fable セッション",
)


class ModelInstructionWithoutAgentDisciplineRationaleTest(unittest.TestCase):
    def test_launch_guidance_keeps_model_instruction(self) -> None:
        for path, instruction in MODEL_INSTRUCTIONS.items():
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertIn(instruction, path.read_text(encoding="utf-8"))

    def test_launch_guidance_does_not_cite_agent_discipline_inheritance_deny(self) -> None:
        targets = [
            path
            for plugin in (
                "pre-push-review",
                "pre-push-codex-review",
                "pre-merge-codex-review",
                "cross-model-advisor",
            )
            for path in (PLUGINS / plugin).rglob("*")
            if path.is_file() and path.suffix in {".sh", ".md", ".json"}
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8", errors="replace")
            for phrase in FORBIDDEN_RATIONALES:
                with self.subTest(path=path.relative_to(ROOT).as_posix(), phrase=phrase):
                    self.assertNotIn(phrase, text)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "fable-risk-labeler"
SKILL = PLUGIN / "skills" / "label-issues" / "SKILL.md"
RUBRIC = SKILL.parent / "references" / "fable-risk-rubric.md"
OPENAI_YAML = SKILL.parent / "agents" / "openai.yaml"
LABEL = "model:prefer-gpt-5.6-sol"


class FableRiskLabelerContractTest(unittest.TestCase):
    def test_plugin_is_published_to_codex_at_version_0_1_0(self) -> None:
        marketplace = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        entry = next(
            plugin
            for plugin in marketplace["plugins"]
            if plugin["name"] == "fable-risk-labeler"
        )
        self.assertEqual("0.1.0", entry["version"])

        manifest = json.loads(
            (PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual("0.1.0", manifest["version"])

        overrides = json.loads(
            (ROOT / "codex" / "marketplace-overrides.json").read_text(
                encoding="utf-8"
            )
        )["plugins"]["fable-risk-labeler"]
        self.assertEqual("available", overrides["distribution"]["status"])
        self.assertEqual("0.1.0", overrides["version"])

        codex_manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("0.1.0", codex_manifest["version"])
        self.assertEqual("./skills/", codex_manifest["skills"])
        self.assertEqual(
            "Fable Risk Labeler", codex_manifest["interface"]["displayName"]
        )

        codex_marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        codex_entry = next(
            plugin
            for plugin in codex_marketplace["plugins"]
            if plugin["name"] == "fable-risk-labeler"
        )
        self.assertEqual("AVAILABLE", codex_entry["policy"]["installation"])

    def test_skill_frontmatter_uses_shared_runtime_intersection(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        frontmatter = skill.split("---", 2)[1]
        keys = set(re.findall(r"^([A-Za-z0-9_-]+):", frontmatter, re.MULTILINE))
        self.assertEqual({"name", "description"}, keys)
        self.assertRegex(frontmatter, r"(?m)^name: label-issues$")
        self.assertIn(LABEL, frontmatter)
        self.assertIn("Claude / Fable session", skill)
        self.assertIn("GitHub write を行わず", skill)

    def test_skill_has_scoped_read_then_additive_write_contract(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        for contract in (
            "open issue",
            "pull request",
            "ラベルが存在しない場合",
            "明示的に依頼",
            "candidate",
            "write 前",
            "github_add_issue_labels",
            "gh issue edit",
            "--add-label",
            "additive",
            "再取得",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, skill)
        self.assertIn("update_issue(labels=...)", skill)
        self.assertIn("remove_issue_label", skill)

    def test_rubric_is_evidence_based_and_not_priority_based(self) -> None:
        rubric = RUBRIC.read_text(encoding="utf-8")
        for signal in (
            "false deny",
            "shell parser",
            "fail-open / fail-closed",
            "lifecycle",
            "provider",
            "high confidence",
            "正規操作",
        ):
            with self.subTest(signal=signal):
                self.assertIn(signal, rubric)
        for non_signal in (
            "P1",
            "優先度",
            "複雑",
            "Fable という語",
            "label 対象外",
        ):
            with self.subTest(non_signal=non_signal):
                self.assertIn(non_signal, rubric)

    def test_skill_ui_metadata_points_to_namespaced_invocation(self) -> None:
        metadata = OPENAI_YAML.read_text(encoding="utf-8")
        self.assertIn("Fable Risk Labeler", metadata)
        self.assertIn("$fable-risk-labeler:label-issues", metadata)


if __name__ == "__main__":
    unittest.main()

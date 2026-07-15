from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StatuslineCodexAdapterTest(unittest.TestCase):
    def assert_config_validation_executes_parser(self, skill: str) -> None:
        self.assertIn("codex --strict-config app-server < /dev/null", skill)
        self.assertNotIn("codex --strict-config --version", skill)
        self.assertIn("item ID", skill)
        self.assertIn("までは検査しない", skill)

    def assert_direct_config_transaction(self, skill: str) -> None:
        for contract in (
            "timestamp 付き backup",
            "in-place",
            "同じ directory",
            "atomic replace",
            "strict parse が非 0",
            "atomic restore",
            "byte-identical",
            "成功した場合だけ",
            "復元元",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, skill)
        self.assertLess(skill.index("timestamp 付き backup"), skill.index("atomic replace"))
        self.assertLess(skill.index("atomic replace"), skill.index("strict parse が非 0"))
        self.assertLess(skill.index("strict parse が非 0"), skill.index("atomic restore"))

    def test_natsuume_setup_uses_current_builtin_item_ids(self) -> None:
        skill = (
            ROOT
            / "plugins"
            / "natsuume-statusline"
            / "skills"
            / "setup-codex"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        for item in (
            "project-name",
            "current-dir",
            "git-branch",
            "context-used",
            "five-hour-limit",
            "weekly-limit",
        ):
            with self.subTest(item=item):
                self.assertIn(f'"{item}"', skill)
        self.assertIn("/statusline", skill)
        self.assertIn("任意 shell statusline", skill)
        self.assert_config_validation_executes_parser(skill)
        self.assert_direct_config_transaction(skill)

    def test_rate_limit_setup_routes_display_and_details(self) -> None:
        skill = (
            ROOT
            / "plugins"
            / "rate-limit"
            / "skills"
            / "setup-codex"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn('"five-hour-limit"', skill)
        self.assertIn('"weekly-limit"', skill)
        self.assertIn("/usage", skill)
        self.assertIn("$rate-limit:codex-status", skill)
        self.assertIn("独自gauge", skill)
        self.assertIn("~/.codex/config.toml", skill)
        self.assert_config_validation_executes_parser(skill)
        self.assert_direct_config_transaction(skill)

    def test_direct_config_transaction_is_documented_as_a_guarantee(self) -> None:
        overrides = json.loads(
            (ROOT / "codex" / "marketplace-overrides.json").read_text(
                encoding="utf-8"
            )
        )["plugins"]
        for plugin_name in ("natsuume-statusline", "rate-limit"):
            with self.subTest(plugin=plugin_name):
                readme = (
                    ROOT / "plugins" / plugin_name / "README.md"
                ).read_text(encoding="utf-8")
                for contract in (
                    "backup",
                    "atomic replace",
                    "strict parse",
                    "atomic restore",
                    "成功",
                    "instruction contract",
                    "hard security boundary",
                ):
                    self.assertIn(contract, readme)

                compatibility = overrides[plugin_name]["compatibility"]
                guarantees = "\n".join(compatibility["guaranteeDifferences"])
                covers = "\n".join(
                    test["covers"] for test in compatibility["verificationTests"]
                )
                for contract in (
                    "backup",
                    "atomic replace",
                    "strict parse",
                    "atomic restore",
                ):
                    self.assertIn(contract, guarantees)
                    self.assertIn(contract, covers)
                for boundary in (
                    "instruction contract",
                    "hard security boundary",
                ):
                    self.assertIn(boundary, guarantees)

    def test_rate_limit_app_server_client_version_matches_manifest(self) -> None:
        manifest_path = (
            ROOT / "plugins" / "rate-limit" / ".claude-plugin" / "plugin.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        script = (
            ROOT / "plugins" / "rate-limit" / "scripts" / "codex-rate-limit.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(f'"version":"{manifest["version"]}"', script)

    def test_marketplace_smoke_exercises_strict_config_parser(self) -> None:
        smoke = (ROOT / "scripts" / "smoke_codex_marketplace.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("codex --strict-config app-server < /dev/null", smoke)
        self.assertIn("marketplace_smoke_unknown_key", smoke)
        self.assertIn('status_line = ["project-name"', smoke)


if __name__ == "__main__":
    unittest.main()

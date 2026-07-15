from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = load_module("check_plugin_versions", ROOT / "scripts/check_plugin_versions.py")


class VersionPolicyTest(unittest.TestCase):
    def test_strict_semver_comparison(self) -> None:
        self.assertEqual(policy.parse_semver("3.0.6"), (3, 0, 6))
        self.assertGreater(policy.parse_semver("1.10.0"), policy.parse_semver("1.2.99"))

    def test_prerelease_and_leading_zero_are_rejected(self) -> None:
        for value in ("1.2", "1.2.3-rc1", "01.2.3", "v1.2.3"):
            with self.subTest(value=value):
                with self.assertRaises(policy.VersionPolicyError):
                    policy.parse_semver(value)

    def test_feature_bump_requires_minor_or_major_increment(self) -> None:
        previous = policy.parse_semver("0.4.2")
        self.assertFalse(
            policy.is_feature_or_breaking_bump(
                previous, policy.parse_semver("0.4.3")
            )
        )
        self.assertTrue(
            policy.is_feature_or_breaking_bump(
                previous, policy.parse_semver("0.5.0")
            )
        )
        self.assertTrue(
            policy.is_feature_or_breaking_bump(
                previous, policy.parse_semver("1.0.0")
            )
        )

    def test_push_comparison_does_not_use_merge_base(self) -> None:
        self.assertEqual(policy.comparison_range("before", direct=True), "before..HEAD")
        self.assertEqual(policy.comparison_range("base", direct=False), "base...HEAD")

    def test_digest_acknowledgements_do_not_require_a_plugin_version_bump(self) -> None:
        previous = {
            "distribution": {"status": "available"},
            "version": "1.0.0",
            "versioning": {
                "claudeOnlyPaths": [],
                "codexOnlyPaths": [],
            },
            "compatibility": {
                "summary": "same behavior",
                "sourceTreeDigest": "old-tree",
                "components": [
                    {
                        "source": "hooks/hooks.json#/hooks/Stop/0/hooks/0",
                        "sourceDigest": "old-component",
                    }
                ],
            }
        }
        current = {
            "distribution": {"status": "available"},
            "version": "1.0.1",
            "versioning": {
                "claudeOnlyPaths": [],
                "codexOnlyPaths": ["hooks/codex-hooks.json"],
            },
            "compatibility": {
                "summary": "same behavior",
                "sourceTreeDigest": "new-tree",
                "components": [
                    {
                        "source": "hooks/hooks.json#/hooks/Stop/0/hooks/0",
                        "sourceDigest": "new-component",
                    }
                ],
            }
        }

        self.assertEqual(
            policy._without_digest_acknowledgements(previous),
            policy._without_digest_acknowledgements(current),
        )

    def test_plugin_paths_default_shared_but_allow_explicit_runtime_scope(self) -> None:
        port = {
            "versioning": {
                "claudeOnlyPaths": ["commands/"],
                "codexOnlyPaths": ["hooks/codex-hooks.json", "skills/codex/"],
            }
        }

        self.assertEqual(
            policy._runtime_scope_for_path("scripts/shared.sh", port),
            {"claude", "codex"},
        )
        self.assertEqual(
            policy._runtime_scope_for_path("commands/review.md", port),
            {"claude"},
        )
        self.assertEqual(
            policy._runtime_scope_for_path("hooks/codex-hooks.json", port),
            {"codex"},
        )
        self.assertEqual(
            policy._runtime_scope_for_path("skills/codex/SKILL.md", port),
            {"codex"},
        )
        self.assertEqual(policy._runtime_scope_for_path("README.md", port), {"either"})
        self.assertEqual(
            policy._runtime_scope_for_path(".codex-plugin/plugin.json", port), set()
        )

        excluded = {
            **port,
            "distribution": {
                "status": "excluded",
                "reason": "not useful on Codex",
            },
        }
        self.assertEqual(
            policy._runtime_scope_for_path("scripts/shared.sh", excluded),
            {"claude"},
        )
        self.assertEqual(
            policy._runtime_scope_for_path("README.md", excluded), {"claude"}
        )

    def test_codex_compatibility_changes_still_require_a_plugin_version_bump(self) -> None:
        previous = {"compatibility": {"summary": "old behavior"}}
        current = {"compatibility": {"summary": "new behavior"}}

        self.assertNotEqual(
            policy._without_digest_acknowledgements(previous),
            policy._without_digest_acknowledgements(current),
        )

    def test_direct_comparison_detects_force_push_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            repository = Path(temporary_name)

            def git(*args: str) -> str:
                result = subprocess.run(
                    ["git", *args],
                    cwd=repository,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                return result.stdout.strip()

            git("init")
            git("config", "user.name", "Marketplace Test")
            git("config", "user.email", "marketplace@example.invalid")
            plugin_file = repository / "plugins" / "sample" / "payload.txt"
            plugin_file.parent.mkdir(parents=True)
            plugin_file.write_text("old\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-m", "old")
            old_revision = git("rev-parse", "HEAD")
            plugin_file.write_text("new\n", encoding="utf-8")
            git("commit", "-am", "new")
            before_revision = git("rev-parse", "HEAD")
            git("checkout", "--detach", old_revision)

            original_root = policy.ROOT
            policy.ROOT = repository
            try:
                self.assertEqual(
                    policy.changed_plugin_names(before_revision, direct=True),
                    {"sample"},
                )
                self.assertEqual(
                    policy.changed_plugin_names(before_revision, direct=False),
                    set(),
                )
            finally:
                policy.ROOT = original_root

    def test_codex_only_change_requires_only_independent_codex_bump(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            repository = Path(temporary_name)

            def git(*args: str) -> str:
                result = subprocess.run(
                    ["git", *args],
                    cwd=repository,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                return result.stdout.strip()

            def write_json(path: Path, value: object) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value) + "\n", encoding="utf-8")

            git("init")
            git("config", "user.name", "Marketplace Test")
            git("config", "user.email", "marketplace@example.invalid")
            marketplace = {
                "plugins": [
                    {
                        "name": "sample",
                        "source": "./plugins/sample",
                        "version": "1.0.0",
                        "description": "sample",
                        "keywords": ["sample"],
                    }
                ]
            }
            manifest = {
                "name": "sample",
                "version": "1.0.0",
                "description": "sample",
            }
            base_port_plugin = {"compatibility": {"summary": "same"}}
            write_json(repository / ".claude-plugin/marketplace.json", marketplace)
            write_json(
                repository / "plugins/sample/.claude-plugin/plugin.json", manifest
            )
            write_json(
                repository / "plugins/sample/.codex-plugin/plugin.json", manifest
            )
            write_json(
                repository / "codex/marketplace-overrides.json",
                {"schemaVersion": 3, "plugins": {"sample": base_port_plugin}},
            )
            git("add", ".")
            git("commit", "-m", "base")
            base_revision = git("rev-parse", "HEAD")

            current_port_plugin = {
                "distribution": {"status": "available"},
                "version": "1.0.0",
                "versioning": {
                    "claudeOnlyPaths": [],
                    "codexOnlyPaths": ["hooks/codex-hooks.json"],
                },
                "compatibility": {"summary": "same"},
            }
            write_json(
                repository / "codex/marketplace-overrides.json",
                {"schemaVersion": 5, "plugins": {"sample": current_port_plugin}},
            )
            codex_hook = repository / "plugins/sample/hooks/codex-hooks.json"
            write_json(codex_hook, {"hooks": {}})
            git("add", ".")
            git("commit", "-m", "codex change without bump")

            original_root = policy.ROOT
            policy.ROOT = repository
            try:
                failures = policy.check_versions(base_revision, direct=True)
                self.assertEqual(len(failures), 1)
                self.assertIn("codex changes require codex version", failures[0])
                self.assertNotIn("claude changes", failures[0])

                current_port_plugin["version"] = "1.0.1"
                write_json(
                    repository / "codex/marketplace-overrides.json",
                    {"schemaVersion": 5, "plugins": {"sample": current_port_plugin}},
                )
                git("add", ".")
                git("commit", "-m", "bump codex only")
                self.assertEqual(
                    policy.check_versions(base_revision, direct=True), []
                )

                current_port_plugin["version"] = "0.9.0"
                write_json(
                    repository / "codex/marketplace-overrides.json",
                    {"schemaVersion": 5, "plugins": {"sample": current_port_plugin}},
                )
                git("add", ".")
                git("commit", "-m", "regress codex version")
                failures = policy.check_versions(base_revision, direct=True)
                self.assertTrue(
                    any("codex version regressed" in item for item in failures)
                )

                current_port_plugin["version"] = "1.0.1"
                write_json(
                    repository / "codex/marketplace-overrides.json",
                    {"schemaVersion": 5, "plugins": {"sample": current_port_plugin}},
                )
                git("add", ".")
                git("commit", "-m", "restore codex version")

                shared = repository / "plugins/sample/scripts/shared.sh"
                shared.parent.mkdir(parents=True)
                shared.write_text("#!/bin/bash\n", encoding="utf-8")
                git("add", ".")
                git("commit", "-m", "shared change")
                failures = policy.check_versions(base_revision, direct=True)
                self.assertTrue(
                    any("claude changes require claude version" in item for item in failures)
                )

                shared.unlink()
                codex_hook.unlink()
                current_port_plugin["distribution"] = {
                    "status": "excluded",
                    "reason": "not useful on Codex",
                }
                current_port_plugin["version"] = "1.0.0"
                write_json(
                    repository / "codex/marketplace-overrides.json",
                    {"schemaVersion": 5, "plugins": {"sample": current_port_plugin}},
                )
                git("add", "-A")
                git("commit", "-m", "exclude without codex bump")
                failures = policy.check_versions(base_revision, direct=True)
                self.assertEqual(len(failures), 1)
                self.assertIn("codex changes require codex version", failures[0])

                current_port_plugin["version"] = "1.0.1"
                write_json(
                    repository / "codex/marketplace-overrides.json",
                    {"schemaVersion": 5, "plugins": {"sample": current_port_plugin}},
                )
                git("add", ".")
                git("commit", "-m", "bump codex exclusion")
                self.assertEqual(
                    policy.check_versions(base_revision, direct=True), []
                )
            finally:
                policy.ROOT = original_root


if __name__ == "__main__":
    unittest.main()

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


def repository_git(repository: Path, *args: str) -> str:
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


def write_plugin_repository_state(
    repository: Path,
    *,
    claude_version: str,
    codex_version: str,
    distribution_status: str,
    marketplace_description: str = "sample",
    manifest_description: str = "sample",
) -> None:
    write_json(
        repository / ".claude-plugin/marketplace.json",
        {
            "plugins": [
                {
                    "name": "sample",
                    "source": "./plugins/sample",
                    "version": claude_version,
                    "description": marketplace_description,
                    "keywords": ["sample"],
                }
            ]
        },
    )
    write_json(
        repository / "plugins/sample/.claude-plugin/plugin.json",
        {
            "name": "sample",
            "version": claude_version,
            "description": manifest_description,
        },
    )
    distribution = {"status": distribution_status}
    if distribution_status == "excluded":
        distribution["reason"] = "not useful on Codex"
    write_json(
        repository / "codex/marketplace-overrides.json",
        {
            "schemaVersion": 5,
            "plugins": {
                "sample": {
                    "distribution": distribution,
                    "version": codex_version,
                    "versioning": {
                        "claudeOnlyPaths": [],
                        "codexOnlyPaths": [],
                    },
                    "compatibility": {"summary": "same"},
                }
            },
        },
    )


def initialize_repository(repository: Path) -> None:
    repository_git(repository, "init")
    repository_git(repository, "config", "user.name", "Marketplace Test")
    repository_git(
        repository,
        "config",
        "user.email",
        "marketplace@example.invalid",
    )


def check_fixture_versions(repository: Path, base_revision: str) -> list[str]:
    original_root = policy.ROOT
    policy.ROOT = repository
    try:
        return policy.check_versions(base_revision, direct=True)
    finally:
        policy.ROOT = original_root


class VersionPolicyTest(unittest.TestCase):
    def check_distribution_transition(
        self,
        *,
        base_status: str,
        base_version: str,
        current_status: str,
        current_version: str,
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as temporary_name:
            repository = Path(temporary_name)
            initialize_repository(repository)
            write_plugin_repository_state(
                repository,
                claude_version="1.0.0",
                codex_version=base_version,
                distribution_status=base_status,
            )
            repository_git(repository, "add", ".")
            repository_git(repository, "commit", "-m", "base")
            base_revision = repository_git(repository, "rev-parse", "HEAD")

            write_plugin_repository_state(
                repository,
                claude_version="1.0.0",
                codex_version=current_version,
                distribution_status=current_status,
            )
            repository_git(repository, "add", ".")
            repository_git(repository, "commit", "-m", "change distribution")
            return check_fixture_versions(repository, base_revision)

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

    def test_excluded_plugin_claude_metadata_changes_do_not_require_codex_bump(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            repository = Path(temporary_name)
            initialize_repository(repository)
            write_plugin_repository_state(
                repository,
                claude_version="1.0.0",
                codex_version="0.3.0",
                distribution_status="excluded",
            )
            repository_git(repository, "add", ".")
            repository_git(repository, "commit", "-m", "base")
            base_revision = repository_git(repository, "rev-parse", "HEAD")

            write_plugin_repository_state(
                repository,
                claude_version="1.0.1",
                codex_version="0.3.0",
                distribution_status="excluded",
                marketplace_description="updated marketplace metadata",
                manifest_description="updated manifest metadata",
            )
            repository_git(repository, "add", ".")
            repository_git(repository, "commit", "-m", "change Claude metadata")

            self.assertEqual(
                check_fixture_versions(repository, base_revision),
                [],
            )

    def test_available_to_excluded_requires_major_codex_bump(self) -> None:
        failures = self.check_distribution_transition(
            base_status="available",
            base_version="0.3.0",
            current_status="excluded",
            current_version="0.3.1",
        )

        self.assertTrue(
            any(
                "distribution" in failure and "major" in failure
                for failure in failures
            ),
            failures,
        )

    def test_available_to_excluded_accepts_major_codex_bump(self) -> None:
        self.assertEqual(
            self.check_distribution_transition(
                base_status="available",
                base_version="0.3.0",
                current_status="excluded",
                current_version="1.0.0",
            ),
            [],
        )

    def test_excluded_to_available_requires_minor_or_major_codex_bump(self) -> None:
        failures = self.check_distribution_transition(
            base_status="excluded",
            base_version="1.2.3",
            current_status="available",
            current_version="1.2.4",
        )

        self.assertTrue(
            any(
                "distribution" in failure and "minor" in failure
                for failure in failures
            ),
            failures,
        )

    def test_excluded_to_available_accepts_minor_codex_bump(self) -> None:
        self.assertEqual(
            self.check_distribution_transition(
                base_status="excluded",
                base_version="1.2.3",
                current_status="available",
                current_version="1.3.0",
            ),
            [],
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

                current_port_plugin["version"] = "2.0.0"
                write_json(
                    repository / "codex/marketplace-overrides.json",
                    {"schemaVersion": 5, "plugins": {"sample": current_port_plugin}},
                )
                git("add", ".")
                git("commit", "-m", "major codex exclusion bump")
                self.assertEqual(
                    policy.check_versions(base_revision, direct=True), []
                )
            finally:
                policy.ROOT = original_root


if __name__ == "__main__":
    unittest.main()

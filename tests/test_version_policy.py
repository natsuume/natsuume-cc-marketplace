from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()

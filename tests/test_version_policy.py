"""scripts/check_plugin_versions.py の新仕様 (Codex 概念の全廃) を検証する spec-first テスト。

Phase A (このテストを書いた時点) では check_plugin_versions.py はまだ旧実装 (Codex
runtime 概念を含む) のままであり、本ファイルは red で正しい。Phase B で以下の新仕様に
書き換えられたときに green になることを期待する:

- CLI は現行維持: `check_plugin_versions.py <base_revision> [--direct]`
- codex/marketplace-overrides.json・.codex-plugin・codexOnlyPaths/claudeOnlyPaths・
  distribution status・available/excluded 遷移・Codex version という概念を持たない
- 変更検出 (checks 1-4):
    1. git diff --name-only で plugins/<name>/ 配下に変更があれば changed
       (ただし plugins/<name>/.claude-plugin/plugin.json 単独の変更は path として数えない)
    2. plugin.json の version 以外のフィールドが変わった場合は changed
    3. marketplace.json の対応 entry の version 以外が変わった場合は changed
    4. marketplace.json の plugins 以外の global metadata が変わった場合は全 plugin が changed
- 検査 (checks a-f):
    a. changed な plugin は current version > base version (bump 必須)
    b. 全 plugin で version の後退 (current < base) を禁止
    c. plugin.json の version と marketplace.json の対応 entry の version の一致
    d. marketplace.json の plugin 名集合と plugins/ 直下のディレクトリ名集合の一致 (双方向)
    e. リポジトリ直下 README.md の plugin 一覧テーブルに全 marketplace plugin が存在し、
       version が plugin.json と一致
    f. plugins/<name>/README.md の `## バージョン` 見出し直下の `vX.Y.Z` が plugin.json の
       version と一致
"""

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


# ---------------------------------------------------------------------------
# fixture 構築ヘルパー
# ---------------------------------------------------------------------------


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


def initialize_repository(repository: Path) -> None:
    repository_git(repository, "init")
    repository_git(repository, "config", "user.name", "Marketplace Test")
    repository_git(repository, "config", "user.email", "marketplace@example.invalid")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def write_plugin_manifest(
    repository: Path, name: str, version: str, description: str = "sample plugin"
) -> None:
    write_json(
        repository / f"plugins/{name}/.claude-plugin/plugin.json",
        {"name": name, "version": version, "description": description},
    )


def marketplace_entry(
    name: str, version: str, description: str = "sample plugin"
) -> dict[str, object]:
    return {
        "name": name,
        "source": f"./plugins/{name}",
        "version": version,
        "description": description,
        "keywords": [name],
    }


def write_marketplace(
    repository: Path,
    entries: list[dict[str, object]],
    *,
    extra_metadata: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "name": "test-marketplace",
        "owner": {"name": "Marketplace Tester"},
        "plugins": entries,
    }
    if extra_metadata:
        payload.update(extra_metadata)
    write_json(repository / ".claude-plugin/marketplace.json", payload)


def write_root_readme(repository: Path, rows: list[tuple[str, str, str]]) -> None:
    lines = [
        "# Marketplace",
        "",
        "## プラグイン一覧",
        "",
        "| プラグイン | version | 説明 |",
        "| --- | --- | --- |",
    ]
    for name, version, description in rows:
        lines.append(f"| [{name}](#{name}) | {version} | {description} |")
    (repository / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_plugin_readme(repository: Path, name: str, version: str) -> None:
    text = f"# {name}\n\n## バージョン\n\nv{version}\n"
    path = repository / f"plugins/{name}/README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_full_consistent_repository(
    repository: Path, plugins: list[tuple[str, str]]
) -> None:
    """plugin.json / marketplace.json / root README / plugin README のすべてが
    一致し、check a〜f がすべて green になる repository state を書く。"""

    entries: list[dict[str, object]] = []
    rows: list[tuple[str, str, str]] = []
    for name, version in plugins:
        write_plugin_manifest(repository, name, version)
        write_plugin_readme(repository, name, version)
        entries.append(marketplace_entry(name, version))
        rows.append((name, version, "sample plugin"))
    write_marketplace(repository, entries)
    write_root_readme(repository, rows)


def commit_all(repository: Path, message: str) -> str:
    repository_git(repository, "add", "-A")
    repository_git(repository, "commit", "-m", message)
    return repository_git(repository, "rev-parse", "HEAD")


def check_fixture_versions(
    repository: Path, base_revision: str, *, direct: bool = True
) -> list[str]:
    original_root = policy.ROOT
    policy.ROOT = repository
    try:
        return policy.check_versions(base_revision, direct=direct)
    finally:
        policy.ROOT = original_root


def changed_names_for_fixture(
    repository: Path, base_revision: str, *, direct: bool = True
) -> set[str]:
    original_root = policy.ROOT
    policy.ROOT = repository
    try:
        return policy.changed_plugin_names(base_revision, direct=direct)
    finally:
        policy.ROOT = original_root


# ---------------------------------------------------------------------------
# semver / CLI 基本契約
# ---------------------------------------------------------------------------


class SemverAndComparisonRangeTest(unittest.TestCase):
    def test_strict_semver_comparison(self) -> None:
        self.assertEqual(policy.parse_semver("3.0.6"), (3, 0, 6))
        self.assertGreater(policy.parse_semver("1.10.0"), policy.parse_semver("1.2.99"))

    def test_prerelease_and_leading_zero_are_rejected(self) -> None:
        for value in ("1.2", "1.2.3-rc1", "01.2.3", "v1.2.3"):
            with self.subTest(value=value):
                with self.assertRaises(policy.VersionPolicyError):
                    policy.parse_semver(value)

    def test_direct_uses_two_dot_range_and_merge_base_uses_three_dot_range(self) -> None:
        self.assertEqual(policy.comparison_range("before", direct=True), "before..HEAD")
        self.assertEqual(policy.comparison_range("base", direct=False), "base...HEAD")


class CommandLineArgumentParsingTest(unittest.TestCase):
    def test_direct_flag_and_default_merge_base_mode(self) -> None:
        self.assertFalse(policy.parse_args(["abc123"]).direct)
        self.assertTrue(policy.parse_args(["abc123", "--direct"]).direct)
        self.assertEqual(policy.parse_args(["abc123"]).base_revision, "abc123")


# ---------------------------------------------------------------------------
# 変更検出 (checks 1-4)
# ---------------------------------------------------------------------------


class ChangedPluginDetectionTest(unittest.TestCase):
    def test_unrelated_plugin_file_change_marks_plugin_changed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            base_revision = commit_all(repository, "base")

            script = repository / "plugins/sample/scripts/extra.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            commit_all(repository, "add script")

            self.assertEqual(
                changed_names_for_fixture(repository, base_revision), {"sample"}
            )

    def test_plugin_json_version_only_change_is_not_counted_as_changed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            base_revision = commit_all(repository, "base")

            write_plugin_manifest(repository, "sample", "1.0.1")
            commit_all(repository, "bump plugin.json version only")

            self.assertEqual(changed_names_for_fixture(repository, base_revision), set())

    def test_plugin_json_non_version_field_change_is_counted_as_changed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            base_revision = commit_all(repository, "base")

            write_plugin_manifest(
                repository, "sample", "1.0.0", description="updated description"
            )
            commit_all(repository, "change plugin.json description")

            self.assertEqual(
                changed_names_for_fixture(repository, base_revision), {"sample"}
            )

    def test_marketplace_entry_version_only_change_is_not_counted_as_changed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            base_revision = commit_all(repository, "base")

            write_marketplace(repository, [marketplace_entry("sample", "1.0.1")])
            commit_all(repository, "bump marketplace entry version only")

            self.assertEqual(changed_names_for_fixture(repository, base_revision), set())

    def test_marketplace_entry_non_version_field_change_is_counted_as_changed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            base_revision = commit_all(repository, "base")

            write_marketplace(
                repository, [marketplace_entry("sample", "1.0.0", description="updated")]
            )
            commit_all(repository, "change marketplace entry description")

            self.assertEqual(
                changed_names_for_fixture(repository, base_revision), {"sample"}
            )

    def test_marketplace_global_metadata_change_marks_all_plugins_changed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(
                repository, [("sample", "1.0.0"), ("other", "2.0.0")]
            )
            base_revision = commit_all(repository, "base")

            write_marketplace(
                repository,
                [marketplace_entry("sample", "1.0.0"), marketplace_entry("other", "2.0.0")],
                extra_metadata={"owner": {"name": "Renamed Owner"}},
            )
            commit_all(repository, "change marketplace global metadata")

            self.assertEqual(
                changed_names_for_fixture(repository, base_revision), {"sample", "other"}
            )

    def test_direct_comparison_detects_force_push_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            plugin_file = repository / "plugins" / "sample" / "payload.txt"
            plugin_file.parent.mkdir(parents=True)
            plugin_file.write_text("old\n", encoding="utf-8")
            repository_git(repository, "add", ".")
            repository_git(repository, "commit", "-m", "old")
            old_revision = repository_git(repository, "rev-parse", "HEAD")
            plugin_file.write_text("new\n", encoding="utf-8")
            repository_git(repository, "commit", "-am", "new")
            before_revision = repository_git(repository, "rev-parse", "HEAD")
            repository_git(repository, "checkout", "--detach", old_revision)

            self.assertEqual(
                changed_names_for_fixture(repository, before_revision, direct=True),
                {"sample"},
            )
            self.assertEqual(
                changed_names_for_fixture(repository, before_revision, direct=False),
                set(),
            )


# ---------------------------------------------------------------------------
# check a: changed plugin はバージョンの bump が必須
# ---------------------------------------------------------------------------


class BumpRequirementCheckTest(unittest.TestCase):
    def test_changed_plugin_without_version_bump_fails_and_patch_bump_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            base_revision = commit_all(repository, "base")

            script = repository / "plugins/sample/scripts/extra.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            commit_all(repository, "add script without version bump")

            failures = check_fixture_versions(repository, base_revision)
            self.assertTrue(any("sample" in failure for failure in failures), failures)

            # 単なる patch bump (minor/major を要求しない) で解消することを確認する。
            write_full_consistent_repository(repository, [("sample", "1.0.1")])
            commit_all(repository, "patch bump resolves the requirement")

            self.assertEqual(check_fixture_versions(repository, base_revision), [])

    def test_new_plugin_addition_does_not_require_a_bump_against_nonexistent_base(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            base_revision = commit_all(repository, "base")

            write_full_consistent_repository(
                repository, [("sample", "1.0.0"), ("fresh", "0.1.0")]
            )
            commit_all(repository, "add brand-new plugin")

            self.assertEqual(check_fixture_versions(repository, base_revision), [])


# ---------------------------------------------------------------------------
# check b: 全 plugin でバージョンの後退を禁止
# ---------------------------------------------------------------------------


class VersionRegressionCheckTest(unittest.TestCase):
    def test_version_regression_fails(self) -> None:
        """plugin.json 等を一貫して退行させると check b (version regression) が発火する。

        新仕様では plugins/<name>/README.md への変更も check 1 の「changed」に数えられる
        ため、この fixture は check a (bump 必須) も同時に発火しうる。ここでは check b が
        少なくとも発火することだけを検証し、他 check との共起は許容する。
        """
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.2.0")])
            base_revision = commit_all(repository, "base")

            write_full_consistent_repository(repository, [("sample", "1.1.0")])
            commit_all(repository, "regress version everywhere")

            failures = check_fixture_versions(repository, base_revision)
            self.assertTrue(any("sample" in failure for failure in failures), failures)

    def test_equal_version_with_no_changes_passes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            revision = commit_all(repository, "consistent state")

            self.assertEqual(check_fixture_versions(repository, revision), [])


# ---------------------------------------------------------------------------
# check c: plugin.json と marketplace.json の対応 entry の version 一致
# ---------------------------------------------------------------------------


class ManifestMarketplaceVersionConsistencyTest(unittest.TestCase):
    def test_mismatched_plugin_json_and_marketplace_entry_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            write_marketplace(repository, [marketplace_entry("sample", "1.0.1")])
            revision = commit_all(repository, "mismatched marketplace entry version")

            failures = check_fixture_versions(repository, revision)
            self.assertTrue(any("sample" in failure for failure in failures), failures)

    def test_matching_plugin_json_and_marketplace_entry_version_passes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            revision = commit_all(repository, "consistent state")

            self.assertEqual(check_fixture_versions(repository, revision), [])


# ---------------------------------------------------------------------------
# check d: marketplace.json の plugin 名集合と plugins/ 直下のディレクトリ名集合の一致
# ---------------------------------------------------------------------------


class PluginNameSetConsistencyTest(unittest.TestCase):
    def test_marketplace_entry_without_plugin_directory_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            # marketplace.json に "ghost" entry を追加するが plugins/ghost/ は作らない。
            write_marketplace(
                repository,
                [
                    marketplace_entry("sample", "1.0.0"),
                    marketplace_entry("ghost", "1.0.0"),
                ],
            )
            write_root_readme(
                repository,
                [
                    ("sample", "1.0.0", "sample plugin"),
                    ("ghost", "1.0.0", "sample plugin"),
                ],
            )
            revision = commit_all(repository, "marketplace lists a nonexistent plugin")

            failures = check_fixture_versions(repository, revision)
            self.assertTrue(any("ghost" in failure for failure in failures), failures)

    def test_plugin_directory_without_marketplace_entry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            # plugins/orphan/ を作るが marketplace.json にも root README にも載せない。
            # (自身の plugin.json / README は一致させ、check c/f への波及を避ける)
            write_plugin_manifest(repository, "orphan", "1.0.0")
            write_plugin_readme(repository, "orphan", "1.0.0")
            revision = commit_all(repository, "plugin directory without marketplace entry")

            failures = check_fixture_versions(repository, revision)
            self.assertTrue(any("orphan" in failure for failure in failures), failures)

    def test_matching_plugin_and_marketplace_name_sets_passes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(
                repository, [("sample", "1.0.0"), ("other", "2.0.0")]
            )
            revision = commit_all(repository, "consistent two-plugin state")

            self.assertEqual(check_fixture_versions(repository, revision), [])


# ---------------------------------------------------------------------------
# check e: リポジトリ直下 README.md の plugin 一覧テーブル
# ---------------------------------------------------------------------------


class RootReadmeTableConsistencyTest(unittest.TestCase):
    def test_missing_readme_table_row_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_plugin_manifest(repository, "sample", "1.0.0")
            write_plugin_readme(repository, "sample", "1.0.0")
            write_marketplace(repository, [marketplace_entry("sample", "1.0.0")])
            write_root_readme(repository, [])  # sample の行を書かない
            revision = commit_all(repository, "root README missing plugin row")

            failures = check_fixture_versions(repository, revision)
            self.assertTrue(any("sample" in failure for failure in failures), failures)

    def test_mismatched_readme_table_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_plugin_manifest(repository, "sample", "1.0.0")
            write_plugin_readme(repository, "sample", "1.0.0")
            write_marketplace(repository, [marketplace_entry("sample", "1.0.0")])
            write_root_readme(repository, [("sample", "0.9.0", "sample plugin")])
            revision = commit_all(repository, "root README table version mismatch")

            failures = check_fixture_versions(repository, revision)
            self.assertTrue(any("sample" in failure for failure in failures), failures)

    def test_matching_readme_table_version_passes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            revision = commit_all(repository, "consistent state")

            self.assertEqual(check_fixture_versions(repository, revision), [])


# ---------------------------------------------------------------------------
# check f: plugins/<name>/README.md の `## バージョン` 見出し直下の vX.Y.Z
# ---------------------------------------------------------------------------


class PluginReadmeVersionConsistencyTest(unittest.TestCase):
    def test_missing_plugin_readme_version_heading_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_plugin_manifest(repository, "sample", "1.0.0")
            readme = repository / "plugins/sample/README.md"
            readme.parent.mkdir(parents=True, exist_ok=True)
            readme.write_text("# sample\n\nno version heading here\n", encoding="utf-8")
            write_marketplace(repository, [marketplace_entry("sample", "1.0.0")])
            write_root_readme(repository, [("sample", "1.0.0", "sample plugin")])
            revision = commit_all(repository, "plugin README missing version heading")

            failures = check_fixture_versions(repository, revision)
            self.assertTrue(any("sample" in failure for failure in failures), failures)

    def test_mismatched_plugin_readme_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_plugin_manifest(repository, "sample", "1.0.0")
            write_plugin_readme(repository, "sample", "0.9.0")
            write_marketplace(repository, [marketplace_entry("sample", "1.0.0")])
            write_root_readme(repository, [("sample", "1.0.0", "sample plugin")])
            revision = commit_all(repository, "plugin README version mismatch")

            failures = check_fixture_versions(repository, revision)
            self.assertTrue(any("sample" in failure for failure in failures), failures)

    def test_non_level_two_version_heading_fails(self) -> None:
        # 規約は「`## バージョン` 直下」であり、level-2 以外の見出しは正本として認めない
        for heading in ("# バージョン", "### バージョン"):
            with self.subTest(heading=heading):
                with tempfile.TemporaryDirectory() as name:
                    repository = Path(name)
                    initialize_repository(repository)
                    write_plugin_manifest(repository, "sample", "1.0.0")
                    readme = repository / "plugins/sample/README.md"
                    readme.parent.mkdir(parents=True, exist_ok=True)
                    readme.write_text(
                        f"# sample\n\n{heading}\n\nv1.0.0\n", encoding="utf-8"
                    )
                    write_marketplace(
                        repository, [marketplace_entry("sample", "1.0.0")]
                    )
                    write_root_readme(
                        repository, [("sample", "1.0.0", "sample plugin")]
                    )
                    revision = commit_all(repository, "non level-2 version heading")

                    failures = check_fixture_versions(repository, revision)
                    self.assertTrue(
                        any("sample" in failure for failure in failures), failures
                    )

    def test_plugin_readme_version_with_extra_blank_line_passes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_plugin_manifest(repository, "sample", "1.0.0")
            readme = repository / "plugins/sample/README.md"
            readme.parent.mkdir(parents=True, exist_ok=True)
            readme.write_text(
                "# sample\n\n## バージョン\n\n\nv1.0.0\n", encoding="utf-8"
            )
            write_marketplace(repository, [marketplace_entry("sample", "1.0.0")])
            write_root_readme(repository, [("sample", "1.0.0", "sample plugin")])
            revision = commit_all(repository, "plugin README with extra blank line")

            self.assertEqual(check_fixture_versions(repository, revision), [])

    def test_matching_plugin_readme_version_passes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(repository, [("sample", "1.0.0")])
            revision = commit_all(repository, "consistent state")

            self.assertEqual(check_fixture_versions(repository, revision), [])


# ---------------------------------------------------------------------------
# 総合: 複数 plugin が全チェックを満たす repository state
# ---------------------------------------------------------------------------


class FullyConsistentRepositoryStateTest(unittest.TestCase):
    def test_multi_plugin_consistent_repository_has_no_failures(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_full_consistent_repository(
                repository, [("sample", "1.0.0"), ("other", "2.3.4")]
            )
            revision = commit_all(repository, "consistent two-plugin repository")

            self.assertEqual(check_fixture_versions(repository, revision), [])


# ---------------------------------------------------------------------------
# semver 不正値はエラーとして伝播する
# ---------------------------------------------------------------------------


class InvalidSemverPropagationTest(unittest.TestCase):
    def test_invalid_plugin_json_version_raises_version_policy_error(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_plugin_manifest(repository, "sample", "1.2")  # invalid: missing patch
            write_plugin_readme(repository, "sample", "1.2")
            write_marketplace(repository, [marketplace_entry("sample", "1.2")])
            write_root_readme(repository, [("sample", "1.2", "sample plugin")])
            revision = commit_all(repository, "invalid semver in plugin.json")

            with self.assertRaises(policy.VersionPolicyError):
                check_fixture_versions(repository, revision)

    def test_invalid_marketplace_entry_version_raises_version_policy_error(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repository = Path(name)
            initialize_repository(repository)
            write_plugin_manifest(repository, "sample", "1.0.0")
            write_plugin_readme(repository, "sample", "1.0.0")
            write_marketplace(repository, [marketplace_entry("sample", "v1.0.0")])
            write_root_readme(repository, [("sample", "1.0.0", "sample plugin")])
            revision = commit_all(repository, "invalid semver in marketplace entry")

            with self.assertRaises(policy.VersionPolicyError):
                check_fixture_versions(repository, revision)


if __name__ == "__main__":
    unittest.main()

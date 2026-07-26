#!/usr/bin/env python3
"""Require a semver bump for changed plugins, and keep version metadata consistent.

Checks enforced (see README / issue history for the full rationale):
  a. a changed plugin's current version must be greater than its base version
  b. no plugin's version may regress (current < base)
  c. plugins/<name>/.claude-plugin/plugin.json version must match the
     corresponding .claude-plugin/marketplace.json entry version
  d. the marketplace.json plugin name set and the plugins/ directory name set
     (directories with a .claude-plugin/plugin.json) must match, both ways
  e. the repository root README.md plugin table must list every marketplace
     plugin with a version matching plugin.json
  f. plugins/<name>/README.md must have a `## バージョン` heading followed by
     `vX.Y.Z` matching plugin.json

"Changed" plugin detection (checks 1-4):
  1. git diff shows a change under plugins/<name>/ (a change limited to
     plugins/<name>/.claude-plugin/plugin.json does not count on its own)
  2. plugins/<name>/.claude-plugin/plugin.json changed in a field other than
     "version"
  3. the plugin's marketplace.json entry changed in a field other than
     "version"
  4. marketplace.json's global metadata (everything other than "plugins")
     changed, which marks every plugin as changed
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
PLUGIN_MANIFEST_RELATIVE_PATH = ".claude-plugin/plugin.json"
README_TABLE_ROW_RE = re.compile(r"^\|\s*\[([^\]]+)\]\([^)]*\)\s*\|\s*([^|]+?)\s*\|")
PLUGIN_README_HEADING_RE = re.compile(r"^##\s+バージョン\s*$")
PLUGIN_README_VERSION_LINE_RE = re.compile(r"^v(\d+\.\d+\.\d+)\s*$")


class VersionPolicyError(RuntimeError):
    """Raised when Git state or version metadata cannot be inspected."""


def parse_semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise VersionPolicyError(f"invalid strict semver: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise VersionPolicyError(f"git {' '.join(args)} failed: {detail}") from exc


def read_json_at_revision(revision: str, path: str) -> dict[str, Any] | None:
    result = git("show", f"{revision}:{path}", check=False)
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VersionPolicyError(f"{revision}:{path} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise VersionPolicyError(f"{revision}:{path} must contain a JSON object")
    return value


def load_current_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VersionPolicyError(f"{path.relative_to(ROOT)} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise VersionPolicyError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def _marketplace_plugins(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if payload is None:
        return {}
    entries = payload.get("plugins")
    if not isinstance(entries, list):
        raise VersionPolicyError("marketplace.plugins must be an array")
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise VersionPolicyError("marketplace contains an invalid plugin entry")
        result[entry["name"]] = entry
    return result


def _without_version(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    normalized = copy.deepcopy(value)
    normalized.pop("version", None)
    return normalized


def _marketplace_global_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(payload or {})
    normalized.pop("plugins", None)
    return normalized


def comparison_range(base_revision: str, *, direct: bool) -> str:
    """Use tree-to-tree comparison for pushes and merge-base comparison for PRs."""

    separator = ".." if direct else "..."
    return f"{base_revision}{separator}HEAD"


def _current_plugin_directory_names() -> set[str]:
    """Return plugins/ subdirectory names that have a .claude-plugin/plugin.json."""

    plugins_dir = ROOT / "plugins"
    if not plugins_dir.is_dir():
        return set()
    return {
        child.name
        for child in plugins_dir.iterdir()
        if child.is_dir() and (child / PLUGIN_MANIFEST_RELATIVE_PATH).is_file()
    }


def _plugin_directory_names_at_revision(revision: str) -> set[str]:
    """Same as _current_plugin_directory_names, but read from a Git revision."""

    result = git("ls-tree", "-r", "--name-only", revision, "--", "plugins", check=False)
    if result.returncode != 0:
        return set()
    names: set[str] = set()
    for raw_line in result.stdout.decode("utf-8", errors="surrogateescape").splitlines():
        parts = Path(raw_line).parts
        if len(parts) == 4 and parts[0] == "plugins" and Path(*parts[2:]).as_posix() == (
            PLUGIN_MANIFEST_RELATIVE_PATH
        ):
            names.add(parts[1])
    return names


def _plugin_relative_path(path: str) -> tuple[str, str] | None:
    """Split a repo-relative path into (plugin_name, plugin_relative_path)."""

    parts = Path(path).parts
    if len(parts) >= 3 and parts[0] == "plugins":
        return parts[1], Path(*parts[2:]).as_posix()
    return None


def changed_plugin_names(base_revision: str, *, direct: bool = False) -> set[str]:
    """Return plugin names considered changed under checks 1-4."""

    changed: set[str] = set()

    # check 1: a path change under plugins/<name>/, except a change limited to
    # the plugin manifest file itself (that is covered separately by check 2,
    # which ignores version-only edits).
    diff_output = git(
        "diff", "--name-only", "-z", comparison_range(base_revision, direct=direct)
    ).stdout
    for raw_path in diff_output.split(b"\0"):
        if not raw_path:
            continue
        parsed = _plugin_relative_path(raw_path.decode("utf-8", errors="surrogateescape"))
        if parsed is None:
            continue
        name, relative = parsed
        if relative == PLUGIN_MANIFEST_RELATIVE_PATH:
            continue
        changed.add(name)

    # check 2: plugin.json changed in a field other than "version".
    plugin_names = _current_plugin_directory_names() | _plugin_directory_names_at_revision(
        base_revision
    )
    for name in plugin_names:
        current_manifest = load_current_json(ROOT / "plugins" / name / PLUGIN_MANIFEST_RELATIVE_PATH)
        base_manifest = read_json_at_revision(
            base_revision, f"plugins/{name}/{PLUGIN_MANIFEST_RELATIVE_PATH}"
        )
        if _without_version(current_manifest) != _without_version(base_manifest):
            changed.add(name)

    current_marketplace_payload = load_current_json(ROOT / ".claude-plugin" / "marketplace.json")
    base_marketplace_payload = read_json_at_revision(
        base_revision, ".claude-plugin/marketplace.json"
    )
    current_marketplace = _marketplace_plugins(current_marketplace_payload)
    base_marketplace = _marketplace_plugins(base_marketplace_payload)

    # check 3: a marketplace.json entry changed in a field other than "version".
    for name in set(current_marketplace) | set(base_marketplace):
        if _without_version(current_marketplace.get(name)) != _without_version(
            base_marketplace.get(name)
        ):
            changed.add(name)

    # check 4: marketplace.json global metadata (everything but "plugins")
    # changed, which marks every known plugin as changed.
    if _marketplace_global_metadata(current_marketplace_payload) != _marketplace_global_metadata(
        base_marketplace_payload
    ):
        changed.update(set(current_marketplace) | set(base_marketplace))

    return changed


def current_manifest_version(plugin_name: str) -> str | None:
    manifest = load_current_json(ROOT / "plugins" / plugin_name / PLUGIN_MANIFEST_RELATIVE_PATH)
    if manifest is None:
        return None
    value = manifest.get("version")
    if not isinstance(value, str):
        raise VersionPolicyError(f"{plugin_name}: current plugin.json version is missing")
    return value


def base_manifest_version(base_revision: str, plugin_name: str) -> str | None:
    manifest = read_json_at_revision(
        base_revision, f"plugins/{plugin_name}/{PLUGIN_MANIFEST_RELATIVE_PATH}"
    )
    if manifest is None:
        return None
    value = manifest.get("version")
    if not isinstance(value, str):
        raise VersionPolicyError(f"{plugin_name}: base plugin.json version is missing")
    return value


def _parsed_version(
    plugin_name: str, value: str | None, *, base: bool
) -> tuple[int, int, int] | None:
    if value is None:
        return None
    try:
        return parse_semver(value)
    except VersionPolicyError as exc:
        prefix = "base " if base else "current "
        raise VersionPolicyError(f"{plugin_name}: {prefix}{exc}") from exc


def _root_readme_plugin_versions() -> dict[str, str]:
    """Parse the root README.md plugin table: `| [name](#anchor) | X.Y.Z | ... |`."""

    readme_path = ROOT / "README.md"
    if not readme_path.is_file():
        return {}
    versions: dict[str, str] = {}
    for line in readme_path.read_text(encoding="utf-8").splitlines():
        match = README_TABLE_ROW_RE.match(line.strip())
        if match is None:
            continue
        versions[match.group(1)] = match.group(2)
    return versions


def _plugin_readme_version(plugin_name: str) -> str | None:
    """Read the `vX.Y.Z` line under the `## バージョン` heading (blank lines allowed)."""

    readme_path = ROOT / "plugins" / plugin_name / "README.md"
    if not readme_path.is_file():
        return None
    lines = readme_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if PLUGIN_README_HEADING_RE.match(line.strip()) is None:
            continue
        for candidate in lines[index + 1 :]:
            stripped = candidate.strip()
            if stripped == "":
                continue
            match = PLUGIN_README_VERSION_LINE_RE.match(stripped)
            return match.group(1) if match else None
    return None


def check_versions(base_revision: str, *, direct: bool = False) -> list[str]:
    git("rev-parse", "--verify", f"{base_revision}^{{commit}}")
    changed = changed_plugin_names(base_revision, direct=direct)

    current_marketplace = _marketplace_plugins(
        load_current_json(ROOT / ".claude-plugin" / "marketplace.json")
    )
    plugin_directory_names = _current_plugin_directory_names()

    failures: list[str] = []

    # checks a-c: per-plugin version bump / regression / manifest-marketplace
    # consistency, evaluated over every plugin known in the current state or
    # touched by this change.
    all_names = set(current_marketplace) | plugin_directory_names | changed
    for plugin_name in sorted(all_names):
        current_version = current_manifest_version(plugin_name)
        base_version = base_manifest_version(base_revision, plugin_name)
        current_tuple = _parsed_version(plugin_name, current_version, base=False)
        previous_tuple = _parsed_version(plugin_name, base_version, base=True)

        # check b: no plugin's version may regress.
        if (
            current_tuple is not None
            and previous_tuple is not None
            and current_tuple < previous_tuple
        ):
            failures.append(
                f"{plugin_name}: version regressed from {base_version} to {current_version}"
            )

        # check a: a changed plugin must have current version > base version.
        if (
            plugin_name in changed
            and current_tuple is not None
            and previous_tuple is not None
            and current_tuple <= previous_tuple
        ):
            failures.append(
                f"{plugin_name}: changed plugin requires version > {base_version} "
                f"(current {current_version})"
            )

        # check c: plugin.json version must match the marketplace.json entry.
        if plugin_name in plugin_directory_names and plugin_name in current_marketplace:
            marketplace_version = current_marketplace[plugin_name].get("version")
            if not isinstance(marketplace_version, str):
                raise VersionPolicyError(
                    f"{plugin_name}: marketplace.json entry version is missing"
                )
            parse_semver(marketplace_version)
            if marketplace_version != current_version:
                failures.append(
                    f"{plugin_name}: plugin.json version ({current_version}) does not "
                    f"match marketplace.json version ({marketplace_version})"
                )

    # check d: the marketplace.json plugin name set and the plugins/ directory
    # name set must match, both ways.
    for name in sorted(set(current_marketplace) - plugin_directory_names):
        failures.append(
            f"{name}: marketplace.json lists this plugin but "
            f"plugins/{name}/{PLUGIN_MANIFEST_RELATIVE_PATH} is missing"
        )
    for name in sorted(plugin_directory_names - set(current_marketplace)):
        failures.append(
            f"{name}: plugins/{name}/{PLUGIN_MANIFEST_RELATIVE_PATH} exists but "
            "marketplace.json does not list it"
        )

    # check e: the root README.md plugin table must list every marketplace
    # plugin with a version matching plugin.json.
    readme_versions = _root_readme_plugin_versions()
    for name in sorted(current_marketplace):
        if name not in readme_versions:
            failures.append(f"{name}: README.md plugin table is missing a row for this plugin")
            continue
        manifest_version = current_manifest_version(name)
        if manifest_version is not None and readme_versions[name] != manifest_version:
            failures.append(
                f"{name}: README.md table version ({readme_versions[name]}) does not "
                f"match plugin.json version ({manifest_version})"
            )

    # check f: plugins/<name>/README.md must carry a `## バージョン` heading with
    # a `vX.Y.Z` line matching plugin.json.
    for name in sorted(plugin_directory_names):
        manifest_version = current_manifest_version(name)
        if manifest_version is None:
            continue
        readme_version = _plugin_readme_version(name)
        if readme_version is None:
            failures.append(
                f"{name}: plugins/{name}/README.md is missing a `## バージョン` heading "
                "with a vX.Y.Z line"
            )
        elif readme_version != manifest_version:
            failures.append(
                f"{name}: plugins/{name}/README.md version ({readme_version}) does not "
                f"match plugin.json version ({manifest_version})"
            )

    return failures


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_revision", help="base commit/ref used for comparison")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="compare base and HEAD trees directly (required for push events/rollbacks)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        failures = check_versions(args.base_revision, direct=args.direct)
    except VersionPolicyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if failures:
        for failure in failures:
            print(f"error: {failure}", file=sys.stderr)
        return 1
    print(f"OK: affected plugin versions are greater than {args.base_revision}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

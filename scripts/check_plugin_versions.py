#!/usr/bin/env python3
"""Require a semver bump for every plugin changed relative to a base Git rev."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class VersionPolicyError(RuntimeError):
    """Raised when Git state or version metadata cannot be inspected."""


def parse_semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise VersionPolicyError(f"invalid strict semver: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def is_feature_or_breaking_bump(
    previous: tuple[int, int, int], current: tuple[int, int, int]
) -> bool:
    """Return whether current is at least a minor bump from previous."""

    return current[0] > previous[0] or (
        current[0] == previous[0] and current[1] > previous[1]
    )


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


def comparison_range(base_revision: str, *, direct: bool) -> str:
    """Use tree-to-tree comparison for pushes and merge-base comparison for PRs."""

    separator = ".." if direct else "..."
    return f"{base_revision}{separator}HEAD"


def changed_plugin_names(base_revision: str, *, direct: bool = False) -> set[str]:
    changed = git(
        "diff", "--name-only", "-z", comparison_range(base_revision, direct=direct)
    ).stdout
    names: set[str] = set()
    for raw_path in changed.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        parts = Path(path).parts
        if len(parts) >= 3 and parts[0] == "plugins":
            names.add(parts[1])

    current_marketplace_payload = load_current_json(
        ROOT / ".claude-plugin" / "marketplace.json"
    )
    base_marketplace_payload = read_json_at_revision(
        base_revision, ".claude-plugin/marketplace.json"
    )
    current_marketplace = _marketplace_plugins(current_marketplace_payload)
    base_marketplace = _marketplace_plugins(base_marketplace_payload)
    for name in set(current_marketplace) & set(base_marketplace):
        current_entry = dict(current_marketplace[name])
        base_entry = dict(base_marketplace[name])
        current_entry.pop("version", None)
        base_entry.pop("version", None)
        if current_entry != base_entry:
            names.add(name)

    current_marketplace_global = dict(current_marketplace_payload or {})
    base_marketplace_global = dict(base_marketplace_payload or {})
    current_marketplace_global.pop("plugins", None)
    base_marketplace_global.pop("plugins", None)
    if current_marketplace_global != base_marketplace_global:
        names.update(current_marketplace)

    current_port = load_current_json(ROOT / "codex" / "marketplace-overrides.json")
    base_port = read_json_at_revision(base_revision, "codex/marketplace-overrides.json")
    current_port_plugins = (
        current_port.get("plugins", {}) if isinstance(current_port, dict) else {}
    )
    base_port_plugins = base_port.get("plugins", {}) if isinstance(base_port, dict) else {}
    if not isinstance(current_port_plugins, dict) or not isinstance(base_port_plugins, dict):
        raise VersionPolicyError("codex port config plugins must be an object")
    for name in set(current_port_plugins) | set(base_port_plugins):
        if current_port_plugins.get(name) != base_port_plugins.get(name):
            names.add(name)
    current_port_global = dict(current_port or {})
    base_port_global = dict(base_port or {})
    current_port_global.pop("plugins", None)
    base_port_global.pop("plugins", None)
    if current_port_global != base_port_global:
        names.update(current_port_plugins)
    return names


def current_version(plugin_name: str) -> str | None:
    manifest = load_current_json(
        ROOT / "plugins" / plugin_name / ".claude-plugin" / "plugin.json"
    )
    if manifest is None:
        return None
    value = manifest.get("version")
    if not isinstance(value, str):
        raise VersionPolicyError(f"{plugin_name}: current manifest version is missing")
    return value


def base_version(base_revision: str, plugin_name: str) -> str | None:
    manifest = read_json_at_revision(
        base_revision, f"plugins/{plugin_name}/.claude-plugin/plugin.json"
    )
    if manifest is None:
        return None
    value = manifest.get("version")
    if not isinstance(value, str):
        raise VersionPolicyError(f"{plugin_name}: base manifest version is missing")
    return value


def introduces_codex_install_surface(base_revision: str, plugin_name: str) -> bool:
    """Detect the backwards-compatible feature addition made by the initial port."""

    current = ROOT / "plugins" / plugin_name / ".codex-plugin" / "plugin.json"
    previous = read_json_at_revision(
        base_revision, f"plugins/{plugin_name}/.codex-plugin/plugin.json"
    )
    return current.is_file() and previous is None


def check_versions(base_revision: str, *, direct: bool = False) -> list[str]:
    git("rev-parse", "--verify", f"{base_revision}^{{commit}}")
    failures: list[str] = []
    for plugin_name in sorted(changed_plugin_names(base_revision, direct=direct)):
        current = current_version(plugin_name)
        previous = base_version(base_revision, plugin_name)
        if current is None:
            # A plugin removed by the change has no cache version to bump.
            continue
        try:
            current_tuple = parse_semver(current)
        except VersionPolicyError as exc:
            failures.append(f"{plugin_name}: {exc}")
            continue
        if previous is None:
            continue
        try:
            previous_tuple = parse_semver(previous)
        except VersionPolicyError as exc:
            failures.append(f"{plugin_name}: base {exc}")
            continue
        if current_tuple <= previous_tuple:
            failures.append(
                f"{plugin_name}: changed plugin requires version > {previous} "
                f"(current {current})"
            )
        elif introduces_codex_install_surface(base_revision, plugin_name) and not (
            is_feature_or_breaking_bump(previous_tuple, current_tuple)
        ):
            failures.append(
                f"{plugin_name}: adding the Codex install surface requires a minor or "
                f"major bump from {previous} (current {current})"
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
    print(f"OK: changed plugin versions are greater than {args.base_revision}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

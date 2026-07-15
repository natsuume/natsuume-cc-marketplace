#!/usr/bin/env python3
"""Require independent runtime semver bumps for changed plugins."""

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


def _without_digest_acknowledgements(value: Any) -> Any:
    """Remove Codex release metadata that does not change its install surface."""

    normalized = copy.deepcopy(value)
    if not isinstance(normalized, dict):
        return normalized
    normalized.pop("version", None)
    normalized.pop("versioning", None)
    distribution = normalized.get("distribution")
    if isinstance(distribution, dict) and distribution.get("status") == "available":
        normalized.pop("distribution", None)
    compatibility = normalized.get("compatibility")
    if not isinstance(compatibility, dict):
        return normalized
    compatibility.pop("sourceTreeDigest", None)
    components = compatibility.get("components")
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                component.pop("sourceDigest", None)
    return normalized


def _port_plugins(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if payload is None:
        return {}
    plugins = payload.get("plugins")
    if not isinstance(plugins, dict):
        raise VersionPolicyError("codex port config plugins must be an object")
    if not all(
        isinstance(name, str) and isinstance(value, dict)
        for name, value in plugins.items()
    ):
        raise VersionPolicyError("codex port config contains an invalid plugin entry")
    return plugins


def _distribution_status(plugin: dict[str, Any] | None) -> str:
    """Return the effective distribution status, including schema v3 defaults."""

    if not isinstance(plugin, dict):
        return "available"
    distribution = plugin.get("distribution")
    if not isinstance(distribution, dict):
        return "available"
    status = distribution.get("status")
    return status if isinstance(status, str) else "available"


def _excluded_in_both(
    plugin_name: str,
    current_plugins: dict[str, dict[str, Any]],
    base_plugins: dict[str, dict[str, Any]],
) -> bool:
    return (
        _distribution_status(current_plugins.get(plugin_name)) == "excluded"
        and _distribution_status(base_plugins.get(plugin_name)) == "excluded"
    )


def _scope_matches(path: str, scope: str) -> bool:
    if scope.endswith("/"):
        return path.startswith(scope)
    return path == scope


def _runtime_scope_for_path(path: str, port: dict[str, Any] | None) -> set[str]:
    """Classify a plugin-relative path, defaulting unknown paths to shared."""

    if path == ".claude-plugin/plugin.json" or path.startswith(".codex-plugin/"):
        return set()
    distribution = port.get("distribution") if isinstance(port, dict) else None
    if isinstance(distribution, dict) and distribution.get("status") == "excluded":
        return {"claude"}
    if path == "README.md":
        return {"either"}
    versioning = port.get("versioning") if isinstance(port, dict) else None
    if not isinstance(versioning, dict):
        return {"claude", "codex"}
    matches: set[str] = set()
    for runtime, field in (
        ("claude", "claudeOnlyPaths"),
        ("codex", "codexOnlyPaths"),
    ):
        scopes = versioning.get(field, [])
        if isinstance(scopes, list) and any(
            isinstance(scope, str) and _scope_matches(path, scope)
            for scope in scopes
        ):
            matches.add(runtime)
    if len(matches) > 1:
        raise VersionPolicyError(
            f"plugin path is assigned to both runtime version scopes: {path}"
        )
    return matches or {"claude", "codex"}


def _manifest_without_version(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    normalized = copy.deepcopy(value)
    normalized.pop("version", None)
    return normalized


def comparison_range(base_revision: str, *, direct: bool) -> str:
    """Use tree-to-tree comparison for pushes and merge-base comparison for PRs."""

    separator = ".." if direct else "..."
    return f"{base_revision}{separator}HEAD"


def changed_runtime_plugins(
    base_revision: str, *, direct: bool = False
) -> dict[str, set[str]]:
    """Return plugin names requiring Claude, Codex, or either runtime bump."""

    current_port = load_current_json(ROOT / "codex" / "marketplace-overrides.json")
    base_port = read_json_at_revision(base_revision, "codex/marketplace-overrides.json")
    current_port_plugins = _port_plugins(current_port)
    base_port_plugins = _port_plugins(base_port)

    changed = git(
        "diff", "--name-only", "-z", comparison_range(base_revision, direct=direct)
    ).stdout
    impacts = {"claude": set(), "codex": set(), "either": set()}
    for raw_path in changed.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        parts = Path(path).parts
        if len(parts) >= 3 and parts[0] == "plugins":
            name = parts[1]
            relative = Path(*parts[2:]).as_posix()
            port = current_port_plugins.get(name) or base_port_plugins.get(name)
            for runtime in _runtime_scope_for_path(relative, port):
                impacts[runtime].add(name)

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
            impacts["claude"].add(name)
            if not _excluded_in_both(
                name, current_port_plugins, base_port_plugins
            ):
                impacts["codex"].add(name)

    current_marketplace_global = dict(current_marketplace_payload or {})
    base_marketplace_global = dict(base_marketplace_payload or {})
    current_marketplace_global.pop("plugins", None)
    base_marketplace_global.pop("plugins", None)
    if current_marketplace_global != base_marketplace_global:
        names = set(current_marketplace) | set(base_marketplace)
        impacts["claude"].update(names)
        impacts["codex"].update(
            name
            for name in names
            if not _excluded_in_both(
                name, current_port_plugins, base_port_plugins
            )
        )

    for name in set(current_port_plugins) | set(base_port_plugins):
        current_distribution = current_port_plugins.get(name, {}).get("distribution")
        base_distribution = base_port_plugins.get(name, {}).get("distribution")
        if (
            isinstance(current_distribution, dict)
            and current_distribution.get("status") == "excluded"
            and isinstance(base_distribution, dict)
            and base_distribution.get("status") == "excluded"
        ):
            continue
        current_plugin = _without_digest_acknowledgements(
            current_port_plugins.get(name)
        )
        base_plugin = _without_digest_acknowledgements(base_port_plugins.get(name))
        if current_plugin != base_plugin:
            impacts["codex"].add(name)
    current_port_global = dict(current_port or {})
    base_port_global = dict(base_port or {})
    current_port_global.pop("plugins", None)
    base_port_global.pop("plugins", None)
    current_port_global.pop("schemaVersion", None)
    base_port_global.pop("schemaVersion", None)
    if current_port_global != base_port_global:
        impacts["codex"].update(set(current_port_plugins) | set(base_port_plugins))

    all_names = (
        set(current_marketplace)
        | set(base_marketplace)
        | set(current_port_plugins)
        | set(base_port_plugins)
    )
    for name in all_names:
        current_manifest = load_current_json(
            ROOT / "plugins" / name / ".claude-plugin" / "plugin.json"
        )
        base_manifest = read_json_at_revision(
            base_revision, f"plugins/{name}/.claude-plugin/plugin.json"
        )
        if _manifest_without_version(current_manifest) != _manifest_without_version(
            base_manifest
        ):
            impacts["claude"].add(name)
            if not _excluded_in_both(
                name, current_port_plugins, base_port_plugins
            ):
                impacts["codex"].add(name)
    return impacts


def changed_plugin_names(base_revision: str, *, direct: bool = False) -> set[str]:
    """Compatibility helper returning the union of all runtime impacts."""

    impacts = changed_runtime_plugins(base_revision, direct=direct)
    return set().union(*impacts.values())


def current_claude_version(plugin_name: str) -> str | None:
    manifest = load_current_json(
        ROOT / "plugins" / plugin_name / ".claude-plugin" / "plugin.json"
    )
    if manifest is None:
        return None
    value = manifest.get("version")
    if not isinstance(value, str):
        raise VersionPolicyError(f"{plugin_name}: current manifest version is missing")
    return value


def base_claude_version(base_revision: str, plugin_name: str) -> str | None:
    manifest = read_json_at_revision(
        base_revision, f"plugins/{plugin_name}/.claude-plugin/plugin.json"
    )
    if manifest is None:
        return None
    value = manifest.get("version")
    if not isinstance(value, str):
        raise VersionPolicyError(f"{plugin_name}: base manifest version is missing")
    return value


def current_codex_version(plugin_name: str) -> str | None:
    port = load_current_json(ROOT / "codex" / "marketplace-overrides.json")
    plugin = _port_plugins(port).get(plugin_name)
    if plugin is None:
        return None
    value = plugin.get("version")
    if not isinstance(value, str):
        raise VersionPolicyError(f"{plugin_name}: current Codex version is missing")
    return value


def base_codex_version(base_revision: str, plugin_name: str) -> str | None:
    port = read_json_at_revision(base_revision, "codex/marketplace-overrides.json")
    plugin = _port_plugins(port).get(plugin_name)
    if plugin is not None and "version" in plugin:
        value = plugin["version"]
        if not isinstance(value, str):
            raise VersionPolicyError(f"{plugin_name}: base Codex version is invalid")
        return value

    # schemaVersion 3 generated the Codex version from the Claude manifest. Use
    # the committed generated manifest as the migration baseline so adopting the
    # independent source does not force a release by itself.
    manifest = read_json_at_revision(
        base_revision, f"plugins/{plugin_name}/.codex-plugin/plugin.json"
    )
    if manifest is None:
        return None
    value = manifest.get("version")
    if not isinstance(value, str):
        raise VersionPolicyError(f"{plugin_name}: base Codex manifest version is missing")
    return value


def _parsed_version(
    plugin_name: str, runtime: str, value: str | None, *, base: bool
) -> tuple[int, int, int] | None:
    if value is None:
        return None
    try:
        return parse_semver(value)
    except VersionPolicyError as exc:
        prefix = "base " if base else ""
        raise VersionPolicyError(f"{plugin_name}: {prefix}{runtime} {exc}") from exc


def _require_runtime_bump(
    failures: list[str],
    plugin_name: str,
    runtime: str,
    current: str | None,
    previous: str | None,
) -> None:
    current_tuple = _parsed_version(plugin_name, runtime, current, base=False)
    previous_tuple = _parsed_version(plugin_name, runtime, previous, base=True)
    if current_tuple is None:
        return
    if previous_tuple is not None and current_tuple <= previous_tuple:
        failures.append(
            f"{plugin_name}: {runtime} changes require {runtime} version > "
            f"{previous} (current {current})"
        )


def check_versions(base_revision: str, *, direct: bool = False) -> list[str]:
    git("rev-parse", "--verify", f"{base_revision}^{{commit}}")
    impacts = changed_runtime_plugins(base_revision, direct=direct)
    failures: list[str] = []
    current_marketplace = _marketplace_plugins(
        load_current_json(ROOT / ".claude-plugin" / "marketplace.json")
    )
    base_marketplace = _marketplace_plugins(
        read_json_at_revision(base_revision, ".claude-plugin/marketplace.json")
    )
    current_port = _port_plugins(
        load_current_json(ROOT / "codex" / "marketplace-overrides.json")
    )
    base_port = _port_plugins(
        read_json_at_revision(base_revision, "codex/marketplace-overrides.json")
    )
    all_names = (
        set().union(*impacts.values())
        | set(current_marketplace)
        | set(base_marketplace)
        | set(current_port)
        | set(base_port)
    )
    for plugin_name in sorted(all_names):
        versions = {
            "claude": (
                current_claude_version(plugin_name),
                base_claude_version(base_revision, plugin_name),
            ),
            "codex": (
                current_codex_version(plugin_name),
                base_codex_version(base_revision, plugin_name),
            ),
        }
        for runtime in ("claude", "codex"):
            current, previous = versions[runtime]
            current_tuple = _parsed_version(
                plugin_name, runtime, current, base=False
            )
            previous_tuple = _parsed_version(
                plugin_name, runtime, previous, base=True
            )
            if (
                current_tuple is not None
                and previous_tuple is not None
                and current_tuple < previous_tuple
            ):
                failures.append(
                    f"{plugin_name}: {runtime} version regressed from {previous} "
                    f"to {current}"
                )
        for runtime in ("claude", "codex"):
            if plugin_name in impacts[runtime]:
                _require_runtime_bump(
                    failures,
                    plugin_name,
                    runtime,
                    *versions[runtime],
                )
        current_distribution = _distribution_status(current_port.get(plugin_name))
        base_distribution = _distribution_status(base_port.get(plugin_name))
        current_codex, base_codex = versions["codex"]
        current_codex_tuple = _parsed_version(
            plugin_name, "codex", current_codex, base=False
        )
        base_codex_tuple = _parsed_version(
            plugin_name, "codex", base_codex, base=True
        )
        codex_version_increased = (
            current_codex_tuple is not None
            and base_codex_tuple is not None
            and current_codex_tuple > base_codex_tuple
        )
        if (
            base_distribution == "available"
            and current_distribution == "excluded"
            and codex_version_increased
            and current_codex_tuple[0] <= base_codex_tuple[0]
        ):
            failures.append(
                f"{plugin_name}: Codex distribution available -> excluded requires "
                f"a major version bump from {base_codex} (current {current_codex})"
            )
        if (
            base_distribution == "excluded"
            and current_distribution == "available"
            and codex_version_increased
            and not is_feature_or_breaking_bump(
                base_codex_tuple, current_codex_tuple
            )
        ):
            failures.append(
                f"{plugin_name}: Codex distribution excluded -> available requires "
                f"a minor or major version bump from {base_codex} "
                f"(current {current_codex})"
            )
        if plugin_name in impacts["either"]:
            bumped = False
            for runtime in ("claude", "codex"):
                current, previous = versions[runtime]
                current_tuple = _parsed_version(
                    plugin_name, runtime, current, base=False
                )
                previous_tuple = _parsed_version(
                    plugin_name, runtime, previous, base=True
                )
                if current_tuple is not None and (
                    previous_tuple is None or current_tuple > previous_tuple
                ):
                    bumped = True
            if not bumped:
                failures.append(
                    f"{plugin_name}: plugin documentation changes require at least "
                    "one runtime version bump"
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
    print(f"OK: affected runtime versions are greater than {args.base_revision}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

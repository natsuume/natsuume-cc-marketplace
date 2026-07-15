#!/usr/bin/env python3
"""Generate and verify the Codex marketplace from the Claude marketplace.

The Claude marketplace is the canonical authoring surface. Codex-specific UX
metadata and explicit compatibility exceptions live in
``codex/marketplace-overrides.json``. Generated files are committed so a
marketplace installed from any Git ref is immediately usable.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MARKETPLACE_PATH = ROOT / ".claude-plugin" / "marketplace.json"
CLAUDE_RULES_PATH = ROOT / ".claude" / "CLAUDE.md"
PORT_CONFIG_PATH = ROOT / "codex" / "marketplace-overrides.json"
CODEX_MARKETPLACE_PATH = ROOT / ".agents" / "plugins" / "marketplace.json"
COMPATIBILITY_DOC_PATH = ROOT / "docs" / "codex-compatibility.md"
AGENTS_PATH = ROOT / "AGENTS.md"
README_PATH = ROOT / "README.md"

SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
README_VERSION_RE = re.compile(
    r"^\| \[([a-z0-9-]+)\]\(#[^)]+\) \| ([0-9]+\.[0-9]+\.[0-9]+) \|",
    re.MULTILINE,
)
PLUGIN_README_VERSION_RE = re.compile(
    r"^## バージョン\s*\n+\s*v([0-9]+\.[0-9]+\.[0-9]+)(?:\s+.*)?$",
    re.MULTILINE,
)
CODEX_HOOK_EVENTS = {
    "PermissionRequest",
    "PostCompact",
    "PostToolUse",
    "PreCompact",
    "PreToolUse",
    "SessionStart",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "UserPromptSubmit",
}
CODEX_TOOL_MATCHERS = {
    "",
    "*",
    "Bash",
    "^Bash$",
    "apply_patch",
    "^apply_patch$",
    "Edit",
    "Write",
    "Edit|Write",
    "Write|Edit",
    # Claude's MultiEdit is represented by Codex's apply_patch; Edit and Write
    # are documented Codex aliases, so this existing union still matches.
    "Write|Edit|MultiEdit",
}
CODEX_COMMAND_HANDLER_FIELDS = {
    "type",
    "command",
    "commandWindows",
    "timeout",
    "statusMessage",
    "async",
}
STRUCTURAL_COMPONENT_DIRS = (
    "commands",
    "agents",
    "statusline",
    "output-styles",
    "monitors",
    "themes",
    "bin",
)
STRUCTURAL_COMPONENT_FILES = (".mcp.json", ".lsp.json", "settings.json")
PLUGIN_MANIFEST_CORE_FIELDS = {"$schema", "name", "version", "description"}
PLUGIN_MANIFEST_MAPPED_FIELDS = {"author"}
PLUGIN_MANIFEST_DECLARATION_FIELDS = {
    "displayName",
    "homepage",
    "repository",
    "license",
    "keywords",
    "defaultEnabled",
    "skills",
    "commands",
    "agents",
    "hooks",
    "mcpServers",
    "outputStyles",
    "lspServers",
    "dependencies",
    "userConfig",
    "channels",
}
PLUGIN_EXPERIMENTAL_FIELDS = {"themes", "monitors"}
MARKETPLACE_PLUGIN_CORE_FIELDS = {
    "$schema",
    "name",
    "source",
    "version",
    "description",
    "keywords",
}
MARKETPLACE_PLUGIN_DECLARATION_FIELDS = {
    "displayName",
    "author",
    "homepage",
    "repository",
    "license",
    "category",
    "tags",
    "strict",
    "relevance",
    "defaultEnabled",
    "skills",
    "commands",
    "agents",
    "hooks",
    "mcpServers",
    "outputStyles",
    "lspServers",
    "dependencies",
    "userConfig",
    "channels",
}
PLUGIN_BEHAVIOR_EXCLUDED_TOP_LEVEL = {".codex-plugin"}
ALLOWED_COMPATIBILITY_LEVELS = {"full", "partial", "metadata-only"}
ALLOWED_DISPOSITIONS = {
    "adapted",
    "surface-unavailable",
    "host-required",
    "not-applicable",
}
PORT_CONFIG_TOP_LEVEL_FIELDS = {"schemaVersion", "marketplace", "publisher", "plugins"}
PORT_CONFIG_MARKETPLACE_FIELDS = {"displayName", "category"}
PORT_CONFIG_PUBLISHER_FIELDS = {"name", "url", "repository", "license"}
PORT_CONFIG_PLUGIN_FIELDS = {
    "displayName",
    "shortDescription",
    "defaultPrompt",
    "compatibility",
}
PORT_CONFIG_COMPATIBILITY_FIELDS = {
    "level",
    "summary",
    "limitations",
    "guaranteeDifferences",
    "verificationTests",
    "components",
    "sourceTreeDigest",
}
PORT_CONFIG_VERIFICATION_FIELDS = {"path", "covers"}
PORT_CONFIG_COMPONENT_FIELDS = {
    "source",
    "sourceDigest",
    "disposition",
    "codexReplacement",
    "reason",
}
SOURCE_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
HOOK_COMPONENT_RE = re.compile(
    r"^hooks/hooks\.json#/hooks/([^/]+)/([0-9]+)/hooks/([0-9]+)$"
)
JSON_COMPONENT_RE = re.compile(r"^(.+\.json)#(/.*)$")
MARKETPLACE_COMPONENT_RE = re.compile(r"^@marketplace#(/.*)$")


class SyncError(RuntimeError):
    """Raised when the canonical marketplace or port registry is invalid."""


@dataclass(frozen=True)
class PluginState:
    name: str
    root: Path
    marketplace: dict[str, Any]
    manifest: dict[str, Any]
    port: dict[str, Any]
    skill_count: int
    command_hook_count: int
    hook_count: int


@dataclass(frozen=True)
class RepositoryState:
    marketplace: dict[str, Any]
    config: dict[str, Any]
    plugins: tuple[PluginState, ...]


@dataclass(frozen=True)
class DigestChange:
    plugin: str
    label: str
    target_path: tuple[str | int, ...]
    previous: object
    current: str | int


@dataclass(frozen=True)
class DigestRefreshPlan:
    candidate: dict[str, Any]
    changes: tuple[DigestChange, ...]
    token: str
    config_bytes: bytes
    marketplace_bytes: bytes


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SyncError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_bytes(path: Path, raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, SyncError) as exc:
        raise SyncError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(value, dict):
        raise SyncError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SyncError(f"unable to read {path.relative_to(ROOT)}: {exc}") from exc
    return _load_json_bytes(path, raw)


def assert_safe_repo_path(root: Path, path: Path) -> None:
    """Reject paths that escape ``root`` lexically or through symlinks."""

    root = root.resolve(strict=True)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SyncError(f"managed path is outside repository: {path}") from exc

    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise SyncError(f"managed path must not contain symlinks: {relative}")

    try:
        path.parent.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise SyncError(f"managed path resolves outside repository: {relative}") from exc


def _reject_plugin_symlinks(plugin_root: Path) -> None:
    assert_safe_repo_path(ROOT, plugin_root)
    for path in plugin_root.rglob("*"):
        if path.is_symlink():
            raise SyncError(
                f"plugin content must not use symlinks: {path.relative_to(ROOT)}"
            )


def require_non_empty_string(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SyncError(f"{label}.{key} must be a non-empty string")
    return value


def require_string_list(mapping: dict[str, Any], key: str, label: str) -> list[str]:
    value = mapping.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise SyncError(f"{label}.{key} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise SyncError(f"{label}.{key} must not contain duplicates")
    return value


def _reject_unknown_fields(
    mapping: dict[str, Any], allowed: set[str], label: str
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise SyncError(
            f"{label}: unknown Codex port config field(s) must be classified "
            "before sync: " + ", ".join(unknown)
        )


def _validate_port_config_known_fields(config: dict[str, Any]) -> None:
    """Fail closed for every schema-v3 registry object, including refresh mode."""

    _reject_unknown_fields(config, PORT_CONFIG_TOP_LEVEL_FIELDS, "config")
    marketplace = config.get("marketplace")
    if marketplace is not None:
        if not isinstance(marketplace, dict):
            raise SyncError("config.marketplace must be an object")
        _reject_unknown_fields(
            marketplace, PORT_CONFIG_MARKETPLACE_FIELDS, "config.marketplace"
        )
    publisher = config.get("publisher")
    if publisher is not None:
        if not isinstance(publisher, dict):
            raise SyncError("config.publisher must be an object")
        _reject_unknown_fields(
            publisher, PORT_CONFIG_PUBLISHER_FIELDS, "config.publisher"
        )
    plugins = config.get("plugins")
    if plugins is None:
        return
    if not isinstance(plugins, dict):
        raise SyncError("config.plugins must be an object")
    for plugin_name, port in plugins.items():
        label = f"config.plugins.{plugin_name}"
        if not isinstance(port, dict):
            raise SyncError(f"{label} must be an object")
        _reject_unknown_fields(port, PORT_CONFIG_PLUGIN_FIELDS, label)
        compatibility = port.get("compatibility")
        if compatibility is None:
            continue
        if not isinstance(compatibility, dict):
            raise SyncError(f"{label}.compatibility must be an object")
        compatibility_label = f"{label}.compatibility"
        _reject_unknown_fields(
            compatibility,
            PORT_CONFIG_COMPATIBILITY_FIELDS,
            compatibility_label,
        )
        verification_tests = compatibility.get("verificationTests")
        if isinstance(verification_tests, list):
            for index, verification in enumerate(verification_tests):
                if not isinstance(verification, dict):
                    continue
                _reject_unknown_fields(
                    verification,
                    PORT_CONFIG_VERIFICATION_FIELDS,
                    f"{compatibility_label}.verificationTests[{index}]",
                )
        components = compatibility.get("components")
        if isinstance(components, list):
            for index, component in enumerate(components):
                if not isinstance(component, dict):
                    continue
                _reject_unknown_fields(
                    component,
                    PORT_CONFIG_COMPONENT_FIELDS,
                    f"{compatibility_label}.components[{index}]",
                )


def _validate_marketplace_header(
    marketplace: dict[str, Any], config: dict[str, Any]
) -> None:
    """Fail closed when marketplace-wide Claude semantics are not mapped."""

    allowed_top_level = {"name", "owner", "metadata", "plugins"}
    unknown = sorted(set(marketplace) - allowed_top_level)
    if unknown:
        raise SyncError(
            "Claude marketplace top-level field(s) require explicit Codex mapping "
            "before sync: " + ", ".join(unknown)
        )
    owner = marketplace.get("owner")
    if not isinstance(owner, dict) or set(owner) != {"name"}:
        raise SyncError(
            "Claude marketplace.owner must contain only name until additional owner "
            "metadata has an explicit Codex mapping"
        )
    owner_name = require_non_empty_string(owner, "name", "marketplace.owner")
    publisher = config.get("publisher")
    if not isinstance(publisher, dict) or publisher.get("name") != owner_name:
        raise SyncError(
            "Claude marketplace owner.name must match Codex publisher.name"
        )
    metadata = marketplace.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != {"description"}:
        raise SyncError(
            "Claude marketplace.metadata must contain only description until other "
            "marketplace metadata has an explicit Codex mapping"
        )
    require_non_empty_string(metadata, "description", "marketplace.metadata")


def _validate_mapped_manifest_metadata(
    plugin_name: str, manifest: dict[str, Any], config: dict[str, Any]
) -> None:
    publisher = config.get("publisher")
    if not isinstance(publisher, dict):
        raise SyncError("Codex port config requires publisher metadata")
    expected_author = {
        "name": require_non_empty_string(publisher, "name", "config.publisher"),
        "url": require_non_empty_string(publisher, "url", "config.publisher"),
    }
    if manifest.get("author") != expected_author:
        raise SyncError(
            f"{plugin_name}: Claude manifest author must exactly match the mapped "
            "Codex publisher name/url"
        )


def _relative_file_paths(root: Path, directory: str) -> set[str]:
    component_root = root / directory
    if not component_root.is_dir():
        return set()
    return {
        path.relative_to(root).as_posix()
        for path in component_root.rglob("*")
        if path.is_file()
    }


def _load_hooks(plugin_root: Path) -> dict[str, Any] | None:
    hooks_path = plugin_root / "hooks" / "hooks.json"
    if not hooks_path.is_file():
        return None
    return load_json(hooks_path)


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _manifest_component_tokens(
    plugin_root: Path, manifest: dict[str, Any] | None = None
) -> set[str]:
    manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
    if manifest is None:
        if not manifest_path.is_file():
            return set()
        manifest = load_json(manifest_path)

    known = (
        PLUGIN_MANIFEST_CORE_FIELDS
        | PLUGIN_MANIFEST_MAPPED_FIELDS
        | PLUGIN_MANIFEST_DECLARATION_FIELDS
        | {"experimental"}
    )
    unknown = sorted(set(manifest) - known)
    if unknown:
        raise SyncError(
            f"{plugin_root.name}: unknown Claude plugin manifest field(s) must be "
            "classified before Codex sync: " + ", ".join(unknown)
        )

    tokens = {
        ".claude-plugin/plugin.json#/" + _json_pointer_token(field)
        for field in PLUGIN_MANIFEST_DECLARATION_FIELDS
        if field in manifest
    }
    if "experimental" in manifest:
        experimental = manifest["experimental"]
        if not isinstance(experimental, dict):
            raise SyncError(
                f"{plugin_root.name}: plugin manifest experimental must be an object"
            )
        unknown_experimental = sorted(
            set(experimental) - PLUGIN_EXPERIMENTAL_FIELDS
        )
        if unknown_experimental:
            raise SyncError(
                f"{plugin_root.name}: unknown Claude experimental component(s) must "
                "be classified before Codex sync: "
                + ", ".join(unknown_experimental)
            )
        tokens.update(
            ".claude-plugin/plugin.json#/experimental/"
            + _json_pointer_token(field)
            for field in PLUGIN_EXPERIMENTAL_FIELDS
            if field in experimental
        )
    return tokens


def _marketplace_component_tokens(
    plugin_root: Path,
    marketplace_entry: dict[str, Any] | None,
    marketplace_index: int | None,
) -> set[str]:
    if marketplace_entry is None:
        return set()
    if marketplace_index is None or marketplace_index < 0:
        raise SyncError(f"{plugin_root.name}: marketplace index is required")

    known = (
        MARKETPLACE_PLUGIN_CORE_FIELDS
        | MARKETPLACE_PLUGIN_DECLARATION_FIELDS
        | {"experimental"}
    )
    unknown = sorted(set(marketplace_entry) - known)
    if unknown:
        raise SyncError(
            f"{plugin_root.name}: unknown Claude marketplace plugin field(s) must be "
            "classified before Codex sync: " + ", ".join(unknown)
        )

    prefix = f"@marketplace#/plugins/{marketplace_index}/"
    tokens = {
        prefix + _json_pointer_token(field)
        for field in MARKETPLACE_PLUGIN_DECLARATION_FIELDS
        if field in marketplace_entry
    }
    if "experimental" in marketplace_entry:
        experimental = marketplace_entry["experimental"]
        if not isinstance(experimental, dict):
            raise SyncError(
                f"{plugin_root.name}: marketplace experimental must be an object"
            )
        unknown_experimental = sorted(
            set(experimental) - PLUGIN_EXPERIMENTAL_FIELDS
        )
        if unknown_experimental:
            raise SyncError(
                f"{plugin_root.name}: unknown marketplace experimental component(s) "
                "must be classified before Codex sync: "
                + ", ".join(unknown_experimental)
            )
        tokens.update(
            prefix + "experimental/" + _json_pointer_token(field)
            for field in PLUGIN_EXPERIMENTAL_FIELDS
            if field in experimental
        )
    return tokens


def _codex_hook_matcher_supported(event: str, matcher: str) -> bool:
    try:
        re.compile(matcher if matcher not in {"", "*"} else ".*")
    except re.error:
        return False
    if event in {"PermissionRequest", "PostToolUse", "PreToolUse"}:
        return matcher in CODEX_TOOL_MATCHERS or matcher.startswith("mcp__")
    if event in {"PostCompact", "PreCompact"}:
        return matcher in {"", "*", "manual", "auto", "manual|auto", "auto|manual"}
    if event == "SessionStart":
        allowed = {"startup", "resume", "clear", "compact"}
        if matcher in {"", "*"}:
            return True
        return set(matcher.split("|")) <= allowed
    if event in {"SubagentStart", "SubagentStop"}:
        return True
    if event in {"UserPromptSubmit", "Stop"}:
        return matcher in {"", "*"}
    return matcher in {"", "*"}


def _codex_command_handler_supported(handler: dict[str, Any]) -> bool:
    if handler.get("type") != "command":
        return False
    command = handler.get("command")
    if not isinstance(command, str) or not command.strip():
        return False
    if set(handler) - CODEX_COMMAND_HANDLER_FIELDS:
        return False
    if handler.get("async") not in {None, False}:
        return False
    return True


def discover_component_differences(
    plugin_root: Path,
    manifest: dict[str, Any] | None = None,
    marketplace_entry: dict[str, Any] | None = None,
    marketplace_index: int | None = None,
) -> set[str]:
    """Return Claude components that cannot be consumed unchanged by Codex."""

    components: set[str] = set()
    for directory in STRUCTURAL_COMPONENT_DIRS:
        components.update(_relative_file_paths(plugin_root, directory))
    for filename in STRUCTURAL_COMPONENT_FILES:
        if (plugin_root / filename).is_file():
            components.add(filename)

    effective_manifest = manifest
    if effective_manifest is None:
        manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
        if manifest_path.is_file():
            effective_manifest = load_json(manifest_path)
    if (
        (plugin_root / "SKILL.md").is_file()
        and not (plugin_root / "skills").is_dir()
        and not (isinstance(effective_manifest, dict) and "skills" in effective_manifest)
    ):
        components.add("SKILL.md")

    components.update(_manifest_component_tokens(plugin_root, effective_manifest))
    components.update(
        _marketplace_component_tokens(
            plugin_root, marketplace_entry, marketplace_index
        )
    )

    hooks = _load_hooks(plugin_root)
    if hooks is None:
        return components

    hook_map = hooks.get("hooks")
    if not isinstance(hook_map, dict):
        raise SyncError(f"{plugin_root.name}/hooks/hooks.json.hooks must be an object")

    for event, groups in hook_map.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            raise SyncError(f"{plugin_root.name}: invalid hook event entry")
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                raise SyncError(
                    f"{plugin_root.name}: hook group {event}/{group_index} must be an object"
                )
            matcher = group.get("matcher", "")
            handlers = group.get("hooks")
            if not isinstance(matcher, str) or not isinstance(handlers, list):
                raise SyncError(
                    f"{plugin_root.name}: hook group {event}/{group_index} is malformed"
                )
            for handler_index, handler in enumerate(handlers):
                if not isinstance(handler, dict):
                    raise SyncError(
                        f"{plugin_root.name}: hook handler {event}/{group_index}/{handler_index} must be an object"
                    )
                token = (
                    "hooks/hooks.json#/hooks/"
                    f"{event}/{group_index}/hooks/{handler_index}"
                )
                if (
                    event not in CODEX_HOOK_EVENTS
                    or set(group) - {"matcher", "hooks"}
                    or not _codex_hook_matcher_supported(event, matcher)
                    or not _codex_command_handler_supported(handler)
                ):
                    components.add(token)
    return components


def _resolve_json_pointer(value: Any, pointer: str, source: str) -> Any:
    current = value
    for encoded_part in pointer.lstrip("/").split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                raise SyncError(f"JSON component pointer does not exist: {source}")
            current = current[part]
        elif isinstance(current, list):
            if not part.isdecimal():
                raise SyncError(f"invalid JSON array pointer: {source}")
            index = int(part)
            if index >= len(current):
                raise SyncError(f"JSON component pointer does not exist: {source}")
            current = current[index]
        else:
            raise SyncError(f"JSON component pointer traverses a scalar: {source}")
    return current


def _json_component_payload(path: Path, pointer: str, source: str) -> bytes:
    payload = load_json(path)
    value = _resolve_json_pointer(payload, pointer, source)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def component_source_payload(plugin_root: Path, source: str) -> bytes:
    """Return the canonical bytes whose change requires adapter re-review."""

    hook_match = HOOK_COMPONENT_RE.fullmatch(source)
    if hook_match is not None:
        event = hook_match.group(1).replace("~1", "/").replace("~0", "~")
        group_index = int(hook_match.group(2))
        handler_index = int(hook_match.group(3))
        hooks = _load_hooks(plugin_root)
        try:
            group = hooks["hooks"][event][group_index] if hooks is not None else None
            handler = group["hooks"][handler_index]
            matcher = group.get("matcher", "")
        except (KeyError, IndexError, TypeError) as exc:
            raise SyncError(f"{plugin_root.name}: invalid hook component {source}") from exc
        payload = {
            "event": event,
            "matcher": matcher,
            "handler": handler,
        }
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    marketplace_match = MARKETPLACE_COMPONENT_RE.fullmatch(source)
    if marketplace_match is not None:
        assert_safe_repo_path(ROOT, CLAUDE_MARKETPLACE_PATH)
        return _json_component_payload(
            CLAUDE_MARKETPLACE_PATH, marketplace_match.group(1), source
        )

    json_match = JSON_COMPONENT_RE.fullmatch(source)
    if json_match is not None:
        relative = Path(json_match.group(1))
        source_path = plugin_root / relative
        if relative.is_absolute() or ".." in relative.parts:
            raise SyncError(
                f"{plugin_root.name}: invalid component source path {source!r}"
            )
        assert_safe_repo_path(plugin_root, source_path)
        return _json_component_payload(source_path, json_match.group(2), source)

    relative = Path(source)
    source_path = plugin_root / relative
    if relative.is_absolute() or ".." in relative.parts:
        raise SyncError(f"{plugin_root.name}: invalid component source path {source!r}")
    assert_safe_repo_path(plugin_root, source_path)
    if not source_path.is_file():
        raise SyncError(f"{plugin_root.name}: component source does not exist: {source}")
    return source_path.read_bytes()


def component_source_digest(plugin_root: Path, source: str) -> str:
    return hashlib.sha256(component_source_payload(plugin_root, source)).hexdigest()


def is_plugin_behavior_path(relative: Path) -> bool:
    return bool(relative.parts) and not (
        relative.parts[0] in PLUGIN_BEHAVIOR_EXCLUDED_TOP_LEVEL
        or "__pycache__" in relative.parts
        or relative.suffix in {".pyc", ".pyo"}
        or relative.name == ".DS_Store"
    )


def plugin_source_tree_digest(
    plugin_root: Path, verification_test_paths: Iterable[str] = ()
) -> str:
    """Fingerprint plugin behavior and its declared verification evidence."""

    digest = hashlib.sha256()
    for path in sorted(
        (candidate for candidate in plugin_root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(plugin_root).as_posix(),
    ):
        relative = path.relative_to(plugin_root)
        if not is_plugin_behavior_path(relative):
            continue
        assert_safe_repo_path(plugin_root, path)
        executable = b"x" if path.stat().st_mode & stat.S_IXUSR else b"-"
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(executable)
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\0")
    for test_path in sorted(set(verification_test_paths)):
        relative = Path(test_path)
        path = ROOT / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.is_file()
        ):
            raise SyncError(
                f"verification evidence path is not a safe repository file: {test_path}"
            )
        assert_safe_repo_path(ROOT, path)
        executable = b"x" if path.stat().st_mode & stat.S_IXUSR else b"-"
        digest.update(b"@verification\0")
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(executable)
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\0")
    return digest.hexdigest()


def _count_hooks(plugin_root: Path) -> tuple[int, int]:
    hooks = _load_hooks(plugin_root)
    if hooks is None:
        return 0, 0
    command_count = 0
    total = 0
    for groups in hooks.get("hooks", {}).values():
        for group in groups:
            for handler in group.get("hooks", []):
                total += 1
                if handler.get("type") == "command":
                    command_count += 1
    return command_count, total


def _validate_skill_manifests(plugin_root: Path) -> int:
    skills_root = plugin_root / "skills"
    if not skills_root.is_dir():
        return 0
    count = 0
    for skill_root in sorted(skills_root.iterdir()):
        if not skill_root.is_dir() or skill_root.name.startswith("."):
            continue
        skill_path = skill_root / "SKILL.md"
        if not skill_path.is_file():
            raise SyncError(
                f"{skill_root.relative_to(ROOT)} is missing required SKILL.md"
            )
        contents = skill_path.read_text(encoding="utf-8")
        if not contents.startswith("---\n"):
            raise SyncError(f"{skill_path.relative_to(ROOT)} has no YAML frontmatter")
        end = contents.find("\n---", 4)
        if end == -1:
            raise SyncError(f"{skill_path.relative_to(ROOT)} has unclosed frontmatter")
        frontmatter = contents[4:end]
        frontmatter_keys = set(
            re.findall(r"^([A-Za-z0-9_-]+):", frontmatter, re.MULTILINE)
        )
        if frontmatter_keys != {"name", "description"}:
            raise SyncError(
                f"{skill_path.relative_to(ROOT)} frontmatter must use the shared "
                "Claude/Codex intersection (name and description only); "
                f"found {sorted(frontmatter_keys)}"
            )
        name_match = re.search(r"^name:\s*(\S.*?)\s*$", frontmatter, re.MULTILINE)
        description_match = re.search(
            r"^description:\s*(\S.*?)\s*$", frontmatter, re.MULTILINE
        )
        if name_match is None or description_match is None:
            raise SyncError(
                f"{skill_path.relative_to(ROOT)} requires non-empty name and description"
            )
        if name_match.group(1) != skill_root.name:
            raise SyncError(
                f"{skill_path.relative_to(ROOT)} name must match its directory"
            )
        count += 1
    return count


def _verification_path_is_ci_executed(path: Path) -> bool:
    return (
        len(path.parts) == 2
        and path.parts[0] == "tests"
        and path.name.startswith("test_")
        and path.suffix == ".py"
    ) or path.as_posix() == "scripts/smoke_codex_marketplace.sh"


def _validate_component_registry(
    plugin_name: str,
    plugin_root: Path,
    compatibility: dict[str, Any],
    manifest: dict[str, Any] | None = None,
    marketplace_entry: dict[str, Any] | None = None,
    marketplace_index: int | None = None,
    stale_digests: dict[str, list[str]] | None = None,
) -> None:
    level = require_non_empty_string(
        compatibility, "level", f"config.plugins.{plugin_name}.compatibility"
    )
    if level not in ALLOWED_COMPATIBILITY_LEVELS:
        raise SyncError(f"{plugin_name}: unsupported compatibility level {level!r}")
    require_non_empty_string(
        compatibility, "summary", f"config.plugins.{plugin_name}.compatibility"
    )
    limitations = require_string_list(
        compatibility, "limitations", f"config.plugins.{plugin_name}.compatibility"
    )
    guarantee_differences = require_string_list(
        compatibility,
        "guaranteeDifferences",
        f"config.plugins.{plugin_name}.compatibility",
    )
    verification_tests = compatibility.get("verificationTests")
    if not isinstance(verification_tests, list) or not verification_tests:
        raise SyncError(
            f"{plugin_name}: compatibility.verificationTests must be a non-empty array"
        )
    for index, verification in enumerate(verification_tests):
        label = (
            f"config.plugins.{plugin_name}.compatibility.verificationTests[{index}]"
        )
        if not isinstance(verification, dict):
            raise SyncError(f"{label} must be an object")
        test_path = require_non_empty_string(verification, "path", label)
        require_non_empty_string(verification, "covers", label)
        relative_path = Path(test_path)
        verification_path = ROOT / relative_path
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or not verification_path.is_file()
        ):
            raise SyncError(f"{label}.path must reference an existing repository file")
        assert_safe_repo_path(ROOT, verification_path)
        if not _verification_path_is_ci_executed(relative_path):
            raise SyncError(
                f"{label}.path is not executed by the declared CI runners; use a "
                "tests/test_*.py unittest-discovery file or "
                "scripts/smoke_codex_marketplace.sh"
            )
    entries = compatibility.get("components")
    if not isinstance(entries, list):
        raise SyncError(f"{plugin_name}: compatibility.components must be an array")

    if level == "full" and (limitations or guarantee_differences or entries):
        raise SyncError(
            f"{plugin_name}: full compatibility cannot declare limitations, "
            "guarantee differences, or components"
        )
    if level != "full" and (not limitations or not guarantee_differences):
        raise SyncError(
            f"{plugin_name}: non-full compatibility requires limitations and "
            "guaranteeDifferences"
        )

    source_tree_digest = compatibility.get("sourceTreeDigest")
    if (
        not isinstance(source_tree_digest, str)
        or SOURCE_DIGEST_RE.fullmatch(source_tree_digest) is None
    ):
        raise SyncError(f"{plugin_name}: compatibility requires sourceTreeDigest")
    verification_paths = [item["path"] for item in verification_tests]
    if source_tree_digest != plugin_source_tree_digest(plugin_root, verification_paths):
        if stale_digests is None:
            raise SyncError(
                f"{plugin_name}: compatibility.sourceTreeDigest is stale: plugin "
                "behavior changed; inspect adapters/guarantees/tests, then run "
                "python3 scripts/sync_codex_marketplace.py "
                f"--refresh-source-digests --plugin {plugin_name}"
            )
        stale_digests.setdefault(plugin_name, []).append("sourceTreeDigest")

    registered: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"config.plugins.{plugin_name}.compatibility.components[{index}]"
        if not isinstance(entry, dict):
            raise SyncError(f"{label} must be an object")
        source = require_non_empty_string(entry, "source", label)
        source_digest = require_non_empty_string(entry, "sourceDigest", label)
        disposition = require_non_empty_string(entry, "disposition", label)
        require_non_empty_string(entry, "reason", label)
        if disposition not in ALLOWED_DISPOSITIONS:
            raise SyncError(f"{label}.disposition is invalid: {disposition!r}")
        if source in registered:
            raise SyncError(f"{plugin_name}: duplicate component registry entry {source}")
        registered.add(source)
        if SOURCE_DIGEST_RE.fullmatch(source_digest) is None:
            raise SyncError(f"{label}.sourceDigest must be a lowercase SHA-256 digest")
        current_digest = component_source_digest(plugin_root, source)
        if source_digest != current_digest:
            if stale_digests is None:
                raise SyncError(
                    f"{label}.sourceDigest is stale: Claude source changed; inspect the "
                    "Codex replacement/guarantees, then run "
                    "python3 scripts/sync_codex_marketplace.py "
                    f"--refresh-source-digests --plugin {plugin_name}"
                )
            stale_digests.setdefault(plugin_name, []).append(source)
        replacement = entry.get("codexReplacement")
        if disposition == "adapted":
            if not isinstance(replacement, str) or not replacement.strip():
                raise SyncError(f"{label} requires codexReplacement")
        if replacement is not None:
            if not isinstance(replacement, str) or not replacement.strip():
                raise SyncError(f"{label}.codexReplacement must be a non-empty string")
            replacement_path = plugin_root / replacement
            if (
                Path(replacement).is_absolute()
                or ".." in Path(replacement).parts
                or not replacement_path.is_file()
            ):
                raise SyncError(
                    f"{label}.codexReplacement must reference an existing plugin file"
                )
            if not is_plugin_behavior_path(Path(replacement)):
                raise SyncError(
                    f"{label}.codexReplacement must be covered by sourceTreeDigest"
                )
        if disposition == "not-applicable" and replacement is not None:
            raise SyncError(f"{label} must omit codexReplacement when not-applicable")

    discovered = discover_component_differences(
        plugin_root, manifest, marketplace_entry, marketplace_index
    )
    missing = sorted(discovered - registered)
    stale = sorted(registered - discovered)
    if missing or stale:
        details: list[str] = []
        if missing:
            details.append("unregistered=" + ", ".join(missing))
        if stale:
            details.append("stale=" + ", ".join(stale))
        raise SyncError(f"{plugin_name}: component registry drift: {'; '.join(details)}")


def load_repository_state() -> RepositoryState:
    for managed_source in (
        CLAUDE_MARKETPLACE_PATH,
        CLAUDE_RULES_PATH,
        PORT_CONFIG_PATH,
        README_PATH,
    ):
        assert_safe_repo_path(ROOT, managed_source)
    marketplace = load_json(CLAUDE_MARKETPLACE_PATH)
    config = load_json(PORT_CONFIG_PATH)
    schema_version = config.get("schemaVersion")
    if type(schema_version) is not int or schema_version != 3:
        raise SyncError("codex port config schemaVersion must be 3")
    _validate_port_config_known_fields(config)
    _validate_marketplace_header(marketplace, config)
    marketplace_config = config.get("marketplace")
    publisher = config.get("publisher")
    config_plugins = config.get("plugins")
    if not isinstance(marketplace_config, dict) or not isinstance(publisher, dict):
        raise SyncError("codex port config requires marketplace and publisher objects")
    if not isinstance(config_plugins, dict):
        raise SyncError("codex port config requires a plugins object")
    require_non_empty_string(marketplace_config, "displayName", "config.marketplace")
    require_non_empty_string(marketplace_config, "category", "config.marketplace")
    for field in ("name", "url", "repository", "license"):
        require_non_empty_string(publisher, field, "config.publisher")
    for field in ("url", "repository"):
        if not publisher[field].startswith("https://"):
            raise SyncError(f"config.publisher.{field} must use https://")

    marketplace_name = require_non_empty_string(marketplace, "name", "marketplace")
    if not KEBAB_RE.fullmatch(marketplace_name):
        raise SyncError("marketplace.name must be kebab-case")
    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or not entries:
        raise SyncError("Claude marketplace.plugins must be a non-empty array")

    names: list[str] = []
    states: list[PluginState] = []
    stale_digests: dict[str, list[str]] = {}
    for index, entry in enumerate(entries):
        label = f"marketplace.plugins[{index}]"
        if not isinstance(entry, dict):
            raise SyncError(f"{label} must be an object")
        name = require_non_empty_string(entry, "name", label)
        if not KEBAB_RE.fullmatch(name):
            raise SyncError(f"{label}.name must be kebab-case")
        if name in names:
            raise SyncError(f"duplicate marketplace plugin: {name}")
        names.append(name)

        source = require_non_empty_string(entry, "source", label)
        expected_source = f"./plugins/{name}"
        if source != expected_source:
            raise SyncError(f"{label}.source must be {expected_source!r}")
        version = require_non_empty_string(entry, "version", label)
        if SEMVER_RE.fullmatch(version) is None:
            raise SyncError(f"{label}.version must use strict semver")
        require_non_empty_string(entry, "description", label)
        require_string_list(entry, "keywords", label)

        plugin_root = ROOT / "plugins" / name
        _reject_plugin_symlinks(plugin_root)
        if not plugin_root.is_dir():
            raise SyncError(f"missing plugin directory: plugins/{name}")
        manifest = load_json(plugin_root / ".claude-plugin" / "plugin.json")
        _validate_mapped_manifest_metadata(name, manifest, config)
        for field in ("name", "version", "description"):
            if manifest.get(field) != entry.get(field):
                raise SyncError(
                    f"{name}: Claude manifest {field} differs from marketplace"
                )

        plugin_readme = plugin_root / "README.md"
        if not plugin_readme.is_file():
            raise SyncError(f"{name}: missing plugin README.md")
        readme_match = PLUGIN_README_VERSION_RE.search(
            plugin_readme.read_text(encoding="utf-8")
        )
        if readme_match is None:
            raise SyncError(
                f"{name}: plugin README requires a '## バージョン' + 'vX.Y.Z' heading"
            )
        if readme_match.group(1) != version:
            raise SyncError(
                f"{name}: plugin README version {readme_match.group(1)} differs from {version}"
            )

        port = config_plugins.get(name)
        if not isinstance(port, dict):
            raise SyncError(f"missing Codex port config for {name}")
        require_non_empty_string(port, "displayName", f"config.plugins.{name}")
        require_non_empty_string(port, "shortDescription", f"config.plugins.{name}")
        prompts = require_string_list(port, "defaultPrompt", f"config.plugins.{name}")
        if len(prompts) > 3 or any(len(prompt) > 128 for prompt in prompts):
            raise SyncError(f"{name}: defaultPrompt allows at most 3 entries of 128 chars")
        compatibility = port.get("compatibility")
        if not isinstance(compatibility, dict):
            raise SyncError(f"{name}: compatibility must be an object")
        _validate_component_registry(
            name,
            plugin_root,
            compatibility,
            manifest,
            entry,
            index,
            stale_digests,
        )

        skill_count = _validate_skill_manifests(plugin_root)
        command_hook_count, hook_count = _count_hooks(plugin_root)
        states.append(
            PluginState(
                name=name,
                root=plugin_root,
                marketplace=entry,
                manifest=manifest,
                port=port,
                skill_count=skill_count,
                command_hook_count=command_hook_count,
                hook_count=hook_count,
            )
        )

    if list(config_plugins) != names:
        raise SyncError(
            "codex port config plugin set/order must exactly match Claude marketplace"
        )

    if stale_digests:
        stale_names = sorted(stale_digests)
        details = "; ".join(
            f"{name} ({', '.join(stale_digests[name])})" for name in stale_names
        )
        refresh_args = " ".join(f"--plugin {name}" for name in stale_names)
        raise SyncError(
            "stale compatibility digests detected for every affected plugin: "
            f"{details}. Review adapters, guarantee differences, and tests for all "
            "listed plugins, then preview the exact complete set with: python3 "
            "scripts/sync_codex_marketplace.py --refresh-source-digests "
            f"{refresh_args}"
        )

    manifest_names = {
        path.parents[1].name
        for path in (ROOT / "plugins").glob("*/.claude-plugin/plugin.json")
    }
    if manifest_names != set(names):
        raise SyncError(
            "Claude plugin manifests and marketplace entries differ: "
            f"manifests={sorted(manifest_names)}, marketplace={sorted(names)}"
        )

    readme_versions = dict(README_VERSION_RE.findall(README_PATH.read_text(encoding="utf-8")))
    expected_versions = {state.name: state.manifest["version"] for state in states}
    if readme_versions != expected_versions:
        raise SyncError(
            "README plugin table differs from Claude manifests: "
            f"README={readme_versions}, manifests={expected_versions}"
        )

    return RepositoryState(
        marketplace=marketplace, config=config, plugins=tuple(states)
    )


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def render_codex_marketplace(state: RepositoryState) -> bytes:
    marketplace_config = state.config["marketplace"]
    payload = {
        "name": state.marketplace["name"],
        "interface": {"displayName": marketplace_config["displayName"]},
        "plugins": [
            {
                "name": plugin.name,
                "source": {
                    "source": "local",
                    "path": plugin.marketplace["source"],
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": marketplace_config["category"],
            }
            for plugin in state.plugins
        ],
    }
    return json_bytes(payload)


def render_codex_manifest(state: RepositoryState, plugin: PluginState) -> bytes:
    publisher = state.config["publisher"]
    compatibility = plugin.port["compatibility"]
    capabilities: list[str] = []
    if plugin.skill_count:
        capabilities.append("Skills")
    if plugin.command_hook_count:
        capabilities.append("Hooks")

    manifest: dict[str, Any] = {
        "name": plugin.name,
        "version": plugin.manifest["version"],
        "description": plugin.manifest["description"],
        "author": {"name": publisher["name"], "url": publisher["url"]},
        "homepage": f"{publisher['repository']}#{plugin.name}",
        "repository": publisher["repository"],
        "license": publisher["license"],
        "keywords": plugin.marketplace["keywords"],
    }
    if plugin.skill_count:
        manifest["skills"] = "./skills/"
    manifest["interface"] = {
        "displayName": plugin.port["displayName"],
        "shortDescription": plugin.port["shortDescription"],
        "longDescription": (
            f"{plugin.manifest['description']} Codex 互換性: {compatibility['summary']}"
        ),
        "developerName": publisher["name"],
        "category": state.config["marketplace"]["category"],
        "capabilities": capabilities,
        "websiteURL": publisher["repository"],
        "defaultPrompt": plugin.port["defaultPrompt"],
    }
    return json_bytes(manifest)


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def render_compatibility_doc(state: RepositoryState) -> bytes:
    labels = {
        "full": "完全共有",
        "partial": "一部変換",
        "metadata-only": "メタデータのみ",
    }
    lines = [
        "# Codex compatibility",
        "",
        "<!-- Generated by scripts/sync_codex_marketplace.py. Do not edit directly. -->",
        "",
        "Claude Code marketplace を正本とし、Skill・script・command hook は可能な限り同じ物理ファイルを共有します。直接機構の差分、Codex 用の意図等価 adapter、残る保証差、検証面は `codex/marketplace-overrides.json` で個別管理されます。",
        "",
        "| Plugin | Version | 移植状態 | Skills | command hooks | 意図した差分 |",
        "|---|---:|---|---:|---:|---|",
    ]
    for plugin in state.plugins:
        compatibility = plugin.port["compatibility"]
        limitations = compatibility["limitations"]
        difference = "なし" if not limitations else "<br>".join(limitations)
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{plugin.name}`",
                    plugin.manifest["version"],
                    labels[compatibility["level"]],
                    str(plugin.skill_count),
                    f"{plugin.command_hook_count}/{plugin.hook_count}",
                    _escape_markdown(difference),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 保証差と検証テスト (Guarantee differences and verification tests)",
            "",
            "保証差は、同じ利用目的を満たしていても Claude Code と同じ強制境界・発火時点・UI・モデル identity を保証できない点です。検証テストは adapter の入出力・lifecycle・install surface を検査しますが、LLM の意味判断品質そのものを証明するものではありません。",
            "",
            "共通の前提:",
            "",
            "- Codex の非 managed command hook は、利用前に `/hooks` で内容を確認して trust する必要があります。",
            "- 現行 Codex の `PreToolUse` は Bash / `apply_patch` / MCP の対応経路に対する guardrail であり、同等操作を行うすべての shell 経路を捕捉する完全な security boundary ではありません。",
            "- `SubagentStop` lifecycle は agent の終了と runtime identity を検証できますが、レビュー内容の意味的正しさを暗号学的に証明しません。",
            "- Skill の自動選択意図は両 runtime が読む共通 `description` に集約し、runtime 固有 frontmatter は同期検証で拒否します。",
            "- Claude marketplace 全体の `metadata.description` は Claude catalog 用です。Codex marketplace は `interface.displayName` と各 plugin description を生成しますが、marketplace 全体説明の同一 UI 表示は保証しません。",
            "",
            "| Plugin | 残る保証差 | 検証テスト |",
            "|---|---|---|",
        ]
    )
    for plugin in state.plugins:
        compatibility = plugin.port["compatibility"]
        differences = compatibility["guaranteeDifferences"]
        rendered_differences = (
            "なし" if not differences else "<br>".join(differences)
        )
        rendered_tests = "<br>".join(
            f"`{test['path']}` — {test['covers']}"
            for test in compatibility["verificationTests"]
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{plugin.name}`",
                    _escape_markdown(rendered_differences),
                    _escape_markdown(rendered_tests),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Component difference registry",
            "",
            "この表にない Claude 固有の既定 component、plugin manifest / marketplace entry の component・enablement・依存宣言、非可搬 hook が追加されると CI は失敗します。未知の manifest / marketplace field と Codex 差分台帳の未知 field も、Codex での扱いを分類するまで fail-closed です。各行は Claude 正本 component（hook は matcher も含む）の SHA-256 fingerprint を固定します。さらに全 plugin は generator 所有の `.codex-plugin` と一時 cache を除く plugin tree 全体（README/docs を含む）と、宣言した verification test の内容を `sourceTreeDigest` に固定します。full 判定済み plugin も含め、既存 component・依存 script・保証説明・検証証跡の内容だけが変わった場合は adapter・保証差の再監査なしに CI を通しません。Disposition は直接ファイルの可搬性ではなく、意図の扱いまで含めて分類します。`adapted` は marketplace 内の代替実装、`surface-unavailable` は stock Codex surface では直接実行不能だが別の情報・workflow で近似可能、`host-required` は app-server 等の専用 host が必要、`not-applicable` は Claude 固有概念で Codex に適用対象がないことを示します。",
            "",
            "| Plugin | Claude component | Source SHA-256 | Disposition | Codex replacement | Reason |",
            "|---|---|---|---|---|---|",
        ]
    )
    any_components = False
    for plugin in state.plugins:
        for component in plugin.port["compatibility"]["components"]:
            any_components = True
            replacement = component.get("codexReplacement", "—")
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{plugin.name}`",
                        f"`{_escape_markdown(component['source'])}`",
                        f"`{component['sourceDigest'][:12]}…`",
                        f"`{component['disposition']}`",
                        f"`{_escape_markdown(replacement)}`" if replacement != "—" else "—",
                        _escape_markdown(component["reason"]),
                    ]
                )
                + " |"
            )
    if not any_components:
        lines.append("| — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## Synchronization contract",
            "",
            "- Canonical metadata: `.claude-plugin/marketplace.json` and `plugins/*/.claude-plugin/plugin.json`",
            "- Codex-specific registry: `codex/marketplace-overrides.json`",
            "- Generated artifacts: `.agents/plugins/marketplace.json`, `plugins/*/.codex-plugin/plugin.json`, `AGENTS.md`, this document",
            "- Runtime validation: pinned Claude Code strict-validates the canonical marketplace; pinned Codex installs every generated plugin on each PR/push, and scheduled latest-version jobs act as compatibility canaries",
            "- Every plugin entry declares `guaranteeDifferences` and at least one `verificationTests[].path`; each path must be executed by unittest discovery or the marketplace smoke job, so CI rejects missing or unexecuted evidence declarations",
            "- Every non-portable Claude component pins `sourceDigest`; every plugin pins `sourceTreeDigest` for its behavior tree and declared test evidence. After reviewing every stale plugin printed together by `--check`, run `python3 scripts/sync_codex_marketplace.py --refresh-source-digests --plugin <name> [--plugin <name> ...]` with the exact complete set for a no-write preview. Apply only by repeating it with the state-bound `--approve <action-token>` printed by that preview; source, test, registry, or marketplace byte changes (including formatting-only registry edits) invalidate the token.",
            "- Regenerate: `python3 scripts/sync_codex_marketplace.py --write`",
            "- Verify without writes: `python3 scripts/sync_codex_marketplace.py --check`",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def expected_files(state: RepositoryState) -> dict[Path, bytes]:
    result = {
        CODEX_MARKETPLACE_PATH: render_codex_marketplace(state),
        COMPATIBILITY_DOC_PATH: render_compatibility_doc(state),
        AGENTS_PATH: CLAUDE_RULES_PATH.read_bytes(),
    }
    for plugin in state.plugins:
        result[plugin.root / ".codex-plugin" / "plugin.json"] = render_codex_manifest(
            state, plugin
        )
    return result


def _atomic_write(path: Path, contents: bytes) -> None:
    assert_safe_repo_path(ROOT, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o644)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def write_files(state: RepositoryState) -> None:
    generated = expected_files(state)
    for path, contents in generated.items():
        _atomic_write(path, contents)
        print(f"wrote {path.relative_to(ROOT)}")

    expected_manifest_paths = {
        plugin.root / ".codex-plugin" / "plugin.json" for plugin in state.plugins
    }
    for stale in sorted((ROOT / "plugins").glob("*/.codex-plugin/plugin.json")):
        if stale not in expected_manifest_paths:
            assert_safe_repo_path(ROOT, stale)
            stale.unlink()
            print(f"removed stale {stale.relative_to(ROOT)}")


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _set_json_path(value: dict[str, Any], path: tuple[str | int, ...], new: Any) -> None:
    current: Any = value
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = new


def _build_digest_refresh_plan(plugin_names: Iterable[str]) -> DigestRefreshPlan:
    """Build a state-bound, no-write digest acknowledgement plan."""

    try:
        config_bytes = PORT_CONFIG_PATH.read_bytes()
        marketplace_bytes = CLAUDE_MARKETPLACE_PATH.read_bytes()
    except OSError as exc:
        raise SyncError(f"unable to snapshot digest refresh inputs: {exc}") from exc
    marketplace = _load_json_bytes(CLAUDE_MARKETPLACE_PATH, marketplace_bytes)
    config = _load_json_bytes(PORT_CONFIG_PATH, config_bytes)
    schema_version = config.get("schemaVersion")
    if type(schema_version) is not int or schema_version not in {2, 3}:
        raise SyncError("cannot refresh digests for an unsupported port config schema")
    _validate_port_config_known_fields(config)
    _validate_marketplace_header(marketplace, config)
    config_plugins = config.get("plugins")
    entries = marketplace.get("plugins")
    if not isinstance(config_plugins, dict) or not isinstance(entries, list):
        raise SyncError("marketplace and Codex port config must declare plugin objects")

    requested_list = list(plugin_names)
    if not requested_list:
        raise SyncError("digest refresh requires at least one explicit --plugin NAME")
    if len(requested_list) != len(set(requested_list)):
        raise SyncError("digest refresh --plugin names must not contain duplicates")
    requested = set(requested_list)

    marketplace_names: list[str] = []
    changes: list[DigestChange] = []
    stale_plugins: set[str] = set()
    for marketplace_index, marketplace_entry in enumerate(entries):
        if not isinstance(marketplace_entry, dict):
            raise SyncError("Claude marketplace plugin entry must be an object")
        name = require_non_empty_string(marketplace_entry, "name", "marketplace plugin")
        if name in marketplace_names:
            raise SyncError(f"duplicate marketplace plugin: {name}")
        marketplace_names.append(name)
        plugin_root = ROOT / "plugins" / name
        if not plugin_root.is_dir():
            raise SyncError(f"missing plugin directory: plugins/{name}")
        _reject_plugin_symlinks(plugin_root)
        manifest = load_json(plugin_root / ".claude-plugin" / "plugin.json")
        _validate_mapped_manifest_metadata(name, manifest, config)
        port = config_plugins.get(name)
        compatibility = port.get("compatibility") if isinstance(port, dict) else None
        components = (
            compatibility.get("components")
            if isinstance(compatibility, dict)
            else None
        )
        verification_tests = (
            compatibility.get("verificationTests")
            if isinstance(compatibility, dict)
            else None
        )
        if not isinstance(components, list):
            raise SyncError(f"{name}: compatibility.components must be an array")
        if not isinstance(verification_tests, list) or not verification_tests:
            raise SyncError(f"{name}: compatibility.verificationTests must be non-empty")
        verification_paths: list[str] = []
        for index, verification in enumerate(verification_tests):
            if not isinstance(verification, dict):
                raise SyncError(f"{name}: verification test {index} must be an object")
            path = require_non_empty_string(
                verification, "path", f"{name} verification test {index}"
            )
            require_non_empty_string(
                verification, "covers", f"{name} verification test {index}"
            )
            if not _verification_path_is_ci_executed(Path(path)):
                raise SyncError(
                    f"{name}: verification test {index} is not executed by the "
                    "declared CI runners"
                )
            verification_paths.append(path)

        registered_sources: list[str] = []
        for component_index, component in enumerate(components):
            if not isinstance(component, dict):
                raise SyncError(f"{name}: component {component_index} must be an object")
            registered_sources.append(
                require_non_empty_string(component, "source", f"{name} component")
            )
        if len(registered_sources) != len(set(registered_sources)):
            raise SyncError(f"{name}: component registry contains duplicate sources")
        discovered_sources = discover_component_differences(
            plugin_root, manifest, marketplace_entry, marketplace_index
        )
        if set(registered_sources) != discovered_sources:
            missing = sorted(discovered_sources - set(registered_sources))
            stale = sorted(set(registered_sources) - discovered_sources)
            raise SyncError(
                f"{name}: resolve component registry drift before refreshing source "
                f"digests: unregistered={missing}, stale={stale}"
            )

        tree_digest = plugin_source_tree_digest(plugin_root, verification_paths)
        previous_tree = compatibility.get("sourceTreeDigest")
        if previous_tree != tree_digest:
            stale_plugins.add(name)
            changes.append(
                DigestChange(
                    plugin=name,
                    label="behavior tree",
                    target_path=(
                        "plugins",
                        name,
                        "compatibility",
                        "sourceTreeDigest",
                    ),
                    previous=previous_tree,
                    current=tree_digest,
                )
            )
        for component_index, component in enumerate(components):
            source = registered_sources[component_index]
            digest = component_source_digest(plugin_root, source)
            previous_digest = component.get("sourceDigest")
            if previous_digest != digest:
                stale_plugins.add(name)
                changes.append(
                    DigestChange(
                        plugin=name,
                        label=source,
                        target_path=(
                            "plugins",
                            name,
                            "compatibility",
                            "components",
                            component_index,
                            "sourceDigest",
                        ),
                        previous=previous_digest,
                        current=digest,
                    )
                )

    if list(config_plugins) != marketplace_names:
        raise SyncError(
            "codex port config plugin set/order must exactly match Claude marketplace"
        )
    unknown = sorted(requested - set(marketplace_names))
    if unknown:
        raise SyncError("unknown --plugin name(s): " + ", ".join(unknown))
    if schema_version == 2:
        stale_plugins.update(marketplace_names)
    if requested != stale_plugins:
        raise SyncError(
            "digest refresh must explicitly name exactly all stale plugins; "
            f"requested={sorted(requested)}, stale={sorted(stale_plugins)}"
        )

    candidate = copy.deepcopy(config)
    if schema_version != 3:
        changes.insert(
            0,
            DigestChange(
                plugin="<registry>",
                label="schemaVersion",
                target_path=("schemaVersion",),
                previous=schema_version,
                current=3,
            ),
        )
    for change in changes:
        _set_json_path(candidate, change.target_path, change.current)

    token_payload = {
        "protocol": "codex-marketplace-digest-refresh-v1",
        "requestedPlugins": sorted(requested),
        "stalePlugins": sorted(stale_plugins),
        "sourceConfigSha256": _canonical_json_sha256(config),
        "sourceConfigBytesSha256": _bytes_sha256(config_bytes),
        "canonicalMarketplaceSha256": _canonical_json_sha256(marketplace),
        "canonicalMarketplaceBytesSha256": _bytes_sha256(marketplace_bytes),
        "candidateConfigSha256": _canonical_json_sha256(candidate),
        "changes": [
            {
                "plugin": change.plugin,
                "label": change.label,
                "targetPath": list(change.target_path),
                "previous": change.previous,
                "current": change.current,
            }
            for change in changes
        ],
    }
    token = _canonical_json_sha256(token_payload)
    return DigestRefreshPlan(
        candidate=candidate,
        changes=tuple(changes),
        token=token,
        config_bytes=config_bytes,
        marketplace_bytes=marketplace_bytes,
    )


def _assert_file_unchanged(path: Path, expected_current: bytes) -> None:
    try:
        current = path.read_bytes()
    except OSError as exc:
        raise SyncError(f"unable to re-read {path.relative_to(ROOT)}: {exc}") from exc
    if current != expected_current:
        raise SyncError(
            f"{path.relative_to(ROOT)} changed after digest review; refusing lost update"
        )


def _atomic_write_if_unchanged(
    path: Path, contents: bytes, expected_current: bytes
) -> None:
    _assert_file_unchanged(path, expected_current)
    _atomic_write(path, contents)


def _print_digest_refresh_plan(
    plan: DigestRefreshPlan, *, no_write_preview: bool
) -> None:
    if no_write_preview:
        print("Digest refresh preview (no files changed):")
    else:
        print("Digest refresh approved candidate (write not started yet):")
    for change in plan.changes:
        old = change.previous if isinstance(change.previous, (str, int)) else "<missing>"
        print(f"  {change.plugin}: {change.label}: {old} -> {change.current}")
    print(f"Action token: {plan.token}")


def refresh_source_digests(
    plugin_names: Iterable[str], approval_token: str | None = None
) -> str:
    """Preview or apply a state-bound digest acknowledgement."""

    names = list(plugin_names)
    plan = _build_digest_refresh_plan(names)
    _print_digest_refresh_plan(plan, no_write_preview=approval_token is None)
    if approval_token is None:
        print(
            "Review every change above, then repeat the same command with "
            f"--approve {plan.token}"
        )
        return plan.token
    if SOURCE_DIGEST_RE.fullmatch(approval_token) is None:
        raise SyncError("--approve must be the exact lowercase SHA-256 action token")
    if approval_token != plan.token:
        raise SyncError(
            "digest refresh action token does not match current repository state; "
            "run the preview again"
        )

    # Recompute immediately before the compare-and-swap write. This binds approval
    # to plugin/test bytes and executable bits as well as both JSON registries.
    current_plan = _build_digest_refresh_plan(names)
    if current_plan.token != approval_token:
        raise SyncError(
            "repository state changed after digest review; run the preview again"
        )
    candidate_bytes = json_bytes(current_plan.candidate)
    _assert_file_unchanged(
        CLAUDE_MARKETPLACE_PATH, current_plan.marketplace_bytes
    )
    _atomic_write_if_unchanged(
        PORT_CONFIG_PATH, candidate_bytes, current_plan.config_bytes
    )
    try:
        _assert_file_unchanged(
            CLAUDE_MARKETPLACE_PATH, current_plan.marketplace_bytes
        )
        load_repository_state()
    except Exception as exc:
        try:
            _atomic_write_if_unchanged(
                PORT_CONFIG_PATH, current_plan.config_bytes, candidate_bytes
            )
        except SyncError as rollback_exc:
            raise SyncError(
                "post-write validation failed and the registry changed before safe "
                f"rollback: {exc}; rollback error: {rollback_exc}"
            ) from exc
        if isinstance(exc, SyncError):
            raise SyncError(
                f"post-write validation failed; restored original registry: {exc}"
            ) from exc
        raise

    print(
        f"applied {len(current_plan.changes)} reviewed digest/schema value(s); "
        "regenerate with --write"
    )
    return current_plan.token


def _text_diff(path: Path, actual: bytes, expected: bytes) -> Iterable[str]:
    try:
        actual_text = actual.decode("utf-8").splitlines(keepends=True)
        expected_text = expected.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return [f"binary content differs: {path.relative_to(ROOT)}\n"]
    return difflib.unified_diff(
        actual_text,
        expected_text,
        fromfile=f"a/{path.relative_to(ROOT)}",
        tofile=f"b/{path.relative_to(ROOT)}",
    )


def check_files(state: RepositoryState) -> bool:
    generated = expected_files(state)
    ok = True
    for path, expected in generated.items():
        assert_safe_repo_path(ROOT, path)
        try:
            actual = path.read_bytes()
        except FileNotFoundError:
            print(f"missing generated file: {path.relative_to(ROOT)}", file=sys.stderr)
            ok = False
            continue
        if actual != expected:
            ok = False
            sys.stderr.writelines(_text_diff(path, actual, expected))

    expected_manifest_paths = {
        plugin.root / ".codex-plugin" / "plugin.json" for plugin in state.plugins
    }
    actual_manifest_paths = set(
        (ROOT / "plugins").glob("*/.codex-plugin/plugin.json")
    )
    for stale in sorted(actual_manifest_paths - expected_manifest_paths):
        assert_safe_repo_path(ROOT, stale)
        print(f"stale generated manifest: {stale.relative_to(ROOT)}", file=sys.stderr)
        ok = False

    for plugin in state.plugins:
        codex_dir = plugin.root / ".codex-plugin"
        if codex_dir.is_dir():
            extras = [
                path
                for path in codex_dir.rglob("*")
                if path.is_file() and path.name != "plugin.json"
            ]
            for extra in extras:
                print(
                    f"unexpected file in generated directory: {extra.relative_to(ROOT)}",
                    file=sys.stderr,
                )
                ok = False

    if not ok:
        print(
            "Generated Codex artifacts are stale. Run: "
            "python3 scripts/sync_codex_marketplace.py --write",
            file=sys.stderr,
        )
    return ok


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate committed artifacts")
    mode.add_argument("--check", action="store_true", help="verify artifacts without writes")
    mode.add_argument(
        "--refresh-source-digests",
        action="store_true",
        help="acknowledge reviewed canonical component changes in the port registry",
    )
    parser.add_argument(
        "--plugin",
        action="append",
        default=[],
        metavar="NAME",
        help="reviewed plugin to acknowledge (repeat; required for digest refresh)",
    )
    parser.add_argument(
        "--approve",
        metavar="ACTION_TOKEN",
        help="apply the exact state-bound token printed by digest refresh preview",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.refresh_source_digests:
            refresh_source_digests(args.plugin, args.approve)
            return 0
        if args.plugin or args.approve:
            raise SyncError(
                "--plugin and --approve are only valid with --refresh-source-digests"
            )
        state = load_repository_state()
        if args.write:
            write_files(state)
            return 0
        if check_files(state):
            print("OK: Claude and Codex marketplace artifacts are synchronized.")
            return 0
        return 1
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

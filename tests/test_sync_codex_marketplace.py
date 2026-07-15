from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves forward annotations through sys.modules.
    import sys

    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sync = load_module("sync_codex_marketplace", ROOT / "scripts/sync_codex_marketplace.py")


class MarketplaceSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = sync.load_repository_state()

    @staticmethod
    def _write_digest_fixture(root: Path, names: list[str]) -> tuple[Path, Path]:
        marketplace_path = root / ".claude-plugin" / "marketplace.json"
        config_path = root / "codex" / "marketplace-overrides.json"
        evidence = root / "tests" / "test_evidence.py"
        marketplace_path.parent.mkdir(parents=True)
        config_path.parent.mkdir(parents=True)
        evidence.parent.mkdir(parents=True)
        evidence.write_text("# adapter evidence\n", encoding="utf-8")
        marketplace_path.write_text(
            json.dumps(
                {
                    "name": "test-marketplace",
                    "owner": {"name": "test-owner"},
                    "metadata": {"description": "test marketplace"},
                    "plugins": [{"name": name} for name in names],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        config = {
            "schemaVersion": 3,
            "publisher": {
                "name": "test-owner",
                "url": "https://example.test/owner",
            },
            "plugins": {
                name: {
                    "compatibility": {
                        "components": [],
                        "sourceTreeDigest": "0" * 64,
                        "verificationTests": [
                            {
                                "path": "tests/test_evidence.py",
                                "covers": "adapter contract",
                            }
                        ],
                    }
                }
                for name in names
            },
        }
        config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
        for name in names:
            plugin_root = root / "plugins" / name
            manifest = plugin_root / ".claude-plugin" / "plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "name": name,
                        "version": "1.0.0",
                        "description": "test plugin",
                        "author": {
                            "name": "test-owner",
                            "url": "https://example.test/owner",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            script = plugin_root / "scripts" / "adapter.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        return marketplace_path, config_path

    def test_plugin_order_matches_claude_marketplace(self) -> None:
        expected = [entry["name"] for entry in self.state.marketplace["plugins"]]
        self.assertEqual([plugin.name for plugin in self.state.plugins], expected)
        self.assertEqual(len(expected), 12)

    def test_generated_files_are_current(self) -> None:
        for path, expected in sync.expected_files(self.state).items():
            self.assertTrue(path.is_file(), path.relative_to(ROOT))
            self.assertEqual(path.read_bytes(), expected, path.relative_to(ROOT))

    def test_codex_marketplace_has_required_policy(self) -> None:
        payload = json.loads(sync.render_codex_marketplace(self.state))
        for source, entry in zip(self.state.marketplace["plugins"], payload["plugins"]):
            self.assertEqual(entry["name"], source["name"])
            self.assertEqual(
                entry["source"], {"source": "local", "path": source["source"]}
            )
            self.assertEqual(
                entry["policy"],
                {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            )
            self.assertEqual(entry["category"], "Productivity")

    def test_codex_manifests_share_canonical_metadata(self) -> None:
        for plugin in self.state.plugins:
            manifest = json.loads(sync.render_codex_manifest(self.state, plugin))
            self.assertEqual(manifest["name"], plugin.manifest["name"])
            self.assertEqual(manifest["version"], plugin.manifest["version"])
            self.assertEqual(manifest["description"], plugin.manifest["description"])
            self.assertEqual(manifest["keywords"], plugin.marketplace["keywords"])
            # Default hooks/hooks.json discovery avoids the current validator/runtime
            # disagreement about a manifest-level hooks field.
            self.assertNotIn("hooks", manifest)

    def test_agents_is_byte_identical_to_claude_rules(self) -> None:
        self.assertEqual(
            (ROOT / "AGENTS.md").read_bytes(),
            (ROOT / ".claude" / "CLAUDE.md").read_bytes(),
        )

    def test_renderer_is_deterministic(self) -> None:
        first = sync.expected_files(self.state)
        second = sync.expected_files(sync.load_repository_state())
        self.assertEqual(first, second)

    def test_codex_port_registry_unknown_fields_fail_closed_at_every_level(self) -> None:
        def fresh() -> dict[str, object]:
            return json.loads(json.dumps(self.state.config))

        mutations = []

        config = fresh()
        config["futureTopLevel"] = True
        mutations.append(("top-level", config))

        config = fresh()
        config["marketplace"]["futureMarketplaceField"] = True
        mutations.append(("marketplace", config))

        config = fresh()
        config["publisher"]["futurePublisherField"] = True
        mutations.append(("publisher", config))

        config = fresh()
        first_plugin = next(iter(config["plugins"].values()))
        first_plugin["futurePluginField"] = True
        mutations.append(("plugin", config))

        config = fresh()
        first_plugin = next(iter(config["plugins"].values()))
        first_plugin["compatibility"]["futureCompatibilityField"] = True
        mutations.append(("compatibility", config))

        config = fresh()
        first_plugin = next(iter(config["plugins"].values()))
        first_plugin["compatibility"]["verificationTests"][0][
            "futureVerificationField"
        ] = True
        mutations.append(("verification", config))

        config = fresh()
        plugin_with_component = next(
            plugin
            for plugin in config["plugins"].values()
            if plugin["compatibility"]["components"]
        )
        plugin_with_component["compatibility"]["components"][0][
            "futureComponentField"
        ] = True
        mutations.append(("component", config))

        for label, mutated in mutations:
            with self.subTest(level=label), self.assertRaisesRegex(
                sync.SyncError, "unknown Codex port config field"
            ):
                sync._validate_port_config_known_fields(mutated)

    def test_declared_verification_paths_are_executed_by_ci(self) -> None:
        for plugin in self.state.plugins:
            for verification in plugin.port["compatibility"]["verificationTests"]:
                self.assertTrue(
                    sync._verification_path_is_ci_executed(Path(verification["path"])),
                    f"{plugin.name}: {verification['path']}",
                )
        self.assertFalse(
            sync._verification_path_is_ci_executed(Path("docs/manual-check.md"))
        )

    def test_all_structural_differences_are_registered(self) -> None:
        for index, plugin in enumerate(self.state.plugins):
            registered = {
                item["source"]
                for item in plugin.port["compatibility"]["components"]
            }
            self.assertEqual(
                sync.discover_component_differences(
                    plugin.root, plugin.manifest, plugin.marketplace, index
                ),
                registered,
                plugin.name,
            )

    def test_component_registry_pins_current_claude_source_digests(self) -> None:
        self.assertEqual(3, self.state.config["schemaVersion"])
        for plugin in self.state.plugins:
            compatibility = plugin.port["compatibility"]
            self.assertEqual(
                sync.plugin_source_tree_digest(
                    plugin.root,
                    [item["path"] for item in compatibility["verificationTests"]],
                ),
                compatibility["sourceTreeDigest"],
                plugin.name,
            )
            for component in plugin.port["compatibility"]["components"]:
                self.assertEqual(
                    sync.component_source_digest(plugin.root, component["source"]),
                    component["sourceDigest"],
                    f"{plugin.name}: {component['source']}",
                )

    def test_hook_source_digest_includes_matcher_context(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            plugin_root = Path(root_name)
            hooks_path = plugin_root / "hooks" / "hooks.json"
            hooks_path.parent.mkdir()
            payload = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "prompt", "prompt": "review this"}],
                        }
                    ]
                }
            }
            hooks_path.write_text(json.dumps(payload), encoding="utf-8")
            source = "hooks/hooks.json#/hooks/PreToolUse/0/hooks/0"
            first = sync.component_source_digest(plugin_root, source)
            payload["hooks"]["PreToolUse"][0]["matcher"] = "Write"
            hooks_path.write_text(json.dumps(payload), encoding="utf-8")
            second = sync.component_source_digest(plugin_root, source)
            self.assertNotEqual(first, second)

    def test_hook_portability_uses_supported_intersection(self) -> None:
        cases = (
            ("PreToolUse", "Bash", {"type": "command", "command": "check"}, True),
            (
                "PreToolUse",
                "Write|Edit|MultiEdit",
                {"type": "command", "command": "check"},
                True,
            ),
            (
                "PreToolUse",
                "mcp__filesystem__.*",
                {"type": "command", "command": "check"},
                True,
            ),
            ("PreToolUse", "Read", {"type": "command", "command": "check"}, False),
            (
                "PreToolUse",
                "Bash",
                {"type": "command", "command": "check", "async": True},
                False,
            ),
            ("PreToolUse", "Bash", {"type": "prompt", "prompt": "check"}, False),
            (
                "Notification",
                "*",
                {"type": "command", "command": "check"},
                False,
            ),
        )
        for event, matcher, handler, portable in cases:
            with self.subTest(event=event, matcher=matcher, handler=handler):
                with tempfile.TemporaryDirectory() as root_name:
                    plugin_root = Path(root_name) / "example"
                    hooks_path = plugin_root / "hooks" / "hooks.json"
                    hooks_path.parent.mkdir(parents=True)
                    hooks_path.write_text(
                        json.dumps(
                            {
                                "hooks": {
                                    event: [
                                        {"matcher": matcher, "hooks": [handler]}
                                    ]
                                }
                            }
                        ),
                        encoding="utf-8",
                    )
                    source = f"hooks/hooks.json#/hooks/{event}/0/hooks/0"
                    discovered = sync.discover_component_differences(plugin_root)
                    self.assertEqual(source not in discovered, portable)

    def test_manifest_component_declarations_are_fail_closed_tokens(self) -> None:
        declarations = {
            "skills": "./extra-skills/",
            "commands": "./commands/",
            "agents": "./agents/",
            "hooks": {"hooks": {}},
            "mcpServers": {"server": {"command": "server"}},
            "lspServers": {"language": {"command": "lsp"}},
            "outputStyles": "./output-styles/",
            "dependencies": ["helper"],
            "channels": [],
            "userConfig": {},
            "defaultEnabled": False,
        }
        with tempfile.TemporaryDirectory() as root_name:
            plugin_root = Path(root_name) / "example"
            manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
            manifest_path.parent.mkdir(parents=True)
            for field, value in declarations.items():
                manifest = {
                    "name": "example",
                    "version": "1.0.0",
                    "description": "example",
                    field: value,
                }
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                discovered = sync.discover_component_differences(
                    plugin_root, manifest
                )
                self.assertIn(
                    f".claude-plugin/plugin.json#/{field}", discovered, field
                )

            manifest = {
                "name": "example",
                "version": "1.0.0",
                "description": "example",
                "experimental": {
                    "themes": "./themes/",
                    "monitors": "./monitors/monitors.json",
                },
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            discovered = sync.discover_component_differences(plugin_root, manifest)
            self.assertIn(
                ".claude-plugin/plugin.json#/experimental/themes", discovered
            )
            self.assertIn(
                ".claude-plugin/plugin.json#/experimental/monitors", discovered
            )

    def test_marketplace_component_declarations_are_fail_closed_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            plugin_root = Path(root_name) / "example"
            plugin_root.mkdir()
            entry = {
                "name": "example",
                "source": "./plugins/example",
                "version": "1.0.0",
                "description": "example",
                "keywords": ["example"],
                "strict": False,
                "skills": "./extra-skills/",
                "commands": "./commands/",
                "agents": "./agents/",
                "hooks": {"hooks": {}},
                "mcpServers": {"server": {"command": "server"}},
                "lspServers": {"language": {"command": "lsp"}},
            }
            discovered = sync.discover_component_differences(
                plugin_root, {}, entry, 4
            )
            for field in (
                "strict",
                "skills",
                "commands",
                "agents",
                "hooks",
                "mcpServers",
                "lspServers",
            ):
                self.assertIn(f"@marketplace#/plugins/4/{field}", discovered)

    def test_default_claude_component_paths_require_classification(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            plugin_root = Path(root_name) / "example"
            plugin_root.mkdir()
            for filename in sync.STRUCTURAL_COMPONENT_FILES + ("SKILL.md",):
                (plugin_root / filename).write_text("component\n", encoding="utf-8")
            for directory in sync.STRUCTURAL_COMPONENT_DIRS:
                component = plugin_root / directory / "component.txt"
                component.parent.mkdir()
                component.write_text("component\n", encoding="utf-8")
            discovered = sync.discover_component_differences(plugin_root, {})
            for filename in sync.STRUCTURAL_COMPONENT_FILES + ("SKILL.md",):
                self.assertIn(filename, discovered)
            for directory in sync.STRUCTURAL_COMPONENT_DIRS:
                self.assertIn(f"{directory}/component.txt", discovered)

    def test_unknown_manifest_and_marketplace_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            plugin_root = Path(root_name) / "example"
            plugin_root.mkdir()
            with self.assertRaisesRegex(sync.SyncError, "unknown Claude plugin"):
                sync.discover_component_differences(
                    plugin_root, {"name": "example", "futureComponent": {}}
                )
            with self.assertRaisesRegex(sync.SyncError, "unknown Claude marketplace"):
                sync.discover_component_differences(
                    plugin_root,
                    {},
                    {"name": "example", "futureComponent": {}},
                    0,
                )

    def test_skill_frontmatter_uses_shared_runtime_intersection(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            plugin_root = root / "plugins" / "example"
            skill = plugin_root / "skills" / "example" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: example\ndescription: shared\n"
                "when_to_use: Claude only\n---\n",
                encoding="utf-8",
            )
            with mock.patch.object(sync, "ROOT", root):
                with self.assertRaisesRegex(sync.SyncError, "intersection"):
                    sync._validate_skill_manifests(plugin_root)

    def test_marketplace_header_unknown_semantics_fail_closed(self) -> None:
        base = {
            "name": "test",
            "owner": {"name": "owner"},
            "metadata": {"description": "description"},
            "plugins": [],
        }
        config = {"publisher": {"name": "owner"}}
        sync._validate_marketplace_header(base, config)
        for field in (
            "allowCrossMarketplaceDependenciesOn",
            "renames",
            "version",
        ):
            value = dict(base)
            value[field] = [] if field != "version" else "1.0.0"
            with self.subTest(field=field):
                with self.assertRaisesRegex(sync.SyncError, "explicit Codex mapping"):
                    sync._validate_marketplace_header(value, config)

    def test_json_pointer_component_digest_tracks_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            plugin_root = Path(root_name) / "example"
            manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
            manifest_path.parent.mkdir(parents=True)
            manifest = {"name": "example", "skills": "./skills-a/"}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            source = ".claude-plugin/plugin.json#/skills"
            first = sync.component_source_digest(plugin_root, source)
            manifest["skills"] = "./skills-b/"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            second = sync.component_source_digest(plugin_root, source)
            self.assertNotEqual(first, second)

    def test_plugin_tree_digest_includes_transitive_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            plugin_root = Path(root_name)
            script = plugin_root / "scripts" / "adapter-dependency.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/bash\nprintf first\\n\n", encoding="utf-8")
            first = sync.plugin_source_tree_digest(plugin_root)
            script.write_text("#!/bin/bash\nprintf second\\n\n", encoding="utf-8")
            second = sync.plugin_source_tree_digest(plugin_root)
            self.assertNotEqual(first, second)

    def test_plugin_tree_digest_includes_declared_verification_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            plugin_root = root / "plugins" / "example"
            plugin_root.mkdir(parents=True)
            evidence = root / "tests" / "adapter.py"
            evidence.parent.mkdir()
            evidence.write_text("assert adapter_safe\n", encoding="utf-8")
            with mock.patch.object(sync, "ROOT", root):
                first = sync.plugin_source_tree_digest(
                    plugin_root, ["tests/adapter.py"]
                )
                evidence.write_text("pass  # weakened\n", encoding="utf-8")
                second = sync.plugin_source_tree_digest(
                    plugin_root, ["tests/adapter.py"]
                )
            self.assertNotEqual(first, second)

    def test_digest_refresh_refuses_unknown_schema_without_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            marketplace_path = root / ".claude-plugin" / "marketplace.json"
            config_path = root / "codex" / "marketplace-overrides.json"
            marketplace_path.parent.mkdir(parents=True)
            config_path.parent.mkdir(parents=True)
            marketplace_path.write_text('{"plugins": []}\n', encoding="utf-8")
            config_path.write_text(
                '{"schemaVersion": 4, "plugins": {}}\n', encoding="utf-8"
            )
            with (
                mock.patch.object(sync, "ROOT", root),
                mock.patch.object(sync, "CLAUDE_MARKETPLACE_PATH", marketplace_path),
                mock.patch.object(sync, "PORT_CONFIG_PATH", config_path),
            ):
                with self.assertRaises(sync.SyncError):
                    sync.refresh_source_digests(["anything"])
            self.assertEqual(
                4,
                json.loads(config_path.read_text(encoding="utf-8"))["schemaVersion"],
            )

    def test_digest_refresh_rejects_float_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            marketplace_path = root / ".claude-plugin" / "marketplace.json"
            config_path = root / "codex" / "marketplace-overrides.json"
            marketplace_path.parent.mkdir(parents=True)
            config_path.parent.mkdir(parents=True)
            marketplace_path.write_text('{"plugins": []}\n', encoding="utf-8")
            original = b'{"schemaVersion": 3.0, "plugins": {}}\n'
            config_path.write_bytes(original)
            with (
                mock.patch.object(sync, "ROOT", root),
                mock.patch.object(sync, "CLAUDE_MARKETPLACE_PATH", marketplace_path),
                mock.patch.object(sync, "PORT_CONFIG_PATH", config_path),
            ):
                with self.assertRaisesRegex(sync.SyncError, "unsupported"):
                    sync.refresh_source_digests(["anything"])
            self.assertEqual(original, config_path.read_bytes())

    def test_digest_refresh_refuses_unlisted_stale_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            marketplace_path = root / ".claude-plugin" / "marketplace.json"
            config_path = root / "codex" / "marketplace-overrides.json"
            marketplace_path.parent.mkdir(parents=True)
            config_path.parent.mkdir(parents=True)
            evidence = root / "tests" / "test_evidence.py"
            evidence.parent.mkdir()
            evidence.write_text("# reviewed evidence\n", encoding="utf-8")
            names = ["plugin-a", "plugin-b"]
            marketplace_path.write_text(
                json.dumps(
                    {
                        "name": "test-marketplace",
                        "owner": {"name": "test-owner"},
                        "metadata": {"description": "test marketplace"},
                        "plugins": [{"name": name} for name in names],
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "schemaVersion": 3,
                "publisher": {
                    "name": "test-owner",
                    "url": "https://example.test/owner",
                },
                "plugins": {
                    name: {
                        "compatibility": {
                            "components": [],
                            "sourceTreeDigest": "0" * 64,
                            "verificationTests": [
                                {
                                    "path": "tests/test_evidence.py",
                                    "covers": "adapter evidence",
                                }
                            ],
                        }
                    }
                    for name in names
                },
            }
            original = (json.dumps(config) + "\n").encode()
            config_path.write_bytes(original)
            for name in names:
                plugin_root = root / "plugins" / name
                manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
                manifest_path.parent.mkdir(parents=True)
                manifest_path.write_text(
                    json.dumps(
                        {
                            "name": name,
                            "version": "1.0.0",
                            "description": "test plugin",
                            "author": {
                                "name": "test-owner",
                                "url": "https://example.test/owner",
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            with (
                mock.patch.object(sync, "ROOT", root),
                mock.patch.object(sync, "CLAUDE_MARKETPLACE_PATH", marketplace_path),
                mock.patch.object(sync, "PORT_CONFIG_PATH", config_path),
            ):
                with self.assertRaisesRegex(sync.SyncError, "exactly all stale"):
                    sync.refresh_source_digests(["plugin-a"])
            self.assertEqual(original, config_path.read_bytes())

    def test_digest_refresh_preview_is_no_write_and_exact_token_applies(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            marketplace_path, config_path = self._write_digest_fixture(
                root, ["plugin-a"]
            )
            original = config_path.read_bytes()
            with (
                mock.patch.object(sync, "ROOT", root),
                mock.patch.object(sync, "CLAUDE_MARKETPLACE_PATH", marketplace_path),
                mock.patch.object(sync, "PORT_CONFIG_PATH", config_path),
            ):
                token = sync.refresh_source_digests(["plugin-a"])
                self.assertEqual(original, config_path.read_bytes())
                with mock.patch.object(sync, "load_repository_state", return_value=None):
                    applied = sync.refresh_source_digests(["plugin-a"], token)
            self.assertEqual(token, applied)
            updated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotEqual(
                "0" * 64,
                updated["plugins"]["plugin-a"]["compatibility"][
                    "sourceTreeDigest"
                ],
            )

    def test_digest_refresh_wrong_token_is_no_write(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            marketplace_path, config_path = self._write_digest_fixture(
                root, ["plugin-a"]
            )
            original = config_path.read_bytes()
            with (
                mock.patch.object(sync, "ROOT", root),
                mock.patch.object(sync, "CLAUDE_MARKETPLACE_PATH", marketplace_path),
                mock.patch.object(sync, "PORT_CONFIG_PATH", config_path),
            ):
                with self.assertRaisesRegex(sync.SyncError, "does not match"):
                    sync.refresh_source_digests(["plugin-a"], "f" * 64)
            self.assertEqual(original, config_path.read_bytes())

    def test_digest_refresh_token_binds_all_review_inputs(self) -> None:
        mutations = (
            "plugin",
            "evidence",
            "config",
            "config_format",
            "marketplace",
            "marketplace_format",
            "mode",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root_name:
                root = Path(root_name).resolve()
                marketplace_path, config_path = self._write_digest_fixture(
                    root, ["plugin-a"]
                )
                script = root / "plugins" / "plugin-a" / "scripts" / "adapter.sh"
                evidence = root / "tests" / "test_evidence.py"
                with (
                    mock.patch.object(sync, "ROOT", root),
                    mock.patch.object(
                        sync, "CLAUDE_MARKETPLACE_PATH", marketplace_path
                    ),
                    mock.patch.object(sync, "PORT_CONFIG_PATH", config_path),
                ):
                    token = sync.refresh_source_digests(["plugin-a"])
                    if mutation == "plugin":
                        script.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
                    elif mutation == "evidence":
                        evidence.write_text("# weakened evidence\n", encoding="utf-8")
                    elif mutation == "config":
                        config = json.loads(config_path.read_text(encoding="utf-8"))
                        config["plugins"]["plugin-a"]["compatibility"][
                            "verificationTests"
                        ][0]["covers"] = "changed review scope"
                        config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
                    elif mutation == "config_format":
                        config = json.loads(config_path.read_text(encoding="utf-8"))
                        config_path.write_text(
                            json.dumps(config, indent=2) + "\n", encoding="utf-8"
                        )
                    elif mutation in {"marketplace", "marketplace_format"}:
                        marketplace = json.loads(
                            marketplace_path.read_text(encoding="utf-8")
                        )
                        if mutation == "marketplace":
                            marketplace["metadata"]["description"] = "changed"
                        marketplace_path.write_text(
                            json.dumps(
                                marketplace,
                                indent=2 if mutation == "marketplace_format" else None,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                    else:
                        script.chmod(0o755)
                    expected_config = config_path.read_bytes()
                    with self.assertRaisesRegex(
                        sync.SyncError, "does not match current repository state"
                    ):
                        sync.refresh_source_digests(["plugin-a"], token)
                self.assertEqual(expected_config, config_path.read_bytes())

    def test_digest_refresh_token_normalizes_requested_order(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            marketplace_path, config_path = self._write_digest_fixture(
                root, ["plugin-a", "plugin-b"]
            )
            with (
                mock.patch.object(sync, "ROOT", root),
                mock.patch.object(sync, "CLAUDE_MARKETPLACE_PATH", marketplace_path),
                mock.patch.object(sync, "PORT_CONFIG_PATH", config_path),
            ):
                first = sync.refresh_source_digests(["plugin-a", "plugin-b"])
                second = sync.refresh_source_digests(["plugin-b", "plugin-a"])
            self.assertEqual(first, second)

    def test_digest_refresh_config_write_race_preserves_external_change(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            marketplace_path, config_path = self._write_digest_fixture(
                root, ["plugin-a"]
            )
            with (
                mock.patch.object(sync, "ROOT", root),
                mock.patch.object(sync, "CLAUDE_MARKETPLACE_PATH", marketplace_path),
                mock.patch.object(sync, "PORT_CONFIG_PATH", config_path),
            ):
                token = sync.refresh_source_digests(["plugin-a"])
                original_atomic = sync._atomic_write_if_unchanged
                external = b'{"external": "config edit"}\n'

                def race(path, contents, expected):
                    config_path.write_bytes(external)
                    return original_atomic(path, contents, expected)

                with mock.patch.object(
                    sync, "_atomic_write_if_unchanged", side_effect=race
                ):
                    with self.assertRaisesRegex(sync.SyncError, "refusing lost update"):
                        sync.refresh_source_digests(["plugin-a"], token)
            self.assertEqual(external, config_path.read_bytes())

    def test_digest_refresh_marketplace_write_race_rolls_back_config(self) -> None:
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name).resolve()
            marketplace_path, config_path = self._write_digest_fixture(
                root, ["plugin-a"]
            )
            original_config = config_path.read_bytes()
            with (
                mock.patch.object(sync, "ROOT", root),
                mock.patch.object(sync, "CLAUDE_MARKETPLACE_PATH", marketplace_path),
                mock.patch.object(sync, "PORT_CONFIG_PATH", config_path),
            ):
                token = sync.refresh_source_digests(["plugin-a"])
                original_atomic = sync._atomic_write_if_unchanged

                def race(path, contents, expected):
                    marketplace = json.loads(
                        marketplace_path.read_text(encoding="utf-8")
                    )
                    marketplace["metadata"]["description"] = "concurrent edit"
                    marketplace_path.write_text(
                        json.dumps(marketplace) + "\n", encoding="utf-8"
                    )
                    return original_atomic(path, contents, expected)

                with mock.patch.object(
                    sync, "_atomic_write_if_unchanged", side_effect=race
                ):
                    with self.assertRaisesRegex(
                        sync.SyncError, "changed after digest review"
                    ):
                        sync.refresh_source_digests(["plugin-a"], token)
            self.assertEqual(original_config, config_path.read_bytes())
            self.assertIn("concurrent edit", marketplace_path.read_text(encoding="utf-8"))

    def test_every_plugin_declares_verification_evidence(self) -> None:
        for plugin in self.state.plugins:
            compatibility = plugin.port["compatibility"]
            tests = compatibility["verificationTests"]
            self.assertGreater(len(tests), 0, plugin.name)
            for verification in tests:
                path = ROOT / verification["path"]
                self.assertTrue(path.is_file(), f"{plugin.name}: {path}")
                self.assertTrue(verification["covers"].strip(), plugin.name)

    def test_non_full_plugins_declare_guarantee_differences(self) -> None:
        for plugin in self.state.plugins:
            compatibility = plugin.port["compatibility"]
            differences = compatibility["guaranteeDifferences"]
            if compatibility["level"] == "full":
                self.assertEqual(differences, [], plugin.name)
            else:
                self.assertGreater(len(differences), 0, plugin.name)

    def test_compatibility_doc_explains_guarantees_and_tests(self) -> None:
        rendered = sync.render_compatibility_doc(self.state).decode("utf-8")
        self.assertIn(
            "## 保証差と検証テスト (Guarantee differences and verification tests)",
            rendered,
        )
        self.assertIn("LLM の意味判断品質そのものを証明", rendered)
        self.assertIn("`surface-unavailable`", rendered)
        self.assertIn("`host-required`", rendered)
        self.assertIn("`not-applicable`", rendered)
        self.assertIn("SHA-256 fingerprint", rendered)
        self.assertIn("--refresh-source-digests", rendered)
        self.assertIn("--plugin <name>", rendered)
        self.assertIn("--approve <action-token>", rendered)
        self.assertIn("declared test evidence", rendered)
        self.assertIn("sourceTreeDigest", rendered)

    def test_plugin_readme_versions_match_manifests(self) -> None:
        for plugin in self.state.plugins:
            readme = plugin.root / "README.md"
            self.assertTrue(readme.is_file(), plugin.name)
            match = sync.PLUGIN_README_VERSION_RE.search(readme.read_text(encoding="utf-8"))
            self.assertIsNotNone(match, plugin.name)
            self.assertEqual(match.group(1), plugin.manifest["version"], plugin.name)

    def test_generated_paths_reject_symlink_escape(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_name,
            tempfile.TemporaryDirectory() as outside_name,
        ):
            root = Path(root_name).resolve()
            (root / "managed").symlink_to(Path(outside_name), target_is_directory=True)
            with self.assertRaises(sync.SyncError):
                sync.assert_safe_repo_path(root, root / "managed" / "artifact.json")

    def test_natsuume_writing_skills_name_both_runtime_surfaces(self) -> None:
        skills = ROOT / "plugins" / "natsuume-writing" / "skills"
        draft = (skills / "draft" / "SKILL.md").read_text(encoding="utf-8")
        outline = (skills / "outline" / "SKILL.md").read_text(encoding="utf-8")
        review = (skills / "review" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Codex では `$natsuume-writing:outline`", draft)
        self.assertIn("Codex では通常のユーザー質問", outline)
        self.assertIn("Codex では native subagent", review)
        self.assertIn("Codex では通常のユーザー質問", review)


if __name__ == "__main__":
    unittest.main()

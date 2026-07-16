from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CodexPromptInjectionTest(unittest.TestCase):
    PLUGINS = {
        "agent-discipline": {
            "session_script": "inject-session.sh",
            "subagent_script": "inject-subagent.sh",
        },
        "ui-discipline": {
            "session_script": "inject-session.sh",
            "subagent_script": "inject-subagent.sh",
        },
        "natsuume-writing": {
            "session_script": "inject-session.sh",
        },
    }

    def run_injector(self, plugin: str, script: str) -> str:
        path = ROOT / "plugins" / plugin / "codex" / "scripts" / script
        result = subprocess.run(
            ["/bin/sh", str(path)],
            cwd=ROOT,
            input=json.dumps(
                {
                    "session_id": "prompt-test",
                    "hook_event_name": (
                        "SubagentStart" if "subagent" in script else "SessionStart"
                    ),
                    "model": "gpt-5.6-luna",
                }
            ),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        return payload["hookSpecificOutput"]["additionalContext"]

    def test_codex_manifests_use_runtime_specific_prompt_hooks(self) -> None:
        overrides = json.loads(
            (ROOT / "codex" / "marketplace-overrides.json").read_text(
                encoding="utf-8"
            )
        )
        for plugin, config in self.PLUGINS.items():
            with self.subTest(plugin=plugin):
                root = ROOT / "plugins" / plugin
                hooks = json.loads(
                    (root / "codex" / "hooks.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    "./codex/hooks.json",
                    overrides["plugins"][plugin]["manifest"]["hooks"],
                )
                self.assertIn(
                    "codex/",
                    overrides["plugins"][plugin]["versioning"]["codexOnlyPaths"],
                )
                handlers = [
                    handler
                    for groups in hooks["hooks"].values()
                    for group in groups
                    for handler in group["hooks"]
                ]
                self.assertTrue(all(handler["type"] == "command" for handler in handlers))
                self.assertTrue(
                    all("${PLUGIN_ROOT}" in handler["command"] for handler in handlers)
                )
                self.assertIn(
                    f"/codex/scripts/{config['session_script']}",
                    hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"],
                )
                if "subagent_script" in config:
                    self.assertIn(
                        f"/codex/scripts/{config['subagent_script']}",
                        hooks["hooks"]["SubagentStart"][0]["hooks"][0]["command"],
                    )

    def test_codex_prompt_injectors_are_portable_and_bounded(self) -> None:
        for plugin, config in self.PLUGINS.items():
            for script in config.values():
                with self.subTest(plugin=plugin, script=script):
                    path = ROOT / "plugins" / plugin / "codex" / "scripts" / script
                    source = path.read_text(encoding="utf-8")
                    self.assertTrue(source.startswith("#!/bin/sh\n"))
                    self.assertIn("CDPATH='' cd", source)
                    self.assertIn("|| exit 0", source)
                    self.assertTrue(os.access(path, os.X_OK), path)
                    syntax = subprocess.run(
                        ["/bin/sh", "-n", str(path)],
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(0, syntax.returncode, syntax.stderr)
                    prompt = self.run_injector(plugin, script)
                    self.assertLessEqual(len(prompt), 8000)
                    self.assertIn("GPT-5.6 Sol", prompt)
                    self.assertIn("GPT-5.6 Luna", prompt)

    def test_agent_discipline_prompt_is_codex_native_and_outcome_focused(self) -> None:
        prompt = self.run_injector("agent-discipline", "inject-session.sh")
        for heading in ("Goal", "Context", "Boundaries", "Done when"):
            self.assertIn(heading, prompt)
        for rule_id in (
            "bash-decompose",
            "decision-boundary",
            "issue-contract",
            "issue-claim",
            "verification",
            "delegation",
        ):
            self.assertIn(f"rule:{rule_id}", prompt)
        for claude_term in (
            "AskUserQuestion",
            "CLAUDE_CODE_SESSION_ID",
            'permission_mode == "auto"',
            "Fable",
            "Sonnet",
            "Agent|Task",
            "PostToolBatch",
            "Workflow agent()",
            "Phase A",
            "Phase B",
        ):
            self.assertNotIn(claude_term, prompt)
        self.assertIn("request_user_input", prompt)
        self.assertIn("利用できる場合", prompt)
        self.assertIn("依頼された scope", prompt)

        validator_prompt = (
            ROOT
            / "plugins"
            / "agent-discipline"
            / "codex"
            / "prompts"
            / "semantic-validator.md"
        ).read_text(encoding="utf-8")
        self.assertIn("selected/default/recommended", validator_prompt)
        self.assertIn("neutral options that remain clearly open", validator_prompt)
        self.assertNotIn("A recommendation is not automatically", validator_prompt)

        subagent = self.run_injector("agent-discipline", "inject-subagent.sh")
        for label in (
            "Goal",
            "Context",
            "Boundaries",
            "Deliverable",
            "Verification",
            "Escalation",
        ):
            self.assertIn(label, subagent)
        self.assertNotIn("AskUserQuestion", subagent)
        self.assertNotIn("SendMessage", subagent)

    def test_ui_prompt_preserves_rules_with_codex_question_semantics(self) -> None:
        prompt = self.run_injector("ui-discipline", "inject-session.sh")
        for rule_id in (
            "component-layers",
            "composition",
            "component-search",
            "visibility-taxonomy",
            "layout-stability",
            "design-tokens",
            "a11y-basics",
            "async-states",
            "robustness",
            "visual-direction",
        ):
            self.assertIn(f"rule:{rule_id}", prompt)
        self.assertIn("request_user_input", prompt)
        self.assertIn("利用できる場合", prompt)
        self.assertIn("3案", prompt)
        self.assertNotIn("3〜4案", prompt)
        self.assertNotIn("AskUserQuestion", prompt)
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", prompt)

        subagent = self.run_injector("ui-discipline", "inject-subagent.sh")
        subagent_note = (
            ROOT
            / "plugins"
            / "ui-discipline"
            / "codex"
            / "prompts"
            / "subagent.md"
        ).read_text(encoding="utf-8").strip()
        self.assertIn("親 agent", subagent)
        self.assertIn("実装せずに返す", subagent)
        self.assertTrue(subagent.endswith(subagent_note))
        self.assertNotIn("AskUserQuestion", subagent)

    def test_writing_prompt_uses_codex_skill_names(self) -> None:
        prompt = self.run_injector("natsuume-writing", "inject-session.sh")
        for skill in (
            "$natsuume-writing:outline",
            "$natsuume-writing:draft",
            "$natsuume-writing:review",
        ):
            self.assertIn(skill, prompt)
        self.assertNotIn("/natsuume-writing:", prompt)
        self.assertIn("執筆と無関係な作業では適用しない", prompt)


if __name__ == "__main__":
    unittest.main()

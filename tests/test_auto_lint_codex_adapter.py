from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "plugins" / "auto-lint-check" / "hooks" / "scripts" / "lib"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


paths_module = load_module("extract_edit_paths", LIB / "extract-edit-paths.py")
DETECT_SCRIPT = LIB / "detect-new-ignores.py"
BLOCK_SCRIPT = LIB.parent / "block-ignore-lint-comment.sh"
FIND_CONFIG_ROOT = LIB / "find-config-root.sh"
CODE_FORMAT = LIB.parent / "code-format.sh"


class EditPathAdapterTest(unittest.TestCase):
    def test_claude_file_path_is_preserved(self) -> None:
        payload = {"tool_name": "Edit", "tool_input": {"file_path": "src/a.py"}}
        self.assertEqual(
            paths_module.extract_paths(payload),
            [os.path.abspath("src/a.py")],
        )

    def test_apply_patch_extracts_add_update_and_move_destination(self) -> None:
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {
                "command": """*** Begin Patch
*** Update File: src/old.py
@@
-old
+new
*** Move to: src/new.py
*** Add File: src/added file.ts
+export const value = 1;
*** Delete File: src/deleted.py
*** End Patch"""
            },
        }
        self.assertEqual(
            paths_module.extract_paths(payload),
            [os.path.abspath("src/new.py"), os.path.abspath("src/added file.ts")],
        )

    def test_apply_patch_deduplicates_paths(self) -> None:
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {
                "command": """*** Begin Patch
*** Update File: src/a.py
@@
-a
+b
*** Update File: src/a.py
@@
-b
+c
*** End Patch"""
            },
        }
        self.assertEqual(paths_module.extract_paths(payload), [os.path.abspath("src/a.py")])


class IgnoreAdapterTest(unittest.TestCase):
    def run_detector(self, payload: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(DETECT_SCRIPT)],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_apply_patch_new_ignore_is_denied(self) -> None:
        result = self.run_detector(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": """*** Begin Patch
*** Update File: app.py
@@
-value = call()
+value = call()  # noqa: F401
*** End Patch"""
                },
            }
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"Ruff", result.stdout)

    def test_added_source_line_starting_with_plus_is_still_inspected(self) -> None:
        result = self.run_detector(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": """*** Begin Patch
*** Add File: app.js
+++value; // eslint-disable-line no-undef
*** End Patch"""
                },
            }
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"ESLint", result.stdout)

    def test_apply_patch_removing_ignore_is_allowed(self) -> None:
        result = self.run_detector(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": """*** Begin Patch
*** Update File: app.py
@@
-value = call()  # noqa: F401
+value = call()
*** End Patch"""
                },
            }
        )
        self.assertEqual(result.returncode, 0)

    def test_apply_patch_moving_same_ignore_is_allowed(self) -> None:
        result = self.run_detector(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": """*** Begin Patch
*** Update File: app.py
@@
-value = call()  # noqa: F401
+value = call()  # noqa: F401
*** End Patch"""
                },
            }
        )
        self.assertEqual(result.returncode, 0)

    def test_malformed_payload_is_fail_open_signal(self) -> None:
        result = subprocess.run(
            [sys.executable, str(DETECT_SCRIPT)],
            input=b"not-json",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 1)

    @unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
    def test_shell_hook_emits_codex_compatible_deny_json(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {
                "command": """*** Begin Patch
*** Update File: app.py
@@
-value = call()
+value = call()  # noqa: F401
*** End Patch"""
            },
        }
        result = subprocess.run(
            ["bash", str(BLOCK_SCRIPT)],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        response = json.loads(result.stdout)
        hook_output = response["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "PreToolUse")
        self.assertEqual(hook_output["permissionDecision"], "deny")


class ConfigRootResolutionTest(unittest.TestCase):
    def run_finder(self, file_path: Path, linter: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(FIND_CONFIG_ROOT), str(file_path), linter],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_symlinked_workspace_path_uses_real_config_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "real-repo"
            source = repository / "packages" / "app"
            source.mkdir(parents=True)
            (repository / ".git").mkdir()
            (repository / "eslint.config.js").write_text(
                "export default [];\n", encoding="utf-8"
            )
            real_file = source / "index.js"
            real_file.write_text("const value = 1;\n", encoding="utf-8")

            linked_source = root / "linked-app"
            linked_source.symlink_to(source, target_is_directory=True)

            result = self.run_finder(linked_source / "index.js", "eslint")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(repository.resolve()))

    def test_real_workspace_path_keeps_existing_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            source = repository / "src"
            source.mkdir(parents=True)
            (repository / ".git").mkdir()
            (repository / ".prettierrc").write_text("{}\n", encoding="utf-8")
            file_path = source / "index.js"
            file_path.write_text("const value = 1;\n", encoding="utf-8")

            result = self.run_finder(file_path, "prettier")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(repository.resolve()))


class CodeFormatRepositoryBoundaryTest(unittest.TestCase):
    def run_hook(self, file_path: Path, marker: Path) -> subprocess.CompletedProcess[bytes]:
        payload = {"tool_name": "Edit", "tool_input": {"file_path": str(file_path)}}
        env = os.environ.copy()
        env["FORMAT_MARKER"] = str(marker)
        return subprocess.run(
            ["bash", str(CODE_FORMAT)],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def make_eslint_fixture(self, root: Path) -> Path:
        source = root / "src"
        source.mkdir(parents=True)
        (root / "eslint.config.js").write_text("export default [];\n", encoding="utf-8")
        eslint = root / "node_modules" / ".bin" / "eslint"
        eslint.parent.mkdir(parents=True)
        eslint.write_text('#!/bin/sh\n: > "$FORMAT_MARKER"\n', encoding="utf-8")
        eslint.chmod(0o755)
        file_path = source / "scratch.js"
        file_path.write_text("const value = 1;\n", encoding="utf-8")
        return file_path

    @unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
    def test_file_outside_git_repository_is_not_formatted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "outside-project"
            file_path = self.make_eslint_fixture(root)
            marker = Path(tmp) / "formatter-ran"

            result = self.run_hook(file_path, marker)

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertFalse(marker.exists())

    @unittest.skipUnless(
        shutil.which("jq") and shutil.which("git"),
        "hook integration requires jq and git",
    )
    def test_untracked_file_inside_git_repository_is_still_formatted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            file_path = self.make_eslint_fixture(repository)
            subprocess.run(
                ["git", "init", "-q", str(repository)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            marker = Path(tmp) / "formatter-ran"

            result = self.run_hook(file_path, marker)

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(marker.exists())

if __name__ == "__main__":
    unittest.main()

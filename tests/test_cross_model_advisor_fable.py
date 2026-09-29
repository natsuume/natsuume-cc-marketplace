"""cross-model-advisor: plugin 名・runner 名と Codex advisor による相談手順の契約テスト。

- plugin 名は `cross-model-advisor` で、marketplace.json のトップレベル `renames` が旧名
  `codex-advisor` を新名へ対応づける。runner は `codex-advisor-runner` / `codex-rescue-runner` /
  `codex-review-runner` の 3 体で、frontmatter の name はファイル名と一致する。
- advisor-rules.md と consult skill は `/cross-model-advisor:consult` の手順と
  `cross-model-advisor:codex-advisor-runner` の起動を記述する。advisor-rules.md は 8,000 字の
  注入予算に収まる。
- review cadence checkpoint は codex-advisor-runner に相談し、gate が検証する attestation は
  codex-advisor-runner が発行する。
- リポジトリ内の `codex-advisor` は renames の旧名・新名の一部 (`codex-advisor-runner`)・
  Codex advisor の wrapper 名 (`run-codex-advisor.sh` とそのログ prefix) 以外に残らない。

subTest は使わない: 違反をリストに集約して 1 テスト = 1 判定に保つ。
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "cross-model-advisor"
OLD_PLUGIN = ROOT / "plugins" / "codex-advisor"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_JSON = PLUGIN / ".claude-plugin" / "plugin.json"
AGENTS_DIR = PLUGIN / "agents"
ADVISOR_RULES = PLUGIN / "hooks" / "prompts" / "advisor-rules.md"
CONSULT_SKILL = PLUGIN / "skills" / "consult" / "SKILL.md"
REVIEW_CADENCE_RULES = (
    ROOT / "plugins" / "pre-push-codex-review" / "hooks" / "prompts" / "review-cadence-rules.md"
)

EXPECTED_AGENTS = {
    "codex-advisor-runner.md",
    "codex-rescue-runner.md",
    "codex-review-runner.md",
}

CODEX_RUNNER = "cross-model-advisor:codex-advisor-runner"

# リポジトリ内に残ってよい `codex-advisor` を含む識別子 (新名の一部・wrapper 名とそのログ prefix)。
ALLOWED_CODEX_ADVISOR_TOKENS = ("codex-advisor-runner", "run-codex-advisor")
# grep の対象外 (本テスト自身)。
GREP_EXCLUDED_FILES = ("tests/test_cross_model_advisor_fable.py",)
# 旧名を利用者向けの移行手順として書く README の節 (見出しから次の `## ` 見出しの直前まで)。
MIGRATION_SECTION_FILE = "plugins/cross-model-advisor/README.md"
MIGRATION_SECTION_HEADING = "## codex-advisor からの移行"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(text: str) -> str:
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    return match.group(1) if match else ""


class RenameTest(unittest.TestCase):
    """plugin・agent の改名と marketplace の renames。"""

    def test_plugin_directory_is_moved(self) -> None:
        self.assertTrue(PLUGIN.is_dir(), f"{PLUGIN} が無い")
        self.assertFalse(OLD_PLUGIN.exists(), f"{OLD_PLUGIN} が残っている")

    def test_plugin_json_name_and_major_version(self) -> None:
        manifest = json.loads(read(PLUGIN_JSON))
        self.assertEqual("cross-model-advisor", manifest["name"])
        self.assertEqual("6.0.0", manifest["version"])

    def test_agent_files_are_renamed(self) -> None:
        names = {path.name for path in AGENTS_DIR.glob("*.md")}
        self.assertEqual(EXPECTED_AGENTS, names)

    def test_agent_frontmatter_names_match_file_names(self) -> None:
        wrong = [
            path.name
            for path in AGENTS_DIR.glob("*.md")
            if f"name: {path.stem}" not in frontmatter(read(path)).splitlines()
        ]
        self.assertEqual([], wrong, f"frontmatter の name がファイル名と一致しない: {wrong}")

    def test_marketplace_entry_and_renames(self) -> None:
        marketplace = json.loads(read(MARKETPLACE))
        names = [plugin["name"] for plugin in marketplace["plugins"]]
        self.assertIn("cross-model-advisor", names)
        self.assertNotIn("codex-advisor", names)
        entry = next(p for p in marketplace["plugins"] if p["name"] == "cross-model-advisor")
        self.assertEqual("./plugins/cross-model-advisor", entry["source"])
        self.assertEqual("cross-model-advisor", marketplace.get("renames", {}).get("codex-advisor"))

    def test_no_leftover_codex_advisor_references(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        offenders = []
        for relative in tracked:
            if relative in GREP_EXCLUDED_FILES:
                continue
            path = ROOT / relative
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            in_migration_section = False
            for number, line in enumerate(text.splitlines(), start=1):
                if relative == MIGRATION_SECTION_FILE and line.startswith("## "):
                    in_migration_section = line == MIGRATION_SECTION_HEADING
                if in_migration_section:
                    continue
                stripped = line
                for token in ALLOWED_CODEX_ADVISOR_TOKENS:
                    stripped = stripped.replace(token, "")
                if relative == ".claude-plugin/marketplace.json":
                    stripped = stripped.replace('"codex-advisor": "cross-model-advisor"', "")
                if "codex-advisor" in stripped:
                    offenders.append(f"{relative}:{number}")
        self.assertEqual([], offenders, "codex-advisor が残る箇所:\n" + "\n".join(offenders))


class ConsultRulesTest(unittest.TestCase):
    """advisor-rules.md / consult skill / review-cadence-rules.md の相談手順の記述。"""

    def test_advisor_rules_describe_consult(self) -> None:
        text = read(ADVISOR_RULES)
        missing = [
            phrase
            for phrase in (
                "/cross-model-advisor:consult",
                f"`{CODEX_RUNNER}`",
                "別系統モデルの独立視点",
            )
            if phrase not in text
        ]
        self.assertEqual([], missing, f"advisor-rules.md に無い記述: {missing}")

    def test_advisor_rules_size_within_inline_limit(self) -> None:
        units = len(read(ADVISOR_RULES).encode("utf-16-le")) // 2
        self.assertLessEqual(units, 8000)

    def test_consult_skill_launches_codex_runner(self) -> None:
        text = read(CONSULT_SKILL)
        missing = [
            phrase
            for phrase in (
                "/cross-model-advisor:consult",
                f'`subagent_type: "{CODEX_RUNNER}"`',
            )
            if phrase not in text
        ]
        self.assertEqual([], missing, f"consult skill に無い記述: {missing}")

    def test_review_cadence_checkpoint_consults_codex(self) -> None:
        text = read(REVIEW_CADENCE_RULES)
        missing = [
            phrase for phrase in (f"`{CODEX_RUNNER}`", "attestation") if phrase not in text
        ]
        self.assertEqual([], missing, f"review-cadence-rules.md に無い記述: {missing}")


if __name__ == "__main__":
    unittest.main()

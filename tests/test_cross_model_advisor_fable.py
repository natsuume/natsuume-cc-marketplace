"""cross-model-advisor: codex-advisor の改名と Codex / Fable 並列相談への一般化の契約テスト。

背景 (spec-first Phase A):
- plugin 名を `codex-advisor` から `cross-model-advisor` に改め、marketplace.json のトップレベル
  `renames` で旧名から新名へ移行する。runner は `codex-advisor-runner` / `codex-rescue-runner` /
  `codex-review-runner` に改名し、Fable の助言役 `fable-advisor-runner` を新設する。
- fable-advisor-runner の tools は Bash, Read, Glob, Grep。frontmatter の model は opus
  (frontmatter は agent-discipline の hook から見えず、fable にすると model 未指定の起動が
  使用率判定を経ずに Fable で走るため)。Fable での起動は呼び出し側の `model: "fable"` 明示に限る。
- 相談 1 回につき codex-advisor-runner (`model: "sonnet"`) と fable-advisor-runner
  (`model: "fable"`) を同一メッセージで並列に起動する。相談前に plugin 同梱の判定コマンド
  `bin/cross-model-advisor-fable-usage` (1 行目 = available / over / unknown、2 行目 = 理由) を
  実行し、available でなければ Fable をスキップする。deny された場合も再起動せずスキップする。
- 使用率判定は agent-discipline と同じ仕様 (cache は
  `natsuume-statusline/weekly-scoped.json`、閾値 env `FABLE_WEEKLY_MAX_PERCENT` 既定 80、
  `percent <= 閾値` で available)。cache は書き込まない。
- review cadence checkpoint も並列に相談し、gate が検証する attestation は従来どおり
  codex-advisor-runner が発行する。
- 改名後、リポジトリ内の `codex-advisor` は renames の旧名・新名の一部 (`codex-advisor-runner`)・
  Codex advisor の wrapper 名 (`run-codex-advisor.sh` とそのログ prefix) 以外に残らない。

subTest は使わない: 違反をリストに集約して 1 テスト = 1 判定に保つ。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "cross-model-advisor"
OLD_PLUGIN = ROOT / "plugins" / "codex-advisor"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_JSON = PLUGIN / ".claude-plugin" / "plugin.json"
AGENTS_DIR = PLUGIN / "agents"
FABLE_RUNNER = AGENTS_DIR / "fable-advisor-runner.md"
USAGE_COMMAND = PLUGIN / "bin" / "cross-model-advisor-fable-usage"
ADVISOR_RULES = PLUGIN / "hooks" / "prompts" / "advisor-rules.md"
CONSULT_SKILL = PLUGIN / "skills" / "consult" / "SKILL.md"
REVIEW_CADENCE_RULES = (
    ROOT / "plugins" / "pre-push-codex-review" / "hooks" / "prompts" / "review-cadence-rules.md"
)

EXPECTED_AGENTS = {
    "codex-advisor-runner.md",
    "codex-rescue-runner.md",
    "codex-review-runner.md",
    "fable-advisor-runner.md",
}

CODEX_RUNNER = "cross-model-advisor:codex-advisor-runner"
FABLE_RUNNER_TYPE = "cross-model-advisor:fable-advisor-runner"
USAGE_COMMAND_NAME = "cross-model-advisor-fable-usage"

# リポジトリ内に残ってよい `codex-advisor` を含む識別子 (新名の一部・wrapper 名とそのログ prefix)。
ALLOWED_CODEX_ADVISOR_TOKENS = ("codex-advisor-runner", "run-codex-advisor")
# grep の対象外 (本テスト自身)。
GREP_EXCLUDED_FILES = ("tests/test_cross_model_advisor_fable.py",)
# 旧名を利用者向けの移行手順として書く README の節 (見出しから次の `## ` 見出しの直前まで)。
MIGRATION_SECTION_FILE = "plugins/cross-model-advisor/README.md"
MIGRATION_SECTION_HEADING = "## codex-advisor からの移行"

CACHE_RELATIVE = Path("natsuume-statusline") / "weekly-scoped.json"
AGE_SECONDS_KEY = "__age_seconds__"
UNSET = object()
OMIT = object()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(text: str) -> str:
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    return match.group(1) if match else ""


def fable_entry(percent: object) -> dict[str, object]:
    return {"display_name": "Fable", "percent": percent, "resets_at": "2026-09-28T00:00:00Z"}


def cache(entries: object = UNSET, *, age_seconds: int = 0, fetched_at: object = UNSET) -> dict[str, object]:
    body: dict[str, object] = {"consecutive_failures": 0, "next_attempt_at": 0}
    if fetched_at is UNSET:
        body[AGE_SECONDS_KEY] = age_seconds
    elif fetched_at is not OMIT:
        body["fetched_at"] = fetched_at
    if entries is UNSET:
        body["weekly_scoped"] = [fable_entry(10)]
    elif entries is not OMIT:
        body["weekly_scoped"] = entries
    return body


def resolve_fetched_at(body: object) -> object:
    if not isinstance(body, dict) or AGE_SECONDS_KEY not in body:
        return body
    resolved = {key: value for key, value in body.items() if key != AGE_SECONDS_KEY}
    resolved["fetched_at"] = int(time.time()) - int(body[AGE_SECONDS_KEY])
    return resolved


def row(label: str, *, status: str, cache_body: object = UNSET, cache_raw: str | None = None,
        cache_kind: str = "file", threshold: object = UNSET, reason: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "label": label,
        "status": status,
        "cache_body": cache_body,
        "cache_raw": cache_raw,
        "cache_kind": cache_kind,
        "threshold": threshold,
        "reason": reason,
    }


USAGE_TABLE = (
    row("available/usage-10", status="available"),
    row("available/exactly-at-threshold", status="available", cache_body=cache([fable_entry(80)])),
    row("available/future-fetched-at", status="available", cache_body=cache(age_seconds=-600)),
    row("over/81", status="over", cache_body=cache([fable_entry(81)]), reason=(r"81", r"80")),
    row("over/decimal", status="over", cache_body=cache([fable_entry(80.5)]), reason=(r"80\.5",)),
    row("over/max-of-multiple", status="over", cache_body=cache([fable_entry(10), fable_entry(90)]),
        reason=(r"90",)),
    row("threshold/env-50/over", status="over", threshold="50", cache_body=cache([fable_entry(60)]),
        reason=(r"50",)),
    row("threshold/env-50/at", status="available", threshold="50", cache_body=cache([fable_entry(50)])),
    row("threshold/invalid-word/falls-back-to-80", status="over", threshold="abc",
        cache_body=cache([fable_entry(81)]), reason=(r"80",)),
    row("threshold/invalid-101/falls-back-to-80", status="available", threshold="101",
        cache_body=cache([fable_entry(79)])),
    row("unknown/cache-missing", status="unknown", cache_kind="missing", reason=(r"確認できない",)),
    row("unknown/cache-symlink", status="unknown", cache_kind="symlink", reason=(r"確認できない",)),
    row("unknown/not-json", status="unknown", cache_raw="{not json", reason=(r"確認できない",)),
    row("unknown/stale", status="unknown", cache_body=cache(age_seconds=1900), reason=(r"確認できない",)),
    row("unknown/fetched-at-missing", status="unknown", cache_body=cache(fetched_at=OMIT),
        reason=(r"確認できない",)),
    row("unknown/no-fable-entry", status="unknown",
        cache_body=cache([{"display_name": "Opus", "percent": 10}]), reason=(r"確認できない",)),
    row("unknown/percent-non-numeric", status="unknown", cache_body=cache([fable_entry("10")]),
        reason=(r"確認できない",)),
)


class RenameTest(unittest.TestCase):
    """plugin・agent の改名と marketplace の renames。"""

    def test_plugin_directory_is_moved(self) -> None:
        self.assertTrue(PLUGIN.is_dir(), f"{PLUGIN} が無い")
        self.assertFalse(OLD_PLUGIN.exists(), f"{OLD_PLUGIN} が残っている")

    def test_plugin_json_name_and_major_version(self) -> None:
        manifest = json.loads(read(PLUGIN_JSON))
        self.assertEqual("cross-model-advisor", manifest["name"])
        self.assertEqual("5.0.5", manifest["version"])

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

    def test_readme_describes_migration_from_old_name(self) -> None:
        text = read(ROOT / MIGRATION_SECTION_FILE)
        self.assertIn(MIGRATION_SECTION_HEADING, text.splitlines())
        section = text.split(MIGRATION_SECTION_HEADING, 1)[1].split("\n## ", 1)[0]
        missing = [
            phrase
            for phrase in ("renames", "2.1.193", "pre-push-codex-review", "同時に更新", "新しいセッション")
            if phrase not in section
        ]
        self.assertEqual([], missing, f"移行節に無い記述: {missing}")

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


class FableAdvisorRunnerDefinitionTest(unittest.TestCase):
    """fable-advisor-runner の agent 定義。"""

    def test_frontmatter_tools_and_model(self) -> None:
        lines = frontmatter(read(FABLE_RUNNER)).splitlines()
        self.assertIn("name: fable-advisor-runner", lines)
        self.assertIn("tools: Bash, Read, Glob, Grep", lines)
        self.assertIn("model: opus", lines)

    def test_body_forbids_side_effects_and_matches_advice_contract(self) -> None:
        body = read(FABLE_RUNNER)
        missing = [
            phrase
            for phrase in (
                f'`subagent_type: "{FABLE_RUNNER_TYPE}"`',
                '`model: "fable"`',
                "ファイル変更",
                "git 状態変更",
                "外部サービス呼び出し",
                "推奨方針・理由・リスク・次の一手",
            )
            if phrase not in body
        ]
        self.assertEqual([], missing, f"fable-advisor-runner の本文に無い記述: {missing}")


class UsageCommandFileTest(unittest.TestCase):
    def test_command_is_an_executable_bash_script(self) -> None:
        self.assertTrue(USAGE_COMMAND.is_file(), f"{USAGE_COMMAND} が無い")
        self.assertTrue(os.access(USAGE_COMMAND, os.X_OK))
        self.assertEqual("#!/bin/bash", read(USAGE_COMMAND).splitlines()[0])


@unittest.skipUnless(shutil.which("jq"), "usage command requires jq")
class UsageCommandDecisionTableTest(unittest.TestCase):
    """判定コマンドの出力 (1 行目 = available / over / unknown、2 行目 = 理由)。"""

    def run_case(self, case: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], bool]:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            home = temp / "home"
            cache_home = temp / "cache"
            home.mkdir()
            cache_home.mkdir()
            env = {"PATH": os.environ["PATH"], "HOME": str(home), "XDG_CACHE_HOME": str(cache_home)}
            if case["threshold"] is not UNSET:
                env["FABLE_WEEKLY_MAX_PERCENT"] = str(case["threshold"])
            cache_path = cache_home / CACHE_RELATIVE
            cache_path.parent.mkdir(parents=True)
            body = resolve_fetched_at(cache() if case["cache_body"] is UNSET else case["cache_body"])
            text = case["cache_raw"] if case["cache_raw"] is not None else json.dumps(body)
            if case["cache_kind"] == "file":
                cache_path.write_text(str(text), encoding="utf-8")
            elif case["cache_kind"] == "symlink":
                target = temp / "real.json"
                target.write_text(str(text), encoding="utf-8")
                cache_path.symlink_to(target)
            before = sorted(p.name for p in cache_path.parent.iterdir())
            before_text = cache_path.read_text(encoding="utf-8") if cache_path.is_file() else None
            result = subprocess.run(
                ["/bin/bash", str(USAGE_COMMAND)],
                cwd=temp, env=env, text=True, capture_output=True, timeout=10, check=False,
            )
            after = sorted(p.name for p in cache_path.parent.iterdir())
            after_text = cache_path.read_text(encoding="utf-8") if cache_path.is_file() else None
            return result, (before != after or before_text != after_text)

    def test_decision_table(self) -> None:
        if not USAGE_COMMAND.is_file():
            self.fail(f"{USAGE_COMMAND} が無い")
        violations = []
        for case in USAGE_TABLE:
            label = str(case["label"])
            result, changed = self.run_case(case)
            if result.returncode != 0:
                violations.append(f"{label}: exit {result.returncode} ({result.stderr.strip()})")
            if changed:
                violations.append(f"{label}: cache が書き換えられた")
            lines = result.stdout.splitlines()
            if len(lines) != 2:
                violations.append(f"{label}: 出力が 2 行でない ({result.stdout!r})")
                continue
            if lines[0] != case["status"]:
                violations.append(f"{label}: status が {lines[0]!r} (期待 {case['status']!r})")
            for pattern in case["reason"]:  # type: ignore[union-attr]
                if not re.search(str(pattern), lines[1]):
                    violations.append(f"{label}: 理由に {pattern!r} が無い ({lines[1]})")
        self.assertEqual([], violations, "\n".join(violations))


class ParallelConsultRulesTest(unittest.TestCase):
    """advisor-rules.md / consult skill / review-cadence-rules.md の並列相談の記述。"""

    def test_advisor_rules_describe_parallel_consult(self) -> None:
        text = read(ADVISOR_RULES)
        missing = [
            phrase
            for phrase in (
                "/cross-model-advisor:consult",
                f"`{CODEX_RUNNER}`",
                f"`{FABLE_RUNNER_TYPE}`",
                f"`{USAGE_COMMAND_NAME}`",
                "同一メッセージで並列に起動する",
                '`model: "fable"`',
                "再起動せずスキップする",
                "別系統モデルの独立視点",
                "同系統の上位モデルの視点",
                "advisor ごとに",
                "スキップした側と理由",
            )
            if phrase not in text
        ]
        self.assertEqual([], missing, f"advisor-rules.md に無い記述: {missing}")

    def test_advisor_rules_size_within_inline_limit(self) -> None:
        units = len(read(ADVISOR_RULES).encode("utf-16-le")) // 2
        self.assertLessEqual(units, 8000)

    def test_consult_skill_launches_both_runners(self) -> None:
        text = read(CONSULT_SKILL)
        missing = [
            phrase
            for phrase in (
                "/cross-model-advisor:consult",
                f'`subagent_type: "{CODEX_RUNNER}"`',
                f'`subagent_type: "{FABLE_RUNNER_TYPE}"`',
                f"`{USAGE_COMMAND_NAME}`",
                '`model: "fable"`',
            )
            if phrase not in text
        ]
        self.assertEqual([], missing, f"consult skill に無い記述: {missing}")

    def test_review_cadence_checkpoint_consults_both(self) -> None:
        text = read(REVIEW_CADENCE_RULES)
        missing = [
            phrase
            for phrase in (f"`{CODEX_RUNNER}`", f"`{FABLE_RUNNER_TYPE}`", "attestation")
            if phrase not in text
        ]
        self.assertEqual([], missing, f"review-cadence-rules.md に無い記述: {missing}")


if __name__ == "__main__":
    unittest.main()

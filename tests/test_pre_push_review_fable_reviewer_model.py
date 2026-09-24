"""pre-push-review: reviewer の起動 model を Fable 週次枠の使用率で決める契約テスト。

背景 (spec-first Phase A):
- pre-push-review の code-reviewer / security-reviewer を、Fable 週次枠に余裕がある間は
  `model: "fable"` で、使用率が閾値を超えた・確認できない場合は `model: "opus"` で起動する。
  agent 定義 frontmatter の model は hook から見えず、frontmatter を fable にすると model
  未指定の起動が使用率判定を経ずに Fable で走るため、frontmatter は opus のまま据え置く。
- 判定は plugin 同梱の `bin/pre-push-review-reviewer-model` (plugin 有効時に Bash の PATH に
  載る) が行い、`/pre-push-review:review` は起動前にこれを 1 回実行して出力の model を使う。
  block-pre-push.sh の deny 文も同じ判定処理で起動 model を案内する。
- 出力契約: 1 行目が起動 model (`fable` / `opus`)、2 行目が判定理由 (超過時は使用率と閾値、
  不明時は使用率を確認できない旨)。
- 使用率判定は agent-discipline の block-fable-subagent.sh と同じ仕様 (cache は
  `${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json`、閾値は env
  `FABLE_WEEKLY_MAX_PERCENT` の 0〜100 の整数で既定 80、`percent <= 閾値` で利用可、
  cache の欠落・破損・stale (1800 秒超) 等は不明 = opus)。cache は書き込まない。

hook / コマンドを実行するテストは ``HOME`` / ``XDG_CACHE_HOME`` を一時ディレクトリへ向け、
親プロセスの env を継承しない。

subTest は使わない: 判定表の行ごとの違反をリストに集約して 1 テスト = 1 判定に保つ
(tests/test_agent_discipline_fable_weekly_gate.py と同じ慣行)。
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
PLUGIN = ROOT / "plugins" / "pre-push-review"
MODEL_COMMAND = PLUGIN / "bin" / "pre-push-review-reviewer-model"
BLOCK_PRE_PUSH = PLUGIN / "hooks" / "scripts" / "block-pre-push.sh"
REVIEW_COMMAND = PLUGIN / "commands" / "review.md"
AGENTS = {
    "code-reviewer": PLUGIN / "agents" / "code-reviewer.md",
    "security-reviewer": PLUGIN / "agents" / "security-reviewer.md",
}

CACHE_RELATIVE = Path("natsuume-statusline") / "weekly-scoped.json"
RESETS_AT = "2026-09-28T00:00:00Z"

# cache fixture が fetched_at の代わりに持つ「現在時刻から何秒前か」の key (実行直前に解決する)。
AGE_SECONDS_KEY = "__age_seconds__"
UNSET = object()
OMIT = object()

UNKNOWN_REASON = r"確認できない"


def fable_entry(percent: object, *, display_name: str = "Fable") -> dict[str, object]:
    return {"display_name": display_name, "percent": percent, "resets_at": RESETS_AT}


def cache(
    entries: object = UNSET,
    *,
    age_seconds: int = 0,
    fetched_at: object = UNSET,
) -> dict[str, object]:
    """weekly-scoped.json と同形の dict (既定は Fable 10% の新鮮な cache)。"""
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


def row(
    label: str,
    *,
    model: str,
    cache_body: object = UNSET,
    cache_raw: str | None = None,
    cache_kind: str = "file",
    threshold: object = UNSET,
    broken_date: bool = False,
    reason: tuple[str, ...] = (),
) -> dict[str, object]:
    """判定表の 1 行。``model`` は期待する 1 行目、``reason`` は 2 行目に一致すべき正規表現。"""
    return {
        "label": label,
        "model": model,
        "cache_body": cache_body,
        "cache_raw": cache_raw,
        "cache_kind": cache_kind,
        "threshold": threshold,
        "broken_date": broken_date,
        "reason": reason,
    }


DECISION_TABLE = (
    # --- 利用可 → fable ---
    row("available/usage-10", model="fable"),
    row("available/exactly-at-threshold", model="fable", cache_body=cache([fable_entry(80)])),
    row("available/decimal-below", model="fable", cache_body=cache([fable_entry(79.9)])),
    row(
        "available/display-name-case-insensitive",
        model="fable",
        cache_body=cache([fable_entry(10, display_name="Weekly FABLE usage")]),
    ),
    row(
        "available/non-fable-entries-ignored",
        model="fable",
        cache_body=cache(
            [{"display_name": "Opus", "percent": 99, "resets_at": RESETS_AT}, fable_entry(10)]
        ),
    ),
    row("available/future-fetched-at", model="fable", cache_body=cache(age_seconds=-600)),
    row("available/fetched-at-just-fresh", model="fable", cache_body=cache(age_seconds=1790)),
    # --- 超過 → opus (使用率と閾値を併記) ---
    row(
        "over/81",
        model="opus",
        cache_body=cache([fable_entry(81)]),
        reason=(r"81", r"80"),
    ),
    row(
        "over/decimal",
        model="opus",
        cache_body=cache([fable_entry(80.5)]),
        reason=(r"80\.5", r"80"),
    ),
    row(
        "over/max-of-multiple",
        model="opus",
        cache_body=cache([fable_entry(10), fable_entry(90)]),
        reason=(r"90",),
    ),
    # --- 閾値 env ---
    row(
        "threshold/env-50/over",
        model="opus",
        threshold="50",
        cache_body=cache([fable_entry(60)]),
        reason=(r"60", r"50"),
    ),
    row("threshold/env-50/at", model="fable", threshold="50", cache_body=cache([fable_entry(50)])),
    row(
        "threshold/env-padded",
        model="opus",
        threshold=" 50 ",
        cache_body=cache([fable_entry(60)]),
        reason=(r"50",),
    ),
    row("threshold/env-0/at-zero", model="fable", threshold="0", cache_body=cache([fable_entry(0)])),
    row(
        "threshold/env-100/full",
        model="fable",
        threshold="100",
        cache_body=cache([fable_entry(100)]),
    ),
    *(
        row(
            f"threshold/invalid-{name}/falls-back-to-80/over",
            model="opus",
            threshold=value,
            cache_body=cache([fable_entry(81)]),
            reason=(r"80",),
        )
        for name, value in (
            ("empty", ""),
            ("101", "101"),
            ("negative", "-1"),
            ("decimal", "50.5"),
            ("word", "abc"),
        )
    ),
    *(
        row(
            f"threshold/invalid-{name}/falls-back-to-80/under",
            model="fable",
            threshold=value,
            cache_body=cache([fable_entry(79)]),
        )
        for name, value in (("101", "101"), ("negative", "-1"), ("word", "abc"))
    ),
    # --- 不明 → opus (使用率を確認できない旨を併記) ---
    row("unknown/cache-missing", model="opus", cache_kind="missing", reason=(UNKNOWN_REASON,)),
    row("unknown/cache-symlink", model="opus", cache_kind="symlink", reason=(UNKNOWN_REASON,)),
    row("unknown/cache-directory", model="opus", cache_kind="directory", reason=(UNKNOWN_REASON,)),
    row("unknown/not-json", model="opus", cache_raw="{not json", reason=(UNKNOWN_REASON,)),
    row("unknown/empty-file", model="opus", cache_raw="", reason=(UNKNOWN_REASON,)),
    row(
        "unknown/two-json-documents",
        model="opus",
        cache_raw=json.dumps(cache(fetched_at=1)) + "\n" + json.dumps(cache(fetched_at=1)),
        reason=(UNKNOWN_REASON,),
    ),
    row("unknown/top-level-array", model="opus", cache_raw="[]", reason=(UNKNOWN_REASON,)),
    row(
        "unknown/fetched-at-missing",
        model="opus",
        cache_body=cache(fetched_at=OMIT),
        reason=(UNKNOWN_REASON,),
    ),
    row(
        "unknown/fetched-at-string",
        model="opus",
        cache_body=cache(fetched_at="1700000000"),
        reason=(UNKNOWN_REASON,),
    ),
    row("unknown/stale", model="opus", cache_body=cache(age_seconds=1900), reason=(UNKNOWN_REASON,)),
    row(
        "unknown/weekly-scoped-missing",
        model="opus",
        cache_body=cache(OMIT),
        reason=(UNKNOWN_REASON,),
    ),
    row(
        "unknown/weekly-scoped-not-array",
        model="opus",
        cache_body=cache({"display_name": "Fable", "percent": 10}),
        reason=(UNKNOWN_REASON,),
    ),
    row("unknown/weekly-scoped-empty", model="opus", cache_body=cache([]), reason=(UNKNOWN_REASON,)),
    row(
        "unknown/no-fable-entry",
        model="opus",
        cache_body=cache([{"display_name": "Opus", "percent": 10}]),
        reason=(UNKNOWN_REASON,),
    ),
    row(
        "unknown/percent-non-numeric",
        model="opus",
        cache_body=cache([fable_entry("10"), fable_entry(None)]),
        reason=(UNKNOWN_REASON,),
    ),
    row("unknown/current-time-unavailable", model="opus", broken_date=True, reason=(UNKNOWN_REASON,)),
)


class IsolatedEnvMixin:
    """HOME / XDG_CACHE_HOME を一時ディレクトリへ向け、使用率 cache を用意する共通処理。"""

    def isolated_env(self, temp: Path) -> dict[str, str]:
        home = temp / "home"
        cache_home = temp / "cache"
        for directory in (home, cache_home):
            directory.mkdir(exist_ok=True)
        return {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "XDG_CACHE_HOME": str(cache_home),
        }

    def prepare_cache(self, temp: Path, env: dict[str, str], case: dict[str, object]) -> Path:
        cache_path = Path(env["XDG_CACHE_HOME"]) / CACHE_RELATIVE
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        body = resolve_fetched_at(cache() if case["cache_body"] is UNSET else case["cache_body"])
        text = case["cache_raw"] if case["cache_raw"] is not None else json.dumps(body)
        kind = case["cache_kind"]
        if kind == "file":
            cache_path.write_text(str(text), encoding="utf-8")
        elif kind == "symlink":
            target = temp / "real-weekly-scoped.json"
            target.write_text(str(text), encoding="utf-8")
            cache_path.symlink_to(target)
        elif kind == "directory":
            cache_path.mkdir()
        if case["threshold"] is not UNSET:
            env["FABLE_WEEKLY_MAX_PERCENT"] = str(case["threshold"])
        if case["broken_date"]:
            shim_dir = temp / "shim"
            shim_dir.mkdir()
            shim = shim_dir / "date"
            shim.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            shim.chmod(0o755)
            env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        return cache_path

    @staticmethod
    def snapshot(directory: Path) -> list[tuple[str, str]]:
        entries = []
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink():
                entries.append((entry.name, f"symlink:{os.readlink(entry)}"))
            elif entry.is_dir():
                entries.append((entry.name, "directory"))
            else:
                entries.append((entry.name, entry.read_text(encoding="utf-8")))
        return entries


class ReviewerModelCommandFileTest(unittest.TestCase):
    """判定コマンドが plugin の bin/ に実行可能な bash script として存在する。"""

    def test_command_is_an_executable_bash_script(self) -> None:
        self.assertTrue(MODEL_COMMAND.is_file(), f"{MODEL_COMMAND} が無い")
        self.assertTrue(os.access(MODEL_COMMAND, os.X_OK), f"{MODEL_COMMAND} が実行可能でない")
        first_line = MODEL_COMMAND.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual("#!/bin/bash", first_line)


@unittest.skipUnless(shutil.which("jq"), "reviewer model command requires jq")
class ReviewerModelCommandDecisionTableTest(IsolatedEnvMixin, unittest.TestCase):
    """判定コマンドの出力 (1 行目 = model、2 行目 = 理由) を使用率 cache の状態で固定する。"""

    def run_case(self, case: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            env = self.isolated_env(temp)
            cache_path = self.prepare_cache(temp, env, case)
            before = self.snapshot(cache_path.parent)
            result = subprocess.run(
                ["/bin/bash", str(MODEL_COMMAND)],
                cwd=temp,
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            after = self.snapshot(cache_path.parent)
            return result, ("" if before == after else f"before={before} after={after}")

    def violations_of(self, case: dict[str, object]) -> list[str]:
        label = str(case["label"])
        if not MODEL_COMMAND.is_file():
            return [f"{label}: {MODEL_COMMAND} が無い"]
        result, cache_change = self.run_case(case)
        problems = []
        if result.returncode != 0:
            problems.append(f"{label}: exit {result.returncode} ({result.stderr.strip()})")
        if cache_change:
            problems.append(f"{label}: cache が書き換えられた ({cache_change})")
        lines = result.stdout.splitlines()
        if len(lines) != 2:
            return [*problems, f"{label}: 出力が 2 行でない ({result.stdout!r})"]
        if lines[0] != case["model"]:
            problems.append(f"{label}: model が {lines[0]!r} (期待 {case['model']!r})")
        for pattern in case["reason"]:  # type: ignore[union-attr]
            if not re.search(str(pattern), lines[1]):
                problems.append(f"{label}: 理由に {pattern!r} が無い ({lines[1]})")
        return problems

    def test_decision_table(self) -> None:
        violations = [problem for case in DECISION_TABLE for problem in self.violations_of(case)]
        self.assertEqual([], violations, "\n".join(violations))


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockPrePushReviewerModelTest(IsolatedEnvMixin, unittest.TestCase):
    """block-pre-push.sh の deny 文が判定結果の model で reviewer の起動を案内する。"""

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def create_repo(self, temporary: Path) -> Path:
        origin = temporary / "origin.git"
        repo = temporary / "repo"
        self.git(temporary, "init", "--bare", str(origin))
        self.git(temporary, "init", str(repo))
        self.git(repo, "config", "user.name", "Marketplace Test")
        self.git(repo, "config", "user.email", "marketplace@example.invalid")
        (repo / "example.txt").write_text("base\n", encoding="utf-8")
        self.git(repo, "add", "example.txt")
        self.git(repo, "commit", "-m", "base")
        self.git(repo, "branch", "-M", "master")
        self.git(repo, "remote", "add", "origin", str(origin))
        self.git(repo, "push", "-u", "origin", "master")
        self.git(repo, "remote", "set-head", "origin", "master")
        self.git(repo, "switch", "-c", "feature")
        (repo / "example.txt").write_text("change\n", encoding="utf-8")
        self.git(repo, "add", "example.txt")
        self.git(repo, "commit", "-m", "change")
        return repo

    def deny_reason(self, case: dict[str, object]) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repo = self.create_repo(temp)
            env = self.isolated_env(temp)
            self.prepare_cache(temp, env, case)
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin HEAD"},
            }
            result = subprocess.run(
                ["bash", str(BLOCK_PRE_PUSH)],
                cwd=repo,
                env=env,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            output = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual("deny", output["permissionDecision"])
            return str(output["permissionDecisionReason"])

    def launch_lines(self, reason: str, model: str) -> list[str]:
        return [
            f'subagent_type="pre-push-review:{name}", model="{model}"'
            for name in ("code-reviewer", "security-reviewer")
            if f'subagent_type="pre-push-review:{name}", model="{model}"' not in reason
        ]

    def test_available_usage_guides_fable_with_opus_fallback(self) -> None:
        reason = self.deny_reason(row("available", model="fable"))
        self.assertEqual([], self.launch_lines(reason, "fable"), reason)
        # agent-discipline の hook に deny された場合は同じ reviewer を opus で再起動する
        self.assertRegex(reason, r'deny[^。]*model="opus"[^。]*再起動')

    def test_over_threshold_guides_opus_with_usage_and_threshold(self) -> None:
        reason = self.deny_reason(row("over", model="opus", cache_body=cache([fable_entry(81)])))
        self.assertEqual([], self.launch_lines(reason, "opus"), reason)
        self.assertNotIn('model="fable"', reason)
        self.assertRegex(reason, r"81")
        self.assertRegex(reason, r"80")

    def test_unknown_usage_guides_opus_and_says_it_cannot_be_confirmed(self) -> None:
        reason = self.deny_reason(row("unknown", model="opus", cache_kind="missing"))
        self.assertEqual([], self.launch_lines(reason, "opus"), reason)
        self.assertNotIn('model="fable"', reason)
        self.assertRegex(reason, UNKNOWN_REASON)


class ReviewCommandContractTest(unittest.TestCase):
    """`/pre-push-review:review` が判定コマンドの出力で起動 model を決める。"""

    def test_review_command_runs_the_model_command_before_launch(self) -> None:
        body = REVIEW_COMMAND.read_text(encoding="utf-8")
        self.assertIn("pre-push-review-reviewer-model", body)
        # 判定コマンドの出力 (1 行目) を 2 reviewer の起動 model に使う
        for name in ("code-reviewer", "security-reviewer"):
            self.assertRegex(
                body,
                rf'subagent_type: "pre-push-review:{name}"`、`model: "{{{{REVIEWER_MODEL}}}}"`',
            )

    def test_review_command_guides_opus_fallback(self) -> None:
        body = REVIEW_COMMAND.read_text(encoding="utf-8")
        # コマンドが実行できない場合は opus
        self.assertRegex(body, r"実行できない[^。]*`opus`")
        # fable で起動して agent-discipline の hook に deny された場合は opus で再起動
        self.assertRegex(body, r'deny[^。]*`model: "opus"`[^。]*再起動')


class AgentFrontmatterModelTest(unittest.TestCase):
    """reviewer 2 体の agent 定義 frontmatter の model は opus のまま据え置く。"""

    def test_frontmatter_model_stays_opus(self) -> None:
        wrong = [
            name
            for name, path in AGENTS.items()
            if not re.search(r"(?m)^model: opus$", path.read_text(encoding="utf-8").split("---")[1])
        ]
        self.assertEqual([], wrong, f"frontmatter の model が opus でない: {wrong}")


if __name__ == "__main__":
    unittest.main()

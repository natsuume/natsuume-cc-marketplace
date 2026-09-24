"""agent-discipline: `model: "fable"` 明示を週次枠判定だけで許可する契約テスト。

背景 (spec-first Phase A):
- メインセッションは Opus 5.5 で、Fable は cross-model-advisor の fable-advisor-runner と
  pre-merge-cross-review の fable-reviewer を `model: "fable"` の明示で起動するときだけ使う。
  Fable をメインセッションで使う運用は無い。
  block-fable-subagent.sh は許可 agent の一覧を持たず、「Fable 週次枠の使用率」だけで fable
  明示を判定する (用途は規律 = prompt で縛る)。session model state と pending マーカーは
  判定に使わない。
- 判定表 (fable 明示の行): session model state (fable を含む) / pending マーカーの有無に
  依らず、使用率判定で利用可なら allow、利用不可 (閾値超過・使用率不明) なら deny
- 使用率判定: ``${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json``
  の Fable entry (display_name が大文字小文字を無視して fable を含み percent が数値) の
  最大 percent を閾値 (env ``FABLE_WEEKLY_MAX_PERCENT``、0〜100 の整数、既定 80) と比べ、
  ``percent <= 閾値`` で利用可。cache が読めない・壊れている・古い (1800 秒超) 等は
  すべて利用不可 (使用率不明)。``fetched_at`` が未来時刻でも stale とみなさない。
- deny 理由では、fable-advisor-runner / fable-reviewer は再起動せずスキップし、それ以外の
  委任では非 Fable の model (`model: "opus"`) を明示するよう案内する。Sonnet / Haiku の明示は
  案内しない。pre-push-review の reviewer は常に Opus で起動するため、deny 理由で reviewer の
  再起動を案内しない。
- サブエージェント内 (入力に agent_id がある) からの model 未指定 (inherit を含む)・fork の
  起動は deny し、model の明示を求める (nested guard)。継承先が起動元サブエージェントの
  モデルになり、週次枠判定を通った Fable サブエージェントの子が判定なしで Fable を継承
  しうるため。CLAUDE_CODE_SUBAGENT_MODEL が非空なら子の実効モデルは env で決まるため
  env で判定する。
- hook は cache を書き込まない。

hook を実行するテストは ``TMPDIR`` / ``HOME`` / ``XDG_CACHE_HOME`` を一時ディレクトリへ
向け、親プロセスの env を継承しない (tests/test_agent_discipline_model_resolution.py の
HookSubprocessTestBase と同じ隔離方式)。

subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が SUBFAILED として
分離報告される一方、親テストノード自体は PASSED と表示され判定が曖昧になるため、
判定表の行ごとの違反をリストに集約して 1 テスト = 1 判定に保つ。
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
PLUGIN = ROOT / "plugins" / "agent-discipline"
BLOCK_FABLE = PLUGIN / "hooks" / "scripts" / "block-fable-subagent.sh"
README = PLUGIN / "README.md"

STATE_DIR_NAME = "agent-discipline-state"
CACHE_RELATIVE = Path("natsuume-statusline") / "weekly-scoped.json"
SESSION_ID = "fable-weekly-gate"

FABLE_SESSION = "claude-fable-5-1"
OPUS_SESSION = "claude-opus-5-5"
SONNET_SESSION = "claude-sonnet-5"

RESETS_AT = "2026-09-28T00:00:00Z"

# deny 理由に求めるキーワード (正規表現)。
EXPLICIT_NON_FABLE_MODEL = r'model: "opus"'
SKIP_ADVISOR = r"fable-advisor-runner[^。]*fable-reviewer[^。]*スキップ"
NAMES_PRODUCER = r"natsuume-statusline"
NESTED_EXPLICIT_MODEL = r'model: "opus"'
# deny 理由に含めない記述 (pre-push-review の reviewer は Fable で起動しない)。
REVIEWER_RELAUNCH_GUIDE = "pre-push-review"
# deny 理由に含めない、ワーカーを Sonnet / Haiku へ下げる案内。
DOWNGRADE_GUIDES = ("model に sonnet / opus", "機械的作業なら haiku")

# UNSET: 引数を「与えなかった」ことを表す番兵 / OMIT: cache の key 自体を書かない番兵。
UNSET = object()
OMIT = object()

# cache fixture が fetched_at の代わりに持つ「現在時刻から何秒前か」の key (hook には渡さない)。
AGE_SECONDS_KEY = "__age_seconds__"


def fable_entry(percent: object, *, display_name: str = "Fable") -> dict[str, object]:
    """weekly_scoped[] の 1 entry (natsuume-statusline が書く形)。"""
    return {"display_name": display_name, "percent": percent, "resets_at": RESETS_AT}


def cache(
    entries: object = UNSET,
    *,
    age_seconds: int = 0,
    fetched_at: object = UNSET,
) -> dict[str, object]:
    """weekly-scoped.json と同形の dict。

    ``entries`` が UNSET なら Fable 10% の 1 entry、OMIT なら ``weekly_scoped`` key を
    書かない。``fetched_at`` に値を与えるとその値を、OMIT を与えると key 欠落を再現する。
    """
    body: dict[str, object] = {"consecutive_failures": 0, "next_attempt_at": 0}
    if fetched_at is UNSET:
        # 判定表はモジュール読み込み時に組み立てるため、現在時刻からの相対値だけを持ち、
        # fetched_at は各ケースの実行直前に resolve_fetched_at で解決する。
        body[AGE_SECONDS_KEY] = age_seconds
    elif fetched_at is not OMIT:
        body["fetched_at"] = fetched_at
    if entries is UNSET:
        body["weekly_scoped"] = [fable_entry(10)]
    elif entries is not OMIT:
        body["weekly_scoped"] = entries
    return body


def resolve_fetched_at(body: object) -> object:
    """``cache`` が相対値で持つ fetched_at を、呼び出し時点の現在時刻から解決した dict を返す。"""
    if not isinstance(body, dict) or AGE_SECONDS_KEY not in body:
        return body
    resolved = {key: value for key, value in body.items() if key != AGE_SECONDS_KEY}
    resolved["fetched_at"] = int(time.time()) - int(body[AGE_SECONDS_KEY])
    return resolved


def row(
    label: str,
    *,
    expect: str,
    model: object = "fable",
    session_state: str | None = OPUS_SESSION,
    pending: bool = False,
    cache_body: object = UNSET,
    cache_raw: str | None = None,
    cache_kind: str = "file",
    threshold: object = UNSET,
    env: object = UNSET,
    force: object = UNSET,
    broken_date: bool = False,
    agent_id: object = UNSET,
    subagent_type: str = "cross-model-advisor:fable-advisor-runner",
    keywords: tuple[str, ...] = (),
) -> dict[str, object]:
    """判定表の 1 行。``expect`` は ``"deny"`` / ``"allow"``。

    ``cache_body`` が UNSET なら Fable 10% の新鮮な cache を置く。``cache_raw`` を与えると
    その文字列をそのまま書く。``cache_kind`` は ``"file"`` / ``"missing"`` /
    ``"symlink"`` (正常な cache への symlink) / ``"directory"``。``threshold`` は env
    ``FABLE_WEEKLY_MAX_PERCENT``、``env`` は ``CLAUDE_CODE_SUBAGENT_MODEL``、``force`` は
    ``CLAUDE_CODE_SUBAGENT_MODEL_FORCE``。``broken_date`` は PATH 先頭に失敗する ``date``
    を置き、現在時刻を取得できない状態を再現する。``agent_id`` を与えると hook 入力の
    ``agent_id`` (サブエージェント内からの起動) として渡す。
    """
    return {
        "label": label,
        "expect": expect,
        "model": model,
        "session_state": session_state,
        "pending": pending,
        "cache_body": cache_body,
        "cache_raw": cache_raw,
        "cache_kind": cache_kind,
        "threshold": threshold,
        "env": env,
        "force": force,
        "broken_date": broken_date,
        "agent_id": agent_id,
        "subagent_type": subagent_type,
        "keywords": keywords,
    }


USAGE_DENY = (EXPLICIT_NON_FABLE_MODEL, SKIP_ADVISOR)
UNKNOWN_DENY = (EXPLICIT_NON_FABLE_MODEL, SKIP_ADVISOR, NAMES_PRODUCER)

DECISION_TABLE = (
    # --- session model state / pending は判定に使わない: 使用率だけで決まる ---
    row(
        "state-independent/fable-session/usage-ok",
        session_state=FABLE_SESSION,
        expect="allow",
    ),
    row(
        "state-independent/fable-session/full-id/usage-ok",
        model="claude-fable-5-1",
        session_state=FABLE_SESSION,
        expect="allow",
    ),
    row(
        "state-independent/fable-session/over-threshold",
        session_state=FABLE_SESSION,
        cache_body=cache([fable_entry(81)]),
        expect="deny",
        keywords=USAGE_DENY,
    ),
    row(
        "state-independent/pending/usage-ok",
        session_state=None,
        pending=True,
        expect="allow",
    ),
    row(
        "state-independent/pending-with-stale-state/usage-ok",
        pending=True,
        expect="allow",
    ),
    row(
        "state-independent/no-information/usage-ok",
        session_state=None,
        expect="allow",
    ),
    row(
        "state-independent/no-information/cache-missing",
        session_state=None,
        cache_kind="missing",
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    # --- 使用率に余裕 → allow ---
    row("usage/opus-session/usage-ok", expect="allow"),
    row("usage/sonnet-session/usage-ok", session_state=SONNET_SESSION, expect="allow"),
    row("usage/full-fable-id", model="claude-fable-5-1", expect="allow"),
    row("usage/uppercase-and-padded-model", model="  FABLE  ", expect="allow"),
    row("usage/exactly-at-threshold", cache_body=cache([fable_entry(80)]), expect="allow"),
    row(
        "usage/decimal-below-threshold",
        cache_body=cache([fable_entry(79.9)]),
        expect="allow",
    ),
    row(
        "usage/display-name-case-insensitive",
        cache_body=cache([fable_entry(10, display_name="Weekly FABLE usage")]),
        expect="allow",
    ),
    row(
        "usage/non-fable-entries-ignored",
        cache_body=cache(
            [
                {"display_name": "Opus", "percent": 99, "resets_at": RESETS_AT},
                fable_entry(10),
            ]
        ),
        expect="allow",
    ),
    row(
        "usage/future-fetched-at-is-not-stale",
        cache_body=cache(age_seconds=-600),
        expect="allow",
    ),
    row("usage/fetched-at-just-fresh", cache_body=cache(age_seconds=1790), expect="allow"),
    # --- 使用率超過 → deny (使用率・閾値・reset 時刻を理由に含める) ---
    row(
        "usage/over-threshold",
        cache_body=cache([fable_entry(81)]),
        expect="deny",
        keywords=(r"81", r"80", RESETS_AT, *USAGE_DENY),
    ),
    row(
        "usage/decimal-over-threshold",
        cache_body=cache([fable_entry(80.5)]),
        expect="deny",
        keywords=(r"80\.5", *USAGE_DENY),
    ),
    row(
        "usage/max-of-multiple-fable-entries",
        cache_body=cache([fable_entry(10), fable_entry(90)]),
        expect="deny",
        keywords=(r"90", *USAGE_DENY),
    ),
    # --- 閾値 env ---
    row(
        "threshold/env-50/over",
        threshold="50",
        cache_body=cache([fable_entry(60)]),
        expect="deny",
        keywords=(r"50", *USAGE_DENY),
    ),
    row(
        "threshold/env-50/at",
        threshold="50",
        cache_body=cache([fable_entry(50)]),
        expect="allow",
    ),
    row(
        "threshold/env-padded",
        threshold=" 50 ",
        cache_body=cache([fable_entry(60)]),
        expect="deny",
        keywords=USAGE_DENY,
    ),
    row(
        "threshold/env-0/at-zero",
        threshold="0",
        cache_body=cache([fable_entry(0)]),
        expect="allow",
    ),
    row(
        "threshold/env-100/full",
        threshold="100",
        cache_body=cache([fable_entry(100)]),
        expect="allow",
    ),
    *(
        row(
            f"threshold/invalid-{name}/falls-back-to-80/over",
            threshold=value,
            cache_body=cache([fable_entry(81)]),
            expect="deny",
            keywords=USAGE_DENY,
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
            threshold=value,
            cache_body=cache([fable_entry(79)]),
            expect="allow",
        )
        for name, value in (("101", "101"), ("negative", "-1"), ("word", "abc"))
    ),
    # --- 使用率不明 → deny (fail-closed) ---
    row("unknown/cache-missing", cache_kind="missing", expect="deny", keywords=UNKNOWN_DENY),
    row("unknown/cache-symlink", cache_kind="symlink", expect="deny", keywords=UNKNOWN_DENY),
    row(
        "unknown/cache-directory",
        cache_kind="directory",
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row("unknown/not-json", cache_raw="{not json", expect="deny", keywords=UNKNOWN_DENY),
    row("unknown/empty-file", cache_raw="", expect="deny", keywords=UNKNOWN_DENY),
    row(
        "unknown/two-json-documents",
        cache_raw=json.dumps(cache(fetched_at=1)) + "\n" + json.dumps(cache(fetched_at=1)),
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/top-level-array",
        cache_raw="[]",
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/fetched-at-missing",
        cache_body=cache(fetched_at=OMIT),
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/fetched-at-string",
        cache_body=cache(fetched_at="1700000000"),
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/stale",
        cache_body=cache(age_seconds=1900),
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/weekly-scoped-missing",
        cache_body=cache(OMIT),
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/weekly-scoped-not-array",
        cache_body=cache({"display_name": "Fable", "percent": 10}),
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/weekly-scoped-empty",
        cache_body=cache([]),
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/no-fable-entry",
        cache_body=cache([{"display_name": "Opus", "percent": 10}]),
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/percent-non-numeric",
        cache_body=cache([fable_entry("10"), fable_entry(None)]),
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    row(
        "unknown/current-time-unavailable",
        broken_date=True,
        expect="deny",
        keywords=UNKNOWN_DENY,
    ),
    # --- nested guard: サブエージェント内 (agent_id あり) からの継承・fork は deny ---
    # 継承先は起動元サブエージェントのモデルで、週次枠判定を通った Fable サブエージェントの
    # 子が判定なしで Fable を継承しうるため、model の明示を求める。
    row(
        "nested/model-unspecified/opus-session",
        model=UNSET,
        agent_id="parent-agent",
        subagent_type="general-purpose",
        expect="deny",
        keywords=(NESTED_EXPLICIT_MODEL,),
    ),
    row(
        "nested/model-inherit/opus-session",
        model="inherit",
        agent_id="parent-agent",
        subagent_type="general-purpose",
        expect="deny",
        keywords=(NESTED_EXPLICIT_MODEL,),
    ),
    row(
        "nested/fork/opus-session",
        model=UNSET,
        agent_id="parent-agent",
        subagent_type="fork",
        expect="deny",
        keywords=(NESTED_EXPLICIT_MODEL,),
    ),
    row(
        "nested/fork-with-explicit-model/opus-session",
        model="sonnet",
        agent_id="parent-agent",
        subagent_type="fork",
        expect="deny",
        keywords=(NESTED_EXPLICIT_MODEL,),
    ),
    row(
        "nested/model-sonnet",
        model="sonnet",
        agent_id="parent-agent",
        subagent_type="general-purpose",
        cache_kind="missing",
        expect="allow",
    ),
    row(
        "nested/model-fable/usage-ok",
        agent_id="parent-agent",
        expect="allow",
    ),
    row(
        "nested/model-fable/over-threshold",
        agent_id="parent-agent",
        cache_body=cache([fable_entry(81)]),
        expect="deny",
        keywords=USAGE_DENY,
    ),
    row(
        "nested/model-unspecified/env-sonnet",
        model=UNSET,
        env="sonnet",
        agent_id="parent-agent",
        subagent_type="general-purpose",
        expect="allow",
    ),
    row(
        "nested/empty-agent-id-is-main-session",
        model=UNSET,
        agent_id="",
        subagent_type="general-purpose",
        expect="allow",
    ),
    # --- fable 明示以外の経路: 継承経路の env fable、FORCE 有効時、非 fable 明示 ---
    row(
        "other-path/model-unspecified/env-fable/usage-ok",
        model=UNSET,
        env="fable",
        expect="deny",
        keywords=(EXPLICIT_NON_FABLE_MODEL,),
    ),
    row(
        "other-path/model-unspecified/opus-session",
        model=UNSET,
        cache_kind="missing",
        expect="allow",
    ),
    row(
        "other-path/force-on/env-sonnet/model-fable/cache-missing",
        force="1",
        env="sonnet",
        cache_kind="missing",
        expect="allow",
    ),
    row(
        "other-path/force-on/env-fable/model-fable/usage-ok",
        force="1",
        env="fable",
        expect="deny",
    ),
    row(
        "other-path/model-sonnet/cache-missing",
        model="sonnet",
        cache_kind="missing",
        expect="allow",
    ),
)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class FableWeeklyGateDecisionTableTest(unittest.TestCase):
    """block-fable-subagent.sh の fable 明示の判定を使用率 cache で固定する (メインモデルに依らない)。"""

    def isolated_env(self, temp: Path) -> dict[str, str]:
        home = temp / "home"
        tmpdir = temp / "tmp"
        cache_home = temp / "cache"
        for directory in (home, tmpdir, cache_home):
            directory.mkdir(exist_ok=True)
        return {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "TMPDIR": str(tmpdir),
            "XDG_CACHE_HOME": str(cache_home),
        }

    def prepare(self, temp: Path, env: dict[str, str], case: dict[str, object]) -> Path:
        """session state / pending / cache / date shim を用意し、cache の path を返す。"""
        state_dir = Path(env["TMPDIR"]) / STATE_DIR_NAME
        state_dir.mkdir(parents=True, exist_ok=True)
        if case["session_state"] is not None:
            (state_dir / f"model-{SESSION_ID}").write_text(
                str(case["session_state"]), encoding="utf-8"
            )
        if case["pending"]:
            (state_dir / f"pending-model-{SESSION_ID}").write_text("", encoding="utf-8")

        cache_path = Path(env["XDG_CACHE_HOME"]) / CACHE_RELATIVE
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        kind = case["cache_kind"]
        body = resolve_fetched_at(cache() if case["cache_body"] is UNSET else case["cache_body"])
        text = case["cache_raw"] if case["cache_raw"] is not None else json.dumps(body)
        if kind == "file":
            cache_path.write_text(str(text), encoding="utf-8")
        elif kind == "symlink":
            target = temp / "real-weekly-scoped.json"
            target.write_text(str(text), encoding="utf-8")
            cache_path.symlink_to(target)
        elif kind == "directory":
            cache_path.mkdir()

        if case["broken_date"]:
            shim_dir = temp / "shim"
            shim_dir.mkdir()
            shim = shim_dir / "date"
            shim.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            shim.chmod(0o755)
            env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        return cache_path

    def run_case(self, case: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], str]:
        """判定表の 1 行を実行し、(結果, 実行後の cache ディレクトリの状態) を返す。"""
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            env = self.isolated_env(temp)
            if case["threshold"] is not UNSET:
                env["FABLE_WEEKLY_MAX_PERCENT"] = str(case["threshold"])
            if case["env"] is not UNSET:
                env["CLAUDE_CODE_SUBAGENT_MODEL"] = str(case["env"])
            if case["force"] is not UNSET:
                env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"] = str(case["force"])
            cache_path = self.prepare(temp, env, case)
            before = self.snapshot(cache_path.parent)

            tool_input: dict[str, object] = {"subagent_type": case["subagent_type"]}
            if case["model"] is not UNSET:
                tool_input["model"] = case["model"]
            payload: dict[str, object] = {
                "hook_event_name": "PreToolUse",
                "session_id": SESSION_ID,
                "tool_input": tool_input,
            }
            if case["agent_id"] is not UNSET:
                payload["agent_id"] = case["agent_id"]
            result = subprocess.run(
                ["/bin/bash", str(BLOCK_FABLE)],
                cwd=ROOT,
                env=env,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            after = self.snapshot(cache_path.parent)
            cache_change = "" if before == after else f"before={before} after={after}"
            return result, cache_change

    @staticmethod
    def snapshot(directory: Path) -> list[tuple[str, str]]:
        """ディレクトリ直下の各 entry の (名前, 種別と内容) の一覧。"""
        entries = []
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink():
                entries.append((entry.name, f"symlink:{os.readlink(entry)}"))
            elif entry.is_dir():
                entries.append((entry.name, "directory"))
            else:
                entries.append((entry.name, entry.read_text(encoding="utf-8")))
        return entries

    def violations_of(self, case: dict[str, object]) -> list[str]:
        label = str(case["label"])
        result, cache_change = self.run_case(case)
        problems = []
        if result.returncode != 0:
            problems.append(f"{label}: exit {result.returncode} ({result.stderr.strip()})")
        if cache_change:
            problems.append(f"{label}: cache が書き換えられた ({cache_change})")
        if case["expect"] == "allow":
            if result.stdout.strip():
                problems.append(f"{label}: allow のはずが出力あり ({result.stdout.strip()})")
            return problems
        try:
            output = json.loads(result.stdout)["hookSpecificOutput"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return [*problems, f"{label}: deny JSON が出力されていない ({result.stdout!r})"]
        if output.get("permissionDecision") != "deny":
            problems.append(f"{label}: permissionDecision が deny ではない ({output})")
        reason = str(output.get("permissionDecisionReason", ""))
        for keyword in case["keywords"]:  # type: ignore[union-attr]
            if not re.search(str(keyword), reason):
                problems.append(f"{label}: deny 理由に {keyword!r} が無い ({reason})")
        if REVIEWER_RELAUNCH_GUIDE in reason:
            problems.append(f"{label}: deny 理由が pre-push-review の reviewer に言及する ({reason})")
        for guide in DOWNGRADE_GUIDES:
            if guide in reason:
                problems.append(f"{label}: deny 理由に Sonnet / Haiku への案内 {guide!r} が残る ({reason})")
        return problems

    def test_decision_table(self) -> None:
        violations = [problem for case in DECISION_TABLE for problem in self.violations_of(case)]
        self.assertEqual([], violations, "\n".join(violations))


class ReadmePermissionRuleTest(unittest.TestCase):
    """README が `Agent(model:fable)` の permission rule を推奨せず、設定済みなら削除を案内する。"""

    def json_blocks(self) -> list[object]:
        parsed = []
        for block in re.findall(r"```json\n(.*?)```", README.read_text(encoding="utf-8"), re.DOTALL):
            try:
                parsed.append(json.loads(block))
            except json.JSONDecodeError:
                continue
        return parsed

    def test_settings_example_denies_fork_only(self) -> None:
        denies = [
            block["permissions"]["deny"]
            for block in self.json_blocks()
            if isinstance(block, dict)
            and isinstance(block.get("permissions"), dict)
            and isinstance(block["permissions"].get("deny"), list)
        ]
        self.assertTrue(denies, "permissions.deny を含む json コードブロックが無い")
        self.assertTrue(
            all("Agent(model:fable)" not in deny for deny in denies),
            f"Agent(model:fable) を推奨する設定例が残っている: {denies}",
        )
        self.assertTrue(
            any("Agent(fork)" in deny for deny in denies),
            f"Agent(fork) の設定例が無い: {denies}",
        )

    def test_readme_does_not_list_reviewers_as_fable_usage(self) -> None:
        text = README.read_text(encoding="utf-8")
        present = [
            phrase
            for phrase in ("pre-push-review の reviewer", 'reviewer は `model: "opus"` で再起動')
            if phrase in text
        ]
        self.assertEqual([], present, f"reviewer を Fable の用途とする記述が残っている: {present}")

    def test_readme_asks_existing_users_to_remove_the_fable_rule(self) -> None:
        self.assertIn(
            "`Agent(model:fable)` を設定済みの場合は削除してください",
            README.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()

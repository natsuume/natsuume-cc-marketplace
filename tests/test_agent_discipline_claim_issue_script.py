"""agent-discipline の claim 用スクリプト (`skills/issue-start/scripts/claim-issue.sh`) の挙動テスト。

スクリプトは `rule:issue-claim` の step 1〜5 (早期判定・claim comment の投稿・3 秒待機・
先着判定・ラベル付与) を行い、結果を stdout の 1 行と exit code (0 = 確保 / 1 = 撤退 /
2 = 停止) で返す。

テストは一時ディレクトリに偽の ``gh`` と偽の ``sleep`` を置き、PATH の先頭に足して
スクリプトを実行する。実際の GitHub には一切アクセスしない (subprocess の env は親の env を
継承せずに組み立て、``GH_CONFIG_DIR`` を一時ディレクトリに向ける)。JSON の処理には本物の
``jq`` を使う。

偽の gh は状態ファイル (JSON、環境変数 ``FAKE_GH_STATE``) を読み書きし、次のコマンドだけに
応答する。それ以外の呼び出しは ``unknown`` として記録して exit 64 で終わる。

- ``gh api 'repos/{owner}/{repo}/issues/<N>'`` (kind ``get_issue``)
- ``gh api --paginate --slurp 'repos/{owner}/{repo}/issues/<N>/comments?per_page=100'``
  (kind ``get_comments``。ページの配列の配列を返す)
- ``gh api -X POST 'repos/{owner}/{repo}/issues/<N>/comments' -f body=<本文>``
  (kind ``post_comment``)
- ``gh api -X DELETE 'repos/{owner}/{repo}/issues/comments/<id>'`` (kind ``delete_comment``)
- ``gh issue edit <N> --add-label ai:in-progress`` (kind ``add_label``)

状態ファイルの形式 (``base_state`` が既定値を作る):

- ``issue``: issue 番号 (数値)。コマンドの <N> がこれと違えば ``unknown`` になる
- ``labels``: issue に付いているラベル名の配列
- ``comments``: comment (``id``・``created_at``・``body``) の配列。一覧は id 昇順で返す
- ``page_size``: ``--paginate --slurp`` の 1 ページの件数 (小さくすると 2 ページ以上に分かれる)
- ``next_comment_id`` / ``post_created_at``: POST で作る comment の id と created_at
- ``post_visible``: false なら、POST は成功を返すが comment を一覧に加えない
- ``after_post_comments``: POST の直後に一覧へ加える comment (他 session の claim の race の
  再現)
- ``failures``: kind ごとの失敗の注入。``{"from_call": n, "until_call": m, "exit_code": c,
  "stdout": s}`` で、その kind の n 回目から m 回目までの呼び出し (既定は 1 回目から最後まで)
  が状態を変えずに stdout に s を書いて exit c で終わる (既定 c = 1、s = "")。c = 0 と
  JSON として読めない s の組み合わせで「応答を読めない」場合を再現する
- ``call_counts``: 偽の gh が kind ごとの呼び出し回数を数える (テストは設定しない)

偽の gh と偽の sleep は、呼び出しを 1 行 1 JSON の記録ファイル (環境変数 ``FAKE_GH_LOG``) に
呼ばれた順に書く。テストは記録から「投稿しなかった」「削除した id」「ラベルを付けた」
「sleep 3 の位置」を検査する。

検査の観点:

- ``ClaimIssueScriptFileTest``: 実行ビット・shebang・bash 3.2 で使えない機能を使わないこと
- ``ClaimSuccessTest``: 確保 (stdout `claimed comment_id=<id>`、exit 0、ラベル付与)、claim
  本文の書式、claim ではない comment・他のラベルを無視すること、sleep 3 の位置、自分の
  claim が 2 ページ目にある場合、ラベル付与の失敗 (`label=failed`)
- ``ClaimEarlyRetreatTest``: 早期判定の撤退 (`retreat reason=label` / `existing-claim`、
  2 ページ目の claim を含む) で投稿しないこと
- ``ClaimRaceTest``: 先着判定 (`(created_at, 数値 id)` の辞書順最小、id は数値として比べる、
  `ts=` は使わない、`session=` の完全一致、他ページの claim)。負けたら POST で得た id の
  comment だけを削除して `retreat reason=lost-race`。削除の失敗は exit 2
- ``ClaimLeftoverOptionTest``: `--ignore-comment-id` (複数指定) と `--confirmed-leftover`
- ``ClaimSessionIdTest``: `--session-id` と `CLAUDE_CODE_SESSION_ID` の優先順位、どちらも
  無ければ投稿せず exit 2
- ``ClaimArgumentTest``: 引数の不正で何も投稿せず exit 2
- ``ClaimGhFailureTest``: 各 gh 呼び出しの失敗・読めない応答で exit 2、そのときに投稿・削除を
  しないこと (step 4 の取得の失敗と自分の claim が無い場合は自分の claim を削除しない)

どのケースでも stdout が 1 行であることと、``unknown`` の呼び出しが無いことを実行 helper で
検査する。exit 2 になる入力のテストは、同じ状態で入力だけを正した実行が確保できることも
確かめ、停止の原因がその入力であることを示す。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "agent-discipline"
CLAIM_SCRIPT = PLUGIN_DIR / "skills" / "issue-start" / "scripts" / "claim-issue.sh"

ISSUE = "42"
BRANCH = "feat/issue-42-add-claim-script"
SESSION = "session-self"
OTHER_SESSION = "session-other"
IN_PROGRESS_LABEL = "ai:in-progress"
CLAIM_PREFIX = "🔒 ai:claim "

# POST で作る comment の既定の id と created_at。
POSTED_ID = 1000
POSTED_AT = "2026-10-01T00:00:10Z"
EARLIER = "2026-10-01T00:00:09Z"
LATER = "2026-10-01T00:00:11Z"
OLD = "2026-09-30T00:00:00Z"

# 書き込みを伴う gh の呼び出し。
WRITE_KINDS = ("post_comment", "delete_comment", "add_label")

CLAIM_BODY_PATTERN = re.compile(
    r"🔒 ai:claim branch=(?P<branch>\S+) session=(?P<session>\S+) ts=(?P<ts>\S+)"
)
UTC_ISO_8601 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
ERROR_LINE = re.compile(r"error reason=\S+")

# bash 3.2 (macOS) に無い機能。コメント行を除いた本文に照合する。
BASH4_FEATURES = (
    ("mapfile", re.compile(r"\bmapfile\b")),
    ("readarray", re.compile(r"\breadarray\b")),
    ("連想配列 (declare -A / local -A)", re.compile(r"\b(?:declare|local|typeset)\s+-[a-zA-Z]*A")),
    ("大文字小文字変換 (${var,,} / ${var^^})", re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?:,,?|\^\^?)\}")),
)

FAKE_GH_SOURCE = r'''
import json
import os
import re
import sys

STATE_PATH = os.environ["FAKE_GH_STATE"]
LOG_PATH = os.environ["FAKE_GH_LOG"]
REPO = "repos/{owner}/{repo}"
DELETE_ENDPOINT = re.compile(r"repos/\{owner\}/\{repo\}/issues/comments/(\d+)")


def load_state():
    with open(STATE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state):
    temporary = STATE_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
    os.replace(temporary, STATE_PATH)


def record(entry):
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def parse_api(args):
    """gh api の引数を (method, paginate, slurp, fields, endpoint) に分ける。"""
    method = "GET"
    paginate = False
    slurp = False
    fields = {}
    endpoints = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-X", "--method") and index + 1 < len(args):
            method = args[index + 1]
            index += 2
        elif arg == "--paginate":
            paginate = True
            index += 1
        elif arg == "--slurp":
            slurp = True
            index += 1
        elif arg in ("-f", "--raw-field") and index + 1 < len(args):
            key, separator, value = args[index + 1].partition("=")
            if not separator:
                return None
            fields[key] = value
            index += 2
        elif arg.startswith("-"):
            return None
        else:
            endpoints.append(arg)
            index += 1
    if len(endpoints) != 1:
        return None
    return method, paginate, slurp, fields, endpoints[0]


def classify(argv, state):
    """呼び出しを kind と付随情報に分類する。"""
    issue = str(state["issue"])
    if argv[:2] == ["issue", "edit"]:
        if argv[2:] == [issue, "--add-label", "ai:in-progress"]:
            return "add_label", {}
        return "unknown", {}
    if argv[:1] != ["api"]:
        return "unknown", {}
    parsed = parse_api(argv[1:])
    if parsed is None:
        return "unknown", {}
    method, paginate, slurp, fields, endpoint = parsed
    plain = not paginate and not slurp
    if method == "GET" and plain and not fields and endpoint == f"{REPO}/issues/{issue}":
        return "get_issue", {}
    if (
        method == "GET"
        and paginate
        and slurp
        and not fields
        and endpoint == f"{REPO}/issues/{issue}/comments?per_page=100"
    ):
        return "get_comments", {}
    if (
        method == "POST"
        and plain
        and set(fields) == {"body"}
        and endpoint == f"{REPO}/issues/{issue}/comments"
    ):
        return "post_comment", {"body": fields["body"]}
    match = DELETE_ENDPOINT.fullmatch(endpoint)
    if method == "DELETE" and plain and not fields and match:
        return "delete_comment", {"comment_id": int(match.group(1))}
    return "unknown", {}


def injected_failure(state, kind, call_number):
    failure = state.get("failures", {}).get(kind)
    if failure is None:
        return None
    first = failure.get("from_call", 1)
    last = failure.get("until_call")
    if call_number < first or (last is not None and call_number > last):
        return None
    return failure


def sorted_comments(state):
    return sorted(state["comments"], key=lambda comment: comment["id"])


def pages(state):
    comments = sorted_comments(state)
    size = max(1, int(state.get("page_size", 100)))
    chunks = [comments[start:start + size] for start in range(0, len(comments), size)]
    return chunks or [[]]


def main():
    argv = sys.argv[1:]
    state = load_state()
    kind, detail = classify(argv, state)
    counts = state.setdefault("call_counts", {})
    counts[kind] = counts.get(kind, 0) + 1
    entry = {"tool": "gh", "argv": argv, "kind": kind, "call": counts[kind]}
    entry.update(detail)

    if kind == "unknown":
        save_state(state)
        entry["exit"] = 64
        record(entry)
        sys.stderr.write("fake gh: unsupported command: " + " ".join(argv) + "\n")
        return 64

    failure = injected_failure(state, kind, counts[kind])
    if failure is not None:
        save_state(state)
        code = int(failure.get("exit_code", 1))
        entry["exit"] = code
        entry["injected_failure"] = True
        record(entry)
        sys.stdout.write(failure.get("stdout", ""))
        if code != 0:
            sys.stderr.write("fake gh: injected failure\n")
        return code

    output = ""
    code = 0
    if kind == "get_issue":
        labels = [{"name": name} for name in state["labels"]]
        output = json.dumps({"number": state["issue"], "labels": labels}, ensure_ascii=False)
    elif kind == "get_comments":
        output = json.dumps(pages(state), ensure_ascii=False)
    elif kind == "post_comment":
        comment_id = int(state.get("next_comment_id", 1000))
        state["next_comment_id"] = comment_id + 1
        comment = {
            "id": comment_id,
            "created_at": state["post_created_at"],
            "body": detail["body"],
        }
        if state.get("post_visible", True):
            state["comments"].append(comment)
        state["comments"].extend(state.get("after_post_comments", []))
        state["after_post_comments"] = []
        entry["comment_id"] = comment_id
        output = json.dumps(comment, ensure_ascii=False)
    elif kind == "delete_comment":
        before = len(state["comments"])
        state["comments"] = [
            comment for comment in state["comments"] if comment["id"] != detail["comment_id"]
        ]
        if len(state["comments"]) == before:
            code = 1
            sys.stderr.write("fake gh: HTTP 404: Not Found\n")
    elif kind == "add_label":
        if "ai:in-progress" not in state["labels"]:
            state["labels"].append("ai:in-progress")
        output = "https://github.com/owner/repo/issues/" + str(state["issue"])

    save_state(state)
    entry["exit"] = code
    record(entry)
    if output:
        sys.stdout.write(output + "\n")
    return code


sys.exit(main())
'''

FAKE_SLEEP_SOURCE = r'''
import json
import os
import sys

with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"tool": "sleep", "argv": sys.argv[1:]}) + "\n")
sys.exit(0)
'''


def claim_comment(
    comment_id: int,
    created_at: str,
    session: str | None,
    *,
    branch: str = "feat/issue-42-other-work",
    ts: str | None = None,
) -> dict[str, Any]:
    """claim comment。``session`` が None なら `session=` を持たない本文にする。"""
    parts = [f"branch={branch}"]
    if session is not None:
        parts.append(f"session={session}")
    parts.append(f"ts={ts or created_at}")
    return {"id": comment_id, "created_at": created_at, "body": CLAIM_PREFIX + " ".join(parts)}


def plain_comment(comment_id: int, body: str, created_at: str = OLD) -> dict[str, Any]:
    return {"id": comment_id, "created_at": created_at, "body": body}


def base_state(**overrides: Any) -> dict[str, Any]:
    """偽の gh の状態の既定値 (ラベルも comment も無い issue)。"""
    state: dict[str, Any] = {
        "issue": int(ISSUE),
        "labels": [],
        "comments": [],
        "page_size": 100,
        "next_comment_id": POSTED_ID,
        "post_created_at": POSTED_AT,
        "post_visible": True,
        "after_post_comments": [],
        "failures": {},
    }
    state.update(overrides)
    return state


@dataclass
class ClaimRun:
    """claim-issue.sh の 1 回の実行結果と、偽の gh / sleep の呼び出し記録。"""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    calls: list[dict[str, Any]]
    state: dict[str, Any]

    @property
    def line(self) -> str:
        return self.stdout.rstrip("\n")

    def gh_calls(self, kind: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call.get("tool") == "gh" and call["kind"] == kind]

    def write_calls(self) -> list[dict[str, Any]]:
        return [
            call for call in self.calls if call.get("tool") == "gh" and call["kind"] in WRITE_KINDS
        ]

    def positions(self, predicate: Any) -> list[int]:
        return [index for index, call in enumerate(self.calls) if predicate(call)]

    def describe(self) -> str:
        kinds = [call.get("kind", call.get("tool")) for call in self.calls]
        return (
            f"args={self.args} exit={self.returncode} stdout={self.stdout!r} "
            f"stderr={self.stderr.strip()[:200]!r} calls={kinds}"
        )


class ClaimIssueScriptTestCase(unittest.TestCase):
    """偽の gh / sleep を置いた一時ディレクトリで claim-issue.sh を実行する helper。"""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.bin_dir = self.work / "bin"
        self.bin_dir.mkdir()
        self.write_executable(self.bin_dir / "gh", FAKE_GH_SOURCE)
        self.write_executable(self.bin_dir / "sleep", FAKE_SLEEP_SOURCE)
        self.state_path = self.work / "gh-state.json"
        self.log_path = self.work / "calls.jsonl"
        for name in ("home", "gh-config", "tmp", "cwd"):
            (self.work / name).mkdir()
        if shutil.which("jq") is None:
            self.fail("jq が PATH に無い (claim-issue.sh は本物の jq を使う)")

    def write_executable(self, path: Path, source: str) -> None:
        path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def run_claim(
        self,
        state: dict[str, Any],
        *extra_args: str,
        args: list[str] | None = None,
        session_env: str | None = SESSION,
    ) -> ClaimRun:
        """状態ファイルと記録を作り直して claim-issue.sh を実行する。

        ``args`` を省くと ``<ISSUE> <BRANCH>`` に ``extra_args`` を足して渡す。
        ``session_env`` が None なら ``CLAUDE_CODE_SESSION_ID`` を env に入れない。
        """
        command_args = list(args) if args is not None else [ISSUE, BRANCH, *extra_args]
        self.state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.log_path.write_text("", encoding="utf-8")
        env = {
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.work / "home"),
            "TMPDIR": str(self.work / "tmp"),
            "GH_CONFIG_DIR": str(self.work / "gh-config"),
            # ts= を UTC で書かない実装を見分けるため、UTC ではないタイムゾーンにする。
            "TZ": "Asia/Tokyo",
            "FAKE_GH_STATE": str(self.state_path),
            "FAKE_GH_LOG": str(self.log_path),
        }
        if session_env is not None:
            env["CLAUDE_CODE_SESSION_ID"] = session_env
        completed = subprocess.run(
            [str(CLAIM_SCRIPT), *command_args],
            cwd=self.work / "cwd",
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        calls = [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        result = ClaimRun(
            args=command_args,
            returncode=completed.returncode,
            stdout=completed.stdout.decode("utf-8", errors="replace"),
            stderr=completed.stderr.decode("utf-8", errors="replace"),
            calls=calls,
            state=json.loads(self.state_path.read_text(encoding="utf-8")),
        )
        self.assertEqual(
            1,
            len(result.stdout.splitlines()),
            f"stdout が 1 行ではない: {result.describe()}",
        )
        self.assertEqual(
            [],
            result.gh_calls("unknown"),
            f"偽の gh が応答しないコマンドを呼んだ: {result.describe()}",
        )
        return result

    # --- 結果の検査 -------------------------------------------------------------

    def assert_claimed(self, run: ClaimRun, comment_id: int = POSTED_ID) -> None:
        self.assertEqual(0, run.returncode, run.describe())
        self.assertEqual(f"claimed comment_id={comment_id}", run.line, run.describe())

    def assert_retreat(self, run: ClaimRun, reason: str) -> None:
        self.assertEqual(1, run.returncode, run.describe())
        self.assertEqual(f"retreat reason={reason}", run.line, run.describe())

    def assert_error(self, run: ClaimRun) -> None:
        self.assertEqual(2, run.returncode, run.describe())
        self.assertRegex(run.line, r"\A" + ERROR_LINE.pattern + r"\Z", run.describe())

    def assert_not_posted(self, run: ClaimRun) -> None:
        self.assertEqual([], run.gh_calls("post_comment"), f"投稿した: {run.describe()}")

    def assert_no_writes(self, run: ClaimRun) -> None:
        self.assertEqual([], run.write_calls(), f"書き込みをした: {run.describe()}")

    def assert_posted_once(self, run: ClaimRun) -> dict[str, Any]:
        posts = run.gh_calls("post_comment")
        self.assertEqual(1, len(posts), f"投稿の回数: {run.describe()}")
        return posts[0]

    def assert_not_deleted(self, run: ClaimRun) -> None:
        self.assertEqual([], run.gh_calls("delete_comment"), f"削除した: {run.describe()}")

    def assert_deleted_ids(self, run: ClaimRun, expected: list[int]) -> None:
        deleted = [call["comment_id"] for call in run.gh_calls("delete_comment")]
        self.assertEqual(expected, deleted, f"削除した comment id: {run.describe()}")

    def assert_label_added(self, run: ClaimRun) -> None:
        self.assertEqual(1, len(run.gh_calls("add_label")), f"ラベル付与: {run.describe()}")

    def assert_label_not_added(self, run: ClaimRun) -> None:
        self.assertEqual([], run.gh_calls("add_label"), f"ラベルを付けた: {run.describe()}")

    def assert_comment_ids(self, run: ClaimRun, expected: set[int]) -> None:
        """実行後に issue に残っている comment の id の集合。"""
        self.assertEqual(
            expected,
            {comment["id"] for comment in run.state["comments"]},
            f"実行後の comment: {run.describe()}",
        )

    def assert_input_causes_stop(self, run: ClaimRun, control: ClaimRun) -> None:
        """不正な入力では何も書き込まずに停止し、入力を正した実行は確保できる。"""
        self.assert_error(run)
        self.assert_no_writes(run)
        self.assert_claimed(control)


class ClaimIssueScriptFileTest(unittest.TestCase):
    """スクリプトのファイルとしての前提。"""

    def test_script_is_an_executable_bash_script(self) -> None:
        """実行ビットが付き、shebang が `#!/bin/bash` である。"""
        self.assertTrue(CLAIM_SCRIPT.is_file(), f"{CLAIM_SCRIPT} が無い")
        self.assertTrue(os.access(CLAIM_SCRIPT, os.X_OK), f"{CLAIM_SCRIPT} に実行ビットが無い")
        first_line = CLAIM_SCRIPT.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual("#!/bin/bash", first_line)

    def test_script_avoids_features_missing_in_bash_3_2(self) -> None:
        """macOS の bash 3.2 に無い機能 (mapfile / readarray / 連想配列 / 大文字小文字変換) を
        コメント以外で使わない。"""
        code_lines = [
            line
            for line in CLAIM_SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        for name, pattern in BASH4_FEATURES:
            with self.subTest(feature=name):
                self.assertIsNone(pattern.search(code), f"{name} を使っている")


class ClaimSuccessTest(ClaimIssueScriptTestCase):
    """確保できる場合。"""

    def test_claims_an_issue_without_label_or_claim(self) -> None:
        """ラベルも claim comment も無ければ、claim comment を 1 回投稿し、ラベルを付けて
        `claimed comment_id=<POST で得た id>` を返す (exit 0)。"""
        run = self.run_claim(base_state())
        self.assert_claimed(run)
        self.assert_posted_once(run)
        self.assert_label_added(run)
        self.assert_not_deleted(run)
        self.assertIn(IN_PROGRESS_LABEL, run.state["labels"])
        self.assert_comment_ids(run, {POSTED_ID})

    def test_claim_body_has_the_documented_format(self) -> None:
        """claim の本文は `🔒 ai:claim branch=<branch> session=<id> ts=<UTC ISO 8601>` の
        1 行で、ts= は実行時の UTC 時刻 (ローカル時刻ではない)。"""
        before = datetime.now(timezone.utc)
        run = self.run_claim(base_state())
        after = datetime.now(timezone.utc)
        body = self.assert_posted_once(run)["body"]
        match = CLAIM_BODY_PATTERN.fullmatch(body)
        self.assertIsNotNone(match, f"claim の本文の書式が違う: {body!r}")
        assert match is not None
        self.assertEqual(BRANCH, match.group("branch"))
        self.assertEqual(SESSION, match.group("session"))
        ts = match.group("ts")
        self.assertRegex(ts, r"\A" + UTC_ISO_8601.pattern + r"\Z")
        stamped = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        margin = timedelta(minutes=2)
        self.assertTrue(
            before - margin <= stamped <= after + margin,
            f"ts= が実行時の UTC 時刻ではない: ts={ts} 実行={before.isoformat()}",
        )

    def test_non_claim_comments_and_other_labels_are_ignored(self) -> None:
        """本文が `🔒 ai:claim ` で始まらない comment は claim ではなく、早期判定でも先着判定
        でも数えない。`ai:in-progress` 以外のラベルでは撤退しない。"""
        state = base_state(
            labels=["P2", "bug"],
            comments=[
                plain_comment(1, f"LGTM {CLAIM_PREFIX}branch=x session={OTHER_SESSION} ts={OLD}"),
                plain_comment(2, f"🔒 ai:claimed branch=x session={OTHER_SESSION} ts={OLD}"),
                plain_comment(3, f"ai:claim branch=x session={OTHER_SESSION} ts={OLD}"),
            ],
            after_post_comments=[
                plain_comment(
                    999, f"note: {CLAIM_PREFIX}session={OTHER_SESSION}", created_at=EARLIER
                ),
            ],
        )
        run = self.run_claim(state)
        self.assert_claimed(run)
        self.assert_label_added(run)

    def test_sleep_3_runs_once_between_post_and_final_fetch(self) -> None:
        """`sleep 3` は claim の投稿の後、先着判定の comment 取得の前に 1 回だけ呼ぶ。"""
        run = self.run_claim(base_state())
        self.assert_claimed(run)
        sleeps = run.positions(lambda call: call.get("tool") == "sleep")
        self.assertEqual(1, len(sleeps), f"sleep の回数: {run.describe()}")
        self.assertEqual(["3"], run.calls[sleeps[0]]["argv"])
        posts = run.positions(lambda call: call.get("kind") == "post_comment")
        fetches = run.positions(lambda call: call.get("kind") == "get_comments")
        self.assertEqual(1, len(posts), run.describe())
        self.assertTrue(fetches, run.describe())
        self.assertLess(posts[0], sleeps[0], f"sleep が投稿より前: {run.describe()}")
        self.assertLess(
            sleeps[0], fetches[-1], f"先着判定の取得が sleep より前: {run.describe()}"
        )

    def test_own_claim_on_the_second_page_is_found(self) -> None:
        """comment が複数ページに分かれ、自分の claim が 2 ページ目にあっても確保できる。"""
        state = base_state(
            page_size=2,
            comments=[plain_comment(index, f"comment {index}") for index in (1, 2, 3)],
        )
        run = self.run_claim(state)
        self.assert_claimed(run)

    def test_label_failure_still_claims(self) -> None:
        """ラベル付与が失敗しても確保として exit 0 を返し、stdout を
        `claimed comment_id=<id> label=failed` にする。claim は削除しない。"""
        state = base_state(failures={"add_label": {}})
        run = self.run_claim(state)
        self.assertEqual(0, run.returncode, run.describe())
        self.assertEqual(f"claimed comment_id={POSTED_ID} label=failed", run.line)
        self.assert_label_added(run)
        self.assert_not_deleted(run)


class ClaimEarlyRetreatTest(ClaimIssueScriptTestCase):
    """早期判定の撤退 (claim を投稿しない)。"""

    def test_in_progress_label_retreats(self) -> None:
        """ラベル `ai:in-progress` があれば `retreat reason=label` で撤退し、投稿しない。"""
        run = self.run_claim(base_state(labels=["P2", IN_PROGRESS_LABEL]))
        self.assert_retreat(run, "label")
        self.assert_no_writes(run)
        self.assertTrue(run.gh_calls("get_issue"), run.describe())

    def test_existing_claim_retreats(self) -> None:
        """他 session の claim comment があれば `retreat reason=existing-claim` で撤退し、
        投稿しない。"""
        state = base_state(comments=[claim_comment(7, OLD, OTHER_SESSION)])
        run = self.run_claim(state)
        self.assert_retreat(run, "existing-claim")
        self.assert_no_writes(run)

    def test_claim_without_session_retreats(self) -> None:
        """`session=` の無い claim comment も claim として撤退する。"""
        state = base_state(comments=[claim_comment(7, OLD, None)])
        run = self.run_claim(state)
        self.assert_retreat(run, "existing-claim")
        self.assert_no_writes(run)

    def test_own_session_claim_retreats(self) -> None:
        """`session=` が自分のセッション ID と同じ claim comment でも、除外の指定が無ければ
        早期判定で撤退する。"""
        state = base_state(comments=[claim_comment(7, OLD, SESSION)])
        run = self.run_claim(state)
        self.assert_retreat(run, "existing-claim")
        self.assert_no_writes(run)

    def test_claim_on_the_second_page_retreats(self) -> None:
        """早期判定も全ページを見る (2 ページ目の claim comment で撤退する)。"""
        state = base_state(
            page_size=1,
            comments=[
                plain_comment(1, "comment 1"),
                plain_comment(2, "comment 2"),
                claim_comment(3, OLD, OTHER_SESSION),
            ],
        )
        run = self.run_claim(state)
        self.assert_retreat(run, "existing-claim")
        self.assert_no_writes(run)

    def test_label_and_claim_retreat(self) -> None:
        """ラベルと claim comment の両方があれば、どちらかの理由で撤退し、投稿しない。"""
        state = base_state(
            labels=[IN_PROGRESS_LABEL], comments=[claim_comment(7, OLD, OTHER_SESSION)]
        )
        run = self.run_claim(state)
        self.assertEqual(1, run.returncode, run.describe())
        self.assertIn(run.line, ("retreat reason=label", "retreat reason=existing-claim"))
        self.assert_no_writes(run)


class ClaimRaceTest(ClaimIssueScriptTestCase):
    """先着判定 (POST の後に他 session の claim comment が現れる場合)。"""

    def assert_lost_race(self, run: ClaimRun) -> None:
        """POST で得た id の comment だけを削除して `retreat reason=lost-race` (exit 1)。"""
        self.assert_retreat(run, "lost-race")
        self.assert_deleted_ids(run, [POSTED_ID])
        self.assert_label_not_added(run)
        self.assertNotIn(POSTED_ID, {comment["id"] for comment in run.state["comments"]})

    def test_earlier_claim_wins(self) -> None:
        """自分より created_at が早い claim comment が現れたら、自分の claim を削除して撤退する。"""
        state = base_state(after_post_comments=[claim_comment(999, EARLIER, OTHER_SESSION)])
        run = self.run_claim(state)
        self.assert_lost_race(run)
        self.assert_comment_ids(run, {999})

    def test_later_claim_does_not_win(self) -> None:
        """自分より created_at が遅い claim comment が現れても、自分が先着として確保する。"""
        state = base_state(after_post_comments=[claim_comment(1001, LATER, OTHER_SESSION)])
        run = self.run_claim(state)
        self.assert_claimed(run)
        self.assert_label_added(run)
        self.assert_not_deleted(run)

    def test_same_created_at_compares_ids_as_numbers(self) -> None:
        """created_at が同じなら数値 id の小さい方が先着 (999 < 1000。文字列として比べると
        逆になる)。"""
        state = base_state(after_post_comments=[claim_comment(999, POSTED_AT, OTHER_SESSION)])
        run = self.run_claim(state)
        self.assert_lost_race(run)

    def test_same_created_at_larger_id_does_not_win(self) -> None:
        """created_at が同じで id が自分より大きい claim comment には勝つ。"""
        state = base_state(after_post_comments=[claim_comment(1001, POSTED_AT, OTHER_SESSION)])
        run = self.run_claim(state)
        self.assert_claimed(run)

    def test_created_at_takes_precedence_over_id(self) -> None:
        """先着は (created_at, 数値 id) の辞書順で決める: id が小さくても created_at が遅い
        claim には勝ち、id が大きくても created_at が早い claim には負ける。"""
        with self.subTest(case="小さい id・遅い created_at"):
            state = base_state(after_post_comments=[claim_comment(999, LATER, OTHER_SESSION)])
            self.assert_claimed(self.run_claim(state))
        with self.subTest(case="大きい id・早い created_at"):
            state = base_state(after_post_comments=[claim_comment(1001, EARLIER, OTHER_SESSION)])
            self.assert_lost_race(self.run_claim(state))

    def test_ts_value_is_not_used(self) -> None:
        """先着判定に本文の `ts=` を使わず、created_at で決める。"""
        with self.subTest(case="ts= は遅いが created_at が早い claim"):
            competitor = claim_comment(
                999, EARLIER, OTHER_SESSION, ts="2099-01-01T00:00:00Z"
            )
            state = base_state(after_post_comments=[competitor])
            self.assert_lost_race(self.run_claim(state))
        with self.subTest(case="ts= は早いが created_at が遅い claim"):
            competitor = claim_comment(
                1001, LATER, OTHER_SESSION, ts="2000-01-01T00:00:00Z"
            )
            state = base_state(after_post_comments=[competitor])
            self.assert_claimed(self.run_claim(state))

    def test_session_must_match_exactly(self) -> None:
        """`session=` の値が自分のセッション ID を含むだけの claim (前方一致) は他 session の
        claim として扱う。"""
        competitor = claim_comment(999, EARLIER, SESSION + "-2")
        state = base_state(after_post_comments=[competitor])
        run = self.run_claim(state)
        self.assert_lost_race(run)

    def test_earlier_claim_on_another_page_wins(self) -> None:
        """先着判定は全ページを結合して行う (自分と相手の claim が別のページにあっても負けを
        判定する)。"""
        state = base_state(
            page_size=1,
            comments=[plain_comment(1, "comment 1")],
            after_post_comments=[claim_comment(999, EARLIER, OTHER_SESSION)],
        )
        run = self.run_claim(state)
        self.assert_lost_race(run)

    def test_delete_failure_after_lost_race_is_an_error(self) -> None:
        """先着判定で負けた後に自分の claim の削除が失敗したら、撤退扱いにせず exit 2。"""
        state = base_state(
            after_post_comments=[claim_comment(999, EARLIER, OTHER_SESSION)],
            failures={"delete_comment": {}},
        )
        run = self.run_claim(state)
        self.assert_error(run)
        self.assert_deleted_ids(run, [POSTED_ID])
        self.assert_label_not_added(run)


class ClaimLeftoverOptionTest(ClaimIssueScriptTestCase):
    """`--ignore-comment-id` と `--confirmed-leftover`。"""

    def test_confirmed_leftover_ignores_the_label(self) -> None:
        """`--confirmed-leftover` では、ラベル `ai:in-progress` があっても撤退せず確保する。"""
        state = base_state(labels=[IN_PROGRESS_LABEL])
        run = self.run_claim(state, "--confirmed-leftover")
        self.assert_claimed(run)
        self.assert_posted_once(run)

    def test_confirmed_leftover_keeps_the_claim_check(self) -> None:
        """`--confirmed-leftover` でも、除外していない claim comment があれば撤退する。"""
        state = base_state(
            labels=[IN_PROGRESS_LABEL], comments=[claim_comment(7, OLD, OTHER_SESSION)]
        )
        run = self.run_claim(state, "--confirmed-leftover")
        self.assert_retreat(run, "existing-claim")
        self.assert_no_writes(run)

    def test_ignored_claim_is_skipped_by_both_checks(self) -> None:
        """`--ignore-comment-id` で指定した claim comment は早期判定でも先着判定でも数えない
        (自分より created_at が早くても負けにしない)。"""
        state = base_state(comments=[claim_comment(7, OLD, OTHER_SESSION)])
        run = self.run_claim(state, "--ignore-comment-id", "7")
        self.assert_claimed(run)
        self.assert_label_added(run)
        self.assert_not_deleted(run)

    def test_multiple_ignored_ids(self) -> None:
        """`--ignore-comment-id` は複数指定できる。"""
        state = base_state(
            comments=[claim_comment(7, OLD, OTHER_SESSION), claim_comment(8, OLD, None)]
        )
        run = self.run_claim(state, "--ignore-comment-id", "7", "--ignore-comment-id", "8")
        self.assert_claimed(run)

    def test_only_listed_ids_are_ignored(self) -> None:
        """指定していない claim comment は除かない (早期判定で撤退する)。"""
        state = base_state(
            comments=[claim_comment(7, OLD, OTHER_SESSION), claim_comment(8, OLD, OTHER_SESSION)]
        )
        run = self.run_claim(state, "--ignore-comment-id", "7")
        self.assert_retreat(run, "existing-claim")
        self.assert_no_writes(run)

    def test_ignored_own_session_claim_is_not_own(self) -> None:
        """除外した claim comment は、`session=` が自分のセッション ID と一致しても自分の claim
        として扱わない (投稿した claim が一覧に無ければ exit 2)。"""
        state = base_state(
            comments=[claim_comment(7, OLD, SESSION)],
            post_visible=False,
        )
        run = self.run_claim(state, "--ignore-comment-id", "7")
        self.assert_error(run)
        self.assert_posted_once(run)
        self.assert_not_deleted(run)
        self.assert_label_not_added(run)

    def test_lost_race_deletes_only_the_posted_claim(self) -> None:
        """除外した claim の後に投稿された他 session の claim には負ける。そのとき削除するのは
        POST で得た id の comment だけで、除外した自分の session の claim は消さない。"""
        state = base_state(
            labels=[IN_PROGRESS_LABEL],
            comments=[claim_comment(7, OLD, SESSION)],
            after_post_comments=[claim_comment(999, EARLIER, OTHER_SESSION)],
        )
        run = self.run_claim(state, "--ignore-comment-id", "7", "--confirmed-leftover")
        self.assert_retreat(run, "lost-race")
        self.assert_deleted_ids(run, [POSTED_ID])
        self.assert_label_not_added(run)
        self.assert_comment_ids(run, {7, 999})


class ClaimSessionIdTest(ClaimIssueScriptTestCase):
    """セッション ID の決め方。"""

    def posted_session(self, run: ClaimRun) -> str:
        match = CLAIM_BODY_PATTERN.fullmatch(self.assert_posted_once(run)["body"])
        self.assertIsNotNone(match, run.describe())
        assert match is not None
        return match.group("session")

    def test_session_id_option_takes_precedence(self) -> None:
        """`--session-id` があれば環境変数より優先し、claim の `session=` と自分の claim の
        識別に使う (環境変数の値の claim は他 session のものとして扱う)。"""
        state = base_state(after_post_comments=[claim_comment(999, EARLIER, "env-session")])
        run = self.run_claim(state, "--session-id", "cli-session", session_env="env-session")
        self.assertEqual("cli-session", self.posted_session(run))
        self.assert_retreat(run, "lost-race")
        self.assert_deleted_ids(run, [POSTED_ID])

    def test_environment_session_id_is_used(self) -> None:
        """`--session-id` が無ければ `CLAUDE_CODE_SESSION_ID` を使う。"""
        run = self.run_claim(base_state(), session_env="env-session")
        self.assert_claimed(run)
        self.assertEqual("env-session", self.posted_session(run))

    def test_session_id_option_works_without_environment(self) -> None:
        """環境変数が無くても `--session-id` があれば確保できる。"""
        run = self.run_claim(base_state(), "--session-id", "cli-session", session_env=None)
        self.assert_claimed(run)
        self.assertEqual("cli-session", self.posted_session(run))

    def test_missing_session_id_stops_without_posting(self) -> None:
        """セッション ID が `--session-id` にも `CLAUDE_CODE_SESSION_ID` にも無ければ、投稿
        せず exit 2 (環境変数が空文字列の場合も同じ)。"""
        for case, session_env in (("環境変数なし", None), ("空文字列", "")):
            with self.subTest(case=case):
                run = self.run_claim(base_state(), session_env=session_env)
                control = self.run_claim(base_state(), session_env=SESSION)
                self.assert_input_causes_stop(run, control)


class ClaimArgumentTest(ClaimIssueScriptTestCase):
    """引数の不正 (何も投稿せず exit 2)。"""

    def assert_invalid(self, cases: dict[str, list[str]]) -> None:
        for case, args in cases.items():
            with self.subTest(case=case):
                run = self.run_claim(base_state(), args=args)
                control = self.run_claim(base_state())
                self.assert_input_causes_stop(run, control)

    def test_issue_number_must_be_digits(self) -> None:
        """issue 番号が数字でなければ exit 2。"""
        self.assert_invalid(
            {
                "英字": ["abc", BRANCH],
                "数字と英字": ["42a", BRANCH],
                "空文字列": ["", BRANCH],
                "負の数": ["-42", BRANCH],
            }
        )

    def test_branch_name_must_not_be_empty(self) -> None:
        """branch 名が空なら exit 2。"""
        self.assert_invalid({"空文字列": [ISSUE, ""]})

    def test_positional_arguments_are_required(self) -> None:
        """issue 番号と branch 名の両方が要る。"""
        self.assert_invalid({"引数なし": [], "branch 名なし": [ISSUE]})

    def test_ignored_comment_id_must_be_digits(self) -> None:
        """`--ignore-comment-id` の値が数字でなければ exit 2 (値が無い場合も同じ)。"""
        self.assert_invalid(
            {
                "英字": [ISSUE, BRANCH, "--ignore-comment-id", "abc"],
                "数字と英字": [ISSUE, BRANCH, "--ignore-comment-id", "7a"],
                "空文字列": [ISSUE, BRANCH, "--ignore-comment-id", ""],
                "2 つ目が不正": [
                    ISSUE,
                    BRANCH,
                    "--ignore-comment-id",
                    "7",
                    "--ignore-comment-id",
                    "x",
                ],
                "値なし": [ISSUE, BRANCH, "--ignore-comment-id"],
            }
        )

    def test_unknown_or_incomplete_options_stop(self) -> None:
        """未知のオプションと、値の欠けた `--session-id` は exit 2。"""
        self.assert_invalid(
            {
                "未知のオプション": [ISSUE, BRANCH, "--force"],
                "--session-id の値なし": [ISSUE, BRANCH, "--session-id"],
                "--session-id の値がオプション": [
                    ISSUE,
                    BRANCH,
                    "--session-id",
                    "--confirmed-leftover",
                ],
            }
        )


class ClaimGhFailureTest(ClaimIssueScriptTestCase):
    """gh の呼び出しの失敗 (fail-closed で exit 2)。"""

    def test_label_fetch_failure_stops_without_posting(self) -> None:
        """step 1 のラベルの取得が失敗したら、投稿せず exit 2。"""
        cases = {
            "非ゼロ終了": {"get_issue": {}},
            "読めない応答": {"get_issue": {"exit_code": 0, "stdout": "not json"}},
        }
        for case, failures in cases.items():
            with self.subTest(case=case):
                run = self.run_claim(base_state(failures=failures))
                self.assert_error(run)
                self.assert_no_writes(run)
                self.assertTrue(run.gh_calls("get_issue"), run.describe())

    def test_comment_fetch_failure_at_early_check_stops_without_posting(self) -> None:
        """step 1 の comment の取得が失敗したら、投稿せず exit 2。"""
        cases = {
            "非ゼロ終了": {"get_comments": {}},
            "読めない応答": {"get_comments": {"exit_code": 0, "stdout": "<html>"}},
        }
        for case, failures in cases.items():
            with self.subTest(case=case):
                run = self.run_claim(base_state(failures=failures))
                self.assert_error(run)
                self.assert_no_writes(run)
                self.assertTrue(run.gh_calls("get_comments"), run.describe())

    def test_post_failure_stops(self) -> None:
        """claim の投稿が失敗したら (応答から id を読めない場合を含む) exit 2 で、削除も
        ラベル付与もしない。"""
        cases = {
            "非ゼロ終了": {"post_comment": {}},
            "id の無い応答": {"post_comment": {"exit_code": 0, "stdout": "{}"}},
        }
        for case, failures in cases.items():
            with self.subTest(case=case):
                run = self.run_claim(base_state(failures=failures))
                self.assert_error(run)
                self.assert_posted_once(run)
                self.assert_not_deleted(run)
                self.assert_label_not_added(run)

    def test_refetch_failure_keeps_the_own_claim(self) -> None:
        """step 4 の取得が失敗したら (非ゼロ終了・読めない応答・途中までの出力で非ゼロ終了)、
        確保を確定せず exit 2。投稿済みの自分の claim は削除しない。"""
        own_claim_only = json.dumps(
            [[{
                "id": POSTED_ID,
                "created_at": POSTED_AT,
                "body": f"{CLAIM_PREFIX}branch={BRANCH} session={SESSION} ts={POSTED_AT}",
            }]],
            ensure_ascii=False,
        )
        cases = {
            "非ゼロ終了": {"from_call": 2},
            "読めない応答": {"from_call": 2, "exit_code": 0, "stdout": "[[{"},
            "途中までの出力で非ゼロ終了": {"from_call": 2, "stdout": own_claim_only},
        }
        for case, failure in cases.items():
            with self.subTest(case=case):
                state = base_state(
                    failures={"get_comments": failure},
                    after_post_comments=[claim_comment(999, EARLIER, OTHER_SESSION)],
                )
                run = self.run_claim(state)
                self.assert_error(run)
                self.assert_posted_once(run)
                self.assert_not_deleted(run)
                self.assert_label_not_added(run)
                self.assertIn(POSTED_ID, {comment["id"] for comment in run.state["comments"]})

    def test_missing_own_claim_stops(self) -> None:
        """step 4 の取得結果に自分の claim が無ければ、確保を確定せず exit 2 (削除しない)。"""
        run = self.run_claim(base_state(post_visible=False))
        self.assert_error(run)
        self.assert_posted_once(run)
        self.assert_not_deleted(run)
        self.assert_label_not_added(run)


if __name__ == "__main__":
    unittest.main()

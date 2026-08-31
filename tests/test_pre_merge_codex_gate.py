"""pre-merge-codex-review の merge gate 契約テスト (Phase A: spec-first, red)。

`GATE` (`plugins/pre-merge-codex-review/hooks/scripts/block-pre-merge.sh`) は
Phase A 時点でまだ存在しない。すべてのテストは、この gate script が実装される
までは意図した失敗 (assert failure、または gate 不在に由来する応答不一致) で
red になる。

固定する契約:

- gate は `gh pr merge` を検出した Bash コマンドに対する PreToolUse hook である。
- marker (`.claude-pre-merge-codex-reviewed`) は改行区切りの key=value で 5 key を
  束縛する: `repo` (owner/name)・`pr` (PR 番号)・`merge_base` (base branch との
  merge-base commit OID)・`head` (レビュー時の PR head commit OID)・`diff_hash`
  (merge-base..head 全差分の sha256。計算式は
  `plugins/pre-push-review/hooks/scripts/lib/diff-hash.sh` の
  `compute_review_hash_in` と同一)。
- marker が無い、または 5 key のいずれかが実 PR metadata・現在のローカル HEAD と
  一致しなければ gate は deny する。
- `--auto` を含む `gh pr merge` は marker の状態に関わらず常に deny する。
  `--auto=<値>` の連結形も値 (true / false) に依らず同様に deny する
  (フラグ形の解析分岐を増やさず fail-closed に倒す)。
- gate は Bash コマンド全体が単一の `gh pr merge` invocation である場合のみ
  判定し、他コマンドとの連結 (`&&` / `;` / `|` 等)・複数の merge invocation・
  `cd` 等の前置コマンドを含む合成形は marker の状態に関わらず保守的に deny
  する (検証した repo / PR と実行される merge の乖離、および 2 つ目以降の
  invocation の検証漏れを塞ぐ)。
- gate は merge 対象 PR の実 metadata を `gh pr view [<対象指定>] --json <fields>`
  で取得する。`gh pr merge` の対象指定 (PR 番号 / URL / branch / 省略) は
  位置引数としてそのまま転送し、`-R` / `--repo` による repo 指定の値も転送する
  (分離形 / `=` 連結形 / `-R<value>` attached 短縮形のいずれも)。marker の
  `pr` は取得した実 metadata の number と照合する (対象省略形ではコマンドに
  PR 番号が現れないため、コマンド文字列やローカル状態からの推定は照合に
  使えない)。
  `--json` で要求してよいフィールドは fake payload の key 集合 (gh 2.96.0 の
  `gh pr view --json` に実在するフィールドのみ。2026-08-31 実測) のサブセットに
  限る。
- 5 key すべてが実 PR metadata・現在のローカル HEAD と一致する場合のみ gate は
  allow し、`merge` サブコマンドトークンの末尾直後に
  ` --match-head-commit <レビュー済み head OID>` を挿入した `updatedInput` を返す
  (enforce-draft-pr と同型の offset 挿入方式。挿入以外の 1 バイトも変更しない)。
  挿入結果の command は完全一致で検証する。merge 方式 (--merge / --squash /
  --rebase) や `--delete-branch` の併用は判定に影響しない。
- コマンドが既に `--match-head-commit` を指定している場合 (分離形 / `=` 連結形
  とも): 値がレビュー済み head OID と一致するなら allow し、コマンドは
  書き換えない (updatedInput を返さない)。一致しない値なら deny する。
  `--match-head-commit` が 1 コマンド中に複数回出現する場合は、値の一致に
  依らず deny する (cobra の後勝ち解決により検証済みの値が後続の未検証値で
  上書きされる経路を塞ぐ)。
- marker の `merge_base` / `diff_hash` の検証は、取得した実 PR metadata の
  base branch (`baseRefName`) を基準にする (ローカル default branch の
  決め打ちは、非 default base への PR で必ず不一致になる)。
- marker の `repo` は merge 対象 (base) リポジトリの identity と照合する。
  fork 由来の PR (`isCrossRepository` が true で `headRepository` が別 repo を
  指す) でも、照合対象は base リポジトリであり head 側リポジトリではない。
  merge queue の GraphQL 照会も base リポジトリに対して行う。
- merge 対象 PR の base branch が merge queue を要求する場合 (PR の GraphQL
  field `isMergeQueueEnabled` が true)、gate は marker の状態に関わらず deny
  する。gh は merge queue 必須 branch への `gh pr merge` を `--auto` の有無に
  依らず即時 merge せず遅延実行 (checks 未完了なら auto-merge 有効化、完了済み
  なら enqueue) に倒すため、`--auto` と同じ理由 (予約時点と実 merge 実行の
  分離) でローカル gate の射程外になる。`isMergeQueueEnabled` は gh 自身が
  enqueue 判定に使うフィールドであり、`gh pr view --json` では取得できない
  (gh 2.96.0。2026-08-31 実測) ため、gate は `gh api graphql` で取得する。
  取得失敗は deny (fail-closed)。

gate は実の GitHub API を叩けないため、テストは一時ディレクトリに fake `gh`
実行ファイルを作り PATH の先頭に置く。fake `gh` は上記の呼び出し形のみを
受理する厳格 fixture であり、想定外のサブコマンド・フラグ・対象指定・未知の
`--json` フィールドを受けると非 0 で終了する (gate は fail-closed のため、
allow を期待するテストの失敗として呼び出し形の契約違反が表面化する)。応答
JSON は gh の実挙動と同じく、要求されたフィールドのみを含む。

fake `gh` は `gh pr view` に加えて `gh api graphql` の呼び出しを受理する。
graphql 呼び出しは、引数の結合文字列に owner / name / PR 番号 /
`isMergeQueueEnabled` が現れること (= gate が対象 PR の merge queue 状態を
要求していること) を検証し、`{"data": {"repository": {"pullRequest": {...}}}}`
形で `isMergeQueueEnabled` / `isInMergeQueue` を返す。テストは fake に
graphql 呼び出しの失敗 (exit 1) を注入でき、取得失敗時の fail-closed deny を
直接検証する。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = (
    ROOT
    / "plugins"
    / "pre-merge-codex-review"
    / "hooks"
    / "scripts"
    / "block-pre-merge.sh"
)

MARKER_NAME = ".claude-pre-merge-codex-reviewed"
CODEX_SUBAGENT_NAME = "pre-merge-codex-review:codex-reviewer"

PR_NUMBER = 123
REPO_NAME_WITH_OWNER = "test-owner/test-repo"
PR_URL = f"https://github.com/{REPO_NAME_WITH_OWNER}/pull/{PR_NUMBER}"

MERGE_COMMAND = f"gh pr merge {PR_NUMBER} --merge"
MERGE_AUTO_COMMAND = f"gh pr merge {PR_NUMBER} --auto --merge"

# 厳格 fake gh 本体。期待する呼び出し形 (expect_target / expect_repo_flag) と
# 応答 payload は同じディレクトリの gh-config.json から読む。想定外の呼び出しは
# exit 64 で拒否し、gate 側の fail-closed 挙動 (取得失敗 → deny) に落とす。
FAKE_GH_SCRIPT = '''#!/usr/bin/env python3
"""厳格 fake gh: gate が採用した呼び出し形のみを受理する contract fixture。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
CONFIG_PATH = os.path.join(HERE, "gh-config.json")
LOG_PATH = os.path.join(HERE, "gh-calls.log")

with open(CONFIG_PATH, encoding="utf-8") as fp:
    config = json.load(fp)

payload = config["payload"]
expect_target = config["expect_target"]
expect_repo_flag = config["expect_repo_flag"]


def log(line):
    with open(LOG_PATH, "a", encoding="utf-8") as fp:
        fp.write(line + "\\n")


def reject(message):
    log("REJECT " + json.dumps(sys.argv[1:]) + " " + message)
    print("fake-gh: " + message, file=sys.stderr)
    sys.exit(64)


args = sys.argv[1:]

if args[:2] == ["api", "graphql"]:
    if config.get("graphql_fail"):
        log("FAIL " + json.dumps(args))
        print("fake-gh: injected graphql failure", file=sys.stderr)
        sys.exit(1)
    joined = " ".join(args[2:])
    missing = [
        token
        for token in config["graphql_required_tokens"]
        if token not in joined
    ]
    if missing:
        reject("graphql call is missing required tokens: " + ",".join(missing))
    log("ACCEPT " + json.dumps(args))
    print(
        json.dumps(
            {"data": {"repository": {"pullRequest": config["merge_queue"]}}}
        )
    )
    sys.exit(0)

if args[:2] != ["pr", "view"]:
    reject("only 'gh pr view' and 'gh api graphql' are accepted")

positional = []
json_fields = None
repo_flag = None
index = 2
while index < len(args):
    arg = args[index]
    if arg == "--json":
        if index + 1 >= len(args):
            reject("--json requires a value")
        json_fields = args[index + 1]
        index += 2
    elif arg.startswith("--json="):
        json_fields = arg.split("=", 1)[1]
        index += 1
    elif arg in ("-R", "--repo"):
        if index + 1 >= len(args):
            reject(arg + " requires a value")
        repo_flag = args[index + 1]
        index += 2
    elif arg.startswith("--repo="):
        repo_flag = arg.split("=", 1)[1]
        index += 1
    elif arg.startswith("-R") and len(arg) > 2:
        # pflag の shorthand は `-Rvalue` / `-R=value` の両形を許し、
        # `=` 区切り形では `=` を除いた value を返す。
        value = arg[2:]
        if value.startswith("="):
            value = value[1:]
        repo_flag = value
        index += 1
    elif arg.startswith("-"):
        reject("unexpected flag: " + arg)
    else:
        positional.append(arg)
        index += 1

if len(positional) > 1:
    reject("too many positional arguments: " + json.dumps(positional))
target = positional[0] if positional else None
if target != expect_target:
    reject(
        "unexpected target: " + json.dumps(target)
        + " (expected " + json.dumps(expect_target) + ")"
    )
if repo_flag != expect_repo_flag:
    reject(
        "unexpected repo flag: " + json.dumps(repo_flag)
        + " (expected " + json.dumps(expect_repo_flag) + ")"
    )
if not json_fields:
    reject("--json is required")
fields = json_fields.split(",")
unknown = [field for field in fields if field not in payload]
if unknown:
    reject("unknown --json fields: " + ",".join(unknown))

log("ACCEPT " + json.dumps(sys.argv[1:]))
print(json.dumps({field: payload[field] for field in fields}))
'''


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git_dir(work: Path) -> Path:
    value = subprocess.check_output(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=work
    )
    return Path(value.decode().strip())


def _rev_parse(work: Path, rev: str) -> str:
    return (
        subprocess.check_output(["git", "rev-parse", rev], cwd=work)
        .decode()
        .strip()
    )


def _create_feature_repository(temporary: Path) -> Path:
    """merge 対象 PR の head 相当のブランチを持つ一時 repo を作る。

    bare origin を用意し、master へ base commit を push、origin/HEAD を設定した
    うえで feature branch に変更 commit を積んで push する (= すでに remote に
    到達した「レビュー対象 PR」を模す)。
    """
    origin = temporary / "origin.git"
    work = temporary / "work"
    _git(temporary, "init", "--bare", str(origin))
    _git(temporary, "init", str(work))
    _git(work, "config", "user.name", "Marketplace Test")
    _git(work, "config", "user.email", "marketplace@example.invalid")
    (work / "example.txt").write_text("base\n", encoding="utf-8")
    _git(work, "add", "example.txt")
    _git(work, "commit", "-m", "base")
    _git(work, "branch", "-M", "master")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "master")
    _git(work, "remote", "set-head", "origin", "master")
    _git(work, "checkout", "-b", "feature/test")
    (work / "example.txt").write_text("changed\n", encoding="utf-8")
    _git(work, "add", "example.txt")
    _git(work, "commit", "-m", "change")
    _git(work, "push", "-u", "origin", "feature/test")
    return work


def _expected_review_hash(work: Path, base: str = "origin/master") -> str:
    """`compute_review_hash_in` (target_cwd=work) と同じ計算式を独立に
    再実装する。base には PR の実 base branch に対応する remote ref を渡す。
    working tree は常に clean な状態で呼ぶため staged / unstaged 差分は空に
    なり、実質 `head` / `mbase` 束縛行 + merge-base..HEAD の全差分がハッシュ
    入力になる。
    """
    head = _rev_parse(work, "HEAD^{commit}")
    merge_base = (
        subprocess.check_output(["git", "merge-base", base, "HEAD"], cwd=work)
        .decode()
        .strip()
    )
    chunks = [
        f"head {head}\n".encode(),
        f"mbase {merge_base}\n".encode(),
    ]
    for args in (
        ("diff", "--no-ext-diff", "--no-textconv", merge_base, "HEAD"),
        ("diff", "--no-ext-diff", "--no-textconv", "--cached"),
        ("diff", "--no-ext-diff", "--no-textconv"),
    ):
        chunks.append(subprocess.check_output(["git", *args], cwd=work).rstrip(b"\n"))
    return hashlib.sha256(b"".join(chunks)).hexdigest()


def _default_pr_payload(
    *, pr_number: int, head_oid: str, base_oid: str, repo: str
) -> dict[str, object]:
    """gh 2.96.0 の `gh pr view --json` 実応答形 (2026-08-31 実測) に合わせた
    PR metadata。key 集合が fake gh の known field set を兼ねる (gate はこの
    サブセットのみ要求できる)。
    """
    owner, name = repo.split("/", 1)
    return {
        "number": pr_number,
        "headRefOid": head_oid,
        "headRefName": "feature/test",
        "baseRefName": "master",
        "baseRefOid": base_oid,
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "autoMergeRequest": None,
        "isCrossRepository": False,
        "url": f"https://github.com/{repo}/pull/{pr_number}",
        "headRepository": {
            "id": "R_faketest",
            "name": name,
            "nameWithOwner": repo,
        },
        "headRepositoryOwner": {
            "id": "U_faketest",
            "name": owner,
            "login": owner,
        },
    }


def _install_fake_gh(bin_dir: Path) -> None:
    gh_path = bin_dir / "gh"
    gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
    mode = gh_path.stat().st_mode
    gh_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("git") and shutil.which("jq"),
    "gate integration requires bash, git, and jq",
)
class PreMergeCodexGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary.name)
        self.work = _create_feature_repository(temporary_path)

        self.head_oid = _rev_parse(self.work, "HEAD^{commit}")
        self.base_oid = _rev_parse(self.work, "origin/master^{commit}")
        self.merge_base_oid = (
            subprocess.check_output(
                ["git", "merge-base", "origin/master", "HEAD"], cwd=self.work
            )
            .decode()
            .strip()
        )
        self.diff_hash = _expected_review_hash(self.work)
        self.payload = _default_pr_payload(
            pr_number=PR_NUMBER,
            head_oid=self.head_oid,
            base_oid=self.base_oid,
            repo=REPO_NAME_WITH_OWNER,
        )

        self.fake_bin_dir = temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        self.gh_log = self.fake_bin_dir / "gh-calls.log"
        _install_fake_gh(self.fake_bin_dir)
        self.configure_fake_gh(expect_target=str(PR_NUMBER))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def configure_fake_gh(
        self,
        *,
        expect_target: str | None,
        expect_repo_flag: str | None = None,
        payload: dict[str, object] | None = None,
        merge_queue_enabled: bool = False,
        in_merge_queue: bool = False,
        graphql_fail: bool = False,
    ) -> None:
        owner, name = REPO_NAME_WITH_OWNER.split("/", 1)
        config = {
            "payload": payload if payload is not None else self.payload,
            "expect_target": expect_target,
            "expect_repo_flag": expect_repo_flag,
            "graphql_fail": graphql_fail,
            "merge_queue": {
                "isMergeQueueEnabled": merge_queue_enabled,
                "isInMergeQueue": in_merge_queue,
            },
            "graphql_required_tokens": [
                owner,
                name,
                str(PR_NUMBER),
                "isMergeQueueEnabled",
            ],
        }
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def reset_gh_log(self) -> None:
        self.gh_log.unlink(missing_ok=True)

    def run_gate(self, command: str) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        env = os.environ.copy()
        env["PATH"] = f"{self.fake_bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            ["bash", str(GATE)],
            cwd=self.work,
            input=json.dumps(payload).encode("utf-8"),
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def decision(self, result: subprocess.CompletedProcess[bytes]) -> str | None:
        if not result.stdout.strip():
            return None
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["permissionDecision"]

    def deny_reason(self, result: subprocess.CompletedProcess[bytes]) -> str:
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["permissionDecisionReason"]

    def updated_command(self, result: subprocess.CompletedProcess[bytes]) -> str:
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["updatedInput"]["command"]

    def maybe_updated_command(
        self, result: subprocess.CompletedProcess[bytes]
    ) -> str | None:
        response = json.loads(result.stdout)
        updated = response["hookSpecificOutput"].get("updatedInput")
        if updated is None:
            return None
        return updated["command"]

    def expected_injected(self, command: str, head_oid: str | None = None) -> str:
        """`merge` サブコマンドトークン末尾直後に ` --match-head-commit <head>` を
        offset 挿入した期待コマンドを組み立てる (完全一致検証用)。本テストの
        merge コマンドはすべて `gh pr merge` で始まる形のみを使う。
        """
        if head_oid is None:
            head_oid = self.head_oid
        prefix = "gh pr merge"
        self.assertTrue(
            command.startswith(prefix), f"unexpected command shape: {command}"
        )
        return f"{prefix} --match-head-commit {head_oid}{command[len(prefix):]}"

    def assert_fake_gh_consulted(self) -> None:
        self.assertTrue(
            self.gh_log.is_file(),
            "gate は gh pr view で実 PR metadata を取得する契約",
        )
        log_text = self.gh_log.read_text(encoding="utf-8")
        self.assertIn(
            "ACCEPT ", log_text, f"fake gh の受理ログが空です: {log_text!r}"
        )
        self.assertNotIn(
            "REJECT ",
            log_text,
            f"fake gh が想定外の呼び出しを拒否しました: {log_text!r}",
        )

    def write_marker(self, content: str) -> None:
        (_git_dir(self.work) / MARKER_NAME).write_text(content, encoding="utf-8")

    def build_marker(
        self,
        *,
        repo: str = REPO_NAME_WITH_OWNER,
        pr: int = PR_NUMBER,
        merge_base: str | None = None,
        head: str | None = None,
        diff_hash: str | None = None,
    ) -> str:
        if merge_base is None:
            merge_base = self.merge_base_oid
        if head is None:
            head = self.head_oid
        if diff_hash is None:
            diff_hash = self.diff_hash
        return (
            f"repo={repo}\n"
            f"pr={pr}\n"
            f"merge_base={merge_base}\n"
            f"head={head}\n"
            f"diff_hash={diff_hash}\n"
        )

    # ------------------------------------------------------------------
    # (a) gate script の存在
    # ------------------------------------------------------------------

    def test_gate_script_exists(self) -> None:
        self.assertTrue(GATE.is_file(), f"missing gate script: {GATE}")

    # ------------------------------------------------------------------
    # (b) marker 無しで deny。deny 文は subagent namespace と codex への言及を持つ
    # ------------------------------------------------------------------

    def test_denies_merge_without_marker(self) -> None:
        result = self.run_gate(MERGE_COMMAND)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

        reason = self.deny_reason(result)
        self.assertIn(CODEX_SUBAGENT_NAME, reason)
        self.assertIn("codex", reason.lower())

    # ------------------------------------------------------------------
    # (c) --auto は marker の有無に依らず常に deny
    # ------------------------------------------------------------------

    def test_denies_auto_merge_regardless_of_marker(self) -> None:
        with self.subTest(marker="absent"):
            result = self.run_gate(MERGE_AUTO_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(self.decision(result), "deny")

        with self.subTest(marker="present_and_valid"):
            self.write_marker(self.build_marker())
            result = self.run_gate(MERGE_AUTO_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(
                self.decision(result),
                "deny",
                "--auto はサポート外のため有効な marker があっても deny する契約",
            )

        for label, command in {
            "auto_equals_true": f"gh pr merge {PR_NUMBER} --auto=true --merge",
            "auto_equals_false": f"gh pr merge {PR_NUMBER} --auto=false --merge",
        }.items():
            with self.subTest(case=label):
                self.write_marker(self.build_marker())
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(
                    self.decision(result),
                    "deny",
                    f"--auto の = 連結形も値に依らず deny する契約 (case={label})",
                )

    # ------------------------------------------------------------------
    # (c2) 合成コマンド (連結・複数 invocation・前置コマンド) は marker の
    #      有無に依らず deny
    # ------------------------------------------------------------------

    def test_denies_compound_or_prefixed_merge_commands(self) -> None:
        commands = {
            "chained_after": f"{MERGE_COMMAND} && echo done",
            "prefixed": f"cd /tmp && {MERGE_COMMAND}",
            "multiple_invocations": (
                f"{MERGE_COMMAND}; gh pr merge {PR_NUMBER + 1} --merge"
            ),
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                self.write_marker(self.build_marker())
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    # ------------------------------------------------------------------
    # (d) 正しい 5 key marker + PR metadata 一致 → allow + --match-head-commit
    #     挿入 (updatedInput の command は完全一致)
    # ------------------------------------------------------------------

    def test_allows_merge_with_valid_marker_and_injects_match_head_commit(
        self,
    ) -> None:
        self.write_marker(self.build_marker())

        result = self.run_gate(MERGE_COMMAND)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "allow")
        self.assertEqual(
            self.updated_command(result), self.expected_injected(MERGE_COMMAND)
        )
        self.assert_fake_gh_consulted()

    # ------------------------------------------------------------------
    # (d2) 対象指定形 (URL / branch / 省略 / --repo) ごとに、gate は同じ対象
    #      指定を gh pr view へ転送したうえで allow + 挿入する
    # ------------------------------------------------------------------

    def test_allows_merge_for_each_target_form(self) -> None:
        cases: dict[str, tuple[str, str | None, str | None]] = {
            "url": (f"gh pr merge {PR_URL} --merge", PR_URL, None),
            "branch": ("gh pr merge feature/test --merge", "feature/test", None),
            "omitted": ("gh pr merge --merge", None, None),
            "repo_flag": (
                f"gh pr merge {PR_NUMBER} --repo {REPO_NAME_WITH_OWNER} --merge",
                str(PR_NUMBER),
                REPO_NAME_WITH_OWNER,
            ),
            "repo_flag_short": (
                f"gh pr merge {PR_NUMBER} -R {REPO_NAME_WITH_OWNER} --merge",
                str(PR_NUMBER),
                REPO_NAME_WITH_OWNER,
            ),
            "repo_flag_equals": (
                f"gh pr merge {PR_NUMBER} --repo={REPO_NAME_WITH_OWNER} --merge",
                str(PR_NUMBER),
                REPO_NAME_WITH_OWNER,
            ),
            "repo_flag_attached": (
                f"gh pr merge {PR_NUMBER} -R{REPO_NAME_WITH_OWNER} --merge",
                str(PR_NUMBER),
                REPO_NAME_WITH_OWNER,
            ),
        }
        for label, (command, expect_target, expect_repo_flag) in cases.items():
            with self.subTest(case=label):
                self.configure_fake_gh(
                    expect_target=expect_target,
                    expect_repo_flag=expect_repo_flag,
                )
                self.reset_gh_log()
                self.write_marker(self.build_marker())

                result = self.run_gate(command)

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "allow", f"case={label}")
                self.assertEqual(
                    self.updated_command(result), self.expected_injected(command)
                )
                self.assert_fake_gh_consulted()

    # ------------------------------------------------------------------
    # (d3) merge 方式 (--squash / --rebase) と --delete-branch 併用は判定に
    #      影響しない (marker 有効なら allow + 挿入)
    # ------------------------------------------------------------------

    def test_allows_each_merge_strategy_and_delete_branch(self) -> None:
        commands = {
            "squash": f"gh pr merge {PR_NUMBER} --squash",
            "rebase": f"gh pr merge {PR_NUMBER} --rebase",
            "delete_branch": f"gh pr merge {PR_NUMBER} --squash --delete-branch",
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                self.reset_gh_log()
                self.write_marker(self.build_marker())

                result = self.run_gate(command)

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "allow", f"case={label}")
                self.assertEqual(
                    self.updated_command(result), self.expected_injected(command)
                )
                self.assert_fake_gh_consulted()

    # ------------------------------------------------------------------
    # (d4) 非 default base の PR: merge_base / diff_hash の検証は実 PR base
    #      (baseRefName) を基準にする。default branch (origin/master) を
    #      決め打ちする実装はこのケースで deny になり契約違反として検出される
    # ------------------------------------------------------------------

    def test_allows_merge_with_non_default_base_branch(self) -> None:
        _git(self.work, "switch", "master")
        _git(self.work, "switch", "-c", "develop")
        (self.work / "develop.txt").write_text("develop base\n", encoding="utf-8")
        _git(self.work, "add", "develop.txt")
        _git(self.work, "commit", "-m", "develop base")
        _git(self.work, "push", "-u", "origin", "develop")
        _git(self.work, "switch", "-c", "feature/nondefault")
        (self.work / "develop.txt").write_text(
            "changed on feature\n", encoding="utf-8"
        )
        _git(self.work, "add", "develop.txt")
        _git(self.work, "commit", "-m", "feature change")
        _git(self.work, "push", "-u", "origin", "feature/nondefault")

        head_oid = _rev_parse(self.work, "HEAD^{commit}")
        merge_base = (
            subprocess.check_output(
                ["git", "merge-base", "origin/develop", "HEAD"], cwd=self.work
            )
            .decode()
            .strip()
        )
        diff_hash = _expected_review_hash(self.work, base="origin/develop")
        self.assertNotEqual(
            merge_base,
            self.merge_base_oid,
            "develop 基準の merge-base が default base 基準と一致すると"
            "このテストは base 決め打ち実装を検出できない",
        )

        payload = dict(self.payload)
        payload["headRefOid"] = head_oid
        payload["headRefName"] = "feature/nondefault"
        payload["baseRefName"] = "develop"
        payload["baseRefOid"] = _rev_parse(self.work, "origin/develop^{commit}")
        self.configure_fake_gh(expect_target=str(PR_NUMBER), payload=payload)

        self.write_marker(
            self.build_marker(
                merge_base=merge_base, head=head_oid, diff_hash=diff_hash
            )
        )

        result = self.run_gate(MERGE_COMMAND)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "allow")
        self.assertEqual(
            self.updated_command(result),
            self.expected_injected(MERGE_COMMAND, head_oid=head_oid),
        )
        self.assert_fake_gh_consulted()

    # ------------------------------------------------------------------
    # (d5) fork 由来 PR (cross-repository): marker の repo は base リポジトリ
    #      と照合する (headRepository への束縛は fork PR で誤動作する)
    # ------------------------------------------------------------------

    def test_allows_merge_for_cross_repository_pull_request(self) -> None:
        payload = dict(self.payload)
        payload["isCrossRepository"] = True
        payload["headRepository"] = {
            "id": "R_fakefork",
            "name": "test-repo",
            "nameWithOwner": "fork-owner/test-repo",
        }
        payload["headRepositoryOwner"] = {
            "id": "U_fakefork",
            "name": "fork-owner",
            "login": "fork-owner",
        }
        self.configure_fake_gh(expect_target=str(PR_NUMBER), payload=payload)
        self.write_marker(self.build_marker())

        result = self.run_gate(MERGE_COMMAND)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "allow")
        self.assertEqual(
            self.updated_command(result), self.expected_injected(MERGE_COMMAND)
        )
        self.assert_fake_gh_consulted()

    # ------------------------------------------------------------------
    # (e) marker 5 key の各 1 key 不一致はそれぞれ単独で deny を導く
    # ------------------------------------------------------------------

    def test_denies_merge_when_any_marker_key_mismatches(self) -> None:
        cases = {
            "repo_mismatch": self.build_marker(repo="other-owner/other-repo"),
            "pr_mismatch": self.build_marker(pr=PR_NUMBER + 1),
            # merge_base_mismatch: 実在するが現在の merge-base とは異なる commit
            # (head commit の OID) を束縛する。
            "merge_base_mismatch": self.build_marker(merge_base=self.head_oid),
            # head_mismatch: 実在するが現在の branch HEAD とは異なる commit
            # (base commit の OID) を束縛する。
            "head_mismatch": self.build_marker(head=self.base_oid),
            "diff_hash_mismatch": self.build_marker(
                diff_hash=hashlib.sha256(b"tampered").hexdigest()
            ),
        }
        for label, marker in cases.items():
            with self.subTest(case=label):
                self.write_marker(marker)
                result = self.run_gate(MERGE_COMMAND)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    # ------------------------------------------------------------------
    # (e2) 対象省略形での pr 不一致: marker の pr は gh 応答の number と
    #      照合するしかない (コマンド文字列からの推定を排除する)
    # ------------------------------------------------------------------

    def test_denies_marker_pr_mismatch_with_omitted_target(self) -> None:
        self.configure_fake_gh(expect_target=None)
        self.write_marker(self.build_marker(pr=PR_NUMBER + 1))

        result = self.run_gate("gh pr merge --merge")

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

    # ------------------------------------------------------------------
    # (f) 既存 --match-head-commit 指定: レビュー済み head と一致すれば
    #     allow + 無変更、不一致の OID なら deny
    # ------------------------------------------------------------------

    def test_allows_existing_match_head_commit_bound_to_reviewed_head(
        self,
    ) -> None:
        commands = {
            "separate": (
                f"gh pr merge {PR_NUMBER} --merge "
                f"--match-head-commit {self.head_oid}"
            ),
            "equals": (
                f"gh pr merge {PR_NUMBER} --merge "
                f"--match-head-commit={self.head_oid}"
            ),
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                self.reset_gh_log()
                self.write_marker(self.build_marker())

                result = self.run_gate(command)

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "allow", f"case={label}")
                self.assertIsNone(
                    self.maybe_updated_command(result),
                    "既にレビュー済み head を指定するコマンドは書き換えない契約",
                )

    # ------------------------------------------------------------------
    # (g) merge queue 必須の base への merge は marker の有無に依らず deny
    #     (gh は --auto なしでも遅延実行に倒すため、--auto と同じ理由)
    # ------------------------------------------------------------------

    def test_denies_merge_when_base_requires_merge_queue(self) -> None:
        self.configure_fake_gh(
            expect_target=str(PR_NUMBER), merge_queue_enabled=True
        )

        with self.subTest(marker="absent"):
            result = self.run_gate(MERGE_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(self.decision(result), "deny")

        with self.subTest(marker="present_and_valid"):
            self.write_marker(self.build_marker())
            result = self.run_gate(MERGE_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(
                self.decision(result),
                "deny",
                "merge queue 必須 branch への merge は遅延実行になるため"
                "有効な marker があっても deny する契約",
            )

        with self.subTest(marker="present_and_valid", command="admin_bypass"):
            self.write_marker(self.build_marker())
            result = self.run_gate(f"{MERGE_COMMAND} --admin")
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(
                self.decision(result),
                "deny",
                "merge queue を --admin でバイパスする merge も deny する契約",
            )

    def test_denies_merge_when_merge_queue_state_is_unavailable(self) -> None:
        self.configure_fake_gh(expect_target=str(PR_NUMBER), graphql_fail=True)
        self.write_marker(self.build_marker())

        result = self.run_gate(MERGE_COMMAND)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(
            self.decision(result),
            "deny",
            "merge queue 状態の取得失敗は fail-closed に deny する契約",
        )

    def test_denies_existing_match_head_commit_bound_to_unreviewed_oid(
        self,
    ) -> None:
        commands = {
            "separate": (
                f"gh pr merge {PR_NUMBER} --merge "
                f"--match-head-commit {self.base_oid}"
            ),
            "equals": (
                f"gh pr merge {PR_NUMBER} --merge "
                f"--match-head-commit={self.base_oid}"
            ),
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                self.write_marker(self.build_marker())
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    def test_denies_duplicated_match_head_commit(self) -> None:
        commands = {
            "both_reviewed": (
                f"gh pr merge {PR_NUMBER} --merge "
                f"--match-head-commit {self.head_oid} "
                f"--match-head-commit {self.head_oid}"
            ),
            "last_unreviewed": (
                f"gh pr merge {PR_NUMBER} --merge "
                f"--match-head-commit {self.head_oid} "
                f"--match-head-commit={self.base_oid}"
            ),
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                self.write_marker(self.build_marker())
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(
                    self.decision(result),
                    "deny",
                    f"複数出現は値の一致に依らず deny する契約 (case={label})",
                )


if __name__ == "__main__":
    unittest.main()

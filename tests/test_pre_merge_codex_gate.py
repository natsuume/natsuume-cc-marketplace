"""pre-merge-codex-review の軽量 merge gate 契約テスト (Phase A: spec-first, red)。

gate (`plugins/pre-merge-codex-review/hooks/scripts/block-pre-merge.sh`) は
Phase A 時点でまだ存在しない。全テストは gate が実装されるまで意図した失敗で
red になる。

固定する契約:

- gate は Bash コマンド文字列に `gh pr merge` の連続列を含む場合のみ関与する。
  含まないコマンドには関与しない (無出力)。連続列の検出は粗い文字列判定で
  よく、フラグ文法の解析や invocation の厳密な分類は行わない (quoted な言及
  等で誤爆した場合はコマンドの言い換えで回避できる、cooperative 利用前提)。
- 関与したコマンドに `--auto` または `--admin` の文字列を含む場合は deny する
  (遅延 merge 予約・保護 bypass はサポート外。粗い文字列検出でよく、
  レビューコメントの有無に依らない)。
- それ以外の関与コマンドでは、merge 対象 PR の現在の head SHA を gh で取得し、
  PR 上のレビューコメントに機械可読 header
  `<!-- codex-review: head=<full head SHA> status=pass|findings -->`
  を持ち、head SHA が現在の head と完全一致するものが存在するかを確認する:
  - 存在すれば無出力で終了する (gate は decision を出さず、既定の許可フロー
    に委ねる)
  - 存在しなければ deny し、`pre-merge-codex-review:codex-reviewer` subagent
    の実行を案内する
- status は pass / findings のどちらでも「レビュー済み」として成立する
  (merge の approve や findings 0 件の証明ではない)。
- 対象 PR の解決と head SHA の取得は gh に委ねる。gh / jq が見つからない・
  PR の解決や取得に失敗した・head SHA が得られない場合はすべて deny する
  (fail-closed)。merge と無関係な Bash 呼び出しには関与しない。
- gate は permissionDecision として deny 以外を出さない (allow / updatedInput
  を出さない。通過時は無出力で既定の許可フローに委ねる)。

テストは一時ディレクトリに fake `gh` を置き PATH の先頭に通す。fake は呼び出し
引数に依らず、設定された head SHA と PR レビュー一覧を含む JSON を返す寛容な
実装であり、gate がどの gh 呼び出し形を採るかは Phase B の実装裁量とする
(検証するのは「gh から得た実状態と照合する」という契約のみ)。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "plugins" / "pre-merge-codex-review" / "hooks" / "scripts"
GATE = SCRIPTS_DIR / "block-pre-merge.sh"
WRAPPER_GUARD = SCRIPTS_DIR / "block-bg-codex-wrapper.sh"

CODEX_SUBAGENT_NAME = "pre-merge-codex-review:codex-reviewer"

PR_NUMBER = 123
HEAD_SHA = "1234567890abcdef1234567890abcdef12345678"
OTHER_SHA = "fedcba0987654321fedcba0987654321fedcba09"

MERGE_COMMAND = f"gh pr merge {PR_NUMBER} --squash"

# 寛容 fake gh: 引数に依らず gh-config.json の payload (headRefOid + reviews) を
# 返す。graphql_fail 相当の失敗注入は fail フラグで行う。
FAKE_GH_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "gh-config.json"), encoding="utf-8") as fp:
    config = json.load(fp)

if config.get("fail"):
    print("fake-gh: injected failure", file=sys.stderr)
    sys.exit(1)

print(json.dumps(config["payload"]))
"""


def codex_review_comment(head_sha: str, status: str = "pass") -> str:
    """wrapper が投稿する PR レビュー本文の形 (機械可読 header + report)。"""
    return (
        f"<!-- codex-review: head={head_sha} status={status} -->\n"
        "# Codex Review\n\nStatus: "
        f"{status}\n"
    )


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq"),
    "gate integration requires bash and jq",
)
class PreMergeCodexGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary.name)
        self.work = temporary_path / "work"
        self.work.mkdir()

        self.fake_bin_dir = temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        gh_path = self.fake_bin_dir / "gh"
        gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        self.configure_fake_gh(review_bodies=[])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def configure_fake_gh(
        self,
        *,
        review_bodies: list[str],
        head_oid: str | None = HEAD_SHA,
        fail: bool = False,
    ) -> None:
        payload: dict[str, object] = {
            "reviews": [{"body": body} for body in review_bodies],
        }
        if head_oid is not None:
            payload["headRefOid"] = head_oid
        config = {"payload": payload, "fail": fail}
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def run_gate(self, command: str) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(self.work),
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

    # ------------------------------------------------------------------
    # (a) hook script の存在 (Phase A 骨格契約)
    # ------------------------------------------------------------------

    def test_gate_script_exists(self) -> None:
        self.assertTrue(GATE.is_file(), f"missing gate script: {GATE}")

    def test_wrapper_guard_script_exists(self) -> None:
        self.assertTrue(
            WRAPPER_GUARD.is_file(), f"missing wrapper guard: {WRAPPER_GUARD}"
        )

    # ------------------------------------------------------------------
    # (b) 関与条件: `gh pr merge` の連続列を含む場合のみ
    # ------------------------------------------------------------------

    def test_ignores_commands_without_merge_sequence(self) -> None:
        commands = {
            "unrelated": "git status",
            "gh_readonly": "gh pr view 123 --json state",
            "merge_named_field": "gh pr list --json mergeable,mergeStateStatus",
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIsNone(self.decision(result), f"case={label}")

    # ------------------------------------------------------------------
    # (c) --auto / --admin はレビューコメントの有無に依らず deny
    # ------------------------------------------------------------------

    def test_denies_auto_and_admin_regardless_of_review_comment(self) -> None:
        self.configure_fake_gh(review_bodies=[codex_review_comment(HEAD_SHA)])
        commands = {
            "auto": f"gh pr merge {PR_NUMBER} --auto --squash",
            "auto_equals": f"gh pr merge {PR_NUMBER} --auto=true --squash",
            "admin": f"gh pr merge {PR_NUMBER} --squash --admin",
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    # ------------------------------------------------------------------
    # (d) 現 head SHA に一致する codex-review コメントが無ければ deny。
    #     deny 文は subagent namespace と codex への言及を持つ
    # ------------------------------------------------------------------

    def test_denies_merge_without_matching_review_comment(self) -> None:
        cases = {
            "no_comments": [],
            "different_head": [codex_review_comment(OTHER_SHA)],
            "plain_comment_without_header": [
                "LGTM! (通常のレビューコメント。codex-review header なし)"
            ],
        }
        for label, review_bodies in cases.items():
            with self.subTest(case=label):
                self.configure_fake_gh(review_bodies=review_bodies)
                result = self.run_gate(MERGE_COMMAND)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")
                reason = self.deny_reason(result)
                self.assertIn(CODEX_SUBAGENT_NAME, reason)
                self.assertIn("codex", reason.lower())

    # ------------------------------------------------------------------
    # (e) 一致するコメントが在れば無出力 (既定の許可フローへ)。
    #     status は pass / findings のどちらでも成立する
    # ------------------------------------------------------------------

    def test_passes_through_merge_with_matching_review_comment(self) -> None:
        cases = {
            "status_pass": [codex_review_comment(HEAD_SHA, status="pass")],
            "status_findings": [
                codex_review_comment(HEAD_SHA, status="findings")
            ],
            "mixed_with_other_comments": [
                "先行する通常コメント",
                codex_review_comment(OTHER_SHA),
                codex_review_comment(HEAD_SHA),
            ],
        }
        for label, review_bodies in cases.items():
            with self.subTest(case=label):
                self.configure_fake_gh(review_bodies=review_bodies)
                result = self.run_gate(MERGE_COMMAND)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIsNone(
                    self.decision(result),
                    f"一致コメントがあれば decision を出さない契約 (case={label})",
                )

    # ------------------------------------------------------------------
    # (f) fail-closed: gh の失敗・head SHA の欠落は deny
    # ------------------------------------------------------------------

    def test_denies_merge_when_gh_fails(self) -> None:
        self.configure_fake_gh(review_bodies=[], fail=True)
        result = self.run_gate(MERGE_COMMAND)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

    def test_denies_merge_when_head_sha_is_unavailable(self) -> None:
        self.configure_fake_gh(
            review_bodies=[codex_review_comment(HEAD_SHA)], head_oid=None
        )
        result = self.run_gate(MERGE_COMMAND)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")


class PreMergeGateMissingDependencyTest(unittest.TestCase):
    """必須依存 (jq / gh) が見つからない環境では merge コマンドを fail-closed に
    deny し、無関係な Bash 呼び出しには関与しない契約。"""

    def _run_gate(
        self, path_value: str, command: str
    ) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        bash = shutil.which("bash")
        assert bash is not None
        env = os.environ.copy()
        env["PATH"] = path_value
        return subprocess.run(
            [bash, str(GATE)],
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            env=env,
        )

    def _minimal_shims(self, work: Path) -> Path | None:
        cat = shutil.which("cat")
        dirname = shutil.which("dirname")
        if shutil.which("bash") is None or cat is None or dirname is None:
            return None
        shims = work / "bin"
        shims.mkdir()
        (shims / "cat").symlink_to(cat)
        (shims / "dirname").symlink_to(dirname)
        return shims

    def test_merge_is_denied_without_jq(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            shims = self._minimal_shims(Path(name))
            if shims is None:
                self.skipTest("requires bash, cat, and dirname")
            result = self._run_gate(str(shims), MERGE_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            response = json.loads(result.stdout)
            output = response["hookSpecificOutput"]
            self.assertEqual(output["permissionDecision"], "deny")
            self.assertIn("jq", output["permissionDecisionReason"])

    def test_unrelated_command_passes_without_jq(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            shims = self._minimal_shims(Path(name))
            if shims is None:
                self.skipTest("requires bash, cat, and dirname")
            result = self._run_gate(str(shims), "printf hello")
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"")

    def test_merge_is_denied_without_gh(self) -> None:
        """gh 以外の全コマンドが見える PATH (symlink farm) でも、gh が無ければ
        merge は deny される。"""
        if not (
            shutil.which("bash") and shutil.which("git") and shutil.which("jq")
        ):
            self.skipTest("requires bash, git, and jq")
        with tempfile.TemporaryDirectory() as name:
            shims = Path(name) / "bin"
            shims.mkdir()
            seen: set[str] = set()
            for directory in os.environ.get("PATH", "").split(os.pathsep):
                candidate = Path(directory)
                if not candidate.is_dir():
                    continue
                for entry in candidate.iterdir():
                    if entry.name in seen or entry.name == "gh":
                        continue
                    try:
                        if entry.is_file() and os.access(entry, os.X_OK):
                            (shims / entry.name).symlink_to(entry)
                            seen.add(entry.name)
                    except OSError:
                        continue
            result = self._run_gate(str(shims), MERGE_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            response = json.loads(result.stdout)
            output = response["hookSpecificOutput"]
            self.assertEqual(output["permissionDecision"], "deny")
            self.assertIn("gh", output["permissionDecisionReason"])


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq"),
    "gate integration requires bash and jq",
)
class PreMergeGateTargetResolutionTest(unittest.TestCase):
    """merge 対象 PR の解決契約 (fail-closed)。

    gate はフラグ文法を解析しないため、「`gh pr merge` の直後に置かれた対象指定」
    だけを受理し、それ以外の曖昧な形 (フラグ先行の位置に現れる非フラグトークン・
    複数の `gh pr merge` 連続列) は照合対象を一意に決められないものとして deny する。
    フラグのみの形は従来どおり current branch 解決 (gh 委譲) に委ねる。

    行継続 `\\<改行>` は bash が **削除** して前後のトークンを連結するため、gate も
    削除して連続列を検出する (空白への置換では `gh pr me\\<改行>rge` を取りこぼす)。
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary.name)
        self.work = temporary_path / "work"
        self.work.mkdir()

        self.fake_bin_dir = temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        gh_path = self.fake_bin_dir / "gh"
        gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        # 既定では 「現 head SHA に一致するレビューコメントが在る」 状態にする。
        # 対象解決の失敗が 「レビュー済みでも deny される」 ことを示すため。
        self.configure_fake_gh(review_bodies=[codex_review_comment(HEAD_SHA)])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def configure_fake_gh(self, *, review_bodies: list[str]) -> None:
        config = {
            "payload": {
                "headRefOid": HEAD_SHA,
                "reviews": [{"body": body} for body in review_bodies],
            },
            "fail": False,
        }
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def run_gate(self, command: str) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(self.work),
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

    def test_denies_non_flag_token_after_flags(self) -> None:
        """フラグ先行形は、対象指定かフラグの値かを判別できないため deny。"""
        cases = {
            "squash_then_number": f"gh pr merge --squash {PR_NUMBER}",
            "flag_value_shape": f"gh pr merge --body text {PR_NUMBER}",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    def test_denies_multiple_merge_sequences(self) -> None:
        """1 呼び出しに複数の merge があると照合対象が一意に決まらないため deny。"""
        cases = {
            "and_chain": (
                f"gh pr merge {PR_NUMBER} --squash && gh pr merge 456 --squash"
            ),
            "semicolon_chain": (
                f"gh pr merge {PR_NUMBER} --squash; gh pr merge 456 --squash"
            ),
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    def test_denies_branch_name_target(self) -> None:
        """PR 番号にも URL にも解釈できない対象指定は deny。"""
        result = self.run_gate("gh pr merge my-feature-branch --squash")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

    def test_line_continuation_inside_merge_sequence_is_detected(self) -> None:
        """行継続で分断された `gh pr merge` も連続列として検出する。"""
        self.configure_fake_gh(review_bodies=[])
        command = f"gh pr me\\\nrge {PR_NUMBER} --squash"
        result = self.run_gate(command)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

    def test_line_continuation_form_passes_with_matching_comment(self) -> None:
        """行継続形でも、一致コメントが在れば decision を出さない。"""
        command = f"gh pr me\\\nrge {PR_NUMBER} --squash"
        result = self.run_gate(command)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIsNone(self.decision(result))

    def test_canonical_forms_are_accepted(self) -> None:
        """受理正規形 (`gh pr merge [<number>] [flags]`) は照合フローへ進み、
        一致コメントが在れば decision を出さない。"""
        cases = {
            "bare": "gh pr merge",
            "flag_only": "gh pr merge --squash",
            "number_only": f"gh pr merge {PR_NUMBER}",
            "number_and_flags": (
                f"gh pr merge {PR_NUMBER} --squash --delete-branch"
            ),
            "short_flag": f"gh pr merge {PR_NUMBER} -d",
            "flag_with_value": (
                f"gh pr merge {PR_NUMBER} --match-head-commit={HEAD_SHA}"
            ),
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIsNone(self.decision(result), f"case={label}")

    def test_trailing_separator_forms_are_denied(self) -> None:
        """後続コマンドとの連結は正規形に一致しないため deny する。"""
        cases = {
            "and_chain": "gh pr merge --squash && echo merged",
            "semicolon_chain": "gh pr merge --squash; git switch master",
            "number_then_semicolon": (
                f"gh pr merge {PR_NUMBER}; git switch master"
            ),
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq"),
    "gate integration requires bash and jq",
)
class PreMergeGateRepoScopeTest(unittest.TestCase):
    """照合 repo の一本化契約 (fail-closed)。

    gate は hook プロセスの cwd の repo 文脈でレビューコメントを照合するため、実際に
    merge される repo がそこから動きうる形 (repo selector 付き・前置コマンド連結・
    URL 指定) はレビューコメントの有無に依らず deny する。リダイレクトは引数ではない
    ため走査対象から除外し、対象指定なしの merge として current branch 解決に委ねる。
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary.name)
        self.work = temporary_path / "work"
        self.work.mkdir()

        self.fake_bin_dir = temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        gh_path = self.fake_bin_dir / "gh"
        gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        # 現 head SHA に一致するレビューコメントが在る状態を既定にする
        # (repo スコープ違反が 「レビュー済みでも deny される」 ことを示すため)。
        config = {
            "payload": {
                "headRefOid": HEAD_SHA,
                "reviews": [{"body": codex_review_comment(HEAD_SHA)}],
            },
            "fail": False,
        }
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_gate(self, command: str) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(self.work),
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

    def test_denies_repo_selector(self) -> None:
        """repo selector 付き merge は別 repo の PR を merge しうるため deny。"""
        cases = {
            "short": f"gh pr merge {PR_NUMBER} -R owner/other",
            "long": f"gh pr merge {PR_NUMBER} --repo owner/other",
            "long_equals": f"gh pr merge {PR_NUMBER} --repo=owner/other",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    def test_denies_command_prefix_before_merge(self) -> None:
        """前置コマンド連結・環境変数前置きは merge 対象 repo が cwd と食い違いうるため deny。"""
        cases = {
            "cd_prefix": f"cd other-repo; gh pr merge {PR_NUMBER} --squash",
            "and_prefix": f"cd other-repo && gh pr merge {PR_NUMBER} --squash",
            "env_prefix": f"GH_TOKEN=x gh pr merge {PR_NUMBER} --squash",
            "quoted_mention": f'echo "gh pr merge {PR_NUMBER}"',
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    def test_denies_url_target(self) -> None:
        """URL 指定は別 repo を指しうるため deny (対象は同 repo 内の番号のみ)。"""
        cases = {
            "https": (
                "gh pr merge https://github.com/owner/other/pull/123 --squash"
            ),
            "http": "gh pr merge http://github.com/owner/other/pull/123",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    def test_non_canonical_forms_are_denied(self) -> None:
        """正規形に一致しない関与形は、gate の解釈と shell 実挙動が乖離しうるため
        レビューコメントの有無に依らず一律 deny する。"""
        cases = {
            "redirect_stdout": "gh pr merge --squash > /tmp/merge.log",
            "redirect_attached": "gh pr merge --squash >/tmp/merge.log",
            "redirect_stderr": "gh pr merge --squash 2>&1",
            "redirect_multi_digit_fd": "gh pr merge --squash 10> /tmp/m.log",
            "redirect_append_fd": "gh pr merge --squash 22>>/tmp/m.log",
            "redirect_input": "gh pr merge --squash </tmp/in.txt",
            "number_with_redirect": (
                f"gh pr merge {PR_NUMBER} --squash > /tmp/m.log"
            ),
            "pipe_chain": f"gh pr merge {PR_NUMBER} | tee /tmp/merge.log",
            "background": f"gh pr merge {PR_NUMBER} &",
            "quoted_flag_value": 'gh pr merge --subject "merge it"',
            "single_quoted": "gh pr merge --subject 'merge it'",
            "command_substitution": "gh pr merge $(cat /tmp/pr-number)",
            "variable_expansion": "gh pr merge $PR_NUMBER --squash",
            "two_numbers": f"gh pr merge {PR_NUMBER} 456",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq"),
    "gate integration requires bash and jq",
)
class PreMergeGatePayloadCwdTest(unittest.TestCase):
    """照合を実行する repo の決定契約。

    Bash tool の cwd は tool 呼び出しをまたいで持続するため、hook プロセス自身の cwd が
    merge 実行位置と一致するとは限らない。hook payload の `cwd` があればその位置で gh を
    実行し、無い場合のみ自プロセスの cwd で実行する。payload の `cwd` が存在しない
    ディレクトリを指す場合は、どの repo を照合すべきか決められないため deny する。
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary.name)
        self.work = temporary_path / "work"
        self.work.mkdir()
        self.other_repo = temporary_path / "other-repo"
        self.other_repo.mkdir()

        self.fake_bin_dir = temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        gh_path = self.fake_bin_dir / "gh"
        gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        self.configure_fake_gh(review_bodies=[codex_review_comment(HEAD_SHA)])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def configure_fake_gh(self, *, review_bodies: list[str]) -> None:
        config = {
            "payload": {
                "headRefOid": HEAD_SHA,
                "reviews": [{"body": body} for body in review_bodies],
            },
            "fail": False,
        }
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def run_gate(
        self, command: str, payload_cwd: str | None
    ) -> subprocess.CompletedProcess[bytes]:
        payload: dict[str, object] = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        if payload_cwd is not None:
            payload["cwd"] = payload_cwd
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

    def test_payload_cwd_is_used_for_matching(self) -> None:
        """payload の cwd が別ディレクトリでも、そこで gh を実行して照合する。"""
        result = self.run_gate(MERGE_COMMAND, str(self.other_repo))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIsNone(self.decision(result))

    def test_payload_cwd_without_matching_comment_denies(self) -> None:
        """payload cwd 指定時も照合は実行され、一致コメントが無ければ deny する。"""
        self.configure_fake_gh(review_bodies=[])
        result = self.run_gate(MERGE_COMMAND, str(self.other_repo))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

    def test_missing_payload_cwd_denies(self) -> None:
        """payload に cwd が無ければ照合対象 repo を決められないため deny する
        (hook プロセスの cwd への fallback は持たない)。"""
        result = self.run_gate(MERGE_COMMAND, None)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

    def test_relative_payload_cwd_denies(self) -> None:
        """payload cwd が絶対パスでなければ照合対象 repo を特定できないため deny。"""
        result = self.run_gate(MERGE_COMMAND, "other-repo")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

    def test_nonexistent_payload_cwd_denies(self) -> None:
        """payload cwd が存在しなければ照合対象 repo を決められないため deny。"""
        missing = str(self.other_repo / "does-not-exist")
        result = self.run_gate(MERGE_COMMAND, missing)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq"),
    "gate integration requires bash and jq",
)
class PreMergeGateHeaderAnchorTest(unittest.TestCase):
    """attestation header は本文の先頭行にあるものだけを受理する契約。

    本文の途中に現れる header 形の文字列 (レビュー report がレビュー対象の差分から
    引用したもの等) を受理すると、レビューされていない head SHA への attestation として
    機能してしまう。
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary.name)
        self.work = temporary_path / "work"
        self.work.mkdir()

        self.fake_bin_dir = temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        gh_path = self.fake_bin_dir / "gh"
        gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def configure_fake_gh(self, *, review_bodies: list[str]) -> None:
        config = {
            "payload": {
                "headRefOid": HEAD_SHA,
                "reviews": [{"body": body} for body in review_bodies],
            },
            "fail": False,
        }
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def run_gate(self) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": MERGE_COMMAND},
            "cwd": str(self.work),
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

    def test_header_below_first_line_is_denied(self) -> None:
        """先頭行以外に現れる header は attestation として受理しない。"""
        header = f"<!-- codex-review: head={HEAD_SHA} status=pass -->"
        cases = {
            "quoted_in_report": (
                "# Codex Review\n\n"
                "レビュー対象の差分に次の行が含まれています:\n\n"
                f"    {header}\n"
            ),
            "second_line": f"先行するテキスト\n{header}\n",
            "indented_first_line": f"  {header}\n# Codex Review\n",
        }
        for label, body in cases.items():
            with self.subTest(case=label):
                self.configure_fake_gh(review_bodies=[body])
                result = self.run_gate()
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    def test_header_on_first_line_is_accepted(self) -> None:
        """先頭行の header は従来どおり attestation として成立する。"""
        cases = {
            "pass": codex_review_comment(HEAD_SHA, status="pass"),
            "findings": codex_review_comment(HEAD_SHA, status="findings"),
            "with_quoted_header_in_report": (
                codex_review_comment(HEAD_SHA)
                + "\n<!-- codex-review (quoted): head=other status=pass -->\n"
            ),
        }
        for label, body in cases.items():
            with self.subTest(case=label):
                self.configure_fake_gh(review_bodies=[body])
                result = self.run_gate()
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIsNone(self.decision(result), f"case={label}")


if __name__ == "__main__":
    unittest.main()

"""pre-merge-cross-review の軽量 merge gate 契約テスト。

gate (`plugins/pre-merge-cross-review/hooks/scripts/block-pre-merge.sh`) が固定する契約:

- gate は Bash コマンド文字列に `gh pr merge` の連続列を含む場合のみ関与する。
  含まないコマンドには関与しない (無出力)。連続列の検出は粗い文字列判定でよく、
  フラグ文法の解析や invocation の厳密な分類は行わない (cooperative 利用前提)。
- 関与したコマンドに `--auto` / `--admin` / `--repo` / `-R` の文字列を含む場合は、
  レビュー記録の有無に依らず deny する。受理正規形 `gh pr merge [<number>] [flags...]`
  以外の関与形も deny する。
- それ以外の関与コマンドでは、merge 対象 PR の番号と現在の head SHA を gh
  (`gh pr view --json number,headRefOid`) で取得し、merge 実行 repo (hook payload の
  `cwd`) の git-dir 直下にある final attestation
  (`.claude-pre-merge-codex-reviewed`、2 行 `pr=<番号>` / `head=<40 hex>`) が両方に一致する
  場合だけ無出力で終了する (既定の許可フローに委ねる)。記録が無い・不一致・形式不正は
  deny し、`pre-merge-cross-review:codex-reviewer` の実行を案内する。
- gh / jq が見つからない・PR の解決や取得に失敗した・番号や head SHA が得られない場合は
  すべて deny する (fail-closed)。
- gate は GitHub に何も書かず、PR のレビューやコメントも取得しない (gh は `pr view` で
  `number,headRefOid` を取得するためだけに呼ぶ)。PR 上に codex review 形式のコメントが
  あっても判断材料にしない。
- gate は permissionDecision として deny 以外を出さない。

テストは一時ディレクトリに fake `gh` を置き PATH の先頭に通す。fake gh はすべての呼び出しの
引数を calls.jsonl に記録し、`pr view` には設定された PR 番号・head SHA (とテスト用の
reviews) を返す。
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
SCRIPTS_DIR = ROOT / "plugins" / "pre-merge-cross-review" / "hooks" / "scripts"
GATE = SCRIPTS_DIR / "block-pre-merge.sh"
WRAPPER_GUARD = SCRIPTS_DIR / "block-bg-codex-wrapper.sh"

CODEX_SUBAGENT_NAME = "pre-merge-cross-review:codex-reviewer"

PR_NUMBER = 123
HEAD_SHA = "1234567890abcdef1234567890abcdef12345678"
OTHER_SHA = "fedcba0987654321fedcba0987654321fedcba09"

MERGE_COMMAND = f"gh pr merge {PR_NUMBER} --squash"

# gate がローカル記録を読む場所 (merge 実行 repo の git-dir 直下)。
FINAL_ATTESTATION = ".claude-pre-merge-codex-reviewed"
PENDING_ATTESTATION = ".claude-pre-merge-codex-reviewed.pending"

# fake gh: すべての呼び出しを calls.jsonl に記録する。`pr view` には gh-config.json の
# payload を返し、fail フラグで失敗を注入する。それ以外の呼び出しは記録だけして成功する。
FAKE_GH_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "gh-config.json"), encoding="utf-8") as fp:
    config = json.load(fp)

args = sys.argv[1:]
with open(os.path.join(HERE, "calls.jsonl"), "a", encoding="utf-8") as fp:
    fp.write(json.dumps(args) + "\\n")

if args[:2] == ["pr", "view"]:
    if config.get("fail"):
        print("fake-gh: injected failure", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(config["payload"]))
    sys.exit(0)

sys.exit(0)
"""


def codex_review_comment(head_sha: str, status: str = "pass") -> str:
    """codex review 形式の PR コメント本文 (gate が判断材料にしないことの確認に使う)。"""
    return (
        f"<!-- codex-review: head={head_sha} status={status} -->\n"
        "# Codex Review\n\nStatus: "
        f"{status}\n"
    )


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def initialize_repository(work: Path) -> Path:
    """work に git repo を作り、絶対 git-dir を返す。"""
    work.mkdir(parents=True, exist_ok=True)
    git(work, "init")
    git(work, "config", "user.name", "Marketplace Test")
    git(work, "config", "user.email", "marketplace@example.invalid")
    (work / "example.txt").write_text("base\n", encoding="utf-8")
    git(work, "add", "example.txt")
    git(work, "commit", "-m", "base")
    value = subprocess.check_output(
        ["git", "-C", str(work), "rev-parse", "--absolute-git-dir"]
    )
    return Path(value.decode().strip())


def attestation_content(*, pr: int = PR_NUMBER, head: str = HEAD_SHA) -> str:
    return f"pr={pr}\nhead={head}\n"


class GateHarness(unittest.TestCase):
    """一時 git repo・fake gh・ローカル記録を用意して gate を実行する共通 helper。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temporary_path = Path(self.temporary.name)

        self.work = self.temporary_path / "work"
        self.git_dir = initialize_repository(self.work)

        self.fake_bin_dir = self.temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        gh_path = self.fake_bin_dir / "gh"
        gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        self.configure_fake_gh()

        isolated_tmp = self.temporary_path / "tmp"
        isolated_tmp.mkdir()
        self.gate_env = os.environ.copy()
        self.gate_env["PATH"] = (
            f"{self.fake_bin_dir}{os.pathsep}{self.gate_env.get('PATH', '')}"
        )
        self.gate_env["TMPDIR"] = str(isolated_tmp)

    def configure_fake_gh(
        self,
        *,
        number: int | None = PR_NUMBER,
        head_oid: str | None = HEAD_SHA,
        review_bodies: list[str] | None = None,
        fail: bool = False,
    ) -> None:
        payload: dict[str, object] = {}
        if number is not None:
            payload["number"] = number
        if head_oid is not None:
            payload["headRefOid"] = head_oid
        if review_bodies is not None:
            payload["reviews"] = [{"body": body} for body in review_bodies]
        config = {"payload": payload, "fail": fail}
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def final_attestation_path(self, git_dir: Path | None = None) -> Path:
        return (git_dir or self.git_dir) / FINAL_ATTESTATION

    def write_final_attestation(
        self,
        *,
        pr: int = PR_NUMBER,
        head: str = HEAD_SHA,
        git_dir: Path | None = None,
    ) -> Path:
        path = self.final_attestation_path(git_dir)
        path.write_text(attestation_content(pr=pr, head=head), encoding="utf-8")
        return path

    def run_gate(
        self,
        command: str = MERGE_COMMAND,
        *,
        payload_cwd: Path | str | None = None,
        omit_cwd: bool = False,
        process_cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        payload: dict[str, object] = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        if not omit_cwd:
            payload["cwd"] = str(self.work if payload_cwd is None else payload_cwd)
        result = subprocess.run(
            ["bash", str(GATE)],
            cwd=process_cwd or self.work,
            input=json.dumps(payload).encode("utf-8"),
            env=self.gate_env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def decision(self, result: subprocess.CompletedProcess[bytes]) -> str | None:
        if not result.stdout.strip():
            return None
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["permissionDecision"]

    def deny_reason(self, result: subprocess.CompletedProcess[bytes]) -> str:
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["permissionDecisionReason"]

    def gh_calls(self) -> list[list[str]]:
        log = self.fake_bin_dir / "calls.jsonl"
        if not log.exists():
            return []
        return [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def assert_allowed(self, result: subprocess.CompletedProcess[bytes], label: str = "") -> None:
        self.assertEqual(result.stdout, b"", f"{label}: {result.stdout.decode()}")

    def assert_denied(self, result: subprocess.CompletedProcess[bytes], label: str = "") -> None:
        self.assertEqual(self.decision(result), "deny", label)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
    "gate integration requires bash, jq, and git",
)
class PreMergeCodexGateTest(GateHarness):
    def test_gate_script_exists(self) -> None:
        self.assertTrue(GATE.is_file(), f"missing gate script: {GATE}")

    def test_wrapper_guard_script_exists(self) -> None:
        self.assertTrue(
            WRAPPER_GUARD.is_file(), f"missing wrapper guard: {WRAPPER_GUARD}"
        )

    def test_ignores_commands_without_merge_sequence(self) -> None:
        commands = {
            "unrelated": "git status",
            "gh_readonly": "gh pr view 123 --json state",
            "merge_named_field": "gh pr list --json mergeable,mergeStateStatus",
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                self.assert_allowed(self.run_gate(command), label)

    def test_denies_auto_and_admin_regardless_of_local_record(self) -> None:
        self.write_final_attestation()
        commands = {
            "auto": f"gh pr merge {PR_NUMBER} --auto --squash",
            "auto_equals": f"gh pr merge {PR_NUMBER} --auto=true --squash",
            "admin": f"gh pr merge {PR_NUMBER} --squash --admin",
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                self.assert_denied(self.run_gate(command), label)

    def test_denies_merge_without_local_record(self) -> None:
        result = self.run_gate()
        self.assert_denied(result)
        reason = self.deny_reason(result)
        self.assertIn(CODEX_SUBAGENT_NAME, reason)
        self.assertIn("codex", reason.lower())
        self.assertIn("ローカル", reason)

    def test_passes_through_merge_with_matching_local_record(self) -> None:
        self.write_final_attestation()
        self.assert_allowed(self.run_gate())

    def test_denies_merge_when_gh_fails(self) -> None:
        self.write_final_attestation()
        self.configure_fake_gh(fail=True)
        self.assert_denied(self.run_gate())

    def test_denies_merge_when_head_sha_is_unavailable(self) -> None:
        self.write_final_attestation()
        self.configure_fake_gh(head_oid=None)
        self.assert_denied(self.run_gate())

    def test_denies_merge_when_pr_number_is_unavailable(self) -> None:
        self.write_final_attestation()
        self.configure_fake_gh(number=None)
        self.assert_denied(self.run_gate())


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
    shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
    "gate integration requires bash, jq, and git",
)
class PreMergeGateTargetResolutionTest(GateHarness):
    """merge 対象 PR の解決契約 (fail-closed)。

    gate はフラグ文法を解析しないため、「`gh pr merge` の直後に置かれた対象指定」
    だけを受理し、それ以外の曖昧な形 (フラグ先行の位置に現れる非フラグトークン・
    複数の `gh pr merge` 連続列) は照合対象を一意に決められないものとして deny する。
    フラグのみの形は current branch 解決 (gh 委譲) に委ねる。

    行継続 `\\<改行>` は bash が **削除** して前後のトークンを連結するため、gate も
    削除して連続列を検出する (空白への置換では `gh pr me\\<改行>rge` を取りこぼす)。
    """

    def setUp(self) -> None:
        super().setUp()
        # 既定では一致するローカル記録が在る状態にする。対象解決の失敗が
        # 「レビュー済みでも deny される」ことを示すため。
        self.write_final_attestation()

    def test_denies_non_flag_token_after_flags(self) -> None:
        cases = {
            "squash_then_number": f"gh pr merge --squash {PR_NUMBER}",
            "flag_value_shape": f"gh pr merge --body text {PR_NUMBER}",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assert_denied(self.run_gate(command), label)

    def test_denies_multiple_merge_sequences(self) -> None:
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
                self.assert_denied(self.run_gate(command), label)

    def test_denies_branch_name_target(self) -> None:
        self.assert_denied(self.run_gate("gh pr merge my-feature-branch --squash"))

    def test_line_continuation_inside_merge_sequence_is_detected(self) -> None:
        """行継続で分断された `gh pr merge` も連続列として検出する。"""
        self.final_attestation_path().unlink()
        command = f"gh pr me\\\nrge {PR_NUMBER} --squash"
        self.assert_denied(self.run_gate(command))

    def test_line_continuation_form_passes_with_matching_record(self) -> None:
        command = f"gh pr me\\\nrge {PR_NUMBER} --squash"
        self.assert_allowed(self.run_gate(command))

    def test_canonical_forms_are_accepted(self) -> None:
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
                self.assert_allowed(self.run_gate(command), label)

    def test_single_char_short_flags_are_accepted(self) -> None:
        cases = {
            "delete_branch": f"gh pr merge {PR_NUMBER} -d",
            "squash": f"gh pr merge {PR_NUMBER} -s",
            "multiple_single_char": f"gh pr merge {PR_NUMBER} -s -d",
            "no_target": "gh pr merge -s",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assert_allowed(self.run_gate(command), label)

    def test_bundled_short_flags_are_denied(self) -> None:
        """短フラグの束ね形は deny する (束の中に repo selector を隠せるため)。"""
        cases = {
            "bundle_with_repo_and_value": (
                f"gh pr merge {PR_NUMBER} -dR owner/other"
            ),
            "bundle_with_repo": f"gh pr merge {PR_NUMBER} -sR",
            "bundle_without_repo": f"gh pr merge {PR_NUMBER} -sd",
            "bundle_no_target": "gh pr merge -dR owner/other",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assert_denied(self.run_gate(command), label)

    def test_edge_separators_are_trimmed(self) -> None:
        cases = {
            "trailing_newline": f"gh pr merge {PR_NUMBER} --squash\n",
            "leading_newline": f"\ngh pr merge {PR_NUMBER} --squash",
            "surrounding_blank_lines": (
                f"\n\ngh pr merge {PR_NUMBER} --squash\n\n"
            ),
            "surrounding_spaces": f"  gh pr merge {PR_NUMBER} --squash  ",
            "trailing_semicolon": f"gh pr merge {PR_NUMBER} --squash;",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assert_allowed(self.run_gate(command), label)

    def test_trailing_separator_forms_are_denied(self) -> None:
        cases = {
            "and_chain": "gh pr merge --squash && echo merged",
            "semicolon_chain": "gh pr merge --squash; git switch master",
            "number_then_semicolon": (
                f"gh pr merge {PR_NUMBER}; git switch master"
            ),
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assert_denied(self.run_gate(command), label)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
    "gate integration requires bash, jq, and git",
)
class PreMergeGateRepoScopeTest(GateHarness):
    """照合 repo の一本化契約 (fail-closed)。

    gate は hook payload の `cwd` の repo 文脈で PR とローカル記録を照合するため、
    実際に merge される repo がそこから動きうる形 (repo selector 付き・前置コマンド連結・
    URL 指定) はローカル記録の有無に依らず deny する。
    """

    def setUp(self) -> None:
        super().setUp()
        self.write_final_attestation()

    def test_denies_repo_selector(self) -> None:
        cases = {
            "short": f"gh pr merge {PR_NUMBER} -R owner/other",
            "long": f"gh pr merge {PR_NUMBER} --repo owner/other",
            "long_equals": f"gh pr merge {PR_NUMBER} --repo=owner/other",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assert_denied(self.run_gate(command), label)

    def test_denies_command_prefix_before_merge(self) -> None:
        cases = {
            "cd_prefix": f"cd other-repo; gh pr merge {PR_NUMBER} --squash",
            "and_prefix": f"cd other-repo && gh pr merge {PR_NUMBER} --squash",
            "env_prefix": f"GH_TOKEN=x gh pr merge {PR_NUMBER} --squash",
            "quoted_mention": f'echo "gh pr merge {PR_NUMBER}"',
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assert_denied(self.run_gate(command), label)

    def test_denies_url_target(self) -> None:
        cases = {
            "https": (
                "gh pr merge https://github.com/owner/other/pull/123 --squash"
            ),
            "http": "gh pr merge http://github.com/owner/other/pull/123",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assert_denied(self.run_gate(command), label)

    def test_non_canonical_forms_are_denied(self) -> None:
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
                self.assert_denied(self.run_gate(command), label)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
    "gate integration requires bash, jq, and git",
)
class PreMergeGatePayloadCwdTest(GateHarness):
    """照合を実行する repo の決定契約。

    hook payload の `cwd` がある場合はその位置の repo で gh を実行し、その git-dir の
    ローカル記録を検証する。hook プロセス自身の cwd への fallback は持たず、payload の
    `cwd` が欠落・非絶対パス・不在の場合は deny する。
    """

    def setUp(self) -> None:
        super().setUp()
        self.other_repo = self.temporary_path / "other-repo"
        self.other_git_dir = initialize_repository(self.other_repo)

    def test_payload_cwd_record_is_used(self) -> None:
        """payload cwd の repo に記録があれば、hook プロセスの cwd に記録が無くても通す。"""
        self.write_final_attestation(git_dir=self.other_git_dir)
        self.assert_allowed(self.run_gate(payload_cwd=self.other_repo))

    def test_process_cwd_record_is_not_used(self) -> None:
        """hook プロセスの cwd の repo にだけ記録がある場合は deny する。"""
        self.write_final_attestation()
        self.assert_denied(
            self.run_gate(payload_cwd=self.other_repo, process_cwd=self.work)
        )

    def test_missing_payload_cwd_denies(self) -> None:
        self.write_final_attestation()
        self.assert_denied(self.run_gate(omit_cwd=True))

    def test_relative_payload_cwd_denies(self) -> None:
        self.write_final_attestation()
        self.assert_denied(self.run_gate(payload_cwd="other-repo"))

    def test_nonexistent_payload_cwd_denies(self) -> None:
        self.write_final_attestation()
        self.assert_denied(
            self.run_gate(payload_cwd=self.other_repo / "does-not-exist")
        )

    def test_non_repository_payload_cwd_denies(self) -> None:
        plain = self.temporary_path / "plain"
        plain.mkdir()
        self.assert_denied(self.run_gate(payload_cwd=plain))


class PreMergeGateMalformedPayloadTest(unittest.TestCase):
    """payload を解析できない場合の fail-closed 契約。"""

    def run_gate(self, raw_payload: bytes) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(GATE)],
            input=raw_payload,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
        )

    def decision(self, result: subprocess.CompletedProcess[bytes]) -> str | None:
        if not result.stdout.strip():
            return None
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["permissionDecision"]

    @unittest.skipUnless(shutil.which("jq"), "requires jq")
    def test_malformed_payload_with_merge_shape_is_denied(self) -> None:
        cases = {
            "truncated": b'{"tool_input": {"command": "gh pr merge 123"',
            "not_json": b'gh pr merge 123 --squash',
            "trailing_garbage": (
                b'{"tool_input": {"command": "gh pr merge 123"}} }}'
            ),
        }
        for label, raw in cases.items():
            with self.subTest(case=label):
                result = self.run_gate(raw)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    def test_malformed_payload_without_merge_shape_is_ignored(self) -> None:
        result = self.run_gate(b'{"tool_input": {"command": "printf hello"')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
    "gate integration requires bash, jq, and git",
)
class PreMergeGateLocalAttestationTest(GateHarness):
    """ローカルの final attestation の検証契約 (fail-closed)。"""

    def test_matching_record_allows_and_is_kept(self) -> None:
        final = self.write_final_attestation()
        before = final.read_bytes()
        self.assert_allowed(self.run_gate())
        self.assertTrue(final.exists(), "記録は gate が消さない")
        self.assertEqual(final.read_bytes(), before)

    def test_merge_without_pr_number_uses_gh_number(self) -> None:
        """番号なし merge でも、gh が返す PR 番号と記録を照合する。"""
        self.write_final_attestation()
        self.assert_allowed(self.run_gate("gh pr merge --squash"))
        self.write_final_attestation(pr=456)
        self.assert_denied(self.run_gate("gh pr merge --squash"))

    def test_denies_on_pr_number_mismatch(self) -> None:
        self.write_final_attestation(pr=456)
        result = self.run_gate()
        self.assert_denied(result)
        self.assertIn("456", self.deny_reason(result))

    def test_denies_on_head_mismatch(self) -> None:
        self.write_final_attestation(head=OTHER_SHA)
        result = self.run_gate()
        self.assert_denied(result)
        reason = self.deny_reason(result)
        self.assertIn(HEAD_SHA, reason)
        self.assertIn(OTHER_SHA, reason)
        self.assertIn("再レビュー", reason)

    def test_denies_malformed_record(self) -> None:
        cases = {
            "missing_pr_line": f"head={HEAD_SHA}\n",
            "short_head": f"pr={PR_NUMBER}\nhead=deadbeef\n",
            "uppercase_head": f"pr={PR_NUMBER}\nhead={HEAD_SHA.upper()}\n",
            "empty": "",
        }
        for label, content in cases.items():
            with self.subTest(case=label):
                self.final_attestation_path().write_text(content, encoding="utf-8")
                self.assert_denied(self.run_gate(), label)

    def test_denies_with_pending_attestation_only(self) -> None:
        (self.git_dir / PENDING_ATTESTATION).write_text(
            attestation_content(), encoding="utf-8"
        )
        self.assertFalse(self.final_attestation_path().exists())
        self.assert_denied(self.run_gate())

    def test_denies_when_final_attestation_is_symlink(self) -> None:
        target = self.temporary_path / "attestation-target"
        target.write_text(attestation_content(), encoding="utf-8")
        self.final_attestation_path().symlink_to(target)
        self.assert_denied(self.run_gate())

    def test_denies_when_final_attestation_is_directory(self) -> None:
        self.final_attestation_path().mkdir()
        self.assert_denied(self.run_gate())


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
    "gate integration requires bash, jq, and git",
)
class PreMergeGateNoGitHubWriteTest(GateHarness):
    """gate は GitHub に書かず、PR のレビュー・コメントを判断材料にしない契約。"""

    def assert_only_number_and_head_are_requested(self) -> None:
        calls = self.gh_calls()
        self.assertTrue(calls, "gate は gh pr view を呼ぶ")
        for call in calls:
            self.assertEqual(call[:2], ["pr", "view"], call)
            self.assertIn("--json", call)
            fields = call[call.index("--json") + 1].split(",")
            self.assertEqual(sorted(fields), ["headRefOid", "number"], call)

    def test_allow_path_calls_only_pr_view_for_number_and_head(self) -> None:
        self.write_final_attestation()
        self.assert_allowed(self.run_gate())
        self.assert_only_number_and_head_are_requested()

    def test_deny_paths_call_only_pr_view_for_number_and_head(self) -> None:
        cases = {
            "no_record": None,
            "head_mismatch": OTHER_SHA,
        }
        for label, head in cases.items():
            with self.subTest(case=label):
                (self.fake_bin_dir / "calls.jsonl").unlink(missing_ok=True)
                self.final_attestation_path().unlink(missing_ok=True)
                if head is not None:
                    self.write_final_attestation(head=head)
                self.assert_denied(self.run_gate(), label)
                self.assert_only_number_and_head_are_requested()

    def test_pr_review_comment_does_not_satisfy_the_gate(self) -> None:
        """PR 上に現在の head の codex review 形式のコメントがあっても、ローカル記録が
        無ければ deny する。"""
        self.configure_fake_gh(
            review_bodies=[
                codex_review_comment(HEAD_SHA, "pass"),
                codex_review_comment(HEAD_SHA, "findings"),
            ]
        )
        self.assert_denied(self.run_gate())
        self.assert_only_number_and_head_are_requested()

    def test_matching_record_allows_regardless_of_pr_review_comments(self) -> None:
        self.configure_fake_gh(review_bodies=[codex_review_comment(OTHER_SHA)])
        self.write_final_attestation()
        self.assert_allowed(self.run_gate())

    def test_deny_message_does_not_mention_posting(self) -> None:
        result = self.run_gate()
        self.assert_denied(result)
        reason = self.deny_reason(result)
        self.assertNotIn("投稿", reason)
        self.assertNotIn("コメント", reason)


if __name__ == "__main__":
    unittest.main()

"""pre-merge-codex-review auto-mark の subagent lifecycle 契約テスト。

pre-merge-codex-review plugin では、PR へのレビューコメント投稿を merge gate
(`block-pre-merge.sh`) が行う。codex review wrapper
(`run-pre-merge-codex-review.sh`) は投稿せず、投稿用の本文ファイルと pending
attestation を git-dir 直下に書くだけで終わり、`pre-merge-codex-review:codex-reviewer`
subagent の lifecycle hook (SubagentStart / SubagentStop) が正規 report と head SHA
一致を確認して final attestation へ昇格させる。

契約 (auto-mark.sh):

- SubagentStart (agent_type が `pre-merge-codex-review:codex-reviewer` の完全一致):
  - レビュー開始時のローカル HEAD (full SHA) を launch attestation
    (git-dir/.claude-pre-merge-launch-<agent_id>) に書く (one-shot 記録)
  - 既存 tombstone / 既存 launch attestation があれば書かない
- SubagentStop (同 agent_type の完全一致):
  - launch attestation は最初の SubagentStop で必ず消費する (one-shot。検証失敗でも
    再 stop で final を書ける経路を残さない)
  - last_assistant_message 全体で `Status: ` で始まる行がちょうど 1 つ、かつ
    `^Status: (pass|findings)$` に一致するときのみ有効
  - launch attestation の HEAD が現在の HEAD と一致し、pending attestation が
    `pr=<全数字>` / `head=<40 hex>` の 2 行で head が現在の HEAD と一致し、投稿用の
    本文ファイルの先頭行が同じ head の header であるときのみ final へ昇格する。
    いずれかを欠けば pending と本文ファイルを破棄して skip する (fail-closed)
  - 本文ファイルは昇格後も残す (gate が投稿の本文として使う)
- PostToolUseFailure (Agent|Task): pending attestation と本文ファイルの best-effort
  破棄のみ (補助的な掃除経路)
- wrapper: 成功時は pending attestation と本文ファイルだけを書き、PR には投稿しない。
  失敗時は stale な attestation / 本文ファイルを掃除する

attestation の内容契約:

- pending / final: `pr=<PR 番号>` と `head=<full head SHA>` の 2 行
- 本文ファイル: 先頭行が `<!-- codex-review: head=<同じ head SHA> status=pass|findings -->`
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "pre-merge-codex-review"
AUTO_MARK = PLUGIN_DIR / "hooks" / "scripts" / "auto-mark.sh"
MARKERS_LIB = PLUGIN_DIR / "hooks" / "scripts" / "lib" / "markers.sh"
RUN_CODEX_REVIEW = (
    PLUGIN_DIR / "hooks" / "scripts" / "run-pre-merge-codex-review.sh"
)
HOOKS_CONFIG = PLUGIN_DIR / "hooks" / "hooks.json"

CODEX_REVIEWER = "pre-merge-codex-review:codex-reviewer"
PUSH_CODEX_REVIEWER = "pre-push-codex-review:codex-reviewer"
CODEX_REVIEWER_MATCHER = "^pre-merge-codex-review:codex-reviewer$"

FINAL_MARKER = ".claude-pre-merge-codex-reviewed"
PENDING_MARKER = ".claude-pre-merge-codex-reviewed.pending"
COMMENT_BODY = ".claude-pre-merge-codex-comment.md"
LAUNCH_ATTESTATION_PREFIX = ".claude-pre-merge-launch-"
LAUNCH_TOMBSTONE_PREFIX = ".claude-pre-merge-done-"

DEFAULT_AGENT_ID = "a1b2c3d4e5f6a7b8c"
PR_NUMBER = 123
OTHER_SHA = "fedcba0987654321fedcba0987654321fedcba09"

PASS_REPORT = "# Codex Review\n\nStatus: pass\nFindings: 0"
FINDINGS_REPORT = (
    "# Codex Review\n\nStatus: findings\n\n"
    "## Finding CODEX-example-correctness"
)

# fake gh: `pr view` は設定ファイルの JSON を返し、それ以外の呼び出しは argv を
# calls.log に 1 行追記する (wrapper が PR へ投稿しないことを検証するため)。
FAKE_GH_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "gh-config.json"), encoding="utf-8") as fp:
    config = json.load(fp)

args = sys.argv[1:]
if args[:2] == ["pr", "view"]:
    print(json.dumps(config["pr"]))
    sys.exit(0)

with open(os.path.join(HERE, "calls.log"), "a", encoding="utf-8") as fp:
    fp.write(" ".join(args) + "\\n")
sys.exit(0)
"""

FAKE_COMPANION_SCRIPT = (
    'process.stdout.write("# Codex Review\\n\\nNo findings.\\n");\n'
)
FAILING_COMPANION_SCRIPT = (
    'process.stderr.write("intentional companion failure\\n");\n'
    "process.exit(1);\n"
)


class RepositoryFixture:
    """git repo fixture と attestation path / 内容 helper (TestCase ではない)。"""

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def create_feature_repository(self, temporary: Path) -> Path:
        origin = temporary / "origin.git"
        work = temporary / "work"
        self.git(temporary, "init", "--bare", str(origin))
        self.git(temporary, "init", str(work))
        self.git(work, "config", "user.name", "Marketplace Test")
        self.git(work, "config", "user.email", "marketplace@example.invalid")
        (work / "example.txt").write_text("base\n", encoding="utf-8")
        self.git(work, "add", "example.txt")
        self.git(work, "commit", "-m", "base")
        self.git(work, "branch", "-M", "master")
        self.git(work, "remote", "add", "origin", str(origin))
        self.git(work, "push", "-u", "origin", "master")
        self.git(work, "remote", "set-head", "origin", "master")
        self.git(work, "checkout", "-b", "feature/test")
        (work / "example.txt").write_text("changed\n", encoding="utf-8")
        self.git(work, "add", "example.txt")
        self.git(work, "commit", "-m", "change")
        return work

    def add_commit(self, work: Path, content: str) -> None:
        (work / "example.txt").write_text(content, encoding="utf-8")
        self.git(work, "add", "example.txt")
        self.git(work, "commit", "-m", "extra")

    def git_dir(self, work: Path) -> Path:
        value = subprocess.check_output(
            ["git", "rev-parse", "--absolute-git-dir"], cwd=work
        )
        return Path(value.decode().strip())

    def head_sha(self, work: Path) -> str:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work)
            .decode()
            .strip()
        )

    def base_sha(self, work: Path) -> str:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "refs/remotes/origin/master"], cwd=work
            )
            .decode()
            .strip()
        )

    # ------------------------------------------------------------------
    # attestation path / 内容
    # ------------------------------------------------------------------

    def final_marker_path(self, work: Path) -> Path:
        return self.git_dir(work) / FINAL_MARKER

    def pending_marker_path(self, work: Path) -> Path:
        return self.git_dir(work) / PENDING_MARKER

    def comment_body_path(self, work: Path) -> Path:
        return self.git_dir(work) / COMMENT_BODY

    def launch_attestation_path(
        self, work: Path, agent_id: str = DEFAULT_AGENT_ID
    ) -> Path:
        return self.git_dir(work) / f"{LAUNCH_ATTESTATION_PREFIX}{agent_id}"

    def launch_tombstone_path(
        self, work: Path, agent_id: str = DEFAULT_AGENT_ID
    ) -> Path:
        return self.git_dir(work) / f"{LAUNCH_TOMBSTONE_PREFIX}{agent_id}"

    def pending_content(self, head: str, *, pr: int = PR_NUMBER) -> str:
        return f"pr={pr}\nhead={head}\n"

    def comment_body_content(self, head: str, *, status: str = "pass") -> str:
        return (
            f"<!-- codex-review: head={head} status={status} -->\n"
            "# Codex Review\n\nNo findings.\n"
        )

    def write_pending(self, work: Path, content: str) -> Path:
        pending = self.pending_marker_path(work)
        pending.write_text(content, encoding="utf-8")
        return pending

    def write_comment_body(self, work: Path, content: str) -> Path:
        body = self.comment_body_path(work)
        body.write_text(content, encoding="utf-8")
        return body


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class PreMergeCodexAutoMarkTest(RepositoryFixture, unittest.TestCase):
    # ------------------------------------------------------------------
    # payload / hook 実行
    # ------------------------------------------------------------------

    def start_payload(
        self,
        *,
        agent_type: str = CODEX_REVIEWER,
        agent_id: str = DEFAULT_AGENT_ID,
    ) -> dict[str, object]:
        return {
            "hook_event_name": "SubagentStart",
            "session_id": "test-session",
            "agent_id": agent_id,
            "agent_type": agent_type,
        }

    def stop_payload(
        self,
        message: str | None,
        *,
        agent_type: str = CODEX_REVIEWER,
        agent_id: str = DEFAULT_AGENT_ID,
        stop_hook_active: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "hook_event_name": "SubagentStop",
            "session_id": "test-session",
            "agent_id": agent_id,
            "agent_type": agent_type,
            "stop_hook_active": stop_hook_active,
        }
        if message is not None:
            payload["last_assistant_message"] = message
        return payload

    def run_hook(
        self,
        work: Path,
        payload: dict[str, object],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(AUTO_MARK)],
            cwd=work,
            input=json.dumps(payload).encode(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def run_start(
        self, work: Path, *, agent_id: str = DEFAULT_AGENT_ID
    ) -> subprocess.CompletedProcess[bytes]:
        result = self.run_hook(work, self.start_payload(agent_id=agent_id))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def assert_no_final(self, work: Path, payload: dict[str, object]) -> None:
        final = self.final_marker_path(work)
        final.unlink(missing_ok=True)
        result = self.run_hook(work, payload)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertFalse(final.exists(), result.stderr.decode())

    # ------------------------------------------------------------------
    # 骨格の存在
    # ------------------------------------------------------------------

    def test_auto_mark_script_exists(self) -> None:
        self.assertTrue(AUTO_MARK.is_file(), f"missing hook script: {AUTO_MARK}")

    def test_markers_lib_exists(self) -> None:
        self.assertTrue(MARKERS_LIB.is_file(), f"missing lib: {MARKERS_LIB}")

    # ------------------------------------------------------------------
    # SubagentStart: launch attestation
    # ------------------------------------------------------------------

    def test_subagent_start_writes_head_sha_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            attestation = self.launch_attestation_path(work)
            attestation.unlink(missing_ok=True)
            self.run_start(work)
            self.assertTrue(attestation.exists())
            recorded = attestation.read_text(encoding="utf-8").strip()
            self.assertEqual(recorded, self.head_sha(work))
            self.assertRegex(recorded, re.compile(r"^[0-9a-f]{40}$"))

    def test_subagent_start_skips_when_tombstone_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            self.launch_tombstone_path(work).write_text(
                self.head_sha(work), encoding="utf-8"
            )
            self.run_start(work)
            self.assertFalse(self.launch_attestation_path(work).exists())

    def test_subagent_start_does_not_overwrite_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            attestation = self.launch_attestation_path(work)
            attestation.write_text(OTHER_SHA, encoding="utf-8")
            self.run_start(work)
            self.assertEqual(
                attestation.read_text(encoding="utf-8").strip(), OTHER_SHA
            )

    # ------------------------------------------------------------------
    # SubagentStop: pending → final の昇格
    # ------------------------------------------------------------------

    def test_stop_promotes_pending_to_final_and_keeps_body(self) -> None:
        reports = {"pass": PASS_REPORT, "findings": FINDINGS_REPORT}
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            for index, (status, report) in enumerate(reports.items()):
                with self.subTest(status=status):
                    # review サイクルごとに一意の agent_id を使う (同一 agent_id を
                    # 使い回すと 2 サイクル目が launch tombstone に阻まれ、この
                    # subTest が検証すべき昇格経路を通らなくなる)。
                    agent_id = f"{DEFAULT_AGENT_ID}{index}"
                    final = self.final_marker_path(work)
                    final.unlink(missing_ok=True)
                    self.run_start(work, agent_id=agent_id)
                    head = self.head_sha(work)
                    pending_text = self.pending_content(head)
                    pending = self.write_pending(work, pending_text)
                    body = self.write_comment_body(
                        work, self.comment_body_content(head, status=status)
                    )
                    result = self.run_hook(
                        work, self.stop_payload(report, agent_id=agent_id)
                    )
                    self.assertEqual(
                        result.returncode, 0, result.stderr.decode()
                    )
                    self.assertTrue(final.exists(), result.stderr.decode())
                    self.assertEqual(
                        final.read_text(encoding="utf-8"), pending_text
                    )
                    self.assertFalse(pending.exists())
                    self.assertTrue(
                        body.exists(),
                        "本文ファイルは gate が投稿に使うため昇格後も残す",
                    )
                    self.assertFalse(
                        self.launch_attestation_path(work, agent_id).exists()
                    )
                    self.assertTrue(
                        self.launch_tombstone_path(work, agent_id).exists()
                    )

    def test_stop_with_invalid_report_discards_pending_and_body(self) -> None:
        cases = {
            "execution_failed": "# Codex Review\n\nStatus: execution-failed\n",
            "missing_status": "# Codex Review\n\nFindings: 0\n",
            "duplicate_status": (
                "# Codex Review\n\nStatus: pass\nStatus: findings\n"
            ),
            "unknown_status": "# Codex Review\n\nStatus: ok\n",
        }
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            for index, (label, report) in enumerate(cases.items()):
                with self.subTest(case=label):
                    agent_id = f"{DEFAULT_AGENT_ID}{index}"
                    self.run_start(work, agent_id=agent_id)
                    head = self.head_sha(work)
                    pending = self.write_pending(
                        work, self.pending_content(head)
                    )
                    body = self.write_comment_body(
                        work, self.comment_body_content(head)
                    )
                    self.assert_no_final(
                        work, self.stop_payload(report, agent_id=agent_id)
                    )
                    self.assertFalse(pending.exists(), f"case={label}")
                    self.assertFalse(body.exists(), f"case={label}")

    def test_stop_with_stale_pending_head_discards_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            self.run_start(work)
            pending = self.write_pending(work, self.pending_content(OTHER_SHA))
            self.write_comment_body(work, self.comment_body_content(OTHER_SHA))
            self.assert_no_final(work, self.stop_payload(PASS_REPORT))
            self.assertFalse(pending.exists())

    def test_stop_after_new_commit_discards_pending(self) -> None:
        # launch attestation の HEAD (start 時点) と現在の HEAD が食い違う場合は
        # 昇格しない (レビュー開始後の commit 追加を fail-closed に遮断する)。
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            self.run_start(work)
            self.add_commit(work, "changed again\n")
            head = self.head_sha(work)
            pending = self.write_pending(work, self.pending_content(head))
            self.write_comment_body(work, self.comment_body_content(head))
            self.assert_no_final(work, self.stop_payload(PASS_REPORT))
            self.assertFalse(pending.exists())

    def test_stop_with_malformed_pending_discards_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            head = self.head_sha(work)
            cases = {
                "missing_pr_line": f"head={head}\n",
                "short_head": f"pr={PR_NUMBER}\nhead=deadbeef\n",
            }
            for index, (label, pending_text) in enumerate(cases.items()):
                with self.subTest(case=label):
                    agent_id = f"{DEFAULT_AGENT_ID}{index}"
                    self.run_start(work, agent_id=agent_id)
                    pending = self.write_pending(work, pending_text)
                    self.write_comment_body(
                        work, self.comment_body_content(head)
                    )
                    self.assert_no_final(
                        work, self.stop_payload(PASS_REPORT, agent_id=agent_id)
                    )
                    self.assertFalse(pending.exists(), f"case={label}")

    def test_stop_requires_comment_body_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            self.run_start(work)
            head = self.head_sha(work)
            pending = self.write_pending(work, self.pending_content(head))
            self.comment_body_path(work).unlink(missing_ok=True)
            self.assert_no_final(work, self.stop_payload(PASS_REPORT))
            self.assertFalse(pending.exists())

    def test_stop_requires_matching_body_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            self.run_start(work)
            head = self.head_sha(work)
            pending = self.write_pending(work, self.pending_content(head))
            self.write_comment_body(work, self.comment_body_content(OTHER_SHA))
            self.assert_no_final(work, self.stop_payload(PASS_REPORT))
            self.assertFalse(pending.exists())

    def test_stop_without_attestation_discards_pending_and_body(self) -> None:
        # 偽装 stop (launch attestation 無し) では昇格せず、pending と本文ファイルを
        # 破棄する (orphan 化した組を後続の別 stop が昇格できる経路を塞ぐ)。
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            head = self.head_sha(work)
            pending = self.write_pending(work, self.pending_content(head))
            body = self.write_comment_body(work, self.comment_body_content(head))
            self.assertFalse(self.launch_attestation_path(work).exists())
            self.assert_no_final(work, self.stop_payload(PASS_REPORT))
            self.assertFalse(pending.exists())
            self.assertFalse(body.exists())

    def test_stop_without_attestation_keeps_promoted_pair(self) -> None:
        # final と本文は gate が投稿に使う対 (pair) であり、昇格済みの対は terminal な
        # 掃除経路でも壊さない。偽装 stop で消えるのは pending だけ。
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            head = self.head_sha(work)
            final = self.final_marker_path(work)
            final.write_text(self.pending_content(head), encoding="utf-8")
            body = self.write_comment_body(work, self.comment_body_content(head))
            pending = self.write_pending(work, self.pending_content(head))
            self.assertFalse(self.launch_attestation_path(work).exists())

            result = self.run_hook(work, self.stop_payload(PASS_REPORT))
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(final.exists(), "昇格済みの final は保持する")
            self.assertTrue(body.exists(), "final と対になる本文も保持する")
            self.assertFalse(pending.exists())

    def test_stop_with_existing_tombstone_discards_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            self.run_start(work)
            attestation = self.launch_attestation_path(work)
            head = self.head_sha(work)
            self.launch_tombstone_path(work).write_text(head, encoding="utf-8")
            pending = self.write_pending(work, self.pending_content(head))
            self.write_comment_body(work, self.comment_body_content(head))
            self.assert_no_final(work, self.stop_payload(PASS_REPORT))
            self.assertFalse(pending.exists())
            self.assertFalse(
                attestation.exists(),
                "残存 attestation の掃除だけは再試行する",
            )

    def test_pending_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            self.run_start(work)
            head = self.head_sha(work)
            target = temporary / "target"
            target.write_text(self.pending_content(head), encoding="utf-8")
            pending = self.pending_marker_path(work)
            pending.symlink_to(target)
            self.write_comment_body(work, self.comment_body_content(head))
            self.assert_no_final(work, self.stop_payload(PASS_REPORT))
            self.assertFalse(os.path.lexists(pending))

    def test_stop_hook_active_consumes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            self.run_start(work)
            head = self.head_sha(work)
            pending = self.write_pending(work, self.pending_content(head))
            body = self.write_comment_body(work, self.comment_body_content(head))
            self.assert_no_final(
                work, self.stop_payload(PASS_REPORT, stop_hook_active=True)
            )
            self.assertTrue(self.launch_attestation_path(work).exists())
            self.assertTrue(pending.exists())
            self.assertTrue(body.exists())

    def test_other_agent_type_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            other_agent_id = "other-agent"
            start = self.run_hook(
                work,
                self.start_payload(
                    agent_type=PUSH_CODEX_REVIEWER, agent_id=other_agent_id
                ),
            )
            self.assertEqual(start.returncode, 0, start.stderr.decode())
            self.assertFalse(
                self.launch_attestation_path(work, other_agent_id).exists()
            )

            self.run_start(work)
            head = self.head_sha(work)
            pending = self.write_pending(work, self.pending_content(head))
            body = self.write_comment_body(work, self.comment_body_content(head))
            self.assert_no_final(
                work,
                self.stop_payload(
                    PASS_REPORT,
                    agent_type=PUSH_CODEX_REVIEWER,
                    agent_id=other_agent_id,
                ),
            )
            self.assertTrue(pending.exists())
            self.assertTrue(body.exists())
            self.assertTrue(self.launch_attestation_path(work).exists())

    # ------------------------------------------------------------------
    # 補助経路: PostToolUseFailure
    # ------------------------------------------------------------------

    def failure_payload(self, subagent_type: str) -> dict[str, object]:
        return {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": subagent_type},
            "error": "agent failed after wrapper completion",
            "is_interrupt": False,
        }

    def test_post_tool_use_failure_discards_pending_and_body(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            head = self.head_sha(work)
            pending = self.write_pending(work, self.pending_content(head))
            body = self.write_comment_body(work, self.comment_body_content(head))
            result = self.run_hook(work, self.failure_payload(CODEX_REVIEWER))
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertFalse(self.final_marker_path(work).exists())
            self.assertFalse(pending.exists())
            self.assertFalse(body.exists())

    def test_post_tool_use_failure_ignores_other_subagent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            head = self.head_sha(work)
            pending = self.write_pending(work, self.pending_content(head))
            body = self.write_comment_body(work, self.comment_body_content(head))
            result = self.run_hook(
                work, self.failure_payload(PUSH_CODEX_REVIEWER)
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(pending.exists())
            self.assertTrue(body.exists())

    # ------------------------------------------------------------------
    # hooks.json 配線
    # ------------------------------------------------------------------

    def test_hooks_config_registers_subagent_lifecycle(self) -> None:
        config = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))
        hooks = config["hooks"]

        start_groups = [
            group
            for group in hooks.get("SubagentStart", [])
            if group.get("matcher") == CODEX_REVIEWER_MATCHER
        ]
        self.assertEqual(len(start_groups), 1)
        self.assertIn("auto-mark.sh", start_groups[0]["hooks"][0]["command"])

        stop_groups = [
            group
            for group in hooks.get("SubagentStop", [])
            if group.get("matcher") == CODEX_REVIEWER_MATCHER
        ]
        self.assertEqual(len(stop_groups), 1)
        self.assertIn("auto-mark.sh", stop_groups[0]["hooks"][0]["command"])

        failure_groups = hooks["PostToolUseFailure"]
        self.assertEqual(len(failure_groups), 1)
        self.assertEqual(failure_groups[0]["matcher"], "Agent|Task")
        self.assertIn("auto-mark.sh", failure_groups[0]["hooks"][0]["command"])

        pre_tool_use_commands = [
            hook.get("command", "")
            for group in hooks["PreToolUse"]
            if group.get("matcher") == "Bash"
            for hook in group.get("hooks", [])
        ]
        self.assertTrue(
            any(
                "block-pre-merge.sh" in command
                for command in pre_tool_use_commands
            )
        )
        self.assertTrue(
            any(
                "block-bg-codex-wrapper.sh" in command
                for command in pre_tool_use_commands
            )
        )


@unittest.skipUnless(
    shutil.which("node") and shutil.which("git"),
    "wrapper integration requires node and git",
)
@unittest.skipUnless(shutil.which("jq"), "wrapper integration requires jq")
class PreMergeCodexWrapperTest(RepositoryFixture, unittest.TestCase):
    """wrapper は投稿せず、pending attestation と投稿用本文だけを書く契約。"""

    def install_fake_gh(self, directory: Path, work: Path) -> Path:
        bin_dir = directory / "fake-bin"
        bin_dir.mkdir()
        gh_path = bin_dir / "gh"
        gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        config = {
            "pr": {
                "number": PR_NUMBER,
                "headRefOid": self.head_sha(work),
                "baseRefName": "master",
                "baseRefOid": self.base_sha(work),
            }
        }
        (bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )
        return bin_dir

    def install_fake_companion(self, home: Path, script: str) -> Path:
        companion = (
            home
            / ".claude"
            / "plugins"
            / "cache"
            / "openai-codex"
            / "codex"
            / "1.0.0"
            / "scripts"
            / "codex-companion.mjs"
        )
        companion.parent.mkdir(parents=True)
        companion.write_text(script, encoding="utf-8")
        return companion

    def wrapper_environment(
        self, home: Path, fake_bin_dir: Path
    ) -> dict[str, str]:
        # 実 node の絶対 path から bin ディレクトリを解決して PATH に通す
        # (HOME 差し替えで shim 経由の node が解決できなくなる環境でも動くように)。
        # fake gh を先頭に置き、実 gh より優先させる。
        real_node = Path(
            subprocess.check_output(
                ["node", "-e", "process.stdout.write(process.execPath)"]
            )
            .decode()
            .strip()
        )
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join(
            [str(fake_bin_dir), str(real_node.parent), env.get("PATH", "")]
        )
        env["HOME"] = str(home)
        return env

    def run_wrapper(
        self, work: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(RUN_CODEX_REVIEW)],
            cwd=work,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def gh_calls(self, fake_bin_dir: Path) -> str:
        log = fake_bin_dir / "calls.log"
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def test_wrapper_writes_pending_and_body_without_posting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            fake_bin_dir = self.install_fake_gh(temporary, work)
            home = temporary / "home"
            self.install_fake_companion(home, FAKE_COMPANION_SCRIPT)
            env = self.wrapper_environment(home, fake_bin_dir)

            result = self.run_wrapper(work, env)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn("# Codex Review", result.stdout.decode())

            head = self.head_sha(work)
            body = self.comment_body_path(work)
            self.assertTrue(body.exists(), result.stderr.decode())
            self.assertEqual(
                body.read_text(encoding="utf-8").split("\n")[0],
                f"<!-- codex-review: head={head} status=pass -->",
            )
            pending = self.pending_marker_path(work)
            self.assertTrue(pending.exists(), result.stderr.decode())
            self.assertEqual(
                pending.read_text(encoding="utf-8"),
                self.pending_content(head),
            )
            self.assertFalse(self.final_marker_path(work).exists())

            for line in self.gh_calls(fake_bin_dir).splitlines():
                self.assertNotIn(
                    "review",
                    line,
                    "wrapper は PR に投稿しない (投稿は merge gate の責務)",
                )
            self.assertIn("ローカル", result.stderr.decode())

    def test_wrapper_failure_removes_stale_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            head = self.head_sha(work)
            pending = self.write_pending(work, "stale\n")
            final = self.final_marker_path(work)
            final.write_text(self.pending_content(head), encoding="utf-8")
            body = self.write_comment_body(work, self.comment_body_content(head))

            fake_bin_dir = self.install_fake_gh(temporary, work)
            home = temporary / "home"
            self.install_fake_companion(home, FAILING_COMPANION_SCRIPT)
            env = self.wrapper_environment(home, fake_bin_dir)

            result = self.run_wrapper(work, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "intentional companion failure", result.stderr.decode()
            )
            self.assertFalse(pending.exists(), result.stderr.decode())
            self.assertFalse(final.exists(), result.stderr.decode())
            self.assertFalse(body.exists(), result.stderr.decode())

    def test_wrapper_aborts_on_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            (work / "untracked.txt").write_text("dirty\n", encoding="utf-8")

            fake_bin_dir = self.install_fake_gh(temporary, work)
            home = temporary / "home"
            self.install_fake_companion(home, FAKE_COMPANION_SCRIPT)
            env = self.wrapper_environment(home, fake_bin_dir)

            result = self.run_wrapper(work, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.pending_marker_path(work).exists())
            self.assertFalse(self.final_marker_path(work).exists())
            self.assertFalse(self.comment_body_path(work).exists())


if __name__ == "__main__":
    unittest.main()

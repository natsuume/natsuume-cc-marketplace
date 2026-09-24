"""pre-merge-cross-review auto-mark の fable-reviewer lifecycle 契約テスト。

`pre-merge-cross-review:fable-reviewer` は wrapper を持たないため、auto-mark.sh が
report 本文から Fable review のローカル記録 (attestation と投稿用本文) を作る。記録の
投稿は merge gate が行う (tests/test_pre_merge_fable_gate_post.py)。

契約 (auto-mark.sh の fable 分岐):

- SubagentStart: codex-reviewer と同じく launch attestation
  (git-dir/.claude-pre-merge-launch-<agent_id>) を書く。1 日より古い hand-back 本文
  (git-dir/.claude-pre-merge-fable-body-*) を prune する。既存の Fable 記録は削除しない
- PostToolUse (SubagentHandback): status record は codex-reviewer と同一規則で書く。
  launch attestation があり tombstone が無く、message が string で、1 回目の hand-back の
  ときだけ message を hand-back 本文 (git-dir/.claude-pre-merge-fable-body-<agent_id>) に
  保存する
- SubagentStop: agent_type / agent_id 検証・stop_hook_active・attestation と tombstone の
  扱いは codex-reviewer と同一。handback record があればその値を status とし、本文は
  hand-back 本文 (無い / symlink なら invalid)。record が無ければ last_assistant_message を
  status と本文の元にする。record と hand-back 本文は常に消費する。status が pass /
  findings 以外、または開始時 HEAD と現在の HEAD が異なる場合は記録しない
- 記録: attestation (git-dir/.claude-pre-merge-fable-reviewed) は `head=<HEAD>` の 1 行、
  投稿用本文 (git-dir/.claude-pre-merge-fable-comment.md) は先頭行が
  `<!-- fable-review: head=<HEAD> status=pass|findings -->` で、その後に report が続く。
  report 中の header 形文字列は `(quoted)` 付きに無害化し、60000 字を超える本文は行単位で
  切り詰めて省略注記を付ける
- Fable 側の処理は codex 側の pending / final / 本文に触れない (逆も同様)
- PostToolUseFailure: fable-reviewer については何もしない
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_auto_mark_fixture():
    """codex-reviewer 側の auto-mark テストが持つ git repo fixture を読み込む。

    `import` 文で書くと「sys.path 操作より前に import 文が来る」という lint 制約
    (E402) に抵触するため、既存テストと同じ importlib 経由の明示 import にする。
    """
    return importlib.import_module("test_pre_merge_auto_mark")


_auto_mark_tests = _load_auto_mark_fixture()

RepositoryFixture = _auto_mark_tests.RepositoryFixture
AUTO_MARK = _auto_mark_tests.AUTO_MARK
CODEX_REVIEWER = _auto_mark_tests.CODEX_REVIEWER
FABLE_REVIEWER = _auto_mark_tests.FABLE_REVIEWER
OTHER_SHA = _auto_mark_tests.OTHER_SHA

FABLE_FINAL_MARKER = ".claude-pre-merge-fable-reviewed"
FABLE_COMMENT_BODY = ".claude-pre-merge-fable-comment.md"
FABLE_HANDBACK_BODY_PREFIX = ".claude-pre-merge-fable-body-"

CODEX_FINAL_MARKER = _auto_mark_tests.FINAL_MARKER
CODEX_PENDING_MARKER = _auto_mark_tests.PENDING_MARKER
CODEX_COMMENT_BODY = _auto_mark_tests.COMMENT_BODY

MAX_COMMENT_BODY_CHARS = 60000
TRUNCATION_NOTE = "(report が長いため以降を省略しました。)"

FABLE_PASS_REPORT = (
    "# Fable Review\n\nStatus: pass\nFindings: 0\n"
    "Checked sources: PR description, issue 12"
)
FABLE_FINDINGS_REPORT = (
    "# Fable Review\n\nStatus: findings\n\n"
    "## Finding FABLE-example-acceptance-criteria\n"
    "- Category: acceptance-criteria\n"
    "- Severity: P2\n"
    "- Confidence: high"
)
HANDBACK_CLOSING_MESSAGE = (
    "Fable review complete. Report delivered to the parent session."
)
ONE_DAY_SECONDS = 24 * 60 * 60


def fable_header(head: str, status: str) -> str:
    return f"<!-- fable-review: head={head} status={status} -->"


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class PreMergeFableAutoMarkTest(RepositoryFixture, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temporary_path = Path(self.temporary.name)
        self.work = self.create_feature_repository(self.temporary_path)
        # hook が一時ファイルやキャッシュを書く場合も、実 HOME ではなく一時ディレクトリ
        # 配下に閉じる。
        isolated_tmp = self.temporary_path / "tmp"
        isolated_cache = self.temporary_path / "cache"
        isolated_tmp.mkdir()
        isolated_cache.mkdir()
        self.hook_env = os.environ.copy()
        self.hook_env["TMPDIR"] = str(isolated_tmp)
        self.hook_env["XDG_CACHE_HOME"] = str(isolated_cache)

    # ------------------------------------------------------------------
    # path helper
    # ------------------------------------------------------------------

    def fable_final_path(self) -> Path:
        return self.git_dir(self.work) / FABLE_FINAL_MARKER

    def fable_body_path(self) -> Path:
        return self.git_dir(self.work) / FABLE_COMMENT_BODY

    def fable_handback_body_path(self, agent_id: str) -> Path:
        return self.git_dir(self.work) / f"{FABLE_HANDBACK_BODY_PREFIX}{agent_id}"

    def handback_record_path(self, agent_id: str) -> Path:
        return self.handback_report_path(self.work, agent_id)

    def codex_paths(self) -> list[Path]:
        git_dir = self.git_dir(self.work)
        return [
            git_dir / CODEX_FINAL_MARKER,
            git_dir / CODEX_PENDING_MARKER,
            git_dir / CODEX_COMMENT_BODY,
        ]

    def fable_record_paths(self) -> list[Path]:
        return [self.fable_final_path(), self.fable_body_path()]

    def snapshot(self, paths: list[Path]) -> dict[str, bytes | None]:
        return {
            path.name: path.read_bytes() if path.exists() else None
            for path in paths
        }

    def write_existing_fable_record(self, head: str) -> dict[str, bytes | None]:
        """既に存在する Fable 記録 (過去の review の記録) を置き、その内容を返す。"""
        self.fable_final_path().write_text(f"head={head}\n", encoding="utf-8")
        self.fable_body_path().write_text(
            f"{fable_header(head, 'pass')}\n{FABLE_PASS_REPORT}\n",
            encoding="utf-8",
        )
        return self.snapshot(self.fable_record_paths())

    # ------------------------------------------------------------------
    # payload / hook 実行
    # ------------------------------------------------------------------

    def run_hook(
        self, payload: dict[str, object]
    ) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            ["bash", str(AUTO_MARK)],
            cwd=self.work,
            input=json.dumps(payload).encode(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.hook_env,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def start(self, agent_id: str, agent_type: str = FABLE_REVIEWER) -> None:
        self.run_hook(
            {
                "hook_event_name": "SubagentStart",
                "session_id": "test-session",
                "agent_id": agent_id,
                "agent_type": agent_type,
            }
        )

    def handback(
        self, agent_id: str, message: object, agent_type: str = FABLE_REVIEWER
    ) -> None:
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "test-session",
                "agent_id": agent_id,
                "agent_type": agent_type,
                "tool_name": "SubagentHandback",
                "tool_input": {"message": message},
                "tool_response": {
                    "success": True,
                    "message": "Report delivered to your caller.",
                },
                "tool_use_id": "toolu_test",
            }
        )

    def stop(
        self,
        agent_id: str,
        message: str | None,
        *,
        agent_type: str = FABLE_REVIEWER,
        stop_hook_active: bool = False,
    ) -> None:
        payload: dict[str, object] = {
            "hook_event_name": "SubagentStop",
            "session_id": "test-session",
            "agent_id": agent_id,
            "agent_type": agent_type,
            "stop_hook_active": stop_hook_active,
        }
        if message is not None:
            payload["last_assistant_message"] = message
        self.run_hook(payload)

    def complete_via_handback(self, agent_id: str, report: str) -> None:
        self.start(agent_id)
        self.handback(agent_id, report)
        self.stop(agent_id, HANDBACK_CLOSING_MESSAGE)

    # ------------------------------------------------------------------
    # assertion helper
    # ------------------------------------------------------------------

    def assert_fable_record(self, head: str, status: str, report: str) -> None:
        final = self.fable_final_path()
        body = self.fable_body_path()
        self.assertTrue(final.is_file(), "Fable attestation が書かれていない")
        self.assertEqual(
            final.read_text(encoding="utf-8").splitlines(), [f"head={head}"]
        )
        self.assertTrue(body.is_file(), "Fable 投稿用本文が書かれていない")
        first_line, _, rest = body.read_text(encoding="utf-8").partition("\n")
        self.assertEqual(first_line, fable_header(head, status))
        self.assertEqual(rest.rstrip("\n"), report.rstrip("\n"))

    def assert_no_fable_record(self) -> None:
        self.assertFalse(self.fable_final_path().exists())
        self.assertFalse(self.fable_body_path().exists())

    # ------------------------------------------------------------------
    # SubagentStart
    # ------------------------------------------------------------------

    def test_start_writes_launch_attestation_and_keeps_existing_record(self) -> None:
        existing = self.write_existing_fable_record(OTHER_SHA)
        self.start("fablestart0")
        attestation = self.launch_attestation_path(self.work, "fablestart0")
        self.assertTrue(attestation.is_file())
        self.assertEqual(
            attestation.read_text(encoding="utf-8").strip(), self.head_sha(self.work)
        )
        self.assertEqual(
            self.snapshot(self.fable_record_paths()),
            existing,
            "SubagentStart は既存の Fable 記録を削除しない",
        )

    def test_start_prunes_stale_handback_bodies(self) -> None:
        stale = self.fable_handback_body_path("stalebody0")
        fresh = self.fable_handback_body_path("freshbody0")
        stale.write_text(FABLE_PASS_REPORT, encoding="utf-8")
        fresh.write_text(FABLE_PASS_REPORT, encoding="utf-8")
        old = time.time() - ONE_DAY_SECONDS - 3600
        os.utime(stale, (old, old))
        self.start("fableprune0")
        self.assertFalse(stale.exists(), "1 日より古い hand-back 本文を prune する")
        self.assertTrue(fresh.exists(), "1 日以内の hand-back 本文は残す")

    # ------------------------------------------------------------------
    # 記録の生成
    # ------------------------------------------------------------------

    def test_handback_report_is_recorded_at_stop(self) -> None:
        reports = {"pass": FABLE_PASS_REPORT, "findings": FABLE_FINDINGS_REPORT}
        head = self.head_sha(self.work)
        for index, (status, report) in enumerate(reports.items()):
            with self.subTest(status=status):
                agent_id = f"fablehandback{index}"
                self.start(agent_id)
                self.handback(agent_id, report)
                record = self.handback_record_path(agent_id)
                handback_body = self.fable_handback_body_path(agent_id)
                self.assertEqual(record.read_text(encoding="utf-8"), status)
                self.assertTrue(handback_body.is_file())
                self.assertEqual(
                    handback_body.read_text(encoding="utf-8").rstrip("\n"),
                    report.rstrip("\n"),
                )

                self.stop(agent_id, HANDBACK_CLOSING_MESSAGE)
                self.assert_fable_record(head, status, report)
                self.assertFalse(record.exists(), "handback record は one-shot")
                self.assertFalse(handback_body.exists(), "hand-back 本文は one-shot")
                self.assertFalse(
                    self.launch_attestation_path(self.work, agent_id).exists()
                )
                self.assertTrue(
                    self.launch_tombstone_path(self.work, agent_id).exists()
                )

    def test_last_assistant_message_is_recorded_without_handback(self) -> None:
        head = self.head_sha(self.work)
        self.start("fablelast0")
        self.stop("fablelast0", FABLE_FINDINGS_REPORT)
        self.assert_fable_record(head, "findings", FABLE_FINDINGS_REPORT)

    def test_existing_record_is_replaced_on_success(self) -> None:
        self.write_existing_fable_record(OTHER_SHA)
        head = self.head_sha(self.work)
        self.complete_via_handback("fablereplace0", FABLE_FINDINGS_REPORT)
        self.assert_fable_record(head, "findings", FABLE_FINDINGS_REPORT)

    # ------------------------------------------------------------------
    # 記録しない経路 (既存記録にも触れない)
    # ------------------------------------------------------------------

    def test_invalid_report_is_not_recorded_and_keeps_existing_record(self) -> None:
        cases = {
            "execution_failed": "# Fable Review\n\nStatus: execution-failed\n",
            "missing_status": "# Fable Review\n\nFindings: 0\n",
            "duplicate_status": "# Fable Review\n\nStatus: pass\nStatus: findings\n",
        }
        for index, (label, report) in enumerate(cases.items()):
            for route in ("handback", "last_assistant_message"):
                with self.subTest(case=label, route=route):
                    existing = self.write_existing_fable_record(OTHER_SHA)
                    agent_id = f"fableinvalid{index}{route[0]}"
                    self.start(agent_id)
                    if route == "handback":
                        self.handback(agent_id, report)
                        # 締めの文に Status: pass があっても record が優先する。
                        self.stop(agent_id, FABLE_PASS_REPORT)
                    else:
                        self.stop(agent_id, report)
                    self.assertEqual(
                        self.snapshot(self.fable_record_paths()), existing
                    )
                    self.assertFalse(self.handback_record_path(agent_id).exists())
                    self.assertFalse(
                        self.fable_handback_body_path(agent_id).exists()
                    )

    def test_duplicate_handback_is_not_recorded(self) -> None:
        existing = self.write_existing_fable_record(OTHER_SHA)
        self.start("fabledup0")
        self.handback("fabledup0", FABLE_PASS_REPORT)
        self.handback("fabledup0", FABLE_FINDINGS_REPORT)
        self.assertEqual(
            self.handback_record_path("fabledup0").read_text(encoding="utf-8"),
            "invalid",
        )
        self.stop("fabledup0", HANDBACK_CLOSING_MESSAGE)
        self.assertEqual(self.snapshot(self.fable_record_paths()), existing)
        self.assertFalse(self.handback_record_path("fabledup0").exists())
        self.assertFalse(self.fable_handback_body_path("fabledup0").exists())

    def test_head_change_after_start_is_not_recorded(self) -> None:
        self.start("fablehead0")
        self.handback("fablehead0", FABLE_PASS_REPORT)
        self.add_commit(self.work, "changed again\n")
        self.stop("fablehead0", HANDBACK_CLOSING_MESSAGE)
        self.assert_no_fable_record()

    def test_existing_tombstone_is_not_recorded(self) -> None:
        self.start("fabletomb0")
        self.handback("fabletomb0", FABLE_PASS_REPORT)
        self.launch_tombstone_path(self.work, "fabletomb0").write_text(
            self.head_sha(self.work), encoding="utf-8"
        )
        self.stop("fabletomb0", HANDBACK_CLOSING_MESSAGE)
        self.assert_no_fable_record()

    def test_stop_without_launch_attestation_is_not_recorded(self) -> None:
        self.stop("fablenoattest0", FABLE_PASS_REPORT)
        self.assert_no_fable_record()

    def test_stop_hook_active_consumes_nothing(self) -> None:
        self.start("fableactive0")
        self.handback("fableactive0", FABLE_PASS_REPORT)
        self.stop("fableactive0", HANDBACK_CLOSING_MESSAGE, stop_hook_active=True)
        self.assert_no_fable_record()
        self.assertTrue(self.launch_attestation_path(self.work, "fableactive0").exists())
        self.assertTrue(self.handback_record_path("fableactive0").exists())
        self.assertTrue(self.fable_handback_body_path("fableactive0").exists())

    def test_missing_or_symlinked_handback_body_is_invalid(self) -> None:
        target = self.temporary_path / "report-target.md"
        target.write_text(FABLE_PASS_REPORT, encoding="utf-8")
        for label in ("missing", "symlink"):
            with self.subTest(case=label):
                agent_id = f"fablebody{label}"
                self.start(agent_id)
                self.handback(agent_id, FABLE_PASS_REPORT)
                handback_body = self.fable_handback_body_path(agent_id)
                handback_body.unlink()
                if label == "symlink":
                    handback_body.symlink_to(target)
                self.stop(agent_id, HANDBACK_CLOSING_MESSAGE)
                self.assert_no_fable_record()
                self.assertFalse(os.path.lexists(handback_body))
                self.assertFalse(self.handback_record_path(agent_id).exists())
                self.assertEqual(
                    target.read_text(encoding="utf-8"), FABLE_PASS_REPORT
                )

    def test_handback_without_launch_attestation_does_not_save_body(self) -> None:
        self.handback("fablenostart0", FABLE_PASS_REPORT)
        self.assertFalse(self.fable_handback_body_path("fablenostart0").exists())
        self.assertFalse(self.handback_record_path("fablenostart0").exists())

    # ------------------------------------------------------------------
    # 本文の整形
    # ------------------------------------------------------------------

    def test_header_like_strings_in_report_are_quoted(self) -> None:
        head = self.head_sha(self.work)
        report = (
            "# Fable Review\n\nStatus: findings\n\n"
            "差分に次の行が含まれています:\n"
            f"{fable_header(OTHER_SHA, 'pass')}\n"
            f"<!-- codex-review: head={OTHER_SHA} status=pass -->\n"
        )
        self.complete_via_handback("fablequote0", report)
        lines = self.fable_body_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], fable_header(head, "findings"))
        rest = "\n".join(lines[1:])
        self.assertIn(
            f"<!-- fable-review (quoted): head={OTHER_SHA} status=pass -->", rest
        )
        self.assertIn(
            f"<!-- codex-review (quoted): head={OTHER_SHA} status=pass -->", rest
        )
        self.assertNotIn("<!-- fable-review:", rest)
        self.assertNotIn("<!-- codex-review:", rest)

    def test_long_report_is_truncated_and_keeps_header(self) -> None:
        head = self.head_sha(self.work)
        filler = "\n".join(
            f"- detail line {index:05d} " + "x" * 80 for index in range(900)
        )
        report = f"# Fable Review\n\nStatus: findings\n\n{filler}\n"
        self.assertGreater(len(report), MAX_COMMENT_BODY_CHARS)
        self.complete_via_handback("fablelong0", report)
        body = self.fable_body_path().read_text(encoding="utf-8")
        self.assertEqual(body.split("\n", 1)[0], fable_header(head, "findings"))
        self.assertIn(TRUNCATION_NOTE, body)
        self.assertLessEqual(
            len(body), MAX_COMMENT_BODY_CHARS + len(TRUNCATION_NOTE) + 200
        )
        self.assertTrue(self.fable_final_path().is_file())

    # ------------------------------------------------------------------
    # codex 側の記録との独立性
    # ------------------------------------------------------------------

    def test_fable_stop_does_not_touch_codex_records(self) -> None:
        head = self.head_sha(self.work)
        final, pending, body = self.codex_paths()
        final.write_text(self.pending_content(head), encoding="utf-8")
        pending.write_text(self.pending_content(head), encoding="utf-8")
        body.write_text(self.comment_body_content(head), encoding="utf-8")
        codex_before = self.snapshot(self.codex_paths())

        self.complete_via_handback("fableisolated0", FABLE_PASS_REPORT)
        self.assertEqual(self.snapshot(self.codex_paths()), codex_before)

        # 記録しない経路 (report 無効) でも codex 側に触れない。
        self.start("fableisolated1")
        self.stop("fableisolated1", "# Fable Review\n\nStatus: execution-failed\n")
        self.assertEqual(self.snapshot(self.codex_paths()), codex_before)

    def test_codex_stop_does_not_touch_fable_records(self) -> None:
        head = self.head_sha(self.work)
        fable_before = self.write_existing_fable_record(head)
        self.fable_handback_body_path("fablepending0").write_text(
            FABLE_PASS_REPORT, encoding="utf-8"
        )
        handback_before = self.fable_handback_body_path("fablepending0").read_bytes()

        # codex-reviewer の昇格経路。
        self.start("codexpromote0", agent_type=CODEX_REVIEWER)
        self.write_pending(self.work, self.pending_content(head))
        self.write_comment_body(self.work, self.comment_body_content(head))
        self.stop(
            "codexpromote0",
            "# Codex Review\n\nStatus: pass\nFindings: 0",
            agent_type=CODEX_REVIEWER,
        )
        self.assertTrue(self.final_marker_path(self.work).exists())

        # codex-reviewer の破棄経路。
        self.start("codexdiscard0", agent_type=CODEX_REVIEWER)
        self.stop(
            "codexdiscard0",
            "# Codex Review\n\nStatus: execution-failed\n",
            agent_type=CODEX_REVIEWER,
        )

        self.assertEqual(self.snapshot(self.fable_record_paths()), fable_before)
        self.assertEqual(
            self.fable_handback_body_path("fablepending0").read_bytes(),
            handback_before,
        )

    def test_post_tool_use_failure_of_fable_touches_nothing(self) -> None:
        head = self.head_sha(self.work)
        fable_before = self.write_existing_fable_record(head)
        pending = self.write_pending(self.work, self.pending_content(head))
        body = self.write_comment_body(self.work, self.comment_body_content(head))
        self.run_hook(
            {
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "Agent",
                "tool_input": {"subagent_type": FABLE_REVIEWER},
                "error": "agent failed",
                "is_interrupt": False,
            }
        )
        self.assertEqual(self.snapshot(self.fable_record_paths()), fable_before)
        self.assertTrue(pending.exists())
        self.assertTrue(body.exists())


if __name__ == "__main__":
    unittest.main()

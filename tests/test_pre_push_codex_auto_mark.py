"""pre-push-codex-review auto-mark の subagent lifecycle 契約テスト。

pre-push-codex-review plugin は codex review 経路のマーカー
(`.claude-pre-push-codex-reviewed`) だけを所有する。wrapper
(`run-pre-push-codex-review.sh`) が review 開始時点の hash を pending attestation に
書き、`pre-push-codex-review:codex-reviewer` subagent の lifecycle hook
(SubagentStart / SubagentStop) が正規 report と hash 一致を確認して final marker へ
昇格させる。

契約 (auto-mark.sh):

- SubagentStart (agent_type が `pre-push-codex-review:codex-reviewer` の完全一致):
  - 開始時 review hash を launch attestation
    (git-dir/.claude-pre-push-launch-<agent_id>) に書く (one-shot 記録)
- SubagentStop (同 agent_type の完全一致):
  - launch attestation は最初の SubagentStop で必ず消費する (one-shot。検証失敗でも
    再 stop で marker が書ける経路を残さない)
  - last_assistant_message 全体で `^Status: (pass|findings|execution-failed)$` に
    一致する行がちょうど 1 つ、かつ値が pass|findings のときのみ有効
  - 開始時 hash と stop 時点の現在 hash が一致し、かつ wrapper の pending
    attestation が regular file かつ現在 hash と一致する場合のみ final marker へ
    昇格する。不一致・symlink・欠落は pending を消費して skip
  - attestation 無し / 既存 tombstone の terminal な拒否経路では pending
    attestation を破棄する (orphan 化した pending を後続の別 stop が昇格できる
    経路を塞ぐ)
- PostToolUseFailure (Agent|Task): codex pending attestation の best-effort
  破棄のみ (補助的な掃除経路)
- wrapper: 成功時は pending attestation だけを書き final marker は書かない。
  失敗時は stale pending attestation を掃除する
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "pre-push-codex-review"
AUTO_MARK = PLUGIN_DIR / "hooks" / "scripts" / "auto-mark.sh"
RUN_CODEX_REVIEW = (
    PLUGIN_DIR / "hooks" / "scripts" / "run-pre-push-codex-review.sh"
)
HOOKS_CONFIG = PLUGIN_DIR / "hooks" / "hooks.json"
CODEX_REVIEWER = "pre-push-codex-review:codex-reviewer"
CODEX_MARKER = ".claude-pre-push-codex-reviewed"
CODEX_PENDING_MARKER = ".claude-pre-push-codex-reviewed.pending"
LAUNCH_ATTESTATION_PREFIX = ".claude-pre-push-launch-"
LAUNCH_TOMBSTONE_PREFIX = ".claude-pre-push-done-"
DEFAULT_AGENT_ID = "a1b2c3d4e5f6a7b8c"
CODEX_REVIEWER_MATCHER = "^pre-push-codex-review:codex-reviewer$"


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class PrePushCodexAutoMarkTest(unittest.TestCase):
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

    def git_dir(self, work: Path) -> Path:
        value = subprocess.check_output(
            ["git", "rev-parse", "--absolute-git-dir"], cwd=work
        )
        return Path(value.decode().strip())

    def expected_review_hash(self, work: Path) -> str:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{commit}"], cwd=work
        ).decode().strip()
        merge_base = subprocess.check_output(
            ["git", "merge-base", "origin/master", "HEAD"], cwd=work
        ).decode().strip()
        chunks = [
            f"head {head}\n".encode(),
            f"mbase {merge_base}\n".encode(),
        ]
        for args in (
            ("diff", "--no-ext-diff", "--no-textconv", merge_base, "HEAD"),
            ("diff", "--no-ext-diff", "--no-textconv", "--cached"),
            ("diff", "--no-ext-diff", "--no-textconv"),
        ):
            chunks.append(
                subprocess.check_output(["git", *args], cwd=work).rstrip(b"\n")
            )
        return hashlib.sha256(b"".join(chunks)).hexdigest()

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

    def wrapper_environment(
        self, home: Path, base_env: dict[str, str]
    ) -> dict[str, str]:
        real_node = Path(
            subprocess.check_output(
                ["node", "-e", "process.stdout.write(process.execPath)"]
            )
            .decode()
            .strip()
        )
        env = base_env.copy()
        existing_path = env.get("PATH")
        env["PATH"] = (
            f"{real_node.parent}{os.pathsep}{existing_path}"
            if existing_path
            else str(real_node.parent)
        )
        env["HOME"] = str(home)
        return env

    def marker_path(self, work: Path) -> Path:
        return self.git_dir(work) / CODEX_MARKER

    def codex_pending_marker_path(self, work: Path) -> Path:
        return self.git_dir(work) / CODEX_PENDING_MARKER

    def launch_attestation_path(
        self, work: Path, agent_id: str = DEFAULT_AGENT_ID
    ) -> Path:
        return self.git_dir(work) / f"{LAUNCH_ATTESTATION_PREFIX}{agent_id}"

    def launch_tombstone_path(
        self, work: Path, agent_id: str = DEFAULT_AGENT_ID
    ) -> Path:
        return self.git_dir(work) / f"{LAUNCH_TOMBSTONE_PREFIX}{agent_id}"

    def assert_no_marker(
        self, work: Path, payload: dict[str, object]
    ) -> None:
        marker = self.marker_path(work)
        marker.unlink(missing_ok=True)
        result = self.run_hook(work, payload)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertFalse(marker.exists(), result.stderr.decode())

    # ------------------------------------------------------------------
    # SubagentStart: launch attestation
    # ------------------------------------------------------------------

    def test_subagent_start_writes_launch_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            attestation = self.launch_attestation_path(work)
            attestation.unlink(missing_ok=True)
            self.run_start(work)
            self.assertTrue(attestation.exists())
            self.assertEqual(
                attestation.read_text(encoding="utf-8"),
                self.expected_review_hash(work),
            )

    # ------------------------------------------------------------------
    # SubagentStop: marker 書き込み
    # ------------------------------------------------------------------

    def test_stop_pass_or_findings_writes_marker_and_consumes(self) -> None:
        reports = {
            "pass": "# Codex Review\n\nStatus: pass\nFindings: 0",
            "findings": (
                "# Codex Review\n\nStatus: findings\n\n"
                "## Finding CODEX-example-correctness"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            for status_index, (status, report) in enumerate(reports.items()):
                with self.subTest(status=status):
                    # 独立した review サイクルごとに一意の agent_id を使う
                    # (実運用では agent_id が spawn ごとに一意である現実を
                    # モデルする。 同一 agent_id を使い回すと 2 サイクル目が
                    # launch tombstone に阻まれ、 この subTest が検証すべき
                    # 「report 正常完了で marker が書かれる」経路を通らなくなる)。
                    agent_id = f"{DEFAULT_AGENT_ID}{status_index}"
                    marker = self.marker_path(work)
                    marker.unlink(missing_ok=True)
                    self.run_start(work, agent_id=agent_id)
                    self.codex_pending_marker_path(work).write_text(
                        self.expected_review_hash(work), encoding="utf-8"
                    )
                    result = self.run_hook(
                        work, self.stop_payload(report, agent_id=agent_id)
                    )
                    self.assertEqual(
                        result.returncode, 0, result.stderr.decode()
                    )
                    self.assertTrue(marker.exists(), result.stderr.decode())
                    self.assertRegex(
                        marker.read_text(encoding="utf-8"),
                        re.compile(r"^[0-9a-f]{64}$"),
                    )
                    self.assertFalse(
                        self.launch_attestation_path(work, agent_id).exists()
                    )

    def test_codex_stop_without_attestation_discards_pending(self) -> None:
        # codex-reviewer の terminal な拒否経路 (attestation 無し = resume 再 stop・
        # 偽装 stop 等) では、git-dir 共有の pending attestation を破棄する。resume
        # された subagent が wrapper を再実行して書き直した pending を放置すると、
        # 後続の別の codex-reviewer stop が orphan 化した pending を昇格できて
        # しまうため (skip_marker と対称の掃除)。
        report = "# Codex Review\n\nStatus: pass\nFindings: 0"
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            pending = self.codex_pending_marker_path(work)
            pending.write_text(
                self.expected_review_hash(work), encoding="utf-8"
            )
            self.assertFalse(self.launch_attestation_path(work).exists())
            self.assert_no_marker(work, self.stop_payload(report))
            self.assertFalse(pending.exists())

    def test_codex_stop_with_existing_tombstone_discards_pending(self) -> None:
        # 既存 tombstone の terminal な拒否経路でも pending attestation を破棄する
        # (attestation 無し経路と同じ orphan 化の遮断)。
        report = "# Codex Review\n\nStatus: pass\nFindings: 0"
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            self.run_start(work)
            attestation = self.launch_attestation_path(work)
            tombstone = self.launch_tombstone_path(work)
            tombstone.write_text(
                attestation.read_text(encoding="utf-8"), encoding="utf-8"
            )
            pending = self.codex_pending_marker_path(work)
            pending.write_text(
                self.expected_review_hash(work), encoding="utf-8"
            )
            self.assert_no_marker(work, self.stop_payload(report))
            self.assertFalse(pending.exists())
            self.assertFalse(attestation.exists())

    # ------------------------------------------------------------------
    # codex-reviewer: wrapper pending attestation との二重束縛
    # ------------------------------------------------------------------

    def test_codex_stop_requires_matching_pending_attestation(self) -> None:
        report = "# Codex Review\n\nStatus: pass\nFindings: 0"
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))

            # 2 つの独立サイクル (pending なし / pending 不一致) にそれぞれ
            # 一意の agent_id を使う (同一 agent_id だと 1 サイクル目の stop が
            # 作る launch tombstone に 2 サイクル目の run_start が阻まれ、
            # pending 不一致検証の経路を通らなくなる)。
            first_agent_id = f"{DEFAULT_AGENT_ID}0"
            self.run_start(work, agent_id=first_agent_id)
            self.assert_no_marker(
                work, self.stop_payload(report, agent_id=first_agent_id)
            )

            second_agent_id = f"{DEFAULT_AGENT_ID}1"
            self.run_start(work, agent_id=second_agent_id)
            pending = self.codex_pending_marker_path(work)
            pending.write_text("0" * 64, encoding="utf-8")
            self.assert_no_marker(
                work, self.stop_payload(report, agent_id=second_agent_id)
            )
            self.assertFalse(pending.exists())

    def test_codex_pending_symlink_is_rejected(self) -> None:
        report = "# Codex Review\n\nStatus: pass\nFindings: 0"
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            self.run_start(work)
            target = temporary / "target"
            target.write_text(
                self.expected_review_hash(work), encoding="utf-8"
            )
            pending = self.codex_pending_marker_path(work)
            pending.symlink_to(target)
            self.assert_no_marker(work, self.stop_payload(report))
            self.assertFalse(os.path.lexists(pending))

    # ------------------------------------------------------------------
    # 補助経路: PostToolUseFailure
    # ------------------------------------------------------------------

    def test_post_tool_use_failure_discards_codex_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            work = self.create_feature_repository(Path(temporary_name))
            pending = self.codex_pending_marker_path(work)
            pending.write_text(self.expected_review_hash(work), encoding="utf-8")
            failure_payload = {
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "Agent",
                "tool_input": {"subagent_type": CODEX_REVIEWER},
                "error": "agent failed after wrapper completion",
                "is_interrupt": False,
            }
            result = self.run_hook(work, failure_payload)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertFalse(
                self.marker_path(work).exists(), result.stderr.decode()
            )
            self.assertFalse(pending.exists())

    # ------------------------------------------------------------------
    # wrapper — pending attestation の書き込みと掃除
    # ------------------------------------------------------------------

    @unittest.skipUnless(shutil.which("node"), "wrapper integration requires node")
    def test_wrapper_writes_pending_attestation_not_final_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            home = temporary / "home"
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
            companion.write_text(
                'process.stdout.write("# Review\\n\\nNo findings.\\n");\n',
                encoding="utf-8",
            )
            shim_directory = temporary / "node-shim"
            shim_directory.mkdir()
            node_shim = shim_directory / "node"
            node_shim.write_text("#!/bin/sh\nexit 126\n", encoding="utf-8")
            node_shim.chmod(0o755)
            base_env = os.environ.copy()
            base_env["PATH"] = (
                f"{shim_directory}{os.pathsep}{base_env.get('PATH', '')}"
            )
            env = self.wrapper_environment(home, base_env)
            result = subprocess.run(
                ["bash", str(RUN_CODEX_REVIEW)],
                cwd=work,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn("# Review", result.stdout.decode())
            final_marker = self.marker_path(work)
            pending = self.codex_pending_marker_path(work)
            self.assertFalse(final_marker.exists(), result.stderr.decode())
            self.assertTrue(pending.exists(), result.stderr.decode())
            self.assertEqual(
                pending.read_text(encoding="utf-8"),
                self.expected_review_hash(work),
            )

    @unittest.skipUnless(shutil.which("node"), "wrapper integration requires node")
    def test_wrapper_failure_removes_stale_pending_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            work = self.create_feature_repository(temporary)
            pending = self.codex_pending_marker_path(work)
            pending.write_text("stale", encoding="utf-8")
            home = temporary / "home"
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
            companion.write_text(
                'process.stderr.write("intentional companion failure\\n");\n'
                "process.exit(1);\n",
                encoding="utf-8",
            )
            env = self.wrapper_environment(home, os.environ.copy())
            result = subprocess.run(
                ["bash", str(RUN_CODEX_REVIEW)],
                cwd=work,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "intentional companion failure",
                result.stderr.decode(),
            )
            self.assertFalse(pending.exists(), result.stderr.decode())
            self.assertFalse(self.marker_path(work).exists())

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
        self.assertIn(
            "auto-mark.sh", start_groups[0]["hooks"][0]["command"]
        )

        stop_groups = [
            group
            for group in hooks.get("SubagentStop", [])
            if group.get("matcher") == CODEX_REVIEWER_MATCHER
        ]
        self.assertEqual(len(stop_groups), 1)
        self.assertIn(
            "auto-mark.sh", stop_groups[0]["hooks"][0]["command"]
        )

        for group in hooks.get("PostToolUse", []):
            for hook in group.get("hooks", []):
                self.assertNotIn(
                    "auto-mark.sh",
                    hook.get("command", ""),
                    "completion 検知は subagent lifecycle hook のみが担う",
                )

        failure_groups = hooks["PostToolUseFailure"]
        self.assertEqual(len(failure_groups), 1)
        self.assertEqual(failure_groups[0]["matcher"], "Agent|Task")
        self.assertIn(
            "auto-mark.sh",
            failure_groups[0]["hooks"][0]["command"],
        )


if __name__ == "__main__":
    unittest.main()

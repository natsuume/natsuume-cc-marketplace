"""agent-discipline: 分業規律の Opus 5 対応 (3-way 配送) 契約テスト
(PR2: agent-discipline 分業規律の Opus 5 対応)。

背景: 分業規律の配送を「fable / opus / その他非 Fable」の 3-way に拡張する。実装は
spec-first 2 段階で進め、本ファイルは実装前の Phase A として契約を固定する。

- 静的契約 (``DisciplineOpusStaticContractTests``): discipline-opus.md (新規) の
  rule ID セットが discipline-fable.md / discipline-sonnet.md と完全一致すること、
  discipline-opus.md が必須文言 (verifier 委任の基準 (限定式) 等) を含むこと、
  discipline-fable.md / discipline-sonnet.md の両方に Opus 5 向け過剰検証注意文言が
  追記されること、lint-prompt-sync.sh と workflow yml の paths が discipline-opus.md
  を参照すること、サイズ予算 (discipline-opus.md <= 7,000 UTF-16 units、
  discipline-sonnet.md + discipline-preamble-self-gate.md 合算 < 8,000 UTF-16
  units) を満たすことを検証する。discipline-opus.md は Phase B で新設されるため、
  本クラスの全テストは Phase A 時点で red になる。

- 挙動契約 (``InjectDisciplineOpusBehaviorTests``): inject-discipline.sh
  (UserPromptSubmit hook) を隔離した TMPDIR 上で直接実行し、マーカー無し /
  sonnet-gate の各状態から fable / opus / sonnet の state へ分岐したときの
  出力・マーカー遷移を固定する。fable / sonnet に関する特性化 (a, c, d, f) は
  現行実装で既に成立するため Phase A 時点で green になる。opus 分岐 (b, e) は
  inject-discipline.sh の 3-way 化 (Phase B) まで、現行実装が opus state を
  非 fable として一律 sonnet 版で処理するため red になる。

Phase B (discipline-opus.md 新設・discipline-fable.md / discipline-sonnet.md への
追記・inject-discipline.sh の 3-way 化・lint-prompt-sync.sh / workflow yml の
paths 追加) が完了すると、本ファイルの全テストが green になる想定。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "agent-discipline"
PROMPTS_DIR = PLUGIN_DIR / "hooks" / "prompts"

DISCIPLINE_FABLE_MD = PROMPTS_DIR / "discipline-fable.md"
DISCIPLINE_SONNET_MD = PROMPTS_DIR / "discipline-sonnet.md"
DISCIPLINE_OPUS_MD = PROMPTS_DIR / "discipline-opus.md"
DISCIPLINE_PREAMBLE_SELF_GATE_MD = PROMPTS_DIR / "discipline-preamble-self-gate.md"
INJECT_DISCIPLINE_SH = PLUGIN_DIR / "hooks" / "scripts" / "inject-discipline.sh"
LINT_PROMPT_SYNC_SH = PLUGIN_DIR / "scripts" / "lint-prompt-sync.sh"
WORKFLOW_YML = ROOT / ".github" / "workflows" / "agent-discipline-prompt-lint.yml"

RULE_ID_PATTERN = re.compile(r"<!--\s*rule:([a-zA-Z0-9_-]+)\s*-->")

OPUS_DISCIPLINE_PATH = "plugins/agent-discipline/hooks/prompts/discipline-opus.md"


def extract_rule_ids(text: str) -> set[str]:
    """discipline-*.md の `<!-- rule:<id> -->` マーカーから rule ID 集合を抽出する。"""
    return set(RULE_ID_PATTERN.findall(text))


def utf16_units(text: str) -> int:
    """文字列の UTF-16 code unit 数 (JS の `string.length` 相当) を返す。"""
    return len(text.encode("utf-16-le")) // 2


class DisciplineOpusStaticContractTests(unittest.TestCase):
    """静的契約: discipline-opus.md 新設と discipline-fable.md /
    discipline-sonnet.md への追記、lint-prompt-sync.sh / workflow yml の paths
    追加を検証する。discipline-opus.md は Phase B で新設されるため、本クラスの
    全テストは Phase A 時点で red になる (存在しないファイルへの依存、または
    Phase B で追記される文言の不在による)。
    """

    def test_rule_id_sets_match_across_three_discipline_files(self) -> None:
        """discipline-opus.md / discipline-fable.md / discipline-sonnet.md の
        rule ID セットが完全一致すること。discipline-opus.md 不在時はまずファイル
        存在を検証する (Phase A では red)。
        """
        self.assertTrue(
            DISCIPLINE_OPUS_MD.is_file(),
            f"{DISCIPLINE_OPUS_MD} が存在しません (Phase B で新設される想定)",
        )
        opus_ids = extract_rule_ids(DISCIPLINE_OPUS_MD.read_text(encoding="utf-8"))
        fable_ids = extract_rule_ids(DISCIPLINE_FABLE_MD.read_text(encoding="utf-8"))
        sonnet_ids = extract_rule_ids(DISCIPLINE_SONNET_MD.read_text(encoding="utf-8"))

        self.assertTrue(opus_ids, "discipline-opus.md から rule ID が抽出できません")
        self.assertEqual(
            fable_ids, opus_ids, "discipline-fable.md と discipline-opus.md の rule ID が不一致"
        )
        self.assertEqual(
            sonnet_ids, opus_ids, "discipline-sonnet.md と discipline-opus.md の rule ID が不一致"
        )

    def test_discipline_opus_contains_required_phrases(self) -> None:
        """discipline-opus.md が verifier 委任の基準・委任粒度の抑制・過剰検証禁止の
        必須文言を含むこと。
        """
        self.assertTrue(
            DISCIPLINE_OPUS_MD.is_file(),
            f"{DISCIPLINE_OPUS_MD} が存在しません (Phase B で新設される想定)",
        )
        text = DISCIPLINE_OPUS_MD.read_text(encoding="utf-8")
        # subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が
        # SUBFAILED として分離報告される一方、親テストノード自体は PASSED と表示され
        # green/red の判定が曖昧になるため、通常の逐次 assertIn で 1 テスト = 1 判定に保つ。
        missing = [phrase for phrase in (
            "verifier 委任の基準 (限定式)",
            "委任は真に独立した相応の規模の作業に限る",
            "Opus 5 への委任では汎用的な再確認指示を加えない",
        ) if phrase not in text]
        self.assertEqual([], missing, f"discipline-opus.md に含まれない必須文言: {missing}")

    def test_fable_and_sonnet_contain_opus_overcheck_caution(self) -> None:
        """discipline-fable.md / discipline-sonnet.md の両方に、Opus 5 への委任で
        汎用的な再確認指示を加えない旨の注意書きが追記されていること (Phase B 追記)。
        """
        phrase = "Opus 5 への委任では汎用的な再確認指示を加えない"
        fable_text = DISCIPLINE_FABLE_MD.read_text(encoding="utf-8")
        sonnet_text = DISCIPLINE_SONNET_MD.read_text(encoding="utf-8")

        # subTest は使わない (上記と同じ理由: pytest の SUBFAILED/PASSED 分離表示による
        # 判定の曖昧さを避けるため)。
        missing_in = [
            name
            for name, text in (
                ("discipline-fable.md", fable_text),
                ("discipline-sonnet.md", sonnet_text),
            )
            if phrase not in text
        ]
        self.assertEqual([], missing_in, f"{phrase!r} を含まないファイル: {missing_in}")

    def test_lint_prompt_sync_references_discipline_opus(self) -> None:
        """lint-prompt-sync.sh が discipline-opus.md のパスを参照すること
        (チェック 4 の 3 ファイル総当たり拡張、DISCIPLINE_OPUS_MD 定数の追加)。
        """
        text = LINT_PROMPT_SYNC_SH.read_text(encoding="utf-8")
        self.assertIn(OPUS_DISCIPLINE_PATH, text)

    def test_workflow_paths_reference_discipline_opus_twice(self) -> None:
        """agent-discipline-prompt-lint.yml の pull_request / push 両方の paths に
        discipline-opus.md が 1 回ずつ (計 2 回) 追加されていること。
        """
        text = WORKFLOW_YML.read_text(encoding="utf-8")
        occurrences = text.count(OPUS_DISCIPLINE_PATH)
        self.assertEqual(
            2,
            occurrences,
            "pull_request / push 両方の paths ブロックに 1 回ずつ出現するはず",
        )

    def test_discipline_opus_size_and_combined_budget(self) -> None:
        """discipline-opus.md のサイズが 7,000 UTF-16 units 以下であること。
        discipline-sonnet.md + discipline-preamble-self-gate.md の合算サイズが
        8,000 UTF-16 units 未満であること。
        """
        self.assertTrue(
            DISCIPLINE_OPUS_MD.is_file(),
            f"{DISCIPLINE_OPUS_MD} が存在しません (Phase B で新設される想定)",
        )
        opus_units = utf16_units(DISCIPLINE_OPUS_MD.read_text(encoding="utf-8"))
        self.assertLessEqual(opus_units, 7000)

        sonnet_units = utf16_units(DISCIPLINE_SONNET_MD.read_text(encoding="utf-8"))
        self_gate_units = utf16_units(
            DISCIPLINE_PREAMBLE_SELF_GATE_MD.read_text(encoding="utf-8")
        )
        self.assertLess(sonnet_units + self_gate_units, 8000)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class InjectDisciplineOpusBehaviorTests(unittest.TestCase):
    """挙動契約: inject-discipline.sh を TMPDIR 隔離で直接実行し、fable / opus /
    sonnet の 3 state と marker (無し・sonnet-gate) の組み合わせで出力とマーカー
    遷移を固定する。実リポジトリの ``${TMPDIR:-/tmp}/agent-discipline-state`` を
    汚さないよう、各テストは一意な ``tempfile.TemporaryDirectory()`` を TMPDIR
    として渡す。session_id もテストごとに一意な値を使う。
    """

    def make_state_paths(self, tmp_dir: str, session_id: str) -> dict[str, Path]:
        state_dir = Path(tmp_dir) / "agent-discipline-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        return {
            "dir": state_dir,
            "marker": state_dir / f"delivered-discipline-{session_id}",
            "pending": state_dir / f"pending-model-{session_id}",
            "state": state_dir / f"model-{session_id}",
        }

    def run_inject_discipline(
        self, session_id: str, tmp_dir: str
    ) -> subprocess.CompletedProcess[str]:
        payload = json.dumps(
            {"session_id": session_id, "hook_event_name": "UserPromptSubmit"},
            ensure_ascii=False,
        )
        env = os.environ.copy()
        env["TMPDIR"] = tmp_dir
        return subprocess.run(
            ["/bin/bash", str(INJECT_DISCIPLINE_SH)],
            cwd=ROOT,
            env=env,
            input=payload,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_a_fable_state_delivers_fable_version_and_marks_final(self) -> None:
        """特性化 (green): marker/pending 無し + state=claude-fable-5 は Fable 版を
        配送し marker=final にする。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-behavior-a-fable"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["state"].write_text("claude-fable-5", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("分業規律 (Fable セッション)", context)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))

    def test_b_opus_state_delivers_opus_version_and_marks_final(self) -> None:
        """契約 (Phase B、現状 red): marker/pending 無し + state=claude-opus-5 は
        Opus 版 (見出し「分業規律 (Opus)」+ verifier 委任の基準 (限定式)) を配送し
        marker=final にする。現行実装は opus state を非 fable として一律 sonnet 版で
        処理するため、この特性化は Phase A 時点で red になる
        (inject-discipline.sh の 3-way 化待ち)。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-behavior-b-opus"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["state"].write_text("claude-opus-5", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("分業規律 (Opus)", context)
            self.assertIn("verifier 委任の基準 (限定式)", context)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))

    def test_c_sonnet_state_delivers_sonnet_version_and_marks_final(self) -> None:
        """特性化 (green): marker/pending 無し + state=claude-sonnet-5 は Sonnet 版を
        配送し marker=final にする。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-behavior-c-sonnet"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["state"].write_text("claude-sonnet-5", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("分業規律 (Sonnet)", context)
            self.assertIn("verifier 委任の義務 (必須)", context)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))

    def test_d_pending_marker_delivers_self_gate_sonnet_and_marks_sonnet_gate(
        self,
    ) -> None:
        """特性化 (green): marker 無し + pending ファイル存在は self-gate 前置き付き
        Sonnet 版を配送し marker=sonnet-gate にする。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-behavior-d-pending"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["pending"].write_text("pending", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("(自己ゲート)", context)
            self.assertIn("分業規律 (Sonnet)", context)
            self.assertEqual("sonnet-gate", paths["marker"].read_text(encoding="utf-8"))

    def test_e_sonnet_gate_opus_confirmed_delivers_correction_and_marks_final(
        self,
    ) -> None:
        """契約 (Phase B、現状 red): marker=sonnet-gate + state=claude-opus-5
        (pending 無し) は one-shot 補正 (補正 prefix + Opus 版) を配送し
        marker=final にする。現行実装は非 fable 確定時に追加配送をせず marker のみ
        final に更新するため、この特性化は Phase A 時点で red になる
        (inject-discipline.sh の 3-way 化待ち)。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-behavior-e-opus-gate"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["marker"].write_text("sonnet-gate", encoding="utf-8")
            paths["state"].write_text("claude-opus-5", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(
                result.stdout.strip(),
                "opus 確定時は one-shot 補正配送が期待されるが、現行実装は空応答"
                " (Phase A 時点の既知の red)",
            )
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("one-shot 補正", context)
            self.assertIn("分業規律 (Opus)", context)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))

    def test_f_sonnet_gate_sonnet_confirmed_delivers_nothing_and_marks_final(
        self,
    ) -> None:
        """特性化 (green): marker=sonnet-gate + state=claude-sonnet-5 (pending 無し)
        は追加配送なし (stdout 空) で marker=final にする。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-behavior-f-sonnet-gate"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["marker"].write_text("sonnet-gate", encoding="utf-8")
            paths["state"].write_text("claude-sonnet-5", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stdout)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

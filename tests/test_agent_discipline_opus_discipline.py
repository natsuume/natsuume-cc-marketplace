"""agent-discipline: 分業規律の Opus 版 / Sonnet 版 2-way 配送の契約テスト。

分業規律はモデル ID で 2 版に分類して配送する: opus を含む → Opus 版
(discipline-opus.md)、それ以外 (fable・sonnet・haiku 等) → Sonnet 版 (discipline-sonnet.md)。

- 静的契約 (``DisciplineOpusStaticContractTests``): discipline-opus.md と
  discipline-sonnet.md の rule ID セットが完全一致すること、discipline-opus.md が必須文言
  (verifier 委任の基準 (限定式) 等) を含むこと、discipline-sonnet.md に Opus 5 向け過剰検証
  注意文言があること、lint-prompt-sync.sh と workflow yml の paths が discipline-opus.md を
  参照すること、サイズ予算 (discipline-opus.md <= 7,000 UTF-16 units、
  discipline-sonnet.md + discipline-preamble-self-gate.md 合算 < 8,000 UTF-16 units) を
  満たすことを検証する。

- 挙動契約 (``InjectDisciplineOpusBehaviorTests``): inject-discipline.sh
  (UserPromptSubmit hook) を隔離した TMPDIR 上で直接実行し、マーカー無し /
  sonnet-gate の各状態から fable / opus / sonnet / haiku の state へ分岐したときの
  出力・マーカー遷移を固定する。canonical 文言は ``OPUS_DISCIPLINE_HEADING`` /
  ``OPUS_CORRECTION_PREFIX`` / ``OPUS_CORRECTION_PRIORITIZE_PHRASE`` /
  ``OPUS_CORRECTION_DISCARD_SONNET_PHRASE`` に固定する。Opus 直接配送・Opus 補正・
  pending 時の self-gate+Sonnet それぞれの additionalContext が UTF-16 code unit 数
  8,000 未満であることも検証する。
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

DISCIPLINE_SONNET_MD = PROMPTS_DIR / "discipline-sonnet.md"
DISCIPLINE_OPUS_MD = PROMPTS_DIR / "discipline-opus.md"
DISCIPLINE_PREAMBLE_SELF_GATE_MD = PROMPTS_DIR / "discipline-preamble-self-gate.md"
INJECT_DISCIPLINE_SH = PLUGIN_DIR / "hooks" / "scripts" / "inject-discipline.sh"
LINT_PROMPT_SYNC_SH = PLUGIN_DIR / "scripts" / "lint-prompt-sync.sh"
WORKFLOW_YML = ROOT / ".github" / "workflows" / "agent-discipline-prompt-lint.yml"

RULE_ID_PATTERN = re.compile(r"<!--\s*rule:([a-zA-Z0-9_-]+)\s*-->")

OPUS_DISCIPLINE_PATH = "plugins/agent-discipline/hooks/prompts/discipline-opus.md"

# canonical 文言 (実装とテストの共通契約)。
# Opus 直接配送の見出し (heading + 空行 + discipline-opus.md 本文の構成で使われる)。
OPUS_DISCIPLINE_HEADING = "# agent-discipline: 分業規律 (Opus)"

# Opus one-shot 補正 prefix (Opus 系に確定した場合)。補正 context は
# prefix + 空行 + OPUS_DISCIPLINE_HEADING + 空行 + discipline-opus.md 本文の構成になる。
OPUS_CORRECTION_PREFIX = (
    "(one-shot 補正) セッション開始時点ではモデルを判定できず、自己ゲート付きで SONNET 向けの分業規律を暫定配送していた。"
    "会話の進行によりこのセッションのモデルが Opus 版分業規律の対象 (Opus 系) であると確定したため、"
    "以後は本メッセージ以下の Opus 版分業規律を優先し、先に配送済みの Sonnet 版分業規律は破棄すること。"
)
OPUS_CORRECTION_PRIORITIZE_PHRASE = "以後は本メッセージ以下の Opus 版分業規律を優先し"
OPUS_CORRECTION_DISCARD_SONNET_PHRASE = "先に配送済みの Sonnet 版分業規律は破棄すること"

# Sonnet 版直接配送の見出し (heading + 空行 + discipline-sonnet.md 本文の構成で使われる)。
SONNET_DISCIPLINE_HEADING = "# agent-discipline: 分業規律 (Sonnet)"


def extract_rule_ids(text: str) -> set[str]:
    """discipline-*.md の `<!-- rule:<id> -->` マーカーから rule ID 集合を抽出する。"""
    return set(RULE_ID_PATTERN.findall(text))


def utf16_units(text: str) -> int:
    """文字列の UTF-16 code unit 数 (JS の `string.length` 相当) を返す。"""
    return len(text.encode("utf-16-le")) // 2


class DisciplineOpusStaticContractTests(unittest.TestCase):
    """静的契約: discipline-opus.md / discipline-sonnet.md の内容と、
    lint-prompt-sync.sh / workflow yml の paths を検証する。
    """

    def test_rule_id_sets_match_across_discipline_files(self) -> None:
        """discipline-opus.md / discipline-sonnet.md の rule ID セットが完全一致すること。"""
        self.assertTrue(
            DISCIPLINE_OPUS_MD.is_file(),
            f"{DISCIPLINE_OPUS_MD} が存在しません",
        )
        opus_ids = extract_rule_ids(DISCIPLINE_OPUS_MD.read_text(encoding="utf-8"))
        sonnet_ids = extract_rule_ids(DISCIPLINE_SONNET_MD.read_text(encoding="utf-8"))

        self.assertTrue(opus_ids, "discipline-opus.md から rule ID が抽出できません")
        self.assertEqual(
            sonnet_ids, opus_ids, "discipline-sonnet.md と discipline-opus.md の rule ID が不一致"
        )

    def test_discipline_opus_contains_required_phrases(self) -> None:
        """discipline-opus.md が verifier 委任の基準・委任粒度の抑制・過剰検証禁止の
        必須文言を含むこと。
        """
        self.assertTrue(
            DISCIPLINE_OPUS_MD.is_file(),
            f"{DISCIPLINE_OPUS_MD} が存在しません",
        )
        text = DISCIPLINE_OPUS_MD.read_text(encoding="utf-8")
        # subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が
        # SUBFAILED として分離報告される一方、親テストノード自体は PASSED と表示され
        # green/red の判定が曖昧になるため、通常の逐次 assertIn で 1 テスト = 1 判定に保つ。
        missing = [phrase for phrase in (
            "verifier 委任の基準 (限定式)",
            "委任は真に独立した相応の規模の作業に限る",
            "Opus 5 / Opus 5.5 への委任では汎用的な再確認指示を加えない",
        ) if phrase not in text]
        self.assertEqual([], missing, f"discipline-opus.md に含まれない必須文言: {missing}")

    def test_sonnet_contains_opus_overcheck_caution(self) -> None:
        """discipline-sonnet.md に、Opus 5 への委任で汎用的な再確認指示を加えない旨の
        注意書きがあること。
        """
        phrase = "Opus 5 への委任では汎用的な再確認指示を加えない"
        sonnet_text = DISCIPLINE_SONNET_MD.read_text(encoding="utf-8")
        self.assertIn(phrase, sonnet_text)

    def test_lint_prompt_sync_references_discipline_opus(self) -> None:
        """lint-prompt-sync.sh が discipline-opus.md のパスを参照すること
        (チェック 4 の discipline-sonnet.md と discipline-opus.md の rule ID セット一致検査)。
        """
        text = LINT_PROMPT_SYNC_SH.read_text(encoding="utf-8")
        self.assertIn(OPUS_DISCIPLINE_PATH, text)

    def test_workflow_pull_request_and_push_paths_each_reference_discipline_opus_once(
        self,
    ) -> None:
        """agent-discipline-prompt-lint.yml の pull_request.paths と push.paths の
        それぞれに discipline-opus.md が 1 回ずつ含まれることを個別に検証する
        (合計出現数のみの検証は、例えば discipline-opus.md への参照が pull_request
        側にだけ 2 回書かれ push 側に無い、といったケースでも合計 2 に一致してしまう
        false positive を生むため、trigger ごとに検証する)。

        PyYAML は GitHub Actions の `on:` キーを YAML 1.1 の慣習でブール値
        `True` として解釈してしまう既知の落とし穴があり、本テストの意図が
        かえって読みにくくなるため使わない。代わりに pull_request: ブロックと
        push: ブロックをそれぞれの開始位置 (次の兄弟キー、または push の後の
        トップレベルキー `permissions:`) で切り出す文字列処理を用いる。
        """
        text = WORKFLOW_YML.read_text(encoding="utf-8")
        pull_request_start = text.index("\n  pull_request:")
        push_start = text.index("\n  push:")
        permissions_start = text.index("\npermissions:")
        self.assertLess(pull_request_start, push_start)
        self.assertLess(push_start, permissions_start)

        pull_request_block = text[pull_request_start:push_start]
        push_block = text[push_start:permissions_start]

        self.assertEqual(
            1,
            pull_request_block.count(OPUS_DISCIPLINE_PATH),
            "pull_request.paths に discipline-opus.md が 1 回だけ含まれるはず",
        )
        self.assertEqual(
            1,
            push_block.count(OPUS_DISCIPLINE_PATH),
            "push.paths に discipline-opus.md が 1 回だけ含まれるはず",
        )

    def test_discipline_opus_excludes_sonnet_only_verifier_obligation_phrases(
        self,
    ) -> None:
        """discipline-opus.md に、Sonnet 版限定の「一律 verifier 義務」文言
        (「verifier 委任の義務 (必須)」「非自明な全成果物」) が混入していないこと。
        Opus 版は verifier 委任を限定式 (基準を満たす場合のみ) にする設計契約であり、
        Sonnet 版の義務化文言がそのまま紛れ込んでいないかを固定する。
        """
        self.assertTrue(
            DISCIPLINE_OPUS_MD.is_file(),
            f"{DISCIPLINE_OPUS_MD} が存在しません",
        )
        text = DISCIPLINE_OPUS_MD.read_text(encoding="utf-8")
        leaked = [
            phrase
            for phrase in ("verifier 委任の義務 (必須)", "非自明な全成果物")
            if phrase in text
        ]
        self.assertEqual(
            [], leaked, f"discipline-opus.md に混入してはいけない Sonnet 限定文言: {leaked}"
        )

    def test_discipline_opus_size_and_combined_budget(self) -> None:
        """discipline-opus.md のサイズが 7,000 UTF-16 units 以下であること。
        discipline-sonnet.md + discipline-preamble-self-gate.md の合算サイズが
        8,000 UTF-16 units 未満であること。
        """
        self.assertTrue(
            DISCIPLINE_OPUS_MD.is_file(),
            f"{DISCIPLINE_OPUS_MD} が存在しません",
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
    """挙動契約: inject-discipline.sh を TMPDIR 隔離で直接実行し、state (opus / fable /
    sonnet / haiku) と marker (無し・sonnet-gate) の組み合わせで出力とマーカー遷移を
    固定する。分業規律は Opus 版 (opus) と Sonnet 版 (fable を含むそれ以外) の 2 版で配送する。実リポジトリの ``${TMPDIR:-/tmp}/agent-discipline-state`` を
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

    def test_a_fable_state_delivers_sonnet_version_and_marks_final(self) -> None:
        """marker/pending 無し + state=claude-fable-5-1 は Opus 版の対象外 (その他の
        モデル) として Sonnet 版を配送し marker=final にする。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-behavior-a-fable"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["state"].write_text("claude-fable-5-1", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn(SONNET_DISCIPLINE_HEADING, context)
            self.assertNotIn(OPUS_DISCIPLINE_HEADING, context)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))

    def test_b_opus_state_delivers_opus_version_and_marks_final(self) -> None:
        """marker/pending 無し + state=claude-opus-5 は Opus 版 (見出し「分業規律
        (Opus)」+ verifier 委任の基準 (限定式)) を配送し marker=final にする。
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
        """marker/pending 無し + state=claude-sonnet-5 は Sonnet 版を
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
        """marker 無し + pending ファイル存在は self-gate 前置き付き
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
        """marker=sonnet-gate + state=claude-opus-5 (pending 無し) は one-shot 補正
        (補正 prefix + Opus 版) を配送し marker=final にする。
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
                "opus 確定時は one-shot 補正の配送が期待されるが、出力が空",
            )
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("one-shot 補正", context)
            self.assertIn("分業規律 (Opus)", context)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))

    def test_f_sonnet_gate_sonnet_confirmed_delivers_nothing_and_marks_final(
        self,
    ) -> None:
        """marker=sonnet-gate + state=claude-sonnet-5 (pending 無し)
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

    def test_1_haiku_state_delivers_sonnet_version_and_marks_final(self) -> None:
        """marker/pending 無し + state=claude-haiku-4-5 (opus を含まない) は
        Sonnet 版を配送し marker=final にする。Opus 版の対象 (opus) 以外の
        state の既定配送が Sonnet 版であることを固定する。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-checkpoint-1-haiku"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["state"].write_text("claude-haiku-4-5", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("分業規律 (Sonnet)", context)
            self.assertIn("verifier 委任の義務 (必須)", context)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))

    def test_2_state_containing_fable_and_opus_substrings_delivers_opus_version(
        self,
    ) -> None:
        """state 文字列が "fable" と "opus" の両方の部分文字列を含む synthetic 値
        (例: fable-opus-hybrid-test) でも Opus 版が配送される。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-checkpoint-2-fable-opus-substrings"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["state"].write_text("fable-opus-hybrid-test", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn(OPUS_DISCIPLINE_HEADING, context)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))

    def test_3_sonnet_gate_fable_confirmed_delivers_nothing_and_marks_final(
        self,
    ) -> None:
        """marker=sonnet-gate + state=claude-fable-5-1 (pending 無し) は Sonnet 確定時と
        同じく追加配送なし (stdout 空) で marker=final にする (暫定配送済みの Sonnet 版が
        そのまま確定版になる)。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-checkpoint-3-fable-gate"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["marker"].write_text("sonnet-gate", encoding="utf-8")
            paths["state"].write_text("claude-fable-5-1", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stdout)
            self.assertEqual("final", paths["marker"].read_text(encoding="utf-8"))

    def test_4_opus_correction_context_places_opus_phrases_before_heading(
        self,
    ) -> None:
        """marker=sonnet-gate + state=claude-opus-5 (pending 無し) の補正 context には
        OPUS_CORRECTION_PRIORITIZE_PHRASE と OPUS_CORRECTION_DISCARD_SONNET_PHRASE
        の両方が含まれ、いずれも OPUS_DISCIPLINE_HEADING より前に位置すること
        (補正 context の構成契約: prefix + 空行 + Opus 見出し + 空行 +
        discipline-opus.md 本文)。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-checkpoint-4-opus-correction-order"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["marker"].write_text("sonnet-gate", encoding="utf-8")
            paths["state"].write_text("claude-opus-5", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(
                result.stdout.strip(),
                "opus 確定時は one-shot 補正の配送が期待されるが、出力が空",
            )
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn(OPUS_CORRECTION_PRIORITIZE_PHRASE, context)
            self.assertIn(OPUS_CORRECTION_DISCARD_SONNET_PHRASE, context)
            self.assertIn(OPUS_DISCIPLINE_HEADING, context)

            heading_index = context.index(OPUS_DISCIPLINE_HEADING)
            self.assertLess(
                context.index(OPUS_CORRECTION_PRIORITIZE_PHRASE), heading_index
            )
            self.assertLess(
                context.index(OPUS_CORRECTION_DISCARD_SONNET_PHRASE), heading_index
            )

    def test_7a_opus_direct_delivery_size_under_budget(self) -> None:
        """Opus 直接配送のサイズ: state=claude-opus-5 (marker/pending 無し) の
        additionalContext が Opus 版であることを確認したうえで、UTF-16 code unit 数が
        8,000 未満であることを assert する。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-checkpoint-7a-opus-direct-size"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["state"].write_text("claude-opus-5", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn(OPUS_DISCIPLINE_HEADING, context)
            self.assertLess(utf16_units(context), 8000)

    def test_7b_opus_correction_size_under_budget(self) -> None:
        """Opus 補正のサイズ: marker=sonnet-gate + state=claude-opus-5 (pending 無し)
        の additionalContext が Opus 補正であることを確認したうえで、UTF-16 code unit
        数が 8,000 未満であることを assert する。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-checkpoint-7b-opus-correction-size"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["marker"].write_text("sonnet-gate", encoding="utf-8")
            paths["state"].write_text("claude-opus-5", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(
                result.stdout.strip(),
                "opus 確定時は one-shot 補正の配送が期待されるが、出力が空",
            )
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn(OPUS_CORRECTION_PREFIX, context)
            self.assertIn(OPUS_DISCIPLINE_HEADING, context)
            self.assertLess(utf16_units(context), 8000)

    def test_7c_pending_self_gate_sonnet_size_under_budget(self) -> None:
        """pending 時のサイズ: marker 無し + pending ファイル存在時に配送される
        self-gate 前置き + Sonnet 版の additionalContext が UTF-16 code unit 数
        8,000 未満であること。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "opus-pr2-checkpoint-7c-pending-size"
            paths = self.make_state_paths(tmp_dir, session_id)
            paths["pending"].write_text("pending", encoding="utf-8")

            result = self.run_inject_discipline(session_id, tmp_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertLess(utf16_units(context), 8000)


if __name__ == "__main__":
    unittest.main()

# issue #272 Phase A: 「TDD 2 段階」→「spec-first 2 段階」rename の設計契約

本ファイルは issue #272 の Phase A (設計記述 commit) である。対象成果物はテストハーネスを持たない markdown (注入 prompt / skill 文書) のため、`rule:tdd-two-phase` の境界節に従い Phase A をこの設計契約で構成する。Phase B (実装本体) の完了時に本ファイルは削除する (v0.12.0 の `docs/temporary-rules-phase-a.md` と同じ運用)。

## 軽微でない判定の理由

本変更は README 類の docs-only 変更ではない。対象の常時注入 prompt (`hooks/prompts/always-*.md`) と skill 文書 (`skills/*/SKILL.md`) は SessionStart / Skill 呼び出しで LLM セッションに配送される runtime behavior であり、issue-start skill の軽微判定第 2 段 (b) 複数ファイル変更・(c) 仕様 (規律の意味論) の変更に該当するため、spec-first 2 段階を適用する。

## rename 対応表 (live reference 全件)

「TDD 2 段階」の出現箇所のうち、現行仕様を説明する live reference のみを rename する。README の変更履歴節 (`### vX.Y.Z → vX.Y.Z の変更点` 配下、v0.5.0〜v0.13.0 の各節) 内の出現は過去記録のため無変更とする。

| ファイル | 箇所 | 変更 |
|---|---|---|
| `plugins/agent-discipline/hooks/prompts/always-fable.md` | rule 9 見出し・本文 | 「TDD 2 段階の開発手順 (R3c)」→「spec-first 2 段階の開発手順 (R3c)」+ 最小言い換え (下記「always prompt の増分方針」) |
| `plugins/agent-discipline/hooks/prompts/always-sonnet-2.md` | part 冒頭説明文 (part 3/3 の内容列挙) | 「排他制御・AskUserQuestion 必須化・TDD 2 段階」→「…・spec-first 2 段階」 |
| `plugins/agent-discipline/hooks/prompts/always-sonnet-3.md` | rule 9 見出し・本文 | always-fable.md と同方針の rename + 最小言い換え |
| `plugins/agent-discipline/skills/issue-start/SKILL.md` | frontmatter description、導入文、第 3 章 (2 箇所)、第 4 章見出し・本文 | 全出現を「spec-first 2 段階」へ。加えて第 4 章配下に新規 5 小節 (下記) |
| `plugins/agent-discipline/skills/issue-plan/SKILL.md` | 第 1 章の参照行 | 「実装時の Phase A (`rule:tdd-two-phase`) に委ねます」→「実装時の spec-first 2 段階の Phase A (`rule:tdd-two-phase`) に委ねます」 |
| `plugins/agent-discipline/README.md` | L200 配送表 before 系・L264 注入内容要約 9 番・L529 /issue-start 説明 | rename (rule ID・v0.5.0 新設の来歴表記は維持) |

rule ID マーカー `<!-- rule:tdd-two-phase -->` は全ファイルで無変更とする (lint-prompt-sync.sh チェック 1/5 の母集合を変えない)。

## issue-start SKILL.md の新章構成契約

既存第 4 章「TDD 2 段階手順」を「spec-first 2 段階手順」へ rename し、既存の手順 6 ステップと「例外」(設計記述 commit) は無変更で維持する。同章の配下に、issue #272 受入基準と 1:1 対応する 5 小節を追加する:

1. **4.1 局所定義 (spec-first の位置づけ)**: 本ワークフローは正典 TDD (1 テストずつの red-green-refactor) ではなく、実行可能仕様の先行固定 (spec-first、ATDD / Specification by Example の系譜) である。手続きは用語に依存しない操作的記述 (Phase A で何を作り、何をレビューし、Phase B で何が許されるか) で書く。AI agent 特有の正当化 2 点 (テストのユーザ承認済み契約としての先行固定による「テスト側書き換え」failure mode の構造的抑止、pre-push-review の diff hash による Phase B でのテスト変更の自動再審査) を含める
2. **4.2 Phase A テストの provisional 契約**: Phase A のテストは「承認済みだが、実装接触後に不自然さ・実装不可能性が判明したら Phase A へ戻して改訂し再レビューできる契約」である。Phase B の途中でテストを都合よく弱めるのではなく、実装を止めて Phase A の改訂 → 再レビュー (push により diff hash が失効し再審査になる) を経てから再開する、と明記する
3. **4.3 Phase A の評価基準**: (a) 受入基準・境界から導く test matrix、(b) どの seam (public boundary) から挙動を観測するかとその選定理由、(c) 実装詳細 (private 関数・呼び出し回数等) への非結合、の 3 点で Phase A の質を評価する
4. **4.4 成果物粒度**: Phase A で固定するもの = テストコード・シグネチャ・インタフェース・データ設計 (公開契約に必要なもの)。Phase B に委ねるもの = private helper・内部アルゴリズム・局所的なファイル内分割
5. **4.5 Phase B 内の進め方**: 承認済みテスト集合を 1 つずつ通し、途中の学習で契約の欠陥が見えたら Phase A ループ (4.2) へ戻る

## always prompt の増分方針

inline 閾値 8K の予算温存のため、always 側は rename + 最小限の言い換えのみとする。具体的には「正典 TDD (1 テストずつの red-green) を要求する規律ではなく、実行可能仕様の先行固定 (spec-first) である」趣旨の 1 文以内の増分に留め、provisional 契約・評価基準・成果物粒度の詳細は issue-start skill 側にのみ置く。変更後の各注入ファイルは 8,000 字以下を維持する (現状: always-fable.md 5,611 字 / always-sonnet-2.md 4,798 字 / always-sonnet-3.md 7,372 字)。

## version bump 計画

共有 path (prompts / skills は両 runtime に配送される) のため両 runtime を bump する。後方互換のある規律の再定義 + skill 拡充であり、rule ID・軽微判定・push / draft PR / ready 化の流れは不変のため minor とする:

- Claude Code: 0.17.1 → 0.18.0 (`plugins/agent-discipline/.claude-plugin/plugin.json` と `.claude-plugin/marketplace.json` を一致させ、リポジトリ直下 README.md の plugin 一覧テーブルと `plugins/agent-discipline/README.md` の `## バージョン` 直下を同期)
- Codex: 0.17.2 → 0.18.0 (`codex/marketplace-overrides.json` の `plugins.agent-discipline.version` を更新し、リポジトリ直下 README.md の Codex 列を同期)
- plugin README の変更履歴見出しは版数差を正確に示すため「Claude Code v0.17.1 / Codex v0.17.2 → v0.18.0 の変更点」とする

## Codex 同期手順

正本変更後に `python3 scripts/sync_codex_marketplace.py --write` を実行する。plugin behavior tree の変更により `sourceTreeDigest` (非可搬 component に触れる場合は `sourceDigest` も) が stale になるため、Codex adapter・保証差・検証テストへの影響を再監査したうえで `--refresh-source-digests --plugin agent-discipline` を no-write preview → 表示された全 old/new を確認 → 同一コマンドに `--approve <action-token>` を付与して適用 → `--write` → `--check` 成功を確認する。

## 検証手順 (Phase B 完了条件)

1. `plugins/agent-discipline/scripts/lint-prompt-sync.sh` が exit 0
2. 変更後の各注入ファイル (`always-fable.md` / `always-sonnet-{1,2,3}.md`) が 8,000 字以下
3. 残存語検索: `grep -rn "TDD 2 段階"` の残存が README 変更履歴節内のみであること (live reference ゼロ)
4. `tests/test_version_policy.py` を含む既存テストが成功
5. `python3 scripts/sync_codex_marketplace.py --check` が exit 0
6. fresh context の verifier subagent による issue #272 受入基準の全件照合

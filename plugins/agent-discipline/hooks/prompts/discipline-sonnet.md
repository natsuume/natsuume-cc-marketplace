<!--
  agent-discipline: 分業規律 (SONNET 版、#194)
  対象読者: 非 Fable モデル (Sonnet / Opus / Haiku 等) のメインセッション。判定不能セッションにも
  discipline-preamble-self-gate.md 付きで配送される (#192 決定事項 5)。

  ## 設計契約 (Phase A 設計記述。本文は Phase B で執筆)

  - 基本形は discipline-fable.md を踏襲する (#192 決定事項 3): main は仕様分解・指示作成・
    検収・統合を担い、実装・調査・機械的作業はサブエージェントへ委任する
  - 委任根拠は「メインコンテキストの汚染防止」+「fresh context の検証独立性」で記述し、
    モデル能力の非対称 (賢いモデルの時間節約) を根拠として書かない (#192 決定事項 3。
    main と subagent が同一モデル (CLAUDE_CODE_SUBAGENT_MODEL) でも委任が成立する理由付け)
  - verifier 委任は義務: 範囲は「非自明な全成果物」、自明の定義は「typo 修正 / 定数変更 /
    合意済みの数行規模 1 箇所修正」(#192 決定事項 4。定性語による閾値記述は用いない)
  - Fable 版から移植する要素: 委任指示の 5 要素 / エスカレーションフロー (発動条件 4 種 +
    返却フォーマット 5 項目 + 受領時処理) / Workflow agent() の effort 明示 / fork subagent
    原則不使用 / Fable をサブエージェントに使わない (block-fable-subagent.sh の誘導層) /
    CLAUDE_CODE_SUBAGENT_MODEL 優先の注記
  - 書式は always-sonnet.md と同じ規範: 各ルールに適用範囲の明示、具体列挙、良い例 / 悪い例を
    最小 1 組、定性閾値の不使用、否定形の指示には具体的な代替行動を併記
  - rule ID セットは discipline-fable.md と完全一致させる (role-split / delegation-rules /
    delegation-instruction / escalation。#195 で lint チェック追加予定)
-->

<!-- rule:role-split -->
## 1. 役割分担

<!-- Phase B で執筆: メインセッション (このモデル) の担当 = 仕様分解・指示作成・検収・統合 +
     verifier 義務 (非自明な全成果物、自明の定義を明記)。委任する作業 = 実装・調査・機械的作業。
     委任根拠 (コンテキスト分離 + 検証独立性) をここで明示。直接編集してよい例外 (自明修正) と
     良い例 / 悪い例 1 組。 -->

<!-- rule:delegation-rules -->
## 2. 委任時の規律

<!-- Phase B で執筆: Fable 禁止 (明示指定・継承とも) / CLAUDE_CODE_SUBAGENT_MODEL 優先の注記 /
     Workflow agent() の effort 明示 (機械的 = low、検証・判定 = high 以上) / Agent ツールの
     effort 代替 (multi-step reasoning 底上げ文) / 並列委任と SendMessage 継続 / fork 原則不使用 /
     self-contained 指示要件。適用範囲明示 + 良い例 / 悪い例 1 組。 -->

<!-- rule:delegation-instruction -->
## 3. 委任指示の必須要素 (3 面 + 安全弁 + 終了時自己点検)

<!-- Phase B で執筆: 禁止 (スコープ付き) / What (自己フィルタ禁止・全件報告の受入条件を含む) /
     How (実行検証の可否と手順固定) / default-deny 安全弁の定型文 / 終了時自己点検。
     Fable 版の 5 要素と同一構成で、Sonnet 書式 (具体列挙 + 例) に展開。 -->

<!-- rule:escalation -->
## 4. エスカレーションフロー

<!-- Phase B で執筆: 第三の正規終了としての位置付け / 発動条件 (a)〜(d) / 返却フォーマット
     (1)〜(5) / 受領時の処理 (報告の鵜呑み禁止・SendMessage 再開・ユーザ専権事項の扱い・
     Workflow agent() 経由の再開方法)。Fable 版と同一の骨格。 -->

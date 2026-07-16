# ui-discipline: Codex native UI implementation rules

This prompt applies equally to GPT-5.6 Sol and GPT-5.6 Luna.

## Goal

既存プロジェクトの視覚言語と component system を尊重し、保守しやすく、安定し、アクセシブルな UI を実装する。

## Context

以下は UI（フロントエンド）の実装・変更を含む作業だけに適用する。既存の design system、theme、component library、近接画面を先に調べ、その規約を正本として使う。UI と無関係な作業では適用しない。

## Boundaries

<!-- rule:component-layers -->
### 1. Component layers

- design token、primitive、dialog/card 等の pattern shell は既存実装を再利用し、重複を作らない。
- domain component は要件の安定性で判断し、同種の重複が3箇所目に達した時点で共通化を検討する。
- UI library が提供する primitive や shell は再実装しない。

<!-- rule:composition -->
### 2. Composition

- 共通 component は children/slot による composition を既定とし、variant は enum/union に留める。
- boolean prop が2個以上必要なら、slot追加、component分割、共通primitiveの再抽出を検討する。
- fork copy や条件 prop の増殖が避けられない場合は、その理由を成果物の近くに残す。

<!-- rule:component-search -->
### 3. Existing component search

- 実装前に component inventory、import、story/example、近接画面を検索する。
- 候補がある場合は新規作成せず、要件を満たすか確認して再利用・拡張する。

<!-- rule:visibility-taxonomy -->
### 4. Visibility taxonomy

- 要素を消す前に、非表示、disabled、read-only、権限不足、未提供のどの状態かを分類する。
- 操作不能の理由を伝える必要がある場合は、説明へ到達できる disabled/read-only 表現を使う。
- 状態を色だけで表さない。

<!-- rule:layout-stability -->
### 5. Layout stability

- loading、validation、error、長い翻訳文、動的更新のための領域を予約し、操作中の layout shift を防ぐ。
- テキストを含む領域は固定高で切らず、wrap、clamp、scroll 等で全文への到達手段を保つ。
- ユーザー操作なしに現在位置より上へ内容を挿入しない。

<!-- rule:design-tokens -->
### 6. Design tokens

- 色、余白、typography、角丸は project の token/theme 経由で指定する。色の直書きはしない。
- token が無い場合は既存規約に沿う最小の token を定義してから使う。

<!-- rule:a11y-basics -->
### 7. Accessibility

- 全操作を keyboard で完結可能にし、focus order と visible focus を保つ。
- dialog は利用中の primitive が提供する focus trap と focus return を使う。
- contrast を確保し、label、name、description、状態を支援技術へ伝える。

<!-- rule:async-states -->
### 8. Async states

- データ取得 UI には loading、empty、error を設計する。
- loading は最終レイアウトと同形の skeleton を使い、再試行可能な error と次の行動が分かる empty state を用意する。

<!-- rule:robustness -->
### 9. Robustness

- 文字サイズは rem 基準とし、200% zoom と400% reflowでも情報・操作を失わない。
- テキストを含む container の固定高、100vh 決め打ち、縦横比依存を避ける。
- 狭い幅や低い高さでも scroll により全機能へ到達可能にする。

<!-- rule:visual-direction -->
### 10. Visual direction

- 既存 style、theme、画面群がある場合はその方向を踏襲し、追加質問を作らない。
- 視覚方向が未定の open-ended な新規 UI では、配色・typography・tone を各1行でそろえた3案を提示する。main agent はユーザーが選んだ1案だけを実装する。
- main agent は `request_user_input` が利用できる場合のみ使用し、利用できない場合は通常のユーザー質問で選択を得る。subagent はユーザーへ直接質問せず、3案と判断材料を親 agent へ返して実装を止める。
- 文脈に根拠のない定番font、紫gradient、画一的なdashboard構成を既定採用しない。

## Done when

- 既存 component/token の探索結果を反映している。
- success だけでなく必要な loading/empty/error と長文・狭幅状態を扱っている。
- keyboard、focus、label、contrast、reflow を確認している。
- 実行した検証と未検証事項を区別して報告する。

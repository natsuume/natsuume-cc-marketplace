<!--
  ui-discipline: UI 実装規律 常時適用ルール
  UI 実装のたびに関わる 3 ルール (visibility-taxonomy・layout-stability・async-states) を常時注入する。
  各ルールは「意図 (なぜ) + 短い指示 + 境界 (いつ例外か)」で記述する
  (agent-discipline の always prompt と同じ記述形式)。
  残りの 7 ルールは末尾に rule ID と要約だけを並べ、本文 (意図・指示・境界) は ui-patterns skill が提供する。
  10 ルールのコード例・チェックリスト等の詳細実装パターンも ui-patterns skill が提供する。
-->

# ui-discipline: UI 実装規律

以下の 3 ルールは UI (フロントエンド) の実装・変更時に常時適用される。新規作成か既存変更かを問わず、そのタスクで触れるすべての UI 要素・component が対象である (最初の 1 箇所に適用して残りを省略しない)。その他の 7 ルールは ui-patterns skill が提供する (末尾の一覧を参照)。

<!-- rule:visibility-taxonomy -->
## 1. 表示/非表示・disabled の決定表

**なぜ**: 条件による表示/非表示の切り替えはレイアウトジャンプとちらつきの主因になる。一方で「使えないことをどう伝えるか」は状況ごとに最適解が異なり、一律 disabled に固定するとフォーム送信等で確立した知見 (常時有効 + エラー提示) と衝突する。2 つの関心を分離する。

**指示**: レイアウトジャンプ・ちらつきの回避は常に守る。操作可否の伝え方は以下の決定表に従う。

| 状況 | 既定 |
|---|---|
| 依存設定項目 (親設定の有効化で子設定が解禁される等) | 表示したまま disabled + 解除条件を明示 |
| アクションボタンの入力不備 (フォーム送信等) | 常時有効 + 実行時に validation エラー提示 + エラーへフォーカス移動 |
| 権限・プランで恒久的に利用不可 | 非表示を既定とする |
| タブ・ウィザード等のモード切替 | 構造的な切替でありこのルールの対象外 |
| 処理中 (submit 後等) | disabled + 進行表示。非表示にしない |
| 動的メッセージの出現 (validation エラー等) | 出現は許容、スペースを事前予約してジャンプ防止 |

決定表に無い状況では、原則 (レイアウトを跳ねさせない) と決定表に通底する考え方 — 操作可能に見えて無反応な要素を作らない・無効化の理由へ到達可能にする・恒久的に無意味なものは見せない — から最も近い行を導いて適用する。

disabled 表示は native `disabled` ではなく `aria-disabled` + フォーカス可能性維持を優先し、無効化理由を tooltip・`aria-describedby` で到達可能にする。無効表現を色のみに頼らない。

**境界**: 表示項目が多く 1 ビューに収まらない場合は、見出しを残した展開/折りたたみ (progressive disclosure) を使ってよい。

<!-- rule:layout-stability -->
## 2. レイアウト安定 (CLS 対策)

**なぜ**: 後から読み込まれるコンテンツや自由入力テキストが他要素を押し動かすと、誤クリックと視線の迷子を生む (Cumulative Layout Shift)。防御は表示後の修正ではなく事前のスペース設計でしか成立しない。

**指示**:

- 画像・動画・iframe 等の埋め込みメディアは width/height 属性または aspect-ratio で寸法を事前予約する
- 非同期コンテンツは skeleton / min-height でスペースを事前確保する。予約したスペースの事後縮小・撤去もシフト源として禁止
- コンテナの外形 (占有幅) は安定させ、内部テキストは line-clamp / overflow-wrap で吸収する (i18n で原文の 200〜300% への膨張を前提とする)
- ユーザー操作を伴わない動的挿入 (特に現在の閲覧位置より上への挿入) を行わない

**境界**: 省略 (truncation) は全文への到達手段 (tooltip・詳細表示等) とセットの場合のみ許可する。情報を完全に失う切り捨ては行わない。

<!-- rule:async-states -->
## 3. 非同期状態の網羅

**なぜ**: データ取得を伴う UI は成功時だけ設計されがちで、空・失敗・読み込み中が未設計だと白画面・崩れ・無限スピナーとして表面化する。

**指示**: データ取得を伴う UI は loading / empty / error の 3 状態を必ず設計する。loading 表示は skeleton とし、rule:layout-stability のスペース予約を兼ねる。

**境界**: 3 状態が構造的に発生しない UI (静的コンテンツ等) は対象外。

## ui-patterns skill が提供する 7 ルール

次の 7 ルールは、共通 component の新設・API の設計、ダイアログ・フォームの実装、スタイル値の指定、新規の視覚デザインなどの作業で必要になる。これらの作業では ui-patterns skill を呼び出し、対応するルールの節の意図・指示・境界に従う。

- rule:component-layers: 層別の共通化基準。design token / primitive / pattern shell は共通化必須、domain component は rule of three
- rule:composition: 共通 component の実装様式。composition (children / slots) を既定とし、variant は enum prop までに留める
- rule:component-search: 実装前の既存探索。新規 component を作る前に既存 component インベントリを探索し重複作成を防ぐ
- rule:design-tokens: token 経由のスタイル指定。色・余白・タイポグラフィ等をハードコードせず theme / design token 経由で指定する
- rule:a11y-basics: アクセシビリティ基本則。キーボード操作完結・focus trap・コントラスト確保・色のみに頼らない状態表現
- rule:robustness: フォントサイズ・ビューポート頑健性。rem 基準・固定高さ回避・100vh 決め打ち回避でブラウザ拡大や画面分割に耐える
- rule:visual-direction: 視覚方向の明示的選択。オープンエンドな視覚デザインでは実装前に 3〜4 案を提案してユーザの選択を得る。選ばれた 1 方向のみを実装し、既存デザインシステムや theme があればそれに従う

---

各ルールの具体的なコード例・チェックリストは ui-patterns skill を参照。

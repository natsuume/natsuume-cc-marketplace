---
name: ui-patterns
description: UI 実装規律 (ui-discipline) のうち 7 ルールの意図・指示・境界と、各ルールの具体的なコード例・チェックリスト・実装パターンを提供する。共通 component の新設と API の設計、ダイアログ・フォームの実装、スタイル値の指定、新規の視覚デザインのほか、UI component・画面・page・一覧の追加・修正・整備で使う
---

# ui-patterns

ui-discipline の UI 実装規律のうち、visibility-taxonomy・layout-stability・async-states の 3 ルールの本文 (意図・指示・境界) は常時注入される `ui-rules.md` にあり、この skill はその 3 ルールのコード例・チェックリストを提供する。残りの 7 ルールは、本文 (意図・指示・境界) とコード例・チェックリストの両方をこの skill が提供する。コード例は React/TSX + CSS で示すが、構造は他のフレームワークでも同様に適用する。

## 1. rule:component-layers — 層別の共通化基準

**なぜ**: 共通化の適否は「見た目が似ているか」ではなく「その部分の要件が今後も安定しているか」で決まる。要件が分岐しうる部分を共通化すると、分岐のたびに条件分岐と prop が増殖する「誤った抽象化」になる。逆に安定した層の重複は、片方だけ修正される silent drift の源になる。

**指示**: UI を次の 4 層で捉え、層ごとの既定に従う。

- design token (色・余白・字形等)、primitive (Button, Input, Tooltip 等)、pattern shell (ダイアログの枠・overlay・footer 構成、Card の枠等): **共通化必須**。重複作成を禁止する
- domain component (業務固有の component): **rule of three** — 重複は 2 箇所まで許容し、3 箇所目の出現で共通化を検討する

**境界**: 採用 UI ライブラリ (shadcn/ui, MUI 等) が primitive / pattern shell を提供している場合はそれを共通実装とみなし、再実装しない。

### 層の判定手順

新規 component を作る前に、作ろうとしているものがどの層かを判定する。

1. スタイル値 (色・余白・字形・角丸) そのもの → **token 層**
2. 単一の UI 要素で、名前に業務語彙を含まない (Button, Input, Badge, Tooltip) → **primitive 層**
3. 複数要素の配置・枠組みで、名前に業務語彙を含まない (Dialog の枠, Card の枠, FormRow, PageHeader) → **pattern shell 層**
4. 名前に業務語彙を含む (UserCard, OrderForm, InvoiceTable) → **domain 層**

判定チェック: 命名に業務語彙が無いのに共通置き場以外へ置こうとしている場合、または業務語彙があるのに共通置き場へ置こうとしている場合は、層の誤判定を疑う。

## 2. rule:composition — 共通 component の実装様式

**なぜ**: 設定 prop の集合で分岐する共通 component は、N 個の boolean で 2^N 通りの挙動を持ち予測不能になる (boolean trap)。中身を呼び出し側が組み立てる composition なら、新要件は slot の中身として吸収され共通部分が汚れない。

**指示**: 共通 component は composition (children / slots) を既定とする。variant は enum (union type) prop までに留める。既存共通 component が要件に合わない場合は、slot 追加 → component 分割 (下位 primitive の共有は維持) → 一旦 inline 化して真の共通部分を再抽出、の順で対処する。

**境界**: boolean prop が 2 個以上になったら component 分割のシグナルとみなす。fork コピーと boolean prop 追加は最後の手段とし、採らざるを得ない理由をコード近傍か PR 説明に記述する。

### composition パターン

設定 prop 型 (避ける):

```tsx
<CommonDialog
  type="confirm"
  title="削除しますか?"
  showFooter
  hideCloseButton
  confirmLabel="削除"
  onConfirm={handleDelete}
/>
```

composition 型 (既定):

```tsx
<Dialog open={open} onClose={handleClose}>
  <DialogTitle>削除しますか?</DialogTitle>
  <DialogContent>この操作は取り消せません。</DialogContent>
  <DialogFooter>
    <Button variant="ghost" onClick={handleClose}>キャンセル</Button>
    <Button variant="danger" onClick={handleDelete}>削除</Button>
  </DialogFooter>
</Dialog>
```

- variant の例: `variant: 'primary' | 'ghost' | 'danger'` は可。`isPrimary?: boolean; isGhost?: boolean` の並列は不可
- 分割の例: `<UserCard compact selectable>` → 表示密度は `<UserCardCompact>` (または variant enum)、選択機構は呼び出し側の composition へ

## 3. rule:component-search — 実装前の既存探索

**なぜ**: UI 実装で最も頻発する失敗は、既存 component を探さずその場で新規作成して重複を生むこと。重複は見た目の不整合と silent drift (片方だけ修正され気づかれない) を招く。

**指示**: UI 実装前に既存 component インベントリ (components ディレクトリ・採用 UI ライブラリ・類似画面の実装) を探索し、同じ役割のものがあれば再利用・拡張する。新設した共通 component はプロジェクト規約の置き場所に配置する。

**境界**: 探索して見つからなければ新規作成してよい。探索を省略してよいのは、同一セッションの直前の作業で同じインベントリを確認済みの場合のみ。

### 探索チェックリスト

- [ ] 共通 component 置き場 (components/ 等、プロジェクト規約の場所) の一覧を確認した
- [ ] 採用 UI ライブラリに該当 component が無いか確認した
- [ ] 類似画面 (同種の一覧・フォーム・ダイアログ) の実装を 1 つ以上読んだ
- [ ] 見つかった場合: そのまま使えるか → slot / enum variant の追加で足りるか、の順に検討した

## 4. rule:visibility-taxonomy — 決定表の実装例

依存設定項目 (表示したまま disabled + 理由の到達可能性):

```tsx
<label>
  <input
    type="checkbox"
    checked={verboseLog}
    aria-disabled={!parentEnabled}
    aria-describedby="verbose-log-hint"
    onChange={(e) => {
      if (!parentEnabled) return; // aria-disabled は操作を止めないため、無効のときは状態を変えない
      setVerboseLog(e.target.checked);
    }}
  />
  詳細ログを出力する
</label>
<p id="verbose-log-hint">
  {parentEnabled
    ? "障害の調査用に、詳細なログを出力します"
    : "「ログ出力」を有効にすると設定できます"}
</p>
```

native `disabled` はフォーカス不可・タブ順除外になり、キーボード / スクリーンリーダー利用者には要素の存在も無効化理由も伝わらない。`aria-disabled` はフォーカス可能なまま「操作不可」を伝える。

`aria-disabled` はクリックやキー操作を止めないため、checkbox は `checked` で状態を制御し、無効のときは `onChange` で状態を変えない (`checked` を持たない checkbox では、ブラウザが表示を切り替えてしまう)。ヒントの要素は常に描画して文言だけを切り替えるので、`aria-describedby` の参照先が常に存在し、レイアウトも動かない。

アクションボタンの入力不備 (常時有効 + エラー提示):

```tsx
<Button type="submit">送信</Button>
// submit ハンドラで validate() し、失敗時は最初のエラー項目へ focus() を移してエラーを表示する。
// 送信ボタンを disabled にしない (なぜ押せないかが伝わらない)
```

動的メッセージのスペース予約:

```css
.field-error {
  min-height: 1.5rem; /* エラーが出現してもフォーム全体が跳ねない */
}
```

## 5. rule:layout-stability — 寸法予約と吸収

```html
<img src="/hero.png" width="800" height="450" alt="..." />
```

```css
.thumbnail {
  aspect-ratio: 16 / 9; /* 読み込み前から領域を確保 */
}

.card-title {
  display: -webkit-box;
  -webkit-line-clamp: 2; /* 自由入力テキストは行数で吸収 */
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: break-word; /* 連続文字列でもはみ出さない */
}
```

- skeleton は最終レイアウトと同じ寸法にする。「読み込み完了後に skeleton より小さくなる」も layout shift
- 省略した全文は tooltip や詳細表示で到達可能にする

## 6. rule:design-tokens — token 経由のスタイル指定

**なぜ**: 色・余白の直書きは画面ごとの微妙な不一致として蓄積し、後からの一括変更 (テーマ変更・ダークモード対応) を不可能にする。一貫した UI の土台は component 共通化ではなく token にある。

**指示**: 色・余白・タイポグラフィ・角丸等のスタイル値をハードコードせず、プロジェクトの theme / design token 経由で指定する。token が未整備のプロジェクトでは新設してから使う。

**境界**: token 化の粒度はプロジェクトの既存規約に従う。1 箇所でしか使わない微調整値まで token 化を強制しない (ただし色は例外なく token 経由とする)。

### token 経由の指定

```css
/* 避ける */
.card { color: #3b82f6; margin: 13px; }

/* 既定 */
.card { color: var(--color-primary); margin: var(--space-3); }
```

Tailwind 等のユーティリティ CSS では theme スケール上の class (`text-primary`, `m-3`) を使い、arbitrary value (`text-[#3b82f6]`, `m-[13px]`) を避ける。

## 7. rule:a11y-basics — アクセシビリティ基本則

**なぜ**: a11y は後付けの修正コストが高く、キーボード・スクリーンリーダー利用者にとっては機能欠落そのものになる。実装時に組み込むのが唯一低コストなタイミングである。

**指示**: 全操作をキーボードで完結可能にする。ダイアログは focus trap + 閉じた後のフォーカス返却を実装する。テキスト・UI コンポーネントのコントラストを WCAG 基準で確保する。状態表現を色のみに頼らない。

**境界**: 採用 UI ライブラリの primitive が focus trap 等を提供する場合はそれに委ね、自前で再実装しない。

### チェックリスト

- [ ] Tab / Shift+Tab で全操作対象に到達でき、Enter / Space で実行できる
- [ ] ダイアログ: 開いたら内部へフォーカス移動 / Tab は内部を循環 (focus trap) / 閉じたら開いた要素へフォーカス返却 / Esc で閉じる
- [ ] コントラスト: 本文テキスト 4.5:1 以上、大きいテキスト・UI 部品 3:1 以上
- [ ] 状態 (エラー・選択中・無効) に色以外の手掛かり (アイコン・テキスト・下線) を併用している

## 8. rule:async-states — 3 状態の分岐

```tsx
if (isLoading) return <ListSkeleton rows={5} />; // 最終レイアウトと同寸 (rule:layout-stability の予約を兼ねる)
if (error) return <ErrorState onRetry={refetch} />;
if (items.length === 0) return <EmptyState message="まだ項目がありません" />;
return <ItemList items={items} />;
```

Skeleton / ErrorState / EmptyState 自体も pattern shell として共通化する (rule:component-layers)。

## 9. rule:robustness — フォントサイズ・ビューポート頑健性

**なぜ**: 利用者はブラウザ拡大・OS 文字サイズ変更・ウィンドウ分割を日常的に使う。px 決め打ちと縦横比前提のレイアウトは、その環境で文字切れ・重なりとして崩壊する。

**指示**:

- 文字サイズは rem 基準とし、ブラウザ拡大・OS 文字サイズ設定に追従させる (WCAG 1.4.4 の 200% 拡大・1.4.10 の 400% reflow を満たす)
- テキストを含むコンテナに固定高さを与えない
- 100vh 決め打ち・縦横比前提のレイアウトを避け、縦に短いウィンドウでもスクロールで全機能に到達可能にする

**境界**: rule:layout-stability の外形安定は「同一ビューポート内で他要素の位置に影響を与えない」ことを指し、ビューポート幅変化への応答 (レスポンシブ、相対単位・flex/grid) を妨げない。

### 拡大・分割への耐性

```css
/* 避ける */
.title { font-size: 14px; }
.card { height: 120px; }   /* 文字拡大で内容が溢れる */
.page { height: 100vh; }   /* 縦に短いウィンドウで下端が切れる */

/* 既定 */
.title { font-size: 0.875rem; }
.card { min-height: 7.5rem; } /* または高さ指定なしで内容に追従 */
.page { min-height: 100dvh; } /* 必要ならコンテナ内スクロール */
```

確認方法: ブラウザ拡大 200% と、縦 600px 程度にリサイズしたウィンドウで、文字切れ・重なり・到達不能な操作が無いことを見る。

## 10. rule:visual-direction — 視覚方向の明示的選択

**なぜ**: 視覚方向の指定が無いオープンエンドな UI 実装では、モデルは固定的なデフォルト美学 (定番フォント・紫系グラデーション・cookie-cutter レイアウト、いわゆる「AI slop」) に収束しやすい。視覚スタイルはプロジェクト固有性が強く、無指定のまま実装すると後からの方向転換コストが大きい。

**指示**: 既存の視覚的手掛かり (デザインシステム・theme・既存画面群) があるプロジェクトではそれに従う。無い状態でオープンエンドな視覚デザインを求められた場合は、実装前に 3〜4 案の視覚方向 (配色・タイポグラフィ・トーンを各 1 行) を提案してユーザの選択を得てから、選ばれた 1 方向のみを実装する。プロジェクトの業種・利用文脈に合う視覚的性格を選ぶ。

避ける既定: 無検討の定番フォント (Inter / Roboto / system fonts)、紫系グラデーション、文脈と無関係な装飾、クリーム・生成り系の背景、見出し内のイタリックのアクセント語、「01 / 02 / 03」形式の番号付きセクションラベル、等幅フォントのラベル、pill 形のボタン。

**境界**: ユーザが視覚方向を明示済みの場合、または既存スタイルの踏襲で足りる変更 (既存画面への機能追加等) では提案を挟まず従う。

### 視覚方向の提案パターン

提案は次の形式で示す:

> この画面の視覚方向として以下を提案します。どれで実装しますか?
>
> 1. **冷たいモノクローム**: 背景 #E9ECEC 系 / アクセント #44545B / 角張ったサンセリフ — 統制された硬質な印象
> 2. **高コントラストの業務ツール**: 白背景 / 群青アクセント / 幅広のサンセリフ — 情報密度を優先した明快な印象
> 3. (以下、配色・書体・トーンを各 1 行で 3〜4 案)

実装する方向には、選ばれなかった案の要素を混ぜない。

## 見落としやすい項目の再確認

そのタスクで触れたルールに対応する項目だけを確認する (全項目の機械的な確認は不要)。

- [ ] 新規 component を作る前に既存を探索した (rule:component-search)
- [ ] 共通 component に boolean prop を追加していない (rule:composition)
- [ ] スタイル値の直書きがない (rule:design-tokens)
- [ ] 極端なコンテンツ (長い連続文字列・空・大量件数) で崩れない (rule:layout-stability)
- [ ] loading / empty / error を実装した (rule:async-states)
- [ ] キーボードのみで一巡できる (rule:a11y-basics)
- [ ] ブラウザ拡大 200% で操作できる (rule:robustness)
- [ ] 条件表示の増減でレイアウトが跳ねない (rule:visibility-taxonomy)
- [ ] オープンエンドな新規デザインでは視覚方向のユーザ選択を得た (rule:visual-direction)

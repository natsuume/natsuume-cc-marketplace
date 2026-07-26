---
name: ui-patterns
description: UI 実装規律 (ui-discipline の 10 ルール) に対応する具体的なコード例・チェックリスト・実装パターンを提供する。UI component、画面・page、dialog、form、一覧の追加・修正・整備で使う
---

# ui-patterns

ui-discipline が常時注入する 10 ルールに対応する、具体的なコード例・チェックリスト集です。ルール本体 (意図・指示・境界) は常時注入される `ui-rules.md` が配送するためここでは複製せず、適用方法のみを提供します。コード例は React/TSX + CSS で示しますが、構造は他のフレームワークでも同様に適用してください。

## 1. rule:component-layers — 層の判定手順

新規 component を作る前に、作ろうとしているものがどの層かを判定します。

1. スタイル値 (色・余白・字形・角丸) そのもの → **token 層**
2. 単一の UI 要素で、名前に業務語彙を含まない (Button, Input, Badge, Tooltip) → **primitive 層**
3. 複数要素の配置・枠組みで、名前に業務語彙を含まない (Dialog の枠, Card の枠, FormRow, PageHeader) → **pattern shell 層**
4. 名前に業務語彙を含む (UserCard, OrderForm, InvoiceTable) → **domain 層**

判定チェック: 命名に業務語彙が無いのに共通置き場以外へ置こうとしている場合、または業務語彙があるのに共通置き場へ置こうとしている場合は、層の誤判定を疑ってください。

## 2. rule:composition — composition パターン

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

- variant は enum まで: `variant: 'primary' | 'ghost' | 'danger'` は可。`isPrimary?: boolean; isGhost?: boolean` の並列は不可
- boolean prop が 2 個に達したら分割を検討: `<UserCard compact selectable>` → 表示密度は `<UserCardCompact>` (または variant enum)、選択機構は呼び出し側の composition へ

## 3. rule:component-search — 探索チェックリスト

UI 実装前に以下を確認します:

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
    aria-disabled={!parentEnabled}
    aria-describedby="verbose-log-hint"
    onChange={(e) => {
      if (!parentEnabled) return; // aria-disabled はクリック抑止しないため自前で行う
      onChange(e);
    }}
  />
  詳細ログを出力する
</label>
{!parentEnabled && (
  <p id="verbose-log-hint">「ログ出力」を有効にすると設定できます</p>
)}
```

native `disabled` はフォーカス不可・タブ順除外になり、キーボード / スクリーンリーダー利用者には要素の存在も無効化理由も伝わりません。`aria-disabled` はフォーカス可能なまま「操作不可」を伝えます。

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

## 6. rule:design-tokens — token 経由の指定

```css
/* 避ける */
.card { color: #3b82f6; margin: 13px; }

/* 既定 */
.card { color: var(--color-primary); margin: var(--space-3); }
```

Tailwind 等のユーティリティ CSS では theme スケール上の class (`text-primary`, `m-3`) を使い、arbitrary value (`text-[#3b82f6]`, `m-[13px]`) を避けます。

## 7. rule:a11y-basics — チェックリスト

- [ ] Tab / Shift+Tab で全操作対象に到達でき、Enter / Space で実行できる
- [ ] ダイアログ: 開いたら内部へフォーカス移動 / Tab は内部を循環 (focus trap) / 閉じたら開いた要素へフォーカス返却 / Esc で閉じる
- [ ] コントラスト: 本文テキスト 4.5:1 以上、大きいテキスト・UI 部品 3:1 以上
- [ ] 状態 (エラー・選択中・無効) に色以外の手掛かり (アイコン・テキスト・下線) を併用している

## 8. rule:async-states — 3 状態の分岐

```tsx
if (isLoading) return <ListSkeleton rows={5} />; // 最終レイアウトと同寸 (rule 5 の予約を兼ねる)
if (error) return <ErrorState onRetry={refetch} />;
if (items.length === 0) return <EmptyState message="まだ項目がありません" />;
return <ItemList items={items} />;
```

Skeleton / ErrorState / EmptyState 自体も pattern shell として共通化します (rule 1)。

## 9. rule:robustness — 拡大・分割への耐性

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

確認方法: ブラウザ拡大 200% と、縦 600px 程度にリサイズしたウィンドウで、文字切れ・重なり・到達不能な操作が無いことを見ます。

## 10. rule:visual-direction — 視覚方向の提案パターン

既存の視覚的手掛かりが無いオープンエンドな新規デザインでは、実装前に次の形式で選択肢を提示し、ユーザの選択を得ます:

> この画面の視覚方向として以下を提案します。どれで実装しますか?
>
> 1. **冷たいモノクローム**: 背景 #E9ECEC 系 / アクセント #44545B / 角張ったサンセリフ — 統制された硬質な印象
> 2. **ウォームエディトリアル**: 生成りの背景 / 深緑アクセント / セリフ見出し — 読み物らしい落ち着き
> 3. (以下、配色・書体・トーンを各 1 行で 3〜4 案)

選ばれた 1 方向だけを実装し、他案の要素を混ぜません。

避ける既定: 無検討の Inter / Roboto / system-ui、紫系グラデーション、文脈と無関係な装飾。プロジェクトの業種・利用文脈 (業務ツール / 消費者向け / 開発者向け) から視覚的性格を導きます。

## 見落としやすい項目の再確認

そのタスクで触れたルールに対応する項目だけを確認します (全項目の機械的な確認は不要)。

- [ ] 新規 component を作る前に既存を探索した (rule 3)
- [ ] 共通 component に boolean prop を追加していない (rule 2)
- [ ] スタイル値の直書きがない (rule 6)
- [ ] 極端なコンテンツ (長い連続文字列・空・大量件数) で崩れない (rule 5)
- [ ] loading / empty / error を実装した (rule 8)
- [ ] キーボードのみで一巡できる (rule 7)
- [ ] ブラウザ拡大 200% で操作できる (rule 9)
- [ ] 条件表示の増減でレイアウトが跳ねない (rule 4)
- [ ] オープンエンドな新規デザインでは視覚方向のユーザ選択を得た (rule 10)

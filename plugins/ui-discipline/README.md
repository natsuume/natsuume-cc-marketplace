# ui-discipline プラグイン

UI (フロントエンド) 実装時の規律を配送するプラグインです。UI を持つプロジェクトでのみ enable して使います。

## バージョン

v0.4.0

### v0.3.0 → v0.4.0 の変更点

Codex 配布対応 (marketplace 移植) を廃止した。`codex/` 配下の manifest・prompt・inject script を削除し、Claude Code 版の SessionStart / SubagentStart hook と ui-patterns Skill は無変更。

### v0.2.1 → v0.3.0 の変更点

- Codex plugin manifest を追加し、SessionStart/SubagentStart hook と ui-patterns Skill を Claude Code / Codex で共有できるようにした

## 概要

UI 実装は「共通化すべきか」「表示/非表示をどう決めるか」「レイアウトが崩れないか」といった判断が実装のたびに発生し、判断がぶれると重複 component の乱立や CLS (Cumulative Layout Shift)、a11y 欠落として表面化します。本プラグインはこれらの判断基準を 10 ルールとして常時配送し、判断のぶれを構造的に抑えます。

配送は次の構成です:

| 層 | 配送経路 | 内容 |
|---|---|---|
| メインセッション | `SessionStart` (`inject-ui-rules.sh`) | `hooks/prompts/ui-rules.md` の 10 ルールを `additionalContext` として注入 |
| subagent | `SubagentStart` (`inject-ui-rules-subagent.sh`) | 同一の `ui-rules.md` に `ui-rules-subagent-preamble.md` を連結して注入 (Claude Code 2.0.43+) |
| ui-patterns Skill | `skills/ui-patterns/SKILL.md` | 10 ルールそれぞれに対応する具体的なコード例・チェックリストを提供 |

常時注入層はルールの「意図・指示・境界」のみを圧縮して伝え、コード例やチェックリストの詳細実装パターンは ui-patterns skill 側が担当することで、常時消費されるトークン量を抑えています。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install ui-discipline@natsuume-plugins
```

## 配送する 10 ルール

`hooks/prompts/ui-rules.md` が常時注入する rule ID の一覧です (rule ID は `<!-- rule:<id> -->` コメントとしてファイル内に埋め込まれています)。

| rule ID | 説明 |
|---|---|
| `component-layers` | 層別の共通化基準。design token / primitive / pattern shell は共通化必須、domain component は rule of three |
| `composition` | 共通 component の実装様式。composition (children / slots) を既定とし、variant は enum prop までに留める |
| `component-search` | 実装前の既存探索。新規 component を作る前に既存 component インベントリを探索し重複作成を防ぐ |
| `visibility-taxonomy` | 表示/非表示・disabled の決定表。状況ごとに表示したまま disabled / 常時有効 + エラー提示 / 非表示、を使い分ける |
| `layout-stability` | レイアウト安定 (CLS 対策)。寸法の事前予約とテキストの吸収でレイアウトジャンプを防ぐ |
| `design-tokens` | token 経由のスタイル指定。色・余白・タイポグラフィ等をハードコードせず theme / design token 経由で指定する |
| `a11y-basics` | アクセシビリティ基本則。キーボード操作完結・focus trap・コントラスト確保・色のみに頼らない状態表現 |
| `async-states` | 非同期状態の網羅。データ取得を伴う UI は loading / empty / error の 3 状態を必ず設計する |
| `robustness` | フォントサイズ・ビューポート頑健性。rem 基準・固定高さ回避・100vh 決め打ち回避でブラウザ拡大や画面分割に耐える |
| `visual-direction` | 視覚方向の明示的選択。オープンエンドな視覚デザインでは実装前に 3〜4 案を提案してユーザの選択を得る。選ばれた 1 方向のみを実装し、既存デザインシステムや theme があればそれに従う |

## 機能一覧

### Hooks

| Hook 名 | イベント | 説明 |
|---|---|---|
| `inject-ui-rules` | SessionStart | `hooks/prompts/ui-rules.md` の全文を `additionalContext` として常時注入する。モデル判定・permission_mode 判定等の条件分岐は持たない |
| `inject-ui-rules-subagent` | SubagentStart | 同一の `ui-rules.md` に subagent 向け前置き注記 (`ui-rules-subagent-preamble.md`) を連結して全 subagent 起動時に注入する。注記中の ui-patterns SKILL.md への参照は注入時に絶対パスへ解決する。Claude Code 2.0.43 以降で有効 |

### Skills

| スキル名 | コマンド | 説明 |
|---|---|---|
| `ui-patterns` | `/ui-patterns` | 常時注入される 10 ルールに対応する具体的なコード例・チェックリスト集を提供する。UI component / 画面 / ダイアログ・フォーム・一覧の実装や修正時にトリガーされる |

## 設計上の選択

### なぜ agent-discipline に統合せず独立 plugin としたか

UI 実装規律は UI を持つプロジェクトでのみ意味を持ち、バックエンドや CLI 中心のプロジェクトでは不要です。agent-discipline のような「全プロジェクト共通で有用な規律」とは適用範囲の性質が異なるため統合せず、plugin の enable/disable 単位をそのまま適用範囲の単位とする独立 plugin としています。

### なぜモデル別 prompt 分岐を持たないか

Fable / Sonnet 共通の 1 prompt を使います。ui-discipline の 10 ルールはコンポーネント層別の共通化基準や表示/非表示の決定表など、モデルに依存しない UI 実装上の判断基準そのものです。

### なぜ SubagentStart でも注入するか

agent-discipline の分業規律では、明確化された仕様に基づく実装は subagent へ委任するのが既定です。つまり UI 実装の実作業者は多くの場合 subagent であり、SessionStart 注入だけでは規律が実装しないメインセッションにしか届きません。SubagentStart 注入により、実作業者に規律が構造的に届きます (委任指示への埋め込みというメインセッション側の遵守に依存しない配送)。

### なぜ subagent 専用テンプレートを複製しないか

ui-discipline の 10 ルールはモデル・実行主体に依存しない判断基準であり、subagent との差分は rule:visual-direction のエスカレーション化だけです。全文コピーによる silent drift を避けるため、単一ソース (`ui-rules.md`) + 前置き注記 (`ui-rules-subagent-preamble.md`) の連結方式を採っています。

前置き注記・本体・ui-patterns SKILL.md のいずれかが欠けた場合は全体を注入しません。読み替え規則を欠いたまま rule:visual-direction を subagent に配送すると、subagent には実行不能な「ユーザの選択を得る」指示が残るためです (部分注入の禁止)。

### なぜ inject script が fail-open か

`inject-ui-rules.sh` / `inject-ui-rules-subagent.sh` は、prompt ファイルが読めない場合に何も出力せず exit 0 で終了します。規律の注入は agent の自発的な遵守を促す誘導層であり、強制力を持つ deny 系の hook とは性質が異なります。誘導層の欠落によってセッション自体を壊す価値はないため、fail-open を採用しています。

## ディレクトリ構成

```
ui-discipline/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   ├── prompts/
│   │   ├── ui-rules.md
│   │   └── ui-rules-subagent-preamble.md
│   └── scripts/
│       ├── inject-ui-rules.sh
│       └── inject-ui-rules-subagent.sh
├── skills/
│   └── ui-patterns/
│       └── SKILL.md
└── README.md
```

## 必要な実行環境

- POSIX `sh`
- `jq`

## 関連プラグイン

- [agent-discipline](../agent-discipline/) — Claude Code の振る舞い規律を全プロジェクト共通で配送する system prompt plugin。ui-discipline は UI プロジェクト限定の規律のみを別 plugin として分離配送する

## 関連情報

- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
- [Claude Code Skills ドキュメント](https://code.claude.com/docs/en/skills)

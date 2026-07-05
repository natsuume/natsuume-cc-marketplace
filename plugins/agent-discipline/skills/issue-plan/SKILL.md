---
name: issue-plan
description: issue を起票する・issue に分解する・sub-issue を作る際の手順 (起票前の壁打ち・body template・分割基準・関係設定コマンド) を提供する
user-invocable: true
when_to_use: |
  ユーザーが以下のようなリクエストをした場合に使用:
  - 「issue を起票する」
  - 「issue に分解する」
  - 「sub-issue を作る」
---

# issue-plan

issue 駆動開発における **起票・分解フェーズ** の詳細手順です。常時注入 (SessionStart) が配送する原則 (`rule:design-approval` / `rule:issue-body` / `rule:issue-granularity` / `rule:closing-keyword`) を前提に、本 skill はそれらを実行するための具体的な手順・body template・コマンド例を提供します。常時注入ルールの手順本体はここでは複製せず、参照するに留めます。

## 1. 起票前の壁打ち

issue body を書き始める前に、未確定の設計・仕様論点をすべて `AskUserQuestion` で解消します。判断基準や自己検知トリガーの詳細は常時注入ルール `rule:design-approval` (設計 / 仕様判断の事前確認) と `rule:issue-body` (issue 起票時の詳細化) を参照してください。本 skill はこれらのルール本体を複製せず、issue-plan 固有の適用ポイントのみを補足します。

壁打ちのタイミングは起票前に限りません。issue を pick up した時点で過去 session の未確認選択や記載漏れを発見した場合も、実装に入る前に追加で `AskUserQuestion` を発行してください (`rule:issue-body` の遡及適用と同じ考え方です)。

## 2. body template

issue body は以下の 6 セクションで構成し、**すべて body に直接埋め込みます** (`.claude/issues/N.md` のような補助ファイルは作りません)。目標は「`gh issue view <N>` 1 発で、別 session の Claude が self-contained に実装着手できること」です。

| セクション | 記載する内容 |
|---|---|
| 背景 | なぜこの issue が必要か、前提となる調査結果や親 issue との関係 |
| 受入基準 | 完了と判定できる具体的な条件 (曖昧な形容詞を避ける) |
| I/O 契約 | 公開 API のシグネチャ・型・入出力形式など、実装が従うべきインタフェース |
| 制約 | 禁止事項・非機能要件・スコープ外事項 |
| 想定ファイル | 変更対象になりうるファイル / ディレクトリの一覧 |
| 関連 issue | 親 issue・sub-issue・関連 issue への `#N` 参照 (詳細はセクション 5) |

テンプレート例:

```markdown
# 概要

<1-2 文で要約>

## 背景

...

## 受入基準

- ...

## I/O 契約

...

## 制約

...

## 想定ファイル

- ...

## 関連 issue

親 issue: #N
```

## 3. 分割基準

1 issue は独立して並列作業できる粒度で起票します。具体的には、他の未完了 issue の成果物を待たずに着手・完了できる単位を指します。

1 PR で閉じられないほど大きい issue (= 複数の独立した実装単位を含む issue) は、sub-issue に分割します。分割後の各 sub-issue も同じ粒度基準 (独立して並列作業できること) を満たすようにしてください。分割基準を満たさないまま巨大な issue を起票すると、後続 session が着手時にさらに分解し直す手戻りが発生します。

## 4. 関係設定コマンド

gh v2.94+ ではネイティブの sub-issue / blocked-by コマンドが使えます (動作確認済みバージョン: 2.96.0)。まずこちらを試してください。

**第一経路 (gh v2.94+ ネイティブ)**:

```bash
# N を親として M を sub-issue 作成
gh issue create --parent <N> --title "..." --body "..."

# 既存の M を N の sub-issue に追加
gh issue edit <N> --add-sub-issue <M>

# M は K の完了を待つ (blocked-by)
gh issue edit <M> --add-blocked-by <K>
```

**旧版 fallback** (ネイティブコマンドが使えない gh バージョン向け):

```bash
# sub_issue_id は issue 番号ではなく issue の内部数値 ID (gh api の .id) である点に注意する。
# issue 番号 (M) をそのまま渡すと別の issue に誤って紐付く事故になる。
SUB_ID=$(gh api /repos/<owner>/<repo>/issues/<M> --jq .id)
gh api -X POST /repos/<owner>/<repo>/issues/<N>/sub_issues -f sub_issue_id="$SUB_ID"
```

## 5. `#N` 相互参照と issue types 不使用

sub-issue 親子リンクに加えて、issue body 内に `#N` 形式の相互参照を必ず併記します (例:「関連: #12, #13」)。親子リンクは GitHub UI 上の階層表示に使われますが、body 本文を読むだけでも関係性が分かるようにするため `#N` 参照は省略しません (`rule:issue-granularity` を参照)。

GitHub の issue types 機能は使いません。issue types は組織 (Organization) 配下の repository でのみ利用できるカタログ機能であり、個人アカウント配下の repository では利用できないためです。

## 6. 親 issue の close 規約

sub-issue が全て完了 (close) されても、親 issue は自動では close されません。GitHub の sub-issue 機能はあくまで進捗の可視化 (親 issue に完了率が表示される) のためのリンクであり、closing keyword のような自動 close 動作は持ちません。

親 issue を close するのは、親 issue が扱う内容を完全に解決する最終 PR のみです。その PR の body に親向けの closing keyword (`Closes #<親N>` 等、詳細は `rule:closing-keyword` を参照) を書くか、あるいは PR マージ後に手動で `gh issue close <親N>` を実行してください。sub-issue 個別の PR には親向け closing keyword を書かないでください (誤って親が早期 close される事故になります)。

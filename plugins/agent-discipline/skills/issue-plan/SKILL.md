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

<!--
  agent-discipline: issue-plan skill (#176 Phase A: 設計記述 commit)

  本ファイルは章構成・トリガー語彙・各章の内容契約のみを定義する骨格である。
  各章本文の具体的な手順・コマンド例・説明文は Phase B で追記する (このファイルはまだ書かない)。

  スコープの境界 (受入基準):
  - frontmatter の description / when_to_use は「起票・分解側」のトリガー語彙のみを含む
    (「issue を起票する」「issue に分解する」「sub-issue を作る」)。
    着手・実装開始側の語彙 (issue-start が担当する「着手する」「実装を始める」「pick up する」等) は
    一切含めない。2 skill 間で同じ動詞を共有しないこと。
  - 常時注入 (hooks/prompts/always-fable.md, always-sonnet.md) が既に持つルール本体
    (rule:design-approval, rule:issue-body, rule:issue-granularity, rule:closing-keyword) は
    本 skill 側で重複記載せず、「参照する」形の記述に留める。
-->

# issue-plan

<!--
  内容契約 (Phase B):
  この段落には skill 全体の要約 (1-2 文) を書く。「issue 駆動開発における起票・分解フェーズの詳細手順を
  progressive disclosure で配送する」主旨と、常時注入との役割分担 (常時注入 = 原則、本 skill = 手順詳細)
  を明記する。
-->

## 1. 起票前の壁打ち

<!--
  内容契約 (Phase B):
  - 未確定の設計・仕様論点を `AskUserQuestion` で解消してから body を書く、という手順を明記する
  - 常時注入の rule:design-approval (設計/仕様判断の事前確認) と rule:issue-body (issue 起票時の詳細化) を
    「参照」する形で示す。両ルールの手順本体 (自己検知トリガー一覧・禁止表現カテゴリ等) はここに複製しない
  - 壁打ちのタイミングが「起票前」だけでなく「pick up 時に不足が判明した場合」にも及ぶ点に触れる
    (rule:issue-body の遡及適用と整合させる)
-->

## 2. body template

<!--
  内容契約 (Phase B):
  - body に含めるセクション一覧を提示する: 背景 / 受入基準 / I/O 契約 / 制約 / 想定ファイル / 関連 issue
  - 各セクションが埋めるべき情報の性質を簡潔に説明する
    (例: I/O 契約 = 公開 API のシグネチャ・型・入出力形式、制約 = 禁止事項・非機能要件 等)
  - 起票内容は body へ全埋め込みし、補助ファイル (`.claude/issues/N.md` 等) を作らない規律を明記する
    (rule:issue-body 参照)
  - 目標として「`gh issue view <N>` 1 発で後続 session が self-contained に実装着手できること」を明記する
-->

## 3. 分割基準

<!--
  内容契約 (Phase B):
  - 1 issue = 独立して並列作業できる粒度で起票する基準を明記する
  - 1 PR で閉じられないほど大きい場合に sub-issue へ分割する、という分割トリガーを明記する
  - 分割後の各 sub-issue も同じ粒度基準 (独立並列作業可能) を満たす必要がある点を明記する
-->

## 4. 関係設定コマンド

<!--
  内容契約 (Phase B):
  - 第一経路 (gh v2.94+ ネイティブ sub-issue / blocked-by 対応) を提示する:
    `gh issue create --parent <N>` / `gh issue edit <N> --add-sub-issue <M>` /
    `gh issue edit <M> --add-blocked-by <K>`
  - 旧版 fallback を提示する:
    `SUB_ID=$(gh api /repos/<o>/<r>/issues/<M> --jq .id)` の後に
    `gh api -X POST /repos/<o>/<r>/issues/<N>/sub_issues -f sub_issue_id="$SUB_ID"`
  - fallback コマンド例には「`sub_issue_id` は issue 番号ではなく内部数値 ID である」旨の注記コメントを
    定型として必ず含める (このコメントが無いと sub_issue_id に issue 番号を渡す事故が起きるため)
  - 動作確認済み gh バージョン (2.96.0) と、fallback が必要になる旧版の目安を明記する
-->

## 5. `#N` 相互参照と issue types 不使用

<!--
  内容契約 (Phase B):
  - sub-issue 親子リンクに加え、body 内 `#N` 相互参照 (例: 「関連: #12, #13」) を併用する規律を明記する
    (rule:issue-granularity を参照し、同ルールの手順本体を複製しない)
  - issue types を使わない理由 (個人 repo では組織 issue type カタログが利用不可のため) を明記する
-->

## 6. 親 issue の close 規約

<!--
  内容契約 (Phase B):
  - sub-issue が全て完了しても親 issue は自動 close されない、という GitHub の挙動を明記する
  - 親 issue を完全に解決する最終 PR にのみ親向け closing keyword (`Closes #N` 等) を書くか、
    もしくは手動 close する、という規律を明記する (rule:closing-keyword を参照し手順本体を複製しない)
-->

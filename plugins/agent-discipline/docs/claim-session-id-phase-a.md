# rule:issue-claim claim comment へのセッション ID 追加 — Phase A 設計契約

本ファイルは TDD 2 段階 (markdown 例外: 設計記述 commit) の Phase A 成果物である。
Phase B (実装本体) で以下の契約をそのまま `rule:issue-claim` 本文に反映し、本ファイルは削除する。

## 解決する問題

現行の claim comment `🔒 ai:claim branch=<prefix>/issue-<N>-<slug> ts=<UTC ISO 8601>` は、
「自分の claim か」の判定を `branch=` 値と自分の作業 branch の一致で行う。しかし branch 名は
issue 番号 + タイトル slug から決定的に導出されるため、並列稼働する別セッションが同一 branch 名を
提案しうる。その場合 `branch=` でも `ts=` (自己申告、秒単位で衝突しうる) でも自他判別できず、
どちらのセッションが優先権を持つか判定不能になる (comment author も同一 GitHub アカウントのため
区別に使えない)。

## 契約 1: claim comment 形式

```
🔒 ai:claim branch=<prefix>/issue-<N>-<slug> session=<セッションID> ts=<UTC ISO 8601>
```

- `session=` キーを `branch=` と `ts=` の間に追加する
- `<セッションID>` は環境変数 `CLAUDE_CODE_SESSION_ID` の値 (Claude Code がセッション毎に付与する
  UUID) を用いる。未設定の場合のみ `uuidgen` で生成した値を代用し、同一セッション中は同じ値を
  使い続ける (ユーザ decision 2026-07-12: session ID が容易に取得できる場合は session ID、
  困難な場合のみ UUID 等で代替)
- `branch=` / `ts=` の意味は現行から変更しない (`branch=` は claim と branch の 1:1 対応の宣言、
  `ts=` は人間向けの参考情報)

## 契約 2: 自他判別 (「自分の claim か」の判定基準)

- 判定基準を「`branch=` 値が自分の作業 branch と一致するか」から「`session=` 値が自分の
  セッション ID と一致するか」に変更する
- 削除対象の comment-id 特定 (先着競合での撤退時・撤退クリーンアップ時) も `session=` 値で行う
  (数値 comment id は契約 3 の REST GET 結果の `id` フィールドから得る)
- `session=` キーが無い claim comment (旧形式) は自分のものと確認できないため、削除判定上は
  他セッションの claim として扱う (= 削除しない)。既存の「他 session の claim / branch / ラベルは
  絶対に削除しない」規律の判定基準を差し替えるのみで、規律自体は変更しない

## 契約 3: 先着判定の明文化

- comment 一覧の再取得は REST GET で全ページ取得する:

  ```
  gh api --paginate 'repos/{owner}/{repo}/issues/<N>/comments?per_page=100'
  ```

  - 取得経路を旧手順の `gh issue view <N> --json comments` から変更する理由: 同コマンドの
    `comments[].id` は GraphQL node ID (`IC_...`) であり、数値 id 比較に使えない。REST response は
    数値 `id`・`created_at`・`body` を 1 呼び出しで返す
  - `{owner}` / `{repo}` placeholder は gh が current repository から解決する
  - endpoint は既定 30 件/ページのため `--paginate` + `per_page=100` で全ページを候補集合にする
    (id 昇順のため、bare GET では直近の自分の claim が第 1 ページに含まれない可能性がある)
  - 着手手順 step 1 の早期判定 (`gh issue view <N> --json labels,comments`) は id 比較に使わない
    ため変更しない
- 先着比較は REST response の server-side `created_at` を用いる (`ts=` 自己申告値は判定に使わない)
- claim comment (body が `🔒 ai:claim ` で始まる comment) のうち `(created_at, 数値 id)` の
  辞書順が最小のものを先着とする。数値 `id` は REST response の `id` フィールド (server 採番で
  単調増加) であり、GraphQL node ID / REST `node_id` は使わない
- 自分の claim の識別は契約 2 のとおり `session=` 一致で行う
- REST GET の失敗 (非ゼロ終了・ページ取得不能) や、取得結果に自分の claim が存在しない場合は
  「競合なし」と扱わず、branch push に進まず停止してユーザに報告する (fail-closed)
- comment 経路で検知できない競合の最終確定は従来どおり branch push の成否 (変更なし)

## 契約 4: wip commit へのセッション ID 埋込

排他基盤 2 (branch push) の wip 空 commit メッセージにセッション ID を含める:

```
git commit --allow-empty -m "wip: claim issue #<N> session=<セッションID>"
```

理由: 同一メッセージ・同一 tree・同一親・同一秒の空 commit は OID が一致し、後発の push が
"already up to date" として成功扱いになる経路が理論上残る。メッセージへの ID 埋込で OID 衝突を
構造的に排除する (ユーザ decision 2026-07-12 で採用)。

## 変更対象 (Phase B)

| ファイル | 変更内容 |
|---|---|
| `plugins/agent-discipline/hooks/prompts/always-fable.md` | `rule:issue-claim` 本文へ契約 1〜4 を反映 |
| `plugins/agent-discipline/hooks/prompts/always-sonnet.md` | 同上 (Sonnet 版の詳細記述も同期。「よくある誤操作」の持ち主識別記述を含む) |
| `plugins/agent-discipline/README.md` | 手順要約 (inject-always 節の rule:issue-claim 記述) 更新 + v0.14.0 変更点追記 |
| `plugins/agent-discipline/.claude-plugin/plugin.json` | version `0.13.1` → `0.14.0` (minor: 後方互換のある挙動追加) |
| `.claude-plugin/marketplace.json` | 同 version 同期 |
| `README.md` (リポジトリ直下) | plugin 一覧テーブルの version 同期 |
| `plugins/agent-discipline/docs/claim-session-id-phase-a.md` | 本ファイルを削除 |

## 受入基準

- always-fable.md / always-sonnet.md の rule ID セットが変わらないこと
  (`plugins/agent-discipline/scripts/lint-prompt-sync.sh` が pass すること)
- 両ファイルの rule:issue-claim が契約 1〜4 を漏れなく含み、判定基準の記述が矛盾しないこと
- 両ファイルの rule:issue-claim が `--paginate`・REST `created_at`・数値 `id`・取得失敗時の
  fail-closed を含むこと (codex review P2 の再発防止)
- version 3 箇所 (plugin.json / marketplace.json / リポジトリ README) が `0.14.0` で一致すること

## スコープ外 (今回のユーザ decision で不採用)

- 旧形式 claim を見た際の無条件撤退 (旧クライアント混在期の優先譲歩)
- 撤退クリーンアップの remote branch 削除を「自分の push 成功時のみ」に限定する規律強化
- claim 投稿の `gh api` POST 化 (返却 JSON からの comment id 直接捕捉)
- issue ごとの lock ref による排他機構の再設計

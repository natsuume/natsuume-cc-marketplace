---
name: leadtime
description: GitHub の issue/PR タイムラインから AI タスクのリードタイム (着手→PR ready) を分析し、バイアス統制付きの推移レポートを生成する。リードタイム分析、タスク完了時間の推移調査、モデル・plugin 変更の影響検証の依頼で使う
---

# /repo-analytics:leadtime — リードタイム分析

GitHub issue/PR のタイムラインを収集し、生存バイアス (打ち切り censoring) とサイズ交絡 (PR の大きさ) を統制したリードタイム推移レポートを Artifact として発行する。

まず、この `SKILL.md` を含む `skills/leadtime/` の 2 階層上を `<plugin-root>` として解決する。通常の Skill 実行では hook 用の `${CLAUDE_PLUGIN_ROOT}` が設定される保証はないため、SKILL.md の実パスを正本にする。以降 `<plugin-root>/skills/leadtime/scripts/...` はこの解決結果を指す。

## 1. 引数の解釈

- 対象: 省略時はカレントの git リポジトリ (origin remote から `owner/repo` を解決)。ディレクトリパスが与えられた場合は配下の git リポジトリを再帰探索する。`owner/repo` のカンマ区切りリストも受け付ける。
- `since=YYYY-MM-DD` (省略可)。省略時は全期間を対象にする。
- remote が GitHub でない、または remote が存在しないリポジトリはスキップし、スキップ件数と理由をレポート・ターミナルサマリの双方に明記する。

(手順詳細は Phase B で全文化する)

## 2. 前提確認 (fail-closed)

- `gh auth status` で認証状態を確認する。
- 未認証、または後続の GraphQL クエリがエラーを返した場合は、部分データのまま分析を進めず中断し、原因をユーザーに報告する。

(手順詳細は Phase B で全文化する)

## 3. データ収集

- `<plugin-root>/skills/leadtime/scripts/` 配下の GraphQL テンプレート 3 本 (`fetch-issues.graphql` / `fetch-prs.graphql` / `fetch-issue-timeline.graphql`) を `gh api graphql --paginate -F owner=... -F name=... -f query=@<file>` で実行し、`--jq` で 1 行 1 レコードの JSONL に整形してセッションの scratchpad に保存する (プロジェクト内には作成しない)。
- `issues.jsonl` の各行で `timelineItems.totalCount > len(nodes)` の issue は、`fetch-issue-timeline.graphql` で当該 issue の timeline を追加ページングし、取得済み nodes とマージする。

(手順詳細は Phase B で全文化する)

## 4. claim 判定パターン (正本)

以下の JSON block が claim 判定パターンの唯一の定義箇所である。実行時にこの block をそのまま scratchpad のファイルに書き出し、`compute_leadtime.py` の `--claim-patterns-file` に渡す。`compute_leadtime.py` 自身はパターンの既定値を一切持たない。

```json
{
  "inProgressLabel": "ai:in-progress",
  "strict": [
    {"id": "lock-claim", "regex": "^🔒 ai:claim branch="},
    {"id": "legacy-backtick-claim", "regex": "^Claim: `claim-{issue_number}-"}
  ],
  "loose": [
    {"id": "loose-ai-claim", "regex": "ai:claim"},
    {"id": "loose-claim-prefix", "regex": "claim-{issue_number}-"}
  ]
}
```

- 各 regex は Python `re` 構文、`re.MULTILINE` で comment body に適用する (行頭 `^` は各行頭に一致)。
- プレースホルダ `{issue_number}` は、判定対象 issue の番号を `re.escape` した文字列に置換してから compile する (同一 issue 番号の一致判定を実現する)。
- strict にも loose にも一致しない comment は無視する。loose のみ一致し strict に一致しない comment は「取りこぼし候補」として issue 参照 (repo, issue number, comment createdAt) を記録する。

## 5. 集計の実行

`<plugin-root>/skills/leadtime/scripts/compute_leadtime.py` を次の CLI 契約で実行する。

```
python3 compute_leadtime.py \
  --issues <issues.jsonl のパス> \
  --prs <prs.jsonl のパス> \
  --claim-patterns-file <patterns.json のパス> \
  --as-of <ISO8601 UTC 例 2026-07-17T04:00:00Z> \
  [--since <YYYY-MM-DD>] \
  [--boundaries-file <boundaries.json のパス>]
```

- `--issues` / `--prs` / `--claim-patterns-file` / `--as-of` は必須。`--as-of` にはデータ収集完了時刻 (UTC) を渡す。
- stdout に結果 JSON (`schemaVersion` を含む) のみを出力する。診断メッセージはすべて stderr に出る。
- exit code: `0` = 成功 (空データ含む)。`2` = 入力エラー (ファイル不存在・JSONL parse 失敗・必須フィールド欠落・`--as-of`/`--since` の形式不正)。`3` = claim patterns file の契約違反 (欠落キー・regex compile 失敗)。0/2/3 いずれでも部分データで黙って続行しない (fail-closed)。
- ターミナルサマリで提示する数値は、この stdout JSON の**決定的な投影**とする。Claude はここで得た JSON の数値を再計算・改変・丸め直ししない (中央値・件数などはすべて JSON の値をそのまま転記する)。

(手順詳細は Phase B で全文化する)

## 6. イベント注釈の収集

- (a) リポジトリイベント: `git log` から plugin 新設・`feat!` (破壊的変更)・CI workflow 追加などの候補を抽出する。
- (b) モデル・ツールイベント: WebSearch で Anthropic / OpenAI の主要イベント (特定モデル名をハードコードしない) を検索し、ローカル痕跡 (`~/.codex/config.toml` の更新日、plugin cache の更新日) で適用日を補正する。
- WebSearch が使えない場合は注釈収集を省略して続行し、省略した旨を Artifact レポートとターミナルサマリの両方に明記する。収集した注釈は `boundaries.json` (契約: `[{"id": ..., "label": ..., "at": ISO8601}]`) に整形し、`--boundaries-file` を付けて集計を再実行する。

(手順詳細は Phase B で全文化する)

## 7. 可視化と Artifact レポート

- チャートを作成する前に dataviz skill を、Artifact を発行する前に artifact-design skill を必ずロードし、palette validator を実行する。
- 含めるチャート: 散布図 (対数軸・打ち切りを◇マーカー・イベント境界の縦線)、区間分解 (start→PR作成→ready→merge) のグループ棒、サイズ帯×週のヒートマップ、draft 経由率の週次系列、イベント年表、区間統計テーブル、全件テーブル (折りたたみ)。
- ライト/ダーク両テーマに対応し、外部ライブラリを使用しない。

(手順詳細は Phase B で全文化する)

## 8. 解釈の規律

- 中央値の底上げ・テールの伸び・停滞 (n の減少) を区別し、要因を分離できない箇所は「識別不能」と明示する。
- レポートには「測定上の限界」節を必須とし、壁時計時間ベースであること・比較可能な coverage 期間・各集計の n の小ささ・推定 (censoring・intervalStats 等) を用いた箇所を列挙する。
- 改善余地は断定した対策ではなく「データが指す介入点」としてユーザーに提示し、意思決定はユーザーに委ねる。

(手順詳細は Phase B で全文化する)

## 9. ターミナルサマリ

- 結論、主要数値 (第 5 章の stdout JSON の決定的投影)、測定上の限界、発行した Artifact の URL を簡潔に報告する。

(手順詳細は Phase B で全文化する)

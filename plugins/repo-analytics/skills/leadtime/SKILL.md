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
- すべてのターゲット (カレントリポジトリ・再帰探索で発見した checkout・明示指定の owner/repo エントリ) は、クエリ実行前に owner/repo の収集キーへ正規化して重複排除する。キーの比較は case-insensitive で行い、同一リポジトリは 1 回だけ収集する (worktree や clone が複数あっても二重集計しない)。この重複排除は 2 段階の契約である。第 1 段はここで述べる、収集キー (owner/repo の入力文字列) を小文字化して比較する case-insensitive dedup である。第 2 段はセクション 3 の収集ループ冒頭で、API が解決した canonical 名 (nameWithOwner) を基準に行う dedup であり、リネーム・移管によって収集キー上は別名に見えるが実体が同一リポジトリであるケースを捕捉する。JSONL レコードに書く repo 値はこの正規化キー (第 1 段のキー) ではなく、API が返す canonical な nameWithOwner を使う。各収集キー (owner/repo) に、解決に使ったローカル checkout パスを optional として保持する。owner/repo 直接指定と発見済み checkout が同一リポジトリに重複した場合も checkout の関連付けを失わない。同一リポジトリに複数の checkout がある場合は最初に発見したものを代表として選ぶ。保持した checkout はセクション 6 のリポジトリイベント抽出で使う。

### 手順

0. 作業ディレクトリを次のとおり定義する。`<work-root>` = セッション scratchpad 配下の `repo-analytics-leadtime/` (`<scratchpad>/repo-analytics-leadtime/`。`<scratchpad>` はセッションの scratchpad ディレクトリ)。`<work>` = `<work-root>/<一意な実行 ID>/` (例: `date -u +%Y%m%dT%H%M%SZ` 等で採番) を **新規作成** する (既存ディレクトリの再利用・`mkdir -p` による黙認は禁止。ディレクトリ作成が既存パスと衝突したら別の実行 ID を採番し、必ず新規作成できたディレクトリを `<work>` として使う)。収集診断 (`collection-diagnostics.json`) を含むこの実行のすべての中間ファイルは `<work>` 配下にのみ書く: `issues.jsonl` / `prs.jsonl` / `patterns.json` / `boundaries.json` / `collection-diagnostics.json` / `result.json`。`collection-diagnostics.json` を `{"skippedRepos": [], "webSearchSkipped": false, "repoEventCollection": []}` で初期化する (Write ツール)。リトライ時は新しい `<work>` を作成してこのセクションからやり直し、過去の実行 (別の `<work>`) の部分成果物を再利用しない。
1. 呼び出し引数の文字列を分割し、`since=YYYY-MM-DD` に一致するトークンを期間指定として取り出す (複数あれば最後の値を採用し、その旨を記録する)。残りのトークンを対象指定として扱う。対象指定・期間指定のいずれも無ければ対象は「カレントディレクトリの git リポジトリ」、期間は「全期間」とみなす。
2. 対象指定が既存ディレクトリのパスであれば手順 3 の再帰探索、それ以外 (存在しないパス、またはカンマを含む文字列) であれば `owner/repo` のカンマ区切りリストとして手順 4 に進む。
3. ディレクトリパスが与えられた場合、配下の git リポジトリを次のように再帰探索する (探索深さの上限で暴走を防ぐ)。

   ```bash
   find <dir> -maxdepth 4 -name .git
   ```

   なお、`-maxdepth` は POSIX の規定外だが、本 Skill の対象環境である GNU find (Linux / WSL2) と macOS の `/usr/bin/find` はともにサポートしている。

   ヒットした各 `.git` (ディレクトリまたは worktree の gitdir ファイル) の親ディレクトリを checkout パスとして扱う。
4. 各 checkout パス (カレントディレクトリを含む) について origin remote から owner/repo を解決する。

   ```bash
   git -C <path> remote get-url origin
   ```

   - コマンドが非 0 で終了する (remote 未設定) 場合: 「remote が存在しない」としてスキップし、`{"repo": "<path>", "reason": "no_remote"}` を `<work>/collection-diagnostics.json` の `skippedRepos` に追記する。
   - URL がホスト `github.com` の SSH 形式 (`git@github.com:owner/repo.git`) の場合、`:` 以降を取り出し末尾の `.git` を除去して `owner/repo` を得る。
   - URL がホスト `github.com` の HTTPS / `ssh://` 形式
     (`https://github.com/owner/repo(.git)?` / `ssh://git@github.com/owner/repo(.git)?`)
     の場合、パス部分から `owner/repo` を取り出し末尾の `.git` を除去する。
   - 上記いずれの形式にも一致しない、またはホストが `github.com` でない場合: 「remote が GitHub でない」としてスキップし、`{"repo": "<解決できた範囲の文字列>", "reason": "non_github_remote"}` を同じく `skippedRepos` に追記する。
5. 明示指定の `owner/repo` エントリ (カンマ区切りリストの各要素、前後の空白を trim) はそのまま収集キー候補として採用する (この段階では GitHub への到達性を検証しない。存在しない owner/repo は第 3 章の `gh api graphql` 実行時にエラーとして判明し、第 2 章の fail-closed 規則に従って扱う)。
6. 手順 4・5 で得た収集キー候補全体を、`owner/repo` を小文字化した文字列をキーとして重複排除する (case-insensitive)。重複した場合、由来 (再帰探索 / 明示指定) を問わず 1 回だけ収集対象に残す。重複排除後のキー一覧が第 3 章のデータ収集ループの対象になる。

## 2. 前提確認 (fail-closed)

- `command -v jq` と `jq --version` で jq の有無とバージョンを確認する。不在、または 1.5 未満の場合は GitHub API 呼び出し前に fail-closed で中断し、必要バージョン (jq 1.5+) を報告する。
- `gh auth status` で認証状態を確認する。
- 未認証、または後続の GraphQL クエリがエラーを返した場合は、部分データのまま分析を進めず中断し、原因をユーザーに報告する。

### 手順

1. データ収集 (第 3 章) を始める前に、必ず次のコマンドで jq の有無とバージョンを確認する。

   ```bash
   command -v jq
   jq --version
   ```

   `command -v jq` が非 0 の exit code で終了する (jq が見つからない)、または `jq --version` が報告するバージョンが 1.5 未満の場合、これ以降の手順に進まず、ここで作業を中断する。中断時にユーザーへ報告する内容:
   - jq が不在、またはバージョンが 1.5 未満であったこと (コマンドの出力を含める)
   - 必要バージョン (jq 1.5+) であること、および対応方法 (jq のインストール・更新)
   - この時点で発生した副作用は無い (gh の read-only query すら未実行) こと
2. jq の前提を満たしていれば、続けて必ず次のコマンドで認証状態を確認する。

   ```bash
   gh auth status
   ```

3. 上記コマンドが非 0 の exit code で終了する、または出力が未認証を示す場合 (例: `You are not logged into any GitHub hosts`)、これ以降の手順に進まず、ここで作業を中断する。中断時にユーザーへ報告する内容:
   - `gh auth status` が未認証を示したこと (コマンドの出力を含める)
   - 対応方法 (`gh auth login` を実行してから再実行する)
   - この時点で発生した副作用は無い (gh の read-only query すら未実行) こと
4. 認証済みであれば第 3 章のデータ収集に進む。第 3 章以降で個々の `gh api graphql` 呼び出しがエラー (非 0 exit code、または応答 JSON に `errors` 配列を含む) を返した場合も同じ fail-closed 規則を適用する — 取得済みの部分データ (JSONL や中間ファイル) を集計・可視化には使わず、収集が完了していたリポジトリ数・失敗したリポジトリと owner/repo・エラーメッセージをユーザーに報告して中断する。

## 3. データ収集

- `<plugin-root>/skills/leadtime/scripts/` 配下の GraphQL テンプレート 4 本 (`fetch-issues.graphql` / `fetch-prs.graphql` / `fetch-issue-timeline.graphql` / `fetch-pr-closing-issues.graphql`) を `gh api graphql` で実行し、`--jq` で 1 行 1 レコードの JSONL に整形してセッションの scratchpad に保存する (プロジェクト内には作成しない)。
- 各行の repo フィールドには API が返す canonical な nameWithOwner を使う (ユーザ入力の owner/repo 文字列を使わない。closer や closingIssuesReferences が返す nameWithOwner と join キーのケーシングを一致させるため)。
- 変数の型に応じて `-f` (`--raw-field`、型変換なし) と `-F` (`--field`、`true`/`false`/`null`/数値に見える値を JSON 型へ変換し `@` をファイル読み込みとして解釈する) を使い分ける: 文字列変数 (`owner` / `name`) は `-f` で渡す (`-F` だと `2026` のような repo 名が数値へ変換され GraphQL `String!` と型不一致になるため)。数値変数 (`fetch-issue-timeline.graphql` / `fetch-pr-closing-issues.graphql` の `$number: Int!`) とクエリファイル展開 (`query=@<file>`、`-f` だと `@` がリテラル送信されてしまう) は `-F` で渡す。例: `gh api graphql --paginate -f owner=<owner> -f name=<name> -F query=@<file>`。
- `issues.jsonl` の各行で `timelineItems.totalCount > len(nodes)` の issue は、`fetch-issue-timeline.graphql` で当該 issue の timeline を先頭から全ページ取得し、一覧クエリ由来の timelineItems を丸ごと置き換える (部分結果とのマージはページ重複を生むため行わない)。置換後の timelineItems は totalCount と全 nodes を保持し、totalCount == len(nodes) を満たす形に再構成する。
- `fetch-prs.graphql` は OPEN + MERGED の PR を収集する (merged PR のみではない)。
- `prs.jsonl` の各行で `timelineItems.totalCount > len(nodes)` の PR は timeline 取得が不完全である。PR 側には追加ページングテンプレートを用意しない (ready/draft の 2 イベント種に絞った totalCount が 100 を超える PR は実運用上ほぼ発生しない) ため、該当 PR は集計スクリプトが除外し `exclusions.prTimelineOverflow` に列挙する。除外件数はレポートの「測定上の限界」に明記する。
- `prs.jsonl` の各行で `closingIssuesReferences.totalCount > len(nodes)` の PR は `fetch-pr-closing-issues.graphql` で当該 PR の closingIssuesReferences を先頭から全ページ取得し、一覧クエリ由来の closingIssuesReferences を丸ごと置き換える (部分結果とのマージはページ重複を生むため行わない)。置換後の closingIssuesReferences は totalCount と全 nodes を保持し、totalCount == len(nodes) を満たす形に再構成する (集計スクリプトは不完全な行を入力エラーとして中断する)。
- 収集段階の診断 (第 1 章のリポジトリスキップ件数・理由、第 6 章のリポジトリイベント収集結果・WebSearch 省略の有無等) は、判明した時点で scratchpad の固定 shape JSON (例: `{"skippedRepos": [{"repo": str, "reason": str}], "webSearchSkipped": bool, "repoEventCollection": [{"repo": str, "status": "collected" | "no_checkout" | "default_ref_unavailable" | "default_ref_stale"}]}`) に追記して記録する。第 9 章のターミナルサマリと Artifact レポートは、この記録された値をそのまま参照し独自に再集計しない。

### 手順

0. 第 1 章で新規作成済みの `<work>` (`<work-root>/<一意な実行 ID>/`) をそのまま使う。
1. 第 1 章で重複排除した収集キー (owner/repo) それぞれについて、次の canonical 名解決 (第 2 段の重複排除) を先に行ったうえで a〜d を順に実行する。

   **canonical 名の解決と収集時 dedup (第 2 段)**

   リポジトリごとの収集開始時、issues / PR の取得より前に、canonical 名を独立に解決する。

   ```bash
   gh api repos/<owner>/<name> --jq .full_name
   ```

   (issue / PR が 0 件のリポジトリでも取得できる)。小文字化した canonical 名を「canonical → (ターゲット, checkout パス)」の対応表と突合する。

   - (i) 既出なら `duplicate_canonical` として収集診断 `skippedRepos` (`{"repo": "<canonical の nameWithOwner>", "reason": "duplicate_canonical"}`) に記録し、そのターゲットの issues / PR 収集をスキップして次の収集キーに進む (この収集キーについては a〜d を実行しない)。既存エントリに checkout パスが無く今回のターゲットが checkout を持つ場合は、既存エントリへ checkout を補完する。
   - (ii) 新規なら対応表に登録し、以降の収集 (a〜d) はこの canonical の owner/name を使う。

   `issues.jsonl` / `prs.jsonl` への追記は、この canonical dedup 判定の後にのみ行う (判定前に書き込まない)。

   **a. issues 収集**

   ```bash
   gh api graphql --paginate \
     -f owner=<owner> -f name=<name> \
     -F query=@<plugin-root>/skills/leadtime/scripts/fetch-issues.graphql \
     --jq '.data.repository as $r | $r.issues.nodes[] | . + {repo: $r.nameWithOwner}' \
     >> <work>/issues.jsonl
   ```

   **b. prs 収集**

   ```bash
   gh api graphql --paginate \
     -f owner=<owner> -f name=<name> \
     -F query=@<plugin-root>/skills/leadtime/scripts/fetch-prs.graphql \
     --jq '.data.repository as $r | $r.pullRequests.nodes[] | . + {repo: $r.nameWithOwner}' \
     >> <work>/prs.jsonl
   ```

   いずれも `--jq` で `repository.nameWithOwner` (canonical 形) を各行の `repo` に注入し、1 行 1 レコードの JSONL として追記する (ユーザ入力の owner/repo 文字列は使わない)。

   手順 c・d で使う `<owner>`/`<name>`/`<owner>/<name>` は、この収集キー (手順 1 の重複排除後のキー。caller 指定や再帰探索由来の casing をそのまま保持しうる) ではなく、a・b で `issues.jsonl` / `prs.jsonl` に書き込み済みの canonical `repo` 値 (nameWithOwner) を `/` で分解して得た owner/name を使う。overflow 検知の `select(.repo == ...)`、再取得 API 呼び出しの `-f owner=... -f name=...`、再取得後の置換対象特定 (`.repo == ... and .number == ...`) はすべてこの canonical 値で統一し、収集キーの casing を使わない (同一リポジトリでも収集キーと canonical の casing が食い違いうるため、caller 指定の casing のまま突合すると一致しない場合がある)。

   **c. issue timeline overflow の検知と置換**

   ```bash
   jq -c 'select(.repo == "<owner>/<name>" and (.timelineItems.totalCount > (.timelineItems.nodes | length)))' <work>/issues.jsonl
   ```

   該当した各 issue (`repo`, `number` の組) について、`fetch-issue-timeline.graphql` で先頭ページから全ページを取得し直す。

   ```bash
   gh api graphql --paginate \
     -f owner=<owner> -f name=<name> -F number=<issue_number> \
     -F query=@<plugin-root>/skills/leadtime/scripts/fetch-issue-timeline.graphql \
     --jq '.data.repository.issue.timelineItems' \
     | jq -s '{totalCount: .[0].totalCount, nodes: (map(.nodes) | add)}' \
     > <work>/_overflow-issue-timeline.json
   ```

   得られた `{totalCount, nodes}` で `totalCount == (nodes | length)` になっていることを確認したうえで、`issues.jsonl` 中の該当行の `timelineItems` を丸ごと置き換える (部分結果とのマージはしない)。置換コマンドは `--argjson` (コマンドライン引数として展開するため ARG_MAX の上限にかかりうる) ではなく `--slurpfile` (ファイルから直接読み込むため ARG_MAX の制約を受けない) でファイルベースに渡す。

   ```bash
   jq -c --slurpfile repl <work>/_overflow-issue-timeline.json \
     'if .repo == "<owner>/<name>" and .number == <issue_number> then .timelineItems = $repl[0] else . end' \
     <work>/issues.jsonl > <work>/issues.jsonl.tmp && mv <work>/issues.jsonl.tmp <work>/issues.jsonl
   ```

   **d. PR closingIssuesReferences overflow の検知と置換**

   `prs.jsonl` に対して同様に検知する。

   ```bash
   jq -c 'select(.repo == "<owner>/<name>" and (.closingIssuesReferences.totalCount > (.closingIssuesReferences.nodes | length)))' <work>/prs.jsonl
   ```

   該当した各 PR (`repo`, `number` の組) について、`fetch-pr-closing-issues.graphql` で先頭ページから全ページを取得し直す。

   ```bash
   gh api graphql --paginate \
     -f owner=<owner> -f name=<name> -F number=<pr_number> \
     -F query=@<plugin-root>/skills/leadtime/scripts/fetch-pr-closing-issues.graphql \
     --jq '.data.repository.pullRequest.closingIssuesReferences' \
     | jq -s '{totalCount: .[0].totalCount, nodes: (map(.nodes) | add)}' \
     > <work>/_overflow-pr-closing-issues.json
   ```

   得られた `{totalCount, nodes}` で `totalCount == (nodes | length)` になっていることを確認したうえで、`prs.jsonl` 中の該当行の `closingIssuesReferences` を丸ごと置き換える (部分結果とのマージはしない)。issue timeline の置換と同じ理由で `--argjson` ではなく `--slurpfile` を使う。

   ```bash
   jq -c --slurpfile repl <work>/_overflow-pr-closing-issues.json \
     'if .repo == "<owner>/<name>" and .number == <pr_number> then .closingIssuesReferences = $repl[0] else . end' \
     <work>/prs.jsonl > <work>/prs.jsonl.tmp && mv <work>/prs.jsonl.tmp <work>/prs.jsonl
   ```

   PR 側の `timelineItems` (`ReadyForReviewEvent` / `ConvertToDraftEvent`) は overflow しても追加ページングテンプレートが無いため置換せず、既存の bullet のとおり集計スクリプトの除外に委ねる。
2. 全リポジトリの収集が終わったら、`date -u +%Y-%m-%dT%H:%M:%SZ` 等で収集完了時刻 (UTC ISO8601) を記録する。この値を第 5 章の `--as-of` に渡す。
3. 収集中に判明した診断 (スキップしたリポジトリの最終件数など) を `<work>/collection-diagnostics.json` へ反映する (Write ツールで上書き)。第 9 章のターミナルサマリと Artifact レポートはこのファイルの値をそのまま参照し、独自に再集計しない。

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

patterns.json への書き出し: 上記 JSON block を一言一句そのまま (プレースホルダ `{issue_number}` を含む) Write ツールで `<work>/patterns.json` に保存する。実行のたびに新規作成 (既存ファイルがあれば上書き) してよい。

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
- exit code: `0` = 成功 (空データ含む)。`2` = 入力エラー (ファイル不存在・JSONL parse 失敗・必須フィールド欠落・`--as-of`/`--since` の形式不正・`--boundaries-file` の検証失敗 (ファイル不存在・JSON parse 失敗・形状不正・`at` の ISO8601/UTC 不正または naive 時刻・`id`/`label` の欠落または空文字列・`id` の重複))。`3` = claim patterns file の契約違反 (欠落キー・regex compile 失敗)。0/2/3 いずれでも部分データで黙って続行しない (fail-closed)。
- ターミナルサマリで提示する数値は、この stdout JSON の**決定的な投影**とする。Claude はここで得た JSON の数値を再計算・改変・丸め直ししない (中央値・件数などはすべて JSON の値をそのまま転記する)。

### 手順

1. 第 4 章で作成した `<work>/patterns.json` と、第 3 章で完成させた `<work>/issues.jsonl` / `<work>/prs.jsonl`、および第 3 章手順 2 で記録した収集完了時刻を用意する。
2. 初回実行 (この時点では第 6 章のイベント注釈がまだ無いため `--boundaries-file` は付けない)。

   ```bash
   python3 <plugin-root>/skills/leadtime/scripts/compute_leadtime.py \
     --issues <work>/issues.jsonl \
     --prs <work>/prs.jsonl \
     --claim-patterns-file <work>/patterns.json \
     --as-of <収集完了時刻> \
     [--since <第 1 章で取り出した since>] \
     > <work>/result.json
   ```

3. exit code に応じて次のように対応する。
   - `0`: 成功 (対象 0 件の空データを含む)。`<work>/result.json` を後続 (第 6〜9 章) の入力として使い続行する。
   - `2`: 入力エラー。stderr の診断メッセージを確認し、`issues.jsonl` / `prs.jsonl` の欠落フィールドや overflow 置換漏れ (第 3 章手順 1c/1d)、`--as-of` / `--since` の形式、`--boundaries-file` (再実行時) の形状を点検して修正し、再実行する。原因を特定・修正できない場合は部分データのまま先へ進まず、第 2 章と同じ fail-closed 規則でユーザーに報告して中断する。
   - `3`: `patterns.json` の契約違反。第 4 章の JSON block と一言一句一致しているか (キー欠落・regex 不正) を確認し、修正して再実行する。修正できない場合は同様に中断してユーザーに報告する。
4. 第 6 章でイベント注釈 (`boundaries.json`) を作成したら、`--boundaries-file <work>/boundaries.json` を追加して同じコマンドを再実行し、`<work>/result.json` を上書きする。以降の第 7〜9 章はこの (boundaries 込みの) 最終版 `result.json` を正本として使う (`intervalStats` は boundaries 無指定だと常に `[]` になるため、区間統計を含むレポートにはこの再実行が必須)。イベント注釈が 1 件も収集できなかった場合 (第 6 章参照) は再実行を省略し、初回の `result.json` をそのまま最終版として扱う。

## 6. イベント注釈の収集

- (a) リポジトリイベント: `git log` から plugin 新設・`feat!` (破壊的変更)・CI workflow 追加などの候補を抽出する。
- (b) モデル・ツールイベント: WebSearch で Anthropic / OpenAI の主要イベント (特定モデル名をハードコードしない) を検索し、ローカル痕跡 (`~/.codex/config.toml` の更新日、plugin cache の更新日) で適用日を補正する。
- WebSearch が使えない場合は注釈収集を省略して続行し、省略した旨を Artifact レポートとターミナルサマリの両方に明記する。収集した注釈は `boundaries.json` (契約: `[{"id": ..., "label": ..., "at": ISO8601}]`) に整形し、`--boundaries-file` を付けて集計を再実行する。

### 手順

**(a) リポジトリイベントの抽出**

対象リポジトリの default branch 上のコミットのみを対象にする (マージ/squash 済みのコミット時刻 = `%cI` (committer date, ISO8601) をイベント時刻として採用する。フィーチャーブランチ上の元コミット日時 `%aI` ではなく、default branch に反映された時刻を使う)。ローカル checkout の作業ツリーが実際に default branch を指しているとは限らないため、`git log` を無条件に実行せず、次の手順で repo/ref を明示的に束縛してから実行する。`git fetch` は使わない (ローカル git メタデータへの書き込みであり、本 skill の副作用契約「gh read-only query + scratchpad 書き込みのみ」に反するため)。

1. read-only の GitHub API で対象リポジトリの default branch 名と tip の commit OID を取得する。

   ```bash
   gh api repos/<owner>/<name> --jq .default_branch
   gh api repos/<owner>/<name>/branches/<default-branch> --jq .commit.sha
   ```

2. 第 1 章で保持したこのリポジトリの checkout パスを確認する。checkout が無い (第 1 章手順 3 の再帰探索で発見されず、明示指定 owner/repo にも対応する checkout が紐付いていない) 場合、このリポジトリのイベント抽出をスキップし、`<work>/collection-diagnostics.json` の `repoEventCollection` に `{"repo": "<owner>/<name>", "status": "no_checkout"}` を追記して手順 3 以降に進まない。
3. checkout があれば、ローカルの追跡 ref を確認する。

   ```bash
   git -C <checkout> rev-parse --verify refs/remotes/origin/<default-branch>
   ```

   コマンドが非 0 で終了する (追跡 ref が無い) 場合、このリポジトリのイベント抽出をスキップし、`repoEventCollection` に `{"repo": "<owner>/<name>", "status": "default_ref_unavailable"}` を追記して手順 4 に進まない。
4. 手順 1 で取得した tip OID と手順 3 で得たローカル ref の OID を比較する。
   - 一致する場合、その ref (`refs/remotes/origin/<default-branch>`) を明示して次のコマンド群を実行する (`<ref>` はこの ref を指す)。

     - plugin / 機能の新設 (初回追加) の検出例:

       ```bash
       git -C <checkout> log <ref> --diff-filter=A --format='%H|%cI|%s' -- 'plugins/*/.claude-plugin/plugin.json'
       ```

     - 破壊的変更 (Conventional Commits の `!:` 記法) の検出例:

       ```bash
       git -C <checkout> log <ref> --format='%H|%cI|%s' | grep -E '^[0-9a-f]+\|[^|]+\|[a-z]+(\([^)]+\))?!:'
       ```

     - CI workflow 追加の検出例:

       ```bash
       git -C <checkout> log <ref> --diff-filter=A --format='%H|%cI|%s' -- '.github/workflows/*.yml' '.github/workflows/*.yaml'
       ```

     各ヒットの `%cI` をイベント時刻として採用し、コミットメッセージ (`%s`) や変更ファイルからラベルを組み立てる (例: 「plugin repo-analytics 新設」)。完了後、`repoEventCollection` に `{"repo": "<owner>/<name>", "status": "collected"}` を追記する。
   - 不一致 (stale) の場合、このリポジトリのイベント抽出をスキップし、`repoEventCollection` に `{"repo": "<owner>/<name>", "status": "default_ref_stale"}` を追記する。

いずれかの理由でスキップした対象については、「この対象はローカル checkout が存在しない (または ref が確認できない) ため、当該リポジトリに由来するリポジトリイベント注釈を含まない」という注記を Artifact レポート (第 7・8 章) とターミナルサマリ (第 9 章) に明記する。

**(b) モデル・ツールイベントの収集**

1. WebSearch で対象期間の Anthropic / OpenAI の主要イベントを検索する。特定モデル名をクエリにハードコードせず、次のようなクエリ雛形を対象期間 (収集対象タスクの `firstStartAt` の最小値〜`--as-of`) に差し替えて使う。

   - `Anthropic model releases <期間>`
   - `OpenAI model releases <期間>`
   - `Claude Code CLI major update <期間>`

   検索結果から判明した候補イベント (リリース日・GA 日・デフォルトモデル変更等) を一旦リストアップする。
2. ローカル痕跡の mtime を取得する。locale 依存の表記 (`%y` / `%Sm` 等) は環境によって曜日・月名の表記が変わり比較を誤らせるため使わず、epoch 秒で取得してから ISO8601 (UTC) に変換する。
   - `~/.codex/config.toml` の mtime (epoch 秒): Linux は `stat -c '%Y' ~/.codex/config.toml`、macOS は `stat -f '%m' ~/.codex/config.toml`。
   - `~/.claude/plugins/cache` / `~/.codex/plugins/cache` 配下の mtime (epoch 秒): 各ディレクトリに対して同様に `stat` (上記いずれかの形式) を実行する。
   - epoch 秒から ISO8601 (UTC) への変換: Linux は `date -u -d @<epoch 秒> +%Y-%m-%dT%H:%M:%SZ`、macOS は `date -u -r <epoch 秒> +%Y-%m-%dT%H:%M:%SZ`。
3. mtime の採用規則。次の (1)〜(3) をこの順に適用する。
   1. ローカル痕跡が対象イベントと意味的に結び付くこと (該当モデル・ツールの設定/キャッシュであること) を確認する。無関係な設定変更等、関連性を確認できない mtime は候補にしない。
   2. `リリース日時 <= mtime <= --as-of` の場合に限り、その mtime を推定適用日として `boundaries.json` の `at` に採用する。`label` には「(適用日は推定、根拠: <ローカル痕跡のパス>)」等、推定であることを明記する。
   3. リリース日より前・`--as-of` より後・関連性を確認できない mtime は採用しない。ローカル痕跡が全く得られない場合も同様に扱う。これらの場合はリリース日をそのまま `at` に採用し、`label` に「(ローカル適用日不明、リリース日で代用)」と明記する。
4. WebSearch が使えない場合は本節 (b) の収集をまるごと省略して続行する。`<work>/collection-diagnostics.json` の `webSearchSkipped` を `true` に更新し (Write ツールで上書き)、省略した旨を Artifact レポート (第 7・8 章) とターミナルサマリ (第 9 章) の両方に明記する。
5. (a)(b) で集めたイベントを `boundaries.json` の契約 (`[{"id": str, "label": str, "at": ISO8601}]`) に整形し、Write ツールで `<work>/boundaries.json` に書き出す。`id` は重複しない短い識別子 (例: `repo-plugin-repo-analytics-added`, `model-anthropic-202607`) を付ける。1 件も収集できなかった場合 (a も b も候補が無い、または b を丸ごと省略しかつ a も 0 件) は `boundaries.json` を作成せず、第 5 章手順 4 の再実行を省略する。

## 7. 可視化と Artifact レポート

- チャートを作成する前に dataviz skill を、Artifact を発行する前に artifact-design skill を必ずロードし、palette validator を実行する。
- 含めるチャート: 散布図 (対数軸・打ち切りを◇マーカー・イベント境界の縦線)、区間分解 (start→PR作成→ready→merge) のグループ棒、サイズ帯×週のヒートマップ、draft 経由率の週次系列、イベント年表、区間統計テーブル、全件テーブル (折りたたみ)。
- 散布図の縦軸は対数スケールを既定とし、描画する y 値が 0 以下のレコードは軸下端の「≤0」専用バンドに別マーカー (×) で表示する (対数変換から除外するのであって、データから除外しない)。× の凡例は「対数軸に直接配置できない値」と定義する。negativeInterval だが y 値が正のレコードは正しい数値位置に置く。値は clamp せず JSON の値を表示し、hover と keyboard focus の双方で実値と理由に到達可能にする。全件テーブルにも同じ値とフラグを残す。
- 週次・区間の中央値を提示するすべてのチャート・テーブルに「完了タスクのみの記述的中央値 (censor 非調整)」の注記を付け、n と censoredN を隣接表示する。ターミナルサマリや結論文で同じ中央値を引用する場合も同じ限定を省略しない。
- 全件テーブル・hover・PR リンクは常に `(prRepo, pr)` の組を使う (`pr` 番号単独で表示・リンクしない。cross-repo close では issue の repo と PR の repo が異なるため、`pr` 番号だけでは対象 PR を一意に特定できない)。
- ライト/ダーク両テーマに対応し、外部ライブラリを使用しない。

### 実行順序

1. dataviz skill をロードする (チャートを 1 つでも作成する前に必須)。
2. 第 5 章で確定した `<work>/result.json` (boundaries 込みの最終版) を基にチャート設計を行う。以下の対応表に従い、各チャートが読む JSON キーを確認する。

   | チャート | 読む JSON キー |
   |---|---|
   | 散布図 (対数軸・打ち切り◇・イベント縦線) | `mainSeries` + `censored` (縦線は result.json の `boundaries` が非空であれば併用) |
   | 区間分解 (start→PR作成→ready→merge) のグループ棒 | `weeklyCohorts[].phaseMedians` |
   | サイズ帯×週のヒートマップ | `weeklyCohorts[].bySizeBand` |
   | draft 経由率の週次系列 | `weeklyCohorts[].viaDraftRate` |
   | イベント年表 | result.json の `boundaries` |
   | 区間統計テーブル | `intervalStats` |
   | 全件テーブル (折りたたみ) | `mainSeries` / `censored` / `auxiliarySeries` |

3. artifact-design skill をロードする (Artifact を発行する前に必須)。
4. HTML を作成する (自己完結、外部ライブラリ不使用、CSP 制約に従う)。既存の契約 (対数軸・非正値バンド・記述的中央値の注記・ライト/ダーク両対応) をすべて満たすこと。
5. palette validator を実行し、使用した配色が検証を通ることを確認する。
6. Artifact ツールで発行し、発行 URL を控える (第 9 章のターミナルサマリで報告するため)。

## 8. 解釈の規律

- 中央値の底上げ・テールの伸び・停滞 (n の減少) を区別し、要因を分離できない箇所は「識別不能」と明示する。
- レポートには「測定上の限界」節を必須とし、壁時計時間ベースであること・比較可能な coverage 期間・各集計の n の小ささ・推定 (censoring・intervalStats 等) を用いた箇所を列挙する。
- 改善余地は断定した対策ではなく「データが指す介入点」としてユーザーに提示し、意思決定はユーザーに委ねる。
- 完了根拠 (qualifying completion) は MERGED の PR、または現在 OPEN・non-draft の PR の ready 到達である (merge そのものではない)。merge されず close された PR は破棄された試行として完了根拠にしない。ready 到達済み・未 merge のタスクは mainSeries に completionBasis = "ready_unmerged" として含まれ、打ち切り (censored) は qualifying PR がリンクされていない着手済み open issue に限定される。
- 週次 cohort の中央値は完了タスクのみから計算される記述的統計である (censor 非調整)。censored のみの週も median null + censoredN で行が出るため、censoredN が大きい週の中央値は必ず censoredN と併せて解釈する。
- 収集範囲外のリポジトリの merged PR で close された issue は `auxiliarySeries.externalMergedClose` として分離集計され、`mainSeries` にも `censored` にも入らない (ready 到達時刻を計測できないため)。

### 「測定上の限界」チェックリスト

レポートの「測定上の限界」節には、次の項目を毎回すべて含める (該当が無い項目も「該当なし」と明記し、省略しない)。

- [ ] 壁時計時間ベースの計測であること (作業時間・稼働時間ではない旨)
- [ ] 比較可能な coverage 期間 (`markerCoverage` の repo × 月別 `coverage` を基に、着手マーカーが安定して観測できている期間の範囲)
- [ ] 各集計の n の小ささ (`weeklyCohorts[].n` / `intervalStats[].n` が小さい週・区間の明示。`n = 0` の週は空欄・線の途切れとして扱い隠さない)
- [ ] 推定を用いた箇所: 打ち切り (`censored` の `elapsedHoursLowerBound` は下限値であること) と、第 6 章で補正・代用したイベント適用日 (推定である旨のラベルをそのまま引用する)
- [ ] `exclusions.timelineOverflow` / `exclusions.prTimelineOverflow` の件数
- [ ] `dataQuality` 各値 (`negativeIntervalCount` / `redraftPrCount` / `notStartedClosedIssues` / `multipleReadyPrIssues`) の件数
- [ ] `markerCoverage` 中の `unknownTimeline` (timeline 不完全で観測不能だった件数)
- [ ] `claimDetection.looseOnlyIssues` の件数 (取りこぼし候補)
- [ ] リポジトリイベント注釈をスキップした対象とその理由 (収集診断の `repoEventCollection`)

### 個別の実行時挙動への対応

- `repos[].closedIssues == 0` のリポジトリでも、OPEN issue が ready 到達済みの qualifying PR を持てば `mainSeries` に `completionBasis == "ready_unmerged"` として編入されうる (mainSeries への編入は issue の `state` を問わないため、closedIssues の値だけでは mainSeries 対象外と断定できない)。レポート・サマリで「ready 済み・未 merge」のタスクがあると記載してよいかどうかは、`mainSeries` に `completionBasis == "ready_unmerged"` のレコードが 1 件以上存在するかで判定する (実メンバーシップ判定。派生カウンタ `repos[].openReadyPrs` による判定は廃止する — `openReadyPrs` は issue との紐付けを問わない repo 単位の PR 集計であり、mainSeries への編入有無と一致しない)。該当レコードが無いにもかかわらず `closedIssues == 0` の repo は、`repos[].mergedPrs` / `repos[].openReadyPrs` (merged PR 系の補助指標) のみで報告し、主系列 (`mainSeries`) の対象外である旨を明記する。エラーとして扱わない。
- `markerCoverage[].coverage == 0.0` (該当 repo × 月に着手マーカーが 1 件も無い) はエラーにせず「marker coverage 0%」としてそのまま報告する。`coverage == null` (`closedIssues == 0`、観測不能) とは区別して報告する。
- draft を経ていない PR は `resolve_ready` の契約により ready 時刻 = PR `createdAt` として `compute_leadtime.py` が解決済みである。SKILL.md 側で追加の判定は行わず、`result.json` の値をそのまま使う。
- 再 draft 化された PR (`redraftCount > 0`) は `dataQuality.redraftPrCount` の件数をそのまま「測定上の限界」チェックリストで開示する (上記チェックリスト参照)。
- reopen された issue は `resolve_close_linkage` の契約により最終 `ClosedEvent` を採用済みである。SKILL.md 側で追加の判定は行わない。

## 9. ターミナルサマリ

- 結論、主要数値、測定上の限界、発行した Artifact の URL を簡潔に報告する。
- 数値の根拠は二源泉に分ける: 集計数値 (中央値・件数等) は compute_leadtime.py の stdout JSON の決定的投影とする。収集段階の診断 (remote が GitHub でない等でスキップしたリポジトリ数と理由、WebSearch 省略の有無) は収集手順中に scratchpad へ固定 shape (JSON) で記録した値を用い、Artifact とターミナルの双方が同じ記録を参照する。

### サマリの構成テンプレート

1. 結論 (1〜3 文): 主指標 (着手→ready) の推移をひとことで要約する。
2. 主要数値: 代表的な週次・区間の中央値と n を `result.json` の値そのまま転記する (第 5 章の「決定的な投影」規則に従う)。
3. 測定上の限界: 第 8 章のチェックリストの要点を凝縮して記載する (省略しない項目は Artifact レポート側と揃える)。
4. 発行した Artifact の URL。

この 4 点の順序でターミナルへ簡潔に報告する。

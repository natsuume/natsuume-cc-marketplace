# natsuume-statusline プラグイン

Claude Code の `statusLine` 表示 (パス / GitHub repo / branch / 変更量 / context 使用量 / レートリミット) を提供するプラグインです。`/natsuume-statusline:setup` で `~/.claude/settings.json` に登録できます。

## バージョン

v0.7.0

## 表示内容

3 行構成 (内容に応じて省略あり):

1. **1 行目**: カレントパス、GitHub リポジトリ名、ブランチ名、staged/modified 変更量、未コミット件数 (or `clean`)
   - リポジトリの owner が自分または所属 org の場合は `owner/repo` を `repo` に短縮
   - 全体がターミナル幅を超える場合は段階的に prefix → パス短縮の順でフォールバック
2. **2 行目**: モデル名 (`model.display_name`) + context 使用量 (`ctx`) + レートリミット (5h)
   - **モデル名**: 色付けせず先頭にそのまま表示。取得できない場合は非表示 (先頭セグメント無し)
   - **context 使用量**: `ctx: (45%) 75.1k/1M` 形式の数値表示 (バー無し)。使用率 (`context_window.used_percentage`)、使用トークン数 (`total_input_tokens`)、最大コンテキスト長 (`context_window_size`) を併記。取得できない初期/compact 直後は非表示。トークン数が取れない場合は `ctx: (45%)` に縮退
   - **5h レートリミット**: `5h: 62% (58m) [████░░]` 形式。パーセンテージ、リセット残時間、プログレスバー
3. **3 行目**: 週次 (7d) レートリミット + モデル別週次枠 (例: `7d(Fable): 71% (7d00h) [███░░]`)
   - **7d レートリミット**: 2 行目の 5h と同形式
   - **モデル別週次枠**: サブスクリプションのモデル別週次上限 (例: Fable) を `7d(<モデル名>)` ラベルで表示。データの取得元・cache 契約は下記「モデル別週次枠 (3 行目)」を参照。取得できない環境 (非サブスク・curl 不在等) では 3 行目は 7d のみ、7d 自体も無ければ 3 行目自体を省略

**使用率の色**: 80% 以上で赤、60% 以上で黄、それ未満で緑 (2 行目・3 行目共通)

**横幅に合わせた段階的縮小** (2 行目・3 行目それぞれに独立して適用): 全内容が収まらない場合、`…` で切り詰める前に情報を保ったまま段階的に簡略化する。優先順位は ⓪ 使用率の小数を四捨五入して整数表示にする (`(45.2%)`→`(45%)`, `62.5%`→`63%`) → ① ctx の使用率 `(P%)` を削除 (使用/最大トークンが残るので情報は保たれる。3 行目には ctx が無いためこの段階はスキップ) → ② レートリミットのバー長を短縮 (最大 10 → 最小 3 文字) → ③ バーを削除 (`5h: 62% (58m)` のみ)。横幅に収まる最も豊かな表示を自動選択する。トークン数が取れず `(P%)` が唯一の情報のときは ① をスキップ。モデル名などの先頭固定セグメントは縮小対象にせず、最後の手段として `…` で切り詰められるのみ

## モデル別週次枠 (3 行目)

Claude Code が statusline の stdin に渡す `rate_limits` には `five_hour` / `seven_day` しか無く、Fable 等サブスクリプション固有のモデル別週次枠は含まれません。この情報は OAuth usage API (`https://api.anthropic.com/api/oauth/usage`) から取得し、TTL 付き file cache + background fetch で 3 行目に供給します。

- **データ優先順位**: (1) stdin の `rate_limits.model_scoped[]` (Claude Code バイナリに schema は存在するが本 README 執筆時点の実 stdin には未出現の公式経路。emit され始めたら自動的にこちらが優先され、下記の cache 経路・background fetch は動作しなくなります) → (2) 本プラグインの cache (OAuth usage API 由来)
- **cache パス**: `${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json` (ディレクトリ・ファイルとも所有者のみ権限、同一ディレクトリの mktemp + mv による atomic write)
- **schema**: `fetched_at` (最後に成功した fetch の epoch 秒)、`consecutive_failures` (連続失敗回数)、`next_attempt_at` (この epoch 秒より前は再 fetch しない)、`weekly_scoped` (`{display_name, percent, resets_at}` の配列)
- **TTL / backoff**: 成功時は 300 秒後に再 fetch 可能になります。失敗時 (token 取得不能・curl 失敗・非 200・JSON 不能) は `consecutive_failures` を増やし、`60 * 2^(failures-1)` 秒 (上限 1800 秒) の指数バックオフで再試行間隔を広げます。失敗時も前回成功した `weekly_scoped` は保持されるため、一時的な取得失敗で表示が消えることはありません
- **lock**: `<cache_dir>/.fetch.lock` を mkdir で排他制御し、background worker の多重起動を防ぎます (mtime が 120 秒より古い lock は前回異常終了とみなして奪取)
- **token の取り扱い**: `~/.claude/.credentials.json` の `claudeAiOauth.accessToken` (macOS では Keychain もフォールバック先) を読み、`curl --config -` で stdin 経由にのみ渡します。argv・ログ・stderr・一時ファイルに token を書き出すことはありません
- **fail-open**: `curl` が無い環境、非サブスクリプション環境 (API が `weekly_scoped` を返さない) では 3 行目に 7d のみ、あるいは 3 行目自体が表示されません。表示処理・statusline のレンダリングを background fetch がブロックすることもありません

## Context cache dump (session-handoff plugin 連携)

表示処理とは別に、statusline は stdin JSON の `context_window` データを per-session の一時 cache ファイルへ書き出します。これは session-handoff plugin (#228) が読む plugin 間契約の producer 側であり、issue #227 で追加されました。

- **出力先**: `${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<sanitized_session_id>.json` (`uid` は `id -u`、`sanitized_session_id` は `session_id` を `A-Za-z0-9._-` のみに制限した値)
- **スキーマ**: `updated_at` (stdin 受領時刻の epoch 秒)、`session_id` (サニタイズ前)、`used_percentage`、`total_input_tokens`、`context_window_size`
- **fail-open**: `session_id` 欠落/サニタイズ後空、`used_percentage` が数値でない (context_window 欠落/null を含む)、`jq` 不在、ディレクトリ作成・書き込み失敗、いずれの場合も無音でスキップし、statusline の表示には一切影響しません。`total_input_tokens` / `context_window_size` が検証に通らない場合はそのキーのみ省略して書き込みます
- **並行書き込みの直列化**: per-session の mkdir lock で直列化します (競合時は 0.1 秒間隔で最大 2 回再試行してから諦める)。monotonic guard が保証するのは「cache の `updated_at` (秒値) が減少しない」ことのみで、同一秒内は last-writer-wins です。並行描画時には最大 1 描画間隔ぶん古いサンプルが残ることがありますが、より後の秒の次の書き込みで自己回復します。consumer は advisory 用途 (閾値検知) を前提とし、この一時的退行を許容する契約です

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install natsuume-statusline@natsuume-plugins
```

インストール後、Claude Code 内で次のスラッシュコマンドを実行します。

```
/natsuume-statusline:setup
```

このコマンドは `~/.claude/settings.json` の `statusLine.command` を書き換えます。実行前に既存の `settings.json` 全体をタイムスタンプ付きでバックアップします。

plugin cache 配下から実行された場合は、`~/.claude/natsuume-statusline-entrypoint.sh` という安定した wrapper を設置し、`statusLine.command` はこの wrapper を指します。wrapper は実行時に最新版の `entrypoint.sh` を解決するため、`/plugin update` 後も**再 setup なしで statusline が追従**します。これは plugin cache が version 固有パス (`~/.claude/plugins/cache/<marketplace>/<plugin>/<VERSION>/...`) に展開され、かつ `statusLine.command` では `${CLAUDE_PLUGIN_ROOT}` 等が展開されない ([Claude Code bug #52079](https://github.com/anthropics/claude-code/issues/52079)) ため、version 固有パスを直接焼き込むと update で旧 dir が消えた際に statusline が無言で壊れる問題を避けるためです。(ローカル clone 等の安定パスから実行された場合は wrapper を介さず entrypoint を直接登録します。)

## 機能一覧

### Commands

| コマンド | 説明 |
|---------|------|
| `/natsuume-statusline:setup` | `${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh` を実行し、`~/.claude/settings.json` の `statusLine.command` をプラグインのエントリポイントに登録する |

### スクリプト

| ファイル | 用途 |
|---------|------|
| `scripts/setup.sh` | settings.json のバックアップ作成、安定 wrapper (`~/.claude/natsuume-statusline-entrypoint.sh`) の設置、`statusLine` 設定書き換え |
| `statusline/entrypoint.sh` | Claude Code から呼ばれる入口。同階層の `main.sh` に exec で委譲 |
| `statusline/main.sh` | JSON 入力のパース、各行の組み立て、ターミナル幅へのフィット |
| `statusline/lib.sh` | カラー定数、進捗バー、可視幅計算、所有 GitHub namespace のキャッシュ |
| `statusline/gauges.sh` | ゲージ行 (context 使用量 / レートリミット) の共通レンダラ (`build_context_segment` / `build_ratelimit_segment` / `render_gauge_line`)。2 行目・3 行目はこれを呼ぶ薄い assembler |
| `statusline/line1.sh` | 1 行目 (パス / repo / branch / 変更量 / 未コミット) のレンダラ |
| `statusline/line2.sh` | 2 行目 (モデル名 / context 使用量 / 5h レートリミット) のレンダラ |
| `statusline/line3.sh` | 3 行目 (7d レートリミット / モデル別週次枠) のレンダラ |
| `statusline/weekly-scoped-limits.sh` | モデル別週次枠の cache 読み出し (`read_weekly_scoped_entries`)、background fetch の起動 (`kick_weekly_scoped_refresh`)、OAuth usage API を叩く fetch worker (`--fetch-worker` として自身を直接実行) |
| `statusline/context-cache-dump.sh` | context cache dump (session-handoff plugin 連携) の `dump_context_cache` 関数 |

## アンインストール / 元に戻す

`/natsuume-statusline:setup` 実行時に作られたバックアップで `settings.json` を上書きしてください。

```bash
cp ~/.claude/settings.natsuume-statusline-backup.<timestamp>.json ~/.claude/settings.json
```

`<timestamp>` は setup 実行時のメッセージに表示されます。setup が設置した安定 wrapper (`~/.claude/natsuume-statusline-entrypoint.sh`) は settings.json を元に戻せば参照されなくなるため、残しておいても無害ですが、不要なら削除して構いません。

## 必要な実行環境

- `bash`
- `jq` (setup と main 双方で利用)

オプション (見つからなければ自動的に縮退):

- `git` — 無いとリポジトリ情報セグメント全体がスキップ
- `gh` — 無いと所有 namespace 判定が無効化され `owner/repo` 形式のまま表示
- `tput` または `stty` — 無いと環境変数 `COLUMNS`、最終的に 80 桁にフォールバック
- `python3` (3.7+) — `resets_at` が ISO 8601 形式で渡された場合の epoch 変換 fallback (BSD/macOS の `date` に `-d` が無い環境用)。無いとレートリミットのリセット残時間が空表示
- `curl` — モデル別週次枠 (3 行目) を OAuth usage API から取得するために使用。無いと background fetch が起動せず、3 行目はモデル別週次枠を含まない (7d のみ、または非表示) まま縮退

## 関連情報

- [Claude Code Status Line ドキュメント](https://code.claude.com/docs/en/statusline)

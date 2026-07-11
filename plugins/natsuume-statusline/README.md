# natsuume-statusline プラグイン

Claude Code の `statusLine` 表示 (パス / GitHub repo / branch / 変更量 / context 使用量 / レートリミット) を提供するプラグインです。`/natsuume-statusline:setup` で `~/.claude/settings.json` に登録できます。

## バージョン

v0.6.0

## 表示内容

3 行構成 (内容に応じて省略あり):

1. **1 行目**: カレントパス、GitHub リポジトリ名、ブランチ名、staged/modified 変更量、未コミット件数 (or `clean`)
   - リポジトリの owner が自分または所属 org の場合は `owner/repo` を `repo` に短縮
   - 全体がターミナル幅を超える場合は段階的に prefix → パス短縮の順でフォールバック
2. **2 行目**: context 使用量 (`ctx`) と レートリミット (5h / 7d)
   - **context 使用量**: `ctx: (45%) 75.1k/1M` 形式の数値表示 (バー無し)。使用率 (`context_window.used_percentage`)、使用トークン数 (`total_input_tokens`)、最大コンテキスト長 (`context_window_size`) を併記。取得できない初期/compact 直後は非表示。トークン数が取れない場合は `ctx: (45%)` に縮退
   - **レートリミット**: `5h: 62% (58m) [████░░]` 形式。パーセンテージ、リセット残時間、プログレスバー
   - 使用率の色: 80% 以上で赤、60% 以上で黄、それ未満で緑
   - **横幅に合わせた段階的縮小**: 全内容が収まらない場合、`…` で切り詰める前に情報を保ったまま段階的に簡略化する。優先順位は ⓪ 使用率の小数を四捨五入して整数表示にする (`(45.2%)`→`(45%)`, `62.5%`→`63%`) → ① ctx の使用率 `(P%)` を削除 (使用/最大トークンが残るので情報は保たれる) → ② レートリミットのバー長を短縮 (最大 10 → 最小 3 文字) → ③ バーを削除 (`5h: 62% (58m)` のみ)。横幅に収まる最も豊かな表示を自動選択する。トークン数が取れず `(P%)` が唯一の情報のときは ① をスキップ
3. **3 行目**: 将来拡張用 (現状は空)

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
| `statusline/line1.sh` | 1 行目 (パス / repo / branch / 変更量 / 未コミット) のレンダラ |
| `statusline/line2.sh` | 2 行目 (context 使用量 / レートリミット) のレンダラ |
| `statusline/line3.sh` | 3 行目 (将来拡張用) |
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
- `python3` (3.7+) — `resets_at` が ISO 8601 形式で渡された場合の epoch 変換 fallback (BSD/macOS の `date` に `-d` が無い環境用)。無いと 2 行目のリセット残時間が空表示

## 関連情報

- [Claude Code Status Line ドキュメント](https://code.claude.com/docs/en/statusline)

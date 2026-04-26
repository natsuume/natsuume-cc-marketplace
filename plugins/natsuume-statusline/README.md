# natsuume-statusline プラグイン

Claude Code の `statusLine` 表示 (パス / GitHub repo / branch / 変更量 / レートリミット) を提供するプラグインです。`/natsuume-statusline:setup` で `~/.claude/settings.json` に登録できます。

## バージョン

v0.1.0

## 表示内容

3 行構成 (内容に応じて省略あり):

1. **1 行目**: カレントパス、GitHub リポジトリ名、ブランチ名、staged/modified 変更量、未コミット件数 (or `clean`)
   - リポジトリの owner が自分または所属 org の場合は `owner/repo` を `repo` に短縮
   - 全体がターミナル幅を超える場合は段階的に prefix → パス短縮の順でフォールバック
2. **2 行目**: レートリミット (5h / 7d) のパーセンテージ、リセット残時間、プログレスバー
   - 80% 以上で赤、60% 以上で黄、それ未満で緑
   - バー幅はターミナル幅に合わせて 4〜20 文字で動的調整
3. **3 行目**: 将来拡張用 (現状は空)

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=natsuume-statusline
```

インストール後、Claude Code 内で次のスラッシュコマンドを実行します。

```
/natsuume-statusline:setup
```

このコマンドは `~/.claude/settings.json` の `statusLine.command` をこのプラグインのエントリポイント (`bash <plugin-root>/statusline/entrypoint.sh`) に書き換えます。実行前に既存の `settings.json` 全体をタイムスタンプ付きでバックアップします。

## 機能一覧

### Commands

| コマンド | 説明 |
|---------|------|
| `/natsuume-statusline:setup` | `${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh` を実行し、`~/.claude/settings.json` の `statusLine.command` をプラグインのエントリポイントに登録する |

### スクリプト

| ファイル | 用途 |
|---------|------|
| `scripts/setup.sh` | settings.json のバックアップ作成と `statusLine` 設定書き換え |
| `statusline/entrypoint.sh` | Claude Code から呼ばれる入口。同階層の `main.sh` に exec で委譲 |
| `statusline/main.sh` | JSON 入力のパース、各行の組み立て、ターミナル幅へのフィット |
| `statusline/lib.sh` | カラー定数、進捗バー、可視幅計算、所有 GitHub namespace のキャッシュ |
| `statusline/line1.sh` | 1 行目 (パス / repo / branch / 変更量 / 未コミット) のレンダラ |
| `statusline/line2.sh` | 2 行目 (レートリミット) のレンダラ |
| `statusline/line3.sh` | 3 行目 (将来拡張用) |

## アンインストール / 元に戻す

`/natsuume-statusline:setup` 実行時に作られたバックアップで `settings.json` を上書きしてください。

```bash
cp ~/.claude/settings.natsuume-statusline-backup.<timestamp>.json ~/.claude/settings.json
```

`<timestamp>` は setup 実行時のメッセージに表示されます。

## 必要な実行環境

- `bash`
- `jq` (setup と main 双方で利用)

オプション (見つからなければ自動的に縮退):

- `git` — 無いとリポジトリ情報セグメント全体がスキップ
- `gh` — 無いと所有 namespace 判定が無効化され `owner/repo` 形式のまま表示
- `tput` または `stty` — 無いと環境変数 `COLUMNS`、最終的に 80 桁にフォールバック

## 関連情報

- [Claude Code Status Line ドキュメント](https://docs.anthropic.com/claude-code/statusline)

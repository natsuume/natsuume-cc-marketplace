#!/bin/bash
# setup.sh
# statusline プラグインを ~/.claude/settings.json の statusLine.command に登録する。
# 既存の statusLine 設定 (含 settings.json 全体) はタイムスタンプ付きでバックアップする。

set -euo pipefail

if ! command -v jq >/dev/null 2>&1; then
  echo "[statusline] jq が見つかりません。jq をインストールしてから再実行してください。" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENTRYPOINT="$PLUGIN_ROOT/statusline/entrypoint.sh"

if [ ! -f "$ENTRYPOINT" ]; then
  echo "[statusline] エントリポイントが見つかりません: $ENTRYPOINT" >&2
  exit 1
fi

# Claude Code は実行ビットを保ったまま展開する保証が無いので、ここで明示的に付与する。
chmod +x "$ENTRYPOINT" "$PLUGIN_ROOT/statusline/main.sh" 2>/dev/null || true

SETTINGS_DIR="$HOME/.claude"
SETTINGS="$SETTINGS_DIR/settings.json"
mkdir -p "$SETTINGS_DIR"

# settings.json が存在しない、または空ファイルなら最低限の JSON を作る。
if [ ! -s "$SETTINGS" ]; then
  echo '{}' > "$SETTINGS"
fi

# 中身が壊れた JSON だった場合は backup して空オブジェクトに置き換える前に中断。
# 自動修復はスコープ外 (誤った状態を黙って上書きしない)。
if ! jq empty "$SETTINGS" >/dev/null 2>&1; then
  echo "[statusline] $SETTINGS が壊れています (jq parse 失敗)。手動で修復してから再実行してください。" >&2
  exit 1
fi

# 同秒内に複数回実行された場合に前のバックアップを上書きしないよう、
# 既存と衝突した場合は連番を付けて回避する。
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
BACKUP="$SETTINGS_DIR/settings.statusline-backup.$TIMESTAMP.json"
suffix=0
while [ -e "$BACKUP" ]; do
  suffix=$((suffix + 1))
  BACKUP="$SETTINGS_DIR/settings.statusline-backup.$TIMESTAMP-$suffix.json"
done
cp "$SETTINGS" "$BACKUP"

# プラグインがスペースやシェルメタ文字を含むパスに置かれていても安全に呼び出せるよう、
# エントリポイントを single-quote で囲み、内部の `'` を `'\''` でエスケープしてから組み立てる。
quoted_entrypoint=$(printf '%s' "$ENTRYPOINT" | sed "s/'/'\\\\''/g")
NEW_COMMAND="bash '$quoted_entrypoint'"
# settings.json はトークン等を含む可能性があるため world-readable な temp を作らない。
# また `mktemp` の default (/tmp) はターゲットと別 FS の可能性があり、その場合
# 後段の `mv` が copy+unlink フォールバックで非アトミックになる。同一ディレクトリに
# temp を作って rename(2) 一発で差し替える。
umask 077
TMP=$(mktemp "$SETTINGS.XXXXXX")
trap 'rm -f "$TMP"' EXIT

jq --arg cmd "$NEW_COMMAND" '
  .statusLine = {
    "type": "command",
    "command": $cmd
  }
' "$SETTINGS" > "$TMP"

# 念のため出力 JSON が valid か再検証してから差し替える。
if ! jq empty "$TMP" >/dev/null 2>&1; then
  echo "[statusline] 生成された JSON が不正です。settings.json は変更しません。" >&2
  exit 1
fi

mv "$TMP" "$SETTINGS"
trap - EXIT

cat <<MSG
[statusline] セットアップ完了。

  settings.json   : $SETTINGS
  バックアップ    : $BACKUP
  エントリポイント: $ENTRYPOINT

statusLine.command を更新しました。次回以降のプロンプト更新からプラグイン版の statusline が使われます。

元の設定に戻したい場合は、上記のバックアップで settings.json を上書きしてください:
  cp '$BACKUP' '$SETTINGS'
MSG

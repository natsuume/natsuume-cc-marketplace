#!/bin/bash
# setup.sh — 安定 launcher の設置と settings.json への登録 (/rate-limit:setup から実行)
#
# natsuume-statusline の scripts/setup.sh (issue #51) の launcher パターンを踏襲する:
# statusLine.command には plugin update で消える version 固有 cache パスを焼き込めないため、
# ~/.claude/rate-limit-statusline-launcher.sh (安定パス) を生成して登録する。
#
# 処理手順 (順序が契約。launcher 設置が settings 更新より先):
#   1. ~/.claude/settings.json をタイムスタンプ付きでバックアップ (同秒衝突は連番回避)
#   2. 既存の statusLine.command 文字列を読み取る (statusLine 未設定なら空)
#   3. launcher を atomic 設置 (umask 077、同一ディレクトリ mktemp + mv)。launcher には
#      (a) version dir の親から active version の cache-write-wrapper.sh を解決するロジック
#          (natsuume-statusline wrapper の mtime + semver tie-break 方式を移植。自己完結 sh)
#      (b) 既存 statusLine.command 文字列を base64 (1 行) で損失なく埋め込み、
#          launcher が実行時にデコードして wrapper の第 1 引数として渡す
#          (改行を含むコマンドでも再実行時の 1 行抽出が壊れない形式)
#   4. statusLine を { "type": "command", "command": "bash '<launcher パス>'" } に atomic 更新
#      (更新前後で jq validate)
#
# 境界の挙動 (issue #225):
#   - statusLine.command が既に launcher 絶対パスを指す (exact 判定) → 二重 wrap しない
#     (launcher 本体の最新内容での再生成は行ってよい。このとき launcher に埋め込み済みの
#     内側コマンドは launcher ファイル自身から引き継ぐ — settings.json 側にはもう元の
#     内側コマンドが残っていないため)
#   - statusLine 未設定 → 内側コマンド無しの launcher を設置
#   - settings.json 不在・空 → {} から開始 / parse 不能 → 変更せずエラー終了
#   - plugin が cache 配下でない安定パスにあっても launcher 方式で統一する
#     (内側コマンドの埋め込みが必要なため direct 分岐は持たない。VERSIONS_DIR 配下に
#     X.Y.Z 形式の version dir が無い場合は自然に FALLBACK_WRAPPER — setup 時点の
#     このプラグインの wrapper パス — へ解決されるため、cache 配下か否かで分岐しなくても
#     動作する)
#
# 依存: jq (必須)。

set -euo pipefail

if ! command -v jq >/dev/null 2>&1; then
  echo "[rate-limit] jq が見つかりません。jq をインストールしてから再実行してください。" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WRAPPER_AT_SETUP="$PLUGIN_ROOT/statusline/cache-write-wrapper.sh"

if [ ! -f "$WRAPPER_AT_SETUP" ]; then
  echo "[rate-limit] cache-write-wrapper.sh が見つかりません: $WRAPPER_AT_SETUP" >&2
  exit 1
fi

# Claude Code は実行ビットを保ったまま展開する保証が無いので、ここで明示的に付与する。
chmod +x "$WRAPPER_AT_SETUP" 2>/dev/null || true

SETTINGS_DIR="$HOME/.claude"
SETTINGS="$SETTINGS_DIR/settings.json"
LAUNCHER="$SETTINGS_DIR/rate-limit-statusline-launcher.sh"
mkdir -p "$SETTINGS_DIR"

# settings.json が存在しない、または空ファイルなら最低限の JSON を作る。
if [ ! -s "$SETTINGS" ]; then
  echo '{}' > "$SETTINGS"
fi

# 中身が壊れた JSON だった場合は、誤った状態を黙って上書きしないよう中断する。
if ! jq empty "$SETTINGS" >/dev/null 2>&1; then
  echo "[rate-limit] $SETTINGS が壊れています (jq parse 失敗)。手動で修復してから再実行してください。" >&2
  exit 1
fi

# 同秒内に複数回実行された場合に前のバックアップを上書きしないよう、
# 既存と衝突した場合は連番を付けて回避する。
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
BACKUP="$SETTINGS_DIR/settings.rate-limit-backup.$TIMESTAMP.json"
suffix=0
while [ -e "$BACKUP" ]; do
  suffix=$((suffix + 1))
  BACKUP="$SETTINGS_DIR/settings.rate-limit-backup.$TIMESTAMP-$suffix.json"
done
cp "$SETTINGS" "$BACKUP"

# パスや内側コマンド文字列にシェルメタ文字が含まれていても安全に埋め込めるよう、
# single-quote で囲み、内部の `'` を `'\''` でエスケープしてから組み立てる。
single_quote() { printf '%s' "$1" | sed "s/'/'\\\\''/g"; }

NEW_COMMAND="bash '$(single_quote "$LAUNCHER")'"

# 既存の statusLine.command 文字列を読み取る (statusLine 未設定なら空文字列)。
EXISTING_COMMAND=$(jq -r '.statusLine.command // empty' "$SETTINGS")

# 二重 wrap 判定: 既に settings.json が launcher の絶対パスを exact に指しているなら、
# このセットアップは再実行 (idempotent) であり、settings.json は変更しない。
ALREADY_WRAPPED=0
if [ "$EXISTING_COMMAND" = "$NEW_COMMAND" ]; then
  ALREADY_WRAPPED=1
fi

# launcher に埋め込む「内側コマンド」は base64 の 1 行 (INNER_COMMAND_B64) で保持する。
# 生文字列の single-quote 埋め込みだと、内側コマンドが改行を含む場合に複数行の代入と
# なり、再実行時の 1 行抽出で引用符未終端の launcher を生成してしまう。base64 は
# 改行・引用符を含まない 1 行になるため抽出が構造的に安全で、verbatim 性 (改行含む
# 損失なし保持) は encoding を介して維持される。
#   - 初回セットアップ (ALREADY_WRAPPED=0): settings.json から直接 encode する。
#     shell 変数 (EXISTING_COMMAND) を経由すると command substitution が末尾改行を
#     落とすため、jq -j (raw 出力・改行付与なし) からのパイプで encode する。
#     GNU base64 の 76 桁折返しは tr で除去する (-w0 は GNU 専用のため使わない)
#   - 再実行 (ALREADY_WRAPPED=1): settings.json 側にはもう元の内側コマンドが残っていない
#     (launcher 自身を指しているため)。既存 launcher ファイルの `INNER_COMMAND_B64=...`
#     行をそのまま (デコードせず) 引き継いで再生成する
if [ "$ALREADY_WRAPPED" -eq 1 ]; then
  INNER_B64_LINE=""
  if [ -f "$LAUNCHER" ]; then
    INNER_B64_LINE=$(grep -m1 '^INNER_COMMAND_B64=' "$LAUNCHER" 2>/dev/null || true)
  fi
  if [ -z "$INNER_B64_LINE" ]; then
    INNER_B64_LINE="INNER_COMMAND_B64=''"
  fi
else
  inner_b64=$(jq -j '(.statusLine.command | select(type == "string")) // empty' "$SETTINGS" | base64 | tr -d '\n')
  INNER_B64_LINE="INNER_COMMAND_B64='$inner_b64'"
fi

# --- (1) launcher を settings 更新より先に atomic 設置する。---
# 順序が重要: launcher を先に置くことで、launcher 書き込み失敗時は settings 未変更
# (= 無害) で済む。先に settings を launcher へ向けてから launcher 書き込みに失敗すると、
# settings が存在しない launcher を指したまま残り statusline が無言で壊れる
# (natsuume-statusline #51 と同種の事故になる)。
VERSIONS_DIR="$(dirname "$PLUGIN_ROOT")"
vq_versions=$(single_quote "$VERSIONS_DIR")
vq_fallback=$(single_quote "$WRAPPER_AT_SETUP")

umask 077
LAUNCHER_TMP=$(mktemp "$LAUNCHER.XXXXXX")
trap 'rm -f "$LAUNCHER_TMP"' EXIT
{
  printf '#!/bin/bash\n'
  printf '# Auto-generated by /rate-limit:setup. このファイルにバージョンを焼き込まないこと。\n'
  printf '# settings.json はこの安定パスを指し、本 launcher が実行時に現在 active な version の\n'
  printf '# cache-write-wrapper.sh を解決することで plugin update に追従する (cache パスは\n'
  printf '# version 固有: issue #51 / Claude Code bug #52079 と同じ問題への対処)。\n'
  printf '# INNER_COMMAND_B64 には setup 実行時点の既存 statusLine.command を base64 (1 行) で\n'
  printf '# 損失なく保持し、デコードして wrapper の第 1 引数として渡すことで元の statusline\n'
  printf '# 表示を壊さずに包む。\n'
  printf "VERSIONS_DIR='%s'\n" "$vq_versions"
  printf "FALLBACK_WRAPPER='%s'\n" "$vq_fallback"
  printf '%s\n' "$INNER_B64_LINE"
  cat <<'LAUNCHER_BODY'

# version dir の親 (VERSIONS_DIR) は plugin update を跨いで安定。そこから「現在 active な
# version」の cache-write-wrapper.sh を解決する。active = mtime 最新、同着は最も高い
# semver で tie-break する (plugins update は複数 version dir に同一 mtime を付けることが
# 実測である)。semver は X.Y.Z の数値 3 フィールドのみを対象とし、それ以外の名前の
# サブディレクトリ (プラグインのローカル clone 直下にある他 plugin ディレクトリ等) は
# 純数値チェックで自然に除外されるため、cache 配下か否かで分岐する必要が無い。
# sort は -V 非依存の POSIX numeric field sort で macOS の bash 3.2 / BSD sort でも動作する。
dir_mtime() {
  # GNU / busybox は `stat -c %Y`、BSD / macOS は `stat -f %m`。
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null
}

resolve_wrapper() {
  [ -d "$VERSIONS_DIR" ] || return 1
  best_entry="" best_mtime="" best_v=""
  for d in "$VERSIONS_DIR"/*/; do
    entry="${d}statusline/cache-write-wrapper.sh"
    [ -f "$entry" ] || continue
    v=$(basename "$d")
    case "$v" in
      ''|*[!0-9.]*) continue ;;  # 純数値 X.Y.Z 以外 (プラグイン名ディレクトリ等) はスキップ
    esac
    m=$(dir_mtime "$d")
    [ -n "$m" ] || m=0
    if [ -z "$best_entry" ]; then
      best_entry="$entry"; best_mtime="$m"; best_v="$v"
      continue
    fi
    take=0
    if [ "$m" -gt "$best_mtime" ]; then
      take=1
    elif [ "$m" -eq "$best_mtime" ]; then
      higher=$(printf '%s\n%s\n' "$best_v" "$v" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1)
      [ "$higher" = "$v" ] && [ "$v" != "$best_v" ] && take=1
    fi
    if [ "$take" -eq 1 ]; then
      best_entry="$entry"; best_mtime="$m"; best_v="$v"
    fi
  done
  [ -n "$best_entry" ] || return 1
  printf '%s' "$best_entry"
}

WRAPPER=$(resolve_wrapper)
if [ -z "$WRAPPER" ] || [ ! -f "$WRAPPER" ]; then
  WRAPPER="$FALLBACK_WRAPPER"
fi

# INNER_COMMAND_B64 をデコードして内側コマンドを復元する。
# - GNU (base64 -d) と BSD/macOS (base64 -D) は別々の試行として実行する
#   (1 本のパイプで || 連結すると先の試行が stdin を消費し、後の試行に同じ入力が
#   渡る保証が無いため、試行ごとに printf で入力を再供給する)
# - command substitution は末尾改行を除去するため、番兵 'x' を付けて受けてから外す
#   (末尾改行を含む内側コマンドも verbatim に復元する)
# - 両方失敗した場合は、保存値の破損で既存 statusline を黙って消さないよう非ゼロ終了する
INNER_COMMAND=$(printf '%s' "$INNER_COMMAND_B64" | base64 -d 2>/dev/null && printf 'x') \
  || INNER_COMMAND=$(printf '%s' "$INNER_COMMAND_B64" | base64 -D 2>/dev/null && printf 'x') \
  || { echo "[rate-limit] launcher: INNER_COMMAND_B64 のデコードに失敗しました。/rate-limit:setup を再実行してください。" >&2; exit 1; }
INNER_COMMAND=${INNER_COMMAND%x}

# plugin のアンインストール等で wrapper が完全に消えている (全 version dir も
# fallback も不在) 場合、既存 statusline を巻き添えで壊さない: 付加機能である
# キャッシュ書き出しだけを諦め、内側コマンドへ直接委譲する (stdin は exec を
# 通じてそのまま内側コマンドへ渡る)。内側未設定なら空出力で正常終了する
# (wrapper 経由時の「内側未指定」と同じ挙動)。
if [ ! -f "$WRAPPER" ]; then
  if [ -n "$INNER_COMMAND" ]; then
    exec bash -c "$INNER_COMMAND"
  fi
  exit 0
fi

if [ -n "$INNER_COMMAND" ]; then
  exec bash "$WRAPPER" "$INNER_COMMAND"
else
  exec bash "$WRAPPER"
fi
LAUNCHER_BODY
} > "$LAUNCHER_TMP"
chmod +x "$LAUNCHER_TMP" 2>/dev/null || true
mv "$LAUNCHER_TMP" "$LAUNCHER"
trap - EXIT

# --- (2) 二重 wrap でなければ settings.json の statusLine.command を更新する。---
if [ "$ALREADY_WRAPPED" -eq 0 ]; then
  # settings.json はトークン等を含む可能性があるため world-readable な temp を作らない。
  # 同一ディレクトリに temp を作って rename(2) 一発で差し替える (異なる FS 間だと
  # mv が非 atomic な copy+unlink にフォールバックしうるため)。
  umask 077
  SETTINGS_TMP=$(mktemp "$SETTINGS.XXXXXX")
  trap 'rm -f "$SETTINGS_TMP"' EXIT

  # 既存 statusLine の type / command 以外のキー (padding 等) は保持し、
  # 本プラグインが所有する type と command だけを上書きする。statusLine が
  # オブジェクト以外の非 null 値の場合は jq の加算が失敗して中断する
  # (壊れた設定を黙って上書きしない方針と同じ)。
  jq --arg cmd "$NEW_COMMAND" '
    .statusLine = ((.statusLine // {}) + {
      "type": "command",
      "command": $cmd
    })
  ' "$SETTINGS" > "$SETTINGS_TMP"

  # 念のため出力 JSON が valid か再検証してから差し替える。
  if ! jq empty "$SETTINGS_TMP" >/dev/null 2>&1; then
    echo "[rate-limit] 生成された JSON が不正です。settings.json は変更しません。" >&2
    exit 1
  fi

  mv "$SETTINGS_TMP" "$SETTINGS"
  trap - EXIT
fi

cat <<MSG
[rate-limit] セットアップ完了。

  settings.json   : $SETTINGS
  バックアップ    : $BACKUP
  launcher        : $LAUNCHER
  wrapper (現在)  : $WRAPPER_AT_SETUP
MSG

if [ "$ALREADY_WRAPPED" -eq 1 ]; then
  cat <<MSG

statusLine.command は既に launcher を指していたため settings.json は変更していません
(二重 wrap 防止)。launcher 本体は最新内容に再生成しました。
MSG
else
  cat <<MSG

statusLine.command を launcher 経由に更新しました。既存の statusline コマンド
(未設定の場合は無し) は launcher が内側コマンドとして包み、表示を壊さず引き継ぎます。
launcher が実行時に最新版の cache-write-wrapper.sh を解決するため、/plugin update 後も
再セットアップなしで追従します。
MSG
fi

cat <<MSG

元の設定に戻したい場合は、上記のバックアップで settings.json を上書きしてください:
  cp '$BACKUP' '$SETTINGS'
MSG

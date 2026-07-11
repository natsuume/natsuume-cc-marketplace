#!/bin/bash
# setup-wrapper.sh — session-handoff cache producer の設置スクリプト (/session-handoff:setup から実行)
#
# session-handoff plugin (#228) は context 使用率の cache (natsuume-statusline v0.6.0+ が
# 書き出す ${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<session_id>.json) を advisory 用途で
# 読む consumer である。cache producer が未構成の環境向けに、既存の statusLine.command を
# 壊さず包む安定 launcher (~/.claude/session-handoff-statusline-launcher.sh) を設置する。
#
# natsuume-statusline/scripts/setup.sh (安定 wrapper 設置 + settings.json バックアップの型) と
# rate-limit/scripts/setup.sh (INNER_COMMAND_B64 launcher の型・base64 encode/decode・plugin
# cache 最新版解決) を踏襲する。settings.json に plugin cache の version 固有パスを焼き込まない
# ため、launcher は実行時に plugin cache から session-handoff 最新版の
# scripts/context-cache-dump.sh を解決する (mtime + semver tie-break、同アルゴリズム)。
#
# サブコマンド:
#   inspect               現在の statusLine.command を分類し JSON で報告する (読み取り専用、書き換えなし)
#   install-wrap          settings.json の既存 statusLine.command を安定 launcher で包んで設置する
#                          (settings.json のバックアップ → launcher atomic 設置 → statusLine 書き換え)
#   install-cache-only     内側コマンド無しの launcher (dump 専用) を設置する
#   regenerate-launcher    既存 launcher の WRAPPED_COMMAND_B64 を引き継いで launcher 本体だけを
#                          最新化する (settings.json は変更しない。抽出・検証に失敗した場合は
#                          launcher / settings.json のいずれも変更せず非 0 終了する)
#
# 設置順序 (契約): launcher を settings.json 書き換えより先に atomic 設置する。launcher 設置に
# 失敗すれば settings.json は未変更のまま (無害) で済む。逆順だと settings.json が存在しない
# launcher を指したまま残り statusline が無言で壊れる (natsuume-statusline #51 と同種の事故)。
#
# 環境変数 (テスト用の差し替えフック。本番では未設定):
#   SESSION_HANDOFF_VERSIONS_DIR_OVERRIDE    launcher に埋め込む VERSIONS_DIR (既定: この
#                                             スクリプトの 1 つ上の親ディレクトリ = 実際の
#                                             plugin cache 規約と同じ「marketplace/plugin dir」)
#   SESSION_HANDOFF_FALLBACK_DUMP_OVERRIDE   launcher に埋め込む FALLBACK_DUMP_SCRIPT (既定:
#                                             このスクリプトと同階層の context-cache-dump.sh)
#
# 依存: jq (必須)。

set -euo pipefail

SELF_LAUNCHER_NAME="session-handoff-statusline-launcher.sh"

if ! command -v jq >/dev/null 2>&1; then
  echo "[session-handoff] jq が見つかりません。jq をインストールしてから再実行してください。" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSIONS_DIR_DEFAULT="$(dirname "$PLUGIN_ROOT")"
VERSIONS_DIR="${SESSION_HANDOFF_VERSIONS_DIR_OVERRIDE:-$VERSIONS_DIR_DEFAULT}"
FALLBACK_DUMP_SCRIPT="${SESSION_HANDOFF_FALLBACK_DUMP_OVERRIDE:-$PLUGIN_ROOT/scripts/context-cache-dump.sh}"

SETTINGS_DIR="$HOME/.claude"
SETTINGS="$SETTINGS_DIR/settings.json"
LAUNCHER="$SETTINGS_DIR/$SELF_LAUNCHER_NAME"

# パスや埋め込み文字列にシェルメタ文字が含まれていても安全に埋め込めるよう、
# single-quote で囲み、内部の `'` を `'\''` でエスケープしてから組み立てる。
single_quote() {
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
}

# GNU / busybox は `stat -c %Y`、BSD / macOS は `stat -f %m`。
dir_mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null
}

# $1=version dir の親、$2=各 version dir からの相対パス。mtime 最新 (同着は最も高い semver)
# の $2 を持つ version dir を選び、その絶対パスを出力する。見つからなければ非 0 終了。
resolve_latest_version_file() {
  local versions_dir="$1" rel="$2"
  local best_entry="" best_mtime="" best_v="" d entry v m higher take
  [ -d "$versions_dir" ] || return 1
  for d in "$versions_dir"/*/; do
    entry="${d}${rel}"
    [ -f "$entry" ] || continue
    v=$(basename "$d")
    case "$v" in
      ''|*[!0-9.]*) continue ;; # 純数値 X.Y.Z 以外 (prerelease や他 plugin 名等) はスキップ
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

# command 文字列からシングル/ダブルクォートされたトークンを抽出し、最初に見つかった
# 「既存の読み取り可能な通常ファイル」を候補として返す。見つからなければ非 0 終了。
find_script_candidate() {
  local command_str="$1" combined tok
  combined=$(
    {
      printf '%s' "$command_str" | grep -oE "'[^']*'" | sed "s/^'//;s/'\$//"
      printf '%s' "$command_str" | grep -oE '"[^"]*"' | sed 's/^"//;s/"$//'
    } 2>/dev/null || true
  )
  while IFS= read -r tok; do
    [ -n "$tok" ] || continue
    if [ -f "$tok" ] && [ -r "$tok" ]; then
      printf '%s' "$tok"
      return 0
    fi
  done <<< "$combined"
  return 1
}

# natsuume-statusline (0.6.0+) の cache producer 対応可否をベストエフォートで判定する。
# 出力: "<capable: true|false|unknown>\t<detail>"
check_natsuume_cache_capable() {
  local command_str="$1" candidate dumpfile vdir fallback resolved
  candidate=$(find_script_candidate "$command_str") || candidate=""
  if [ -z "$candidate" ]; then
    printf 'unknown\tcommand からスクリプトパスを抽出できませんでした\n'
    return 0
  fi
  case "$(basename "$candidate")" in
    natsuume-statusline-entrypoint.sh)
      # 安定 wrapper (plugin cache install 由来)。埋め込まれた VERSIONS_DIR /
      # FALLBACK_ENTRYPOINT を読んで同じ mtime+semver tie-break で active entrypoint を
      # 解決する (natsuume-statusline/scripts/setup.sh の WRAPPER_BODY と同じアルゴリズム)。
      vdir=$(grep -m1 "^VERSIONS_DIR='" "$candidate" 2>/dev/null | sed -E "s/^VERSIONS_DIR='(.*)'\$/\\1/") || vdir=""
      fallback=$(grep -m1 "^FALLBACK_ENTRYPOINT='" "$candidate" 2>/dev/null | sed -E "s/^FALLBACK_ENTRYPOINT='(.*)'\$/\\1/") || fallback=""
      resolved=""
      if [ -n "$vdir" ]; then
        resolved=$(resolve_latest_version_file "$vdir" "statusline/entrypoint.sh") || resolved=""
      fi
      [ -n "$resolved" ] || resolved="$fallback"
      if [ -z "$resolved" ] || [ ! -f "$resolved" ]; then
        printf 'unknown\t%s から active entrypoint を解決できませんでした\n' "$candidate"
        return 0
      fi
      dumpfile="$(dirname "$resolved")/context-cache-dump.sh"
      ;;
    entrypoint.sh)
      dumpfile="$(dirname "$candidate")/context-cache-dump.sh"
      ;;
    *)
      printf 'unknown\t%s の形式を判別できませんでした\n' "$candidate"
      return 0
      ;;
  esac
  if [ -f "$dumpfile" ]; then
    printf 'true\t%s\n' "$dumpfile"
  else
    printf 'false\t%s が存在しません (natsuume-statusline 0.6.0 未満の可能性)\n' "$dumpfile"
  fi
}

# 1 段の連鎖検査: command が指す既存 statusline が既に自 launcher を包んでいないか調べる。
# 出力 (1 行、タブ区切り): "<status: clear|detected|ambiguous>\t<detail>"
# (read で複数行を分割取得できない — read は IFS に関わらず改行を 1 レコードの終端として
# 扱うため、2 行目以降は読み捨てられる — ため、check_natsuume_cache_capable と同じ
# 1 行タブ区切り形式に統一する)
chain_check() {
  local command_str="$1" candidate var_name line_count line b64_value decoded decode_failed

  if printf '%s' "$command_str" | grep -qF "$SELF_LAUNCHER_NAME"; then
    printf 'detected\tcommand 文字列自体に自 launcher (%s) への平文参照があります\n' "$SELF_LAUNCHER_NAME"
    return 0
  fi

  candidate=$(find_script_candidate "$command_str") || candidate=""
  if [ -z "$candidate" ]; then
    printf 'clear\tcommand が読み取り可能なスクリプトファイルを指していないため、連鎖は検出されませんでした\n'
    return 0
  fi

  if grep -qF "$SELF_LAUNCHER_NAME" "$candidate" 2>/dev/null; then
    printf 'detected\t%s 内に自 launcher (%s) への平文参照があります\n' "$candidate" "$SELF_LAUNCHER_NAME"
    return 0
  fi

  # 既知形式の base64 代入行 (rate-limit の INNER_COMMAND_B64 / 本 plugin の
  # WRAPPED_COMMAND_B64) を一意抽出して decode し、中に自 launcher 参照が無いか確認する。
  for var_name in INNER_COMMAND_B64 WRAPPED_COMMAND_B64; do
    line_count=$(grep -c "^${var_name}='" "$candidate" 2>/dev/null || true)
    [ "${line_count:-0}" -eq 1 ] || continue
    line=$(grep "^${var_name}='" "$candidate")
    b64_value=$(printf '%s' "$line" | sed -E "s/^${var_name}='(.*)'\$/\\1/")
    if [ -z "$b64_value" ]; then
      continue
    fi
    decode_failed=0
    decoded=$(printf '%s' "$b64_value" | base64 -d 2>/dev/null) || decoded=$(printf '%s' "$b64_value" | base64 -D 2>/dev/null) || decode_failed=1
    if [ "$decode_failed" -eq 1 ]; then
      if [ -f "$LAUNCHER" ]; then
        printf 'ambiguous\t%s の %s を decode できず、自 launcher (%s) が既に存在するため循環リスクがあります\n' "$candidate" "$var_name" "$LAUNCHER"
        return 0
      fi
      continue
    fi
    if [ -n "$decoded" ] && printf '%s' "$decoded" | grep -qF "$SELF_LAUNCHER_NAME"; then
      printf 'detected\t%s の %s (decode 後) に自 launcher (%s) への参照があります\n' "$candidate" "$var_name" "$SELF_LAUNCHER_NAME"
      return 0
    fi
  done

  printf 'clear\t%s を確認しましたが自 launcher への参照は検出されませんでした\n' "$candidate"
  return 0
}

# settings.json をタイムスタンプ付きでバックアップする (同秒衝突は連番回避)。
# バックアップパスを stdout に返す。settings.json が不在/空なら {} から開始し、
# 壊れた JSON (jq parse 失敗) の場合は誤った状態を黙って上書きしないよう中断する。
backup_settings() {
  mkdir -p "$SETTINGS_DIR"
  if [ ! -s "$SETTINGS" ]; then
    echo '{}' > "$SETTINGS"
  fi
  if ! jq empty "$SETTINGS" >/dev/null 2>&1; then
    echo "[session-handoff] $SETTINGS が壊れています (jq parse 失敗)。手動で修復してから再実行してください。" >&2
    exit 1
  fi
  local timestamp backup suffix
  timestamp=$(date +%Y%m%dT%H%M%S)
  backup="$SETTINGS_DIR/settings.session-handoff-backup.$timestamp.json"
  suffix=0
  while [ -e "$backup" ]; do
    suffix=$((suffix + 1))
    backup="$SETTINGS_DIR/settings.session-handoff-backup.$timestamp-$suffix.json"
  done
  cp "$SETTINGS" "$backup"
  printf '%s' "$backup"
}

# statusLine.command を $1 に atomic 更新する (更新前後で jq validate)。
update_settings_command() {
  local new_command="$1" tmp
  umask 077
  tmp=$(mktemp "$SETTINGS.XXXXXX")
  trap 'rm -f "$tmp"' EXIT
  jq --arg cmd "$new_command" '
    .statusLine = ((.statusLine // {}) + {"type": "command", "command": $cmd})
  ' "$SETTINGS" > "$tmp"
  if ! jq empty "$tmp" >/dev/null 2>&1; then
    echo "[session-handoff] 生成された JSON が不正です。settings.json は変更しません。" >&2
    exit 1
  fi
  mv "$tmp" "$SETTINGS"
  trap - EXIT
}

# launcher 本体を atomic 設置する。$1=WRAPPED_COMMAND_B64 に埋め込む値 (空文字列可、cache-only)。
write_launcher() {
  local b64_value="$1" vq_versions vq_fallback tmp
  vq_versions=$(single_quote "$VERSIONS_DIR")
  vq_fallback=$(single_quote "$FALLBACK_DUMP_SCRIPT")

  mkdir -p "$SETTINGS_DIR"
  umask 077
  tmp=$(mktemp "$LAUNCHER.XXXXXX")
  trap 'rm -f "$tmp"' EXIT
  {
    printf '#!/bin/bash\n'
    printf '# Auto-generated by /session-handoff:setup. このファイルにバージョンを焼き込まないこと。\n'
    printf '# settings.json はこの安定パスを指し、本 launcher が実行時に現在 active な version の\n'
    printf '# scripts/context-cache-dump.sh を解決することで plugin update に追従する (cache パスは\n'
    printf '# version 固有: natsuume-statusline issue #51 / Claude Code bug #52079 と同じ問題への対処)。\n'
    printf '# WRAPPED_COMMAND_B64 には setup 実行時点の既存 statusLine.command を base64 (1 行) で\n'
    printf '# 損失なく保持し、デコードして bash -c へ渡すことで元の statusline 表示を壊さずに包む。\n'
    printf '# 空文字列の場合は内側コマンド無し (cache-only): dump のみ行い、表示は何も出力しない。\n'
    printf '#\n'
    printf '# 実行時の再帰ガード (NATSUUME_SESSION_HANDOFF_LAUNCHER_ACTIVE) は、setup skill の連鎖検査\n'
    printf '# をすり抜けた循環構成が実行時に無限再帰しないための安全網 (代替ではない)。\n'
    printf "VERSIONS_DIR='%s'\n" "$vq_versions"
    printf "FALLBACK_DUMP_SCRIPT='%s'\n" "$vq_fallback"
    printf "WRAPPED_COMMAND_B64='%s'\n" "$b64_value"
    cat <<'LAUNCHER_BODY'

set +x

# 固有名の env 再帰ガード: 既にこの launcher 経由で起動中なら、連鎖検査をすり抜けた循環構成が
# 残っていても、何も出力せず正常終了する (無限再帰・ハングの構造的切断)。
if [ "${NATSUUME_SESSION_HANDOFF_LAUNCHER_ACTIVE:-}" = "1" ]; then
  exit 0
fi

# stdin を一度だけ読み切り、dump と内側コマンドへの委譲の両方に再利用する。
STDIN_DATA=$(cat)

# GNU / busybox は `stat -c %Y`、BSD / macOS は `stat -f %m`。
dir_mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null
}

# VERSIONS_DIR の親 (plugin cache の marketplace/plugin dir) は plugin update を跨いで安定。
# そこから「現在 active な version」の scripts/context-cache-dump.sh を解決する。active =
# mtime 最新、同着は最も高い semver で tie-break (plugins update は複数 version dir に同一
# mtime を付けることが実測である)。semver は X.Y.Z の数値 3 フィールドのみを対象とする。
resolve_dump_script() {
  [ -d "$VERSIONS_DIR" ] || return 1
  best_entry="" best_mtime="" best_v=""
  for d in "$VERSIONS_DIR"/*/; do
    entry="${d}scripts/context-cache-dump.sh"
    [ -f "$entry" ] || continue
    v=$(basename "$d")
    case "$v" in
      ''|*[!0-9.]*) continue ;;
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

# --- dump: context cache への書き出し (session-handoff plugin producer) ---
# 失敗しても後段の delegation を止めない (fail-open)。サブシェルに隔離し、失敗を無視する。
(
  DUMP_SCRIPT=$(resolve_dump_script 2>/dev/null) || DUMP_SCRIPT=""
  if [ -z "$DUMP_SCRIPT" ] || [ ! -f "$DUMP_SCRIPT" ]; then
    DUMP_SCRIPT="$FALLBACK_DUMP_SCRIPT"
  fi
  if [ -n "$DUMP_SCRIPT" ] && [ -f "$DUMP_SCRIPT" ] && command -v jq >/dev/null 2>&1; then
    session_id="" used_percentage="" total_input_tokens="" context_window_size=""
    eval "$(printf '%s' "$STDIN_DATA" | jq -r '
      @sh "session_id=\(.session_id // "")",
      @sh "used_percentage=\(.context_window.used_percentage // "")",
      @sh "total_input_tokens=\(.context_window.total_input_tokens // "")",
      @sh "context_window_size=\(.context_window.context_window_size // "")"
    ' 2>/dev/null)" 2>/dev/null
    received_at=$(date +%s 2>/dev/null)
    # shellcheck disable=SC1090
    . "$DUMP_SCRIPT" 2>/dev/null
    if command -v dump_context_cache >/dev/null 2>&1; then
      dump_context_cache "$session_id" "$used_percentage" "$total_input_tokens" "$context_window_size" "$received_at"
    fi
  fi
) 2>/dev/null || true

# --- delegation: 元の statusline コマンドへ委譲 ---
# WRAPPED_COMMAND_B64 が空なら cache-only (内側コマンド無し): 何も出力せず正常終了する。
if [ -n "$WRAPPED_COMMAND_B64" ]; then
  INNER_COMMAND=$(printf '%s' "$WRAPPED_COMMAND_B64" | base64 -d 2>/dev/null && printf 'x') \
    || INNER_COMMAND=$(printf '%s' "$WRAPPED_COMMAND_B64" | base64 -D 2>/dev/null && printf 'x') \
    || exit 0
  INNER_COMMAND=${INNER_COMMAND%x}
  # 内側コマンドの起動時に限り再帰ガードを export する (委譲先が本 launcher 自身を
  # 再度指す循環構成だった場合に、その 1 段先で無出力 exit 0 させるため)。
  export NATSUUME_SESSION_HANDOFF_LAUNCHER_ACTIVE=1
  printf '%s' "$STDIN_DATA" | bash -c "$INNER_COMMAND"
  exit $?
fi

exit 0
LAUNCHER_BODY
  } > "$tmp"
  chmod +x "$tmp" 2>/dev/null || true
  mv "$tmp" "$LAUNCHER"
  trap - EXIT
}

cmd_inspect() {
  local settings_exists="false" statusline_configured="false" command_str=""
  if [ -s "$SETTINGS" ] && jq empty "$SETTINGS" >/dev/null 2>&1; then
    settings_exists="true"
    command_str=$(jq -r '.statusLine.command // empty' "$SETTINGS" 2>/dev/null)
    [ -n "$command_str" ] && statusline_configured="true"
  fi

  local launcher_exists="false"
  [ -f "$LAUNCHER" ] && launcher_exists="true"

  local classification="none" nss_capable="" nss_detail="" chain_status="" chain_detail=""

  if [ "$statusline_configured" = "true" ]; then
    if printf '%s' "$command_str" | grep -qF "$LAUNCHER"; then
      classification="self-launcher"
    elif printf '%s' "$command_str" | grep -qF "natsuume-statusline"; then
      classification="natsuume-statusline"
      IFS=$'\t' read -r nss_capable nss_detail < <(check_natsuume_cache_capable "$command_str")
    else
      classification="other"
      IFS=$'\t' read -r chain_status chain_detail < <(chain_check "$command_str")
    fi
  fi

  # 「現セッションの cache ファイル存在」の近似判定 (branch 1)。setup skill は slash command
  # 実行時に自セッションの session_id を持たないため、正確な一致確認はできない。直近更新の
  # cache entry の有無を「producer が現在稼働中」の代替シグナルとして用いる (best-effort)。
  local uid_num cache_dir="" freshest_age="" cache_active_guess="false"
  uid_num=$(id -u 2>/dev/null || true)
  if [[ "$uid_num" =~ ^[0-9]+$ ]]; then
    cache_dir="${TMPDIR:-/tmp}/natsuume-context-cache-$uid_num"
    if [ -d "$cache_dir" ]; then
      local newest="" f m
      for f in "$cache_dir"/*.json; do
        [ -f "$f" ] || continue
        m=$(dir_mtime "$f")
        [[ "$m" =~ ^[0-9]+$ ]] || continue
        if [ -z "$newest" ] || [ "$m" -gt "$newest" ]; then
          newest="$m"
        fi
      done
      if [ -n "$newest" ]; then
        local now
        now=$(date +%s)
        freshest_age=$((now - newest))
        [ "$freshest_age" -lt 0 ] && freshest_age=0
        [ "$freshest_age" -le 120 ] && cache_active_guess="true"
      fi
    fi
  fi
  local freshest_age_json="null"
  [ -n "$freshest_age" ] && freshest_age_json="$freshest_age"

  jq -n \
    --arg settings_path "$SETTINGS" \
    --argjson settings_exists "$settings_exists" \
    --argjson statusline_configured "$statusline_configured" \
    --arg statusline_command "$command_str" \
    --arg classification "$classification" \
    --arg launcher_path "$LAUNCHER" \
    --argjson launcher_exists "$launcher_exists" \
    --argjson cache_producer_active_guess "$cache_active_guess" \
    --arg cache_dir "$cache_dir" \
    --argjson freshest_cache_age_seconds "$freshest_age_json" \
    --arg natsuume_statusline_cache_capable "$nss_capable" \
    --arg natsuume_statusline_detail "$nss_detail" \
    --arg chain_status "$chain_status" \
    --arg chain_detail "$chain_detail" \
    '{
      settings_path: $settings_path,
      settings_exists: $settings_exists,
      statusline_configured: $statusline_configured,
      statusline_command: $statusline_command,
      classification: $classification,
      launcher_path: $launcher_path,
      launcher_exists: $launcher_exists,
      cache_producer_active_guess: $cache_producer_active_guess,
      cache_dir: $cache_dir,
      freshest_cache_age_seconds: $freshest_cache_age_seconds,
      natsuume_statusline_cache_capable: $natsuume_statusline_cache_capable,
      natsuume_statusline_detail: $natsuume_statusline_detail,
      chain_status: $chain_status,
      chain_detail: $chain_detail
    }'
}

cmd_install_wrap() {
  local backup existing_b64
  backup=$(backup_settings)
  existing_b64=$(jq -j '(.statusLine.command | select(type == "string")) // empty' "$SETTINGS" | base64 | tr -d '\n')
  write_launcher "$existing_b64"
  update_settings_command "bash '$(single_quote "$LAUNCHER")'"
  jq -n --arg backup "$backup" --arg launcher "$LAUNCHER" --arg settings "$SETTINGS" \
    '{status: "installed", mode: "wrap", backup: $backup, launcher: $launcher, settings: $settings}'
}

cmd_install_cache_only() {
  local backup
  backup=$(backup_settings)
  write_launcher ""
  update_settings_command "bash '$(single_quote "$LAUNCHER")'"
  jq -n --arg backup "$backup" --arg launcher "$LAUNCHER" --arg settings "$SETTINGS" \
    '{status: "installed", mode: "cache-only", backup: $backup, launcher: $launcher, settings: $settings}'
}

cmd_regenerate_launcher() {
  if [ ! -f "$LAUNCHER" ]; then
    echo "[session-handoff] launcher が見つかりません: $LAUNCHER 。変更は行いません。" >&2
    exit 1
  fi
  local line_count line b64_value decode_failed decoded
  line_count=$(grep -c "^WRAPPED_COMMAND_B64='" "$LAUNCHER" 2>/dev/null || true)
  if [ "${line_count:-0}" -ne 1 ]; then
    echo "[session-handoff] 既存 launcher から WRAPPED_COMMAND_B64 を一意に抽出できませんでした。launcher / settings.json は変更しません。" >&2
    exit 1
  fi
  line=$(grep "^WRAPPED_COMMAND_B64='" "$LAUNCHER")
  b64_value=$(printf '%s' "$line" | sed -E "s/^WRAPPED_COMMAND_B64='(.*)'\$/\\1/")
  decode_failed=0
  decoded=$(printf '%s' "$b64_value" | base64 -d 2>/dev/null) || decoded=$(printf '%s' "$b64_value" | base64 -D 2>/dev/null) || decode_failed=1
  if [ "$decode_failed" -eq 1 ]; then
    echo "[session-handoff] 既存 launcher の WRAPPED_COMMAND_B64 の検証 (decode) に失敗しました。launcher / settings.json は変更しません。" >&2
    exit 1
  fi
  : "$decoded" # 抽出値の検証のみに使用 (再エンコードはせず原文の b64_value をそのまま引き継ぐ)
  write_launcher "$b64_value"
  jq -n --arg launcher "$LAUNCHER" '{status: "regenerated", mode: "regenerate", launcher: $launcher}'
}

case "${1:-}" in
  inspect) cmd_inspect ;;
  install-wrap) cmd_install_wrap ;;
  install-cache-only) cmd_install_cache_only ;;
  regenerate-launcher) cmd_regenerate_launcher ;;
  *)
    echo "usage: $(basename "$0") {inspect|install-wrap|install-cache-only|regenerate-launcher}" >&2
    exit 2
    ;;
esac

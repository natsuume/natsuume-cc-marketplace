#!/bin/bash
# save-codex-handoff.sh
#
# Codex PreCompact (matcher "auto|manual") で transcript の末尾を別の read-only Codex
# process に要約させ、<git-dir>/session-handoff/pending-*.md へ atomic 保存する。
# Claude Code も PreCompact を持つため hooks.json 自体は共有するが、Codex input schema で必須の
# turn_id (Codex extension) を runtime guard とし、Claude Code では無音 no-op にする。
#
# 安全性・失敗契約:
#   - default disabled。git-dir 内の owner-only regular opt-in marker が exact v1 content / mode
#     0600 の場合だけ transcript を読み nested Codex を起動する
#   - nested Codex は --sandbox read-only / --ephemeral / --disable hooks /
#     --ignore-user-config / --ignore-rules で起動し、専用の空作業ディレクトリから
#     transcript excerpt だけを渡す
#   - transcript は不安定な内部形式として JSON parse せず、未信頼の opaque data として扱う
#   - transcript が上限を超える場合は末尾だけを渡す (既定 524288 bytes)
#   - summary は handoff dir 内の hidden temp に書き、検証後に pending-*.md へ rename する
#   - transcript 欠落・codex 不在/未認証・nested 実行失敗等は systemMessage で警告するが、
#     hook 自体は常に exit 0 (fail-open) とし compaction を止めない
#   - SESSION_HANDOFF_CODEX_PRECOMPACT_ACTIVE と --disable hooks の二重ガードで再帰を防ぐ

emit_warning() {
  message=$1
  if command -v jq >/dev/null 2>&1; then
    jq -n --arg message "$message" '{
      continue: true,
      systemMessage: $message
    }' 2>/dev/null || true
  fi
}

# 将来の設定変更や wrapper 経由で hooks が誤って有効になっても自己再帰しない。
if [ "${SESSION_HANDOFF_CODEX_PRECOMPACT_ACTIVE:-}" = "1" ]; then
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  # 安全な hook JSON を構築できないため、この経路だけは無音 fail-open。
  exit 0
fi

INPUT=$(cat)

{
  IFS= read -r -d '' HOOK_EVENT
  IFS= read -r -d '' TURN_ID
  IFS= read -r -d '' TRIGGER
  IFS= read -r -d '' RAW_SESSION_ID
  IFS= read -r -d '' TRANSCRIPT_PATH
  IFS= read -r -d '' CWD
} < <(
  printf '%s' "$INPUT" | jq -j '
    (.hook_event_name // ""), "\u0000",
    (.turn_id // ""), "\u0000",
    (.trigger // ""), "\u0000",
    (.session_id // ""), "\u0000",
    (.transcript_path // ""), "\u0000",
    (.cwd // ""), "\u0000"
  ' 2>/dev/null
)

# turn_id は Codex hook input の required extension。Claude Code の同名 event では存在しないため、
# hooks.json を共有してもここで無音 no-op にできる。
if [ "$HOOK_EVENT" != "PreCompact" ] || [ -z "$TURN_ID" ]; then
  exit 0
fi

case "$TRIGGER" in
  auto | manual) ;;
  *)
    emit_warning "session-handoff: Codex PreCompact input の trigger を確認できなかったため、handoff 保存をスキップしました。compaction は継続します。"
    exit 0
    ;;
esac

SANITIZED_SESSION_ID=$(printf '%s' "$RAW_SESSION_ID" | tr -cd 'A-Za-z0-9._-')
if [ -z "$SANITIZED_SESSION_ID" ] || [ -z "$CWD" ]; then
  emit_warning "session-handoff: Codex PreCompact input に session_id または cwd が無いため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
fi

SAVE_SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
OPT_IN_LIB="$SAVE_SCRIPT_DIR/../../scripts/lib/codex-summary-opt-in.sh"
if [ -z "$SAVE_SCRIPT_DIR" ] || [ ! -f "$OPT_IN_LIB" ] || [ -L "$OPT_IN_LIB" ]; then
  emit_warning "session-handoff: Codex summary opt-in contract を安全に読み込めないため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
fi
# shellcheck disable=SC1090,SC1091 # runtime-resolved plugin root; file existence/type checked above
source "$OPT_IN_LIB"

GIT_DIR=$(git -C "$CWD" rev-parse --absolute-git-dir 2>/dev/null)
if [ -z "$GIT_DIR" ]; then
  # 既存の threshold/injection hook と同じく非 git workspace は対象外。
  exit 0
fi
if [ -L "$GIT_DIR" ] || [ ! -d "$GIT_DIR" ] || [ ! -O "$GIT_DIR" ]; then
  emit_warning "session-handoff: git-dir が安全な owner directory ではないため、Codex handoff 保存をスキップしました。compaction は継続します。"
  exit 0
fi

HANDOFF_DIR="$GIT_DIR/session-handoff"
OPT_IN_STATE=$(session_handoff_codex_summary_opt_in_state "$HANDOFF_DIR")
case "$OPT_IN_STATE" in
  enabled) ;;
  disabled)
    # Privacy-safe default: opt-in が無ければ transcript を読まず、nested Codex も起動しない。
    exit 0
    ;;
  *)
    emit_warning "session-handoff: Codex summary opt-in marker が安全な exact v1 owner-only regular file ではないため無効です ($OPT_IN_STATE)。setup を再実行してください。compaction は継続します。"
    exit 0
    ;;
esac

# transcript_path は hook の安定 API ではない。内容を parse せず、通常ファイル・所有者・
# 読み取り可能性だけを検査して opaque text として nested Codex に渡す。
if [ -z "$TRANSCRIPT_PATH" ] || [ -L "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ] || [ ! -O "$TRANSCRIPT_PATH" ] || [ ! -r "$TRANSCRIPT_PATH" ]; then
  emit_warning "session-handoff: Codex transcript が無いか安全に読めないため、handoff 保存をスキップしました。transcript_path は不安定な hook interface であり、compaction 自体は継続します。"
  exit 0
fi

TRANSCRIPT_SIZE=$(wc -c < "$TRANSCRIPT_PATH" 2>/dev/null | tr -d '[:space:]')
if ! [[ "$TRANSCRIPT_SIZE" =~ ^[0-9]+$ ]] || [ "$TRANSCRIPT_SIZE" -eq 0 ]; then
  emit_warning "session-handoff: Codex transcript が空またはサイズ判定不能のため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
fi

if ! command -v codex >/dev/null 2>&1; then
  emit_warning "session-handoff: codex CLI が見つからないため、PreCompact handoff を生成できませんでした。compaction は継続します。"
  exit 0
fi

if ! (
  umask 077
  mkdir -p "$HANDOFF_DIR" 2>/dev/null || exit 1
  [ -L "$HANDOFF_DIR" ] && exit 1
  [ -d "$HANDOFF_DIR" ] || exit 1
  [ -O "$HANDOFF_DIR" ] || exit 1
  chmod 700 "$HANDOFF_DIR" 2>/dev/null || exit 1
  probe=$(mktemp "$HANDOFF_DIR/.probe.XXXXXX" 2>/dev/null) || exit 1
  rm -f "$probe" 2>/dev/null || exit 1
) 2>/dev/null; then
  emit_warning "session-handoff: git dir 内の handoff 保存先を安全に準備できなかったため、保存をスキップしました。compaction は継続します。"
  exit 0
fi

WORK_DIR=""
SUMMARY_TMP=""
# trap から間接呼び出しされるため、ShellCheck の到達不能推定をこの関数だけ抑制する。
# shellcheck disable=SC2317
cleanup() {
  if [ -n "$SUMMARY_TMP" ] && [ -e "$SUMMARY_TMP" ]; then
    rm -f "$SUMMARY_TMP" 2>/dev/null || true
  fi
  if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ] && [ ! -L "$WORK_DIR" ]; then
    rm -rf "$WORK_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM

umask 077
# repository の AGENTS.md / .codex/config.toml を nested summarizer に継承させないため、
# working directory は git tree の外に置く。mktemp が作る directory を所有確認・mode 0700 にする。
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/session-handoff-codex.XXXXXX" 2>/dev/null)
SUMMARY_TMP=$(mktemp "$HANDOFF_DIR/.codex-handoff-summary.XXXXXX" 2>/dev/null)
if [ -z "$WORK_DIR" ] || [ -L "$WORK_DIR" ] || [ ! -d "$WORK_DIR" ] || [ ! -O "$WORK_DIR" ] || [ -z "$SUMMARY_TMP" ] || [ -L "$SUMMARY_TMP" ] || [ ! -f "$SUMMARY_TMP" ] || [ ! -O "$SUMMARY_TMP" ]; then
  emit_warning "session-handoff: handoff 生成用の安全な一時領域を作成できなかったため、保存をスキップしました。compaction は継続します。"
  exit 0
fi
chmod 700 "$WORK_DIR" 2>/dev/null || {
  emit_warning "session-handoff: handoff 生成用一時領域の権限を制限できなかったため、保存をスキップしました。compaction は継続します。"
  exit 0
}
chmod 600 "$SUMMARY_TMP" 2>/dev/null || {
  emit_warning "session-handoff: handoff 一時ファイルの権限を制限できなかったため、保存をスキップしました。compaction は継続します。"
  exit 0
}

MAX_BYTES="${SESSION_HANDOFF_CODEX_TRANSCRIPT_MAX_BYTES:-524288}"
if ! [[ "$MAX_BYTES" =~ ^[0-9]+$ ]] || [ "$MAX_BYTES" -lt 65536 ] || [ "$MAX_BYTES" -gt 4194304 ]; then
  MAX_BYTES=524288
fi

TRANSCRIPT_RAW="$WORK_DIR/transcript-tail.raw"
TRANSCRIPT_INPUT="$WORK_DIR/transcript-tail.txt"
TAIL_BYTES=$MAX_BYTES
if [ "$TRANSCRIPT_SIZE" -gt "$MAX_BYTES" ]; then
  # desired window の直前 1 byte も取り、そこが改行なら最初の完全 record を落とさずに済む。
  TAIL_BYTES=$((MAX_BYTES + 1))
fi
if ! tail -c "$TAIL_BYTES" "$TRANSCRIPT_PATH" > "$TRANSCRIPT_RAW" 2>/dev/null; then
  emit_warning "session-handoff: Codex transcript excerpt を準備できなかったため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
fi

# byte 単位 tail が先頭の UTF-8 code point / JSONL record を途中で切る場合に備え、truncated
# excerpt だけは最初の改行までを捨てる。形式自体は parse せず、それ以降を opaque text とする。
if [ "$TRANSCRIPT_SIZE" -gt "$MAX_BYTES" ]; then
  if ! LC_ALL=C sed '1d' "$TRANSCRIPT_RAW" > "$TRANSCRIPT_INPUT" 2>/dev/null; then
    emit_warning "session-handoff: truncated transcript excerpt の境界を安全に整えられなかったため、handoff 保存をスキップしました。compaction は継続します。"
    exit 0
  fi
else
  if ! mv "$TRANSCRIPT_RAW" "$TRANSCRIPT_INPUT" 2>/dev/null; then
    emit_warning "session-handoff: transcript excerpt を準備できなかったため、handoff 保存をスキップしました。compaction は継続します。"
    exit 0
  fi
fi

if [ ! -s "$TRANSCRIPT_INPUT" ]; then
  emit_warning "session-handoff: transcript excerpt に完全な record が無いため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
fi
chmod 600 "$TRANSCRIPT_INPUT" 2>/dev/null || {
  emit_warning "session-handoff: transcript excerpt の権限を制限できなかったため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
}

if [ -e "$TRANSCRIPT_RAW" ] && ! rm -f "$TRANSCRIPT_RAW" 2>/dev/null; then
  emit_warning "session-handoff: transcript の一時コピーを安全に削除できなかったため、handoff 生成をスキップしました。compaction は継続します。"
  exit 0
fi

TRUNCATED=false
if [ "$TRANSCRIPT_SIZE" -gt "$MAX_BYTES" ]; then
  TRUNCATED=true
fi

PROMPT_FILE="$SAVE_SCRIPT_DIR/../prompts/codex-handoff-summary.md"
PROMPT=$(cat "$PROMPT_FILE" 2>/dev/null)
if [ -z "$PROMPT" ]; then
  emit_warning "session-handoff: Codex handoff summary prompt を読み込めなかったため、保存をスキップしました。compaction は継続します。"
  exit 0
fi

PROMPT="$PROMPT

Runtime metadata (trusted; do not invent values beyond this block):
- compaction trigger: $TRIGGER
- session id: $SANITIZED_SESSION_ID
- transcript excerpt is tail-truncated: $TRUNCATED
- transcript bytes available: $TRANSCRIPT_SIZE
- transcript bytes supplied at most: $MAX_BYTES"

# Codex CLI が transcript を instructions として確実に読むよう、static prompt / trusted metadata /
# transcript framing を単一 stdin stream にする。positional prompt は使わず最後の引数を '-' にする。
REQUEST_INPUT="$WORK_DIR/request.txt"
if ! {
  printf '%s\n\n' "$PROMPT"
  printf '%s\n' '<transcript>'
  command cat "$TRANSCRIPT_INPUT"
  printf '\n%s\n' '</transcript>'
} > "$REQUEST_INPUT"; then
  emit_warning "session-handoff: nested codex 用の統合 stdin を構築できなかったため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
fi
chmod 600 "$REQUEST_INPUT" 2>/dev/null || {
  emit_warning "session-handoff: nested codex 用 stdin の権限を制限できなかったため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
}
if ! rm -f "$TRANSCRIPT_INPUT" 2>/dev/null; then
  emit_warning "session-handoff: transcript excerpt の中間ファイルを安全に削除できなかったため、handoff 生成をスキップしました。compaction は継続します。"
  exit 0
fi

# stdin 用 fd を開いてから pathname を unlink する。nested Codex が動いている間に signal/timeout で
# shell が終了しても prompt/transcript request の pathname を一時領域へ残さない。
if ! exec 3< "$REQUEST_INPUT"; then
  emit_warning "session-handoff: 統合 stdin を nested codex に接続できなかったため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
fi
if ! rm -f "$REQUEST_INPUT" 2>/dev/null; then
  exec 3<&-
  emit_warning "session-handoff: nested codex 用 stdin を安全に unlink できなかったため、handoff 生成をスキップしました。compaction は継続します。"
  exit 0
fi

CODEX_STDERR="$WORK_DIR/codex.stderr"
export SESSION_HANDOFF_CODEX_PRECOMPACT_ACTIVE=1

# transcript 準備中に disable / marker 変更が行われた場合、provider 境界を越える直前に再検証する。
OPT_IN_STATE=$(session_handoff_codex_summary_opt_in_state "$HANDOFF_DIR")
if [ "$OPT_IN_STATE" != "enabled" ]; then
  exec 3<&-
  if [ "$OPT_IN_STATE" != "disabled" ]; then
    emit_warning "session-handoff: Codex summary opt-in marker が実行前に安全でない状態へ変化しました ($OPT_IN_STATE)。nested Codex を起動せず compaction を継続します。"
  fi
  exit 0
fi

if ! codex exec \
  --ignore-user-config \
  --ignore-rules \
  --disable hooks \
  --ephemeral \
  --sandbox read-only \
  --skip-git-repo-check \
  --color never \
  --cd "$WORK_DIR" \
  --output-last-message "$SUMMARY_TMP" \
  - \
  <&3 \
  > /dev/null \
  2> "$CODEX_STDERR"; then
  exec 3<&-
  emit_warning "session-handoff: nested codex による handoff 生成に失敗しました。未認証・model/network 設定・CLI 互換性を確認してください。compaction は継続します。"
  exit 0
fi
exec 3<&-

SUMMARY_SIZE=$(wc -c < "$SUMMARY_TMP" 2>/dev/null | tr -d '[:space:]')
if ! [[ "$SUMMARY_SIZE" =~ ^[0-9]+$ ]] || [ "$SUMMARY_SIZE" -eq 0 ] || [ "$SUMMARY_SIZE" -gt 262144 ]; then
  emit_warning "session-handoff: nested codex の summary が空または上限超過だったため、handoff 保存をスキップしました。compaction は継続します。"
  exit 0
fi

for REQUIRED_HEADING in \
  "# Session handoff" \
  "## Objective and intent" \
  "## Completed work" \
  "## Current state and evidence" \
  "## Decisions and constraints" \
  "## Remaining work" \
  "## Risks and verification"; do
  if ! grep -Fqx "$REQUIRED_HEADING" "$SUMMARY_TMP" 2>/dev/null; then
    emit_warning "session-handoff: nested codex の summary が必須 Markdown 構造を満たさなかったため、handoff 保存をスキップしました。compaction は継続します。"
    exit 0
  fi
done

# mktemp の suffix を final name に引き継ぐことで同一 user の並行 PreCompact と衝突しない。
SUMMARY_BASENAME=$(basename "$SUMMARY_TMP")
UNIQUE_SUFFIX=${SUMMARY_BASENAME##*.}
NOW=$(date +%s 2>/dev/null)
if ! [[ "$NOW" =~ ^[0-9]+$ ]] || [ -z "$UNIQUE_SUFFIX" ]; then
  emit_warning "session-handoff: pending handoff の一意な保存名を作れなかったため、保存をスキップしました。compaction は継続します。"
  exit 0
fi

PENDING_PATH="$HANDOFF_DIR/pending-codex-$SANITIZED_SESSION_ID-$NOW-$UNIQUE_SUFFIX.md"
if [ -e "$PENDING_PATH" ] || [ -L "$PENDING_PATH" ]; then
  emit_warning "session-handoff: pending handoff の保存名が衝突したため、上書きせずスキップしました。compaction は継続します。"
  exit 0
fi

chmod 600 "$SUMMARY_TMP" 2>/dev/null || {
  emit_warning "session-handoff: pending handoff の権限を制限できなかったため、保存をスキップしました。compaction は継続します。"
  exit 0
}
if ! mv "$SUMMARY_TMP" "$PENDING_PATH" 2>/dev/null; then
  emit_warning "session-handoff: pending handoff の atomic 保存に失敗しました。compaction は継続します。"
  exit 0
fi
SUMMARY_TMP=""

# PreCompact では追加 context は不要。直後の SessionStart(source=compact) が pending を claim して
# developer context として注入する。成功時は無音 exit 0。
exit 0

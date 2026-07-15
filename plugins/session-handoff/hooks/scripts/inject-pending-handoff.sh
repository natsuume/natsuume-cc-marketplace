#!/bin/bash
# inject-pending-handoff.sh
# SessionStart で発火し、直前セッションが書き残した pending handoff を新セッションへ
# 自動注入する (issue #228)。clear/startup は runtime 間で共有する。resume/compact は
# save-codex-handoff.sh が同じ session_id で生成した pending-codex-* だけを対象にすることで、
# Claude Code の generic pending を再開済み context や compact 時に消費しない。
#
# 手順 (途中の失敗はすべて無音 exit 0。fail-open でセッションを壊さない):
#   1. jq 不在
#   2. hook_event_name が SessionStart でない、source が対象外、または compact なのに session_id がない
#   3. git 管理下でない (cwd から絶対 git-dir が解決できない)
#   4. handoff ディレクトリの検証 (symlink 拒否・ディレクトリ確認・所有確認)
#   5. pending-*.md / consumed-*.md を走査し、各候補ファイルを検証 (symlink 拒否・
#      通常ファイル確認・所有確認) したうえで mtime を取得。30 日超は削除 (best-effort)、
#      24 時間以内の pending のみを新しい順の候補とする
#   6. 候補なしなら無音 exit
#   7. rename-first claim: 新しい順に pending- → consumed- への mv を試み、
#      成功した最初の 1 件だけを採用する (mv の atomic 性で at-most-once を保証)
#   8. 採用した handoff 全文 + preamble + 残り pending の列挙を additionalContext として出力
#   9. 手順 8 の途中で失敗したら、出力前に限り consumed- → pending- へ best-effort で
#      差し戻してから無音終了する

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

{
  IFS= read -r -d '' HOOK_EVENT
  IFS= read -r -d '' SOURCE
  IFS= read -r -d '' RAW_SESSION_ID
  IFS= read -r -d '' CWD
} < <(
  printf '%s' "$INPUT" | jq -j '
    (.hook_event_name // ""), "\u0000",
    (.source // ""), "\u0000",
    (.session_id // ""), "\u0000",
    (.cwd // ""), "\u0000"
  ' 2>/dev/null
)

# 2. clear/startup は新しい context なので既存 pending 全般を対象にする。SessionStart input には
# Codex 固有の runtime marker がないため、resume/compact では source と同じ sanitized
# session_id を filename に持つ Codex producer の pending だけを後段で候補にする。これにより
# Claude の resume が generic pending を重複注入して consumed 化することを防ぐ。
[ "$HOOK_EVENT" = "SessionStart" ] || exit 0

CODEX_SESSION_ID=""
case "$SOURCE" in
  clear | startup) ;;
  resume | compact)
    CODEX_SESSION_ID=$(printf '%s' "$RAW_SESSION_ID" | tr -cd 'A-Za-z0-9._-')
    [ -n "$CODEX_SESSION_ID" ] || exit 0
    ;;
  *) exit 0 ;;
esac

# 3. 非 git プロジェクトは対象外。--absolute-git-dir を使う理由は
# detect-context-threshold.sh と同じ (常に絶対パスで、走査対象と矛盾しない)。
GIT_DIR=$(git -C "$CWD" rev-parse --absolute-git-dir 2>/dev/null)
if [ -z "$GIT_DIR" ]; then
  exit 0
fi

HANDOFF_DIR="$GIT_DIR/session-handoff"

# 4. 走査前に handoff ディレクトリ自体を検証する。存在しない (= 一度も検知していない)
# 場合もここで false になり、通常の「何もしない」経路として扱われる。
if [ -L "$HANDOFF_DIR" ] || [ ! -d "$HANDOFF_DIR" ] || [ ! -O "$HANDOFF_DIR" ]; then
  exit 0
fi

NOW=$(date +%s)
THIRTY_DAYS=$((30 * 24 * 3600))
TWENTY_FOUR_HOURS=$((24 * 3600))

# 5. pending-*.md / consumed-*.md を走査する。各候補は symlink 拒否 → 通常ファイル確認 →
# 所有確認の順で検証してから stat・削除・読み取り・rename の対象にする (検証に落ちた
# ファイルは無音 skip)。mtime は GNU (`stat -c %Y`) / BSD・macOS (`stat -f %m`) の
# 2 段 fallback で取得する。
candidates=()
for f in "$HANDOFF_DIR"/pending-*.md "$HANDOFF_DIR"/consumed-*.md; do
  [ -e "$f" ] || continue
  [ -L "$f" ] && continue
  [ -f "$f" ] || continue
  [ -O "$f" ] || continue

  mtime=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)
  [[ "$mtime" =~ ^[0-9]+$ ]] || continue

  age=$((NOW - mtime))
  if [ "$age" -gt "$THIRTY_DAYS" ]; then
    rm -f "$f" 2>/dev/null
    continue
  fi

  case "$f" in
    */pending-*.md)
      if [ "$SOURCE" = "compact" ] || [ "$SOURCE" = "resume" ]; then
        base=$(basename "$f")
        name_without_ext=${base%.md}
        without_unique_suffix=${name_without_ext%-*}
        timestamp=${without_unique_suffix##*-}
        producer_prefix=${without_unique_suffix%-*}
        [[ "$timestamp" =~ ^[0-9]+$ ]] || continue
        [ "$producer_prefix" = "pending-codex-$CODEX_SESSION_ID" ] || continue
      fi
      if [ "$age" -le "$TWENTY_FOUR_HOURS" ]; then
        # handoff filename は producer が sanitized/固定文字だけで生成する。repository の絶対
        # path は改行を含みうるため、newline sort には安全な basename だけを渡し、claim 時に
        # HANDOFF_DIR と再結合する。
        candidates+=("$mtime|$(basename "$f")")
      fi
      ;;
  esac
done

# 6. 24 時間以内の pending が 1 件もなければ何もしない。
if [ "${#candidates[@]}" -eq 0 ]; then
  exit 0
fi

# 新しい順 (mtime 降順) に並べ替える。mapfile/readarray は bash 4+ 限定のため
# (macOS の既定 bash は 3.2)、while-read + process substitution で bash 3.2 互換に保つ。
sorted_candidates=()
while IFS= read -r line; do
  sorted_candidates+=("$line")
done < <(printf '%s\n' "${candidates[@]}" | sort -t'|' -k1,1nr)

# 7. rename-first claim: 新しい順に pending- → consumed- への mv を試みる。mv は同一
# ファイルシステム内では atomic rename であり、二重セッションが同じ候補に対して
# 同時に mv しても成功できるのは 1 プロセスだけである (敗者は source 消失により
# 失敗する)。失敗したエントリは既に他セッションに claim されたとみなし、
# 「残り pending」の列挙対象にも含めない (このプロセスにとってはもう pending ではない)。
claimed_path=""
consumed_path=""
remaining_paths=()

for entry in "${sorted_candidates[@]}"; do
  path="$HANDOFF_DIR/${entry#*|}"

  if [ -n "$claimed_path" ]; then
    remaining_paths+=("$path")
    continue
  fi

  base=$(basename "$path")
  candidate_consumed="$HANDOFF_DIR/consumed-${base#pending-}"

  if mv "$path" "$candidate_consumed" 2>/dev/null; then
    claimed_path="$path"
    consumed_path="$candidate_consumed"
  fi
done

if [ -z "$claimed_path" ]; then
  exit 0
fi

# 8. 採用した handoff を読み、preamble + handoff 全文 + 残り pending の列挙を連結する。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
PREAMBLE=$(cat "$PROMPTS_DIR/inject-preamble.md" 2>/dev/null)
if [ -z "$PREAMBLE" ]; then
  # 9. 出力前の失敗。best-effort で pending に差し戻してから無音終了する。
  mv "$consumed_path" "$claimed_path" 2>/dev/null
  exit 0
fi

HANDOFF_BODY=$(cat "$consumed_path" 2>/dev/null)
if [ -z "$HANDOFF_BODY" ]; then
  mv "$consumed_path" "$claimed_path" 2>/dev/null
  exit 0
fi

CONTEXT="$PREAMBLE

$HANDOFF_BODY"

if [ "${#remaining_paths[@]}" -gt 0 ]; then
  LISTING="他にも直近の handoff が残っています:"
  for p in "${remaining_paths[@]}"; do
    LISTING="$LISTING
- $p"
  done
  CONTEXT="$CONTEXT

$LISTING"
fi

CONTEXT_JSON=$(jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}' 2>/dev/null)
if [ -z "$CONTEXT_JSON" ]; then
  mv "$consumed_path" "$claimed_path" 2>/dev/null
  exit 0
fi

printf '%s\n' "$CONTEXT_JSON"

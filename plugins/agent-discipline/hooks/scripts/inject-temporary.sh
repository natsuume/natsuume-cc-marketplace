#!/bin/bash
# inject-temporary.sh
# SessionStart と UserPromptSubmit で「暫定ルール (temporary rules)」を注入する。
#
# 暫定ルールとは、Claude Code 本体や外部ツールの問題が修正されるまでの間だけ配送したい
# 一時的な作業規律であり、恒久規律 (always-*.md / discipline-*.md) と違い
# 「問題修正後にいつでも外せること」を第一要件とする。そのため本スクリプトは
# inject-always.sh から独立した配送経路を持ち、以下の撤去手順を成立させる:
#
#   - 個別撤去: hooks/prompts/temporary/ 配下の対象 md を削除するだけで注入が消える
#     (スクリプトと hooks.json entry は残っても no-op で無害)
#   - 完全撤去: hooks.json の entry・本スクリプト・temporary ディレクトリを削除する
#   - いずれの場合も plugin version bump は必要 (プロジェクト CLAUDE.md の bump 規約)
#
# ## 注入仕様
#
# - SessionStart: hooks/prompts/temporary/*.md を従来どおり全件配送し、同じ session の
#   UserPromptSubmit では再送しないよう配送済み集合を記録する
# - UserPromptSubmit: SessionStart 後に追加された未配送 md だけをファイル名の辞書順
#   (LC_ALL=C で固定) に連結し、1 つの additionalContext として one-shot 配送する
# - 配送済み単位はファイル名の POSIX cksum (CRC + byte length)。本文変更ではなく
#   temporary md の追加・削除を lifecycle とする既存の撤去契約に合わせる
# - モデル判定・permission_mode 判定は行わない (暫定ルールはモデルに依らず全セッション共通)。
#   inject-always.sh とは別 hook entry = 別メッセージとして注入されるため、
#   inject-always.sh 側の self-gate 射程 (「見出し〜メッセージ末尾」) には影響しない
# - agent_id 付き UserPromptSubmit は subagent 経路として無音終了する
#
# ## 出力 JSON 形状 (inject-always.sh と同形)
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "<temporary/*.md の連結本文>"
#     }
#   }
#
# ## fail-open 条件 (いずれも無音終了 exit 0、出力なし)
#
# - jq 不在
# - stdin が不正 JSON / hook_event_name が空 / 未対応 event
# - UserPromptSubmit で session_id が空、または sanitize 後に空
# - state directory / 配送済み集合の atomic 書込に失敗
# - hooks/prompts/temporary/ ディレクトリ不在
# - temporary/*.md が 0 件、連結結果が空、または全件配送済み
#
# SessionStart の session_id 欠落時だけは v0.12.0 からの既存配送を維持するため、marker
# 無しで全件を注入する。正常な hook input では session_id があり、atomic marker 書込成功後に
# のみ出力する。設計経緯: PR #218、issue #237 を参照。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# モデル判定は行わない — 暫定ルールは全セッション共通。
# 不正 JSON 時の jq の parse error は inject-always.sh と同じ理由で抑制する。
{ read -r HOOK_EVENT; read -r SESSION_ID; read -r HAS_AGENT_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.session_id // ""),
    (if has("agent_id") then "1" else "0" end)
  ' 2>/dev/null
)
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

case "$HOOK_EVENT" in
  SessionStart)
    ;;
  UserPromptSubmit)
    if [ "$HAS_AGENT_ID" = "1" ] || [ -z "$SESSION_ID" ]; then
      exit 0
    fi
    ;;
  *)
    exit 0
    ;;
esac

PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
TEMPORARY_DIR="$PROMPTS_DIR/temporary"
if [ ! -d "$TEMPORARY_DIR" ]; then
  exit 0
fi

# glob 展開順をロケール非依存のバイト順に固定する (連結順の決定論性)。
export LC_ALL=C

CONTEXT=""
MARKER_ENABLED=0
if [ -n "$SESSION_ID" ]; then
  SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
  if [ -z "$SAFE_SESSION_ID" ]; then
    # UserPromptSubmit は marker 無しで配送すると再送を防げないため無音終了する。
    # SessionStart は従来の全件配送を維持し、marker だけを省略する。
    if [ "$HOOK_EVENT" = "UserPromptSubmit" ]; then
      exit 0
    fi
  else
    MARKER_ENABLED=1
  fi
fi

if [ "$MARKER_ENABLED" -eq 1 ]; then
  STATE_DIR="${TMPDIR:-/tmp}/agent-discipline-state"
  DELIVERED_FILE="$STATE_DIR/delivered-temporary-$SAFE_SESSION_ID"
  NEXT_DELIVERED_FILE="$DELIVERED_FILE.tmp.$$"
  umask 077

  if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
    exit 0
  fi

  if [ "$HOOK_EVENT" = "UserPromptSubmit" ] && [ -f "$DELIVERED_FILE" ]; then
    if ! cp "$DELIVERED_FILE" "$NEXT_DELIVERED_FILE" 2>/dev/null; then
      exit 0
    fi
  elif ! : 2>/dev/null > "$NEXT_DELIVERED_FILE"; then
    exit 0
  fi
fi

for f in "$TEMPORARY_DIR"/*.md; do
  [ -f "$f" ] || continue
  BODY=$(cat "$f" 2>/dev/null)
  [ -n "$BODY" ] || continue

  if [ "$MARKER_ENABLED" -eq 1 ]; then
    FILE_NAME=${f##*/}
    CHECKSUM=$(printf '%s' "$FILE_NAME" | cksum 2>/dev/null)
    [ -n "$CHECKSUM" ] || {
      rm -f "$NEXT_DELIVERED_FILE" 2>/dev/null
      exit 0
    }
    FILE_KEY="${CHECKSUM%% *}:${CHECKSUM##* }"

    if [ "$HOOK_EVENT" = "UserPromptSubmit" ] \
      && [ -f "$DELIVERED_FILE" ] \
      && grep -Fqx "$FILE_KEY" "$DELIVERED_FILE" 2>/dev/null; then
      continue
    fi

    if ! printf '%s\n' "$FILE_KEY" 2>/dev/null >> "$NEXT_DELIVERED_FILE"; then
      rm -f "$NEXT_DELIVERED_FILE" 2>/dev/null
      exit 0
    fi
  fi

  if [ -n "$CONTEXT" ]; then
    CONTEXT="$CONTEXT

$BODY"
  else
    CONTEXT="$BODY"
  fi
done

# JSON は marker より先に生成して、serialization 失敗時に未配送ファイルを配送済み扱い
# しない。SessionStart の temporary directory が空の場合は空集合の marker だけを atomic
# 更新し、同名ファイルが後から追加されたときに UserPromptSubmit で配送できるようにする。
OUTPUT=""
if [ -n "$CONTEXT" ]; then
  OUTPUT=$(jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
    hookSpecificOutput: {
      hookEventName: $evt,
      additionalContext: $ctx
    }
  }' 2>/dev/null) || {
    if [ "$MARKER_ENABLED" -eq 1 ]; then
      rm -f "$NEXT_DELIVERED_FILE" 2>/dev/null
    fi
    exit 0
  }
fi

if [ "$MARKER_ENABLED" -eq 1 ]; then
  if [ "$HOOK_EVENT" = "UserPromptSubmit" ] && [ -z "$CONTEXT" ]; then
    rm -f "$NEXT_DELIVERED_FILE" 2>/dev/null
    exit 0
  fi
  if ! mv "$NEXT_DELIVERED_FILE" "$DELIVERED_FILE" 2>/dev/null; then
    rm -f "$NEXT_DELIVERED_FILE" 2>/dev/null
    exit 0
  fi
fi

[ -n "$OUTPUT" ] || exit 0
printf '%s\n' "$OUTPUT"

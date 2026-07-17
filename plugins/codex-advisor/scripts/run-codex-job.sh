#!/bin/bash
# Codex runner subagent 専用 companion adapter (issue #291)。
#
# prompt 本文を shell command / argv に載せず、rescue / advisor は detached companion
# job を開始して job ID を即時に返す。review は official companion の foreground 契約を
# 維持する。status / result / cancel / snapshot は追跡を失った runner が永続 job state から
# 復旧するための管理操作である。
#
# Linux (WSL2) と macOS の system bash 3.2 の両方で動作させるため、連想配列、mapfile、
# GNU 専用 option は使わない。

set -e

_CODEX_JOB_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# install root is resolved at runtime.
# shellcheck disable=SC1091
source "$_CODEX_JOB_SCRIPT_DIR/lib/codex-companion-resolver.sh"

fail() {
  printf '%s\n' "[run-codex-job] $1" >&2
  exit 1
}

usage() {
  printf '%s\n' 'usage:' >&2
  printf '%s\n' '  run-codex-job.sh rescue PROMPT_FILE (--fresh|--resume) [--write] [--model MODEL] [--effort EFFORT]' >&2
  printf '%s\n' '  run-codex-job.sh advisor PROMPT_FILE' >&2
  printf '%s\n' '  run-codex-job.sh review [--scope auto|working-tree|branch] [--base REF] [--adversarial] [--focus-file FILE]' >&2
  printf '%s\n' '  run-codex-job.sh snapshot' >&2
  printf '%s\n' '  run-codex-job.sh status JOB_ID [--wait] [--timeout-ms MILLISECONDS]' >&2
  printf '%s\n' '  run-codex-job.sh result JOB_ID' >&2
  printf '%s\n' '  run-codex-job.sh cancel JOB_ID' >&2
}

validate_prompt_file() {
  [ -f "$1" ] || fail "prompt file が regular file として存在しません。"
  [ -s "$1" ] || fail "prompt file が空です。"
}

resolve_companion_or_fail() {
  command -v node >/dev/null 2>&1 || fail "Node.js が見つかりません。"
  COMPANION=$(resolve_codex_companion) \
    || fail "codex companion が見つかりません。/codex:setup で install と認証を確認してください。"
}

[ "$#" -ge 1 ] || {
  usage
  exit 1
}

MODE=$1
shift
resolve_companion_or_fail

case "$MODE" in
  rescue)
    [ "$#" -ge 2 ] || {
      usage
      exit 1
    }
    PROMPT_FILE=$1
    THREAD_MODE=$2
    shift 2
    validate_prompt_file "$PROMPT_FILE"
    case "$THREAD_MODE" in
      --fresh|--resume) ;;
      *) fail "rescue には --fresh または --resume を明示してください。" ;;
    esac

    # Bash 3.2 互換の positional parameter 構築。任意 option は allow-list し、prompt
    # file 以外の自由文が argv に混入する経路を作らない。
    RESCUE_ARGS=("$@")
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --write)
          shift ;;
        --model|--effort)
          OPTION=$1
          [ "$#" -ge 2 ] || fail "$OPTION に値がありません。"
          case "$2" in
            ''|--*) fail "$OPTION に不正な値が指定されました。" ;;
          esac
          shift 2 ;;
        *) fail "rescue の未知 option: $1" ;;
      esac
    done
    exec node "$COMPANION" task --background --json --prompt-file "$PROMPT_FILE" "$THREAD_MODE" "${RESCUE_ARGS[@]}"
    ;;

  advisor)
    [ "$#" -eq 1 ] || {
      usage
      exit 1
    }
    validate_prompt_file "$1"
    exec node "$COMPANION" task --background --json --fresh --effort xhigh --prompt-file "$1"
    ;;

  review)
    ADVERSARIAL=0
    FOCUS_FILE=""
    set -- "$@"
    REVIEW_ARGS=()
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --adversarial)
          ADVERSARIAL=1
          shift ;;
        --focus-file)
          [ "$#" -ge 2 ] || fail "--focus-file に値がありません。"
          validate_prompt_file "$2"
          FOCUS_FILE=$2
          shift 2 ;;
        --scope)
          [ "$#" -ge 2 ] || fail "--scope に値がありません。"
          case "$2" in
            auto|working-tree|branch) ;;
            *) fail "--scope は auto、working-tree、branch のいずれかです。" ;;
          esac
          REVIEW_ARGS+=(--scope "$2")
          shift 2 ;;
        --base)
          [ "$#" -ge 2 ] || fail "$1 に値がありません。"
          case "$2" in
            ''|--*) fail "$1 に不正な値が指定されました。" ;;
          esac
          # ref / SHA は shell で再評価せず positional parameter に戻すため、空白や shell
          # metacharacter を拒否する。通常の git ref / OID はこの集合内で表現できる。
          case "$2" in
            *[!A-Za-z0-9._/-]*) fail "$1 の値に不正な文字が含まれています。" ;;
          esac
          REVIEW_ARGS+=("$1" "$2")
          shift 2 ;;
        *) fail "review の未知 option: $1" ;;
      esac
    done
    if [ "$ADVERSARIAL" -eq 1 ]; then
      if [ -n "$FOCUS_FILE" ]; then
        FOCUS_TEXT=$(cat "$FOCUS_FILE")
        [ -n "$(printf '%s' "$FOCUS_TEXT" | tr -d '[:space:]')" ] \
          || fail "focus file が空白だけです。"
        exec node "$COMPANION" adversarial-review --wait "${REVIEW_ARGS[@]}" "$FOCUS_TEXT"
      fi
      exec node "$COMPANION" adversarial-review --wait "${REVIEW_ARGS[@]}"
    fi
    [ -z "$FOCUS_FILE" ] || fail "native review は focus text を受け付けません。--adversarial を指定してください。"
    exec node "$COMPANION" review --wait "${REVIEW_ARGS[@]}"
    ;;

  snapshot)
    [ "$#" -eq 0 ] || {
      usage
      exit 1
    }
    exec node "$COMPANION" status --all --json
    ;;

  status)
    [ "$#" -ge 1 ] || {
      usage
      exit 1
    }
    JOB_ID=$1
    shift
    case "$JOB_ID" in
      ''|*[!A-Za-z0-9._:-]*) fail "job ID に不正な文字が含まれています。" ;;
    esac
    WAIT_FOR_TERMINAL=0
    TIMEOUT_MS=240000
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --wait)
          WAIT_FOR_TERMINAL=1
          shift ;;
        --timeout-ms)
          [ "$#" -ge 2 ] || fail "--timeout-ms に値がありません。"
          case "$2" in
            ''|*[!0-9]*) fail "--timeout-ms は 0 以上の整数です。" ;;
          esac
          TIMEOUT_MS=$2
          shift 2 ;;
        *) fail "status の未知 option: $1" ;;
      esac
    done
    # companion v1.0.6 の status は単発取得だけを提供するため、--wait は本 plugin の短い
    # poll adapter で構成する。job state 自体は companion の公開 status 契約だけを読む。
    if [ "$WAIT_FOR_TERMINAL" -eq 1 ]; then
      exec bash "$_CODEX_JOB_SCRIPT_DIR/poll-codex-job.sh" "$COMPANION" "$JOB_ID" "$TIMEOUT_MS"
    fi
    # status management は JSON 固定。runner が terminal state と job ID を混同せず読める。
    exec node "$COMPANION" status "$JOB_ID" --json
    ;;

  result|cancel)
    [ "$#" -eq 1 ] || {
      usage
      exit 1
    }
    case "$1" in
      ''|*[!A-Za-z0-9._:-]*) fail "job ID に不正な文字が含まれています。" ;;
    esac
    exec node "$COMPANION" "$MODE" "$1"
    ;;

  *)
    usage
    fail "未知 mode: $MODE"
    ;;
esac

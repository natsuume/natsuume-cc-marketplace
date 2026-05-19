#!/bin/bash
# codex-review-detect.sh
# codex companion の `review` サブコマンド起動を検知する共通ユーティリティ。
#
# auto-mark.sh (PostToolUse) は「marker を書く対象」 として、
# block-bg-codex-review.sh (PreToolUse) は「background 起動を block する対象」 として、
# 同じコマンドを判定する。 両者の regex が drift すると整合性が崩れ、
# 「marker 書き込みは走るのに block は走らない」 等の片肺状態が発生する。 ここで共通
# 関数として一本化することで構造的に drift を防ぐ。
#
# 検知条件:
#   1. コマンド先頭が `node` (env-prefix `FOO=bar` 0 回以上を許容)
#      - 先頭 `node` 制約により、 `echo codex-companion.mjs review` のような偶発的
#        substring 一致を排除する
#   2. companion path に `review` サブコマンドが続く
#      - `.mjs` / `.mts` 両拡張子を許容、 closing quote `"` のオプション許容
#
# bash 内蔵 `[[ =~ ]]` を使うことで subprocess fork を回避する (全 Bash 呼び出しで
# 走る hot path のため、 grep 実装と比較して fork コスト分高速)。

_CODEX_REVIEW_NODE_RE='^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*node([[:space:]]|$)'
_CODEX_REVIEW_PATH_RE='codex-companion\.m[jt]s"?[[:space:]]+review([[:space:]]|$)'

is_codex_review_invocation() {
  local cmd="$1"
  [[ "$cmd" =~ $_CODEX_REVIEW_NODE_RE ]] || return 1
  [[ "$cmd" =~ $_CODEX_REVIEW_PATH_RE ]] || return 1
  return 0
}

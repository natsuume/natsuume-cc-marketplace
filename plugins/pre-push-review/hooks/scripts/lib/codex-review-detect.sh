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

# 行継続 `\<改行>` は実行時に bash がバックスラッシュ + 改行を消して隣接トークンに連結
# する (例: `--back\<newline>ground` は実行時 `--background` となる)。 hook 側の regex は
# contiguous な token を要求するため、 normalize しないと line continuation で flag 検知を
# bypass される (例: `--background` が hook に見えず deny を素通りする経路)。
#
# **削除 (空文字置換) を行う**: bash の実挙動と一致させるため、 `\<newline>` を **空文字** に
# 置換して隣接 token を連結する。 block-pre-push.sh は同じ `\<newline>` を **空白** に置換
# しているが、 そちらは normalize 後に `cmd-parser.sh` で tokenize する設計のためトークン
# 区切りを残すのが正しい。 本関数の caller (block-bg-codex-review.sh / auto-mark.sh) は
# normalize 後に **regex match** するため、 bash 実挙動と合わせて連結 (= 削除) する必要が
# ある。 用途が異なるため、 同じ「行継続正規化」 でも実装が分かれる。
#
# 対象は line continuation のみ。 ANSI-C quoting / heredoc / 引用符内改行など bash の他構文は
# 本関数で正規化しないが、 codex companion の argv 分割で flag 名を隠せる現実的経路は line
# continuation が主対象のため、 残余リスクは限定的。
normalize_line_continuations() {
  printf '%s' "${1//$'\\\n'/}"
}

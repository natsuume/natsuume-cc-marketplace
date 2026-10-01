#!/bin/sh
# prompt ファイルを配送用の本文として読む helper。
#
# read_ui_discipline_prompt <file>
#   file の内容を標準出力へ書く。ファイルの 1 行目が `<!--` で始まり、その後ろに `-->` が
#   ある場合は、先頭の HTML コメント (保守者向けメモ) と、その直後に続く空行を除いて書く。
#   - 1 行目が `<!--` で始まらないファイルは、そのまま書く
#   - 先頭のコメントが閉じていない (`-->` が無い) ファイルは、そのまま書く
#   - 1 行目が rule マーカー (`<!-- rule:` / `<!-- subagent-rule:`) のファイルは、そのまま書く
#   - 先頭のコメントより後ろにある HTML コメント (rule マーカーを含む) は残す
#   - 先頭の空行を除くほかは、本文を変えない
#   ファイルが読めない場合は何も書かずに 1 を返す。呼び出し側は `$(...)` で受け取る
#   (末尾の改行はコマンド置換で落ちる。`$(cat <file>)` と同じ扱い)。
#
# 除き方は agent-discipline の hooks/scripts/lib/prompt-body.sh (read_agent_discipline_prompt)
# と同じ規則にする。両者が同じ出力を返すことはテストで検査する。
#
# POSIX sh と POSIX awk だけを使い、bash 3.2 (macOS)・dash・BSD awk・GNU awk で動かす。

read_ui_discipline_prompt() {
  # 未実装。上記の規則で本文を書く処理に置き換える。
  return 1
}

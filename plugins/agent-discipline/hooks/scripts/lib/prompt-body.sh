#!/bin/sh
# prompt ファイルを配送用の本文として読む helper。
#
# read_agent_discipline_prompt <file>
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
# POSIX sh と POSIX awk だけを使い、bash 3.2 (macOS)・dash・BSD awk・GNU awk で動かす。

read_agent_discipline_prompt() {
  _agent_discipline_prompt_file=$1

  [ -r "$_agent_discipline_prompt_file" ] || return 1

  awk '
    { lines[NR] = $0 }
    END {
      start = 1
      if (NR > 0 && substr(lines[1], 1, 4) == "<!--" && lines[1] !~ /^<!-- (rule|subagent-rule):/) {
        close_line = 0
        rest = ""
        for (i = 1; i <= NR; i++) {
          text = (i == 1) ? substr(lines[i], 5) : lines[i]
          pos = index(text, "-->")
          if (pos > 0) {
            close_line = i
            rest = substr(text, pos + 3)
            break
          }
        }
        if (close_line > 0) {
          if (rest ~ /^[ \t]*$/) {
            start = close_line + 1
          } else {
            lines[close_line] = rest
            start = close_line
          }
          while (start <= NR && lines[start] ~ /^[ \t]*$/) {
            start++
          }
        }
      }
      for (i = start; i <= NR; i++) {
        print lines[i]
      }
    }
  ' "$_agent_discipline_prompt_file" 2>/dev/null
}

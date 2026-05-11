#!/bin/bash
# cmd-parser.sh
# Bash command parser for pre-push-review.
#
# 設計意図: 従来の regex ベース「危険プレフィクスを deny で列挙」方式は、新しい bypass
# パターンが見つかるたびに deny を追加する負け戦になっていた。本ライブラリはコマンドを
# segment 単位に正確に分割 + トークン化することで、後段の resolver が「実 target を決定的に
# 取り出す」positive list 設計を取れるようにする。
#
# 提供する関数:
#   - split_command  : コマンドを top-level separator (`&&` / `||` / `;` / `|` / `&`) で
#                      segment 分割する。 quotes / escapes / 括弧の depth を尊重する。
#   - tokenize_segment : 1 segment をトークン (空白区切り) に分割する。 quotes 内の空白は
#                      区切らない。
#   - unquote_token  : トークン両端の quote ペアを 1 段剥がす。
#
# limitations (cooperative 利用前提):
#   - heredoc (`<<EOF ... EOF`) の body 内 quote toggle は素朴に追跡してしまう。 cooperative
#     利用で問題は出にくい (重要判定は最終的に `bash -n` のような構文チェックでなく resolver
#     の positive 判定で行うため)。
#   - **command substitution `$(...)` / process substitution `<(...)` `>(...)` /
#     backtick `` `...` `` の内部は parser から隠蔽される経路**。 これらの置換内に
#     `git push` がある形式は block-pre-push.sh の事前 shape チェックで substring 検出して
#     **保守的に deny** する (内部の cwd / push を本 parser では解析しないため)。 paren-based
#     置換 `$(` `<(` `>(` は paren_depth に算入されて `;` 区切りが発火しないこともあり、
#     内部の `git push` が segment に巻き込まれて push 検出が token level で失敗する経路が
#     あった。 それを shape check で塞ぐ。 backtick は depth tracking していない。

# split_command <cmd>
# stdout: 行ごとに segment を出力。 segment 間には `SEP:<separator>` 行を挟む。
#   例: "cd a && git push" → ["cd a", "SEP:&&", "git push"]
split_command() {
  local cmd="$1"
  local i=0 len=${#cmd}
  local in_squote=0 in_dquote=0
  local paren_depth=0 brace_depth=0
  local segment=""

  while [ "$i" -lt "$len" ]; do
    local c="${cmd:$i:1}"

    if [ "$in_squote" -eq 1 ]; then
      [ "$c" = "'" ] && in_squote=0
      segment+="$c"; i=$((i+1)); continue
    fi

    if [ "$in_dquote" -eq 1 ]; then
      if [ "$c" = "\\" ]; then
        # 次の 1 文字を escape として消費
        local nc="${cmd:$((i+1)):1}"
        case "$nc" in
          '$'|'`'|'"'|'\\')
            segment+="$c$nc"; i=$((i+2)); continue ;;
        esac
      fi
      [ "$c" = '"' ] && in_dquote=0
      segment+="$c"; i=$((i+1)); continue
    fi

    case "$c" in
      "'") in_squote=1; segment+="$c"; i=$((i+1)) ;;
      '"') in_dquote=1; segment+="$c"; i=$((i+1)) ;;
      '\\')
        # quote 外の `\` は次の 1 文字を escape する。両方をそのまま保持。
        local nc="${cmd:$((i+1)):1}"
        segment+="$c$nc"
        i=$((i+2))
        ;;
      '(') paren_depth=$((paren_depth+1)); segment+="$c"; i=$((i+1)) ;;
      ')') paren_depth=$((paren_depth-1)); segment+="$c"; i=$((i+1)) ;;
      '{') brace_depth=$((brace_depth+1)); segment+="$c"; i=$((i+1)) ;;
      '}') brace_depth=$((brace_depth-1)); segment+="$c"; i=$((i+1)) ;;
      ';')
        if [ "$paren_depth" -eq 0 ] && [ "$brace_depth" -eq 0 ]; then
          printf '%s\nSEP:;\n' "$segment"
          segment=""
        else
          segment+="$c"
        fi
        i=$((i+1))
        ;;
      $'\n')
        # quote 外の生改行は bash でも `;` 等価のコマンド区切り。 これを segment 区切りに
        # 含めないと、 multi-line command (`echo prep<NL>git push origin x` のような形) が
        # 単一 segment 扱いになり、 push 検出 (= 「最初の token が git で次が push」) を
        # 素通りして gate を bypass する。 quote / paren / brace 内の改行は空白に置換して
        # 1 segment として保持する (heredoc 埋め込みなど)。
        if [ "$paren_depth" -eq 0 ] && [ "$brace_depth" -eq 0 ]; then
          printf '%s\nSEP:;\n' "$segment"
          segment=""
        else
          segment+=" "
        fi
        i=$((i+1))
        ;;
      '&')
        if [ "$paren_depth" -eq 0 ] && [ "$brace_depth" -eq 0 ]; then
          local nc="${cmd:$((i+1)):1}"
          if [ "$nc" = "&" ]; then
            printf '%s\nSEP:&&\n' "$segment"
            segment=""; i=$((i+2))
          else
            printf '%s\nSEP:&\n' "$segment"
            segment=""; i=$((i+1))
          fi
        else
          segment+="$c"; i=$((i+1))
        fi
        ;;
      '|')
        if [ "$paren_depth" -eq 0 ] && [ "$brace_depth" -eq 0 ]; then
          local nc="${cmd:$((i+1)):1}"
          if [ "$nc" = "|" ]; then
            printf '%s\nSEP:||\n' "$segment"
            segment=""; i=$((i+2))
          else
            printf '%s\nSEP:|\n' "$segment"
            segment=""; i=$((i+1))
          fi
        else
          segment+="$c"; i=$((i+1))
        fi
        ;;
      *) segment+="$c"; i=$((i+1)) ;;
    esac
  done

  printf '%s\n' "$segment"
}

# unquote_token <token>
# stdout: トークン両端の quote ペアを 1 段剥がした文字列。
unquote_token() {
  local s="$1"
  case "$s" in
    \"*\") s="${s%\"}"; s="${s#\"}" ;;
    \'*\') s="${s%\'}"; s="${s#\'}" ;;
  esac
  printf '%s' "$s"
}

# skip_env_assignments <toks_array_ref> <idx_var_ref>
# 引数: tokenize_segment の出力配列の nameref、 現在 index 変数の nameref
# 動作: idx 位置から `NAME=VALUE` パターンの env-var assignment トークンを skip し、
#       最初の non-env トークンの位置に idx を進める。 unquote 後の値で判定する。
#
# 用途: bash の Simple Command (POSIX) は `[var=value ...] cmd args ...` の形式で、
# env-var assignment が cmd 名の前に並ぶ (`FOO=bar BAZ=qux git push ...` 等)。 各 hook で
# 「実コマンド token を取り出す」ためにこのループを書く必要があり、 同形のループが複数
# 箇所に重複していた。 構文解析 (= cmd-parser の責務) として 1 箇所に集約する。
#
# **nameref 名衝突に注意**: 呼び出し側の変数名が `_toks_ref` / `_idx_ref` / `_t` / `_n` と
# 一致すると bash の nameref が circular reference エラーになる。 呼び出し側は別 prefix
# (例: `_first_toks _fi`) を使うこと。
skip_env_assignments() {
  local -n _toks_ref="$1"
  local -n _idx_ref="$2"
  local _n=${#_toks_ref[@]}
  while [ "$_idx_ref" -lt "$_n" ]; do
    local _t
    _t="$(unquote_token "${_toks_ref[$_idx_ref]}")"
    if [[ "$_t" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      _idx_ref=$((_idx_ref+1))
    else
      break
    fi
  done
}

# tokenize_segment <segment> <output_array_name>
# 引数: segment 文字列、出力先 nameref 配列
# 動作: segment をトークン (空白区切り) に分割。 quote 内の空白は分割しない。
#       quote 文字自体はトークンに残す (呼び出し側が unquote_token を使う想定)。
tokenize_segment() {
  local seg="$1"
  local -n out_arr="$2"
  out_arr=()
  local i=0 len=${#seg}
  local in_squote=0 in_dquote=0
  local current=""

  while [ "$i" -lt "$len" ]; do
    local c="${seg:$i:1}"

    if [ "$in_squote" -eq 1 ]; then
      [ "$c" = "'" ] && in_squote=0
      current+="$c"
    elif [ "$in_dquote" -eq 1 ]; then
      if [ "$c" = "\\" ]; then
        local nc="${seg:$((i+1)):1}"
        case "$nc" in
          '$'|'`'|'"'|'\\')
            current+="$c$nc"; i=$((i+2)); continue ;;
        esac
      fi
      [ "$c" = '"' ] && in_dquote=0
      current+="$c"
    elif [ "$c" = "'" ]; then
      in_squote=1; current+="$c"
    elif [ "$c" = '"' ]; then
      in_dquote=1; current+="$c"
    elif [[ "$c" == [[:space:]] ]]; then
      if [ -n "$current" ]; then
        out_arr+=("$current")
        current=""
      fi
    else
      current+="$c"
    fi

    i=$((i+1))
  done

  [ -n "$current" ] && out_arr+=("$current")
}

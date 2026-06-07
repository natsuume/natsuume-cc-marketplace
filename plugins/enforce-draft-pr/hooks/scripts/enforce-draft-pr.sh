#!/bin/bash
# enforce-draft-pr.sh
# `gh pr create` に `--draft` を自動付与する PreToolUse フック。
#
# ## 設計
#
# 旧実装は生コマンド文字列への素朴な grep/sed で、draft 強制 (本プラグインの唯一の目的)
# が複数経路で静かに無効化されていた:
#   - `--title "my --draft feature"` のように引数値内の `--draft` を既存フラグと誤検知して
#     draft 付与を skip する (false negative)
#   - 検出 (`\s+`) と書き換え (固定スペース) の不整合で連続空白 / タブ区切りだと `--draft`
#     が付かない
#   - `gh -R owner/repo pr create` (global option) / `cd repo && gh pr create` /
#     `GH_TOKEN=x gh pr create` (env prefix) / 行継続・チェーンを検出できない
#
# 本実装はコマンド全体を **1 回のクォート対応スキャン** でトークン化し、 各 top-level
# コマンドの先頭が `[env]* gh [global-flags]* pr create` のときだけ、 `create` トークンの
# 末尾オフセット直後に ` --draft` を挿入する。 挿入以外の文字 (引数 / body / 空白 / タブ /
# 改行 / 区切り / コメント) は **1 バイトも変更しない** (offset insert)。
#
# ## なぜ 1 回スキャンか (コマンド / PR body を壊さないため)
#
# `--draft` 挿入のためコマンドを書き換える hook なので、 検出が **クォート非対応** だと
# `--body "...gh pr create..."` のように本文内に "gh pr create" を含むケースで本文側に
# 誤挿入し PR 本文を破壊する。 クォート対応スキャンでは quoted な `--body "..."` は
# **不透明な単一トークン** になるため、 本文内の "pr create" / 改行 / 区切り文字 / `#` は
# invocation 検出に一切影響しない。 また segment 分割を中間文字列プロトコルに頼らず
# offset で直接扱うため、 本文行が偶然区切り文字列に一致しても誤読しない。
#
# ## top-level コメント
#
# `#` が語頭 (= command-start / 空白直後) に現れたら行末までをコメントとして読み飛ばす
# (bash 準拠)。 これによりコメントは保持しつつ、 同一コマンド内の本物の `gh pr create`
# には正しく `--draft` を付与できる。 引用符内の `#` (`--body "fix #1"`) はコメント扱い
# しない。
#
# ## --draft=false (明示的 非 draft) の扱い
#
# `--draft` / `--draft=true` / `-d` は「既に draft 指定あり」として素通しする。 一方
# `--draft=false` (cobra falsy: false/False/FALSE/0/f/F) のように **明示的に非 draft** を
# 指定した PR 作成は、 本プラグインの enforce 方針 (README:「PR は必ず draft で起こす」) に
# 反するため **deny (ブロック)** する。 non-draft を作りたい場合はプラグインを無効化する運用。
#
# ## limitations (cooperative 利用前提・README にも明記)
#
# - シェルラッパー (`bash -c "..."`) / `$(...)` 置換 / バッククォート / subshell `(...)` の
#   **内部** の `gh pr create` には介入しない。
# - `gh api` で直接 PR を作成するケースには介入しない。
# - **コマンド名の前** に置くリダイレクト (`>out gh pr create` / `2>/tmp/log gh pr create`) は
#   検出しない (リダイレクト対象を command-start と誤認するため)。 通常の末尾リダイレクト
#   (`gh pr create ... > out`) は問題なく付与する。 先頭リダイレクトは agent 生成では稀。
# - 粗フィルタ (冒頭 case) は生コマンド文字列の `gh` / `pr` / `create` 部分文字列で早期判定する
#   ため、 **キーワードの途中** に行継続を挟む難読化 (`cre\<改行>ate` 等) は検出しない。
#   トークン **間** の行継続 (`gh pr \<改行>create`) は正しく処理する。 これは sibling の
#   block-default-branch-pr.sh も用いる同一の coarse-filter 方針で、 substring フィルタである
#   以上 cooperative 利用前提の制約 (意図的難読化による bypass は対象外)。
# - 単一引用符 `'...'` 内に literal な `\<改行>` を含む稀なケースでは行継続除去でそれが消える。

INPUT=$(cat)

# 大半の Bash 呼び出しは `gh pr create` と無関係。粗フィルタで早期離脱。
case "$INPUT" in
  *"gh"*"pr"*"create"*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# bash の `$(...)` trailing-LF trim で消えた末尾 `\<LF>` を復元
# (詳細は cmd-parser.sh の「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション)。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh"

# 行継続 `\<LF>` のみ除去 (bash 実挙動と一致)。 生改行 (body 内など) は温存する。
# fast-path: `\<LF>` を含まない大多数の入力は subshell fork を回避し原文をそのまま使う。
case "$COMMAND" in
  *\\$'\n'*) COMMAND=$(normalize_line_continuations "$COMMAND") ;;
esac

# コマンド全体を quote 対応で 1 回スキャンし、 本物の `gh ... pr create` の create 直後に
# ` --draft` を挿入する。 結果は RW_OUT に、 変更があれば RW_CHANGED=1 を設定する。
RW_OUT="$COMMAND"
RW_CHANGED=0
RW_DENY=0
analyze_and_rewrite() {
  local cmd="$1"
  local n=${#cmd} i=0 sq=0 dq=0 cur="" started=0 pend=1 tcs=0 nl=0 tnl=0 c nc
  # 各トークンの value / 末尾オフセット / command-start フラグ / 物理行頭フラグ (直前に改行が
  # あったか = here-doc 本文など別行のトークンか) を記録する。
  local -a TVAL=() TEND=() TSTART=() TNL=()
  while [ "$i" -lt "$n" ]; do
    c="${cmd:$i:1}"
    if [ "$sq" -eq 1 ]; then
      cur+="$c"; [ "$c" = "'" ] && sq=0; i=$((i+1)); continue
    fi
    if [ "$dq" -eq 1 ]; then
      if [ "$c" = "\\" ]; then
        nc="${cmd:$((i+1)):1}"
        case "$nc" in '$'|'`'|'"'|'\\') cur+="$c$nc"; i=$((i+2)); continue ;; esac
      fi
      cur+="$c"; [ "$c" = '"' ] && dq=0; i=$((i+1)); continue
    fi
    case "$c" in
      "'") [ "$started" -eq 0 ] && { tcs=$pend; pend=0; tnl=$nl; nl=0; }; sq=1; cur+="$c"; started=1; i=$((i+1)) ;;
      '"') [ "$started" -eq 0 ] && { tcs=$pend; pend=0; tnl=$nl; nl=0; }; dq=1; cur+="$c"; started=1; i=$((i+1)) ;;
      '\')
        # クォート外の `\` は次の 1 文字を escape する (bash 準拠)。 両者を同一トークンに取り込み、
        # `echo \; gh pr create` の `\;` を separator と誤認しないようにする。 末尾単独 `\` は
        # backslash 自体を保持する。
        [ "$started" -eq 0 ] && { tcs=$pend; pend=0; tnl=$nl; nl=0; }
        nc="${cmd:$((i+1)):1}"
        if [ -n "$nc" ]; then cur+="$c$nc"; i=$((i+2)); else cur+="$c"; i=$((i+1)); fi
        started=1
        ;;
      '#')
        if [ "$started" -eq 0 ]; then
          # 語頭の `#` は行末までコメント (改行自体は次ループで区切りとして処理)。
          while [ "$i" -lt "$n" ] && [ "${cmd:$i:1}" != $'\n' ]; do i=$((i+1)); done
        else
          cur+="$c"; i=$((i+1))
        fi
        ;;
      ' '|$'\t'|'<'|'>')
        # トークン区切り (コマンド区切りではない)。 `<` `>` はリダイレクト演算子。
        if [ "$started" -eq 1 ]; then TVAL+=("$cur"); TEND+=("$i"); TSTART+=("$tcs"); TNL+=("$tnl"); cur=""; started=0; fi
        i=$((i+1))
        ;;
      $'\n')
        # **改行は command-start にしない** (pend は立てない): here-doc 本文の各行を新規コマンドと
        # 誤認して本文に --draft を挿入し破壊するのを防ぐため。 代わりに nl=1 を立て、 次の
        # トークンに「物理行頭」フラグ (TNL) を付ける。 これにより引数スキャンを「コマンド行内」
        # に限定でき、 here-doc 本文 (= 別行) を引数と誤認しない。 また `gh pr create --body-file -
        # <<EOF ... EOF` は先頭行で検出・付与でき本文は不変。 代償として **literal な改行で区切られた
        # 2 行目以降の行頭 `gh pr create`** は検出対象外 (`;` / `&&` ・単一行・別 Bash 呼び出しを想定)。
        if [ "$started" -eq 1 ]; then TVAL+=("$cur"); TEND+=("$i"); TSTART+=("$tcs"); TNL+=("$tnl"); cur=""; started=0; fi
        nl=1; i=$((i+1))
        ;;
      ';')
        if [ "$started" -eq 1 ]; then TVAL+=("$cur"); TEND+=("$i"); TSTART+=("$tcs"); TNL+=("$tnl"); cur=""; started=0; fi
        pend=1; i=$((i+1))
        ;;
      '&'|'|')
        if [ "$started" -eq 1 ]; then TVAL+=("$cur"); TEND+=("$i"); TSTART+=("$tcs"); TNL+=("$tnl"); cur=""; started=0; fi
        nc="${cmd:$((i+1)):1}"
        if [ "$nc" = "$c" ]; then i=$((i+2)); else i=$((i+1)); fi
        pend=1
        ;;
      '(')
        if [ "$started" -eq 1 ]; then
          # トークン途中の `(` は `NAME=(...)` 配列代入 / `$(...)` 置換 / 関数定義 `foo(` など
          # の **構文・データ** であり、 サブシェルのコマンド開始ではない。 `(` を現トークンに
          # 取り込み、 中の語を command-start にしない (配列要素・置換内部を実コマンドと誤認して
          # 書き換え・誤 deny しないため)。
          cur+="$c"; i=$((i+1))
        else
          # コマンド位置の `(` = サブシェル開始 (`( cmd )`)。 中の先頭は command-start。
          pend=1; i=$((i+1))
        fi
        ;;
      ')')
        if [ "$started" -eq 1 ]; then TVAL+=("$cur"); TEND+=("$i"); TSTART+=("$tcs"); TNL+=("$tnl"); cur=""; started=0; fi
        pend=1; i=$((i+1))
        ;;
      *)
        [ "$started" -eq 0 ] && { tcs=$pend; pend=0; tnl=$nl; nl=0; }
        cur+="$c"; started=1; i=$((i+1))
        ;;
    esac
  done
  [ "$started" -eq 1 ] && { TVAL+=("$cur"); TEND+=("$i"); TSTART+=("$tcs"); TNL+=("$tnl"); }

  local ntok=${#TVAL[@]} t=0 v
  local -a INS=()   # ` --draft` を挿入するオフセット (昇順)
  while [ "$t" -lt "$ntok" ]; do
    if [ "${TSTART[$t]}" -eq 1 ]; then
      local k=$t
      local boundary_hit=0
      # 先頭の env-var assignment (`NAME=VALUE`) を skip。
      # **境界越境ガード (v0.2.1 fix)**: env-skip ループは現在のコマンド境界 (token $t) 内のみで
      # 動かす。 $k > $t で次の command-start (`TSTART=1`) または next-line (`TNL=1`) に達したら
      # boundary_hit=1 で記録して break する。 これが無いと `FOO=bar; gh pr create` のような
      # 連結で、 `FOO=bar` を skip した後 boundary を越境して次の `gh` 段の `pr create` まで
      # env-skip 走査が伸び、 同じ `create` オフセットに **2 度** ` --draft` を挿入する重複付与
      # バグになる。 `gh` は重複 `--draft` を寛容に扱うため機能破壊は無いが parser bug として
      # は明確。 **boundary_hit を flag に立てる理由 (v0.2.1 fix #2 — codex review 指摘)**: 単に
      # break するだけでは `k` は次コマンドの `gh` を指したまま落下し、 後段の `gh|*/gh)`
      # matcher が走って ` --draft` を 1 回挿入してしまう。 さらに outer while が `t=$((t+1))`
      # で次イテレーションに進み、 `TSTART[$t]=1` の `gh` で改めて正規処理が走るため、 同じ
      # オフセットに 2 度 INS が push される。 boundary_hit を立てて outer iteration を直接
      # skip すれば、 outer の次イテレーション (= t=1 の `gh`) で 1 回だけ正規処理が走る。
      while [ "$k" -lt "$ntok" ]; do
        if [ "$k" -gt "$t" ] && { [ "${TSTART[$k]}" -eq 1 ] || [ "${TNL[$k]}" -eq 1 ]; }; then
          boundary_hit=1
          break
        fi
        v="$(unquote_token "${TVAL[$k]}")"
        if [[ "$v" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then k=$((k+1)); else break; fi
      done
      if [ "$boundary_hit" -eq 1 ]; then
        t=$((t+1))
        continue
      fi
      if [ "$k" -lt "$ntok" ]; then
        v="$(unquote_token "${TVAL[$k]}")"
        case "$v" in
          gh|*/gh)
            k=$((k+1))
            # gh と subcommand の間に置ける、 値を取る global option は `-R` / `--repo` のみ。
            # 分離形 `-R x` は値も skip、 連結形 `-R=x` / `--repo=x` と attached 短縮形 `-Rx`
            # (実機確認: `gh -Rcli/cli pr list` は有効) は自己完結なので flag のみ skip する。
            # 汎用 glob で skip すると `gh extension exec myext pr create` の引数を subcommand と
            # 取り違えるため、 明示的に `-R` / `--repo` 系だけを扱う。
            while [ "$k" -lt "$ntok" ] && [ "${TSTART[$k]}" -eq 0 ] && [ "${TNL[$k]}" -eq 0 ]; do
              v="$(unquote_token "${TVAL[$k]}")"
              case "$v" in
                -R=*|--repo=*) k=$((k+1)) ;;                                   # 連結 = 形 (自己完結)
                -R|--repo) k=$((k+1)); [ "$k" -lt "$ntok" ] && [ "${TSTART[$k]}" -eq 0 ] && [ "${TNL[$k]}" -eq 0 ] && k=$((k+1)) ;;  # 分離形: 値 (repo) も skip
                -R?*) k=$((k+1)) ;;                                            # attached 短縮形 -Rvalue (gh で有効・自己完結)
                *) break ;;
              esac
            done
            # subcommand が `pr`、 次が `create` のときだけ対象。 gh と **同一コマンド・同一行**
            # の token に限定し (TSTART=0 && TNL=0)、 `gh && pr create` のように別コマンドの
            # pr/create を境界を跨いで誤マッチしない。
            if [ "$k" -lt "$ntok" ]; then
              v="$(unquote_token "${TVAL[$k]}")"
              if [ "${TSTART[$k]}" -eq 0 ] && [ "${TNL[$k]}" -eq 0 ] && [ "$v" = "pr" ]; then
                k=$((k+1))
                # `pr` と `create` の間にも repo flag を置ける (`gh pr -R owner/repo create` は
                # gh が許容する有効形) ので、 ここでも `-R` / `--repo` (+ 値) を skip する。
                while [ "$k" -lt "$ntok" ] && [ "${TSTART[$k]}" -eq 0 ] && [ "${TNL[$k]}" -eq 0 ]; do
                  v="$(unquote_token "${TVAL[$k]}")"
                  case "$v" in
                    -R=*|--repo=*) k=$((k+1)) ;;
                    -R|--repo) k=$((k+1)); [ "$k" -lt "$ntok" ] && [ "${TSTART[$k]}" -eq 0 ] && [ "${TNL[$k]}" -eq 0 ] && k=$((k+1)) ;;
                    -R?*) k=$((k+1)) ;;
                    *) break ;;
                  esac
                done
                if [ "$k" -lt "$ntok" ] && [ "${TSTART[$k]}" -eq 0 ] && [ "${TNL[$k]}" -eq 0 ] && [ "$(unquote_token "${TVAL[$k]}")" = "create" ]; then
                  local cidx=$k j=$((k+1)) on=0 off=0 dval
                  # この invocation の引数 (次の command-start まで) の draft 指定を判定する。
                  #   on  : --draft / --draft=<truthy> / -d / -d=<truthy>  (既に draft 指定あり)
                  #   off : --draft=<falsy> / -d=<falsy>                    (= 明示的 非 draft)
                  # falsy は cobra (strconv.ParseBool) の偽値: false/False/FALSE/0/f/F。
                  # **値を取るフラグ (--title 等) の分離形の値は skip** する。 これをしないと
                  # `--title "--draft"` / `--body "--draft=false"` のように値が draft-flag 風の
                  # 文字列のとき、 値をフラグと誤認して付与漏れ / 誤 deny する。 連結形
                  # (`--title=...`) は自己完結なので値 skip 不要。
                  # 引数スキャンは「コマンド行内」に限定する: 次の command-start (TSTART=1) または
                  # 次の物理行 (TNL=1 = here-doc 本文など) で停止し、 別行のトークンを引数と誤認しない。
                  while [ "$j" -lt "$ntok" ] && [ "${TSTART[$j]}" -eq 0 ] && [ "${TNL[$j]}" -eq 0 ]; do
                    v="$(unquote_token "${TVAL[$j]}")"
                    case "$v" in
                      -t|--title|-b|--body|-F|--body-file|-B|--base|-H|--head|-a|--assignee|-l|--label|-m|--milestone|-p|--project|-r|--reviewer|-T|--template|--recover|-R|--repo)
                        # 値消費フラグ: 同一行の次トークン (= 値) も skip する。
                        j=$((j+1))
                        [ "$j" -lt "$ntok" ] && [ "${TSTART[$j]}" -eq 0 ] && [ "${TNL[$j]}" -eq 0 ] && j=$((j+1))
                        continue ;;
                      --draft|-d) on=1 ;;
                      --draft=*|-d=*)
                        # 値部のクォートを剥がす (`--draft="false"` は bash が --draft=false を
                        # 渡すため falsy 判定が必要)。
                        dval="$(unquote_token "${v#*=}")"
                        case "$dval" in
                          false|False|FALSE|0|f|F) off=1 ;;
                          *) on=1 ;;
                        esac ;;
                    esac
                    j=$((j+1))
                  done
                  # `--draft=false` 等の明示的 非 draft は enforce 方針違反として deny する
                  # (truthy / 未指定より優先)。 README『PR は必ず draft で起こす』に合わせる。
                  if [ "$off" -eq 1 ]; then
                    RW_DENY=1
                  elif [ "$on" -eq 0 ]; then
                    INS+=("${TEND[$cidx]}")
                  fi
                fi
              fi
            fi
            ;;
        esac
      fi
    fi
    t=$((t+1))
  done

  local m=${#INS[@]}
  if [ "$m" -eq 0 ]; then RW_CHANGED=0; RW_OUT="$cmd"; return; fi
  # オフセットは昇順なので、 後ろから挿入して前方オフセットの不変性を保つ。
  local result="$cmd" idx=$((m-1)) off
  while [ "$idx" -ge 0 ]; do
    off="${INS[$idx]}"
    result="${result:0:$off} --draft${result:$off}"
    idx=$((idx-1))
  done
  RW_CHANGED=1; RW_OUT="$result"
}

analyze_and_rewrite "$COMMAND"

# `--draft=false` 等の明示的 非 draft 指定は enforce 方針違反として deny する。
if [ "$RW_DENY" -eq 1 ]; then
  jq -n '{
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "deny",
      "permissionDecisionReason": "enforce-draft-pr: `--draft=false` のように非 draft を明示する PR 作成はこのプラグインの方針 (PR は必ず draft で起こす) で許可していません。`--draft=false` を外して draft PR を作成するか、 draft 強制が不要ならこのプラグインを無効化してください。"
    }
  }'
  exit 0
fi

# どの invocation も既に draft 指定済みなら書き換え不要。
[ "$RW_CHANGED" -eq 1 ] || exit 0

jq -n --arg cmd "$RW_OUT" '{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "PR は draft として作成されます",
    "updatedInput": {
      "command": $cmd
    }
  }
}'

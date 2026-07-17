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
# 改行 / 区切り / コメント / heredoc 本文) は **1 バイトも変更しない** (offset insert)。
#
# ## トークン構築 (index ベース、O(n) 保証)
#
# トークン値は `cur+="$c"` の 1 文字連結ではなく、 トークン開始オフセット (`tbeg`) から
# flush 時点の `i` までを `${cmd:$tbeg:$((i-tbeg))}` で 1 回だけ substring 化して得る。
# quote 内は次の quote / escape 文字までの距離を `${rest%%pattern}` で測って一括ジャンプ
# する (single quote は次の `'` まで。double quote は `"` と `\` の近い方まで、 candidate
# 文字列を `CHUNK` 単位の bounded window に区切って測ることで、 backslash が密集する入力
# でも tail 全体の再切り出しコストを CHUNK 定数に抑える)。 quote 外の行継続 (`\<LF>`) で
# トークンが分断された場合のみ、 それまでの区間を `frag` バッファへ退避してから連結する
# (=「行継続を含むトークンだけ」 O(区間数) の追加コストを払う設計)。
#
# ## なぜ 1 回スキャンか (コマンド / PR body を壊さないため)
#
# `--draft` 挿入のためコマンドを書き換える hook なので、 検出が **クォート非対応** だと
# `--body "...gh pr create..."` のように本文内に "gh pr create" を含むケースで本文側に
# 誤挿入し PR 本文を破壊する。 クォート対応スキャンでは quoted な `--body "..."` は
# **不透明な単一トークン** になるため、 本文内の "pr create" / 改行 / 区切り文字 / `#` は
# invocation 検出に一切影響しない。 heredoc 本文も同様に不透明データとして扱う (後述)。
# また segment 分割を中間文字列プロトコルに頼らず offset で直接扱うため、 本文行が偶然
# 区切り文字列に一致しても誤読しない。
#
# ## top-level コメント
#
# `#` が語頭 (= command-start / 空白直後) に現れたら行末までをコメントとして読み飛ばす
# (bash 準拠)。 これによりコメントは保持しつつ、 同一コマンド内の本物の `gh pr create`
# には正しく `--draft` を付与できる。 引用符内の `#` (`--body "fix #1"`) はコメント扱い
# しない。 コメント内の `<<` も heredoc として登録しない (行末までスキップするため
# `<` ハンドラに到達しない)。
#
# ## heredoc 認識 (#144)
#
# quote 外で `<<` (herestring `<<<` は除外) を検出したら、 `-` (strip フラグ) → 空白 →
# delimiter word (`WORD` / `'WORD'` / `"WORD"` / `\WORD`、 WORD = `[A-Za-z0-9_]+`) の順に
# 読み取り、 pending キュー (`HD_DELIMS`/`HD_STRIPS`/`HD_QUOTED`) へ積む。 delimiter word
# 自体はトークンに含めない。 宣言行の残りは通常どおり引数として走査を続け、 quote 外の
# 生改行に到達した時点でキューが非空なら本文モードへ入る。 本文モードはキューの出現順
# (FIFO) に 1 entry ずつ、 行単位で delimiter 単独行 (`<<-` は行頭タブ除去後に完全一致)
# を探して消費する。 unquoted delimiter の本文では、 行末の連続 backslash が奇数個の行の
# 直後行は terminator 判定をしない (bash の行継続結合が terminator 判定に先行するため)。
# quoted delimiter (`'W'`/`"W"`/`\W` の 3 形式とも) はこの行継続処理をしない。 本文全体は
# トークン化・command-start 設定・引数スキャンの対象にしない不透明データであり、 未終端
# (入力末尾まで delimiter が見つからない) の場合はそのまま走査を終了する (opaque
# fallback ではない — README にも明記)。
#
# ## 算術式スキップ (regression 回避)
#
# heredoc の `<<` 検出はビットシフト演算子 `<<` を誤認しうるため、 `$((...))` / コマンド
# 位置の `((...))` を対応する `))` まで (内部括弧の深度追跡込み、 quote は無視) 丸ごと
# 1 トークンとして取り込み、 内部の `<<` を heredoc 宣言として扱わないようにする。
#
# ## opaque fallback (#144)
#
# heredoc 宣言が対応構文 (上記 5 形式) に一致しない場合 (部分 quote 連結 `E"OF"` 等、
# 英数字・アンダースコア以外を含む unquoted word、 word 直後が区切り文字以外など) は
# **それ以降のコマンド文字列を一切解析しない** (opaque fallback、 スキャンループを break)。
# fallback 発動後は、 発動位置 (対象 invocation (`gh ... pr create`) の引数領域内か、
# 別コマンドの領域か) に **依らず常に deny** する (契約 1 本化。 security レビューで
# 「別コマンドの領域で発動 → 不介入」 という旧 2 分岐の一方が bypass と指摘されたため)。
# fallback 以降は解析不能 (opaque) であり、 残余文字列への部分文字列判定による救済は、
# 行継続 (`cre\<改行>ate`) や quote 分断 (`cre""ate`) といった bash の語結合を使う難読化
# で bypass できるため、 解析できない領域が生じた時点で fail-closed に倒す。 冒頭の粗
# フィルタ (`*gh*pr*create*`) を通過した入力のみがこのスキャナに到達するため、
# `gh pr create` 風の文字列を全く含まない無関係コマンドがこの deny に到達することはない。
# fallback より前に separator (`;`/`&&`/`||`/`|`/`&`) で完結した invocation があっても、
# deny が全体に優先するため ` --draft` 挿入は行わない (旧契約の 「完結済み invocation
# への挿入は有効」 は廃止)。
#
# ## 行継続のスキャナ内処理
#
# 解析前の一括 `normalize_line_continuations` 呼び出しは行わない (原文保持を offset
# insert のみで保証するため)。 quote 外 および double quote 内の `\<LF>` は行継続として
# 扱う (トークンを連結し、 行境界にしない = command-start / 物理行フラグを立てない)。
# single quote 内 および quoted heredoc 本文内の `\<LF>` は literal データとして扱う
# (delimiter 行継続判定を除く)。 出力コマンド文字列は原文保持 (挿入のみ) であり、
# 行継続の除去は **トークンの論理値** (フラグ名・`--draft=` 値の判定に使う) にのみ適用
# される。
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
# - 未対応の heredoc delimiter 構文 (部分 quote 連結 `E"OF"` 等、 英数字・アンダースコア
#   以外を含む unquoted word 等) を検出したら、 それ以降は一切解析しない (opaque
#   fallback)。 fallback 発動時は発動位置 (対象 invocation の引数領域内か、 別コマンドの
#   領域か) に依らず deny する (draft 付与漏れ方向の fail-safe)。

INPUT=$(cat)

# 大半の Bash 呼び出しは `gh pr create` と無関係。粗フィルタで早期離脱。
case "$INPUT" in
  *"gh"*"pr"*"create"*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# bash の `$(...)` は trailing newline を全部 trim するため、 単純に
# `jq -r '.tool_input.command'` で取得すると末尾 LF (複数可) が失われる。 jq 側で
# sentinel (SOH = `\x01`。 shell コマンドに現れない制御文字) を値の末尾に付加してから
# 取得し、 `$(...)` の trim を通過させたあと bash 側でパターン置換により sentinel だけを
# 剥がすことで、 元の末尾 LF をそのままバイト保持する。 sentinel は `--arg` 経由で渡す
# ため、 jq プログラム文字列中に制御文字のエスケープ表記を書く必要がない。
SENTINEL=$'\x01'
COMMAND=$(printf '%s' "$INPUT" | jq -r --arg sentinel "$SENTINEL" '(.tool_input.command // empty) + $sentinel')
COMMAND="${COMMAND%$'\x01'}"
[ -n "$COMMAND" ] || exit 0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh"

# コマンド全体を quote 対応で 1 回スキャンし、 本物の `gh ... pr create` の create 直後に
# ` --draft` を挿入する。 結果は RW_OUT に、 変更があれば RW_CHANGED=1 を設定する。
RW_OUT="$COMMAND"
RW_CHANGED=0
RW_DENY=0

# _flush_token: analyze_and_rewrite のトークン走査ループから呼ばれる内部ヘルパー。
# bash の関数呼び出しは動的スコープなので、 呼び出し元 (analyze_and_rewrite) が
# local 宣言した cmd/i/tbeg/frag/started/tcs/tnl/TVAL/TEND/TSTART/TNL を、 本関数が
# 同名の local を宣言せずに直接読み書きする。 進行中のトークンが無ければ何もしない。
_flush_token() {
  [ "$started" -eq 1 ] || return 0
  local val
  if [ -n "$frag" ]; then
    val="$frag${cmd:$tbeg:$((i-tbeg))}"
  else
    val="${cmd:$tbeg:$((i-tbeg))}"
  fi
  TVAL+=("$val")
  TEND+=("$i")
  TSTART+=("$tcs")
  TNL+=("$tnl")
  started=0
  frag=""
}

analyze_and_rewrite() {
  local LC_ALL=C
  local cmd="$1"
  local n=${#cmd} i=0
  local sq=0 dq=0 started=0 pend=1 tcs=0 nl=0 tnl=0
  local tbeg=0 frag=""
  local c nc
  local NL=$'\n' TAB=$'\t'
  # 各トークンの value / 末尾オフセット / command-start フラグ / 物理行頭フラグ (直前に
  # 改行があったか = here-doc 本文など別行のトークンか) を記録する。
  local -a TVAL=() TEND=() TSTART=() TNL=()
  # heredoc pending キュー (出現順 FIFO)。
  local -a HD_DELIMS=() HD_STRIPS=() HD_QUOTED=()
  local OPAQUE=0
  # double quote 内ジャンプの bounded window サイズ。 backslash が密集する入力でも
  # 1 回の window 抽出コストを定数に抑え、 全体を O(n) に保つ (tail 全体を毎回
  # 切り出す設計だと、 backslash が 1 文字おきに現れる入力で O(n^2) に劣化する)。
  local CHUNK=128

  while [ "$i" -lt "$n" ]; do
    if [ "$sq" -eq 1 ]; then
      local sq_rest sq_chunk
      sq_rest="${cmd:$i}"
      sq_chunk="${sq_rest%%\'*}"
      i=$((i+${#sq_chunk}))
      if [ "$i" -lt "$n" ]; then
        i=$((i+1)); sq=0
      fi
      continue
    fi

    if [ "$dq" -eq 1 ]; then
      local dq_piece dq_plen dq_cq dq_cb dq_lq dq_lb
      dq_piece="${cmd:$i:$CHUNK}"
      dq_plen=${#dq_piece}
      dq_cq="${dq_piece%%\"*}"
      dq_cb="${dq_piece%%\\*}"
      dq_lq=${#dq_cq}
      dq_lb=${#dq_cb}
      if [ "$dq_lb" -ge "$dq_plen" ] && [ "$dq_lq" -ge "$dq_plen" ]; then
        # window 内に `"` も `\` も無い (window が尽きた、 または入力末尾)。
        # window 分だけ進めて再スキャンする (末尾なら次ループの `i<n` で自然終了)。
        i=$((i+dq_plen))
        continue
      fi
      if [ "$dq_lb" -lt "$dq_lq" ]; then
        i=$((i+dq_lb))
        nc="${cmd:$((i+1)):1}"
        case "$nc" in
          '$'|'`'|'"'|'\')
            # 標準 escape ペア。 両者を同一トークンに取り込む。
            i=$((i+2))
            ;;
          "$NL")
            # dq 内の行継続。 それまでの区間を frag へ退避し、 `\<LF>` の 2 文字を
            # 論理値から除去する (出力は原文保持なので i を進めるだけで良い)。
            frag+="${cmd:$tbeg:$((i-tbeg))}"
            i=$((i+2))
            tbeg=$i
            ;;
          *)
            # backslash 単体 (次が非 escape 文字)。 backslash 自身だけ 1 文字進める。
            i=$((i+1))
            ;;
        esac
      else
        # 閉じ quote の方が近い。
        i=$((i+dq_lq+1))
        dq=0
      fi
      continue
    fi

    c="${cmd:$i:1}"
    case "$c" in
      "'")
        if [ "$started" -eq 0 ]; then tbeg=$i; tcs=$pend; pend=0; tnl=$nl; nl=0; fi
        sq=1; started=1; i=$((i+1))
        ;;
      '"')
        if [ "$started" -eq 0 ]; then tbeg=$i; tcs=$pend; pend=0; tnl=$nl; nl=0; fi
        dq=1; started=1; i=$((i+1))
        ;;
      '\')
        # クォート外の `\`。 次の 1 文字が LF なら行継続 (トークン連結・行境界にしない)。
        # それ以外は bash 準拠の 1 文字 escape として同一トークンに取り込む。
        nc="${cmd:$((i+1)):1}"
        if [ "$nc" = "$NL" ]; then
          if [ "$started" -eq 1 ]; then
            frag+="${cmd:$tbeg:$((i-tbeg))}"
            i=$((i+2)); tbeg=$i
          else
            # トークン境界での行継続 (`gh pr \<LF>create` 等) は完全に不可視。
            i=$((i+2))
          fi
        else
          if [ "$started" -eq 0 ]; then tbeg=$i; tcs=$pend; pend=0; tnl=$nl; nl=0; fi
          started=1
          if [ -n "$nc" ]; then i=$((i+2)); else i=$((i+1)); fi
        fi
        ;;
      '#')
        if [ "$started" -eq 0 ]; then
          # 語頭の `#` は行末までコメント (改行自体は次ループで区切りとして処理)。
          local cmt_rest cmt_head
          cmt_rest="${cmd:$i}"
          cmt_head="${cmt_rest%%"$NL"*}"
          i=$((i+${#cmt_head}))
        else
          started=1; i=$((i+1))
        fi
        ;;
      ' '|"$TAB"|'>')
        # トークン区切り (コマンド区切りではない)。 `>` はリダイレクト演算子。
        _flush_token
        i=$((i+1))
        ;;
      '<')
        # `<` はリダイレクト演算子だが、 `<<`/`<<<` は heredoc / herestring の可能性が
        # あるため個別に判定する。
        _flush_token
        if [ "${cmd:$((i+1)):1}" != "<" ]; then
          i=$((i+1))
        elif [ "${cmd:$((i+2)):1}" = "<" ]; then
          # herestring `<<<`。 heredoc ではない。 3 文字進めて通常のトークン境界にする。
          i=$((i+3))
        else
          # heredoc 宣言。 `-` (strip) → 空白 → delimiter word を読み取る。
          local hj=$((i+2)) hd_strip=0 hd_word="" hd_quoted=0
          local hd_wstart hd_wc hd_rest hd_w hd_next
          if [ "${cmd:$hj:1}" = "-" ]; then hd_strip=1; hj=$((hj+1)); fi
          while :; do
            case "${cmd:$hj:1}" in
              ' '|"$TAB") hj=$((hj+1)) ;;
              *) break ;;
            esac
          done
          case "${cmd:$hj:1}" in
            "'")
              hj=$((hj+1))
              hd_rest="${cmd:$hj}"
              hd_w="${hd_rest%%\'*}"
              if [ "${#hd_w}" -eq "${#hd_rest}" ] || ! [[ "$hd_w" =~ ^[A-Za-z0-9_]+$ ]]; then
                OPAQUE=1; break
              fi
              hd_word="$hd_w"; hj=$((hj+1+${#hd_w})); hd_quoted=1
              ;;
            '"')
              hj=$((hj+1))
              hd_rest="${cmd:$hj}"
              hd_w="${hd_rest%%\"*}"
              if [ "${#hd_w}" -eq "${#hd_rest}" ] || ! [[ "$hd_w" =~ ^[A-Za-z0-9_]+$ ]]; then
                OPAQUE=1; break
              fi
              hd_word="$hd_w"; hj=$((hj+1+${#hd_w})); hd_quoted=1
              ;;
            '\')
              hj=$((hj+1)); hd_wstart=$hj
              while :; do
                hd_wc="${cmd:$hj:1}"
                case "$hd_wc" in
                  [A-Za-z0-9_]) hj=$((hj+1)) ;;
                  *) break ;;
                esac
              done
              if [ "$hj" -eq "$hd_wstart" ]; then OPAQUE=1; break; fi
              hd_word="${cmd:$hd_wstart:$((hj-hd_wstart))}"; hd_quoted=1
              ;;
            *)
              hd_wstart=$hj
              while :; do
                hd_wc="${cmd:$hj:1}"
                case "$hd_wc" in
                  [A-Za-z0-9_]) hj=$((hj+1)) ;;
                  *) break ;;
                esac
              done
              if [ "$hj" -eq "$hd_wstart" ]; then OPAQUE=1; break; fi
              hd_word="${cmd:$hd_wstart:$((hj-hd_wstart))}"; hd_quoted=0
              ;;
          esac
          hd_next="${cmd:$hj:1}"
          case "$hd_next" in
            ""|" "|"$TAB"|"$NL"|";"|"&"|"|"|"("|")"|"<"|">")
              HD_DELIMS+=("$hd_word")
              HD_STRIPS+=("$hd_strip")
              HD_QUOTED+=("$hd_quoted")
              i=$hj
              ;;
            *)
              OPAQUE=1; break
              ;;
          esac
        fi
        ;;
      "$NL")
        # **改行は command-start にしない** (pend は立てない): here-doc 本文の各行を
        # 新規コマンドと誤認して本文に --draft を挿入し破壊するのを防ぐため。 代わりに
        # nl=1 を立て、 次のトークンに「物理行頭」フラグ (TNL) を付ける。 pending
        # heredoc があれば、 ここから本文モードに入り delimiter 行まで丸ごとスキップする。
        _flush_token
        i=$((i+1))
        if [ "${#HD_DELIMS[@]}" -gt 0 ]; then
          local defer_next=0 hb_delim hb_strip hb_quoted hb_rest hb_line hb_llen
          local hb_line_end hb_has_nl hb_check hb_bs bs_probe bs_stripped hb_odd hb_term
          while [ "${#HD_DELIMS[@]}" -gt 0 ] && [ "$i" -lt "$n" ]; do
            hb_delim="${HD_DELIMS[0]}"; hb_strip="${HD_STRIPS[0]}"; hb_quoted="${HD_QUOTED[0]}"
            hb_rest="${cmd:$i}"
            hb_line="${hb_rest%%"$NL"*}"
            hb_llen=${#hb_line}
            hb_line_end=$((i+hb_llen))
            hb_has_nl=1
            [ "$hb_line_end" -ge "$n" ] && hb_has_nl=0

            hb_check="$hb_line"
            if [ "$hb_strip" -eq 1 ]; then
              while [ "${hb_check:0:1}" = "$TAB" ]; do
                hb_check="${hb_check:1}"
              done
            fi

            hb_term=0
            if [ "$defer_next" -eq 0 ] && [ "$hb_check" = "$hb_delim" ]; then
              hb_term=1
            fi

            if [ "$hb_term" -eq 1 ]; then
              HD_DELIMS=("${HD_DELIMS[@]:1}")
              HD_STRIPS=("${HD_STRIPS[@]:1}")
              HD_QUOTED=("${HD_QUOTED[@]:1}")
              defer_next=0
            else
              if [ "$hb_quoted" -eq 0 ]; then
                # unquoted delimiter のみ、 行末の連続 backslash の奇偶を見て次行の
                # terminator 判定を遅延させるか決める (bash の行継続結合が terminator
                # 判定に先行する実挙動に合わせる)。
                hb_bs=0; bs_probe="$hb_line"
                while :; do
                  bs_stripped="${bs_probe%\\}"
                  [ "$bs_stripped" = "$bs_probe" ] && break
                  hb_bs=$((hb_bs+1)); bs_probe="$bs_stripped"
                done
                hb_odd=0
                [ $((hb_bs % 2)) -eq 1 ] && hb_odd=1
                defer_next=$hb_odd
              else
                defer_next=0
              fi
            fi

            i=$hb_line_end
            [ "$hb_has_nl" -eq 1 ] && i=$((i+1))
          done
        fi
        nl=1
        ;;
      ';')
        _flush_token
        pend=1; i=$((i+1))
        ;;
      '&'|'|')
        _flush_token
        nc="${cmd:$((i+1)):1}"
        if [ "$nc" = "$c" ]; then i=$((i+2)); else i=$((i+1)); fi
        pend=1
        ;;
      '(')
        if [ "$started" -eq 1 ]; then
          # トークン途中の `(` は `NAME=(...)` 配列代入 / `$(...)` 置換 / 関数定義 `foo(` など
          # の **構文・データ** であり、 サブシェルのコマンド開始ではない。 直前 2 文字が
          # `$(` (= `$((` 完成) なら算術式として深度追跡込みで丸ごと取り込み、 内部の `<<`
          # (ビットシフト) を heredoc と誤認しないようにする。 それ以外は通常の 1 文字。
          if [ "$i" -ge 2 ] && [ "${cmd:$((i-2)):2}" = '$(' ]; then
            local depth=2
            i=$((i+1))
            while [ "$i" -lt "$n" ] && [ "$depth" -gt 0 ]; do
              case "${cmd:$i:1}" in
                '(') depth=$((depth+1)) ;;
                ')') depth=$((depth-1)) ;;
              esac
              i=$((i+1))
            done
          else
            i=$((i+1))
          fi
        else
          # コマンド位置の `(`。 次も `(` なら算術コマンド `((...))` として深度追跡込みで
          # 丸ごと 1 トークンに取り込む。 それ以外はサブシェル開始 (`( cmd )`)。
          if [ "${cmd:$((i+1)):1}" = "(" ]; then
            tbeg=$i; tcs=$pend; pend=0; tnl=$nl; nl=0
            started=1
            local depth=2
            i=$((i+2))
            while [ "$i" -lt "$n" ] && [ "$depth" -gt 0 ]; do
              case "${cmd:$i:1}" in
                '(') depth=$((depth+1)) ;;
                ')') depth=$((depth-1)) ;;
              esac
              i=$((i+1))
            done
          else
            pend=1; i=$((i+1))
          fi
        fi
        ;;
      ')')
        _flush_token
        pend=1; i=$((i+1))
        ;;
      *)
        if [ "$started" -eq 0 ]; then tbeg=$i; tcs=$pend; pend=0; tnl=$nl; nl=0; fi
        started=1; i=$((i+1))
        ;;
    esac
  done
  _flush_token

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
                  # opaque fallback (#144) の deny 判定はここでは行わない: fallback は発動
                  # 位置に依らず常に deny する契約のため、 ループ終了後に OPAQUE を 1 回
                  # 全体判定する (このループ内で per-invocation に判定すると、 fallback が
                  # 対象 invocation の外側で発動したケースを見逃す)。
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

  # opaque fallback (#144) が発動していたら、 発動位置 (対象 invocation の引数領域内か、
  # 別コマンドの領域か) に依らず常に deny する (契約 1 本化。 fallback 以降は解析不能な
  # ため、 挿入対象があっても書き換えを返さず、 コマンド文字列は原文のまま返す)。
  if [ "$OPAQUE" -eq 1 ]; then
    RW_DENY=1
    RW_CHANGED=0
    RW_OUT="$cmd"
    return
  fi

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

# `--draft=false` 等の明示的 非 draft 指定、 または未対応 heredoc delimiter 構文による
# opaque fallback (発動位置に依らず) は enforce 方針違反 / 解析不能として deny する。
if [ "$RW_DENY" -eq 1 ]; then
  jq -n '{
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "deny",
      "permissionDecisionReason": "enforce-draft-pr: `--draft=false` のように非 draft を明示する PR 作成、または未対応の heredoc delimiter 構文のため draft 指定を解析できないコマンドはこのプラグインの方針 (PR は必ず draft で起こす) で許可していません。`--draft=false` を外して draft PR を作成するか、heredoc の delimiter を単純な形 (`<<EOF` / `<<'"'"'EOF'"'"'` 等) に書き直すか、draft 強制が不要ならこのプラグインを無効化してください。"
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

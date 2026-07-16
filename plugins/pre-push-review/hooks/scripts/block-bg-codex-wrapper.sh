#!/bin/bash
# block-bg-codex-wrapper.sh
# `run-codex-review.sh` wrapper の background 起動、 および `pre-push-review:codex-reviewer`
# subagent 以外からの起動を deny する PreToolUse フック。
#
# policy: 本 hook 全体は fail-open (PreToolUse / defense-in-depth 補助) / agent_type gate と
#   segment 分類 (下記) は fail-closed
#   本 hook は補助的な regression 防御で、 真の push gate は block-pre-push.sh (= fail-closed)
#   が担う。 そのため jq 不在等の環境失敗時は silent に exit 0 で抜けて allow に倒す
#   (= 環境失敗で「合法な wrapper 起動」 が deny される false positive を避ける)。
#   検知ロジックに該当した場合のみ deny を返す。 真の保証は block-pre-push.sh が
#   marker hash check で行うため、 本 hook が抜けても未レビュー push は通らない。
#   ただし issue #267 で追加した agent_type gate と、 その後の codex review P2 指摘
#   (substring 一致だけでは無害な言及コマンドまで deny する regression があった) を
#   受けて追加した実行形 segment 分類は fail-closed (agent_type 欠落 = deny、 分類不能な
#   segment = 実行形扱い) で判定する。 jq 不在等の環境失敗時のみ本 hook 全体としての
#   fail-open (= 上の `command -v jq` チェック) に従う。
#
# ## なぜ必要か
#
# v1.1.0 で codex review は wrapper script (run-codex-review.sh) 経由に切替えた。当時は
# wrapper 自身が完了時に codex marker を書いていたが、v4.0.1 では review 開始時点の hash を
# pending attestation に書き、codex-reviewer の parent-safe report 完了後に auto-mark.sh が
# final marker へ昇格する設計へ変更した。それでも **wrapper を Bash tool の
# `run_in_background: true` で起動すると regression が発生する**:
#   - wrapper 内部の `node codex-companion.mjs review --wait --scope branch` は foreground で
#     完走するが、codex-reviewer subagent の Agent tool は先に完了しうる
#   - **codex-reviewer subagent が wrapper の stdout / stderr (= codex review の verdict /
#     findings) を観察できず、parent-safe report を正規化できない**。Bash tool は bg 起動の
#     場合 `BashOutput` で後追い取得する必要があるため、review cycle と Agent completion の
#     順序が分離される
#   - final marker は report 成功前には発行されないので push gate bypass にはならないが、
#     後から stale pending attestation だけが残り、review 完了を正しく配送できない
#
# v1.0.0 までは PreToolUse の `block-bg-codex-review.sh` が `run_in_background: true` を deny
# して同類の問題を防いでいたが、 v1.1.0 で Skill 経由 `/codex:review` 廃止に伴い不要として
# 削除した。 しかし wrapper を bg で起動するという新経路に対する gate が欠如していたため、
# 本 hook を再導入する。
#
# v3.0.0 で codex review は `pre-push-review:codex-reviewer` subagent 経由の起動に統一されたが、
# 当時は **メインセッションが wrapper を直接 Bash 実行しても同じく marker が書かれてしまい**、
# subagent 経由での起動は agents/codex-reviewer.md の指示文という prompt 規律だけで担保されて
# いた (issue #267)。 メインセッションによる直接実行は subagent が持つ context isolation
# (詳細出力を subagent context に閉じ込め、 親 session には report だけを返す設計) を毀損する。
# v4.0.0 で agent_type gate (下記) を追加し、 `pre-push-review:codex-reviewer` subagent 以外から
# の wrapper 起動を fail-closed に deny するようにした。v4.0.1 では direct wrapper が作れる
# のは pending attestation までで、対応する正規 Agent report がなければ final marker には
# ならないが、raw review output の親 context 流入と、不正な caller が残す pending artifact を
# 防ぐため caller gate は引き続き必要。
#
# その後の codex review (P2 指摘) で、 agent_type gate が command 文字列に
# `run-codex-review.sh` substring を含むだけで無条件に発火するため、 wrapper を実行せず
# 言及するだけの無害なコマンド (`cat` / `git diff` / `grep` 等) まで deny される regression が
# 見つかった。 これを受け、 gate の前段に「実行形 segment 分類」 を追加し、 実行形 segment が
# 1 つも無いコマンドは agent_type gate 自体を skip するようにした (下記「検知ロジック」参照)。
#
# ## 検知ロジック
#
# 0. **segment 分類** (実行形のみ gate、 fail-closed): command を `split_command` で
#    segment 分割し、 substring `run-codex-review.sh` を含む各 segment を次の 3 規則で
#    分類する。 **実行形 segment が 1 つも無ければ、 agent_type gate も bg / pipeline 判定も
#    skip して allow する** (= wrapper を実行せず言及するだけの read-only コマンドは deny
#    しない)。
#      - **規則 1 (実行形 + indirection、 最優先、 quote-aware)**: segment がコマンド置換
#        `$(` / バッククォート `` ` `` / プロセス置換 `<(` `>(` のいずれかを **quote 状態を
#        考慮した上で** 含む場合。 quote 状態 (single / double / escaped) を 1 文字ずつ
#        追跡し、 single quote 内の literal な `$(` 等 (例: `grep -n '$(' file` の監査
#        コマンド) は indirection と判定しない。 double quote 内は bash が実際に `$(...)`
#        / バッククォートを展開するため引き続き indirection、 escape (`\$(` 等) された
#        ものは非 indirection とする (詳細は `segment_has_indirection` 関数コメント参照)。
#        これらの内部は本 parser が安全に解析できないため保守的に実行形とし、 command 全体
#        の INDIRECTION フラグを立てる (下記 2. で使用)。
#      - **規則 2 (言及のみ)**: `tokenize_segment` + `skip_env_assignments` 後の先頭コマンド
#        token (`unquote_token` 適用) が、 exec 機能を持たない read-only allowlist
#        (`cat` `head` `tail` `nl` `wc` `grep` `diff` `cmp` `file` `stat` `ls` `md5sum`
#        `sha1sum` `shasum` `sha256sum` `uniq` `cut` `shellcheck`) に完全一致する場合。
#        `git` は特例で、 直後 token (global option を挟まない) が `diff` / `log` / `show` /
#        `status` / `ls-files` / `rev-parse` / `cat-file` のいずれかに完全一致し、 かつ
#        segment 内に `--ext-diff` / `--textconv` token が無い場合のみ言及扱いとする (直後
#        token が `-` 始まりの global option (`git -c ... diff` 等) の場合や、 上記以外の
#        subcommand (`difftool` 等) は実行形)。 `git grep` は `--open-files-in-pager
#        [=<cmd>]` / `-O<cmd>` option で外部プログラムを起動できるため、 git 特例の
#        subcommand 集合から除外した (単体コマンドの `grep` は上の allowlist に引き続き
#        含まれる。 `git log -- file | grep pattern` のように単体 `grep` へ差し替えて使う
#        こと)。 path 修飾形 (`/bin/cat` 等) は allowlist 文字列と完全一致しないため規則 3 へ
#        落ちる。 `sed` / `awk` / `find` / `rg` / `sort` / `less` / `more` / `xargs` は
#        子プロセス実行面 (GNU sed の `e` コマンド、 awk `system()`、 `find -exec`、
#        `rg --pre`、 `sort --compress-program`、 pager の shell escape) を持つため
#        allowlist に含めない。
#        **mention 扱い segment の pipe chain 検査**: 規則 2 で言及扱いに分類された segment
#        についても、 その segment が属する pipe chain (両方向に separator が `|` である限り
#        連続する segment の極大区間、 `&&` / `||` / `;` / `&` で途切れる。 `split_command`
#        の出力仕様上 SEPARATORS[i-1] が SEGMENTS[i] の直前、 SEPARATORS[i] が直後を指す)
#        内の他の全 segment (substring を含まないものも含む) が `mention_safe_segment`
#        (indirection 不在 + allowlist / git 特例一致) を満たすことを要求する
#        (`pipe_chain_all_mention_safe` 関数)。 隣接 1 段でなく chain 全体を見るのは、
#        `cat wrapper | head -100 | bash` のように allowlist コマンドを 1 段挟むと隣接判定
#        だけでは素通りするため (上流側 `bash gen.sh | grep -f - wrapper` も同様に保守的に
#        検査する)。 また `cat wrapper | grep "$(bash)"` のように allowlist head でも
#        コマンド置換の内側 (`bash`) が pipe の stdin (= wrapper 内容) を読んで実行できる
#        ため、 neighbor の indirection も同じ chain 走査で検査する。 chain 内に
#        mention-safe でない segment が 1 つでもあれば command 全体を実行形とする (下記 1.
#        の agent_type gate を発火させる。 該当 segment が indirection を含む場合は
#        INDIRECTION フラグも連動して立てる)。
#      - **規則 3 (実行形・既定)**: 上記 2 規則のいずれにも該当しない場合 (bash/sh 等
#        interpreter、 不明コマンド、 env 代入のみで先頭コマンド token が無い segment、
#        path 修飾された allowlist コマンド等)。 分類不能・想定外の形はすべてここに落ちる
#        fail-closed の既定分類。
# 1. **agent_type gate** (v4.0.0 / issue #267 / fail-closed): 実行形 segment が 1 つでも
#    ある場合のみ到達する。 command に `run-codex-review.sh` substring を含む場合、 hook
#    payload のトップレベル `agent_type` が `pre-push-review:codex-reviewer` に完全一致しな
#    ければ deny する (欠落・別値いずれも deny)。 一致した場合のみ後続の bg / pipeline 判定へ
#    進む。 **実機検証済み (Claude Code 2.1.211)**: メインセッションの Bash では `agent_type`
#    がペイロードに含まれず、 plugin subagent の Bash では namespace 付き
#    `pre-push-review:codex-reviewer` が届くことを確認した。
# 2. **bg / pipeline 検知** (従来ロジック + indirection 拡張): Bash tool option
#    `tool_input.run_in_background == true` の場合、 または wrapper を含む segment に
#    **隣接** する shell-level の `&` / `|` で連結している場合に deny する。 加えて、 上記 0.
#    の INDIRECTION フラグが立っている場合は、 隣接判定に加えて command 全体の separator を
#    **位置を問わず** 走査し、 単独 `&` / `|` が 1 つでもあれば deny する (コマンド置換で
#    wrapper 起動を隠した場合、 substring を含む segment と実際に wrapper を実行する segment
#    が別 segment になり得るため、 隣接判定だけでは
#    `WRAPPER=$(find ... run-codex-review.sh ...) && bash "$WRAPPER" | tee log` のような形を
#    素通りしてしまう)。 wrapper の起動は通常 `bash <abs-path>/run-codex-review.sh` の形
#    (deny メッセージで案内) なので、 path のどこかに `run-codex-review.sh` が現れる前提。
#    substring match なので、 ユーザ独自の wrapper alias (例: `bash my-codex.sh`) は対象外
#    (= cooperative 利用前提)。

# 予期せぬエラー時の診断 trap を install (実装は lib/exit-trap.sh)。
_PRE_PUSH_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/exit-trap.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/exit-trap.sh"
# cmd-parser.sh は line continuation 正規化 (`normalize_line_continuations`) に加え、
# segment 分類・bg / pipeline 判定で使う `split_command` / `tokenize_segment` /
# `skip_env_assignments` / `unquote_token` のため source する。 substring match の前に
# `\<LF>` を削除して隣接 token を連結することで、 `bash .../run-codex-revie\<LF>w.sh` の
# ような line continuation 経由の検知 bypass を塞ぐ (= block-pre-push.sh も同じ防御を行って
# いる)。
# shellcheck source=lib/cmd-parser.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/cmd-parser.sh"
install_exit_trap "block-bg-codex-wrapper" "run-codex-review wrapper の background 起動 deny が機能していない可能性があり、 codex-reviewer が review 結果を観察できず parent-safe report を正しく返せないかもしれません。"

INPUT=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# bash `$(...)` の trailing-LF trim で消えた `\<LF>` を復元 (詳細は cmd-parser.sh の
# 「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション)。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# 行継続 `\<改行>` を **削除** して隣接 token を連結する (bash 実挙動と一致)。 これを
# やらないと `bash .../run-codex-revie\<LF>w.sh` のような書き方で substring match
# (`run-codex-review.sh`) を bypass される経路が残る。 fast-path で line continuation を
# 含まない 99% の入力は `$(...)` fork を回避する (cmd-parser.sh 関数内に fast-path あり)。
case "$COMMAND" in
  *\\$'\n'*) COMMAND=$(normalize_line_continuations "$COMMAND") ;;
esac

# 粗フィルタ: command 文字列に `run-codex-review.sh` が含まれなければ即抜け (fork なし)。
case "$COMMAND" in
  *run-codex-review.sh*) ;;
  *) exit 0 ;;
esac

# `&` を含む shell redirection (`2>&1` / `&>file` / `<<EOF` 等) を空白に置換する。
# cmd-parser は `&` を一律 separator として扱うため、 redirection 内の `&` を parallel
# separator と誤認して false-positive deny を起こす経路を塞ぐ目的 (block-pre-push.sh と
# 同じ理由・同じ sed パターン)。 特に deny message が案内する
# `bash run-codex-review.sh > codex.log 2>&1` (= 推奨 logging 形式) を素通させるため必須。
# segment 分類 (下記) も同じ split を使うため、 split_command より前に済ませる。
COMMAND=$(printf '%s' "$COMMAND" \
  | sed -E 's/[0-9]?(&>>|&>|>>|>\&|<\&|<<<|<<|<>)[[:space:]]*[A-Za-z0-9_./=+@:-]*/ /g')

# cmd-parser の split_command で segment と separator を取る。 segment 分類・agent_type
# gate・bg / pipeline 判定のすべてがこの 1 回の split を再利用する。
SEGMENTS=()
SEPARATORS=()
while IFS= read -r line; do
  if [[ "$line" == SEP:* ]]; then
    SEPARATORS+=("${line#SEP:}")
    continue
  fi
  SEGMENTS+=("$line")
done < <(split_command "$COMMAND")

# segment_has_indirection <segment>
# 戻り値: 0 = quote-aware に indirection shape (コマンド置換 `$(` / バッククォート /
# プロセス置換 `<(` `>(`) を検出、 1 = 未検出。
#
# 規則 1 の生 substring 判定 (`*'$('*` 等) は、 引用符内の literal な文字列 (例:
# `grep -n '$(' run-codex-review.sh` のような wrapper 監査コマンド) まで indirection と
# 誤分類する false positive があった (codex review High 指摘)。 本関数は segment 文字列を
# 1 文字ずつ走査し、 in_single / in_double / (backslash による) escaped の状態を追跡して
# 以下のみを indirection と判定する:
#   - コマンド置換 `$(` : single quote 内でなく、 `$` が escape されていない場合
#     (double quote 内は bash が実際に展開するため indirection のまま)
#   - バッククォート `` ` `` : single quote 内でなく、 escape されていない場合
#     (double quote 内も展開されるため indirection)
#   - プロセス置換 `<(` / `>(` : single quote 内でも double quote 内でもなく、
#     escape されていない場合 (プロセス置換は unquoted の文脈でのみ有効なため)
# escape (`\`) は single quote 内では literal 文字として扱い (次の文字を escape しない)、
# それ以外 (unquoted / double quote 内) では次の 1 文字を escape して読み飛ばす。
# substring `run-codex-review.sh` を含む segment に対してのみ呼ばれる低頻度パスのため、
# 1 文字ループの性能コストは許容する (cmd-parser.sh の split_command / tokenize_segment と
# 同じ設計判断)。 bash 3.2 互換 (mapfile / declare -A / `${var,,}` を使わない)。
segment_has_indirection() {
  local seg="$1"
  local i=0 len=${#seg}
  local in_squote=0 in_dquote=0
  local c nc

  while [ "$i" -lt "$len" ]; do
    c="${seg:$i:1}"

    if [ "$in_squote" -eq 1 ]; then
      # single quote 内: `\` も含めすべて literal。 close quote のみ判定する。
      [ "$c" = "'" ] && in_squote=0
      i=$((i+1))
      continue
    fi

    if [ "$c" = "\\" ]; then
      # unquoted / double quote 内の `\` は次の 1 文字を escape して読み飛ばす
      # (escape された `$` / `` ` `` / `<` / `>` は indirection と判定しない)。
      i=$((i+2))
      continue
    fi

    if [ "$in_dquote" -eq 1 ]; then
      if [ "$c" = '"' ]; then
        in_dquote=0
        i=$((i+1))
        continue
      fi
      # double quote 内でも `$(` / バッククォートは bash が実際に展開するため indirection。
      nc="${seg:$((i+1)):1}"
      if [ "$c" = '$' ] && [ "$nc" = "(" ]; then
        return 0
      fi
      if [ "$c" = '`' ]; then
        return 0
      fi
      i=$((i+1))
      continue
    fi

    # unquoted 領域
    case "$c" in
      "'") in_squote=1; i=$((i+1)); continue ;;
      '"') in_dquote=1; i=$((i+1)); continue ;;
    esac
    nc="${seg:$((i+1)):1}"
    if [ "$c" = '$' ] && [ "$nc" = "(" ]; then
      return 0
    fi
    if [ "$c" = '`' ]; then
      return 0
    fi
    if [ "$c" = '<' ] && [ "$nc" = "(" ]; then
      return 0
    fi
    if [ "$c" = '>' ] && [ "$nc" = "(" ]; then
      return 0
    fi
    i=$((i+1))
  done

  return 1
}

# mention_safe_segment <segment>
# 戻り値: 0 = mention-safe (indirection を含まず、 かつ先頭コマンド token が read-only
#   allowlist または git 特例 (縮小 subcommand 集合) に完全一致)、 1 = mention-safe でない。
#
# 規則 2 の主 segment 判定と、 修正 1 の pipe chain 検査 (`pipe_chain_all_mention_safe`)
# の両方から呼ばれる共通 helper (ファイルヘッダ「検知ロジック」規則 2 節参照)。 先頭コマンド
# token が無い segment (env 代入のみ・空) は fail-closed で mention-safe でないとする。
mention_safe_segment() {
  local _ms_seg="$1"

  if segment_has_indirection "$_ms_seg"; then
    return 1
  fi

  local -a _ms_toks
  tokenize_segment "$_ms_seg" _ms_toks
  local _ms_idx=0
  local _ms_n=${#_ms_toks[@]}
  skip_env_assignments _ms_toks _ms_idx

  if [ "$_ms_idx" -ge "$_ms_n" ]; then
    unset _ms_toks
    return 1
  fi

  local _ms_head
  _ms_head="$(unquote_token "${_ms_toks[$_ms_idx]}")"

  case "$_ms_head" in
    cat|head|tail|nl|wc|grep|diff|cmp|file|stat|ls|md5sum|sha1sum|shasum|sha256sum|uniq|cut|shellcheck)
      unset _ms_toks
      return 0
      ;;
    git)
      # git 特例: 直後 token (global option を挟まない) が縮小 subcommand 集合に完全一致
      # し、 かつ segment 内に --ext-diff / --textconv token が無い場合のみ mention-safe。
      # `grep` は修正 2 (issue #267 codex review 指摘) で本集合から除外した (`git grep` の
      # `--open-files-in-pager[=<cmd>]` / `-O<cmd>` option で外部プログラムを起動できる
      # ため。 単体コマンドの `grep` は上の allowlist に引き続き含まれる)。
      local _ms_next_idx=$((_ms_idx+1))
      if [ "$_ms_next_idx" -lt "$_ms_n" ]; then
        local _ms_next
        _ms_next="$(unquote_token "${_ms_toks[$_ms_next_idx]}")"
        case "$_ms_next" in
          diff|log|show|status|ls-files|rev-parse|cat-file)
            local _ms_has_ext=0
            local _ms_gt _ms_gtu
            for _ms_gt in "${_ms_toks[@]}"; do
              _ms_gtu="$(unquote_token "$_ms_gt")"
              case "$_ms_gtu" in
                --ext-diff|--textconv) _ms_has_ext=1; break ;;
              esac
            done
            if [ "$_ms_has_ext" -eq 0 ]; then
              unset _ms_toks
              return 0
            fi
            ;;
        esac
      fi
      ;;
  esac

  unset _ms_toks
  return 1
}

# pipe_chain_all_mention_safe <segment_index>
# 修正 1 (issue #267 codex review 指摘): 規則 2 で言及扱いに分類された segment (SEGMENTS
# 配列の index で指定) が属する pipe chain — 両方向に separator が `|` である限り連続する
# segment の極大連続区間 (`&&` / `||` / `;` / `&` で途切れる) — を走査し、 当該 segment 以外
# の全 segment (substring `run-codex-review.sh` を含まないものも含む) が
# `mention_safe_segment` を満たすことを確認する。 1 つでも満たさない segment があれば 1
# (false) を返す。 その segment が `segment_has_indirection` にも該当する場合は、 呼び出し元
# (メインの分類 loop) が INDIRECTION フラグも連動して立てられるよう、 グローバル変数
# HAS_INDIRECTION を直接更新する (本 hook は関数を同一プロセス内で呼ぶ trusted script のため
# グローバル変更で問題ない)。
#
# なぜ隣接 1 段でなく chain 全体を見るか: `cat wrapper | head -100 | bash` のように allowlist
# コマンドを 1 段挟むと隣接判定だけでは素通りしてしまう (上流側 `bash gen.sh | grep -f -
# wrapper` も同様に保守的に検査する)。 また `cat wrapper | grep "$(bash)"` のように allowlist
# head でもコマンド置換の内側 (`bash`) が pipe の stdin (= wrapper 内容) を読んで実行できる
# ため、 neighbor の indirection も同じ chain 走査で検査する。
#
# 戻り値: 0 = chain 内の他 segment がすべて mention-safe、 1 = 1 つでも mention-safe でない
# segment がある。
pipe_chain_all_mention_safe() {
  local _pc_center="$1"
  local _pc_lo=$_pc_center
  local _pc_hi=$_pc_center

  # 左方向へ拡張: SEPARATORS[_pc_lo-1] (= SEGMENTS[_pc_lo] の直前 separator) が `|` である
  # 限り _pc_lo を減らす。
  while [ "$_pc_lo" -gt 0 ] && [ "${SEPARATORS[$((_pc_lo-1))]}" = "|" ]; do
    _pc_lo=$((_pc_lo-1))
  done

  # 右方向へ拡張: SEPARATORS[_pc_hi] (= SEGMENTS[_pc_hi] の直後 separator) が `|` である限り
  # _pc_hi を増やす。
  while [ "$_pc_hi" -lt "${#SEPARATORS[@]}" ] && [ "${SEPARATORS[$_pc_hi]}" = "|" ]; do
    _pc_hi=$((_pc_hi+1))
  done

  local _pc_j=$_pc_lo
  while [ "$_pc_j" -le "$_pc_hi" ]; do
    if [ "$_pc_j" -ne "$_pc_center" ]; then
      if ! mention_safe_segment "${SEGMENTS[$_pc_j]}"; then
        if segment_has_indirection "${SEGMENTS[$_pc_j]}"; then
          HAS_INDIRECTION=1
        fi
        return 1
      fi
    fi
    _pc_j=$((_pc_j+1))
  done

  return 0
}

# ---------------------------------------------------------------------------
# 0. segment 分類 (実行形のみ gate、 fail-closed)。 詳細はファイルヘッダ「検知ロジック」
#    節を参照。 HAS_EXEC_SEGMENT が 0 のままなら agent_type gate も bg / pipeline 判定も
#    skip して allow する。
# ---------------------------------------------------------------------------
HAS_EXEC_SEGMENT=0
HAS_INDIRECTION=0

for _cls_i in "${!SEGMENTS[@]}"; do
  _cls_seg="${SEGMENTS[$_cls_i]}"
  case "$_cls_seg" in
    *run-codex-review.sh*) ;;
    *) continue ;;
  esac

  # 規則 1 (実行形 + indirection、 最優先、 quote-aware): コマンド置換 `$(` / バッククォート
  # / プロセス置換 `<(` `>(` のいずれかを quote 状態を考慮して含む segment は、 実 target を
  # 本 parser が解析できないため保守的に実行形とし、 command 全体の INDIRECTION フラグを
  # 立てる。 single quote 内の literal な `$(` 等は indirection と判定しない
  # (`segment_has_indirection` 関数コメント参照)。
  if segment_has_indirection "$_cls_seg"; then
    HAS_EXEC_SEGMENT=1
    HAS_INDIRECTION=1
    continue
  fi

  # 規則 2 / 3: mention_safe_segment (indirection 不在 + read-only allowlist / git 特例
  # 一致) を満たせば言及のみ。 満たさなければ規則 3 (実行形・既定) の fail-closed 分類
  # (env 代入のみ・空 segment、 bash/sh 等 interpreter、 不明コマンド、 path 修飾された
  # allowlist コマンド等はすべて mention_safe_segment が 1 を返し、 ここに落ちる)。
  if mention_safe_segment "$_cls_seg"; then
    # 修正 1 (issue #267 codex review 指摘): 言及扱いでも、 この segment が属する pipe
    # chain 内の他 segment が全て mention-safe でなければ command 全体を実行形とする
    # (詳細はファイルヘッダ「mention 扱い segment の pipe chain 検査」節、 および
    # `pipe_chain_all_mention_safe` 関数コメント参照)。
    if ! pipe_chain_all_mention_safe "$_cls_i"; then
      HAS_EXEC_SEGMENT=1
    fi
  else
    HAS_EXEC_SEGMENT=1
  fi
done

# 実行形 segment が 1 つも無ければ、 agent_type gate も bg / pipeline 判定も skip して
# allow する (= wrapper を実行せず言及するだけの read-only コマンドを deny しない)。
if [ "$HAS_EXEC_SEGMENT" -eq 0 ]; then
  exit 0
fi

# agent_type 検証 gate (fail-closed): wrapper 起動を許可する呼び出し元は
# `pre-push-review:codex-reviewer` subagent のみ。 hook payload のトップレベル `agent_type`
# が完全一致しない場合 (欠落含む) は deny する。 一致した場合のみ後続の bg / pipeline 判定
# へ進む。 jq 不在時は既に上の `command -v jq` チェックで fail-open 済みのため、 ここに到達
# する時点で jq は利用可能。
AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')
if [ "$AGENT_TYPE" != "pre-push-review:codex-reviewer" ]; then
  if [ -z "$AGENT_TYPE" ]; then
    REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper は `pre-push-review:codex-reviewer` subagent 経由でのみ起動できます。

理由: 本 hook の payload に `agent_type` が含まれていません (欠落)。 これはメインセッションが wrapper を直接 Bash 実行した場合、 または `agent_type` を hook payload に含めない旧 Claude Code を使用している場合に発生します。

wrapper を実行せずファイル内容を確認したいだけなら、 `cat` / `git diff` / `grep` 等の read-only コマンドや Read / Grep tool を使ってください (それらは deny されません)。

対応:
  - `/pre-push-review:review` で 3 レビューを並列起動してください (推奨)
  - codex review 単独が必要な場合は Agent / Task tool で subagent_type="pre-push-review:codex-reviewer" を起動してください
  - 上記を行っても `agent_type` 欠落が解消しない場合は、 Claude Code を 2.1.211 以上へ更新してください (本 gate は Claude Code 2.1.211 で実機検証済みです)
EOF
)
  else
    REASON=$(cat <<EOF
プッシュ前レビューをブロックしました。 \`run-codex-review.sh\` wrapper は \`pre-push-review:codex-reviewer\` subagent 経由でのみ起動できます。

理由: 検出された \`agent_type\` は \`${AGENT_TYPE}\` で、 \`pre-push-review:codex-reviewer\` と一致しません。 メインセッションが wrapper を直接 Bash 実行した場合や、 \`agent_type\` を hook payload に含めない旧 Claude Code を使用している場合にも同様の deny になります。

wrapper を実行せずファイル内容を確認したいだけなら、 \`cat\` / \`git diff\` / \`grep\` 等の read-only コマンドや Read / Grep tool を使ってください (それらは deny されません)。

対応:
  - \`/pre-push-review:review\` で 3 レビューを並列起動してください (推奨)
  - codex review 単独が必要な場合は Agent / Task tool で subagent_type="pre-push-review:codex-reviewer" を起動してください
  - お使いの Claude Code が \`agent_type\` を正しく送信しない場合は 2.1.211 以上へ更新してください (本 gate は Claude Code 2.1.211 で実機検証済みです)
EOF
)
  fi
  jq -n --arg reason "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
fi

# 2 種類の bg 起動経路を検知する:
#   (1) Bash tool option `run_in_background: true`
#   (2) shell-level backgrounding (`bash run-codex-review.sh &`) や pipeline (`bash run-codex-review.sh | tee log`)
#       — Bash tool option は false だが shell が wrapper を bg / 並列起動して主 session が
#       review 結果を観察しない経路。 block-pre-push.sh も同じ理由で単独 `&` / `|` を deny
#       している (markers gate 検証後の race 経路も同型)。
RUN_IN_BG=$(printf '%s' "$INPUT" | jq -r '.tool_input.run_in_background // false')

# (2) shell-level backgrounding / pipeline の検知。 SEGMENTS / SEPARATORS は前段の segment
# 分類で既に計算済みのものを再利用する (二重 split を避ける)。 wrapper を含む segment の
# **隣接** (直前 / 直後) separator が `&` (background) または `|` (pipeline) のときだけ
# deny する。 SEPARATORS[i-1] が segment[i] の直前、 SEPARATORS[i] が segment[i] の直後を
# 指す (= split_command が segment と separator を交互に出力する仕様)。 wrapper と無関係な
# segment 間の `&` / `|` (例: `bash run-codex-review.sh && echo done | tee log`) は false
# positive にしない。 `&&` / `||` / `;` は逐次実行なので race にならず許容。
_SHELL_BG=0
for i in "${!SEGMENTS[@]}"; do
  case "${SEGMENTS[$i]}" in
    *run-codex-review.sh*) ;;
    *) continue ;;
  esac
  # 直前 separator (SEPARATORS[i-1], i==0 なら無し)
  if [ "$i" -gt 0 ]; then
    case "${SEPARATORS[$((i-1))]}" in
      "&"|"|") _SHELL_BG=1; break ;;
    esac
  fi
  # 直後 separator (SEPARATORS[i], 最後の segment なら無し)
  if [ "$i" -lt "${#SEPARATORS[@]}" ]; then
    case "${SEPARATORS[$i]}" in
      "&"|"|") _SHELL_BG=1; break ;;
    esac
  fi
done

# INDIRECTION フラグが立っている場合、 隣接判定だけでは不十分。 コマンド置換の内部に
# 隠れた wrapper 起動 (`WRAPPER=$(find ... run-codex-review.sh ...)`) と、 実際に wrapper を
# 実行する別 segment (`bash "$WRAPPER"`) は別 segment になり得るため、 隣接判定は
# substring を含む segment (前者) しか見ておらず、 実行 segment (後者) に隣接する `|` /
# `&` を見逃す。 そのため command 全体の SEPARATORS を **位置を問わず** 走査し、 単独
# `&` / `|` が 1 つでもあれば deny する。 例: `WRAPPER=$(find ... run-codex-review.sh ...) &&
# bash "$WRAPPER" | tee log` は隣接判定 (substring 含有 segment の直後は `&&`) だけでは
# 素通りするが、 本チェックが末尾の `|` を検出して deny する。
if [ "$HAS_INDIRECTION" -eq 1 ] && [ "$_SHELL_BG" -eq 0 ]; then
  for _sep in "${SEPARATORS[@]}"; do
    case "$_sep" in
      "&"|"|") _SHELL_BG=1; break ;;
    esac
  done
fi

# どちらの経路でもなければ allow。
if [ "$RUN_IN_BG" != "true" ] && [ "$_SHELL_BG" -eq 0 ]; then
  exit 0
fi

if [ "$RUN_IN_BG" = "true" ]; then
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper を `run_in_background: true` で起動することはできません。

理由: wrapper 自身は codex review を foreground で実行しますが、 Bash tool の `run_in_background: true` で起動すると **codex-reviewer subagent は wrapper の stdout / stderr (= codex review の verdict / findings) を観察できないまま完了しうる**ため、parent-safe report を正しく組み立てられません。v4.0.1 以降は wrapper が pending attestation を書いても、正規 report 成功前に final marker へ昇格しないため push gate bypass にはなりませんが、review cycle と report delivery が分離し、stale pending だけが残る不正な完了になります。

対応: `run_in_background: true` を使わず、 wrapper を plain foreground の単独コマンドとして再実行してください。 wrapper は内部で codex companion を `--wait` で foreground 起動するため、 Bash 呼び出し自体が review 完了まで block しますが、 これが本プラグインの想定する正しい使い方です (= review 結果を観察してから push 判断する)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-review:codex-reviewer` subagent を再起動してください。
EOF
)
elif [ "$HAS_INDIRECTION" -eq 1 ]; then
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper を shell-level の `&` (background) や `|` (pipeline) で起動することはできません。

理由: `bash run-codex-review.sh &` のような shell-level backgrounding、 `bash run-codex-review.sh | tee log` のような pipeline で wrapper を起動すると、 Bash tool option `run_in_background: false` で呼び出しても shell が wrapper を別 process で並列起動したり、output を別 process が変換したりするため、 **codex-reviewer subagent が wrapper の verdict / findings を観察しない / 不完全にしか観察しない** 経路ができます。pending attestation と schema 上の report だけが揃っても、report の根拠となる review output を完全に観察した保証がないため、foreground review 要件に反します。

本コマンドはコマンド置換 `$(...)` 等の間接実行 (indirection) を含むため、 `&` / `|` が wrapper 呼び出しの直前・直後に隣接していなくても **位置を問わず** deny しています (indirection 経由の実行は、 substring を含む segment と実際に wrapper を実行する segment の対応関係を本 parser が追跡できないため、 保守的に deny する必要があります)。

対応: `&` / `|` を外して wrapper を plain foreground の単独コマンドとして再実行するか、 `&&` (success-and) / `;` (sequential) で連結してください。 logging が必要なら file redirection (`bash run-codex-review.sh > codex.log 2>&1`) で代替できます (= wrapper 完了後に file を読めば review 結果を観察可能)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-review:codex-reviewer` subagent を再起動してください。
EOF
)
else
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper を shell-level の `&` (background) や `|` (pipeline) で起動することはできません。

理由: `bash run-codex-review.sh &` のような shell-level backgrounding、 `bash run-codex-review.sh | tee log` のような pipeline で wrapper を起動すると、 Bash tool option `run_in_background: false` で呼び出しても shell が wrapper を別 process で並列起動したり、output を別 process が変換したりするため、 **codex-reviewer subagent が wrapper の verdict / findings を観察しない / 不完全にしか観察しない** 経路ができます。pending attestation と schema 上の report だけが揃っても、report の根拠となる review output を完全に観察した保証がないため、foreground review 要件に反します。

対応: `&` / `|` を外して wrapper を plain foreground の単独コマンドとして再実行するか、 `&&` (success-and) / `;` (sequential) で連結してください。 logging が必要なら file redirection (`bash run-codex-review.sh > codex.log 2>&1`) で代替できます (= wrapper 完了後に file を読めば review 結果を観察可能)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-review:codex-reviewer` subagent を再起動してください。
EOF
)
fi

jq -n --arg reason "$REASON" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'

exit 0

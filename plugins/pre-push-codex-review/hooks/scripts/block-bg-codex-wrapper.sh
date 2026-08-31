#!/bin/bash
# block-bg-codex-wrapper.sh
# `run-pre-push-codex-review.sh` wrapper の background 起動、 および
# `pre-push-codex-review:codex-reviewer` subagent 以外からの起動を deny する PreToolUse
# フック。
#
# policy: 本 hook 全体は fail-open (PreToolUse / defense-in-depth 補助)。 jq 不在等の
#   環境失敗時は silent に exit 0 で抜けて allow に倒す (= 環境失敗で「合法な wrapper
#   起動」 が deny される false positive を避ける)。 一方 agent_type gate と
#   segment 分類 (下記「検知ロジック」節) は fail-closed で判定する。
#   (a) **fail-open 経路の限定**: 本 hook が意図的に allow へ倒す fail-open 経路は
#       「外部コマンド形の不明 head による引数・quoted 文字列としての言及」
#       (決定表 step 14、 例: `rg -n 'run-pre-push-codex-review.sh' dir/`) の
#       1 経路のみに限定される。 それ以外 (agent_type 欠落・不一致、 canonical 化
#       失敗、 word-shape / keyword / builtin superset 該当、 wrapper basename 一致
#       等) はすべて fail-closed (実行形扱い) で deny する。
#   (b) **cooperative 補助 gate であること**: 本 hook は adversarial な security
#       boundary ではなく、 cooperative 利用を前提とした補助 gate である。 任意の
#       未知 launcher (`frobnicate <wrapper>` 等) や動的にコマンド文字列を構築する
#       経路への完全性は保証しない (= 意図的に攻め切らない設計判断。 詳細は下記
#       「検知ロジック」節および `classify_wrapper_segment` の関数コメントの
#       14-step 決定表を参照)。
#   (c) **真の push gate は block-pre-push-codex.sh**: 本 hook が抜けても (= 上記 (a) の
#       fail-open 経路や jq 不在等の環境失敗経路)、 真の保証は block-pre-push-codex.sh が
#       marker hash check (= fail-closed) で行うため、 未レビュー push は
#       block-pre-push-codex.sh が別途ブロックする。
#
# ## なぜ必要か
#
# wrapper は完了時に review 開始時点の hash を pending attestation に書き、
# codex-reviewer の parent-safe report 完了後に auto-mark.sh が final marker へ昇格する。
# この設計でも **wrapper を Bash tool の `run_in_background: true` で起動すると
# regression が発生する**:
#   - wrapper 内部の `node codex-companion.mjs review --wait --scope branch` は foreground で
#     完走するが、codex-reviewer subagent の Agent tool は先に完了しうる
#   - **codex-reviewer subagent が wrapper の stdout / stderr (= codex review の verdict /
#     findings) を観察できず、parent-safe report を正規化できない**。Bash tool は bg 起動の
#     場合 `BashOutput` で後追い取得する必要があるため、review cycle と Agent completion の
#     順序が分離される
#   - final marker は report 成功前には発行されないので push gate bypass にはならないが、
#     後から stale pending attestation だけが残り、review 完了を正しく配送できない
#
# また、 メインセッションが wrapper を直接 Bash 実行すると、 subagent が持つ context
# isolation (詳細出力を subagent context に閉じ込め、 親 session には report だけを返す
# 設計) が毀損される。 direct wrapper 実行が作れるのは pending attestation までで、
# 対応する正規 Agent report がなければ final marker にはならないが、 raw review output の
# 親 context 流入と、 不正な caller が残す pending artifact を防ぐため caller gate
# (下記 1.) が必要になる。
#
# ## 検知ロジック
#
# 0. **segment 分類** (実行形のみ gate、 fail-closed): command を `split_command` で
#    segment 分割し、 substring `run-pre-push-codex-review.sh` を含む各 segment を次の規則で
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
#      - **規則 1.5 (解析可能性検査、 決定表 step 2)**: 規則 1 に該当しない segment に対し
#        `segment_is_unanalyzable` (戻り値 0 = 解析不能、 1 = 解析可能) を評価する。
#        `split_command` は quote 外の group delimiter (`(`/`)`/`{`/`}`) の depth が非 0 の
#        間、 separator 候補文字 (`;`/`|`/`&`/改行) を real separator ではなく segment 内の
#        literal として保持する仕様のため、 未対応の `(` 等があると本来複数コマンドになる
#        はずの入力が 1 segment に merge され、 head token が実コマンドを代表しなくなる
#        (例: `echo ( ; bash <wrapper>` は `echo` が harmless builtin のため誤って mention
#        扱いされかねない)。 quote 外に ANSI-C quoting の開始 (`$'`) が現れる場合も、 本検査
#        がその内部意味論を模さず quote 状態が乖離するため同様に解析不能とする。 解析不能
#        なら実行形とし `classify_wrapper_segment` の呼び出し自体を skip する (詳細は
#        `segment_is_unanalyzable` 関数コメント参照)。
#      - **規則 2/3 (executable 位置分類)**: 規則 1 / 1.5 に該当しない segment は
#        `classify_wrapper_segment` (戻り値 0 = 実行形、 1 = mention 候補) が判定する。
#        判定は「canonical token 値」 (single/double quote 除去・backslash escape 解決・
#        fragment 連結を行った shell word の静的な値。 `canonicalize_token` 関数参照) に
#        対して行う 14-step の順序付き決定表であり、 概略は次のとおり: 全 token が静的
#        literal であること (`token_is_unanalyzable`。 quote 外の `$`・brace
#        expansion・pathname expansion・tilde expansion は bash が parse 後に展開
#        するため、 これらを含む token は展開結果を静的に決定できず解析不能とする。
#        共有 tokenizer の quote 状態 desync で複数 word が 1 token に merge
#        され危険 option を隠す経路も同じ検査で塞ぐ。 検査は head と operand で
#        非対称で、 head は厳格、 operand は「token 先頭の fd 数字列 + `<`/`>`」 と
#        「固定開始を持つ token の glob と先頭 `~`」 の 2 点のみ許容する。 固定開始
#        (fixed start) とは「その token が word へ最初に寄与する literal 文字
#        (quote 除去・backslash escape 解決後の値の先頭文字) が `[A-Za-z0-9_/.]`
#        である」 か「raw token が `~/` で始まる」 ことを指し、 raw token の
#        1 文字目ではない (`'plugins'/*` は固定開始 `p` を持ち、 `'--pre'*` は
#        持たない) — pathname expansion が literal prefix を保存するため
#        展開結果が `-` 始まりの危険 option になり得ない、 という字句的不変条件に
#        基づく緩和であり位置推定ではない。 head 判定は launcher 剥がし後の実 head に
#        対して行う) /
#        leading 代入列 (NAME=VALUE 連鎖) の代入 slot が 1 つでも存在すれば値によらず
#        実行形 (GIT_EXTERNAL_DIFF / RIPGREP_CONFIG_PATH / LESSOPEN 等、 変数名列挙では
#        なく存在自体で判定) / head の canonical 化が失敗 (動的展開が残る) すれば実行形 /
#        echo・true・false・pwd・type は無害 builtin として mention 候補 (`test`・`[` は
#        算術評価の再評価面のため除外し builtin superset へ委ねる) / command・builtin・
#        env・timeout・nohup・nice・setsid・stdbuf・sudo・doas・time・`!` は launcher
#        prefix として限定解析で剥がして再評価 / 通常の外部コマンド word 形でない・bash
#        keyword・shell builtin static superset (`test`・`[` を含む) に該当すれば実行形 /
#        head の basename が wrapper 名または shell interpreter (bash/sh/dash/zsh/ksh)
#        に一致すれば実行形 / sed・awk・xargs・less・more・parallel、 および find の
#        -exec 系・rg の --pre 系 / --hostname-bin 系・sort の --compress-program 系
#        option (canonical token として存在) は実行形 / git は縮小 subcommand 集合
#        (diff/log/show/status/ls-files/rev-parse/cat-file) + --ext-diff・--textconv
#        不在の場合のみ mention 候補、 それ以外の git はすべて実行形 / ここまでで実行形と
#        確定しなかった head (無害 builtin・git 特例・外部コマンド形の不明 head) が
#        mention 候補、 という順序で評価する。 詳細な各 step の根拠・境界条件は
#        `classify_wrapper_segment` 関数コメントを参照する。
#        **mention 候補 segment の pipe chain 検査**: mention 候補と判定された segment に
#        ついても、 その segment が属する pipe chain (両方向に separator が `|` である限り
#        連続する segment の極大区間、 `&&` / `||` / `;` / `&` で途切れる。 `split_command`
#        の出力仕様上 SEPARATORS[i-1] が SEGMENTS[i] の直前、 SEPARATORS[i] が直後を指す)
#        内の他の全 segment (substring を含まないものも含む) が `mention_safe_segment`
#        (indirection 不在 + read-only allowlist / git 特例一致。 exact 判定のまま維持し
#        basename 正規化は適用しない — 中心 segment の判定との非対称は受容境界) を満たす
#        ことを要求する (`pipe_chain_all_mention_safe` 関数)。
#        隣接 1 段でなく chain 全体を見るのは、 `cat wrapper | head -100 | bash` のように
#        allowlist コマンドを 1 段挟むと隣接判定だけでは素通りするため (上流側 `bash
#        gen.sh | grep -f - wrapper` も同様に保守的に検査する)。 また `cat wrapper | grep
#        "$(bash)"` のように allowlist head でもコマンド置換の内側 (`bash`) が pipe の
#        stdin (= wrapper 内容) を読んで実行できるため、 neighbor の indirection も同じ
#        chain 走査で検査する。 chain 内に mention-safe でない segment が 1 つでもあれば
#        command 全体を実行形とする (下記 1. の agent_type gate を発火させる。 該当 segment
#        が indirection を含む場合は INDIRECTION フラグも連動して立てる)。
#      - **受容境界 (wrapper file への書き込み)**: redirection の書き込み先 (target) と
#        して wrapper path が現れる形 (`echo stub > <wrapper>` / `cat x >> <wrapper>` 等)
#        は operand として扱い、 mention 候補のままとする (= allow しうる)。 根拠は 3 点:
#        (1) 本 hook の責務は wrapper の **起動** を gate することに限定され、 wrapper
#        file への書き込みは対象外であること、 (2) 「wrapper への書き込みは deny される」
#        という不変条件はそもそも成立していないこと (`cat x > <wrapper>` /
#        `cat x >> <wrapper>` は read-only allowlist の head を持つため allow される)、
#        (3) Write / Edit tool 経由の書き込みは Bash tool 専用の本 hook からそもそも
#        観測できないこと。 wrapper file の完全性は本 hook の保証範囲外であり、 真の
#        push gate である `block-pre-push-codex.sh` の marker hash 検証が担う。
# 1. **agent_type gate** (fail-closed): 実行形 segment が 1 つでもある場合のみ到達する。
#    command に `run-pre-push-codex-review.sh` substring を含む場合、 hook payload の
#    トップレベル `agent_type` が `pre-push-codex-review:codex-reviewer` に完全一致しな
#    ければ deny する (欠落・別値いずれも deny)。 一致した場合のみ後続の bg / pipeline 判定へ
#    進む。 **実機検証済み (Claude Code 2.1.211)**: メインセッションの Bash では `agent_type`
#    がペイロードに含まれず、 plugin subagent の Bash では namespace 付きの
#    agent_type が届くことを確認した。
# 2. **bg / pipeline 検知**: Bash tool option `tool_input.run_in_background == true` の場合、
#    または wrapper を含む segment に **隣接** する shell-level の `&` / `|` で連結している
#    場合に deny する。 加えて、 上記 0. の INDIRECTION フラグが立っている場合は、 隣接判定に
#    加えて command 全体の separator を **位置を問わず** 走査し、 単独 `&` / `|` が 1 つでも
#    あれば deny する (コマンド置換で wrapper 起動を隠した場合、 substring を含む segment と
#    実際に wrapper を実行する segment が別 segment になり得るため、 隣接判定だけでは
#    `WRAPPER=$(find ... run-pre-push-codex-review.sh ...) && bash "$WRAPPER" | tee log` の
#    ような形を素通りしてしまう)。 wrapper の起動は通常
#    `bash <abs-path>/run-pre-push-codex-review.sh` の形 (deny メッセージで案内) なので、
#    path のどこかに `run-pre-push-codex-review.sh` が現れる前提。
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
install_exit_trap "block-bg-codex-wrapper" "run-pre-push-codex-review wrapper の background 起動 deny が機能していない可能性があり、 codex-reviewer が review 結果を観察できず parent-safe report を正しく返せないかもしれません。"

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
# (`run-pre-push-codex-review.sh`) を bypass される経路が残る。 fast-path で line continuation を
# 含まない 99% の入力は `$(...)` fork を回避する (cmd-parser.sh 関数内に fast-path あり)。
case "$COMMAND" in
  *\\$'\n'*) COMMAND=$(normalize_line_continuations "$COMMAND") ;;
esac

# 粗フィルタ: command 文字列に `run-pre-push-codex-review.sh` が含まれなければ即抜け (fork なし)。
case "$COMMAND" in
  *run-pre-push-codex-review.sh*) ;;
  *) exit 0 ;;
esac

# `&` を含む shell redirection (`2>&1` / `&>file` / `<<EOF` 等) を空白に置換する。
# cmd-parser は `&` を一律 separator として扱うため、 redirection 内の `&` を parallel
# separator と誤認して false-positive deny を起こす経路を塞ぐ目的 (block-pre-push.sh と
# 同じ理由・同じ sed パターン)。 特に deny message が案内する
# `bash run-pre-push-codex-review.sh > codex.log 2>&1` (= 推奨 logging 形式) を素通させるため必須。
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
# `grep -n '$(' run-pre-push-codex-review.sh` のような wrapper 監査コマンド) まで indirection と
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
# substring `run-pre-push-codex-review.sh` を含む segment に対してのみ呼ばれる低頻度パスのため、
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

# segment_is_unanalyzable <segment>
# 戻り値: 0 = 解析不能 (`split_command` の depth 追跡と実際の shell 意味論が乖離し、
#   複数コマンドが 1 segment に merge された可能性がある)、 1 = 解析可能。
#
# 決定表 step 2 (issue #339 2 巡目レビュー、 code P2: master 比の退行修正)。
# `split_command` (lib/cmd-parser.sh) は、 quote 外の group delimiter
# (`(`/`)`/`{`/`}`) の paren_depth / brace_depth が非 0 の間、 separator 候補文字
# (`;`/`|`/`&`/改行) を real separator として扱わず segment 内の literal として
# 保持する仕様である。 そのため、 未対応の `(` (例: `echo ( ; bash <wrapper>`。
# bash 上は syntax error になる形だが本 parser は構文検証をしない) のように depth
# が segment 末尾まで 0 に戻らない入力では、 本来複数コマンドとして扱われるべき
# 内容が 1 segment に merge され、 head だけを見る `classify_wrapper_segment` の
# 後続 step が merge 後の先頭語 (`echo` 等の無害 builtin) で誤って mention 扱いに
# 倒しかねない。 これを避けるため、 以下のいずれかを解析不能と判定する:
#   - segment 末尾で paren_depth または brace_depth が 0 に戻らない (unbalanced)
#   - quote 外の separator 候補文字 (`;`/`|`/`&`/改行) が segment 内に (深さを問わず)
#     現れる。 `split_command` の実装上、 これらの文字が 1 度でも出力 segment 内に
#     残っているなら、 それは元の入力で該当箇所が非 0 depth を通過した証跡であり
#     (depth 0 であれば `split_command` が real separator として分割済みのはず)、
#     bash 実行結果は 1 segment に留まらない可能性がある。 バランスの取れた
#     subshell / brace group (`(cd /tmp && bash <wrapper>)` 等) もこの条件で
#     解析不能と判定されるが、 それらは既存の word-shape 検査 (step 8、 head が
#     `(cd` 等の記号混じりの語になる) でも同じく実行形に確定するため、 最終判定は
#     変わらない (受容される保守化)。
# quote 状態の追跡・escape 処理は `segment_has_indirection` と同じ規約 (単純な
# in_squote / in_dquote トグル、 single quote 内の `\` は literal、 それ以外の `\`
# は次の 1 文字を escape して読み飛ばす) に従う。 bash 3.2 互換
# (mapfile / declare -A / `${var,,}` を使わない)。
#
# **ANSI-C quoting (`$'...'`) の検出 (issue #339 3 巡目レビュー、 code P2)**: 本関数
# (および split_command / segment_has_indirection 等の同系関数) は plain single
# quote (`'`) しか追跡せず、 ANSI-C quote 内の escape 意味論 (`\'` が quote を
# 終端しない等) を模さない。 そのため quote 外に `$'` が現れると、 以降の quote
# トグルが実際の bash 意味論と乖離し、 本来複数コマンドのはずの入力が誤って 1
# segment に merge される (例: `echo $'\''; bash <wrapper>` は `\'` を本関数が
# 「close quote」 と誤認し、 続く `'` を新たな open quote と誤認するため、 本来
# top-level の `;` が「quote 内」 と誤判定され split されない)。 これを避けるため、
# quote 外で `$` の直後に `'` が現れた時点で解析不能とする。
segment_is_unanalyzable() {
  local seg="$1"
  local i=0 len=${#seg}
  local in_squote=0 in_dquote=0
  local paren_depth=0 brace_depth=0
  local has_stray_separator=0
  local has_ansi_c_quote=0
  local c nc
  local nl=$'\n'

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
      # (escape された `(`/`)`/`{`/`}`/`;`/`|`/`&` は group delimiter / separator
      # として数えない)。
      i=$((i+2))
      continue
    fi

    if [ "$in_dquote" -eq 1 ]; then
      [ "$c" = '"' ] && in_dquote=0
      i=$((i+1))
      continue
    fi

    # unquoted 領域
    if [ "$c" = '$' ]; then
      nc="${seg:$((i+1)):1}"
      [ "$nc" = "'" ] && has_ansi_c_quote=1
    fi
    case "$c" in
      "'") in_squote=1; i=$((i+1)); continue ;;
      '"') in_dquote=1; i=$((i+1)); continue ;;
      '(') paren_depth=$((paren_depth+1)) ;;
      ')') paren_depth=$((paren_depth-1)) ;;
      '{') brace_depth=$((brace_depth+1)) ;;
      '}') brace_depth=$((brace_depth-1)) ;;
      ';'|'|'|'&') has_stray_separator=1 ;;
      "$nl") has_stray_separator=1 ;;
    esac
    i=$((i+1))
  done

  if [ "$paren_depth" -ne 0 ] || [ "$brace_depth" -ne 0 ] || [ "$has_stray_separator" -eq 1 ] \
    || [ "$has_ansi_c_quote" -eq 1 ]; then
    return 0
  fi
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
# の全 segment (substring `run-pre-push-codex-review.sh` を含まないものも含む) が
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

# dollar_starts_expansion <next_char>
# 戻り値: 0 = `$` + <next_char> が展開を開始する、 1 = `$` は literal。
#
# **この表が「`$` の直後 1 文字で展開が始まるか」 の単一の正本である**。
# `canonicalize_token` (double quote 分岐・unquoted 分岐) と
# `token_is_unanalyzable` の rule (d) はいずれもこの helper だけを参照し、
# 列挙をインラインで複製しない (以前は 3 箇所に複製されており、 旧算術展開
# `$[...]` の `[` が 1 箇所も入っていないという drift を実際に生んでいた)。
#
# 集合は bash の展開文法から導かれる**閉じた集合**である: 変数名の先頭文字
# (`[A-Za-z0-9_]`。 数字は位置パラメータ `$1` 等)、 `${...}` の `{`、
# コマンド置換 `$(...)` の `(`、 旧算術展開 `$[...]` の `[`、 特殊パラメータ
# `@` / `*` / `#` / `?` / `!` / `-` / `$`。 bash 3.2.57 と 5.2.21 の実機で
# double quote 内の `$` の直後に全 ASCII 記号を置いて走査し、 両者でこの 11
# 種のみが展開を開始する (それ以外は literal のまま) ことを確認した表である。
#
# `$'` (ANSI-C quoting) と `$"` (locale 翻訳) は含めない: double quote 内では
# `$'` の `'` が single quote 開始にならず `$` が literal になり、 `$"` は
# double quote の終端になるため、 いずれも展開を開始しないためである
# (quote 外に現れる `$'` / `$"` は `segment_is_unanalyzable` および
# `token_is_unanalyzable` の rule (c) が `$` の出現だけで一括して覆う)。
dollar_starts_expansion() {
  case "$1" in
    [A-Za-z0-9_]|'{'|'('|'['|'@'|'*'|'#'|'?'|'!'|'-'|'$') return 0 ;;
  esac
  return 1
}

# canonicalize_token <raw_token>
# stdout: canonical token 値 (single/double quote 除去・backslash escape 解決・
#   fragment 連結を行った、 shell word の静的な値)。
# 戻り値: 0 = canonical 化成功 (stdout に値)、 1 = 失敗 (single quote 外に解決不能な
#   `$` (動的展開、 例: `$VAR` / `${VAR}`) が残る。 コマンド置換 `$(` 等は
#   `segment_has_indirection` (規則 1) が本関数より先に検知済みのため、 ここに残る
#   `$` は素の変数展開のみ)。
#
# **backslash の意味論は quote 文脈で分岐する** (bash 実挙動 / lib/cmd-parser.sh
# の `tokenize_segment` / `split_command` の double quote 分岐と同じ意味論):
#   - unquoted (single quote 外): `\` は次の 1 文字を無条件に escape する
#     (`\x` → `x`)。
#   - single quote 内: `\` を含めすべて literal (escape 無効)。
#   - double quote 内: `\` は次の文字が `$` / バッククォート / `"` / `\` の
#     場合のみ escape として消費し、 それ以外 (例 `\-`) は `\` 自体を literal
#     として保持する (bash は `"\-\-pre"` を `\-\-pre` のまま解決し `--pre`
#     にはしない。 これを無条件 escape すると `"\-\-pre"` が誤って `--pre` に
#     一致してしまう false positive を生む)。 escaped backslash (`\\`) は 1 つの
#     `\` に collapse し、 その直後に現れる文字は改めて通常どおり評価する
#     (直後が escape されていない `$` なら動的展開として canonical 化失敗と
#     判定する。 `"\\$HOME"` の regression 修正: escape 判定の case pattern が
#     2 文字 arity (`'\\'`) で書かれており、 1 文字の backslash しか保持しない
#     `_ct_nc` とは一致し得なかった。 escape が常に不発になり `\\$HOME` を
#     静的解決不能な動的展開として検知できていなかったバグを修正した。
#     修正後の pattern は 1 文字 (`'\'`) で `_ct_nc` と正しく一致する)。
#
# **`$` 判定の精密化 (issue #339 3 巡目レビュー、 codex P2 may-defer 採用)**: `$`
# の直後の 1 文字が展開開始として有効な文字 (英数字 / `_` / `{` / `(` / `[` /
# `@` / `*` / `#` / `?` / `!` / `-` / `$`。 `$VAR` / `${...}` / `$(...)` /
# 旧算術展開 `$[...]` / `$@` / `$*` / `$#` / `$?` / `$!` / `$-` / `$$` に対応)
# の場合のみ動的展開として canonical 化失敗
# (`_ct_failed=1`) とする。 **この列挙は本関数内に複製せず、 単一の正本である
# `dollar_starts_expansion` helper を double quote 分岐・unquoted 分岐の
# 両方から呼ぶ** (`token_is_unanalyzable` の rule (d) も同じ helper を参照する
# ため、 3 箇所の列挙 drift が構造的に起きない。 5 巡目レビュー時点では `[` が
# 3 箇所すべてから欠落しており、 `"marker$[1]"` (bash 実挙動では `marker1` へ
# 展開される) を literal と誤認していた)。 それ以外 (token 末尾の `$`、 または `"marker$"` の
# ような正規表現の行末アンカーとして使われる literal な `$`) は展開を開始しない
# ため literal 文字として結果に含める。 コマンド置換 `$(` は `segment_has_indirection`
# (規則 1) が本関数より先に検知済みなので、 ここでの `$(` 判定は主に「規則 1 を
# すり抜けた single quote 内の `$(` を canonicalize_token 内で再度誤検知しない」
# ための一貫性維持であり、 実質的な検知は規則 1 が担う。
#
# **4 巡目レビューによる位置づけの変化 (静的 literal 検査、 決定表 step 3)**: quote
# 外の `$` (変数展開・`${...}`・`$(...)`・ANSI-C quoting `$'...'`・locale 翻訳
# quoting `$"..."`・旧算術展開 `$[...]` を含む) を持つ token は、 本関数より先に
# `token_is_unanalyzable` (step 3) が一律に解析不能 (実行形) と判定して落とすため、
# 本関数が実際に処理する `$` は **double quote 内の残余ケースのみ**になった
# (unquoted 領域の `$` 分岐は、 step 3 を通過した token には理論上到達しないが、
# 保険として残す実装は変更しない)。 double quote 内の `$` 判定・backslash の
# quote 別意味論はいずれも現行のまま維持する (詳細は `token_is_unanalyzable`
# 関数コメント参照)。
#
# **cmd-parser.sh の `unquote_token` との違い**: `unquote_token` はトークン両端の
# quote ペアを 1 段剥がすだけで、 `--'pre'` や `--'ext-diff'` のような fragment
# 分断形 (先頭 `--` が quote 外、 残りが quote 内という 1 token 内の quote 混在) を
# 解決できない (両端が quote で揃っていないため無変化のまま返る)。 本関数はトークン
# 全体を 1 文字ずつ走査し、 quote 区間をまたいで literal 文字を連結するため、
# `--'pre'` → `--pre` のように正しい canonical 値が得られる (issue #339 の
# `BlockBgCodexWrapperExecPositionClassificationTest` 決定表 step 5 / 12 / 13 で
# 使用)。 segment 全体ではなく個々の token (数十〜数百 byte) に対してのみ呼ぶため、
# `result+="$c"` の 1 文字ループの計算コストは許容する (cmd-parser.sh の
# `split_command` / `tokenize_segment` と同じ設計判断)。 bash 3.2 互換
# (mapfile / declare -A / `${var,,}` / nameref を使わない)。
#
# **lib/cmd-parser.sh の同型 arity 不一致 (修正済み)**: `tokenize_segment` /
# `split_command` の double quote 分岐にあった同型の case pattern arity 不一致
# (`'$'|'`'|'"'|'\\'` が 1 文字変数 `$nc` / `$c` に一致しない) は issue #354 で
# cmd-parser.sh 側も修正済み。 本 hook の検知層 (`token_is_unanalyzable` の
# rule (b) 等) は、 共有 parser (cmd-parser.sh) 側の同種の回帰に対する多重防御
# として引き続き維持する。
canonicalize_token() {
  local _ct_tok="$1"
  local _ct_i=0 _ct_len=${#_ct_tok}
  local _ct_in_squote=0 _ct_in_dquote=0
  local _ct_result="" _ct_c _ct_nc
  local _ct_failed=0

  while [ "$_ct_i" -lt "$_ct_len" ]; do
    _ct_c="${_ct_tok:$_ct_i:1}"

    if [ "$_ct_in_squote" -eq 1 ]; then
      # single quote 内: quote 終端のみ判定し、 それ以外 (`\` を含む) はすべて literal。
      if [ "$_ct_c" = "'" ]; then
        _ct_in_squote=0
      else
        _ct_result+="$_ct_c"
      fi
      _ct_i=$((_ct_i+1))
      continue
    fi

    if [ "$_ct_in_dquote" -eq 1 ]; then
      if [ "$_ct_c" = '"' ]; then
        _ct_in_dquote=0
        _ct_i=$((_ct_i+1))
        continue
      fi
      if [ "$_ct_c" = "\\" ]; then
        # double quote 内の `\` は、 次の文字が `$` / バッククォート / `"` /
        # `\` の場合のみ escape として消費する (bash の実意味論)。 それ以外は
        # `\` 自体を literal として結果に残し、 次の文字は改めて通常どおり
        # 処理する (2 文字纏めて消費しない)。
        _ct_nc="${_ct_tok:$((_ct_i+1)):1}"
        case "$_ct_nc" in
          '$'|'`'|'"'|'\')
            _ct_result+="$_ct_nc"
            _ct_i=$((_ct_i+2))
            continue
            ;;
        esac
        _ct_result+="$_ct_c"
        _ct_i=$((_ct_i+1))
        continue
      fi
      if [ "$_ct_c" = '$' ]; then
        # `$` 直後が展開開始として有効な文字の場合のみ動的展開 (canonical 化
        # 失敗) とする。 判定表は `dollar_starts_expansion` に単一化されている
        # (詳細は関数コメント「`$` 判定の精密化」節および同 helper 参照)。
        _ct_nc="${_ct_tok:$((_ct_i+1)):1}"
        if dollar_starts_expansion "$_ct_nc"; then
          _ct_failed=1
        fi
      fi
      _ct_result+="$_ct_c"
      _ct_i=$((_ct_i+1))
      continue
    fi

    # unquoted 領域
    if [ "$_ct_c" = "\\" ]; then
      # unquoted (single quote 外) の `\` は次の 1 文字を無条件に escape する
      # (`\x` → `x`)。 token 末尾の孤立した `\` (次の文字が無い) は `\` 自体を
      # literal として残す。
      _ct_nc="${_ct_tok:$((_ct_i+1)):1}"
      if [ -z "$_ct_nc" ]; then
        _ct_result+="$_ct_c"
        _ct_i=$((_ct_i+1))
      else
        _ct_result+="$_ct_nc"
        _ct_i=$((_ct_i+2))
      fi
      continue
    fi

    if [ "$_ct_c" = '$' ]; then
      # 同上 (double quote 内と同じ `dollar_starts_expansion` の表を参照)。
      _ct_nc="${_ct_tok:$((_ct_i+1)):1}"
      if dollar_starts_expansion "$_ct_nc"; then
        _ct_failed=1
      fi
    fi
    case "$_ct_c" in
      "'") _ct_in_squote=1; _ct_i=$((_ct_i+1)); continue ;;
      '"') _ct_in_dquote=1; _ct_i=$((_ct_i+1)); continue ;;
    esac
    _ct_result+="$_ct_c"
    _ct_i=$((_ct_i+1))
  done

  if [ "$_ct_failed" -eq 1 ]; then
    return 1
  fi
  printf '%s' "$_ct_result"
  return 0
}

# note_literal_contribution <literal_char>
# `token_is_unanalyzable` の走査ループ専用の記録 helper。 引数はその位置で
# **word へ literal として寄与する文字** (quote 除去・backslash escape 解決を
# 反映した値の 1 文字) であり、 最初の 1 文字だけを見て「固定開始 (fixed
# start) を持つか」 を確定する。 2 文字目以降の呼び出しは何もしない。
#
# 呼び出し元の局所変数 `_tw_first_seen` / `_tw_fixed_start` を bash の動的
# スコープ経由で更新する。 呼び出し元は `token_is_unanalyzable` の走査ループ
# 1 箇所だけで再帰も並行呼び出しも無いため、 状態を引数と戻り値で往復させる
# より意図が読み取りやすい。 本 helper は quote 状態を一切持たない (第 2 の
# quote 状態機械を作らないための設計。 #355 の重複指摘参照)。
note_literal_contribution() {
  [ "$_tw_first_seen" -eq 0 ] || return 0
  _tw_first_seen=1
  case "$1" in
    [A-Za-z0-9_/.]) _tw_fixed_start=1 ;;
  esac
}

# token_is_unanalyzable <raw_token> <position_kind>
# <position_kind>: `head` = 実行面そのものを決める token (厳格判定)、
#   `operand` = head の実行面に一切影響しない token (rule (c) を 2 点緩和)。
#   `head` 以外の未知値は緩和しない (fail-closed 側の既定)。
# 戻り値: 0 = 解析不能 (静的 literal ではない、 または quote 状態 desync の疑い)、
#   1 = 静的 literal として解析可能。
#
# 決定表 step 3 (issue #339 3/4/5 巡目レビュー、 codex P1 + security P2、 全て同根)。
#
# **なぜ「静的 literal であること」を積極要件にするか**: bash は quote 外の
# `$` (変数展開・コマンド置換・各種 quoting)・brace expansion・pathname
# expansion (glob)・tilde expansion を **parse 後、実際にコマンドへ渡す前** に
# 展開する。 そのため、 これらの構文を含む token の raw 文字列は、 実際にコマンド
# へ渡される引数と一致する保証が無い (例: `{--pre=bash,--pre=bash}` は brace
# expansion 後 `--pre=bash` という別 token に展開されるが、 raw 文字列だけを見て
# `--pre` との exact / prefix 一致を判定する `canonicalize_token` はこれを見抜け
# ない)。 3 巡目で `$` の直後文字を精査する緩和を入れたが、 これは bash が `$` を
# 消費する他の quoting form (`$"..."` locale 翻訳、 `$[...]` 旧算術展開) を
# 取りこぼし、 さらに brace / glob / tilde expansion も未対応だった。 個別の
# 構文を列挙して塞ぐのではなく、 **mention 判定の材料にしてよい token は
# 「静的に決定できる literal 値である」 という積極要件で一般化する** (これに
# 該当しない token は保守的に解析不能 = 実行形とする)。
#
# 本関数は raw token 文字列を quote 意味論 (single quote 内は escape 無効、
# double quote 内は `$`/バッククォート/`"`/`\` の前でのみ escape、 それ以外は
# unquoted として次の 1 文字を無条件 escape。 `canonicalize_token` と同じ規約)
# で 1 文字ずつ走査し、 次のいずれかに該当すれば解析不能 (実行形) とする:
#   (a) 走査終了時に single/double quote が閉じていない
#   (b) quote 外 (unquoted) の空白文字が現れる (= 本来 tokenize_segment が
#       ここで token を区切っているはずなのに 1 token に残っている。 共有
#       tokenizer の quote 状態 desync (#354) の証跡)
#   (c) quote 外に展開・置換・word 生成を導入する文字が現れる: `$` (変数展開・
#       `${...}`・コマンド置換 `$(...)`・ANSI-C quoting `$'...'`・locale 翻訳
#       quoting `$"..."`・旧算術展開 `$[...]` を、 直後の文字を見ずに `$` の
#       出現だけで一括して覆う)、 バッククォート、 brace expansion の `{`/`}`、
#       pathname expansion の `*`/`?`/`[`/`]`、 token 先頭の `~`
#       (tilde expansion)、 `(`/`)`/`<`/`>` 等の shell 構文文字
#   (d) double quote 内に、 展開開始として有効な文字 (`dollar_starts_expansion`
#       の 11 種: 英数字/`_`/`{`/`(`/`[`/`@`/`*`/`#`/`?`/`!`/`-`/`$`) が続く
#       `$`、 または escape されていないバッククォートが現れる
# quote 外の特殊文字が `\` で escape されている場合は「現れた」ことにならない
# ため rule (c) の対象外とする (unquoted の `\` は次の 1 文字を無条件 escape
# して読み飛ばすため、 escape pair は 2 文字纏めて消費し個別の文字判定に到達
# しない)。 double quote 内で展開開始が続かない `$` (正規表現の行末アンカー
# `"marker$"` 等) と、 quote 内の glob / brace 文字 (`'a*b'` 等) は bash が
# 展開しないため literal として許容する (対応する allow テストあり)。
#
# **head / operand の非対称 (issue #339 5 巡目レビュー、 code P1)**: 4 巡目
# 時点では rule (c) を token の位置に依らず適用していたため、 head の実行面に
# 一切影響しない operand 位置の token でも実行形 (deny) に倒れ、 origin/master
# では allow だった read-only 形 (`cat <path>/run-pre-push-codex-review.sh > out.txt`、
# `cat plugins/*/hooks/scripts/run-pre-push-codex-review.sh`、
# `cat ~/.claude/plugins/cache/.../run-pre-push-codex-review.sh`、
# `wc -l ~/x/run-pre-push-codex-review.sh`) が退行していた。 とくに `~/` 始まりの
# plugin-cache path は installed wrapper を参照する正規の書き方であり、
# issue #339 が解消対象としている false positive クラスそのものである
# (deny メッセージが「read-only コマンドは deny されない」 と案内するため、
# 自走 agent が誤診して同じ形を再試行する二次リスクもある)。 そこで
# <position_kind> = `operand` の場合に限り rule (c) を次の 2 点だけ緩和する
# (rule (a)/(b)/(d) は operand でも不変):
#   - **緩和 1 (先頭 redirection)**: quote 外の `<` / `>` が、 その文字より
#     前の文字がすべて 10 進数字 (0 文字も可、 = token 先頭) である位置に
#     現れる場合は解析不能の理由にしない。 この token が実 argv へ渡しうる
#     word は「無し」 か「全数字の word 1 つ」 のいずれかに限られ、 どちらも
#     `-` 始まりである列挙済み危険 option (`--pre` 等) にはなり得ないため
#     である。 内訳: fd 番号が実装上の上限内なら token 全体が redirection と
#     して消費され argv word は生じない。 上限を超える数字列 (実測では
#     bash 5.2.21 の `2147483648>tgt` が argv `[2147483648] [ARG]` を生む。
#     bash 3.2.57 は同形を "file descriptor out of range" として redirection
#     のまま扱いエラーにする) では、 bash は数字列を通常の word として渡すが、
#     漏れる word は必ず全数字である。
#     語の途中に現れる `<` / `>` では、 bash は演算子より前の prefix だけを
#     argv word として渡し、 残りを redirection の書き込み先にする
#     (実測でも bash 5.2.21 / 3.2.57 の双方が `--pre>x ARG` から argv
#     `[--pre] [ARG]` を生む)。 したがって canonical 値 `--pre>x` は実 argv の
#     `--pre` と乖離する (#353 と同根)。 この乖離が判定を誤らせるのは argv
#     word 側が危険 option になり得る場合だけなので、 語中の `<` / `>` は
#     **その token が既に固定開始 (下記 緩和 2 の定義) を持つ場合に限り**
#     許容する (緩和 1b)。 `<path>/run-pre-push-codex-review.sh>out.txt` の argv word は
#     `<path>/run-pre-push-codex-review.sh` で非 `-` 始まりなので安全である一方、
#     `--pre>x` / `-'-pre'>x` は最初の寄与文字が `-` で固定開始を持たないため
#     従来どおり解析不能とする。 書き込み先が `-` 始まり
#     (`<path>/run-pre-push-codex-review.sh>--pre`) でも argv word には現れないため
#     判定に影響しない。
#   - **緩和 2 (固定開始を持つ token の pathname / tilde expansion)**: token が
#     **固定開始 (fixed start)** — その token が **word へ最初に寄与する
#     literal 文字** (quote 除去・backslash escape 解決を考慮した値の先頭
#     文字) が `[A-Za-z0-9_/.]` のいずれかであるか、 raw token が `~/` の
#     2 文字で始まる — を持つ場合に限り、 quote 外の glob 文字 `*` / `?` /
#     `[` / `]` と token 先頭の `~` を解析不能の理由にしない。 判定対象が
#     raw token の 1 文字目ではないのは、 quote された literal な path 断片の
#     後に unquoted な glob が続く形 (`'plugins'/*/<wrapper>` /
#     `"plugins"/*/<wrapper>` / `\plugins/*/<wrapper>`) が実際には固定開始
#     `p` を持つのに、 raw の 1 文字目 (`'` / `"` / `\`) だけを見ると
#     取りこぼすためである (origin/master では allow だった read-only 形が
#     deny に退行していた)。 逆に `'--pre'*` / `"--pre"*` / `'-'-pre*` は
#     最初の寄与文字が `-` であるため固定開始を持たず、 従来どおり解析不能
#     とする。 quote 区切りの `'` / `"`、 緩和 1 で許容した token 先頭の
#     `<` / `>`、 緩和 2 で許容した token 先頭の `~` は word へ literal
#     文字を寄与しないため固定開始の判定に算入しない (`~/x` では続く `/` が
#     最初の寄与文字になる)。 glob 文字自身が最初の寄与位置になる token
#     (`*.sh` / `[a]x/<wrapper>`) も固定開始を持たない。 固定開始を持たない
#     token (`-` 始まり、 `*` 始まり、 `[` 始まり、 `~` 単独や `~name` 形、
#     `<`/`>` 始まり等) では従来どおり解析不能とする。 `{`/`}` (brace
#     expansion)・`(`/`)`・`$`・バッククォートは operand でも緩和しない。
#     固定開始の判定は**走査ループの中で遅延的に**行う (`_tw_first_seen` /
#     `_tw_fixed_start` を `note_literal_contribution` が更新する)。 事前に
#     raw token を別途走査して判定すると、 quote 意味論を扱う第 2 の状態機械
#     を持つことになり #355 が指摘する重複が悪化するためである。
# 緩和の根拠は「この token は operand のはずだ」 という位置推定ではなく
# **字句的不変条件**である: pathname expansion は最初の glob メタ文字より前の
# literal prefix を必ず保存するため、 固定開始 `[A-Za-z0-9_/.]` を持つ token の
# 展開結果はすべて同じ非 `-` 文字で始まり、 `-` 始まりである列挙済み危険 option
# (`--pre` / `--pre=` / `--hostname-bin` / `--hostname-bin=` /
# `--compress-program` の `--co` prefix / `-exec` 系 / `--ext-diff` /
# `--textconv`) のいずれにもなり得ない。 brace expansion は literal prefix を
# 保存しない (`{--pre,x}` が `--pre` を生む) ため緩和対象から外す。 `~/` は
# `$HOME` + `/` + 残りへ展開されるため展開結果は必ず `/` を含む path 形であり、
# exact 一致の危険 option とは一致しない。 prefix 一致の option については、
# `$HOME` が `-` 始まりの異常環境でも一致方向 (= deny 方向) にしか働かないため
# bypass にはならない。
#
# `classify_wrapper_segment` は step 3 の一括検査で全 token を `operand` として
# 呼び (merge された token・展開構文を含む token のいずれも option 位置に限らず
# operand 位置にも現れうるため)、 決定表 step 4 (leading 代入列) 以降より前に
# 実施する。 `head` 判定は静的な位置推定を行わず、 step 5〜7 の launcher 剥がし
# loop の中で「その時点で実際に head になった token」 に対して canonical 化の
# 直前に 1 回ずつ呼ぶ (`env cat ~/x/<wrapper>` なら剥がし後の `cat`)。 共有
# tokenizer 側の quote 状態 desync の根本原因そのものは #354 で追跡中であり、
# 本関数はその症状 (b) を検知する防御層の 1 つにすぎない。
#
# **platform caveat (bash 3.2 系では tilde 規則が発火しない)**: 共有 tokenizer
# (`lib/cmd-parser.sh` の `tokenize_segment`) は結果配列を `printf '%q'` +
# `eval` で書き戻す。 bash 3.2.57 の `printf '%q'` は先頭 `~` を escape せず
# (bash 5.2.21 は `\~` にする)、 書き戻しの `eval` で tilde expansion が起きる
# ため、 bash 3.2 系 (macOS の system bash) では `~` を含む token が展開済み
# path として本関数に届く。 結果として rule (c) の「token 先頭の `~`」 も
# 緩和 2 の `~/` 分岐も bash 3.2 では発火しない。 判定差は緩和方向にのみ生じ
# (`cat ~root/<wrapper>` が bash 5 では実行形、 bash 3.2 では mention 候補)、
# 展開後 path でも basename 判定は機能するため wrapper 起動・interpreter
# 起動・script 実行面コマンドは両者とも実行形のままである (bypass ではない)。
# 根本原因は本 hook の変更スコープ外 (共有 parser) のため #356 で追跡する。
# bash 3.2 互換 (mapfile / declare -A / `${var,,}` を使わない)。
token_is_unanalyzable() {
  local _tw_tok="$1"
  local _tw_pos="$2"
  local _tw_i=0 _tw_len=${#_tw_tok}
  local _tw_in_squote=0 _tw_in_dquote=0
  local _tw_c _tw_nc
  local _tw_relax=0 _tw_first_seen=0 _tw_fixed_start=0

  if [ "$_tw_pos" = "operand" ]; then
    # operand 位置のみ rule (c) を 2 点緩和する (head は従来どおり厳格。
    # 詳細は関数コメント「head / operand の非対称」節参照)。 `head` 以外の
    # 未知の値が渡された場合も緩和しない (fail-closed 側の既定)。
    _tw_relax=1
  fi

  while [ "$_tw_i" -lt "$_tw_len" ]; do
    _tw_c="${_tw_tok:$_tw_i:1}"

    if [ "$_tw_in_squote" -eq 1 ]; then
      # single quote 内: quote 終端のみ判定し、 それ以外はすべて literal
      # (展開・置換の対象にならないため rule (c)/(d) は適用しない)。 終端
      # `'` 以外の全文字が word への寄与文字である (quote 内の空白も含む)。
      if [ "$_tw_c" = "'" ]; then
        _tw_in_squote=0
      else
        note_literal_contribution "$_tw_c"
      fi
      _tw_i=$((_tw_i+1))
      continue
    fi

    if [ "$_tw_in_dquote" -eq 1 ]; then
      if [ "$_tw_c" = '"' ]; then
        _tw_in_dquote=0
        _tw_i=$((_tw_i+1))
        continue
      fi
      if [ "$_tw_c" = "\\" ]; then
        _tw_nc="${_tw_tok:$((_tw_i+1)):1}"
        case "$_tw_nc" in
          '$'|'`'|'"'|'\')
            # escape pair: word へ寄与するのは escape された側の文字。
            note_literal_contribution "$_tw_nc"
            _tw_i=$((_tw_i+2))
            continue
            ;;
        esac
        # escape として消費されない `\` は double quote 内では literal で
        # あり、 `\` 自身が word へ寄与する。
        note_literal_contribution "$_tw_c"
        _tw_i=$((_tw_i+1))
        continue
      fi
      if [ "$_tw_c" = '`' ]; then
        # rule (d): escape されていないバッククォート。
        return 0
      fi
      if [ "$_tw_c" = '$' ]; then
        # rule (d): 展開開始として有効な文字が続く `$` (判定表は
        # `dollar_starts_expansion` に単一化。 `$[` の旧算術展開も含む)。
        _tw_nc="${_tw_tok:$((_tw_i+1)):1}"
        if dollar_starts_expansion "$_tw_nc"; then
          return 0
        fi
      fi
      # 展開を開始しない `$` (行末アンカー等) とその他の文字は literal と
      # して word へ寄与する。
      note_literal_contribution "$_tw_c"
      _tw_i=$((_tw_i+1))
      continue
    fi

    # unquoted 領域。
    if [[ "$_tw_c" == [[:space:]] ]]; then
      # rule (b): quote 外の空白 (quote 状態 desync の証跡)。
      return 0
    fi

    if [ "$_tw_c" = "\\" ]; then
      # unquoted の `\` は次の 1 文字を無条件に escape する。 escape された
      # 展開開始文字は rule (c) の対象外 (2 文字纏めて読み飛ばす)。 word へ
      # 寄与するのは escape された側の文字であり (`\p` なら `p`)、 次の文字が
      # 無い token 末尾の孤立 `\` では `\` 自身が寄与する。
      _tw_nc="${_tw_tok:$((_tw_i+1)):1}"
      if [ -n "$_tw_nc" ]; then
        note_literal_contribution "$_tw_nc"
      else
        note_literal_contribution "$_tw_c"
      fi
      _tw_i=$((_tw_i+2))
      continue
    fi

    if [ "$_tw_i" -eq 0 ] && [ "$_tw_c" = '~' ]; then
      # 緩和 2 の `~/` 分岐だけは raw token の 2 文字目を直接見る。 固定開始は
      # 走査が進むまで確定しない遅延判定であり、 token 先頭の `~` に到達した
      # 時点ではまだ寄与文字が 1 つも無いためである。
      if [ "$_tw_relax" -eq 1 ] && [ "${_tw_tok:1:1}" = "/" ]; then
        # 緩和 2 (operand かつ `~/` 始まり): `$HOME` + `/` + 残りへ展開
        # されるため、 展開結果は必ず `/` を含む path 形になり、 exact 一致の
        # 危険 option とは一致しない (prefix 一致の option も deny 方向にしか
        # 働かない)。 `~` 自身は word へ literal 文字を寄与しないため記録
        # せず、 続く `/` が最初の寄与文字になる。
        _tw_i=$((_tw_i+1))
        continue
      fi
      # rule (c): token 先頭の tilde expansion。
      return 0
    fi

    case "$_tw_c" in
      "'") _tw_in_squote=1; _tw_i=$((_tw_i+1)); continue ;;
      '"') _tw_in_dquote=1; _tw_i=$((_tw_i+1)); continue ;;
      '<'|'>')
        if [ "$_tw_relax" -eq 1 ]; then
          # 緩和 1 (operand): 直前の文字がすべて 10 進数字 (0 文字も可、
          # = token 先頭) なら、 その `<` / `>` は fd 数字列を前置した
          # redirection である。 この token が実 argv へ渡しうる word は
          # 「無し」 か「全数字の word 1 つ」 (fd 番号が実装上限を超える場合。
          # 関数コメント緩和 1 の実測参照) に限られ、 どちらも `-` 始まりの
          # 危険 option にはなり得ない。 redirection 演算子は word へ literal
          # 文字を寄与しないため固定開始の判定には算入しないが、 その後ろの
          # 文字は通常どおり寄与する (`>out*` は固定開始 `o` を持つため
          # 緩和 2 の経路でも許容される。 argv word を生まない token なので
          # どちらの経路でも危険 option にはなり得ない)。
          case "${_tw_tok:0:$_tw_i}" in
            *[!0-9]*) ;;
            *) _tw_i=$((_tw_i+1)); continue ;;
          esac
          if [ "$_tw_fixed_start" -eq 1 ]; then
            # 緩和 1b (operand かつ固定開始): 語中の `<` / `>` であっても、
            # bash が argv word として渡すのはこの演算子より前の prefix だけで
            # ある (`<path>/run-pre-push-codex-review.sh>out.txt` なら
            # `<path>/run-pre-push-codex-review.sh`)。 その prefix が固定開始を持つなら
            # 展開結果は非 `-` 文字で始まり、 `-` 始まりの列挙済み危険 option に
            # なり得ないため、 canonical 値との乖離が判定を誤らせない。
            # `--pre>x` は最初の寄与文字が `-` で固定開始を持たないため、
            # 従来どおりこの分岐に入らず解析不能 (実行形) となる。
            _tw_i=$((_tw_i+1))
            continue
          fi
        fi
        # rule (c): 語中の `<` / `>` (bash は `--pre>x` を argv word `--pre` と
        # redirection に分割するため、 canonical 値が実 argv と乖離する)。
        return 0
        ;;
      '*'|'?'|'['|']')
        if [ "$_tw_relax" -eq 1 ] && [ "$_tw_first_seen" -eq 1 ] && [ "$_tw_fixed_start" -eq 1 ]; then
          # 緩和 2 (operand かつ固定開始): pathname expansion は最初の glob
          # メタ文字より前の literal prefix を必ず保存するため、 固定開始
          # `[A-Za-z0-9_/.]` を持つ token の展開結果はすべて同じ非 `-` 文字で
          # 始まり、 `-` 始まりの列挙済み危険 option になり得ない。
          _tw_i=$((_tw_i+1))
          continue
        fi
        # rule (c): pathname expansion。 寄与文字がまだ 1 つも無い
        # (`_tw_first_seen` が 0 = この glob が最初の寄与位置) 場合は固定開始
        # を持たないため、 固定開始が `-` 等だった場合と同じく解析不能とする。
        return 0
        ;;
      '$'|'`'|'{'|'}'|'('|')')
        # rule (c): 展開・置換・word 生成を導入する quote 外の文字。 brace
        # expansion (`{`/`}`) は literal prefix を保存しない (`{--pre,x}` が
        # `--pre` を生む) ため operand でも緩和しない。
        return 0
        ;;
    esac
    # 上記のいずれにも該当しない unquoted の文字は literal として word へ
    # 寄与する。
    note_literal_contribution "$_tw_c"
    _tw_i=$((_tw_i+1))
  done

  if [ "$_tw_in_squote" -eq 1 ] || [ "$_tw_in_dquote" -eq 1 ]; then
    # rule (a): 走査終了時に quote が閉じていない。
    return 0
  fi
  return 1
}

# classify_wrapper_segment <segment>
# 戻り値: 0 = 実行形、 1 = mention 候補 (呼び出し側が `pipe_chain_all_mention_safe`
#   で pipe chain 検査を行う)。
#
# issue #339: read-only allowlist 方式 (allowlist 外の不明コマンドは fail-closed で
# 実行形) から executable 位置方式へ転換した分類関数。 規則 2/3 (旧
# `mention_safe_segment` が担っていた「言及か実行か」 の判定) を置き換える。 規則 1
# (`segment_has_indirection`) と規則 1.5 (`segment_is_unanalyzable`、 決定表 step 2)
# は呼び出し元の分類 loop で既に評価済みのため、 本関数は呼ばれない前提 (= 本関数内
# では indirection / 解析可能性を再チェックせず、 決定表 step 3 から開始する)。
#
# 正本は `tests/test_pre_push_bg_codex_wrapper.py` の
# `BlockBgCodexWrapperExecPositionClassificationTest` docstring にある 14-step の
# 順序付き決定表 (step 3〜14。 step 1 は規則 1、 step 2 は規則 1.5 として既に呼び出し
# 元で処理済み)。 各 step は上から順に評価し、 最初に確定した判定を採用する:
#   - step 3: segment の全 token (`tokenize_segment` の出力) それぞれが「静的
#     literal」 であることを `token_is_unanalyzable` で検査する (issue #339
#     3/4/5 巡目レビュー、 codex P1 + security P2、 全て同根)。 bash は quote 外の
#     `$` (各種 quoting 含む)・brace expansion・pathname expansion (glob)・
#     tilde expansion を parse 後に展開するため、 これらを含む token の raw
#     文字列は実際にコマンドへ渡される引数と一致する保証が無い。 また共有
#     tokenizer の quote 状態 desync (#354) により複数 shell word が 1 token に
#     merge され、 内側に危険 option (`--pre` 等) を隠して canonicalize_token の
#     exact / prefix 一致を逃れうる。 1 つでも解析不能な token があれば実行形と
#     する。 **検査は head と operand で非対称**であり、 2 箇所に分けて行う:
#       (i) 本 step の一括検査は全 token を `operand` 基準 (rule (c) のうち
#           「token 先頭の fd 数字列 + `<`/`>`」 と「固定開始 `[A-Za-z0-9_/.]`
#           または `~/` を持つ token の glob `*`/`?`/`[`/`]` と先頭 `~`」 を
#           許容する緩和つき) で行い、 step 4 (leading 代入列) より前に実施
#           する。 緩和の根拠は位置推定ではなく「pathname expansion が literal
#           prefix を保存するため、 固定開始を持つ token の展開結果は `-`
#           始まりの列挙済み危険 option になり得ない」 という字句的不変条件。
#      (ii) `head` 基準の厳格検査 (緩和なし) は step 5 の canonical 化直前に、
#           launcher 剥がしを経て実際に head になった token へ行う (下記
#           step 5 参照)。
#     判定内容の詳細は `token_is_unanalyzable` 関数コメント参照。
#   - step 4: segment 先頭の `NAME=VALUE` (leading 代入 slot) が存在すれば、 値に
#     関わらず実行形とする (issue #339 2 巡目レビュー、 codex P1 must-fix)。 代入値が
#     指すのは wrapper path だけでなく、 head コマンドの間接実行面を有効化する設定値
#     でもありうるため (GIT_EXTERNAL_DIFF は外部 diff driver、 RIPGREP_CONFIG_PATH は
#     `--pre` を含みうる config file、 LESSOPEN は input preprocessor 等)、 変数名の
#     列挙ではなく代入 slot の存在自体で判定する。 bash の Simple Command 文法上、
#     代入 slot は segment 先頭に連続してのみ現れる (途中からは現れない) ため、 先頭
#     token 1 つの判定で十分 (先頭が代入でなければそれ以降にも代入は無い)。 先頭 token
#     が無い (空 segment) 場合も実行形。
#   - step 5: その時点の head token を `token_is_unanalyzable ... head` で厳格
#     判定し (step 3 (ii)。 `./b*sh` の glob head・`~/bin/bash` の tilde head 等を
#     捕捉する)、 解析不能なら実行形。 続けて head token の canonical 化が失敗
#     (single quote 外に `$VAR` 等の動的展開が残る) すれば実行形。 この 2 つは
#     step 7 の launcher 剥がし loop の内側で毎周回評価されるため、 剥がした
#     結果として head になった token にも同じ厳格判定が適用される。
#   - step 6: canonical head が無害 builtin (echo/true/false/pwd/type、 basename
#     適用なし) に完全一致すれば mention 候補 (word-shape・keyword・builtin
#     superset 検査より前に評価する)。 `test` / `[` は bash の算術評価による
#     再評価面 (`[ -v 'arr[$(cmd)]' ]` の subscript が算術評価され、 single
#     quote 内でもコマンド置換が実行される) を持つため allowlist に含めない
#     (issue #339 3 巡目レビュー、 codex P1。 除去された `test` / `[` は
#     step 10 の builtin superset に落ちて実行形になる)。
#   - step 7: canonical head が launcher prefix (command/builtin/env/timeout/
#     nohup/nice/setsid/stdbuf/sudo/doas/time/`!`、 basename 適用なし) に完全一致
#     すれば、 直後の代入 slot を再評価し、 存在すれば step 4 と同じ規則 (値によらず
#     実行形) を適用する。 timeout の場合のみ単純形 duration operand
#     (`^[0-9]+(\.[0-9]+)?[smhd]?$`) を 1 つ消費する (`-` 始まり・認識不能な operand
#     は実行形)。 剥がし後の残り token 列で step 5 から反復再評価する。
#   - step 8: canonical head が通常の外部コマンド word の形 (英数字・`_`・`/` の
#     いずれかで始まり `[A-Za-z0-9_/.+-]` のみで構成) でなければ実行形。
#   - step 9: canonical head が bash keyword 静的 superset に該当すれば実行形
#     (time と `!` は step 7 で処理済みのため対象外)。
#   - step 10: canonical head が shell builtin 静的 superset (`test` / `[` を
#     含む。 対応 bash 世代の compgen -b 相当の全集合) に該当すれば実行形
#     (step 6 の無害 builtin と step 7 の launcher は到達しない)。
#   - step 11 以降: head の basename (`##*/`) を初めて適用する。 basename が
#     wrapper 名に完全一致、 または shell interpreter (bash/sh/dash/zsh/ksh) に
#     一致すれば実行形。 launcher (command/builtin/env/timeout/nohup/nice/
#     setsid/stdbuf/sudo/doas/time) が path 修飾形 (`/usr/bin/sudo` 等) で
#     現れ、 step 7 の完全一致 (basename 適用なし) を素通りした場合も、 basename
#     一致により実行形とする (test_path_qualified_sudo_launch_is_denied の契約。
#     step 7 が担う代入 slot / duration の精密な剥がしはこの経路には適用しない —
#     path 修飾形の launcher は単に実行形と確定するのみで、 その後続 token を
#     head として再評価しない保守的な扱い)。
#   - step 12: basename が sed/awk/xargs/less/more/parallel なら実行形。
#     find なら token 群 (canonical 値) に `-exec`/`-execdir`/`-ok`/`-okdir` の
#     完全一致が、 rg なら `--pre` / `--hostname-bin` の完全一致または `--pre=` /
#     `--hostname-bin=` prefix 一致 (`--hostname-bin` は hyperlink format と
#     併用して外部プログラムを起動できる option) が、 sort なら
#     `--compress-program` の `--co` 以上の prefix 一致 (`=` 付き含む) が 1 つでも
#     あれば実行形 (値を取る option の literal 引数も保守的に deny する意図的な
#     false positive。 列挙外の value-taking option は残余ギャップとして受容する)。
#     option 走査対象 token の canonical 化が失敗した場合も、 それが option 位置か
#     operand 位置かを静的に区別できないため実行形とする (step 5 と同じ fail-closed
#     不変条件)。
#   - step 13: basename が git なら、 直後 token (canonical 値) が縮小 subcommand
#     集合 (diff/log/show/status/ls-files/rev-parse/cat-file) に完全一致し、 かつ
#     token 群に `--ext-diff`/`--textconv` の完全一致が無い場合のみ mention 候補。
#     それ以外の git はすべて実行形 (fall-through は実行形側)。
#   - step 14: ここまでで実行形と確定しなかった head (無害 builtin・git 特例・外部
#     コマンド形の不明 head) は mention 候補。
classify_wrapper_segment() {
  local _cw_seg="$1"
  local -a _cw_toks
  tokenize_segment "$_cw_seg" _cw_toks
  local _cw_n=${#_cw_toks[@]}
  local _cw_idx=0
  local _cw_raw _cw_unq

  # step 3 (一括検査、 operand 基準): 全 token が静的 literal であることを
  # 検査する (issue #339 3/4/5 巡目レビュー、 codex P1 + security P2、 全て
  # 同根)。 quote 外の `$` / brace / glob / tilde expansion を含む token は
  # 展開結果を静的に決定できず、 また共有 tokenizer の quote 状態 desync
  # (#354) により複数 shell word が 1 token に merge され内側の危険 option
  # (`--pre` 等) が exact / prefix 一致から隠れる経路もある。 ここでは head の
  # 実行面に影響しない operand 基準 (rule (c) を 2 点緩和) で全 token を検査
  # し、 leading 代入列の判定 (step 4) より前に行う。 実際に head になった
  # token の厳格判定は step 5 の canonical 化直前で別途行う (静的な位置推定は
  # しない。 詳細は `token_is_unanalyzable` 関数コメント参照)。
  for _cw_raw in "${_cw_toks[@]}"; do
    if token_is_unanalyzable "$_cw_raw" operand; then
      unset _cw_toks
      return 0
    fi
  done

  # step 4: leading 代入 slot。 bash の Simple Command 文法上、 代入 slot は
  # segment 先頭に連続してのみ現れるため、 先頭 token 1 つを見れば「代入 slot が
  # 存在するか」 を判定できる (先頭が代入でなければ、 それ以降にも代入は無い)。
  # 存在すれば値に関わらず実行形とする (issue #339 2 巡目レビュー、 codex P1
  # must-fix。 理由は本関数コメント参照)。
  if [ "$_cw_n" -eq 0 ]; then
    # 空 segment。
    unset _cw_toks
    return 0
  fi

  _cw_raw="${_cw_toks[0]}"
  _cw_unq="$(unquote_token "$_cw_raw")"
  if [[ "$_cw_unq" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
    unset _cw_toks
    return 0
  fi

  # steps 5〜7: head の canonical 化、 無害 builtin 判定、 launcher 剥がしの反復。
  local _cw_canon_head _cw_raw_head _cw_launcher
  local _cw_araw _cw_aunq _cw_draw _cw_dcanon
  while :; do
    _cw_raw_head="${_cw_toks[$_cw_idx]}"

    # step 3 (head 基準の厳格検査): step 7 の launcher 剥がしを経て「実際に
    # head になった」 token に対してのみ、 rule (c) を緩和しない厳格判定を
    # 行う (`./b*sh` が bash に展開しうる glob head や `~/bin/bash` の tilde
    # head を捕捉する)。 静的な位置推定は行わず、 剥がしの各周回で都度評価
    # する (詳細は `token_is_unanalyzable` 関数コメント参照)。
    if token_is_unanalyzable "$_cw_raw_head" head; then
      unset _cw_toks
      return 0
    fi

    if ! _cw_canon_head="$(canonicalize_token "$_cw_raw_head")"; then
      # step 5: canonical 化失敗 (動的展開が残る)。
      unset _cw_toks
      return 0
    fi

    case "$_cw_canon_head" in
      echo|true|false|pwd|type)
        # step 6: 無害 builtin (basename 適用なし)。 `test` / `[` は算術評価
        # による再評価面を持つため allowlist から除外し、 step 10 の builtin
        # superset に委ねる (関数コメント参照)。
        unset _cw_toks
        return 1
        ;;
    esac

    case "$_cw_canon_head" in
      command|builtin|env|timeout|nohup|nice|setsid|stdbuf|sudo|doas|time|'!')
        # step 7: launcher prefix (basename 適用なし) を剥がして再評価する。
        _cw_launcher="$_cw_canon_head"
        _cw_idx=$((_cw_idx+1))

        if [ "$_cw_idx" -lt "$_cw_n" ]; then
          _cw_araw="${_cw_toks[$_cw_idx]}"
          _cw_aunq="$(unquote_token "$_cw_araw")"
          if [[ "$_cw_aunq" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            # 直後の代入 slot が存在する時点で実行形 (step 4 と同じ規則)。
            unset _cw_toks
            return 0
          fi
        fi

        if [ "$_cw_launcher" = "timeout" ]; then
          if [ "$_cw_idx" -ge "$_cw_n" ]; then
            # timeout の後に operand が無い。
            unset _cw_toks
            return 0
          fi
          _cw_draw="${_cw_toks[$_cw_idx]}"
          if ! _cw_dcanon="$(canonicalize_token "$_cw_draw")"; then
            unset _cw_toks
            return 0
          fi
          if [[ "$_cw_dcanon" =~ ^[0-9]+(\.[0-9]+)?[smhd]?$ ]]; then
            _cw_idx=$((_cw_idx+1))
          else
            # `-` 始まり、 または duration の単純形と認識できない operand。
            unset _cw_toks
            return 0
          fi
        fi

        if [ "$_cw_idx" -ge "$_cw_n" ]; then
          # launcher (+ operand) を剥がした後に head token が無い。
          unset _cw_toks
          return 0
        fi
        continue
        ;;
    esac

    break
  done

  # step 8: 通常の外部コマンド word 形かどうか (canonical head、 basename 適用前)。
  if ! [[ "$_cw_canon_head" =~ ^[A-Za-z0-9_/][A-Za-z0-9_/.+-]*$ ]]; then
    unset _cw_toks
    return 0
  fi

  # step 9: bash keyword 静的 superset (time / `!` は step 7 で処理済みのため対象外)。
  case "$_cw_canon_head" in
    if|then|else|elif|fi|case|'esac'|for|select|while|until|do|done|in|function|coproc|'{'|'}'|'[['|']]')
      unset _cw_toks
      return 0
      ;;
  esac

  # step 10: shell builtin 静的 superset (対応 bash 世代の `compgen -b` 相当の全集合
  # から、 step 7 の launcher を除いたもの)。 `test` / `[` は step 6 の無害 builtin
  # allowlist から除外された (算術評価による再評価面のため) ので、 ここで実行形として
  # 捕捉する。
  case "$_cw_canon_head" in
    .|:|alias|bg|bind|break|caller|cd|compgen|complete|compopt|continue|declare|dirs|disown|enable|eval|exec|exit|export|fc|fg|getopts|hash|help|history|jobs|kill|let|local|logout|mapfile|popd|printf|pushd|read|readarray|readonly|return|set|shift|shopt|source|suspend|test|times|trap|typeset|ulimit|umask|unalias|unset|wait|'[')
      unset _cw_toks
      return 0
      ;;
  esac

  # step 11 以降: ここから basename (`##*/`) を適用する。
  local _cw_base="${_cw_canon_head##*/}"

  case "$_cw_base" in
    run-pre-push-codex-review.sh)
      unset _cw_toks
      return 0
      ;;
    bash|sh|dash|zsh|ksh)
      unset _cw_toks
      return 0
      ;;
    command|builtin|env|timeout|nohup|nice|setsid|stdbuf|sudo|doas|time)
      # step 7 の launcher 完全一致 (basename 適用なし) を path 修飾形 (`/usr/bin/sudo`
      # 等) が素通りした場合の basename 一致による捕捉
      # (test_path_qualified_sudo_launch_is_denied 契約)。 この経路は「実行形」と
      # 確定するのみで、 step 7 のような代入 slot / duration の精密な剥がしは行わない
      # (path 修飾形 launcher は保守的に実行形へ倒すだけで十分なため)。
      unset _cw_toks
      return 0
      ;;
  esac

  # step 12: script/対話内実行面を持つコマンド + option-aware 検査。
  local _cw_j _cw_traw _cw_tcanon _cw_prefix
  case "$_cw_base" in
    sed|awk|xargs|less|more|parallel)
      unset _cw_toks
      return 0
      ;;
    find)
      _cw_j=$_cw_idx
      while [ "$_cw_j" -lt "$_cw_n" ]; do
        _cw_traw="${_cw_toks[$_cw_j]}"
        if _cw_tcanon="$(canonicalize_token "$_cw_traw")"; then
          case "$_cw_tcanon" in
            -exec|-execdir|-ok|-okdir)
              unset _cw_toks
              return 0
              ;;
          esac
        else
          # head (step 5) と対称の fail-closed: option 走査対象 token の
          # canonical 化が失敗する (ANSI-C quote `$'...'` 等、 動的展開が残る)
          # 場合、 mention 判定に必要な値を静的決定できないため解析不能として
          # 実行形とする (契約 docstring の全体不変条件: どの step であれ
          # canonical 化失敗は実行形)。
          unset _cw_toks
          return 0
        fi
        _cw_j=$((_cw_j+1))
      done
      ;;
    rg)
      _cw_j=$_cw_idx
      while [ "$_cw_j" -lt "$_cw_n" ]; do
        _cw_traw="${_cw_toks[$_cw_j]}"
        if _cw_tcanon="$(canonicalize_token "$_cw_traw")"; then
          case "$_cw_tcanon" in
            --pre|--pre=*|--hostname-bin|--hostname-bin=*)
              # --hostname-bin は hyperlink format と併用して外部プログラムを
              # 起動できる option (issue #339 3 巡目レビュー、 codex P1)。
              unset _cw_toks
              return 0
              ;;
          esac
        else
          # 同上 (find と対称の fail-closed)。
          unset _cw_toks
          return 0
        fi
        _cw_j=$((_cw_j+1))
      done
      ;;
    sort)
      _cw_j=$_cw_idx
      while [ "$_cw_j" -lt "$_cw_n" ]; do
        _cw_traw="${_cw_toks[$_cw_j]}"
        if _cw_tcanon="$(canonicalize_token "$_cw_traw")"; then
          _cw_prefix="${_cw_tcanon%%=*}"
          if [ "${#_cw_prefix}" -ge 4 ]; then
            # 意図的に固定文字列 (`--compress-program`) を subject、 動的な
            # `_cw_prefix` を pattern 側に置き、 「token の prefix が
            # `--compress-program` の接頭辞になっているか」 (`--co` 以上の
            # abbreviation 一致。 GNU sort の `--c` 系 long option は `--check`
            # と `--compress-program` のみで、 `--co` (4 文字) の時点で既に
            # 一意省略として受理されるため閾値を 4 とする) を判定する。
            # 定数を case の subject にするのは variable の `$` 付け忘れでは、
            # という shellcheck の誤検知 (SC2194) は意図的な用法のため抑止する。
            # shellcheck disable=SC2194
            case "--compress-program" in
              "$_cw_prefix"*)
                unset _cw_toks
                return 0
                ;;
            esac
          fi
        else
          # 同上 (find/rg と対称の fail-closed)。
          unset _cw_toks
          return 0
        fi
        _cw_j=$((_cw_j+1))
      done
      ;;
  esac

  # step 13: git 特例。
  if [ "$_cw_base" = "git" ]; then
    local _cw_gi=$((_cw_idx+1))
    local _cw_sub_ok=0
    local _cw_subcanon
    if [ "$_cw_gi" -lt "$_cw_n" ]; then
      if _cw_subcanon="$(canonicalize_token "${_cw_toks[$_cw_gi]}")"; then
        case "$_cw_subcanon" in
          diff|log|show|status|ls-files|rev-parse|cat-file)
            _cw_sub_ok=1
            ;;
        esac
      fi
    fi

    if [ "$_cw_sub_ok" -eq 0 ]; then
      # 特例に一致しない git はすべて実行形 (fall-through は実行形側)。
      unset _cw_toks
      return 0
    fi

    _cw_j=$_cw_idx
    while [ "$_cw_j" -lt "$_cw_n" ]; do
      _cw_traw="${_cw_toks[$_cw_j]}"
      if _cw_tcanon="$(canonicalize_token "$_cw_traw")"; then
        case "$_cw_tcanon" in
          --ext-diff|--textconv)
            unset _cw_toks
            return 0
            ;;
        esac
      else
        # 同上 (find/rg/sort と対称の fail-closed。 git の --ext-diff/--textconv
        # 走査中も同じ不変条件を適用する)。
        unset _cw_toks
        return 0
      fi
      _cw_j=$((_cw_j+1))
    done

    unset _cw_toks
    return 1
  fi

  # step 14: 実行形と確定しなかった head は mention 候補。
  unset _cw_toks
  return 1
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
    *run-pre-push-codex-review.sh*) ;;
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

  # 規則 1.5 (解析可能性検査、 決定表 step 2、 issue #339 2/3 巡目): split_command の
  # depth 追跡と実際の shell 意味論が乖離して複数コマンドが 1 segment に merge
  # された場合、 または ANSI-C quoting (`$'`) で quote 状態が乖離した場合、 head
  # token が実コマンドを代表しないため classify_wrapper_segment の呼び出し自体を
  # skip して実行形とする (詳細は `segment_is_unanalyzable` 関数コメント参照)。
  if segment_is_unanalyzable "$_cls_seg"; then
    HAS_EXEC_SEGMENT=1
    continue
  fi

  # 規則 2 / 3 (executable 位置分類、 issue #339): classify_wrapper_segment
  # (戻り値 0 = 実行形、 1 = mention 候補) が 14-step 決定表 (step 3〜14。 step 1/2
  # は上記規則 1 / 1.5 として既に処理済み) で判定する。 詳細は
  # `classify_wrapper_segment` 関数コメントおよびファイルヘッダ「検知ロジック」節
  # 参照。
  if classify_wrapper_segment "$_cls_seg"; then
    HAS_EXEC_SEGMENT=1
  else
    # mention 候補でも、 この segment が属する pipe chain 内の他 segment が全て
    # mention-safe でなければ command 全体を実行形とする (詳細はファイルヘッダ
    # 「mention 候補 segment の pipe chain 検査」節、 および
    # `pipe_chain_all_mention_safe` 関数コメント参照。 現行のまま変更していない)。
    if ! pipe_chain_all_mention_safe "$_cls_i"; then
      HAS_EXEC_SEGMENT=1
    fi
  fi
done

# 実行形 segment が 1 つも無ければ、 agent_type gate も bg / pipeline 判定も skip して
# allow する (= wrapper を実行せず言及するだけの read-only コマンドを deny しない)。
if [ "$HAS_EXEC_SEGMENT" -eq 0 ]; then
  exit 0
fi

# agent_type 検証 gate (fail-closed): wrapper 起動を許可する呼び出し元は
# `pre-push-codex-review:codex-reviewer` subagent のみ。 hook payload のトップレベル `agent_type`
# が完全一致しない場合 (欠落含む) は deny する。 一致した場合のみ後続の bg / pipeline 判定
# へ進む。 jq 不在時は既に上の `command -v jq` チェックで fail-open 済みのため、 ここに到達
# する時点で jq は利用可能。
AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')
if [ "$AGENT_TYPE" != "pre-push-codex-review:codex-reviewer" ]; then
  if [ -z "$AGENT_TYPE" ]; then
    REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-pre-push-codex-review.sh` wrapper は `pre-push-codex-review:codex-reviewer` subagent 経由でのみ起動できます。

理由: 本 hook の payload に `agent_type` が含まれていません (欠落)。 これはメインセッションが wrapper を直接 Bash 実行した場合、 または `agent_type` を hook payload に含めない旧 Claude Code を使用している場合に発生します。

wrapper を実行せずファイル内容を確認したいだけなら、 **Read / Grep tool を使ってください** (本 hook は Bash tool のみを対象とするため、 形によらず deny されません)。

Bash で確認する場合は、 `cat` / `git diff` / `grep` 等の read-only コマンドを、 環境変数代入を前置せず、 静的に決まる literal path で使ってください。 たとえば次の形は read-only コマンドでも deny されます: path に `$VAR` 等の動的展開・コマンド置換・brace expansion (`{a,b}`)・`~user` 形が含まれる (展開結果を静的に決定できないため) / path が glob メタ文字で始まる (`*/run-pre-push-codex-review.sh` 等。 `./*/run-pre-push-codex-review.sh` のように `./` を前置すれば allow されます) / `NAME=VALUE cmd ...` のように代入を前置している (代入値が head の間接実行面を有効化しうるため、 値によらず deny します)。 これら以外にも、 静的に解析できない形は保守的に deny されます。

対応:
  - `/pre-push-codex-review:review` で push 前レビューを並列起動してください (推奨)
  - codex review 単独が必要な場合は Agent / Task tool で subagent_type="pre-push-codex-review:codex-reviewer", model="sonnet" を起動してください
  - 上記を行っても `agent_type` 欠落が解消しない場合は、 Claude Code を 2.1.211 以上へ更新してください (本 gate は Claude Code 2.1.211 で実機検証済みです)
EOF
)
  else
    REASON=$(cat <<EOF
プッシュ前レビューをブロックしました。 \`run-pre-push-codex-review.sh\` wrapper は \`pre-push-codex-review:codex-reviewer\` subagent 経由でのみ起動できます。

理由: 検出された \`agent_type\` は \`${AGENT_TYPE}\` で、 \`pre-push-codex-review:codex-reviewer\` と一致しません。 メインセッションが wrapper を直接 Bash 実行した場合や、 \`agent_type\` を hook payload に含めない旧 Claude Code を使用している場合にも同様の deny になります。

wrapper を実行せずファイル内容を確認したいだけなら、 **Read / Grep tool を使ってください** (本 hook は Bash tool のみを対象とするため、 形によらず deny されません)。

Bash で確認する場合は、 \`cat\` / \`git diff\` / \`grep\` 等の read-only コマンドを、 環境変数代入を前置せず、 静的に決まる literal path で使ってください。 たとえば次の形は read-only コマンドでも deny されます: path に \`\$VAR\` 等の動的展開・コマンド置換・brace expansion (\`{a,b}\`)・\`~user\` 形が含まれる (展開結果を静的に決定できないため) / path が glob メタ文字で始まる (\`*/run-pre-push-codex-review.sh\` 等。 \`./*/run-pre-push-codex-review.sh\` のように \`./\` を前置すれば allow されます) / \`NAME=VALUE cmd ...\` のように代入を前置している (代入値が head の間接実行面を有効化しうるため、 値によらず deny します)。 これら以外にも、 静的に解析できない形は保守的に deny されます。

対応:
  - \`/pre-push-codex-review:review\` で push 前レビューを並列起動してください (推奨)
  - codex review 単独が必要な場合は Agent / Task tool で subagent_type="pre-push-codex-review:codex-reviewer", model="sonnet" を起動してください
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
#   (2) shell-level backgrounding (`bash run-pre-push-codex-review.sh &`) や pipeline (`bash run-pre-push-codex-review.sh | tee log`)
#       — Bash tool option は false だが shell が wrapper を bg / 並列起動して主 session が
#       review 結果を観察しない経路。 block-pre-push.sh も同じ理由で単独 `&` / `|` を deny
#       している (markers gate 検証後の race 経路も同型)。
RUN_IN_BG=$(printf '%s' "$INPUT" | jq -r '.tool_input.run_in_background // false')

# (2) shell-level backgrounding / pipeline の検知。 SEGMENTS / SEPARATORS は前段の segment
# 分類で既に計算済みのものを再利用する (二重 split を避ける)。 wrapper を含む segment の
# **隣接** (直前 / 直後) separator が `&` (background) または `|` (pipeline) のときだけ
# deny する。 SEPARATORS[i-1] が segment[i] の直前、 SEPARATORS[i] が segment[i] の直後を
# 指す (= split_command が segment と separator を交互に出力する仕様)。 wrapper と無関係な
# segment 間の `&` / `|` (例: `bash run-pre-push-codex-review.sh && echo done | tee log`) は false
# positive にしない。 `&&` / `||` / `;` は逐次実行なので race にならず許容。
_SHELL_BG=0
for i in "${!SEGMENTS[@]}"; do
  case "${SEGMENTS[$i]}" in
    *run-pre-push-codex-review.sh*) ;;
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
# 隠れた wrapper 起動 (`WRAPPER=$(find ... run-pre-push-codex-review.sh ...)`) と、 実際に wrapper を
# 実行する別 segment (`bash "$WRAPPER"`) は別 segment になり得るため、 隣接判定は
# substring を含む segment (前者) しか見ておらず、 実行 segment (後者) に隣接する `|` /
# `&` を見逃す。 そのため command 全体の SEPARATORS を **位置を問わず** 走査し、 単独
# `&` / `|` が 1 つでもあれば deny する。 例: `WRAPPER=$(find ... run-pre-push-codex-review.sh ...) &&
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
プッシュ前レビューをブロックしました。 `run-pre-push-codex-review.sh` wrapper を `run_in_background: true` で起動することはできません。

理由: wrapper 自身は codex review を foreground で実行しますが、 Bash tool の `run_in_background: true` で起動すると **codex-reviewer subagent は wrapper の stdout / stderr (= codex review の verdict / findings) を観察できないまま完了しうる**ため、parent-safe report を正しく組み立てられません。v4.0.1 以降は wrapper が pending attestation を書いても、正規 report 成功前に final marker へ昇格しないため push gate bypass にはなりませんが、review cycle と report delivery が分離し、stale pending だけが残る不正な完了になります。

対応: `run_in_background: true` を使わず、 wrapper を plain foreground の単独コマンドとして再実行してください。 wrapper は内部で codex companion を `--wait` で foreground 起動するため、 Bash 呼び出し自体が review 完了まで block しますが、 これが本プラグインの想定する正しい使い方です (= review 結果を観察してから push 判断する)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-codex-review:codex-reviewer` subagent (model: "sonnet") を再起動してください。
EOF
)
elif [ "$HAS_INDIRECTION" -eq 1 ]; then
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-pre-push-codex-review.sh` wrapper を shell-level の `&` (background) や `|` (pipeline) で起動することはできません。

理由: `bash run-pre-push-codex-review.sh &` のような shell-level backgrounding、 `bash run-pre-push-codex-review.sh | tee log` のような pipeline で wrapper を起動すると、 Bash tool option `run_in_background: false` で呼び出しても shell が wrapper を別 process で並列起動したり、output を別 process が変換したりするため、 **codex-reviewer subagent が wrapper の verdict / findings を観察しない / 不完全にしか観察しない** 経路ができます。pending attestation と schema 上の report だけが揃っても、report の根拠となる review output を完全に観察した保証がないため、foreground review 要件に反します。

本コマンドはコマンド置換 `$(...)` 等の間接実行 (indirection) を含むため、 `&` / `|` が wrapper 呼び出しの直前・直後に隣接していなくても **位置を問わず** deny しています (indirection 経由の実行は、 substring を含む segment と実際に wrapper を実行する segment の対応関係を本 parser が追跡できないため、 保守的に deny する必要があります)。

対応: `&` / `|` を外して wrapper を plain foreground の単独コマンドとして再実行するか、 `&&` (success-and) / `;` (sequential) で連結してください。 logging が必要なら file redirection (`bash run-pre-push-codex-review.sh > codex.log 2>&1`) で代替できます (= wrapper 完了後に file を読めば review 結果を観察可能)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-codex-review:codex-reviewer` subagent (model: "sonnet") を再起動してください。
EOF
)
else
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-pre-push-codex-review.sh` wrapper を shell-level の `&` (background) や `|` (pipeline) で起動することはできません。

理由: `bash run-pre-push-codex-review.sh &` のような shell-level backgrounding、 `bash run-pre-push-codex-review.sh | tee log` のような pipeline で wrapper を起動すると、 Bash tool option `run_in_background: false` で呼び出しても shell が wrapper を別 process で並列起動したり、output を別 process が変換したりするため、 **codex-reviewer subagent が wrapper の verdict / findings を観察しない / 不完全にしか観察しない** 経路ができます。pending attestation と schema 上の report だけが揃っても、report の根拠となる review output を完全に観察した保証がないため、foreground review 要件に反します。

対応: `&` / `|` を外して wrapper を plain foreground の単独コマンドとして再実行するか、 `&&` (success-and) / `;` (sequential) で連結してください。 logging が必要なら file redirection (`bash run-pre-push-codex-review.sh > codex.log 2>&1`) で代替できます (= wrapper 完了後に file を読めば review 結果を観察可能)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-codex-review:codex-reviewer` subagent (model: "sonnet") を再起動してください。
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

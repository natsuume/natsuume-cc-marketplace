#!/bin/bash
# block-bg-codex-wrapper.sh
# `run-codex-review.sh` wrapper の background 起動、 および `pre-push-review:codex-reviewer`
# subagent 以外からの起動を deny する PreToolUse フック。
#
# policy: 本 hook 全体は fail-open (PreToolUse / defense-in-depth 補助)。 jq 不在等の
#   環境失敗時は silent に exit 0 で抜けて allow に倒す (= 環境失敗で「合法な wrapper
#   起動」 が deny される false positive を避ける)。 一方 agent_type gate と
#   segment 分類 (下記「検知ロジック」節) は fail-closed で判定する。
#   (a) **fail-open 経路の限定**: 本 hook が意図的に allow へ倒す fail-open 経路は
#       「外部コマンド形の不明 head による引数・quoted 文字列としての言及」
#       (issue #339 の決定表 step 14、 例: `rg -n 'run-codex-review.sh' dir/`) の
#       1 経路のみに限定される。 それ以外 (agent_type 欠落・不一致、 canonical 化
#       失敗、 word-shape / keyword / builtin superset 該当、 wrapper basename 一致
#       等) はすべて fail-closed (実行形扱い) で deny する。
#   (b) **cooperative 補助 gate であること**: 本 hook は adversarial な security
#       boundary ではなく、 cooperative 利用を前提とした補助 gate である。 任意の
#       未知 launcher (`frobnicate <wrapper>` 等) や動的にコマンド文字列を構築する
#       経路への完全性は保証しない (= 意図的に攻め切らない設計判断。 詳細は下記
#       「検知ロジック」節および `classify_wrapper_segment` の呼び出し元である
#       対象テストクラス `BlockBgCodexWrapperExecPositionClassificationTest` の
#       docstring 14-step 決定表を参照)。
#   (c) **真の push gate は block-pre-push.sh**: 本 hook が抜けても (= 上記 (a) の
#       fail-open 経路や jq 不在等の環境失敗経路)、 真の保証は block-pre-push.sh が
#       marker hash check (= fail-closed) で行うため、 未レビュー push は
#       block-pre-push.sh が別途ブロックする。
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
# issue #339 でこの「実行形 segment 分類」 をさらに改良した。 従来の read-only allowlist
# 方式 (allowlist 外の不明コマンドは fail-closed で実行形扱い) は、 `rg -n
# 'run-codex-review.sh' dir/` のような「不明コマンドへの単なる引数参照」 まで実行形として
# deny する false positive (issue 実測) を生んでいた。 これを、 「head token が実際に
# どこにあるか」 を canonical token 値ベースで判定する executable 位置方式に転換した
# (下記「検知ロジック」規則 2/3、 `classify_wrapper_segment` / `canonicalize_token` 関数
# コメント、 および正本である `tests/test_pre_push_bg_codex_wrapper.py` の
# `BlockBgCodexWrapperExecPositionClassificationTest` docstring の 14-step 決定表を参照)。
# Phase B 2 巡目レビューで、 (1) leading / launcher 剥がし後の代入 slot 判定を「値に
# wrapper substring を含むか」 から「代入 slot が 1 つでも存在するか」 へ変更 (代入値は
# wrapper path 以外にも GIT_EXTERNAL_DIFF / RIPGREP_CONFIG_PATH / LESSOPEN 等、 head
# コマンドの間接実行面を有効化する設定値でありうるため、 変数名列挙ではなく存在自体で
# 判定する構造的な閉じ方に変更)、 (2) `split_command` の depth 追跡と実際の shell 意味論が
# 乖離し複数コマンドが 1 segment に merge される場合 (未対応の `(` / `{` 等) の解析不能
# 判定 (`segment_is_unanalyzable`、 新 step 2) を追加、 (3) `canonicalize_token` の
# double quote 内 escaped backslash 判定の case pattern arity 不一致を修正、 の 3 点を
# 反映した。 Phase B 3 巡目レビューで、 (4) 無害 builtin allowlist から算術評価の再評価
# 面を持つ `test` / `[` を除去、 (5) rg の危険 option に `--hostname-bin` を追加、 (6)
# 共有 tokenizer の quote 状態 desync (#354) で複数 shell word が 1 token に merge
# される経路を検知する token 単位の単一 shell word 検査 (`token_is_unanalyzable`、 新
# step 3) を追加、 (7) `segment_is_unanalyzable` に ANSI-C quoting (`$'`) の検出を
# 追加、 (8) `canonicalize_token` の `$` 判定を「直後が展開開始として有効な文字の場合
# のみ失敗」 へ精密化、 の 5 点をさらに反映した。
#
# ## 検知ロジック
#
# 0. **segment 分類** (実行形のみ gate、 fail-closed): command を `split_command` で
#    segment 分割し、 substring `run-codex-review.sh` を含む各 segment を次の規則で
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
#      - **規則 1.5 (解析可能性検査、 決定表 step 2、 issue #339 2/3 巡目)**: 規則 1 に
#        該当しない segment に対し `segment_is_unanalyzable` (戻り値 0 = 解析不能、 1 =
#        解析可能) を評価する。 `split_command` は quote 外の group delimiter
#        (`(`/`)`/`{`/`}`) の depth が非 0 の間、 separator 候補文字 (`;`/`|`/`&`/改行)
#        を real separator ではなく segment 内の literal として保持する仕様のため、
#        未対応の `(` 等があると本来複数コマンドになるはずの入力が 1 segment に merge
#        され、 head token が実コマンドを代表しなくなる (例: `echo ( ; bash <wrapper>`
#        は `echo` が harmless builtin のため誤って mention 扱いされかねない)。 quote 外
#        に ANSI-C quoting の開始 (`$'`) が現れる場合も、 本検査がその内部意味論を模さず
#        quote 状態が乖離するため同様に解析不能とする。 解析不能なら実行形とし
#        `classify_wrapper_segment` の呼び出し自体を skip する (詳細は
#        `segment_is_unanalyzable` 関数コメント参照)。
#      - **規則 2/3 (executable 位置分類、 issue #339)**: 規則 1 / 1.5 に該当しない segment
#        は `classify_wrapper_segment` (戻り値 0 = 実行形、 1 = mention 候補) が判定する。
#        判定は「canonical token 値」 (single/double quote 除去・backslash escape 解決・
#        fragment 連結を行った shell word の静的な値。 `canonicalize_token` 関数参照) に
#        対して行う 14-step の順序付き決定表であり、 概略は次のとおり: 全 token が単一
#        shell word であること (`token_is_unanalyzable`。 共有 tokenizer の quote 状態
#        desync #354 で複数 word が 1 token に merge され危険 option を隠す経路を塞ぐ) /
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
#        `classify_wrapper_segment` 関数コメントと、 正本である
#        `BlockBgCodexWrapperExecPositionClassificationTest` docstring を参照する。
#        **mention 候補 segment の pipe chain 検査**: mention 候補と判定された segment に
#        ついても、 その segment が属する pipe chain (両方向に separator が `|` である限り
#        連続する segment の極大区間、 `&&` / `||` / `;` / `&` で途切れる。 `split_command`
#        の出力仕様上 SEPARATORS[i-1] が SEGMENTS[i] の直前、 SEPARATORS[i] が直後を指す)
#        内の他の全 segment (substring を含まないものも含む) が `mention_safe_segment`
#        (indirection 不在 + read-only allowlist / git 特例一致。 exact 判定のまま維持し
#        basename 正規化は適用しない — 中心 segment の判定との非対称は受容境界) を満たす
#        ことを要求する (`pipe_chain_all_mention_safe` 関数、 現行のまま変更していない)。
#        隣接 1 段でなく chain 全体を見るのは、 `cat wrapper | head -100 | bash` のように
#        allowlist コマンドを 1 段挟むと隣接判定だけでは素通りするため (上流側 `bash
#        gen.sh | grep -f - wrapper` も同様に保守的に検査する)。 また `cat wrapper | grep
#        "$(bash)"` のように allowlist head でもコマンド置換の内側 (`bash`) が pipe の
#        stdin (= wrapper 内容) を読んで実行できるため、 neighbor の indirection も同じ
#        chain 走査で検査する。 chain 内に mention-safe でない segment が 1 つでもあれば
#        command 全体を実行形とする (下記 1. の agent_type gate を発火させる。 該当 segment
#        が indirection を含む場合は INDIRECTION フラグも連動して立てる)。
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
# の直後の 1 文字が展開開始として有効な文字 (英数字 / `_` / `{` / `(` / `@` / `*` /
# `#` / `?` / `!` / `-` / `$`。 `$VAR` / `${...}` / `$(...)` / `$@` / `$*` / `$#` /
# `$?` / `$!` / `$-` / `$$` に対応) の場合のみ動的展開として canonical 化失敗
# (`_ct_failed=1`) とする。 それ以外 (token 末尾の `$`、 または `"marker$"` の
# ような正規表現の行末アンカーとして使われる literal な `$`) は展開を開始しない
# ため literal 文字として結果に含める。 コマンド置換 `$(` は `segment_has_indirection`
# (規則 1) が本関数より先に検知済みなので、 ここでの `$(` 判定は主に「規則 1 を
# すり抜けた single quote 内の `$(` を canonicalize_token 内で再度誤検知しない」
# ための一貫性維持であり、 実質的な検知は規則 1 が担う。
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
# **lib/cmd-parser.sh への既知の指摘 (対象外)**: `tokenize_segment` の double
# quote 分岐にも同型の case pattern arity 不一致があるとレビューで指摘された
# (`tokenize_segment` / `split_command` の `'$'|'`'|'"'|'\\'` も同じ 1 文字変数
# vs 2 文字 pattern の arity 不一致を抱える)。 共有 parser (cmd-parser.sh) は
# 本 hook 以外の caller (block-pre-push.sh 等) にも影響するため、 本 hook 単体の
# 変更スコープでは触れない (別 issue で扱う)。
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
        # 失敗) とする (詳細は関数コメント「`$` 判定の精密化」節参照)。
        _ct_nc="${_ct_tok:$((_ct_i+1)):1}"
        case "$_ct_nc" in
          [A-Za-z0-9_]|'{'|'('|'@'|'*'|'#'|'?'|'!'|'-'|'$')
            _ct_failed=1
            ;;
        esac
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
      # 同上 (double quote 内と同じ「展開開始として有効な文字」判定)。
      _ct_nc="${_ct_tok:$((_ct_i+1)):1}"
      case "$_ct_nc" in
        [A-Za-z0-9_]|'{'|'('|'@'|'*'|'#'|'?'|'!'|'-'|'$')
          _ct_failed=1
          ;;
      esac
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

# token_is_unanalyzable <raw_token>
# 戻り値: 0 = 解析不能 (quote 状態 desync の疑い)、 1 = 単一 shell word として
#   解析可能。
#
# 決定表 step 3 (issue #339 3 巡目レビュー、 codex P1 + security P2)。
# `tokenize_segment` (共有 parser、 #354 で追跡中の quote 状態 desync 疑い) は、
# 本来複数の shell word であるべき入力を 1 token に merge して返す経路がある
# (例: `"x\\"` のように escape 判定が絡む形。 `--pre` のような危険 option が
# merge 後 token の内側に隠れると、 `canonicalize_token` による exact / prefix
# 一致判定を欺いて option 走査 (step 12/13) をすり抜けうる)。 本関数は
# `tokenize_segment` の出力結果を信頼せず、 raw token 文字列を
# `canonicalize_token` と同じ quote 意味論 (single quote 内は escape 無効、
# double quote 内は `$`/バッククォート/`"`/`\` の前でのみ escape、 それ以外は
# unquoted として次の 1 文字を無条件 escape) で独立に再走査し、
#   - quote 外 (unquoted) の空白文字が現れる (= 本来 tokenize_segment が
#     ここで token を区切っているはずなのに 1 token に残っている。 quote 状態
#     desync の証跡)
#   - 走査終了時に single/double quote が閉じていない (= token 内で quote が
#     完結していない)
# のいずれかを解析不能と判定する。 head だけでなく `classify_wrapper_segment`
# が扱う全 token に対して呼ぶ (merge された token は option 位置に限らず
# operand 位置にも現れうるため)。 共有 parser 側の quote 状態 desync の根本
# 原因そのものは #354 で追跡中であり、 本関数はその症状を検知する防御層に
# すぎない。 bash 3.2 互換 (mapfile / declare -A / `${var,,}` を使わない)。
token_is_unanalyzable() {
  local _tw_tok="$1"
  local _tw_i=0 _tw_len=${#_tw_tok}
  local _tw_in_squote=0 _tw_in_dquote=0
  local _tw_c _tw_nc

  while [ "$_tw_i" -lt "$_tw_len" ]; do
    _tw_c="${_tw_tok:$_tw_i:1}"

    if [ "$_tw_in_squote" -eq 1 ]; then
      [ "$_tw_c" = "'" ] && _tw_in_squote=0
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
            _tw_i=$((_tw_i+2))
            continue
            ;;
        esac
      fi
      _tw_i=$((_tw_i+1))
      continue
    fi

    # unquoted 領域: 空白が現れたら quote 状態 desync の証跡 (解析不能)。
    if [[ "$_tw_c" == [[:space:]] ]]; then
      return 0
    fi

    case "$_tw_c" in
      "'") _tw_in_squote=1; _tw_i=$((_tw_i+1)); continue ;;
      '"') _tw_in_dquote=1; _tw_i=$((_tw_i+1)); continue ;;
      "\\")
        _tw_i=$((_tw_i+2))
        continue
        ;;
    esac
    _tw_i=$((_tw_i+1))
  done

  if [ "$_tw_in_squote" -eq 1 ] || [ "$_tw_in_dquote" -eq 1 ]; then
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
#   - step 3: segment の全 token (`tokenize_segment` の出力) それぞれが単一 shell
#     word であることを `token_is_unanalyzable` で検査する (issue #339 3 巡目
#     レビュー、 codex P1 + security P2)。 共有 tokenizer の quote 状態 desync
#     (#354) により複数 shell word が 1 token に merge され、 内側に危険 option
#     (`--pre` 等) を隠して canonicalize_token の exact / prefix 一致を逃れうる
#     ため、 head だけでなく全 token を対象に、 step 4 (leading 代入列) より前に
#     検査する。 1 つでも解析不能な token があれば実行形とする。
#   - step 4: segment 先頭の `NAME=VALUE` (leading 代入 slot) が存在すれば、 値に
#     関わらず実行形とする (issue #339 2 巡目レビュー、 codex P1 must-fix)。 代入値が
#     指すのは wrapper path だけでなく、 head コマンドの間接実行面を有効化する設定値
#     でもありうるため (GIT_EXTERNAL_DIFF は外部 diff driver、 RIPGREP_CONFIG_PATH は
#     `--pre` を含みうる config file、 LESSOPEN は input preprocessor 等)、 変数名の
#     列挙ではなく代入 slot の存在自体で判定する。 bash の Simple Command 文法上、
#     代入 slot は segment 先頭に連続してのみ現れる (途中からは現れない) ため、 先頭
#     token 1 つの判定で十分 (先頭が代入でなければそれ以降にも代入は無い)。 先頭 token
#     が無い (空 segment) 場合も実行形。
#   - step 5: head token の canonical 化が失敗 (single quote 外に `$VAR` 等の動的
#     展開が残る) すれば実行形。
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

  # step 3: 全 token が単一 shell word であることを検査する (issue #339 3 巡目
  # レビュー、 codex P1 + security P2)。 共有 tokenizer の quote 状態 desync
  # (#354) により複数 shell word が 1 token に merge され、 内側の危険 option
  # (`--pre` 等) が exact / prefix 一致から隠れる経路を塞ぐ。 head だけでなく
  # 全 token を対象とし、 leading 代入列の判定 (step 4) より前に行う。
  for _cw_raw in "${_cw_toks[@]}"; do
    if token_is_unanalyzable "$_cw_raw"; then
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
    run-codex-review.sh)
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
  - codex review 単独が必要な場合は Agent / Task tool で subagent_type="pre-push-review:codex-reviewer", model="sonnet" を起動してください
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
  - codex review 単独が必要な場合は Agent / Task tool で subagent_type="pre-push-review:codex-reviewer", model="sonnet" を起動してください
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

対応: `run_in_background: true` を使わず、 wrapper を plain foreground の単独コマンドとして再実行してください。 wrapper は内部で codex companion を `--wait` で foreground 起動するため、 Bash 呼び出し自体が review 完了まで block しますが、 これが本プラグインの想定する正しい使い方です (= review 結果を観察してから push 判断する)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-review:codex-reviewer` subagent (model: "sonnet") を再起動してください。
EOF
)
elif [ "$HAS_INDIRECTION" -eq 1 ]; then
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper を shell-level の `&` (background) や `|` (pipeline) で起動することはできません。

理由: `bash run-codex-review.sh &` のような shell-level backgrounding、 `bash run-codex-review.sh | tee log` のような pipeline で wrapper を起動すると、 Bash tool option `run_in_background: false` で呼び出しても shell が wrapper を別 process で並列起動したり、output を別 process が変換したりするため、 **codex-reviewer subagent が wrapper の verdict / findings を観察しない / 不完全にしか観察しない** 経路ができます。pending attestation と schema 上の report だけが揃っても、report の根拠となる review output を完全に観察した保証がないため、foreground review 要件に反します。

本コマンドはコマンド置換 `$(...)` 等の間接実行 (indirection) を含むため、 `&` / `|` が wrapper 呼び出しの直前・直後に隣接していなくても **位置を問わず** deny しています (indirection 経由の実行は、 substring を含む segment と実際に wrapper を実行する segment の対応関係を本 parser が追跡できないため、 保守的に deny する必要があります)。

対応: `&` / `|` を外して wrapper を plain foreground の単独コマンドとして再実行するか、 `&&` (success-and) / `;` (sequential) で連結してください。 logging が必要なら file redirection (`bash run-codex-review.sh > codex.log 2>&1`) で代替できます (= wrapper 完了後に file を読めば review 結果を観察可能)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-review:codex-reviewer` subagent (model: "sonnet") を再起動してください。
EOF
)
else
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper を shell-level の `&` (background) や `|` (pipeline) で起動することはできません。

理由: `bash run-codex-review.sh &` のような shell-level backgrounding、 `bash run-codex-review.sh | tee log` のような pipeline で wrapper を起動すると、 Bash tool option `run_in_background: false` で呼び出しても shell が wrapper を別 process で並列起動したり、output を別 process が変換したりするため、 **codex-reviewer subagent が wrapper の verdict / findings を観察しない / 不完全にしか観察しない** 経路ができます。pending attestation と schema 上の report だけが揃っても、report の根拠となる review output を完全に観察した保証がないため、foreground review 要件に反します。

対応: `&` / `|` を外して wrapper を plain foreground の単独コマンドとして再実行するか、 `&&` (success-and) / `;` (sequential) で連結してください。 logging が必要なら file redirection (`bash run-codex-review.sh > codex.log 2>&1`) で代替できます (= wrapper 完了後に file を読めば review 結果を観察可能)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-review:codex-reviewer` subagent (model: "sonnet") を再起動してください。
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

#!/bin/bash
# first-party-review.sh
# pre-push-review プラグインの「第一者 (Anthropic) review 要件」を決定する。
#
# ## 背景: /simplify と /code-review は version 帯で意味が違う
#
# Claude Code の bundled skill は履歴上 2 度反転している (一次情報: 公式 CHANGELOG):
#   - ≤ 2.1.145 : /simplify = cleanup-and-fix (コードを編集)、/code-review は存在しない
#   - 2.1.147   : /simplify を /code-review に「リネーム」。ただし cleanup-and-fix 挙動は
#                 削除され、/code-review は read-only の correctness バグ検出器になった。
#                 = この帯では /simplify が消滅し /code-review (read-only) だけが存在
#   - 2.1.154   : /simplify が cleanup-only (編集する) skill として再導入。/code-review
#                 (read-only バグ検出) と併存。= 以降は両者が別物として両立
#
# つまり「リネーム」は実態としては「役割の分岐」だった。pre-push-review は
#   - /simplify   → .claude-pre-push-simplified     (cleanup・コードを編集する)
#   - /code-review → .claude-pre-push-code-reviewed  (read-only バグ検出)
# の 2 つを別マーカーとして扱う (auto-mark.sh の case 分岐がそれぞれを書き分ける)。
#
# ## この lib が解く問題: 両方を必須化すると旧 version 帯で永久 deny になる
#
# 2.1.154+ では /simplify と /code-review が両方存在するので「両方必須」を gate できる
# (= 案 B defense-in-depth: Anthropic cleanup + Anthropic バグ検出 + OpenAI codex バグ検出
#  + security の 4 レビュー)。 しかし旧 version 帯では片方の skill が存在しない:
#   - ≤ 2.1.145     : /code-review が無い  → code-reviewed マーカーを書けない
#   - 2.1.147-2.1.153: /simplify が無い    → simplified マーカーを書けない
# この帯で「両方必須」を強制すると、存在しない skill のマーカーが永遠に埋まらず push 不能
# (= permanent deny) になる。これは user-hostile。
#
# ## 解法: version をエスカレーション専用シグナルにし、fail-open で緩める
#
# 本 lib の `pre_push_review_require_both_first_party` は **CC >= 2.1.154 を肯定的に確認
# できたときだけ 0 (= 両方必須に昇格)** を返す。それ以外 (旧 version / version 不明 /
# 検出失敗) はすべて 1 (= どちらか 1 本で可、lenient に降格) を返す。
#
# この非対称が安全性の肝:
#   - 検出が成功して 2.1.154+ と分かった場合のみ「両方必須」に **昇格**
#   - 検出が失敗 / 不明な場合は「1 本で可」に **降格** = 案 A 相当の強度に落ちるだけで、
#     codex + security + 第一者 1 本は常に必須なので **未レビュー push は決して通らない**
#     し、存在しない skill を要求して **永久 deny にもならない**
#
# これは MEMORY の教訓 [[reference-prompt-hook-model-spof]] (「外部の可用性に依存する判定は
# SPOF。失敗時に全体を止める設計にしない」) と整合する。version 検出は **緩める方向にのみ**
# 倒れ、gate を硬直させない。version 文字列の取得手段が将来変わって検出が壊れても、最悪
# 「2.1.154+ なのに 1 本で通せてしまう (= 案 A 相当)」に劣化するだけで、push gate 自体は
# 生き続ける。
#
# ## version の取得: fork なしの env 優先
#
# `claude --version` を fork すると (1) ~数百 ms の起動コスト、(2) claude hook 内から claude
# を起動する再入、(3) 出力フォーマット変更で壊れる、というリスクがある。代わりに Claude Code
# が子プロセスへ継承させる env 変数から version を読む (fork ゼロ):
#   1. $CLAUDE_CODE_VERSION         (もし hook 環境に export されていれば最も直接的)
#   2. $AI_AGENT = claude-code_2-1-161_agent (install レイアウト非依存。dash を dot に変換)
#   3. $CLAUDE_CODE_EXECPATH = .../versions/2.1.161 (native installer のパス成分)
# いずれも取れなければ version 不明 → 呼び出し側は lenient に倒す。

# 検出した Claude Code version を "X.Y.Z" 形式で stdout に出す。取得できなければ return 1。
# 副作用なし・fork なし (env 変数の参照とパラメータ展開のみ)。
pre_push_review_detect_cc_version() {
  # 1. 直接 env (存在すれば最優先)。"2.1.161 (Claude Code)" のように後続語を持つ可能性が
  #    あるため、先頭の空白までを version token として取り出す。
  if [ -n "${CLAUDE_CODE_VERSION:-}" ]; then
    local v="${CLAUDE_CODE_VERSION%% *}"
    if [ -n "$v" ]; then
      printf '%s' "$v"
      return 0
    fi
  fi

  # 2. AI_AGENT=claude-code_<ver>_agent (例: claude-code_2-1-161_agent)。
  #    install 方式 (native / npm) に依存せず version 文字列から構築されるため最も堅牢。
  #    dash 区切り (2-1-161) を dot 区切り (2.1.161) に変換する。`tr` を 1 回 fork するが
  #    push gate は低頻度なので許容 (bash 3.2 の ${//} 置換に関する既知の落とし穴を避ける)。
  case "${AI_AGENT:-}" in
    claude-code_*_agent)
      local raw="${AI_AGENT#claude-code_}"
      raw="${raw%_agent}"
      local dotted
      dotted=$(printf '%s' "$raw" | tr '-' '.')
      if [ -n "$dotted" ]; then
        printf '%s' "$dotted"
        return 0
      fi
      ;;
  esac

  # 3. CLAUDE_CODE_EXECPATH=.../versions/2.1.161 (native installer のレイアウト)。
  #    npm install ではパスに version が現れないため最後の手段。
  case "${CLAUDE_CODE_EXECPATH:-}" in
    */versions/*)
      local base="${CLAUDE_CODE_EXECPATH##*/versions/}"
      base="${base%%/*}"
      case "$base" in
        [0-9]*.[0-9]*.[0-9]*)
          printf '%s' "$base"
          return 0
          ;;
      esac
      ;;
  esac

  return 1
}

# CC version >= 2.1.154 を肯定的に確認できたら 0 (= 第一者 review を simplify + code-review
# の両方必須に昇格)、それ以外はすべて 1 (= どちらか 1 本で可、fail-open lenient)。
#
# 2.1.154 は /simplify (cleanup) と /code-review (bug) が初めて併存した version。これ未満では
# どちらか一方の skill しか存在しないため、両方必須にすると永久 deny になる (上記コメント参照)。
pre_push_review_require_both_first_party() {
  local ver
  ver=$(pre_push_review_detect_cc_version) || return 1
  [ -n "$ver" ] || return 1

  local major minor patch
  IFS='.' read -r major minor patch <<EOF
$ver
EOF
  # patch には "161-beta" のような suffix が付く可能性があるので、先頭の数字列のみ採用する。
  patch="${patch%%[!0-9]*}"
  # 3 成分のいずれかが空 / 非数字なら判定不能 → lenient に倒す。
  case "$major" in ''|*[!0-9]*) return 1 ;; esac
  case "$minor" in ''|*[!0-9]*) return 1 ;; esac
  case "$patch" in ''|*[!0-9]*) return 1 ;; esac

  # 数値比較 (bash 3.2 の整数比較)。2.1.154 を閾値に major.minor.patch を辞書式に比較。
  if [ "$major" -gt 2 ]; then return 0; fi
  if [ "$major" -lt 2 ]; then return 1; fi
  # major == 2
  if [ "$minor" -gt 1 ]; then return 0; fi
  if [ "$minor" -lt 1 ]; then return 1; fi
  # 2.1.x
  if [ "$patch" -ge 154 ]; then return 0; fi
  return 1
}

#!/bin/bash
# codex-companion-resolver.sh
# 公式 codex プラグインの `codex-companion.mjs` を **install 形態 (cache / marketplace
# clone) に依存せず** に発見する共通ユーティリティ。
#
# 用途: pre-push-review v1.1.0 で `/codex:review` slash command 経由ではなく、 codex
# companion を **直接 Bash で叩く wrapper** (run-codex-review.sh) を案内する設計に変えた
# (背景は run-codex-review.sh のヘッダ参照)。 wrapper / hook script はいずれも codex
# プラグインの ${CLAUDE_PLUGIN_ROOT} を直接参照できないため、 ここで自前 path 解決を行う。
#
# ## なぜ resolver を別 lib に切り出すか
#
# 現状 caller は run-codex-review.sh の 1 つのみだが、 path 解決ロジック (versioned cache
# の semver 降順探索 + marketplace clone へのフォールバック + 環境差吸収) は run-codex-review.sh
# 本体の review 実行責務とは独立した concern。 named unit として lib に切り出すことで、
# run-codex-review.sh 本体が「companion を呼んで marker を書く」 という主目的に集中でき
# 可読性が上がる (= 「現時点で責務が明確に分かれている」 ことが lib 化の根拠であり、 「将来
# の柔軟性のために」 ではない)。
#
# ## 探索順序と semver 解釈
#
# 1. **versioned cache** (`~/.claude/plugins/cache/openai-codex/codex/<version>/scripts/codex-companion.mjs`):
#    `claude plugin install` で marketplace 経由導入した場合のレイアウト。 複数 version が
#    並ぶ可能性があるため semver 降順で最新を選ぶ。 「semver で最新を選ぶ」 原則と整合
#    させるため、 unversioned より優先する (両 layout が共存する環境で古い unversioned が
#    新しい versioned を上書きしないよう順序設計)。
# 2. **unversioned cache** (`~/.claude/plugins/cache/openai-codex/codex/scripts/codex-companion.mjs`):
#    Claude Code の install 形態によっては version dir を経由せず cache_root 直下に scripts/
#    が置かれるパターン。 codex プラグインの version が固定された install で観測される。
#    versioned cache に何も無い (or companion が欠落) 場合の fallback。
# 3. **marketplace clone (フォールバック)**: `~/.claude/plugins/marketplaces/openai-codex/
#    plugins/codex/scripts/codex-companion.mjs`。 marketplace を `claude plugin marketplace
#    add` で clone した状態 (= unversioned working tree) の path。 ローカル開発ユース
#    ケース等で cache が無いことが起きうるため、 セーフティネットとして用意する。
#
# **semver 降順** で最新を選ぶ。 `sort -V` (GNU 拡張 / macOS Sonoma 以降) が使える環境では
# それを使い、 使えない環境では本 lib 内 `_compare_semver_desc` で 3 成分 (major.minor.patch)
# の数値降順を計算する fallback に倒す (v2.0.0 で導入)。 `_compare_semver_desc` は数値比較で
# major / minor / patch を順に評価するため、 `1.10.x > 1.2.x` (semver) を正しく判定する。
#
# **v1.x までの lex 降順 fallback の問題**: 旧実装は `sort -V -r 2>/dev/null || sort -r` で
# lex 降順に fallback していたが、 lex 順では `1.2 > 1.10` となるため codex プラグインが
# 1.10 以降を release した時点で BSD sort 環境 (macOS Ventura 以前) で古い `1.2.x` が選ばれ、
# 古い companion で新しい review.md を呼ぼうとして失敗する経路があった (audit #5)。 v2.0.0 で
# 数値比較 fallback に置換して根本修正。 GNU `sort -V` が使える環境では従来通り high-perf な
# 単一 pipeline で済ませ、 fallback は probe ベースで 1 度だけ判定する。
#
# ## 失敗時の挙動
#
# 全候補で companion が見つからなければ非ゼロ exit。 caller は人間可読なエラーメッセージ
# を出して中断する責務を持つ (= 「codex プラグインが install されていない」 と判明できる)。
# silent skip は **しない** (pre-push-review の loop discipline 維持の観点で、 「codex
# review が実行できない」 ことを silent に通すと未レビュー push の経路を作る)。

# _pre_push_review_semver_desc_sort_dirs <cache_root>
# 出力 (stdout): cache_root 直下のディレクトリを semver 3 成分の数値降順で 1 行ずつ出力。
# GNU `sort -V -r` が使える環境ではそれを使い、 BSD sort で `-V` が拒否された場合のみ
# bash 内で `awk` を用いて 3 成分の数値降順を計算する fallback パスに倒す。
#
# fallback 経路: `awk` で各行の basename を取り出して `major.minor.patch` を抽出 → 「数値
# 比較ができる形 (zero-padded key)」 を行頭に prepend → `sort -r` (lex 降順) で並べる → key を
# 剥がして元 path を出力。 zero-padding は major.minor.patch を 6 桁固定にすることで
# `1.10.x` が `1.02.x` より大きいと lex 順でも正しく評価される。 6 桁は codex の semver
# 想定範囲 (各成分 0-999999) を十分カバーする。
#
# semver 違反 (`-alpha.1` などの prerelease) や 3 成分以外の version 文字列は数値抽出に
# 失敗し fallback key が `000000.000000.000000` になるため、 並びとしては最下位に落ちる。
# codex プラグインの version は現状 `<major>.<minor>.<patch>` のみで運用されているため
# この pre-release fallback で問題は出ない。 もし pre-release が導入されたら、 そもそも
# GNU `sort -V` が semver 標準に従って正しく順位付けするため fallback 経路は使われない。
_pre_push_review_semver_desc_sort_dirs() {
  local cache_root="$1"
  local files
  files=$(find "$cache_root" -mindepth 1 -maxdepth 1 -type d 2>/dev/null) || return 1
  [ -n "$files" ] || return 0
  # まず GNU `sort -V -r` を試す (高速かつ正確)。 失敗時 (BSD sort) は awk fallback。
  local sorted
  if sorted=$(printf '%s\n' "$files" | sort -V -r 2>/dev/null); then
    printf '%s' "$sorted"
    return 0
  fi
  # fallback: awk で zero-padded semver key を行頭に prepend して数値降順を lex 順で代用。
  # basename ごとに `major.minor.patch` を抽出し、 それぞれを 6 桁 zero-pad して連結。
  # 抽出できない行は `000000000000000000` (最小キー = 並びの末尾) に倒す。
  printf '%s\n' "$files" | awk '
    {
      n = split($0, parts, "/")
      base = parts[n]
      if (match(base, /^[0-9]+\.[0-9]+\.[0-9]+/)) {
        ver = substr(base, RSTART, RLENGTH)
        split(ver, c, ".")
        key = sprintf("%06d%06d%06d", c[1], c[2], c[3])
      } else {
        key = "000000000000000000"
      }
      printf "%s\t%s\n", key, $0
    }
  ' | sort -r | awk -F'\t' '{print $2}'
}

# 引数: なし
# 出力 (stdout): codex-companion.mjs の絶対パス
# exit code: 0 = 発見、 1 = 未発見
resolve_codex_companion() {
  # ${HOME} unset / 空時に cache_root が `/.claude/plugins/cache/openai-codex/codex` という
  # システム絶対パスに化けると、 root 環境では偶然存在する別ディレクトリを誤検出する経路や、
  # 「codex プラグインが見つかりません」 メッセージで真因 (HOME 不在) を隠蔽する経路ができる。
  # 通常運用 (Claude Code セッション内 / ユーザシェル) では HOME は必ずセットされているが、
  # CI / docker / init script / setuid 起動等で発火しうるため明示的に guard する。
  if [ -z "${HOME:-}" ]; then
    return 1
  fi
  local cache_root="${HOME}/.claude/plugins/cache/openai-codex/codex"
  local marketplace_path="${HOME}/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs"
  local found=""

  if [ -d "$cache_root" ]; then
    # ## 2 つの cache layout を順に探索
    #
    # Claude Code の plugin cache は install 形態によって 2 種類の layout がある:
    #   1. **versioned cache** (`<cache_root>/<version>/scripts/codex-companion.mjs`):
    #      version 別に dir を分ける形態。 複数 version が並びうる (= 最新を semver 降順で選ぶ)。
    #   2. **unversioned cache** (`<cache_root>/scripts/codex-companion.mjs`): marketplace
    #      経由で導入したが version dir を経由しない形態。 version が固定された install。
    # どちらの layout も実環境で観測されるため、 両方を順に試す。
    #
    # **探索順序は versioned 優先**: 両 layout が同一 cache_root に共存する環境 (= 過去の
    # install で unversioned が残り、 その後 `claude plugin install` で新しい versioned が
    # 入った等) では、 versioned 側が新しい version を持つ可能性が高いため、 先に versioned
    # を scan する。 versioned に何も無いか、 companion が見つからない場合のみ unversioned に
    # fallback する。 「semver で最新を選ぶ」 という基本原則と整合させる設計。
    #
    # versioned cache: <cache_root>/<version>/scripts/codex-companion.mjs を semver 降順で探索。
    # find は基本 POSIX なので macOS / Linux 双方で動く。 mindepth/maxdepth は GNU 拡張だが
    # macOS の BSD find でも 10.5 以降サポートされているためどちらでも使える。
    #
    # sort は `sort -V -r` (semver 降順、 GNU 拡張 / macOS Sonoma 以降) を最初に試し、 BSD
    # sort で `-V` が拒否される環境では bash 内で数値 3 成分比較する `_semver_desc_sort` に
    # fallback する。 v1.x までの `sort -r` (lex 降順) fallback は `1.10.x` 系で `1.2.x` を
    # 最新と誤判定する既知バグがあり、 v2.0.0 で数値比較に置換した。
    local sorted
    sorted=$(_pre_push_review_semver_desc_sort_dirs "$cache_root")
    local d
    while IFS= read -r d; do
      [ -n "$d" ] || continue
      if [ -f "$d/scripts/codex-companion.mjs" ]; then
        found="$d/scripts/codex-companion.mjs"
        break
      fi
    done <<< "$sorted"

    # unversioned cache fallback (= cache_root 直下の scripts/codex-companion.mjs):
    # versioned で見つからなかった場合のみここに到達する。
    if [ -z "$found" ] && [ -f "$cache_root/scripts/codex-companion.mjs" ]; then
      found="$cache_root/scripts/codex-companion.mjs"
    fi
  fi

  if [ -z "$found" ] && [ -f "$marketplace_path" ]; then
    found="$marketplace_path"
  fi

  [ -n "$found" ] || return 1
  printf '%s' "$found"
  return 0
}

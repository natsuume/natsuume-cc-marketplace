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
# - run-codex-review.sh が起動時に path 解決して invoke するが、 block-pre-push.sh の
#   deny メッセージ生成側でも「companion が見つかるか」 を事前確認したいため、 共通化
#   しないと検知ロジックが drift する (片方は通るが片方は失敗、 等)
# - codex プラグインの将来の install 形態変更 (path 構造変更等) に追随する際に
#   修正箇所を 1 箇所に集約できる
#
# ## 探索順序と semver 解釈
#
# 1. **cache 配下 (versioned plugin install)**: `~/.claude/plugins/cache/openai-codex/
#    codex/<version>/scripts/codex-companion.mjs`。 `claude plugin install` で marketplace
#    から導入した場合のレイアウト。 複数 version が並ぶ可能性があるため最新を選ぶ。
# 2. **marketplace clone (フォールバック)**: `~/.claude/plugins/marketplaces/openai-codex/
#    plugins/codex/scripts/codex-companion.mjs`。 marketplace を `claude plugin marketplace
#    add` で clone した状態 (= unversioned working tree) の path。 ローカル開発ユース
#    ケース等で cache が無いことが起きうるため、 セーフティネットとして用意する。
#
# **semver 降順** で最新を選ぶ。 `sort -V` (GNU 拡張) が使える環境ではそれを使い、
# 使えない環境 (古い macOS の BSD sort 等) では文字列降順 (`sort -r`) にフォールバックする。
# 文字列降順は `1.10.x` < `1.2.x` (lex 順では `1.2` > `1.10`) という semver 違反を起こすが、
# 1.x 系の patch / minor 増加で十分実用に耐え、 致命的な誤選択にはならない (どちらの version
# でも `review` サブコマンドの I/F は安定している前提)。
#
# ## 失敗時の挙動
#
# 全候補で companion が見つからなければ非ゼロ exit。 caller は人間可読なエラーメッセージ
# を出して中断する責務を持つ (= 「codex プラグインが install されていない」 と判明できる)。
# silent skip は **しない** (pre-push-review の loop discipline 維持の観点で、 「codex
# review が実行できない」 ことを silent に通すと未レビュー push の経路を作る)。

# 引数: なし
# 出力 (stdout): codex-companion.mjs の絶対パス
# exit code: 0 = 発見、 1 = 未発見
resolve_codex_companion() {
  local cache_root="${HOME}/.claude/plugins/cache/openai-codex/codex"
  local marketplace_path="${HOME}/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs"
  local found=""

  if [ -d "$cache_root" ]; then
    # `sort -V` の可用性チェック: 空入力で sort -V を試し、成功すれば semver sort 利用可能。
    # GNU coreutils なら成功、 古い BSD sort なら `invalid option -- 'V'` で失敗する。
    local sort_opts="-r"
    if printf '' | sort -V >/dev/null 2>&1; then
      sort_opts="-V -r"
    fi
    # find は基本 POSIX なので macOS / Linux 双方で動く。 mindepth/maxdepth は GNU 拡張だが
    # macOS の BSD find でも 10.5 以降サポートされているため WSL2/macOS 両対応で使える。
    local sorted
    # shellcheck disable=SC2086  # $sort_opts は word splitting させたい
    sorted=$(find "$cache_root" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort $sort_opts)
    local d
    while IFS= read -r d; do
      [ -n "$d" ] || continue
      if [ -f "$d/scripts/codex-companion.mjs" ]; then
        found="$d/scripts/codex-companion.mjs"
        break
      fi
    done <<< "$sorted"
  fi

  if [ -z "$found" ] && [ -f "$marketplace_path" ]; then
    found="$marketplace_path"
  fi

  [ -n "$found" ] || return 1
  printf '%s' "$found"
  return 0
}

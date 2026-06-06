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
# 1. **cache 配下 (versioned plugin install)**: `~/.claude/plugins/cache/openai-codex/
#    codex/<version>/scripts/codex-companion.mjs`。 `claude plugin install` で marketplace
#    から導入した場合のレイアウト。 複数 version が並ぶ可能性があるため最新を選ぶ。
# 2. **marketplace clone (フォールバック)**: `~/.claude/plugins/marketplaces/openai-codex/
#    plugins/codex/scripts/codex-companion.mjs`。 marketplace を `claude plugin marketplace
#    add` で clone した状態 (= unversioned working tree) の path。 ローカル開発ユース
#    ケース等で cache が無いことが起きうるため、 セーフティネットとして用意する。
#
# **semver 降順** で最新を選ぶ。 `sort -V` (GNU 拡張) が使える環境ではそれを使い、 使えない
# 環境 (古い macOS の BSD sort 等) では文字列降順 (`sort -r`) にフォールバックする。 実装上は
# `sort -V -r 2>/dev/null || sort -r` の `||` chain で 1 行に圧縮しており、 probe する必要が
# ないため簡潔。 文字列降順は `1.10.x` < `1.2.x` (lex 順では `1.2` > `1.10`) という semver
# 違反を起こすが、 1.x 系の patch / minor 増加で十分実用に耐え、 致命的な誤選択にはならない
# (どちらの version でも `review` サブコマンドの I/F は安定している前提)。
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
    # find は基本 POSIX なので macOS / Linux 双方で動く。 mindepth/maxdepth は GNU 拡張だが
    # macOS の BSD find でも 10.5 以降サポートされているためどちらでも使える。
    #
    # sort は `sort -V -r` (semver 降順、 GNU 拡張) を最初に試し、 BSD sort で `-V` が拒否される
    # 環境では `sort -r` (lex 降順) に fallback する。 stderr を 2>/dev/null で抑止することで
    # 「probe してから本実行」 の 2 段階を避け、 1 行の `||` chain にまとめている。 lex 降順は
    # `1.10 < 1.2` という semver 違反を起こすが、 codex プラグインの 1.x 系 patch / minor 増加
    # に対しては十分実用に耐え、 致命的な誤選択にはならない (どちらの version でも `review`
    # サブコマンドの I/F は安定している前提)。
    local sorted
    sorted=$(find "$cache_root" -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
      | { sort -V -r 2>/dev/null || sort -r; })
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

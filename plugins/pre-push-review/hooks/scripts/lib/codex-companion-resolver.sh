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
# ないため簡潔。
#
# **lex 降順 fallback の既知の限界**: `1.10.x` < `1.2.x` (lex 順では `1.2` > `1.10`) という
# semver 違反を起こす。 codex プラグインが 1.0.x → 1.9.x の patch / minor を続けている間は
# lex でも正しく最新を選べるが、 **1.10 以降が release された時点で BSD sort 環境では古い
# `1.2.x` 等を最新と誤判定する**。 codex 1.0.x の review CLI I/F は安定している前提なので
# 「致命的な誤動作」 にはならないが、 codex 側で互換破壊変更が入った場合は古い companion で
# 新しい review.md を呼ぼうとして失敗する経路ができる。 macOS Sonoma (14, 2023) 以降の sort
# は `-V` をサポートするため、 影響範囲は macOS 13 (Ventura) 以前の旧環境に限定される。
# 1.10 release が現実に迫ったタイミングで、 fallback を「probe → 確定」 形に作り直す TODO。
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
    # find は基本 POSIX なので macOS / Linux 双方で動く。 mindepth/maxdepth は GNU 拡張だが
    # macOS の BSD find でも 10.5 以降サポートされているためどちらでも使える。
    #
    # sort は `sort -V -r` (semver 降順、 GNU 拡張) を最初に試し、 BSD sort で `-V` が拒否される
    # 環境では `sort -r` (lex 降順) に fallback する。 stderr を 2>/dev/null で抑止することで
    # 「probe してから本実行」 の 2 段階を避け、 1 行の `||` chain にまとめている。 lex 降順
    # fallback は codex 1.0.x - 1.9.x の patch / minor を順に並べる範囲では正しく最新を選ぶが、
    # **1.10 以降の release で BSD sort 環境では古い `1.2.x` 等を選ぶ既知の制約**がある (詳細は
    # 上部 docstring 参照)。 macOS Ventura (13) 以前の旧 BSD sort 環境かつ codex 1.10 release
    # 以降のタイミングで顕在化する。
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

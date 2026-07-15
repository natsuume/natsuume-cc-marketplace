#!/bin/bash
# rate-limit-status-resolver.sh
# rate-limit plugin (natsuume-plugins marketplace) の `scripts/codex-rate-limit.sh` を
# **install 形態 (cache / marketplace clone) に依存せず**に発見する resolver (issue #247)。
#
# codex-implementer plugin は他 plugin のファイルを ${CLAUDE_PLUGIN_ROOT} 越しに参照
# できない (plugin 間でファイルを共有する仕組みがない) ため、rate limit ガードの判定
# 部品である codex-rate-limit.sh (rate-limit plugin v0.2.0 で追加、issue #245) への
# パス解決を本 lib が担う。探索の原則・semver 降順ソートの実装は
# lib/codex-companion-resolver.sh (pre-push-review 由来) と同一パターンを踏襲する。
#
# ## 探索順序
#
# 1. **versioned cache**: `~/.claude/plugins/cache/natsuume-plugins/rate-limit/<X.Y.Z>/scripts/codex-rate-limit.sh`
#    を semver 降順で探索し、script が存在する最新 version を採用する
#    (v0.1.0 の cache には本 script が存在しないため、自然に次の候補へ進む)
# 2. **unversioned cache**: `~/.claude/plugins/cache/natsuume-plugins/rate-limit/scripts/codex-rate-limit.sh`
# 3. **marketplace clone (フォールバック)**:
#    `~/.claude/plugins/marketplaces/natsuume-plugins/plugins/rate-limit/scripts/codex-rate-limit.sh`
#
# semver 降順は POSIX numeric field sort (`sort -t. -k1,1nr -k2,2nr -k3,3nr`) を使う
# (`sort -V` は BSD sort 非対応のため使わない。詳細は codex-companion-resolver.sh の
# docstring を参照)。
#
# ## I/O 契約
#
# - 関数: `resolve_rate_limit_status_script` (引数なし)
# - stdout: codex-rate-limit.sh の絶対パス (発見時)
# - exit code: 0 = 発見 / 1 = 未発見 (caller は fail-closed で委任を中止し、
#   `claude plugin install rate-limit@natsuume-plugins` を案内する責務を持つ)
# - ${HOME} unset / 空の場合は誤検出防止のため即 return 1 (companion-resolver と同じ guard)
#
# ---------------------------------------------------------------------------
# Phase A (設計記述 commit): 上記が確定仕様。実装本体は Phase B で追加する。
# ---------------------------------------------------------------------------

resolve_rate_limit_status_script() {
  echo "[codex-implementer] rate-limit-status-resolver.sh は未実装です (issue #247 Phase A)。" >&2
  return 1
}

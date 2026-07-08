#!/bin/bash
# inject-temporary.sh
# SessionStart で「暫定ルール (temporary rules)」を注入する。
#
# 暫定ルールとは、Claude Code 本体や外部ツールの問題が修正されるまでの間だけ配送したい
# 一時的な作業規律であり、恒久規律 (always-*.md / discipline-*.md) と違い
# 「問題修正後にいつでも外せること」を第一要件とする。そのため本スクリプトは
# inject-always.sh から独立した配送経路を持ち、以下の撤去手順を成立させる:
#
#   - 個別撤去: hooks/prompts/temporary/ 配下の対象 md を削除するだけで注入が消える
#     (スクリプトと hooks.json entry は残っても no-op で無害)
#   - 完全撤去: hooks.json の entry・本スクリプト・temporary ディレクトリを削除する
#   - いずれの場合も plugin version bump は必要 (プロジェクト CLAUDE.md の bump 規約)
#
# ## 注入仕様
#
# - hooks/prompts/temporary/*.md をファイル名の辞書順 (LC_ALL=C で固定) に連結して
#   1 つの additionalContext として注入する
# - モデル判定・permission_mode 判定は行わない (暫定ルールはモデルに依らず全セッション共通)。
#   inject-always.sh とは別 hook entry = 別メッセージとして注入されるため、
#   inject-always.sh 側の self-gate 射程 (「見出し〜メッセージ末尾」) には影響しない
#
# ## 出力 JSON 形状 (inject-always.sh と同形)
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "<temporary/*.md の連結本文>"
#     }
#   }
#
# ## fail-open 条件 (いずれも無音終了 exit 0、出力なし)
#
# - jq 不在
# - stdin が不正 JSON / hook_event_name が空
# - hooks/prompts/temporary/ ディレクトリ不在
# - temporary/*.md が 0 件、または連結結果が空
#
# 設計経緯: docs/temporary-rules-phase-a.md (Phase A 設計契約、Phase B で削除) を参照。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# Phase A (設計記述 commit) の no-op 骨格。実装本体は Phase B で追加する。
exit 0

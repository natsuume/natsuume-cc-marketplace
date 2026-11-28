#!/bin/bash
# text-paths.sh
# 変更がテキスト系文書 (Markdown 等の文書) だけで構成されるかを判定する関数集。source して
# 使う。push gate・merge gate・codex review wrapper は、この判定が成立した変更に対して
# レビュー記録を要求せずに通す (pre-push-review の push gate は security review の記録だけは
# 引き続き要求する)。
#
# canonical は pre-push-review (`plugins/pre-push-review/hooks/scripts/lib/text-paths.sh`) で、
# pre-push-codex-review と pre-merge-cross-review の `hooks/scripts/lib/` に byte-identical な
# コピーを置く。コピー先の plugin が持たない lib もあるため、他の lib に依存せず単独で source
# できる状態を保つ。
#
# Linux (WSL2) と macOS の bash 3.2 の両方で動く書き方にする (`${var,,}` 等の大文字小文字
# 変換の展開・連想配列・GNU 固有のコマンドオプションを使わない)。
#
# ## テキスト系文書の定義 (is_text_document_path)
#
# repo 相対 path の basename の拡張子 (最後の `.` より後ろ) が、ASCII の大文字小文字を区別
# せずに md / markdown / rst / adoc のいずれかであるもの。拡張子の無いファイル・mdx・txt は
# 含めない。
#
# ただし coding agent が指示として読む Markdown はコードとして扱い、次のいずれかに当たる
# path はテキスト系文書から除く (照合は ASCII の大文字小文字を区別しない):
#
#   - basename を除くディレクトリ要素のどれかが `agents` / `commands` / `skills` / `rules` /
#     `output-styles` / `.claude` / `.agents` / `.cursor` / `.gemini` / `.codex` / `.windsurf` /
#     `.clinerules` / `.roo` / `.kiro` / `.continue` / `.junie` / `.amazonq` に一致する
#     (深さは問わない)
#   - 連続する 2 つのディレクトリ要素が `hooks` → `prompts`、`.github` → `instructions`、
#     `.github` → `prompts`、`.github` → `chatmodes` のいずれか
#   - basename が `CLAUDE.md` / `CLAUDE.local.md` / `AGENTS.md` / `AGENTS.override.md` /
#     `GEMINI.md` / `copilot-instructions.md` のいずれか
#
# ## 判定の範囲 (is_text_only_range_in)
#
# `<base_ref>` と `<head>` の merge-base から `<head>` までの net diff で変わった path だけを
# 見る (途中の commit ごとの内容は見ない)。diff は次の形で取る:
#
#   git -c core.quotePath=false -c diff.relative=false diff --name-only -z --no-renames \
#     <merge-base> <head>
#
#   - `core.quotePath=false` と `-z`: 日本語のファイル名を引用符なしで受け取る
#   - `--no-renames`: rename を削除と追加に分け、コードを `.md` に rename した変更を文書だけと
#     判定しない
#   - `diff.relative=false`: 利用者の `diff.relative` の設定で、cwd の subdirectory の外の path
#     が欠けないようにする
#
# ## exit code
#
#   - is_text_document_path: 0 = テキスト系文書、1 = それ以外
#   - is_text_only_range_in: 0 = 変わった path がすべてテキスト系文書、1 = それ以外。
#     merge-base や diff の git が失敗した場合と、変わった path が 0 件 (net diff が空) の
#     場合も 1 (レビュー必須の側) にする
#
# ## 前提
#
# 利用者はリポジトリの持ち主本人であり、ローカルの状態 (ref・git の設定・履歴) や repo の
# 内容を意図的に細工することは想定しない。そのような細工で skip が成立しうることには対策
# しない。この lib が守るのは次の 3 点である:
#
#   - 普通の操作で誤って skip しない
#   - git が失敗したらレビュー必須の側 (1) に倒す
#   - gate と wrapper の判定が食い違わない (同じ関数を同じ引数の形で呼ぶ)
#
# ## 利用側
#
# gate と wrapper は is_text_only_range_in を、完全な ref 名の base と head で呼ぶ。
#
#   - pre-push-review の push gate (block-pre-push.sh): 空 push の skip の直後に、
#     detect_base_branch (diff-hash.sh) が返す base branch 名から作る
#     `refs/remotes/origin/<base>` と HEAD で判定する。0 なら code review の記録を要求せず、
#     security review の記録だけを要求する
#   - pre-push-codex-review の push gate (block-pre-push-codex.sh): 同じ引数で判定し、0 なら
#     codex review の記録を要求せずに通す
#   - pre-push-codex-review の wrapper (run-pre-push-codex-review.sh): 同じ引数で判定し、0 なら
#     codex review を起動せず、stdout に `Review skipped: text-only changes` の 1 行を出して
#     exit 0 で終わる (記録は書かない)
#   - pre-merge-cross-review の merge gate (block-pre-merge.sh): gh から取った PR の base branch
#     名 (`baseRefName`) から作る `refs/remotes/origin/<baseRefName>` と、PR の head SHA で判定
#     する。0 ならレビュー記録を照合せずに通す。base branch 名が使えない値のときや、head SHA が
#     40 桁の小文字 16 進数でないときは判定せずに記録の照合へ進む
#   - pre-merge-cross-review の wrapper (run-pre-merge-codex-review.sh): base の検証の後、
#     `refs/remotes/origin/<base>` と HEAD で判定し、0 なら codex review を起動せず、stdout に
#     `Review skipped: text-only changes` の 1 行を出して exit 0 で終わる (記録は書かない)

# is_text_document_path <repo 相対 path>
# path がテキスト系文書 (定義はファイルヘッダの「テキスト系文書の定義」) なら 0、それ以外は
# 1 を返す。
#
# 現在の本体は判定を持たない骨格で、常に 1 を返す (どの path もテキスト系文書と判定しない)。
is_text_document_path() {
  return 1
}

# is_text_only_range_in <target_cwd> <base_ref> <head>
# <base_ref> は完全な ref 名 (`refs/remotes/origin/master` 等)、<head> は `HEAD` か 40 桁の
# SHA。target_cwd が空なら現在の cwd で、空でなければ `git -C <target_cwd>` で git を実行する。
#
# 手順:
#   1. `git merge-base <base_ref> <head>` で merge-base を解決する。終了コードが 0 でなければ 1
#   2. merge-base から <head> までの net diff で変わった path を、ファイルヘッダの「判定の
#      範囲」の形で取る。終了コードを出力を読む前に確かめ、0 でなければ 1
#   3. path が 0 件なら 1
#   4. すべての path が is_text_document_path を満たせば 0、1 つでも満たさなければ 1
#
# 現在の本体は判定を持たない骨格で、常に 1 を返す (どの変更もテキスト系文書だけと判定
# しない)。
is_text_only_range_in() {
  return 1
}

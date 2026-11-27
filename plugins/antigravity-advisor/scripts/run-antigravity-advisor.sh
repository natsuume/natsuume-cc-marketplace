#!/bin/bash
# run-antigravity-advisor.sh
# antigravity-advisor plugin の相談 wrapper。antigravity-advisor-runner subagent が
# foreground の Bash 1 回で起動し、相談 request のファイルを Antigravity CLI (agy) に
# plan mode で読ませて、助言の本文だけを stdout に返す。
#
# ## 使い方
#
#   bash run-antigravity-advisor.sh <request file>
#
# ## 引数
#
# - ちょうど 1 つ。相談 request を書いたファイルのパス
# - 引数のパスそのものが symlink でなく、存在する通常ファイルで、空 (0 byte) でないこと。
#   親ディレクトリの経路に含まれる symlink (macOS の /var -> /private/var 等) は問わない
# - 違反は exit 2
#
# ## 環境変数
#
# - ANTIGRAVITY_ADVISOR_MODEL: 空または未設定なら agy に --model を渡さず、agy の既定の
#   model を使う。非空なら `^[A-Za-z0-9._-]+$` に一致する場合だけ `--model <値>` として
#   渡し、一致しなければ agy を起動せず exit 2
# - TMPDIR: wrapper 専用の一時ディレクトリを `${TMPDIR:-/tmp}` の直下に `mktemp -d` で
#   作る。一時ディレクトリは EXIT trap で削除し、成功・失敗のどちらでも残さない
#
# ## 必要なコマンド
#
# - agy と jq が PATH 上にあること。どちらかが無ければ agy を起動せず exit 3。どちらが
#   無いのかは stderr のメッセージで区別する
# - git は cwd が git の work tree 内のときだけ使う
#
# ## 処理
#
# 1. 一時ディレクトリを作り、request をそこへコピーする
# 2. cwd が git の work tree 内なら、`git rev-parse HEAD` と
#    `git status --porcelain=v1 -z --untracked-files=all` の出力を記録する。
#    work tree の外なら記録も比較もしない
# 3. cwd を変えずに次を実行する。`-p` は直後の引数を値に取り、続けて別のフラグを
#    置くとエラーになるため、`-p <prompt>` をフラグ列の最後に置く。
#
#      agy --mode plan --output-format json [--model <model>] \
#        --add-dir <一時ディレクトリ> --print-timeout 25m -p <prompt>
#
#    prompt は英語の固定文で、コピーした request の絶対パスを含み、request の本文は
#    含まない。prompt は次を指示する:
#    - read-only の advisor として振る舞う
#    - shell コマンドを一切実行せず、ファイルの作成・編集もしない。読み取りのツールだけを使う
#    - コピーした request ファイルを読み、その問いに答える
#    - 必要ならカレントディレクトリ (リポジトリ) のファイルを読んで裏取りする
#    - request と同じ言語で答える
#    - 計画の承認待ちをせず、実装計画の artifact を作らない
# 4. 2 で記録した場合は、agy の終了後に同じ 2 つの出力を取り直して比較する
# 5. agy の stdout (JSON 1 つ) を jq で検査し、`.response` を取り出す
#
# agy の成功時の stdout は
# `{"conversation_id":str,"status":"SUCCESS","response":str,"duration_seconds":num,"num_turns":int,"usage":{...}}`
# の JSON 1 つである。plan mode で shell の実行を試みると拒否され、`status` は
# "SUCCESS" のまま `"denied_actions":[{"action":"command","display_name":"Bash"}]` が
# 付き、応答は途中で終わる (2026-11-27 agy 1.5.18 / WSL2 で確認)。
#
# ## 終了コード
#
# | exit | runner の Failure class | 条件 |
# |---|---|---|
# | 0 | (成功) | 下のいずれにも該当しない |
# | 2 | invalid-request | 引数が不正、または ANTIGRAVITY_ADVISOR_MODEL が不正 |
# | 3 | agy-unavailable | agy または jq が PATH に無い |
# | 4 | agy-failed | agy が非 0 で終了した |
# | 5 | workspace-modified | cwd が git の work tree 内で、agy の実行前後に HEAD または working tree の状態が変わった |
# | 6 | incomplete-response | agy の stdout が JSON でない、`.status` が "SUCCESS" でない、`.denied_actions` が空でない、または `.response` が空 |
#
# 上の表にない失敗 (一時ディレクトリの作成や request のコピーの失敗等) は exit 1 とし、
# runner は Failure class を other とする。
#
# ## stdout / stderr
#
# - stdout: exit 0 のときだけ `.response` の本文を出す。それ以外の内容は出さない。
#   0 以外で終了するときは stdout に何も出さない
# - stderr: 進捗と失敗の理由を日本語で出す
#
# ## 制約
#
# Linux (WSL2 含む) と macOS (bash 3.2 / BSD ツール) の両方で動くこと。bash 4 以降の
# 拡張 (連想配列等) と GNU 専用のオプションを使わない。mktemp の `-t` は GNU と BSD で
# 意味が違うため使わず、`${TMPDIR:-/tmp}/<prefix>.XXXXXX` の形のテンプレートを明示する。

printf '%s\n' "[run-antigravity-advisor] wrapper の本体は未実装です" >&2
exit 1

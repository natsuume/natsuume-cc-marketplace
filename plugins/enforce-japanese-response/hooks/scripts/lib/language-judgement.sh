#!/bin/bash
# language-judgement.sh — 目標言語の解決と英語の判定 (source して使う。直接実行しない)
#
# Stop hook (enforce-japanese-response.sh) と MessageDisplay hook
# (record-english-message.sh) が、同じ基準で「目標言語が日本語か」「本文が英語か」を
# 決めるための共有ライブラリー。判定基準・除去処理・言語の解決順の実装はこのファイルに
# まとめ、各 hook はここの関数を呼ぶ。
#
# bash と jq のみに依存し、Linux (WSL2) と macOS (bash 3.2 / BSD ツール) の両方で動く
# ように書く。文字種の数え上げと除去処理は jq の中で行い、sed / grep / awk の GNU 拡張に
# 依存しない。呼び出し側は jq があることを確認してから使う。
#
# ## 提供する関数 (I/O 契約)
#
# - resolve_project_dir <cwd>
#     プロジェクトの settings を探す基準ディレクトリーを 1 行で出力する。env
#     `CLAUDE_PROJECT_DIR` が空でなければその値、空または未設定なら引数の <cwd>。
#     どちらも空なら空文字列を出力する
# - read_language_setting <settings_file>
#     settings ファイル 1 つから `language` の値を JSON で出力する。ファイルが無い・
#     JSON object 1 つとして解析できない・`language` が無い (null を含む) ときは何も
#     出力しない
# - resolve_target_language <project_dir>
#     次の順に settings ファイルを探し、`language` キーを持つ最初のファイルの値を JSON で
#     出力する。どこにも無ければ何も出力しない。<project_dir> が空なら 1 と 2 を飛ばす
#       1. `<project_dir>/.claude/settings.local.json`
#       2. `<project_dir>/.claude/settings.json`
#       3. `$HOME/.claude/settings.json`
# - is_japanese_language <language_json>
#     language の値 (JSON) が日本語を表すなら `true`、それ以外なら `false` を出力する。
#     `日本語` / `ja` / `ja-` で始まる文字列 / `japanese` を日本語とみなす。英字の大文字
#     小文字は区別せず、前後の空白は取り除かない。文字列でない値は日本語以外とする
# - is_target_language_japanese <project_dir>
#     resolve_target_language と is_japanese_language を順に行い、目標言語が日本語なら
#     終了ステータス 0、日本語でない・未設定・判定できないなら非 0 を返す。何も出力しない
# - is_english_text
#     stdin に本文 (生のテキスト) を受け取り、次の除去処理と判定基準で英語と判定したら
#     終了ステータス 0、英語でない・判定できない (jq の失敗等) なら非 0 を返す。何も
#     出力しない
#
# ## 除去処理
#
# 本文から次の順に取り除いた残りを「除去後の本文」とする:
#   1. fenced code block: 行内の位置に依らず ``` から次の ``` までの範囲 (両端を含む)。
#      閉じる ``` が無い場合は、開始の ``` から本文末尾までを取り除く
#   2. インライン code: ` から次の ` までの範囲 (両端を含む。改行をまたいでもよい)
#   3. URL: `http://` または `https://` と、それに続く 1 文字以上の URL 本体。URL 本体は
#      印字可能な ASCII (0x21〜0x7E) のうち `(` `)` `<` `>` `[` `]` `"` を除いた文字の
#      並びで、空白文字・印字可能な ASCII 以外の文字 (日本語など)・上記 7 文字の
#      いずれかの直前で終わる
#
# ## 判定基準
#
# 除去後の本文について、jq の正規表現 (Oniguruma) で次を数える:
#   - L: ASCII 英字 `[A-Za-z]` の数
#   - J: ひらがな・カタカナ・漢字 `[\p{Hiragana}\p{Katakana}\p{Han}]` の数
# `L >= 40` かつ `J / (J + L) < 0.05` のとき英語と判定する。
# 比率は浮動小数点の丸めを避けるため、同値な整数比較 `20 * J < J + L` で評価する。

resolve_project_dir() {
  :
}

read_language_setting() {
  :
}

resolve_target_language() {
  :
}

is_japanese_language() {
  :
}

is_target_language_japanese() {
  return 1
}

is_english_text() {
  return 1
}

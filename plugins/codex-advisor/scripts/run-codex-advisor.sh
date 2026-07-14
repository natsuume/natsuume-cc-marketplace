#!/bin/bash
# run-codex-advisor.sh
# codex-advisor plugin の **Codex advisor 相談 (consultation) 実行 wrapper** (issue #219)。
# `/codex-advisor:consult` skill が Bash tool 経由で本 wrapper を foreground 起動し、
# 相談プロンプトを stdin から渡す。 Codex は read-only sandbox でリポジトリを自分で
# 読んで裏取りしたうえで、 plan / course-correction の助言テキストを返す。 実行 (ファイル
# 変更・コマンド実行) は一切行わない。
#
# ## I/O 契約
#
# - 標準入力 (stdin): 相談プロンプト全文をファイルからの stdin リダイレクト
#   (`< "/path/to/prompt.md"`) で渡す。 引数は一切受け取らない
#   (プロンプトを argv に乗せない設計。 shell quoting 事故や history 露出を避けるため
#   stdin 経由に統一する)
# - 標準出力 (stdout): Codex companion の stdout (= 助言テキスト) をそのまま流す。 wrapper
#   はこれを一切加工しない (呼び出し側が助言を verbatim で受け取れることが contract)
# - 標準エラー出力 (stderr): wrapper 自身の進捗 / エラーメッセージ (companion path・
#   実行コマンド・完了通知・失敗理由)。 stdout (助言) と stderr (wrapper 状態) を分離する
#   ことで、 呼び出し側が両者を混同しない設計は run-codex-review.sh と同一
# - 終了コード: companion が exit 0 で完了したら 0。 それ以外 (Node.js 不在 / stdin 未指定 /
#   空プロンプト / companion 未検出 / companion 失敗) は `fail()` 経由で人間可読メッセージを
#   stderr に出して 1
#
# ## 制約
#
# Linux (WSL2) / macOS (bash 3.2 / BSD ツール) の両方で動作すること。 bash 4+ 拡張
# (`${var//pattern/}` の複雑な parameter expansion、 連想配列等) や GNU 専用オプション
# (`sort -V` 等) は使わない。
#
# ## 設計判断
#
# - **reasoning effort は `xhigh` 固定**: advisor 用途は「戦略的な岐路での深い助言」が
#   目的であり、 浅い effort では pre-push-review の codex review (bug 検出) と差別化
#   できない。 issue #219 でユーザが xhigh 固定を確定させたため、 上書きフラグは設けない
#   (呼び出し側からの effort 引き下げは「相談の質を落とす」判断そのものであり、 wrapper が
#   軽々に許可する余地ではない)
# - **`--write` は付けない (read-only sandbox 固定)**: advisor は Claude の判断品質を
#   上げるための助言役であり、 実行主体ではない (advisor-rules.md の rule:advisor-boundary)。
#   companion に書き込み権限を与えると executor/advisor の役割分離が崩れるため、 sandbox は
#   常に read-only に固定する
# - **model は未指定**: issue #219 でユーザが「Codex 側の既定に委ねる」ことを確定済み。
#   model を固定すると Codex 側のデフォルト更新に追従できず、 陳腐化した model 指定が
#   silent に残るリスクがある
# - **git 状態を検査しない**: run-codex-review.sh (pre-push-review) は dirty tree / branch /
#   diff hash を検査するが、 それは「committed 差分をレビューする」ため。 本 wrapper の
#   相談は git 状態に依存しない (質問に git diff が絡むかどうかは呼び出し側がプロンプトに
#   含める判断であり、 wrapper 側で強制しない)。 git repository の外でも動作する
# - **marker ファイルを書かない**: pre-push-review の marker はレビュー gate の再実行防止
#   機構だが、 advisor 相談は gate ではなく都度の助言取得であり、 marker という永続状態を
#   持つ必要がない

set -e

_RUN_CODEX_ADVISOR_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/codex-companion-resolver.sh
source "$_RUN_CODEX_ADVISOR_SCRIPT_DIR/lib/codex-companion-resolver.sh"

# stderr に人間可読のエラーを出して非ゼロ exit する helper。 set -e と組み合わせて使う。
fail() {
  printf '%s\n' "[run-codex-advisor] $1" >&2
  exit 1
}

# usage を stderr に表示する helper (stdin 未指定 / 空プロンプトの両方から呼ぶため共通化)。
usage() {
  printf '%s\n' "[run-codex-advisor] 使い方: 相談プロンプトをファイルに書き出し、stdin リダイレクトで渡してください。引数は受け取りません。" >&2
  printf '%s\n' "[run-codex-advisor] 例: bash run-codex-advisor.sh < \"/path/to/prompt.md\"" >&2
}

# 「引数は受け取らない」契約 (usage に明記) を実装でも強制する。 stray な引数を silent に
# 無視すると、 プロンプトを argv に乗せた誤用が「成功したように見える」まま素通りするため
# (エンドツーエンド検証時の codex advisor 相談での指摘を反映)。
if [ "$#" -ne 0 ]; then
  usage
  fail "引数は受け取りません ($# 個の引数を検出)。相談プロンプトは stdin で渡してください。"
fi

command -v node >/dev/null 2>&1 || fail "Node.js が見つかりません。Codex companion の実行には Node.js が必要です。インストールしてから再実行してください。"

# stdin が TTY の場合は相談プロンプトが渡されていない (= 対話的に起動された) ため、
# usage を出して早期に fail する。 stdin をファイルからのリダイレクトで渡す運用を強制する。
if [ -t 0 ]; then
  usage
  fail "相談プロンプトが stdin から渡されていません (TTY 検出)。"
fi

PROMPT=$(cat)

# 空 / 空白のみの判定は bash 3.2 互換で行う (`${var//[[:space:]]/}` は bash 4 拡張のため
# 使わない)。 `tr -d '[:space:]'` で空白類を全て除去した結果が空文字かどうかで判定する。
if [ -z "$(printf '%s' "$PROMPT" | tr -d '[:space:]')" ]; then
  usage
  fail "相談プロンプトが空です。"
fi

# codex companion path 解決。 resolver は pre-push-review 由来のコピー (plugin 間でファイル
# 共有ができないため各 plugin が自前で持つ、詳細は lib/codex-companion-resolver.sh ヘッダ参照)。
COMPANION=$(resolve_codex_companion) || fail "codex プラグインが見つかりません。 \`claude plugin install codex@openai-codex\` で導入してください (versioned cache / unversioned cache / marketplace clone のいずれにも codex-companion.mjs が見つかりませんでした)。"

printf '[run-codex-advisor] codex companion: %s\n' "$COMPANION" >&2
printf '[run-codex-advisor] running: node %s task --effort xhigh\n' "$COMPANION" >&2

# `if !` で node の成否を直接捕捉する (set -e 配下でも失敗ブランチでカスタムメッセージを
# 出せる。 run-codex-review.sh と同じパターン)。 companion の stdout はそのまま本スクリプト
# の stdout に流し、 一切加工しない (呼び出し側が助言を verbatim で受け取る契約)。
if ! printf '%s' "$PROMPT" | node "$COMPANION" task --effort xhigh; then
  fail "codex companion の実行に失敗しました。codex CLI が未インストール / 未認証の可能性があります。\`/codex:setup\` で診断してください。"
fi

printf '[run-codex-advisor] codex advisor への相談が完了しました。\n' >&2

exit 0

#!/bin/bash
# inject-decompose-context.sh
# SessionStart で Bash コマンドを最小粒度に分解して呼び出すよう Claude に指示する
# additionalContext を注入する。
#
# 目的: Claude Code の PreToolUse hook は Bash ツールの command 文字列に対する
# パターンマッチで判定されるため、`A && B && C` のような合成コマンドは先頭
# 以外の部分が hook 検知を取りこぼす。各コマンドを独立した Bash 呼び出しに
# 分解することで、すべての PreToolUse hook が意図どおり機能するようにする。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name は入力からそのまま読み取る (既定値を埋めると別 event の
# 文脈に誘導する恐れがあるため)。INPUT が不正な JSON / 空の場合 jq は parse error を
# stderr に吐くため 2>/dev/null で抑制し、HOOK_EVENT 空判定でフォールバックさせる
# (hook の stderr は利用者に見えるため、解析失敗をノイズとして表に出さない)。
HOOK_EVENT=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // ""' 2>/dev/null)
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

CONTEXT=$(cat <<'EOF'
Bash コマンドは可能な限り分解し、それぞれを独立した Bash ツール呼び出しとして実行してください。

**なぜ**: Claude Code の PreToolUse hook は Bash ツールの `command` 文字列に対するパターンマッチで判定されます。複数コマンドを合成すると 1 回の Bash 呼び出しとして扱われ、先頭以外の部分が hook 検知から外れる可能性があります。例えば `git add foo.txt && git commit -m "..." && git push` は、`git push` を deny したい hook が先頭の `git add` パターンしか見ずに通過させてしまう恐れがあります。各コマンドを独立した Bash 呼び出しに分解すれば、リポジトリのガードレール (git-guardrails / pre-push-review / auto-lint-check 等) が意図どおり機能します。

**分解すべきパターン**:

- **異なる目的のコマンドを `&&` / `||` / `;` / `&` で連結しない**。サブシェル `(cmd1; cmd2)` やブレースグループ `{ cmd1; cmd2; }` も同等に扱う。`git add foo && git commit -m "msg" && git push` は **3 回の独立した Bash 呼び出し** に分解する (依存が無い独立コマンドは並列 Bash 呼び出しも可)
- **コマンド置換 `$(...)` / バッククォートで別コマンドを埋め込まない**。例えば `cat $(find . -name '.env')` ではなく、`find` で対象を確認したうえで個別に `Read` ツールで開く
- **ラッパー経由でコマンドを隠さない** (`eval "..."`, `bash -c "..."`, `sh -c "..."`, `sudo sh -c "..."` 等)。内側コマンドが `command` 文字列のパターンマッチから外れ、hook を素通りさせる典型経路になる
- **`xargs <cmd>` / `find -exec <cmd> {}` も hook 検知の観点ではシェル合成と同等**として扱う
- **パイプライン `|`** は単一論理操作 (例: `git log --oneline | head -20`, `grep foo | wc -l`) で使う場合のみ許容。必然性がなければ分解する

**例外** (連結を許容するケース):

- ディレクトリを移動した状態でコマンドを実行したい場合の `cd $dir && do_something` (`cwd` 制約のため)
- 前段の成功/失敗で後段を確実に制御する必要があるトランザクション的合成 (例: `make build && make test` のように、ビルド失敗時に必ずテストを止めたい場合)

例外時は合成を使う必然性が明確であることを前提とすること。汎用的な「タイプ数を減らす」「効率化」目的の連結は例外に該当しない。
EOF
)

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'

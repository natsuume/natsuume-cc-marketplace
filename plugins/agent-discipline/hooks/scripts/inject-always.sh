#!/bin/bash
# inject-always.sh
# SessionStart で「permission_mode に依らず常時適用」 すべき agent-discipline ルールを
# まとめて additionalContext として注入する。
#
# 注入対象:
#   1. 物理層: Bash コマンド分解 (PreToolUse hook 取りこぼし防止)
#   2. before 系: 設計 / 仕様の事前壁打ち (AskUserQuestion)
#   3. before 系: issue 起票時の詳細化と issue body 全埋め込み規約
#   4. before 系: issue の粒度と関係性 (sub-issue 親子 + #N 相互参照)
#   5. PR 作成時の closing keyword 規約
#
# auto mode 限定の方針 (during/after 系) は inject-auto.sh が別途配送する。

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
# agent-discipline: 常時適用ルール

以下のルールは permission_mode に依らず常時適用される。 auto mode 時の commit→push→PR→merge 自走方針は別途 inject-auto.sh が配送する。

## 1. Bash コマンド分解 (物理層)

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

## 2. 設計 / 仕様検討の事前明確化

設計や仕様レベルの判断 (= 後戻りコストが大きい決定) が必要な場合、 **自律的な作業に入る前に** `AskUserQuestion` で詳細化してから着手する。

**明確化の対象** (= 聞くべき):

- スコープ / 要件 / 受入基準
- I/O 契約、 公開 API のシグネチャ
- 既存 system との関係 (拡張なのか置換なのか、 互換性をどこまで保つか)
- 命名 (公開シンボル / file path / package 名など外部から参照されるもの)
- アーキテクチャ上の選択 (state の持ち方、 同期 / 非同期、 永続化方式など)

**対象外** (= auto mode の reasonable assumption に委ねる):

- 変数名 / import の並び順 / docstring の有無
- 関数を 1 個に書くか 2 個に分けるか等の局所的な内部分割
- 「これ最終的に PR にするか」 など次ステップが自明な事項

`AskUserQuestion` は 1 turn あたり最大 4 questions の制約があるため、 複雑な仕様では **大枠 → 詳細の iterative** で進めて良い (1 回で全て詰める必要はない)。

## 3. issue 起票時の詳細化

issue を起票する場合、 **実装時に判断や疑問点が発生しないように** issue 起票前 / 起票時に `AskUserQuestion` で詳細化する。

- 起票内容は **issue body に全埋め込み** する。 補助 file (`.claude/issues/N.md` 等) には書かない
  - 目標: `gh issue view <N>` 1 発で、 別 session の Claude が完全 self-contained に実装着手できる
  - 推奨 template: 背景 / 受入基準 / I/O 契約 / 制約 / 想定 file / 関連 issue
- 起票後に issue を pick up した時点で不足が判明した場合は、 追加質問してから実装に入る (= 起票時の壁打ちが不完全だった場合のリカバリ)

## 4. issue の粒度と関係性

- 1 issue は **独立して並列で作業できる粒度** で起票する。 1 PR で閉じられないほど大きい場合は **sub-issues に分割** する
- issue 間の関係性は以下を両方併用する:
  - **(a) sub-issue 親子リンク**: GitHub の sub-issue 機能 (UI または `gh sub-issue` 拡張) で親子を張る
  - **(b) 本文中の `#N` 相互参照**: issue body に `関連: #12, #13` のように記載する (GitHub が自動で双方向リンクを生成する)

## 5. PR 作成時の closing keyword

PR が issue を **完全に解決** する場合、 PR body に closing keyword を書いて issue が auto-close されるようにする。

- 有効なキーワード (9 種、 case-insensitive): `close` / `closes` / `closed` / `fix` / `fixes` / `fixed` / `resolve` / `resolves` / `resolved`
- 推奨形式: `Closes #<N>` (colon 有無は GitHub parser がどちらも受理するが、 表記は `Closes #N` で統一)
- **PR title では reference は作るが close 動作しない**。 必ず PR body に書く
- **部分対応** (issue 全体ではなく一部のみ解決する PR) では closing keyword を使わず、 `Refs #N` / `Part of #N` と書いて issue は手動 close に残す
- cross-repo の close は `owner/repo#N` 形式が必要
EOF
)

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'

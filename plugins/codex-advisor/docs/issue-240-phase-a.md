# issue #240 Phase A: 設計契約 (Phase B で本ファイルは削除する)

consult のプロンプト受け渡しを「ファイル書き出し + stdin リダイレクト」に固定し、
プロンプト本文が Bash の command 文字列に載ることによる guardrail hook の誤 deny を
構造的に回避する。

## 変更対象と各契約

### 1. skills/consult/SKILL.md (セクション 2「wrapper の起動」)

呼び出し手順を以下の 2 ステップに固定する (heredoc・引数直渡しの手順記述を残さない):

1. 相談プロンプト全文を **Write ツール**でセッションの scratchpad ディレクトリ配下の
   一時ファイル (例: `<scratchpad>/codex-advisor-consult.md`) に書き出す。
   プロジェクト内には作成しない
2. Bash ツールで `bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-codex-advisor.sh" < <プロンプトファイル絶対パス>`
   を foreground 実行する (timeout 600000 明示、`run_in_background` 禁止は現行どおり)

- command 文字列に載るのはファイルパスのみ (プロンプト本文を一切含めない)
- fallback (plugin cache から wrapper を解決する経路) も同じ stdin リダイレクトに変更する
- 失敗の表面化 (無音成功にならない根拠):
  - ファイル不在 → shell のリダイレクトエラーで Bash が非ゼロ終了し、エラーが可視化される
  - 空 / 空白のみのファイル → wrapper 既存の空プロンプト検査が stderr へ
    「相談プロンプトが空です」を出して exit 1 する
- 入出力仕様 (相談内容 → 助言テキスト) は不変。変更は受け渡し経路のみ

### 2. hooks/prompts/advisor-rules-subagent.md (「実行」項)

SKILL.md と同一の heredoc 手順が subagent へ配送されており、subagent の Bash 呼び出しにも
同じ guardrail が掛かるため同一の誤 deny 経路である。issue #240 で決定済みの方針
(プロンプト本文を command 文字列に載せない) をそのまま適用し、
「scratchpad へ Write → `bash {{WRAPPER_PATH_SH}} < <ファイルパス>`」の手順に置換する。
`{{WRAPPER_PATH_SH}}` の injector 置換仕様は変更しない。

### 3. scripts/run-codex-advisor.sh (usage 文言のみ)

- I/O 契約 (stdin からプロンプト受領・引数拒否・stdout に助言 verbatim・fail-closed) は**不変**
- ヘッダコメントと `usage()` の例示が heredoc を推奨したままだと、失敗時に呼び出し側を
  heredoc へ誘導し戻すため、例示を「ファイルからの stdin リダイレクト」に更新する

### 4. version bump (patch: 0.1.1 → 0.1.2)

- plugins/codex-advisor/.claude-plugin/plugin.json
- .claude-plugin/marketplace.json (plugins[].version)
- README.md の plugin 一覧テーブル

## 受入基準 (issue #240 より)

- SKILL.md の呼び出し手順が「Write → stdin リダイレクト」に固定され、heredoc・引数直渡しの
  手順記述が残らない
- プロンプト本文が Bash ツールの command 文字列に一切含まれない (コマンド側はファイルパスのみ)
- 一時ファイルの置き場所はセッションの scratchpad ディレクトリと明記される
- プロンプトファイルが空・不在の場合の失敗が利用者に分かる形で表面化する
- Linux (WSL2) / macOS の両方で動作する手順である

## スコープ判断の記録

issue の想定ファイルは SKILL.md + version 3 点のみだが、上記 2 (subagent 版規律) と
3 (usage 文言) は「プロンプト本文を command に載せない」という決定済み方針の同一適用であり、
残すと誤 deny 経路 (2) / heredoc への誘導 (3) が存続するため本 PR に含める。
guardrail hook 側・codex CLI オプション・wrapper の実行モード (read-only, effort xhigh) は
一切変更しない (issue の制約どおり)。

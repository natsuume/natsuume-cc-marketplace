# enforce-draft-pr プラグイン

`gh pr create` に `--draft` フラグを自動付与する PreToolUse フックプラグインです。「PR は必ず draft で起こし、レビュー後に手動で ready 化する」運用を強制したい場合に使います。

## バージョン

v0.5.2

## 概要

`PreToolUse` (matcher: `Bash`) で `gh pr create` の呼び出しを検知し、`--draft` が付いていなければコマンドを書き換えてフラグを追加します。`updatedInput.command` を返すため、Claude Code は書き換え後のコマンドで実行します。

git-guardrails プラグインの一部として提供されていましたが、責務分離のため独立プラグインに切り出しました。「draft 強制を **使いたくない**」運用を選ぶ場合はこのプラグインを **インストールしない** だけで済みます。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install enforce-draft-pr@natsuume-plugins
```

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

## 機能一覧

### Hooks

#### enforce-draft-pr

**ファイル**: `hooks/scripts/enforce-draft-pr.sh`
**イベント**: PreToolUse (matcher: `Bash`)

**動作**:

- `gh ... pr create` invocation をトークン化して検出 (global option / env prefix / `cd &&` チェーン / 行継続 / 複数行コマンドにも追随)
- 既に `--draft` / `--draft=true` / 短縮形 `-d` が付いていれば素通し
- draft 指定が無ければ `create` キーワード直後に `--draft` を挿入してコマンドを書き換える
- `--draft=false` のように **明示的に非 draft を指定** している場合は enforce 方針違反として **deny (ブロック)** し、理由メッセージを返す (指定位置・出現順・行継続や quote による綴りの分割に依らず判定する)
- 同一コマンドに複数の `gh pr create` があれば各々を独立に処理する (1 つでも `--draft=false` があれば deny)
- 先頭・中間・末尾の redirect target / IO number / redirection target としての process substitution は gh の argv から除外し、`>--draft` のようなファイル名を既存 flag と誤認しない
- 未対応の heredoc delimiter 構文を検出した場合は、発動位置に依らず解析不能として **deny** し書き直しを促す

**例**:

```
入力: gh pr create --title "新機能" --body-file body.md
出力: gh pr create --draft --title "新機能" --body-file body.md
```

## 既知の制約

- このプラグインの hook は **コマンドの書き換え** を行います。`cd repo && gh pr create` / 環境変数 prefix / `;` `&&` `||` `|` チェーン / `-R`・`--repo` global option / 行継続 (`\<改行>`) には追随しますが、シェルラッパー (`bash -c "..."`) / `$(...)` 等のコマンド置換 / バッククォート / subshell `(...)` / process substitution `<(...)`・`>(...)` の **内部** の `gh pr create` には介入しません (parser から隠蔽されるため)。process substitution 自体は 1 token として保持し、redirection target または gh option の値としては正しく除外します。
- 検出は **クォート対応** です。`--body "... gh pr create ..."` のように **PR 本文の中に `gh pr create` という文字列** が含まれていても、本文側には `--draft` を挿入しません (本物のコマンドにのみ挿入)。複数行 `--body "..."` の改行・本文・特殊文字 (区切り文字や `#` を含む) も byte 単位で保持します。挿入は `create` トークン直後への ` --draft` 差し込みのみです。
- **here-doc** (`<<EOF ... EOF`) の本文は data として保持し、本文中の `gh pr create` / 区切り文字 / `--draft=false` 文字列には介入しません。一方 `gh pr create --body-file - <<EOF ... EOF` のように here-doc を本文として渡す正当なケースは、先頭の `gh pr create` に `--draft` を付与しつつ本文を保持します。対応する delimiter 形式は `<<WORD` / `<< WORD` / `<<-WORD` / `<<'WORD'` / `<<"WORD"` / `<<\WORD` (WORD は英数字・アンダースコア・ハイフン) です。
- **未対応の heredoc delimiter 構文** (部分 quote 連結 `E"OF"`、上記以外の文字を含む unquoted word 等) を検出した場合、それ以降のコマンド文字列は解析しません (opaque fallback)。fallback は発動位置 (`gh pr create` の引数領域内か、別コマンドの領域か) に依らず常に deny します (fallback より前に完結した invocation への `--draft` 付与も行いません。opaque 領域には検出不能な invocation が隠れうるため、fail-closed で全体を deny します)。deny された場合は `<<EOF` / `<<'EOF'` のような単純な形に書き直してください。
- **literal な改行で区切られた 2 行目以降** (例: `echo x` の次の行に `gh pr create`) は検出しません (here-doc 本文を誤って書き換えないための仕様)。`;` / `&&` 区切り・単一行・別 Bash 呼び出しを使ってください。コメント行だけが先行する場合は、その後の最初の `gh pr create` に付与します。
- top-level コメント (`# ...`) は保持したまま、同一行 (末尾コメント) や先行コメント行の後の本物の `gh pr create` には `--draft` を付与します。引用符内の `#` (`--body "fix #123"`) はコメント扱いしません。
- 行継続 (`\<改行>`) は **トークン間** (`gh pr \<改行>create`) では正しく処理しますが、**キーワードの途中** (`cre\<改行>ate` 等) に挟む難読化は粗フィルタを抜けて検出しません (sibling の git-guardrails と同じ coarse-filter 方針。意図的難読化による bypass は cooperative 利用前提で対象外)。
- `gh pr create` の代わりに `gh api` で直接 PR を作成するケースでは介入できません。
- 算術式 `$((x << 1))` / `((x << 1))` 内のビットシフト `<<` は heredoc と誤認しません (深度追跡で式全体をスキップ)。
- 一度作成された draft PR を ready 化するのはユーザーまたは Claude が `gh pr ready <PR>` を明示実行する必要があります (本プラグインは作成時のみ介入)。

## 関連プラグイン

- [git-guardrails](../git-guardrails/) — デフォルトブランチへの直接 push を禁止する hook + rebase ワークフロー Skill

## ディレクトリ構成

```
enforce-draft-pr/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── enforce-draft-pr.sh
│       └── lib/
│           └── cmd-parser.sh   # git-guardrails / pre-push-review と共有 (CI で byte-identical を強制)
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`

## 関連情報

- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
- [GitHub CLI - gh pr create](https://cli.github.com/manual/gh_pr_create)

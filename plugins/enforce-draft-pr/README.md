# enforce-draft-pr プラグイン

`gh pr create` に `--draft` フラグを自動付与する PreToolUse フックプラグインです。「PR は必ず draft で起こし、レビュー後に手動で ready 化する」運用を強制したい場合に使います。

## バージョン

v0.2.1

### v0.2.0 → v0.2.1 の変更点

env-skip ループが現コマンド境界を越境して次コマンドの `pr create` まで届き、 同一オフセットに ` --draft` を **2 度** 挿入する parser bug を修正しました。 `FOO=bar; gh pr create ...` のような boundary 含む連結形で発火します (`gh` は重複 `--draft` を寛容に扱うため機能破壊は無いですが parser bug としては明確)。 修正は env-skip ループに「`$k > $t` で `TSTART=1` または `TNL=1` のトークンに到達したら break」 する境界ガードを追加することで、 重複 INS push を構造的に閉じます。 単一コマンド内 env-prefix 動線 (`FOO=bar gh pr create`) の正常系 1 回付与は破壊されません (同一コマンド継続トークンは `TSTART=0 && TNL=0` のためガードが発火せず、 NAME=VALUE skip → `gh` → `pr create` の正規処理が走ります)。

### v0.1.0 → v0.2.0 の変更点

検出ロジックを生コマンド文字列への素朴な `grep`/`sed` から、コマンド全体を **1 回のクォート対応スキャン** でトークン化する方式に刷新しました (行継続正規化と unquote は git-guardrails / pre-push-review と共有する `cmd-parser.sh` を利用)。これにより以下を解消:

- `--title "my --draft feature"` のように **引数値内** の `--draft` を既存フラグと誤検知して draft 付与を skip する問題 (false negative)
- 検出 (`\s+`) と書き換え (固定スペース) の不整合で、連続空白 / タブ区切りだと検出は成功するのに `--draft` が付かない問題
- `gh -R owner/repo pr create` (global option) / `cd repo && gh pr create` / `GH_TOKEN=x gh pr create` (env prefix) / `;` `&&` チェーン / 行継続 `\<改行>` の取りこぼし
- `--draft` / `--draft=true` / 短縮形 `-d` を既存 draft 指定として認識し重複付与しない。`--draft=false` (cobra falsy: false/False/FALSE/0/f/F) のように **明示的に非 draft を指定** した PR 作成は enforce 方針違反として **deny (ブロック)** する

`--draft` は `create` トークン直後 (= 引数 / body より前) に挿入し、後続の引数・複数行 `--body` の中身・空白・改行は 1 バイトも変更しません。`gh extension exec ... pr create` のように引数中に "pr create" を含む別 subcommand や、`--body "...gh pr create..."` のように本文に "gh pr create" を含むケースは誤検知しません。

## 概要

`PreToolUse` (matcher: `Bash`) で `gh pr create` の呼び出しを検知し、`--draft` が付いていなければコマンドを書き換えてフラグを追加します。`updatedInput.command` を返すため、Claude Code は書き換え後のコマンドで実行します。

git-guardrails プラグインの一部として提供されていましたが、責務分離のため独立プラグインに切り出しました。「draft 強制を **使いたくない**」運用を選ぶ場合はこのプラグインを **インストールしない** だけで済みます。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install enforce-draft-pr@natsuume-plugins
```

## 機能一覧

### Hooks

#### enforce-draft-pr

**ファイル**: `hooks/scripts/enforce-draft-pr.sh`
**イベント**: PreToolUse (matcher: `Bash`)

**動作**:

- `gh ... pr create` invocation をトークン化して検出 (global option / env prefix / `cd &&` チェーン / 行継続 / 複数行コマンドにも追随)
- 既に `--draft` / `--draft=true` / 短縮形 `-d` が付いていれば素通し
- draft 指定が無ければ `create` キーワード直後に `--draft` を挿入してコマンドを書き換える
- `--draft=false` のように **明示的に非 draft を指定** している場合は enforce 方針違反として **deny (ブロック)** し、理由メッセージを返す
- 同一コマンドに複数の `gh pr create` があれば各々を独立に処理する (1 つでも `--draft=false` があれば deny)

**例**:

```
入力: gh pr create --title "新機能" --body-file body.md
出力: gh pr create --draft --title "新機能" --body-file body.md
```

## 既知の制約

- このプラグインの hook は **コマンドの書き換え** を行います。`cd repo && gh pr create` / 環境変数 prefix / `;` `&&` `||` `|` チェーン / `-R`・`--repo` global option / 行継続 (`\<改行>`) には追随しますが、シェルラッパー (`bash -c "..."`) / `$(...)` 等のコマンド置換 / バッククォート / subshell `(...)` の **内部** の `gh pr create` には介入しません (parser から隠蔽されるため)。
- 検出は **クォート対応** です。`--body "... gh pr create ..."` のように **PR 本文の中に `gh pr create` という文字列** が含まれていても、本文側には `--draft` を挿入しません (本物のコマンドにのみ挿入)。複数行 `--body "..."` の改行・本文・特殊文字 (区切り文字や `#` を含む) も byte 単位で保持します。挿入は `create` トークン直後への ` --draft` 差し込みのみです。
- **here-doc** (`<<EOF ... EOF`) の本文は data として保持し、本文中の `gh pr create` には介入しません。一方 `gh pr create --body-file - <<EOF ... EOF` のように here-doc を本文として渡す正当なケースは、先頭の `gh pr create` に `--draft` を付与しつつ本文を保持します。
- **literal な改行で区切られた 2 行目以降** (例: `echo x` の次の行に `gh pr create`) は検出しません (here-doc 本文を誤って書き換えないための仕様)。`;` / `&&` 区切り・単一行・別 Bash 呼び出しを使ってください。コメント行だけが先行する場合は、その後の最初の `gh pr create` に付与します。
- top-level コメント (`# ...`) は保持したまま、同一行 (末尾コメント) や先行コメント行の後の本物の `gh pr create` には `--draft` を付与します。引用符内の `#` (`--body "fix #123"`) はコメント扱いしません。
- **コマンド名の前に置くリダイレクト** (`>out gh pr create` / `2>/tmp/log gh pr create` 等) は検出しません。通常の末尾リダイレクト (`gh pr create ... > out`) は問題なく付与します。先頭リダイレクトは agent 生成コマンドでは稀なため cooperative 利用前提の既知の制約とします。
- 行継続 (`\<改行>`) は **トークン間** (`gh pr \<改行>create`) では正しく処理しますが、**キーワードの途中** (`cre\<改行>ate` 等) に挟む難読化は粗フィルタを抜けて検出しません (sibling の git-guardrails と同じ coarse-filter 方針。意図的難読化による bypass は cooperative 利用前提で対象外)。
- `gh pr create` の代わりに `gh api` で直接 PR を作成するケースでは介入できません。
- 単一引用符 `'...'` の中に literal な `\<改行>` を含む稀なコマンドでは、行継続除去でその backslash-改行が消えます (cooperative 利用前提の既知の制約。通常の複数行 `--body "..."` は二重引用符でも改行が保持されます)。
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

# decompose-bash プラグイン

`SessionStart` で Bash コマンドを最小粒度に分解して独立した Bash ツール呼び出しとして実行するよう Claude に指示する `additionalContext` を注入するプラグインです。`git add ... && git commit ... && git push` のようなコマンド合成によって `PreToolUse` hook の検知が取りこぼされる事故を防ぐことを目的とします。

## バージョン

v0.1.0

## 概要

Claude Code の `PreToolUse` hook は Bash ツールの `command` 文字列に対するパターンマッチで判定されます。このため `A && B && C` のような合成コマンドは **先頭以外の部分が hook 検知を取りこぼす** ことがあり、リポジトリのガードレールを意図せず迂回してしまう恐れがあります。

例えば次の合成は、`git push` を deny したい hook が先頭の `git add` のパターンしか見ずに通過させてしまう可能性があります:

```bash
git add foo.txt && git commit -m "msg" && git push
```

本プラグインは、Claude がこの種のコマンド合成を避け、各コマンドを独立した Bash 呼び出しとして実行するよう、セッション開始時に方針 (`additionalContext`) を注入します。

## 関連プラグインとの位置づけ

本プラグインは「**hook の信頼性を補強する補完プラグイン**」として位置づけられます。以下のプラグインの hook が正しく機能するためには、Bash コマンドが個別に PreToolUse を通る必要があります:

- [git-guardrails](../git-guardrails/) — `master` への直接 commit / push / PR を deny する PreToolUse hook
- [pre-push-review](../pre-push-review/) — `git push` 前にレビューループを強制する PreToolUse hook
- [auto-lint-check](../auto-lint-check/) — Edit/Write 前の linter チェックを行う PreToolUse hook

これらをインストールしている環境では、本プラグインも併用することで hook の取りこぼしリスクを下げられます。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=decompose-bash
```

## 機能一覧

### Hooks

#### inject-decompose-context

**ファイル**: `hooks/scripts/inject-decompose-context.sh`
**イベント**: `SessionStart`

**動作**:

- セッション開始時に 1 回だけ発火し、Bash コマンドの分解方針を `additionalContext` として注入する
- 入力 JSON から `hook_event_name` を読み取り、`hookSpecificOutput.hookEventName` に同じ値を設定する (誤った既定値で別 event の文脈に誘導しないため)
- `jq` が無い環境や `hook_event_name` が取得できない場合は無音で `exit 0` (フェイルセーフ)

**注入内容の要約**:

1. **分解すべきパターン**:
   - 異なる目的のコマンドを `&&` / `||` / `;` で連結しない
   - コマンド置換 `$(...)` / バッククォートで別コマンドを埋め込まない
   - `xargs <cmd>` / `find -exec <cmd> {}` も hook 検知の観点では合成と同等として扱う
   - パイプライン `|` は単一論理操作の場合のみ許容
2. **例外** (連結を許容するケース):
   - `cwd` 制約のための `cd $dir && do_something`
   - トランザクション的合成 (例: `make build && make test`)
3. **なぜ分解が必要か** を明示し、Claude が境界判断できるようにする

## 設計上の選択

### なぜ SessionStart か (vs UserPromptSubmit)

- **`SessionStart`** はセッション開始時に 1 回だけ発火する。コンテキストトークンの消費を最小化できる
- **`UserPromptSubmit`** は毎ターン発火する。長時間セッションで context が圧縮 (compact) されても方針を再注入し続けられる利点があるが、トークンコストは増える

本プラグインは静的な規律 (時間で変わらない汎用ルール) のため `SessionStart` を採用しています。長時間セッションで方針が薄れていると感じた場合は、`hooks/hooks.json` の `SessionStart` を `UserPromptSubmit` に書き換えるか両方に追加することで強化できます。

### なぜ「禁止」ではなく「分解推奨 + 例外」か

完全禁止にすると `git log --oneline | head -20` のような明らかに無害な使い方や、`cd $dir && cmd` のような必然性のある合成まで阻害して非効率になります。Claude が境界を自己判断できるよう、規律 (rule) と理由 (why) と例外 (exception) を併記する形にしてあります。

### 強制ではなく誘導

`additionalContext` を Claude に渡すだけなので、Claude が指示を無視することは原理的に可能です。確実に止めたいケースは別途 PreToolUse の deny hook を組む必要があります (このプラグインは「Claude が自発的に hook-friendly な形で呼ぶ」確率を上げる **誘導** プラグインであり、強制ではありません)。

## ディレクトリ構成

```
decompose-bash/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       └── inject-decompose-context.sh
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
- [auto-followthrough](../auto-followthrough/) — auto mode 専用の方針注入プラグイン。本プラグインと同型 (additionalContext 注入) の設計を踏襲している

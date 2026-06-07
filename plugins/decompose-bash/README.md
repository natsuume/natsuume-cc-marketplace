# decompose-bash プラグイン

`SessionStart` で Bash コマンドを最小粒度に分解して独立した Bash ツール呼び出しとして実行するよう Claude に指示する `additionalContext` を注入するプラグインです。`git add ... && git commit ... && git push` のようなコマンド合成によって `PreToolUse` hook の検知が取りこぼされる事故を防ぐことを目的とします。

## バージョン

v0.1.1

### v0.1.0 → v0.1.1 の変更点

- **不正 / 空 JSON 入力時の jq parse error stderr 漏れを抑制**: SessionStart hook の input 解析で JSON parse 失敗 (空入力 / malformed) が stderr に漏出していた問題を、 jq 呼び出しに `2>/dev/null` を付与して silent skip するよう修正。 fail-open 設計 (= 解析不能なら additionalContext 注入を skip) の挙動は維持。

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
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install decompose-bash@natsuume-plugins
```

## 機能一覧

### Hooks

#### inject-decompose-context

**ファイル**: `hooks/scripts/inject-decompose-context.sh`
**イベント**: `SessionStart`

**動作**:

- `SessionStart` イベント発火時に Bash コマンドの分解方針を `additionalContext` として注入する (`SessionStart` は `startup` 以外に `resume` / `clear` / `compact` などの source でも発火するため、同一セッション内で複数回呼ばれる可能性がある。注入内容は静的なので重複しても害は無いが、毎回コンテキストトークンを再消費する点に留意)
- 入力 JSON から `hook_event_name` を読み取り、`hookSpecificOutput.hookEventName` に同じ値を設定する (誤った既定値で別 event の文脈に誘導しないため)
- `jq` が無い環境や `hook_event_name` が取得できない場合は無音で `exit 0` (フェイルセーフ)

**注入内容の要約**:

1. **分解すべきパターン**:
   - 異なる目的のコマンドを `&&` / `||` / `;` / `&` で連結しない (サブシェル `(...)` / ブレースグループ `{...;}` も同等)
   - コマンド置換 `$(...)` / バッククォートで別コマンドを埋め込まない
   - ラッパー経由 (`eval` / `bash -c` / `sh -c` / `sudo sh -c`) でコマンドを隠さない
   - `xargs <cmd>` / `find -exec <cmd> {}` も hook 検知の観点では合成と同等として扱う
   - パイプライン `|` は単一論理操作の場合のみ許容
2. **例外** (連結を許容するケース):
   - `cwd` 制約のための `cd $dir && do_something`
   - トランザクション的合成 (例: `make build && make test`)
3. **なぜ分解が必要か** を明示し、Claude が境界判断できるようにする

## 設計上の選択

### なぜ SessionStart か (vs UserPromptSubmit)

- **`SessionStart`** はセッション開始系イベント (`startup` / `resume` / `clear` / `compact` 等) で発火する。発火頻度はセッション内で限定的なため、コンテキストトークン消費を抑えられる
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

- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
- [auto-followthrough](../auto-followthrough/) — auto mode 専用の方針注入プラグイン。本プラグインと同型 (additionalContext 注入) の設計を踏襲している

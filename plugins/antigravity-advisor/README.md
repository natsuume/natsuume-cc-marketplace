# antigravity-advisor

Antigravity CLI (`agy`) を read-only の助言役 (advisor) として相談するプラグインです。`/antigravity-advisor:consult` skill が self-contained な相談 request を組み立て、`antigravity-advisor-runner` subagent が wrapper script を通じて agy を plan mode で 1 回起動し、助言を親セッションへ返します。

hook は持ちません。SessionStart での規律の常時注入も行わず、相談は skill を呼び出したときだけ行います。

## バージョン

v0.1.0

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install antigravity-advisor@natsuume-plugins
```

## 前提

- Antigravity CLI (`agy`) が PATH 上にあり、ログイン済みであること
- jq
- git (cwd が git の work tree 内のとき、working tree の変化の検知に使う)
- Linux (WSL2 含む) / macOS

## 動作

### consult skill (`/antigravity-advisor:consult`)

1. 相談 request を self-contained に組み立てる。目的、制約・受入基準、現在の方針、集めた証拠、具体的な問い、期待する回答形式の 6 項目を含め、リポジトリ内のファイルは絶対パスで示す
2. Agent tool で `subagent_type: "antigravity-advisor:antigravity-advisor-runner"`、`model: "sonnet"` を指定して runner を起動する。起動 mode は指定しない。runner の report は completion notification で届き、受け取るまで相談を完了扱いにしない
3. 助言はフラットに扱い、採否と理由を作業報告に明示する。コード差分の finding を得る用途には使わない。設計・仕様の決定はユーザーの専権事項で、助言は判断材料に留める
4. runner が `Status: execution-failed` を返した場合は、相談なしで作業を続けてよく、相談できなかった理由を作業報告に含める

### antigravity-advisor-runner

tools は `Bash, Write, Read`、model は `sonnet` に固定しています。

1. Bash で `mktemp -d` を 1 回実行して一時ディレクトリを得る
2. Write tool で、親から受け取った request を一字一句そのまま `<dir>/request.md` に書く
3. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-antigravity-advisor.sh" "<dir>/request.md"` を foreground の Bash 1 回 (`timeout` 1800000) で起動する。`${CLAUDE_PLUGIN_ROOT}` が空、または展開した path に wrapper が無い場合は、plugin cache 内の候補 path を別の Bash で列挙し、最新版を選んで literal な絶対 path で起動する
4. wrapper の結果を report にして返す。`SubagentHandback` tool がある場合はその `message` として 1 回だけ渡す

wrapper は 1 回だけ実行し、失敗しても再実行しません。runner はファイルの編集と Agent の起動を行いません。

成功時の report:

```
# Antigravity Advice

Status: success

<wrapper の stdout に出た助言の本文>
```

失敗時の report (wrapper の raw stdout / stderr は含めない):

```
# Antigravity Advice

Status: execution-failed
Exit status: <数値 or unknown>
Failure class: agy-unavailable | agy-failed | incomplete-response | workspace-modified | invalid-request | other
Recovery direction: <概念的な次の一手>
```

### wrapper (`scripts/run-antigravity-advisor.sh`)

引数に request ファイルを 1 つ取ります。wrapper 専用の一時ディレクトリを `${TMPDIR:-/tmp}` の直下に作って request をコピーし、cwd を変えずに次のコマンドで agy を起動します。一時ディレクトリは終了時に削除します。

```
agy --mode plan --output-format json [--model <model>] --add-dir <一時ディレクトリ> --print-timeout 25m -p '<prompt>'
```

prompt は英語の固定文で、コピーした request の絶対パスを含みます (request の本文は含みません)。agy に対して、read-only の advisor として振る舞うこと、shell コマンドを一切実行せずファイルの作成・編集もしないこと、読み取りのツールだけを使うこと、request を読んで問いに答えること、必要ならカレントディレクトリのファイルを読んで裏取りすること、request と同じ言語で答えること、計画の承認待ちや実装計画の artifact を作らないことを指示します。

agy の stdout (JSON 1 つ) を検査し、成功時は `.response` の本文だけを stdout に出します。進捗と失敗の理由は stderr に日本語で出します。

## wrapper の終了コード

| exit | runner の Failure class | 条件 |
|---|---|---|
| 0 | (成功) | 下のいずれにも該当しない。stdout に `.response` の本文だけを出す |
| 2 | invalid-request | 引数がちょうど 1 つでない、引数のパスが存在しない・通常ファイルでない・symlink である・空である、または `ANTIGRAVITY_ADVISOR_MODEL` が不正 |
| 3 | agy-unavailable | agy または jq が PATH に無い (どちらが無いかは stderr で区別する) |
| 4 | agy-failed | agy が非 0 で終了した |
| 5 | workspace-modified | cwd が git の work tree 内で、agy の実行前後に HEAD または working tree の状態が変わった |
| 6 | incomplete-response | agy の stdout が JSON でない、`.status` が `SUCCESS` でない、`.denied_actions` が空でない、または `.response` が空 |

上の表にない wrapper 内部の失敗 (一時ディレクトリの作成や request のコピーの失敗等) は exit 1 で、runner は Failure class を `other` とします。0 以外で終了するときは stdout に何も出しません。

## `ANTIGRAVITY_ADVISOR_MODEL`

| 値 | 動作 |
|---|---|
| 未設定または空 | agy に `--model` を渡さず、agy の既定の model を使う |
| `^[A-Za-z0-9._-]+$` に一致する | `--model <値>` として agy に渡す。agy が受け付けない model 名なら agy が exit 1 で終了し、wrapper は exit 4 になる |
| 上記以外 | agy を起動せず exit 2 |

wrapper は runner の Bash の環境変数から値を読むため、Claude Code を起動するシェルか `settings.json` の `env` で設定します。

## read-only の保証の範囲

agy は plan mode で起動されます。plan mode では shell コマンドの実行が拒否されます。拒否が起きると agy の応答は途中で終わり、JSON に `denied_actions` が付くため、wrapper は exit 6 にします。

**ファイルの書き込みの禁止は運用上の制約です。** plan mode でもファイルの読み取りと書き込みのツールは使えます。書き込みは wrapper の prompt で禁じるだけで、ツールのレベルでは止まりません。そのため cwd が git の work tree 内なら、wrapper は agy の実行前後に `git rev-parse HEAD` と `git status --porcelain=v1 -z --untracked-files=all` の出力を比較し、変化していれば exit 5 で失敗にします。

この検知は事後のもので、変更を防いだり元に戻したりはしません。また、次の変更は検知しません。

- 実行前から変更済みだったファイルへの追加の変更 (`git status` の状態が変わらないため)
- `.gitignore` で無視されるファイルへの書き込み
- リポジトリの外への書き込み
- cwd が git の work tree の外のときのすべての変更 (比較を行わない)

変更の主体は区別しません。相談中に親セッションやユーザーが同じ work tree を変更した場合も exit 5 になります。

## 既知の制約

- runner が `mktemp -d` で作る一時ディレクトリと `request.md` は削除されず、OS の一時領域に残る
- agy の応答は `--print-timeout 25m` で 25 分に制限される。runner の Bash の timeout は 30 分
- Bash tool が wrapper の実行を background へ移した場合、runner は結果を回収せず `Status: execution-failed` を返す。このとき agy はまだ実行中の可能性がある
- wrapper は agy の JSON 出力の形式 (`status` / `response` / `denied_actions`) と、`-p` を最後に置く引数の制約を前提にする。この前提は agy 1.5.18 (2026-11-27、WSL2) で確認した。macOS での実 agy の動作は未確認

## ファイル構成

| ファイル | 役割 |
|---|---|
| `.claude-plugin/plugin.json` | plugin の manifest |
| `skills/consult/SKILL.md` | 相談 request の組み立て、runner の起動、助言の扱い、失敗時の対応 |
| `agents/antigravity-advisor-runner.md` | request を一時ファイルに書いて wrapper を 1 回起動し、report を返す runner |
| `scripts/run-antigravity-advisor.sh` | agy を plan mode で起動し、応答と working tree の変化を検査して助言の本文を返す wrapper |

## キーワード

`antigravity` `agy` `advisor` `second-opinion` `skill` `gemini`

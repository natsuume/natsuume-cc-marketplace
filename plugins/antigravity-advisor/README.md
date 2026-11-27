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

1. Write tool で、親から受け取った request を一字一句そのまま session scratchpad の一意なファイルに書く。project 内へは書かず、本文を Bash の command に載せない
2. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-antigravity-advisor.sh" "<request file の絶対 path>"` を foreground の Bash 1 回 (`timeout` 1800000) で起動する。`${CLAUDE_PLUGIN_ROOT}` が空、または展開した path に wrapper が無い場合は、別の Bash で plugin cache を `find` で検索して version ディレクトリの semver が最も新しい wrapper の絶対 path を表示させ、その path を literal に書いた次の Bash で起動する
3. wrapper の結果を report にして返す。`SubagentHandback` tool がある場合はその `message` として 1 回だけ渡す

wrapper は 1 回だけ実行し、失敗しても再実行しません。runner は request のファイル以外への書き込みと Agent の起動を行いません。

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

引数に request ファイルを 1 つ取ります。wrapper 専用の一時ディレクトリを `${TMPDIR:-/tmp}` の直下に 2 つ作ります。1 つは request のコピーだけを置いて agy の `--add-dir` に渡すもの、もう 1 つは agy の stdout と working tree の記録を置く、agy に渡さないものです。cwd を変えずに次のコマンドで agy を起動し、一時ディレクトリはどちらも終了時に削除します。

```
agy --mode plan --output-format json [--model <model>] \
  --settings '{"permissions":{"deny":["write_file(*)","command(*)","mcp(*)","read_url(*)"]}}' \
  --add-dir <request の一時ディレクトリ> --print-timeout 25m -p '<prompt>'
```

`--settings` の値は固定で、このセッションに限ってファイルの書き込み・shell の実行・MCP・URL の取得を権限設定で拒否し、ローカルのファイルの読み取りだけを許します。

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
| 6 | incomplete-response | agy の stdout が JSON 1 つでない、`.status` が `SUCCESS` でない、`.denied_actions` が空でない、または `.response` が空 |

agy の実行後に複数の条件が同時に成り立つ場合は、5 (workspace-modified) > 4 (agy-failed) > 6 (incomplete-response) の順に採用します。working tree の比較は agy の終了コードに関わらず必ず行います。

上の表にない wrapper 内部の失敗 (一時ディレクトリの作成、request のコピー、実行前の working tree の記録の失敗等) は exit 1 で、runner は Failure class を `other` とします。0 以外で終了するときは stdout に何も出しません。

## `ANTIGRAVITY_ADVISOR_MODEL`

| 値 | 動作 |
|---|---|
| 未設定または空 | agy に `--model` を渡さず、agy の既定の model を使う |
| `^[A-Za-z0-9._-]+$` に一致する | `--model <値>` として agy に渡す。agy が受け付けない model 名なら agy が exit 1 で終了し、wrapper は exit 4 になる |
| 上記以外 | agy を起動せず exit 2 |

wrapper は runner の Bash の環境変数から値を読むため、Claude Code を起動するシェルか `settings.json` の `env` で設定します。

## read-only の保証の範囲

read-only は、権限設定による拒否 (主) と、working tree の事後の検知 (補) の 2 段で守ります。prompt でも shell を使わないことと、ファイルを作成・編集しないことを指示します。

### 権限設定による拒否 (主)

plan mode でもファイルの書き込みのツールは使えるため、wrapper は agy の起動に `--settings '{"permissions":{"deny":["write_file(*)","command(*)","mcp(*)","read_url(*)"]}}'` を必ず付けます。このセッションに限り、次の操作が権限のレベルで拒否されます。

| deny の規則 | 拒否する操作 |
|---|---|
| `write_file(*)` | ファイルの作成と編集 |
| `command(*)` | shell コマンドの実行 |
| `mcp(*)` | MCP の tool の呼び出し |
| `read_url(*)` | URL の取得 |

ローカルのファイルの読み取りは拒否しないため、agy はリポジトリと request を読んで裏取りできます。shell の実行の試みが拒否されて応答が途中で終わると、JSON に `denied_actions` が付き、wrapper は exit 6 にします。

### working tree の事後の検知 (補)

権限設定が効かない場合に備えて、cwd が git の work tree 内なら、wrapper は agy の実行前後に `git rev-parse --verify -q HEAD` と `git status --porcelain=v1 -z --untracked-files=all` の出力を比較します。変化していれば、agy の終了コードに関わらず exit 5 で失敗にします。git のコマンドには `GIT_OPTIONAL_LOCKS=0` を付け、wrapper 自身が index を書き換えないようにします。commit の無い repo では HEAD の出力が空のまま比較します。比較に使う記録は agy に渡さない一時ディレクトリに置きます。

この検知は事後のもので、変更を防いだり元に戻したりはしません。また、次の変更は検知しません。

- 実行前から変更済みだったファイルへの追加の変更 (`git status` の状態が変わらないため)
- `.gitignore` で無視されるファイルへの書き込み
- リポジトリの外への書き込み
- cwd が git の work tree の外のときのすべての変更 (比較を行わない)

変更の主体は区別しません。相談中に親セッションやユーザーが同じ work tree を変更した場合も exit 5 になります。

## 既知の制約

- 権限設定による拒否は、2026-11-27 に agy 1.5.18 で `write_file(*)` と `command(*)` の 2 規則の設定を使って確認した。この設定で、`write_file` によるファイルの作成と `edit_file` による編集はどちらも user-configured deny rule として拒否され、`read_file` による読み取りは成功し、ファイルは変わらなかった。括弧の無い `"write_file"` は反映されなかった
- 4 規則をまとめた設定は、同じ日に agy 1.5.18 で `-p "/permissions"` を実行し、4 規則とも global scope の deny として表示されることを確認した。この設定を付けた wrapper の相談も成功した。`mcp(*)` と `read_url(*)` が MCP の tool と URL の取得を実際に拒否するかは未確認 (書式は agy のバイナリ内の例による)。agy の更新で設定が受け付けられなくなった場合は、すべての相談が exit 4 になる。設定全体が無視されるようになった場合は、権限による拒否が効かず、working tree の事後の検知だけが残る
- 権限の種別を確認できていない tool (ブラウザー系の tool など) は、deny の規則の対象外になりうる
- 権限設定による拒否が `denied_actions` に記録されるかは未確認
- agy の応答は `--print-timeout 25m` で 25 分に制限される。runner の Bash の timeout は 30 分
- Bash tool が wrapper の実行を background へ移した場合、runner は結果を回収せず `Status: execution-failed` を返す。このとき agy はまだ実行中の可能性がある
- wrapper は agy の JSON 出力の形式 (`status` / `response` / `denied_actions`) と、`-p` を最後に置く引数の制約を前提にする。この前提は agy 1.5.18 (2026-11-27、WSL2) で確認した。macOS での実 agy の動作は未確認

## ファイル構成

| ファイル | 役割 |
|---|---|
| `.claude-plugin/plugin.json` | plugin の manifest |
| `skills/consult/SKILL.md` | 相談 request の組み立て、runner の起動、助言の扱い、失敗時の対応 |
| `agents/antigravity-advisor-runner.md` | request を session scratchpad のファイルに書いて wrapper を 1 回起動し、report を返す runner |
| `scripts/run-antigravity-advisor.sh` | agy を plan mode と権限設定で起動し、応答と working tree の変化を検査して助言の本文を返す wrapper |

## キーワード

`antigravity` `agy` `advisor` `second-opinion` `skill` `gemini`

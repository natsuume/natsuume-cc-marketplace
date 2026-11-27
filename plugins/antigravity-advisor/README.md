# antigravity-advisor

Antigravity CLI (`agy`) を read-only の助言役 (advisor) として相談するプラグインです。`/antigravity-advisor:consult` skill が self-contained な相談 request を組み立て、`antigravity-advisor-runner` subagent が wrapper script を通じて agy を 1 回起動し、助言を親セッションへ返します。

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
- git 2.31 以降 (一時ディレクトリの場所の検査と、cwd が git の work tree 内のときの変化の検知に使う)
- Linux (WSL2 含む) / macOS

## 動作

### consult skill (`/antigravity-advisor:consult`)

1. 相談 request を self-contained に組み立てる。目的、制約・受入基準、現在の方針、集めた証拠、具体的な問い、期待する回答形式の 6 項目を含め、リポジトリ内のファイルは絶対パスで示す。agy はディレクトリの一覧や検索のツールを使えない可能性があるため、読ませたいファイルは絶対パスで列挙する
2. Agent tool で `subagent_type: "antigravity-advisor:antigravity-advisor-runner"`、`model: "sonnet"` を指定して runner を起動する。起動 mode は指定しない。runner の report は completion notification で届き、受け取るまで相談を完了扱いにしない
3. 助言はフラットに扱い、採否と理由を作業報告に明示する。コード差分の finding を得る用途には使わない。設計・仕様の決定はユーザーの専権事項で、助言は判断材料に留める
4. runner が `Status: execution-failed` を返した場合は、相談なしで作業を続けてよく、相談できなかった理由を作業報告に含める

### antigravity-advisor-runner

tools は `Bash, Write, Read`、model は `sonnet` に固定しています。

1. Write tool で、親から受け取った request を一字一句そのまま session scratchpad の一意なファイルに書く。project 内へは書かず、本文を Bash の command に載せない
2. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-antigravity-advisor.sh" "<request file の絶対 path>"` を foreground の Bash 1 回 (`timeout` 1800000) で、cwd を変えずに起動する。`${CLAUDE_PLUGIN_ROOT}` が空、または展開した path に wrapper が無い場合は、別の Bash で plugin cache を `find` で検索して version ディレクトリの semver が最も新しい wrapper の絶対 path を表示させ、その path を literal に書いた次の Bash で起動する
3. wrapper の結果を report にして返す。`SubagentHandback` tool がある場合はその `message` として 1 回だけ渡す

wrapper は 1 回だけ実行し、失敗しても再実行しません。runner は request のファイル以外への書き込みと Agent の起動を行いません。

成功時の report (wrapper は成功時に stderr へ何も出さないため、wrapper の出力全体が助言の本文になる):

```
# Antigravity Advice

Status: success

<wrapper の出力全体>
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

引数に request ファイルを 1 つ取り、起動したときの cwd を相談対象のリポジトリとみなします。cwd が git の work tree 内ならその toplevel、外ならその cwd をリポジトリのルートとします。

wrapper 専用の一時ディレクトリを `${TMPDIR:-/tmp}` の直下に 2 つ作ります。1 つは agy の cwd にする空のディレクトリで、request のコピーだけを置きます。もう 1 つは agy の stdout / stderr と working tree の記録を置く、agy に渡さないものです。作ったディレクトリが git の work tree の中にある場合は `/tmp` の直下に作り直し、それでも中なら exit 1 にします。一時ディレクトリはどちらも終了時に削除します。

agy の cwd にするディレクトリへサブシェルの中で移って (wrapper 自身の cwd は変えません)、次のコマンドで agy を起動します。`--add-dir` は使いません。

```
agy --mode plan --output-format json [--model <model>] \
  --settings '{"allowNonWorkspaceAccess":true,"permissions":{"deny":["write_file(*)","command(*)","mcp(*)","read_url(*)","execute_url(*)","download_url(*)"]}}' \
  --print-timeout 25m -p '<prompt>'
```

prompt は英語の固定文で、request のコピーの絶対パスと、リポジトリのルートの絶対パスを含みます (request の本文は含みません)。agy に対して、次を指示します。

- read-only の advisor として振る舞う
- shell コマンドを一切実行せず、ファイルの作成・編集・削除もしない。ローカルのファイルを読むツールだけを使う
- request を読んで問いに答える
- リポジトリのファイルは絶対パスで読み (一覧・検索のツールは使えない可能性がある)、必要なら読んで裏取りする
- request と同じ言語で答える
- 計画の承認待ちや実装計画の artifact を作らない

agy の stdout (JSON 1 つ) を検査し、成功時は `.response` の本文だけを stdout に出します。成功の経路では stderr に何も出しません。失敗のときだけ、理由を stderr に日本語で出します。agy の stderr は一時ファイルに書き、そのまま流さずに、agy が失敗したときに終了コードと先頭の 1 行 (200 文字まで) だけを要約として出します。

## wrapper の終了コード

| exit | runner の Failure class | 条件 |
|---|---|---|
| 0 | (成功) | 下のいずれにも該当しない。stdout に `.response` の本文だけを出す |
| 2 | invalid-request | 引数がちょうど 1 つでない、引数のパスが存在しない・通常ファイルでない・symlink である・空である、または `ANTIGRAVITY_ADVISOR_MODEL` が不正 |
| 3 | agy-unavailable | agy または jq が PATH に無い (どちらが無いかは stderr で区別する) |
| 4 | agy-failed | agy が非 0 で終了した |
| 5 | workspace-modified | cwd が git の work tree 内で、agy の実行前後に `.git` の設定・hook・info、HEAD・ref、status、working tree の内容のいずれかが変わった |
| 6 | incomplete-response | agy の stdout が JSON 1 つでない、`.status` が `SUCCESS` でない、`.denied_actions` が空でない、または `.response` が空 |

agy の実行後に複数の条件が同時に成り立つ場合は、5 (workspace-modified) > 4 (agy-failed) > 6 (incomplete-response) の順に採用します。working tree の比較は agy の終了コードに関わらず必ず行います。

上の表にない wrapper 内部の失敗 (一時ディレクトリの作成、git の work tree の外に一時ディレクトリを作れない、request のコピー、実行前の記録の失敗等) は exit 1 で、runner は Failure class を `other` とします。0 以外で終了するときは stdout に何も出しません。

## `ANTIGRAVITY_ADVISOR_MODEL`

| 値 | 動作 |
|---|---|
| 未設定または空 | agy に `--model` を渡さず、agy の既定の model を使う |
| `^[A-Za-z0-9._-]+$` に一致する | `--model <値>` として agy に渡す。agy が受け付けない model 名なら agy が exit 1 で終了し、wrapper は exit 4 になる |
| 上記以外 | agy を起動せず exit 2 |

wrapper は runner の Bash の環境変数から値を読むため、Claude Code を起動するシェルか `settings.json` の `env` で設定します。

## read-only の保証の範囲

read-only は、agy を空のディレクトリで起動することと権限設定による拒否 (主)、working tree の事後の検知 (補) の 2 段で守ります。prompt でも shell を使わないことと、ファイルを作成・編集しないことを指示します。`--mode plan` は prompt に計画の指示を足すだけで、権限の境界ではありません。

以下の agy の挙動は 2026-11-27 に agy 1.5.18 (WSL2) で確認したものです。

### 空のディレクトリでの起動と権限設定による拒否 (主)

agy をリポジトリの中で起動すると (cwd がリポジトリ、または `--add-dir` でリポジトリを加える)、そのリポジトリの `.agents/hooks.json` (別名の `.agent/`・`_agents/` を含む) と `.agents/plugins/` 配下の plugin の hook が agent の turn の中で shell で実行され、deny の規則では止まりません。`.agents/mcp_config.json` と plugin の MCP サーバーのプロセスも起動し、`AGENTS.md`・`GEMINI.md`・`.agents/rules/` は prompt に注入されます。これをセッションに限って止める `--settings` のキーや CLI のフラグはありません。

そのため wrapper は、git の work tree の外の空のディレクトリを agy の cwd にし、`--add-dir` を使わず、リポジトリのファイルを絶対パスで読ませます。この方式では、相談対象のリポジトリの `.agents/` の hook・MCP・rules・skills は読み込まれず、git の管理外のディレクトリの祖先の `.agents/` も読み込まれません。

空のディレクトリの外にあるファイル (相談対象のリポジトリを含む) を読むには、`--settings` に `"allowNonWorkspaceAccess":true` が必要です。このキーが無いと、headless の実行では `read_file` が承認待ちになって自動で拒否され (soft-deny)、応答が空のまま終わります。キーを付けると、絶対パスを示した `read_file` による読み取りができ、読み取り先のリポジトリの `.agents/hooks.json` は実行されませんでした。

あわせて、agy の起動に `--settings '{"allowNonWorkspaceAccess":true,"permissions":{"deny":["write_file(*)","command(*)","mcp(*)","read_url(*)","execute_url(*)","download_url(*)"]}}'` を必ず付けます。このセッションに限り、次の操作が権限のレベルで拒否されます。

| deny の規則 | 拒否する操作 | 拒否の確認 |
|---|---|---|
| `write_file(*)` | ファイルの作成と編集 | 確認済み |
| `command(*)` | shell コマンドの実行 | 確認済み |
| `mcp(*)` | MCP の tool の呼び出し | 未確認 |
| `read_url(*)` | URL の取得 | 未確認 |
| `execute_url(*)` | URL の実行 (権限の種別の名前による) | 未確認 |
| `download_url(*)` | URL からの download (権限の種別の名前による) | 未確認 |

ローカルのファイルの読み取りは拒否しないため、agy はリポジトリと request を読んで裏取りできます。deny の規則で拒否された tool の呼び出しは `denied_actions` に記録されず、transcript にだけ残ります。承認を求めずに拒否された呼び出し (soft-deny) は `denied_actions` に記録され、応答は空のまま `status` が `SUCCESS`・exit 0 で終わるため、wrapper は exit 6 にします。

### working tree の事後の検知 (補)

権限設定が効かない場合に備えて、cwd が git の work tree 内なら、wrapper は agy の実行前後に次を記録して比べます。どれかが変わっていれば、agy の終了コードに関わらず exit 5 で失敗にします。

| 比べるもの | 記録の方法 |
|---|---|
| `.git` の設定・hook・info | `<git-dir>/config`・`config.worktree`・`hooks/` 配下・`info/` 配下の各ファイルの path・内容のハッシュ・実行権 (worktree では git-dir と common dir の両方) |
| HEAD と ref | `git symbolic-ref -q HEAD` (detached なら空)、`git rev-parse --verify -q HEAD` (commit の無い repo なら空)、`git for-each-ref --format='%(refname) %(objectname)'` |
| status | `git status --porcelain=v1 -z --untracked-files=all` |
| working tree の内容 | 一時的な index と object ディレクトリで `git read-tree HEAD` (commit の無い repo では空の index) → `git add -A` → `git write-tree` を実行して得る、working tree 全体 (ignore されていない未追跡のファイルを含む) の tree OID |

wrapper が実行する git コマンドにはすべて `GIT_OPTIONAL_LOCKS=0` と `-c core.fsmonitor=false` を付けます。agy が権限設定をすり抜けて `.git/config` に fsmonitor を仕込んでいても、事後の検査でそのコマンドが実行されないようにするためです。実行後の記録では `.git` の設定・hook・info を最初に比べ、変わっていれば `git add` を実行する前に失敗にします。一時的な index と object ディレクトリは agy に渡さない一時ディレクトリに置き、リポジトリの object ディレクトリと本物の index には書き込みません。

この検知は事後のもので、変更を防いだり元に戻したりはしません。変更の主体も区別しないため、相談中に親セッションやユーザーが同じ work tree を変更した場合も exit 5 になります。

## 既知の制約

- agy の読み取りは相談対象のリポジトリに限られず、ローカルのどのファイルにも及ぶ。読んだ内容は助言の本文として親セッションに返りうる
- global の `~/.gemini/config` の設定 (hook を含む) は、空のディレクトリで起動しても効く
- 権限の種別が分からない tool (ブラウザー系・web 検索・subagent 等) は、deny の規則の対象外になりうる
- 権限設定による拒否は、2026-11-27 に agy 1.5.18 で `write_file(*)` と `command(*)` の 2 規則の設定を使って確認した。この設定で、`write_file` によるファイルの作成と `edit_file` による編集はどちらも user-configured deny rule として拒否され、`read_file` による読み取りは成功し、ファイルは変わらなかった。括弧の無い `"write_file"` は反映されなかった。空のディレクトリで起動する方式でも、書き込みは deny の規則で拒否された
- wrapper が渡す設定 (6 規則と `allowNonWorkspaceAccess`) は、同じ日に agy 1.5.18 で受理され、この設定での相談が成功した。`-p "/permissions"` では deny の規則が global scope の deny として表示される。`execute_url(*)` と `download_url(*)` が効くかは未確認。`mcp(*)` と `read_url(*)` が MCP の tool と URL の取得を実際に拒否するかも未確認。agy の更新で設定が受け付けられなくなった場合は、すべての相談が exit 4 になる。設定全体が無視されるようになった場合は、権限による拒否が効かず、working tree の事後の検知だけが残る
- agy はディレクトリの一覧や検索のツールを使えない可能性がある (model の自己申告による)。読ませたいファイルは request に絶対パスで列挙する
- 事後の検知は次の変更を見ない: `.gitignore` などで ignore されたファイル、リポジトリの外 (wrapper の一時ディレクトリを含む)、`.git` のうち設定・hook・info・ref 以外の部分 (object ディレクトリ、logs など)、`core.hooksPath` で `.git` の外に置いた hook、cwd が git の work tree の外のときのすべての変更
- 事後の検知の `git add -A` は、利用者の git 設定にある clean filter (git-lfs など) を実行し、filter が自分の保存先 (`.git/lfs/` など) に書き込むことがある。また、working tree に既存の object と同じ内容のファイルがあると、git がその object の更新時刻を更新することがある (内容は変わらない)
- 大きなリポジトリでは、事後の検知が実行前後に working tree 全体を読むため時間がかかる
- agy の応答は `--print-timeout 25m` で 25 分に制限される。runner の Bash の timeout は 30 分
- Bash tool が wrapper の実行を background へ移した場合、runner は結果を回収せず `Status: execution-failed` を返す。このとき agy はまだ実行中の可能性がある
- wrapper は agy の JSON 出力の形式 (`status` / `response` / `denied_actions`) と、`-p` を最後に置く引数の制約を前提にする。この前提は agy 1.5.18 (2026-11-27、WSL2) で確認した。macOS での実 agy の動作は未確認

## ファイル構成

| ファイル | 役割 |
|---|---|
| `.claude-plugin/plugin.json` | plugin の manifest |
| `skills/consult/SKILL.md` | 相談 request の組み立て、runner の起動、助言の扱い、失敗時の対応 |
| `agents/antigravity-advisor-runner.md` | request を session scratchpad のファイルに書いて wrapper を 1 回起動し、report を返す runner |
| `scripts/run-antigravity-advisor.sh` | agy を git の work tree の外の空のディレクトリと権限設定で起動し、応答と working tree の変化を検査して助言の本文を返す wrapper |

## キーワード

`antigravity` `agy` `advisor` `second-opinion` `skill` `gemini`

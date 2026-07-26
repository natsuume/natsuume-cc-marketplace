# git-guardrails プラグイン

GitHub Flow に準拠した Git ワークフローを **構造強制** するプラグインです。

## バージョン

v0.6.0

### v0.5.3 → v0.6.0 の変更点

Codex 配布対応 (marketplace 移植) を廃止した。Codex plugin manifest を削除し、Claude Code 版の 3 hook と rebase-workflow Skill は無変更。

### v0.5.2 → v0.5.3 の変更点 (#141)

- 隣接 quote で command / refspec を分断する形と git / gh alias が検出対象外であることを既知の制約へ追記
- 本 hook は cooperative な agent が生成する通常形の誤操作を防ぐものであり、意図的な難読化や alias 展開までを扱う security sandbox ではないという保証境界を明文化

### v0.5.1 → v0.5.2 の変更点 (#139)

- 3 hook の jq parse error を明示的に非ゼロ終了へ伝播し、共有 EXIT trap の診断が確実に stderr へ出るよう修正
- 最初の `exit-trap.sh` 読み込み前にも caller 内の最小 bootstrap trap を設置し、同 library 自体の欠損・構文エラーも無音の exit 0 に戻さないよう修正
- `cmd-parser.sh` / `default-branch.sh` の source status と必須関数 API を読み込み直後に検証し、配布欠損や不完全な更新による command not found が後続の正常分岐で隠れないよう修正
- jq / library / 必須関数の異常系 18 ケースと正常系 6 ケースを process boundary から検証する受入テストを追加

### v0.5.0 → v0.5.1 の変更点 (#140)

- target-mismatch の `git -C <dir>` 検出を token ベースの global option walk に変更し、`git blame -C` / `git log -C` / `git diff -C` のような subcommand-local option を repo 切替と誤認して後続の commit / push / PR 作成を deny する false positive を解消
- global `-C`、引用付き `"-C"`、`-c <name=value>` 後の `-C`、`--git-dir` / `--work-tree` は引き続き target-mismatch として fail-closed に deny

### v0.4.2 → v0.5.0 の変更点

- Codex plugin manifest を追加し、3 つの default branch 保護 hook と rebase Skill を Claude Code / Codex で共有できるようにした

### v0.4.1 → v0.4.2 の変更点

plugin description (plugin.json / marketplace.json / リポジトリ README の一覧テーブル) を 1〜2 文に短縮しました。あわせて PR deny の記述を実挙動 (master/main を head とする PR 作成のみ deny) に合わせて正確化。hook の動作変更はありません。

### v0.4.0 → v0.4.1 の変更点

plugin description (plugin.json / marketplace.json) を人間向けの簡潔な概要に刷新しました (変更履歴・実装詳細は本 README のバージョン節へ集約)。hook の動作変更はありません。

## 概要

「デフォルトブランチ (master/main) への変更は、他ブランチからの **GitHub 上の PR merge** 経由のみで取り込む」という運用を構造的に保証します。ローカル側の write 経路 (commit / push / PR head) を 3 つの PreToolUse フックで多層防御し、cooperative 利用前提で誤操作・横紙破りを deny に倒します。

加えて、rebase によるリモート default branch 取り込みワークフローを Skill として提供します。

### v0.3.2 → v0.4.0 の変更点 (#135, #136, #137)

- **3 hook (push/commit/pr) の検出方式を segment/token ベースに刷新**: v0.3.x までは COMMAND 文字列の改行を無条件に `;` へ正規化してから quote 非対応の regex で invocation を検出していた。この方式は quote 内・heredoc 内の「コマンド例文」(複数行コミットメッセージ中の `git push origin master` 等) を実コマンドと誤認して deny する false-positive を持っていた:
  - `git commit -m "note: run\ngit push origin master\nto publish"` (改行を含むコミットメッセージ) が push hook に deny される (#135)
  - heredoc / JSON 文字列内の `gh pr create --head master` という文字列が pr hook に deny される (#136)
  - 複数行コミットメッセージ本文中の `cd /other && git commit ...` のような例文が commit hook に deny される (#137)
- 修正は pre-push-review/block-pre-push.sh と同じ `cmd-parser.sh` (`split_command` + `tokenize_segment`) ベースの検出への移植。大まかな流れ: segment 分割 → subshell `(...)` / brace group `{...}` のグループ unwrap → `$(...)` / `<(...)` / `>(...)` / バッククォート内の置換 shape 保守的 deny → token level invocation 検出 (env-var assignment skip → `git`/`gh` 判定 → global option walk) → target-mismatch 判定、という単一パイプラインに統一
- 共有 lib `cmd-parser.sh` の `split_command` に潜在していたバグを修正: quote (`'...'`/`"..."`) 内の生改行がそのまま segment に残っていたため、呼び出し側の `while IFS= read -r line; do ... done < <(split_command ...)` が segment 内部の改行を次 segment との境界と誤認し、1 segment が複数行に分裂する実害があった (quote 内改行は paren/brace depth 内の改行と同様に空白 1 文字へ正規化して 1 segment のまま保持するよう修正)。pre-push-review / enforce-draft-pr の `cmd-parser.sh` へも byte-identical で sync
- 新たに **グループ unwrap** (`(cd /other; git push origin feature)` / `{ cd /other; git push origin feature; }` のような subshell / brace group 内の target-mismatch を検出) と **置換 shape の保守的 deny** (`echo $(git push origin master)` / バッククォート版を deny) に対応。旧実装ではこれらは素通りしていた
- **レビュー (codex / code-reviewer / security-reviewer) で実測確認された bug を同一 version 内で追加修正 (計 5 件)**:
  1. **target-mismatch scope が invocation の後続引数まで含み誤 deny (code-reviewer High)**: `git push origin cd` (`cd` という名のブランチ) / `git commit -m cd` / `gh pr create --title cd` のような、たまたま `cd` を含む refspec / コミットメッセージ / オプション値が target-mismatch の `cd` 検出に誤ヒットしていた。scope の最終要素を「segment 全体」から「先頭 token (env assignment 含む) から subcommand token (`push`/`commit`/`create`) までの invocation prefix」に narrowing して修正
  2. **group 内の生改行が空白化され separator 情報が失われ bypass (security-reviewer High)**: `(echo prep<改行>git push origin master)` が無出力 allow になっていた。原因は `cmd-parser.sh` の `split_command` が paren/brace depth 内の改行を空白に正規化していたため、group unwrap の再分割時に separator を復元できなかったこと。paren/brace depth 内 (かつ quote 外) の改行を `;` に正規化するよう修正 (quote 内改行の空白化は維持。3 プラグインの `cmd-parser.sh` へ byte-identical で sync)
  3. **`strip_squoted_text` が dquote 内アポストロフィを squote 境界と誤認し bypass (security-reviewer High)**: `echo "it's $(git push origin master) don't"` のように dquote 内に英文アポストロフィが 2 つ以上あると、naive な `sed -E "s/'[^']*'/ /g"` がその間 (= `$(git push ...)` を含む範囲) を squote 領域と誤認して空白化し、opener-anchored 検出 (下記) が発火しなくなっていた。quote 文脈 (dquote 内では `'` を toggle しない) を追跡する純 bash の文字 walk に置き換えて修正
  4. **group unwrap の「最後の閉じ文字」ヒューリスティックが redirection target 内の `)` を誤選択し bypass (codex P1 / code-reviewer High)**: `(git push origin master) >/tmp/out` (閉じ `)` 直後に redirection が続く形) や `(git push origin master) > $(mktemp)` (redirection target 自体が `$(...)` を含み `)` が複数ある形) で、「segment 内の最後の `)`/`}` で切る」文字列ヒューリスティックが誤った位置で切ってしまい、token が破損して refspec 比較を素通りしていた。quote 文脈 + depth 追跡で「対応する閉じ文字」を正確に特定する `find_group_close` helper に置き換えて修正。閉じ文字より後の suffix (redirection 等) は**破棄せず**独立した worklist 要素として扱う (`(echo hi) > $(git push origin master)` のように redirection target の置換内に隠れた invocation も、後段の置換 shape check に到達させて deny するため)
  5. **codex rescue の再レビューで「quote された command substitution は README 明記のみ」という方針が非承認となり、dquote 内 invocation の opener-anchored 保守的 deny を追加実装 (F6)**: `echo "$(git push origin master)"` のように dquote 内で実行される `$(...)` / `<(...)` / `>(...)` を、substitution opener 自身 (`$(`/`<(`/`>(`) を左境界とした第 2 パスの regex (検査対象は `strip_squoted_text` で single quote 領域のみ空白化したテキスト) で検出して deny する。境界を bare `(`/`;`/`&`/`|` に広げず backtick も opener に含めないのは、コミットメッセージ規約の本文例文や markdown code span を誤検出しないため (詳細は下記「既知の制約」参照)
- 既知の制約を 5 点追記 (下記「既知の制約」参照): シェルラッパー経由の検出対象外、置換 shape 検出のスコープ、dquote 内 command substitution 検出の残存範囲 (backtick 置換 / opener 非直後の invocation)、quote されていない heredoc body 行頭の例文、quote 数が奇数の複数行文字列

### v0.3.1 → v0.3.2 の変更点 (marketplace audit drift cleanup)

- **README 内 version 見出しを `v0.3.0` → `v0.3.2` に同期** (#114 で `v0.3.1` を name のみ bump して README badge と changelog backfill を漏らした self-introduced drift を解消、 本 PR で再修正)
- **plugin.json / marketplace.json / root README plugin 表の同期維持**: bump 対象 3 箇所のうち README が drift していた点を marketplace 全体 audit で検出して再収束

### v0.3.0 → v0.3.1 の変更点 (#114, cross-plugin sync)

- **関連プラグイン pre-push-review の記述を v2.0.0 仕様に同期**: `## 関連プラグイン` の pre-push-review 行が「push 前に `/code-review` (旧名 `/simplify`) → `/codex:review --scope branch` を強制」 という v1.x 以前の旧記述だったため、 v2.0.0 の「`/pre-push-review:review` slash command で 3 レビュー (`/code-review` + codex review wrapper + `pre-push-review:security-reviewer` subagent) を並列起動」 に書き換え。 documentation のみの修正で hook / 動作は不変

### v0.2.4 → v0.3.0 の変更点 (#61, #62, #63, #60)

- **3 hook に診断 EXIT trap を追加 (#61)**: sibling の pre-push-review (`lib/exit-trap.sh`) と同型の `install_exit_trap` を `lib/exit-trap.sh` として導入。hook が予期せず非ゼロで終了 (jq クラッシュ / signal / シェル展開失敗等) した場合に stderr へ通知し、PreToolUse 仕様「その他 exit code で続行」による default branch 保護の無音 fail-open を可視化する。trap は exit code を変えないため deny/allow 挙動は不変
- **false-positive deny を既知の制約に明記 (#62)**: `git checkout <default> -- <pathspec>` (ファイル復元) と remote 名が `master`/`main` の push が誤って deny される件を文書化 (security gate は保守側に倒す方針のため修正せず明記)
- **共通 lib テーブル / ディレクトリ構成を実態に合わせ更新 (#63)**: `default-branch.sh` の全関数を列挙し、ベンダリングしている `cmd-parser.sh` と新規 `exit-trap.sh` を追記。cmd-parser.sh は pre-push-review から byte-identical でベンダリングしている旨を明記
- **README の version 見出し / changelog を実 version に追随 (#60)**: 見出しが v0.2.0 のまま実 version とドリフトしていたため v0.3.0 に更新し、欠落していた v0.2.1〜v0.2.4 の changelog を backfill

### v0.2.3 → v0.2.4 の変更点 (#92)

- rebase-workflow の `SKILL.md` frontmatter の無効なキー `when-to-use` (kebab-case) を有効な `when_to_use` (snake_case) に修正

### v0.2.2 → v0.2.3 の変更点 (#47, cross-plugin)

- **末尾 `\<LF>` (line continuation) による検知 bypass を修正**: bash の `$(...)` が trailing newline を trim する仕様で `"command":"git push origin master\<LF>"` 等が取得時点で壊れ default branch 保護を素通りする経路を、jq 取得直後の復元処理で 3 hook (commit/push/pr) に塞いだ。あわせて line continuation 正規化を macOS bash 3.2 互換実装に統一

### v0.2.1 → v0.2.2 の変更点 (#46)

- 共通 lib `cmd-parser.sh` を pre-push-review v0.8.0 の canonical 実装に sync (byte-identical ベンダリングの同期)

### v0.2.0 → v0.2.1 の変更点 (#42)

- macOS 互換性 fix (cmd-parser.sh canonical) への追随 version bump、およびプラグイン配下を変更したら version を必ず bump するルールを CLAUDE.md に追加

### v0.1.0 → v0.2.0 の変更点

- **commit / push / PR の 3 経路を全て deny 対象に拡張** (旧版は push のみ):
  - `git commit` (デフォルトブランチ上で実行された場合)
  - `git push` (引数の有無に関わらず、デフォルトブランチを更新する経路すべて)
  - `gh pr create` (head が master/main になるケース、`--head` 明示時も含む)
- 旧版の push 検出 regex は `git push <remote> master` のような明示引数形式しか拾えず、`git push` 単独 / `git push origin` 等の引数省略形 (= upstream 設定で master を更新する形) を素通りさせていました。v0.2.0 ではカレントブランチを `git symbolic-ref --short HEAD` で取得し「master/main 上にいるなら全 push 系を deny」「他ブランチからは引数の master/main トークンで判定」の 2 軸で網羅的に検出します。push 検出は `git -C dir push` / `git -c push.default=current push` のような git global option 経由の形式も拾えるよう OPT-aware にしています。
- **target-mismatch prefix の保守的 deny**: 対象 repo / branch を切り替える前段を含むコマンドは、hook 実行時の cwd / カレントブランチと実コマンドの target が乖離して default branch 保護を素通りさせる経路になります。3 hook すべてで `has_target_mismatch_prefix` 検出を入れ、対象 repo / branch に切り替えた上で別 Bash 呼び出しとして実行するよう促します:
  - 対象 repo を切り替える形: `cd /other && git push` / `git -C /other push` / `GIT_DIR=/foo/.git git push`
  - 対象 branch を切り替える形: `git switch master && git commit` / `git checkout main && git push` / `git switch -c master && ...` (新規作成も同列)
  - subshell / brace group / command substitution 越し: `(cd /other; git commit)` / `{ cd /other; git push; }` / `$(cd /other; git ...)` (`()` / `{}` を空白に正規化してから検出)
  - redirection 演算子直結: `cd>/dev/null /other && git push` / `cd</dev/null /other && ...` (cd の右境界に `<>` を含める)
  
  pre-push-review (v0.1.0 で pre-commit-review v0.4.0 から push 境界に移行) の cd 許容方針とは別軸の防御で、本プラグインは default branch 保護が責務。
- 共通 lib (`hooks/scripts/lib/default-branch.sh`) に `current_branch` / `is_default_branch` / `emit_deny` / `has_target_mismatch_prefix` / `TARGET_MISMATCH_DENY_REASON` を集約し、3 hook 間で判定ロジック・deny ペイロード形式・メッセージ文言がドリフトしないようにしています。

> v0.1.0 までは `gh pr create` への `--draft` 自動付与もこのプラグインに含まれていましたが、責務分離のため [enforce-draft-pr](../enforce-draft-pr/) プラグインに切り出しました。draft 強制を使いたい場合はそちらを別途インストールしてください。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install git-guardrails@natsuume-plugins
```

## 機能一覧

### Hooks

#### 1. block-default-branch-commit (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-default-branch-commit.sh`

カレントブランチが master/main のときに `git commit` 系コマンドを deny します。

**動作**:

- 入力に `commit` 文字列が無ければ即離脱 (粗フィルタ)
- `cmd-parser.sh` の `split_command` で COMMAND を segment に分割し (subshell `(...)` / brace group `{...}` は中身を unwrap して再分割)、各 segment を `tokenize_segment` でトークン化して検出する (quote / heredoc 内のテキストを実コマンドと誤認しない)
- env-var assignment を skip した先頭 token が `git`/`*/git` で、続く global option (`-C` / `-c key=val` 等) を walk して `commit` サブコマンドに到達する segment のみを commit invocation として検出
- `$(...)` / `<(...)` / `>(...)` / バッククォート内に `git commit` の形状が隠れている場合は保守的に deny
- `git symbolic-ref --short HEAD` でカレントブランチを取得
- master/main なら deny、それ以外なら exit 0
- detached HEAD (cherry-pick / rebase 中など) はブランチ名が空なので自然に通る
- **chained 形式**: `git commit -m a && cd /other && git commit -m b` のように複数の commit を連結したコマンドは、各 commit 呼び出しを独立に target-mismatch 検査します。最初の commit が問題なくても、その後の cd で対象 repo を切り替えて 2 つ目の commit を行う経路は detect されて deny

**deny 例**:

```
master ブランチで `git commit -m "fix"` を実行
  → "デフォルトブランチ (master) 上での git commit は禁止されています。
     working branch を切ってから commit してください
     (例: git switch -c feat/my-change)。"
```

#### 2. block-default-branch-push (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-default-branch-push.sh`

デフォルトブランチを更新するすべての push 系コマンドを deny します。

**動作**:

- `cmd-parser.sh` の `split_command` で COMMAND を segment に分割し (subshell `(...)` / brace group `{...}` は中身を unwrap して再分割)、各 segment を `tokenize_segment` でトークン化して検出する (quote / heredoc 内のテキストを実コマンドと誤認しない)
- env-var assignment を skip した先頭 token が `git`/`*/git` で、続く global option (`-C` / `-c key=val` 等) を walk して `push` サブコマンドに到達する segment のみを push invocation として検出
- `$(...)` / `<(...)` / `>(...)` / バッククォート内に `git push` の形状が隠れている場合は保守的に deny (例: `echo $(git push origin master)` / `` echo `cd /other; git push origin feature` ``)
- カレントブランチが master/main の場合: 引数の有無に関わらず deny (引数省略形 `git push` / `git push origin` でも upstream 経由で master を更新するため)
- それ以外のブランチの場合: push サブコマンドより後の各 token を `+` / `refs/heads/` プレフィックス剥がし後に完全一致比較し、master/main が現れたら deny
  - `git push origin master` (明示)
  - `git push origin feat:master` (refspec の右側) / `git push origin master:feat` (refspec の左側)
  - `git push --force-with-lease origin master`
  - `git push origin +master` / `git push origin refs/heads/master` / `git push origin HEAD:refs/heads/main` (refspec normalize 後に完全一致)
  - `git push origin "master"` (shell quote 剥がし後に完全一致)
- **`--all` / `--mirror` も deny**: refspec を明示せず全ローカル branch を push するモードは、master/main が local に存在すれば自動的にそれを更新するため、token 完全一致比較を素通りする bypass になる。default branch 保護のため一律 deny
- **chained 形式**: `git push origin feature && git push origin master` のように `&&` / `||` / `;` / `&` / `|` で連結された複数の push を含むコマンドは、各 push 呼び出しを独立に検査します。最初の push が APPROVE でも 2 つ目が master を更新するならその時点で deny
- **グループ unwrap 経由**: `(cd /other; git push origin feature)` / `{ cd /other; git push origin feature; }` のような subshell / brace group は中身を展開してから検査するため、内部の `cd` による target-mismatch も検出される。閉じ括弧の直後に redirection が続く形 (`(git push origin master) >/tmp/out` / `(git push origin master) > $(mktemp)` 等) も、quote 文脈 + depth 追跡で対応する閉じ括弧を正確に特定してから解析するため中身の検出は損なわれない。閉じ括弧より後の redirection suffix は破棄せず独立した segment として扱われるため、suffix 自体に `$(git push ...)` のような置換が隠れていても後段の置換 shape check で deny される。group 内の生改行 (`(echo prep<改行>git push origin master)` 等) は `;` (real separator) として扱われるため、unwrap の再分割で複数コマンドに正しく分かれる

**通す例**:

- master 以外のブランチからの `git push` (引数なし、master を更新しない)
- `git push origin feature` (master トークンを含まない)
- `git push origin feature/main` (working branch 名は完全一致しないので APPROVE)
- `echo git push origin master` / `grep "git push origin master" README.md` (segment 先頭が `git` ではないテキスト参照は invocation として検出されない)
- 複数行コミットメッセージ本文中に `git push origin master` という例文を含む `git commit -m "..."` (quote 内改行は 1 segment のまま空白に正規化されるため、本文が独立した invocation として分裂しない)

#### 3. block-default-branch-pr (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-default-branch-pr.sh`

`gh pr create` で master/main を head (= source) とする PR を deny します。head が master とは「master の中身を別ブランチに PR で入れる」変則的な経路で、cooperative 運用では発生せず防御対象とします。

**動作**:

- `cmd-parser.sh` の `split_command` で COMMAND を segment に分割し (subshell `(...)` / brace group `{...}` は中身を unwrap して再分割)、各 segment を `tokenize_segment` でトークン化して検出する (quote / heredoc 内のテキストを実コマンドと誤認しない)
- env-var assignment を skip した先頭 token が `gh`/`*/gh` である segment について、以降の token 列を走査し隣接する 2 token が `pr` `create` である位置を invocation として検出 (`gh` と `pr create` の間に任意 token `-R owner/repo` 等を許容する意味論を維持)
- `$(...)` / `<(...)` / `>(...)` / バッククォート内に `gh pr create` の形状が隠れている場合は保守的に deny
- head 指定の以下 5 形式を `create` token より後の token 列から抽出して評価し、master/main なら deny:
  - `--head <branch>` (別トークン long)
  - `--head=<branch>` (`=` 付き long)
  - `-H <branch>` (別トークン short)
  - `-H=<branch>` (`=` 付き short)
  - `-Hbranch` (cluster short)
- 値が `owner:branch` 形式 (cross-fork PR) なら owner プレフィックスを剥がした branch 部分で判定。shell quote (`"master"`/`'main'`) も剥がしてから比較
- 明示がなければカレントブランチを評価し、master/main なら deny
- それ以外は exit 0
- **chained 形式**: `gh pr create --head feat && gh pr create --head master` のように複数の `gh pr create` を連結した場合、各 PR 作成を独立に検査します。最初の PR が APPROVE でも 2 つ目で master を head にする形なら detect されて deny

**通す例**:

- working branch から `gh pr create --base master` (= 通常の PR)
- master 上から `gh pr create --head feature` (head 明示で master を head にしない)

### Skills

#### /rebase-workflow

**ファイル**: `skills/rebase-workflow/SKILL.md`

rebase を用いてリモートのデフォルトブランチの変更を作業ブランチに取り込む手順をガイドします。

**使用シーン**:

- 「リモートの変更を取り込みたい」
- 「rebase したい」
- 「master/main を取り込む」
- 「ブランチを最新にしたい」

## 共通 lib

3 つの hook (`block-default-branch-{commit,push,pr}.sh`) が source する共有ライブラリ:

| ファイル | 用途 |
|---|---|
| `hooks/scripts/lib/default-branch.sh` | デフォルトブランチ名集合 (`master`/`main`) と、`is_default_branch` / `current_branch` / `strip_shell_quotes` / `normalize_refspec_part` / `strip_quoted_text` / `strip_squoted_text` (v0.4.0 追加。single quote 領域のみ空白化し dquote 内容は残す。dquote 内 command substitution の opener-anchored 検出用) / `emit_deny` / `has_target_mismatch_prefix` の関数群 + `readonly TARGET_MISMATCH_DENY_REASON` を集約。3 hook が source して使う |
| `hooks/scripts/lib/cmd-parser.sh` | pre-push-review から **byte-identical でベンダリング** している共有パーサ。v0.4.0 以降、git-guardrails の 3 hook は `normalize_line_continuations_to_space` に加えて `split_command` / `tokenize_segment` / `skip_env_assignments` / `unquote_token` も実際に使用する (segment/token ベースの invocation 検出のため)。canonical 実装とのドリフト防止のためファイル全体を丸ごとベンダリングしている (ヘッダコメントが pre-push-review を指すのはこのため) |
| `hooks/scripts/lib/exit-trap.sh` | 予期せぬ非ゼロ終了を stderr に可視化する `install_exit_trap`。3 hook が冒頭で呼ぶ (#61) |

## 既知の制約 (cooperative 利用前提)

- **本 plugin は cooperative な agent が生成する通常形の Git / GitHub CLI 操作を対象とする誤操作防止 hook であり、任意の shell 入力を解析・封じ込める security sandbox ではありません**。特に、次の意図的な難読化・間接呼び出しは bash 実行時には通常形と等価でも検出対象外です (#141):
  - command keyword 内に隣接 quote を挿入する形 (`git pu''sh origin main` / `git commi''t -m x` / `g''h pr create --head main`)。粗フィルタと token 比較は、shell が quote fragment を連結した後の語を復元しません
  - default branch refspec を隣接 quote で分断する形 (`git push origin 'ma''in'`)。quote 除去は token 全体を囲む 1 組だけを対象とし、token 途中の quote fragment は正規化しません
  - git / gh のユーザ定義 alias (`git config alias.p push` 後の `git p` 等)。hook は実行時の alias 設定を展開せず、command 文字列に現れた subcommand だけを判定します
- ブランチ名は `master` と `main` をハードコード対象としています。`develop` 等を保護対象に加えたい場合は `lib/default-branch.sh` の `DEFAULT_BRANCH_NAMES` 配列に追記してください
- push hook の master/main 検出は push 引数 token を完全一致比較するため、`git push origin origin/master` のように remote-tracking ref を直接引数に書く稀な経路は false negative で通ります (cooperative 利用では発生しにくく、`feature/main` のような working branch 名や `git push ... && git switch main` の連結後段を誤検出しないことを優先)
- **以下は誤検知 (false-positive deny) として `deny` に倒れます** (#62)。いずれも security gate を「保守側に倒す」方針の結果で、cooperative 利用では実害が小さいため修正せず明記しています。回避するには対象操作を別の方法で行ってください:
  - `git checkout <default> -- <pathspec>` を **commit/push/PR と連結した** chained コマンド (例: `git checkout main -- file.txt && git commit -m x`): `-- file.txt` は branch を切り替えず特定ファイルを作業ツリーに復元するだけですが、後続 invocation の target-mismatch 前段検査 (`has_target_mismatch_prefix`) が `checkout main` 部分を branch 切替とみなし、その commit/push/PR を deny します (単独の `git checkout main -- file.txt` は commit/push/PR を含まず各 hook の粗フィルタを通らないため deny されません)。回避するには checkout を別の Bash 呼び出しに分けてから commit してください
  - remote 名が `master` / `main` の push (例: `git push master feature`): `master` という名前の **remote** へ feature を push する操作ですが、push hook は引数 token を remote/refspec の区別なく比較するため、その `master` を default branch token と誤認して deny します
- `gh pr create` 以外の経路 (Web UI 等での PR 作成) は介入できません。これは設計上の意図です
- **v0.4.0 の segment/token ベース検出に伴う残存制約 6 点**:
  1. **シェルラッパー経由の invocation は検出対象外**: token level 検出は「env-var assignment を skip した最初の実 token が `git`/`gh` であること」を要求するため、`bash -c "git push origin master"` / `sh -c "..."` / `eval "..."` / `time git push ...` / `env git push ...` のようなラッパー経由の invocation は素通りします (pre-push-review/block-pre-push.sh は明示的にこれらを deny しますが、本プラグインの 3 hook は対象外。cooperative 利用では発生しにくいため許容)
  2. **置換 shape の保守的 deny は対象コマンド文字列を含む場合のみ発動**: `$(...)` / `<(...)` / `>(...)` / バッククォート内に対象コマンド (`git push` / `git commit` / `gh pr create`) の形状が実際に含まれる場合のみ deny する positive-detection です。`$(cd /other)` のように cwd だけを変える置換自体は本 hook のスコープ外ですが、その後に続く実 invocation は別 segment として通常どおり検出されるため実害はありません
  3. **dquote 内 command substitution は opener 直後の invocation のみ検出対象**: bash は dquote 内でも `$(...)` / `<(...)` / `>(...)` を実行するため、`echo "$(git push origin master)"` のような形は deny されます (第 2 パス: `strip_squoted_text` で single quote 領域のみ空白化した上で、左境界を substitution opener 自身 (`$(`/`<(`/`>(`) に限定した opener-anchored regex で検出。境界を bare `(`/`;`/`&`/`|` に広げないのは、コミットメッセージ規約 `git commit -m "$(cat <<'EOF' ... EOF)"` の本文中に含まれるコマンド例文まで誤検出して deny する false-positive クラス (本プラグインが解消した問題) を再導入しないため)。残存する検出対象外 (既知の false negative):
     - **dquote 内バッククォート置換** (`echo "` `` `git push origin master` `` `"` のような archaic command substitution): backtick を opener に含めると、markdown code span (`` `git push origin master` `` のような例文をバッククォートで囲む house style) を含むコミットメッセージが軒並み誤 deny されるため、意図的に検出対象外としています
     - **opener 直後が保護対象コマンドでない形** (`"$(cd /x; git push origin feature)"` 等): opener-anchored 検出は「opener の直後」のみを見るため、`;` 等の区切りを挟んだ後続コマンドは dquote 内では検出されません (dquote 内で `;` を境界として扱う設計は heredoc 例文の誤検出を増やすため採用していません)
     
     逆に、**保守的 deny 側の新たな false positive** として、引用符内に `$(git push origin master)` という **リテラルな例文テキスト** を opener 直後の形で書いた場合も deny されます (実行されない例文であっても区別できません)。回避するには例文の記法を変えてください (バッククォートで囲む・空白を挟む等)
  4. **quote されていない heredoc body の行頭例文は依然 false positive になり得ます**: `split_command` は heredoc (`<<DELIM ... DELIM`) を追跡しないため、`cat > doc.md <<'EOF'` で書き出すドキュメント本文のように **quote で囲まれていない** heredoc body の各行は通常の segment として解析されます。body の行頭に `git push origin master` のような保護対象コマンドの例文がそのまま置かれると、token level 検出でも実 invocation と区別できず deny されます。quote 内に包まれた heredoc (リポジトリ規約のコミット形式 `git commit -m "$(cat <<'EOF' ... EOF)"` 等) は v0.4.0 の quote 内改行正規化により 1 segment に保持されるため、この問題は起きません。回避するにはファイル書き出しに Write 系ツールを使うか、例文の行頭にバッククォート等を置いてください (heredoc 追跡の実装は follow-up issue で検討)
  5. **複数行文字列内の double quote が奇数個の場合の残存ケース**: quote の対応が取れない複数行文字列では、奇数個目の quote 以降のテキストが quote 外と解釈され、そこに含まれる改行が segment 境界に化けます。その位置に保護対象コマンドの行頭例文があると false positive になり得ます (行内で対応が取れた quote では発生しません)
  6. **稀な git global option (`--namespace` 等) は token level 検出を素通りし得ます**: token walk は「2 token 消費する global option」を `-C`/`--git-dir`/`--work-tree`/`-c`/`--config`/`--config-env` に固定したハードコードリストで判定します。`git --namespace foo push origin master` のようにリスト外の「引数を取る global option」が使われると、`foo` を subcommand と誤認して push 検出を素通りします。あえて機械的には塞いでいません: 「任意の option は次の非 `-` token を引数として消費し得る」という汎用ルールに一般化すると、`--bare` / `-p` / `--paginate` のような**引数を取らないブールフラグ**の直後に来る本物の subcommand token (`git --bare push origin master` の `push`) を誤って「フラグの引数」と飲み込んでしまい、今回塞ごうとしている穴より広い bypass を新たに生みます。参照実装である pre-push-review (`block-pre-push.sh`) 自身も同じ固定リスト方式を採用しており、本プラグインはそれに意図的に揃えています。`--namespace` 等の稀な global option 経由の push は、cooperative 利用では発生しにくい edge case として許容します

## ディレクトリ構成

```
git-guardrails/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── block-default-branch-commit.sh
│       ├── block-default-branch-push.sh
│       ├── block-default-branch-pr.sh
│       └── lib/
│           ├── default-branch.sh
│           ├── cmd-parser.sh    # pre-push-review から byte-identical ベンダリング
│           └── exit-trap.sh
├── skills/
│   └── rebase-workflow/
│       └── SKILL.md
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`
- `git`

## 関連プラグイン

- [enforce-draft-pr](../enforce-draft-pr/) — `gh pr create` 時に `--draft` を自動付与 (任意導入)
- [pre-push-review](../pre-push-review/) — push 前に 3 レビュー (`/code-review` + codex review wrapper + `pre-push-review:security-reviewer` subagent) を `/pre-push-review:review` slash command で並列起動して強制 (本プラグインのブランチ判定とは別軸の防御)

## 関連情報

- [GitHub Flow](https://docs.github.com/ja/get-started/quickstart/github-flow)
- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)

#!/bin/bash
# text-paths.sh
# 変更が「テキスト系文書」だけで構成されるかを判定する。push gate / merge gate と codex
# review wrapper は、この判定が成立した変更に対してレビュー記録を要求せずに通す。判定に
# 必要な情報が得られない場合や git の呼び出しが 1 つでも失敗した場合は、すべて
# 「テキスト系文書のみではない」(= レビュー必須) 側に倒す (fail-closed)。
#
# 本 lib は pre-push-review (`plugins/pre-push-review/hooks/scripts/lib/`) が canonical で、
# pre-push-codex-review と pre-merge-cross-review の `hooks/scripts/lib/` に byte-identical な
# コピーを置く。同一性は tests/test_shared_lib_copies.py と tests/test_pre_merge_lib_copies.py
# が検査する。コピー先の plugin が持たない lib もあるため、本 lib は他の lib に依存せず
# 単独で source できる状態を保つ。
#
# bash 3.2 (macOS の /bin/bash) で動くこと。大文字小文字変換の展開 (`${var,,}` 等)・連想
# 配列・mapfile / readarray・GNU 拡張のコマンドオプションを使わない。
#
# ## テキスト系文書の定義 (is_text_document_path)
#
# repo 相対 path の basename の最後の `.` より後ろ (拡張子) が、大文字小文字を区別せずに
# md / markdown / rst / adoc のいずれかであればテキスト系文書とする。basename に `.` が無い
# (拡張子が無い) path はテキスト系文書ではない。txt と mdx を含めないのは、txt には依存
# 宣言 (requirements.txt 等) やビルド定義 (CMakeLists.txt) があり、mdx にはコードを埋め込める
# ため、どちらもレビュー無しで通すと挙動の変更を見逃すから。
#
# Claude Code や他の coding agent は、特定の場所・名前の Markdown を指示として読み込む
# (実質的にコードとして働く)。そのため、次に列挙した場所・名前の Markdown はコード扱いにし、
# テキスト系文書から除く:
#
#   - basename を除くディレクトリ要素のどれかが `agents` / `commands` / `skills` / `rules` /
#     `output-styles` / `.claude` / `.agents` / `.cursor` / `.gemini` / `.codex` / `.windsurf` /
#     `.clinerules` / `.roo` / `.kiro` / `.continue` / `.junie` / `.amazonq` に一致する (深さは
#     問わない)
#   - 連続する 2 つのディレクトリ要素が `hooks` → `prompts`、`.github` → `instructions`、
#     `.github` → `prompts`、`.github` → `chatmodes` のいずれかの順に並ぶ
#   - basename が `CLAUDE.md` / `CLAUDE.local.md` / `AGENTS.md` / `AGENTS.override.md` /
#     `GEMINI.md` / `copilot-instructions.md` に一致する
#
# 除外するのは列挙した場所・名前だけである。列挙に無い場所にある agent の指示ファイルや、
# 除外対象のファイルから間接的に読み込まれる文書 (`CLAUDE.md` から `@path` で import される
# 文書など) はテキスト系文書と判定される。
#
# 除外条件の照合は ASCII の大文字小文字を区別しない。macOS 既定の filesystem は大文字小文字を
# 区別しないため、`Agents/` や `claude.md` も `agents/` や `CLAUDE.md` と同じファイルとして
# 解決され、agent に読み込まれる。
#
# 同じ理由で、次の非 ASCII 文字を path のどこかに含む場合は、テキスト系文書としない:
#
#   - 大文字小文字の変換や畳み込みで ASCII の文字になる文字: `İ` (U+0130)・`ı` (U+0131)・
#     `ſ` (U+017F)・`ß` (U+00DF)・`ẞ` (U+1E9E)・`K` (U+212A)・合字 U+FB00〜U+FB06 (`ﬀ`〜`ﬆ`)
#   - HFS+ が名前の比較で無視する不可視の文字 (git の `core.protectHFS` が扱う文字と同じ):
#     U+200C〜U+200F・U+202A〜U+202E・U+206A〜U+206F・U+FEFF
#
# filesystem がこれらを含む名前を ASCII だけの名前と同一視する場合、`ſkills/` のような path は
# ASCII の照合では除外対象に当たらないまま、除外対象の場所 (`skills/`) に解決されうる。日本語
# などの文字はこの条件に当たらない。
#
# 例: `README.md` / `docs/a.MD` / `plugins/x/README.md` / `docs/skills.md` (basename は
# ディレクトリ要素の除外条件の対象外) / `.github/README.md` はテキスト系文書。
# `plugins/x/agents/r.md` / `plugins/x/skills/s/SKILL.md` / `plugins/x/hooks/prompts/p.md` /
# `a/rules/r.md` / `.claude/CLAUDE.md` / `sub/CLAUDE.md` / `AGENTS.md` / `docs/agents.md` /
# `docs/Agents/x.md` / `.github/copilot-instructions.md` / `notes.txt` / `a.mdx` / `x.sh` /
# `Makefile` / `README` はテキスト系文書ではない。
#
# 分類は外部コマンドを使わず、case パターンと parameter 展開だけで行う。本 lib は hook
# 経由だけでなくセッション shell から起動される wrapper からも source されるため、呼び出し
# 元環境の alias / 関数 export / PATH 差し替えで分類結果が変わる余地を作らない。大文字小文字
# を区別しない照合も、範囲表記 (`[a-z]` 等) を使わず `[aA]` のように文字を列挙して書く
# (bracket 式の範囲が含む文字は locale によって変わるため)。
#
# ## 範囲の判定 (is_text_only_range_in)
#
# `<base_rev>..<head_rev>` の変更がテキスト系文書だけと判定するのは、次をすべて満たす
# ときに限る:
#
#   1. `<base_rev>..<head_rev>` の commit が 1 つ以上、100 以下である
#   2. 範囲内に merge commit が無い
#   3. 範囲内の各 commit について、親 commit との raw 差分の全エントリで、変更前・変更後の
#      ファイル mode がそれぞれ `000000` (不在) か `100644` (通常ファイル) のいずれかで、
#      かつ path が is_text_document_path を満たす。親を持たない commit が範囲内にあれば
#      不成立
#   4. merge-base(`<base_rev>`, `<head_rev>`) と `<head_rev>` の raw 差分も、3 と同じ条件を
#      満たす
#
# git はすべて `--no-replace-objects -c core.commitGraph=false` を付けて呼ぶ (秘匿情報の検査で
# gitleaks に渡す入力を書き出す `git log` も同じ)。ローカルの replace ref (`git replace`) があると、
# git は commit や tree を置き換え先の内容で読むため、コードを足す commit をテキスト系文書だけの
# commit に見せかけられる。commit-graph ファイルは commit の親を記録した cache で、git は
# commit の object より先にこれを読むため、書き換えられると範囲の commit を隠せる。push や
# merge で remote に届くのは、どちらにも左右されない object そのものである。
#
# 同じ理由で、graft ファイル (`<git dir>/info/grafts`、または環境変数 `GIT_GRAFT_FILE` が
# 指す file) が存在する場合は、判定を行わずレビュー必須 (戻り値 1) とする。graft は commit の
# 親を書き換えるため、コードを足して後で消す中間 commit を範囲から隠せる (3 の検査を回避
# できる)。graft を無効にする git の option は無いため、存在を検出して打ち切る。
#
# object そのものの改ざん (git dir の object の file を書き換える等) は検出しない。git dir に
# 書き込める主体は、判定に使う object 自体を書き換えられる。
#
# raw 差分は rename / copy の検出を行わずに取る (rename は削除と追加の 2 エントリになり、
# 変更前と変更後の両方の path が検査対象になる)。出力は NUL 区切りで読み、空白・改行・
# 非 ASCII 文字を含む path も quote されないまま扱う。mode の条件により、symlink
# (`120000`)・submodule (`160000`)・実行ビット付きファイル (`100755`) はテキスト系文書から
# 除かれる。
#
# net diff (merge-base..head) だけでなく範囲内の各 commit を検査するのは、コードを追加する
# commit とそれを revert する commit を重ねた範囲を skip させないため。push や merge で
# remote に届くのは net diff ではなく範囲内の全 commit であり、中間 commit のコードも履歴に
# 残る。
#
# commit 数の上限 (100) と判定の時間の上限 (10 秒) は、gate の hook が timeout する前に判定を
# 終えるためのもの。PreToolUse の command hook は timeout しても通常は tool call を止めないため、
# timeout した gate は判定を終えないまま push や merge を止められなくなる (Claude Code の hooks
# リファレンスで 2026-11-28 に確認。command hook の既定の timeout は 600 秒で、gate の hook は
# timeout を指定しない)。範囲の判定 (10 秒) と秘匿情報の検査 (20 秒) の上限は、gate 自身の処理
# (merge gate が先に行う `gh pr view` 等) と合わせても既定の timeout より十分短い。上限を超えた
# 場合は判定を打ち切ってレビュー必須 (戻り値 1) として扱う。
#
# 3 は commit ごとに git を起動し、raw 差分の解析は bash で 1 エントリずつ行うため、commit 数と
# 変更されたファイルの数に比例して時間がかかる。時間の上限は、判定の開始から数えて、
# 各 commit の差分を取る前と raw 差分の各エントリを読むたびに確かめる (bash の `SECONDS`
# を使うため、1 秒未満の端数の分だけ遅れうる)。秘匿情報の検査の時間は別に上限を設ける
# (下記「秘匿情報の検査」)。1 の commit 数は上限 + 1 まで数えたら数えるのをやめる
# (`rev-list --count --max-count=101`)。上限を大きく超える範囲を全部たどらないため。
#
# 時間の上限は git を呼ぶ前に確かめるため、1 回の git の呼び出しが止まったままになると上限を
# 超える。partial clone (`--filter` を付けて clone した repo) では、git は手元に無い object を
# remote (promisor) から取りに行き、remote が応答しなければ gate の hook が timeout するまで
# 止まりうる。そのため is_text_only_range_in と is_text_only_push_in は、関数の中でだけ
# 環境変数 `GIT_NO_LAZY_FETCH=1` を設定して (`local -x`) git を呼び、手元に無い object を
# 取りに行かせない (秘匿情報の検査の `git log` を含む)。判定に必要な object が手元に無い範囲は、
# git の呼び出しが失敗してレビュー必須になる (範囲の判定では 1、秘匿情報の検査では 2)。この
# 環境変数は git 2.45.1 以降と、同じ修正を取り込んだ保守版 (2.39.4・2.43.4・2.44.1 等) で有効で、
# それ以外の古い git は無視する (取りに行き、時間の上限を超えうる)。git 2.45.0 は、`git log -p`
# が差分のための blob を先にまとめて取りに行く処理でこの環境変数を確かめない。同じ働きの
# `--no-lazy-fetch` option は git 2.45.0 で追加され、それより古い git では未知の option として
# 失敗するため使わない。
#
# 判定は 1 → 2 → 4 → 3 の順に行う。git の呼び出しが 1 回で済む 4 (net diff) を、commit 数に
# 比例する 3 より先に行い、net diff にテキスト系文書以外の変更がある範囲では各 commit を走査
# せずに不成立を返す。
#
# 1-4 を満たした範囲は、最後に秘匿情報の候補を検査する (下記「秘匿情報の検査」)。
#
# exit code:
#
#   - 0: 範囲内の変更がテキスト系文書だけで、秘匿情報の検査も通った (skip してよい)
#   - 1: 1-4 のいずれかを満たさない (テキスト系文書だけではない、commit 数が上限を超える等)、
#        graft ファイルがある、判定が時間の上限を超えた、または 1-4 の判定に必要な git の
#        呼び出しが失敗した (判定に必要な object が手元に無い場合を含む)
#   - 2: テキスト系文書だけだが、gitleaks の結果が canary だけの検出ではなかった (秘匿情報の
#        候補を検出した・canary を検出しなかった・実行に失敗した)、gitleaks に渡す入力を
#        書き出せなかった (`git log` の失敗・空の出力)、範囲に UTF-8 以外の `encoding` header を
#        持つ commit がある、gitleaks に渡す入力が 100,000 バイトを超える (下記「秘匿情報の
#        検査」)、または実際に使う gitleaks の設定に `path =` の key がある・設定を読めない
#        (下記「設定と ignore の読み方」)
#
# 呼び出し側は 0 のときだけ skip し、1 と 2 では従来どおりレビューを要求する。2 を 1 と
# 分けるのは、deny の理由文で「テキスト系文書だけだが秘匿情報の検査で止めた」ことを示すため。
#
# ## 秘匿情報の検査 (gitleaks)
#
# テキスト系文書だけの skip ではレビュー (security review を含む) を行わないため、文書に
# 書き込まれた認証情報も検査されない。PATH に gitleaks がある (`command -v gitleaks` が成功
# する) 場合に限り、skip の前に範囲内の commit を検査する。範囲内の commit の message と差分を
# `git log` で一時ファイル (<log>) に書き出し、1 行目に canary を置いた入力 (<入力>) を作って
# gitleaks の stdin に渡す:
#
#   git --no-replace-objects -c core.commitGraph=false [-C <target_cwd>] -c diff.relative=false
#     log -p -U0 --no-textconv --no-ext-diff --text --no-color --no-show-signature
#     --encoding=UTF-8 --format='commit %H%n%n%B' <base の OID>..<head の OID> > <log>
#
#   <入力> = <canary> + 改行 + <log>
#
#   (cwd = <空のディレクトリ>)
#   gitleaks stdin --no-banner --redact --ignore-gitleaks-allow --log-level error
#     --exit-code 3 --timeout <残り秒数> --report-format template
#     --report-template <template> --report-path <report> [--config <一時ファイル>]
#     --gitleaks-ignore-path <空のディレクトリ> < <入力>
#
#   <template> = `{{ range . }}{{ .RuleID }} {{ .StartLine }}\n{{ end }}` (`\n` は改行 1 つ。
#   末尾に改行は付けない)
#
#   - 範囲の両端には、1-4 の判定で解決済みの commit OID を使う。`git log` の先頭の option の
#     理由は「範囲の判定」を参照
#   - 入力は path を持たないため、path で照合する allowlist (gitleaks の既定の設定にある、
#     場所を問わず当たる `node_modules`・`bower_components`・`vendor/bundle`・ファイル名の
#     `gitleaks.toml` 等と、base の `.gitleaks.toml` に書かれたもの) は文書に当たらない。repo を
#     path ごとに走査させると、テキスト系文書を `docs/node_modules/a.md` のような場所に置く
#     だけで検出から外せる
#   - commit message (本文を含む) も検査する。差分は追加行と削除行を検査するため、秘匿情報を
#     含む行を消す変更でも skip しない (レビュー必須の側に倒す)
#   - `-U0` は文脈行を入力に入れず、差分の追加行と削除行だけを検査するために付ける。
#     文脈行を入れると、既存の例示用の認証情報の近くを編集しただけで skip しなくなる。ただし
#     git は hunk の見出し (`@@ ... @@` の行) の後ろに、変更箇所より前にある行を 1 行付ける
#     ことがあり (関数名の表示。属性で diff driver を指定しない文書では、英字・`_`・`$` で
#     始まる直近の行)、その行は入力に入る
#   - `git log` の残りの option は、入力をローカルの設定と属性で変えさせないために付ける。
#     `--format` は利用者の `format.pretty` (`oneline` 等) で message の本文が落ちないように、
#     `diff.relative=false` は target_cwd が subdirectory のときに差分がその配下に絞られない
#     ように、`--encoding=UTF-8` は `i18n.logOutputEncoding` で message が別の encoding に
#     変換されないように付ける (commit 自身の `encoding` header による変換はこの option では
#     防げないため、UTF-8 以外の header を持つ commit では検査しない。下記)。属性で文書を
#     binary 扱いにする (`.git/info/attributes` の `*.md binary` 等) と、差分から文書の内容を
#     読み取れない。`color.ui = always` では、
#     出力に色の制御文字が混ざる。textconv で内容を変換した場合も、変換後の差分に秘匿情報が残らなければ
#     検出されない。`--text` / `--no-textconv` / `--no-color` はそれぞれを無効にし、
#     `--no-ext-diff` は外部 diff を使わせない。`--no-show-signature` は `log.showSignature`
#     で署名の検証 (gpg 等の起動) を行わせない
#   - `git log` の stderr は捨てる。`git log` が 0 以外で終わった場合 (下の時間の上限による
#     打ち切りを含む) と、<log> が 0 バイトの場合は gitleaks を起動せず exit code 2 にする。
#     <log> が空でも、canary の行だけの入力は「検出なし」になるため、検査できていない入力を
#     ここで止める
#   - <log> を書き出す前に、範囲の commit の `encoding` header を
#     `git <先頭の option> log --no-show-signature --format=%e <base の OID>..<head の OID>` で
#     1 行ずつ書き出し (時間の上限の中で行う)、空 (header が無い commit。git は UTF-8 と
#     みなす) か `UTF-8` / `utf8` (大文字小文字を区別しない) 以外の行があれば exit code 2 に
#     する。git は message を header の encoding から `--encoding` の encoding に変換して
#     書き出すため、たとえば header が `IBM037` の commit では、message のバイト列にある token が
#     変換で別の文字列になり、入力から消える。この `git log` が失敗した場合も exit code 2
#   - <入力> が 100,000 バイトを超える場合は gitleaks を起動せず exit code 2 にする。gitleaks
#     stdin は最初に 100,000 バイトを読み、それを超える入力は分割して検査することがあり、分割の
#     境界をまたぐ token を検出しない (stdin が file のときは、空行が見つかるまで最大 25,000
#     バイトを読み足してから分割するため、空行を含まない入力は 125,000 バイトの位置で
#     分割される。gitleaks 8.30.1 で、この位置をまたぐ token を見逃すことを確認した)。lib は
#     stdin に通常の file を渡すため、100,000 バイト以下の入力は 1 回で読まれ、分割は起きない。
#     大きさは `wc -c` で測る
#   - canary は、gitleaks の既定の `github-pat` rule に当たる偽 token (`ghp_` + 英数字 36 文字。
#     英数字は検査ごとに `/dev/urandom` から作る)。代入の形にせず token だけを 1 行に置くため、
#     既定の rule では `github-pat` の 1 件だけが当たる。`github-pat` rule は値の entropy が 3 を
#     超えることも求める。ランダムな 36 文字なら通常は超え、超えなかった場合は canary が検出
#     されずに exit code 2 になる。canary を置くのは、gitleaks が入力の形式を先頭の内容で判定し、
#     binary の形式 (tar 等) と判定すると何も検査せずに終了コード 0 で終わるため (形式の判定は
#     改行をまだ 1 つも読んでいない間だけ行われ、canary の行の改行があるため先頭の chunk だけが
#     判定される)。たとえば入力の offset 257 に tar の magic
#     (`ustar` + NUL + `00`) があると tar と判定される。<log> の先頭は固定の `commit <OID>` の
#     header だが、その後ろの commit message や文書の内容 (`--text` で NUL もそのまま出る) で
#     offset 257 を細工できるため、終了コード 0 だけでは入力を検査したことにならない。canary が
#     検出されたことで、入力を実際に検査したことを確かめる
#   - 検出なしとするのは、終了コード 3・stderr が空・report がバイト単位で `github-pat 1` +
#     改行 (canary の 1 件だけ) のすべてを満たすときに限る。それ以外 (終了コード 0 = canary も
#     検出されなかった、1 = 致命的なエラー、時間の上限による打ち切り、report の不一致 = canary
#     以外の候補を検出した) はすべて exit code 2 にする。`--exit-code 3` は、検出 (3) と
#     致命的なエラー (設定の構文誤り等で 1) を終了コードで見分けるために付ける。
#     `--log-level error` では正常な走査は stderr に何も出さないため、stderr に何か出ていれば
#     エラーが起きたものとして扱う
#   - `--report-format template` は明示する。`--report-format` が無いと gitleaks は report の
#     形式を `--report-path` の拡張子から決めようとし、拡張子の無い <report> では致命的なエラー
#     (終了コード 1) で終わる
#   - `git log` と gitleaks は合わせて 20 秒で打ち切り、レビュー必須 (戻り値 2) として扱う。
#     範囲の判定 (10 秒) と gate 自身の処理と合わせて、gate の hook が timeout する前に終える
#     ための値 (「範囲の判定」を参照)。期限は `git log` の前に bash の `SECONDS` で 1 つ決め、
#     gitleaks には起動時点の残り秒数を `--timeout` と外側の打ち切り
#     (_text_paths_run_with_time_limit) の両方に渡す。gitleaks がこの指定どおりに終わらない
#     場合に備えて、外側でも打ち切る。残りが 1 秒未満なら gitleaks を起動せず 2 にする
#   - gitleaks の stdin には <入力> を子 process の中で redirect して渡す
#     (_text_paths_exec_in_dir_with_input)。_text_paths_run_with_time_limit が background で
#     起動する command の stdin は `/dev/null` になるため
#   - `--ignore-gitleaks-allow` は、文書中に `gitleaks:allow` コメントを書くだけで検出を
#     抑止できないようにするために付ける (テキスト系文書だけの変更は、レビュー無しで
#     そのコメントを書き込める)
#   - stdout は捨て、stderr は一時ディレクトリのファイルに受けて空かどうかだけを見る。report
#     には template で rule と行番号だけを書かせる。いずれも、検出した認証情報の内容を hook や
#     wrapper の出力に流さないため
#
# ### 設定と ignore の読み方
#
# gitleaks の設定 (`.gitleaks.toml`) は、範囲の起点 (解決済みの base OID) の tree から読み、
# repo の working tree からは読ませない。working tree の未追跡のファイルを読ませると、push gate
# の clean 検査は未追跡ファイルを見ず、merge gate には clean 検査が無いため、未追跡の設定
# ファイルを置くだけで検出を抑止できてしまう。base の tree は、テキスト系文書だけの変更では
# 書き換えられない (`.gitleaks.toml` はテキスト系文書ではない):
#
#   - 利用者の環境変数 `GITLEAKS_CONFIG` か `GITLEAKS_CONFIG_TOML` が空でない場合は、
#     利用者自身の設定として尊重し、`--config` を渡さない
#   - それ以外の場合、base の tree に `.gitleaks.toml` があればその内容を一時ファイル
#     (cwd にする空のディレクトリの外) に取り出して `--config` に渡す。無ければ `[extend]` と
#     `useDefault = true` だけを書いた一時ファイルを渡す (gitleaks の既定のルールだけを使う)
#   - 「無い」と「読めない」は分ける。entry の有無は `git ls-tree --full-tree -z` で確かめ
#     (target_cwd が subdirectory でも tree の root からの path で探す)、entry が無いときだけ
#     既定のルールを使う。entry があっても通常ファイル (`100644` / `100755`) でない (symlink・
#     ディレクトリ・submodule) か、blob を読めない (object が手元に無い等) 場合と、tree を
#     読めない場合は exit code 2 にする。既定のルールに切り替えると、base の設定にある独自の
#     rule だけが当たる値を見逃すため
#   - どの設定でも、既定の `github-pat` rule を含まない場合 (既定のルールを extend しない等) や、
#     canary を検出から外す場合 (allowlist・`disabledRules` 等) は、canary が検出されないため
#     exit code 2 になり、skip しない
#   - 実際に使う設定 (`--config` に渡す一時ファイル、`GITLEAKS_CONFIG` が指すファイル、
#     `GITLEAKS_CONFIG_TOML` の値。環境変数が両方とも空でなければ両方) のどこかに key `path` の
#     代入 (`path =`) があれば、gitleaks を起動せず exit code 2 にする (時間の上限の期限を決める
#     前に確かめる)。gitleaks は path を持たない入力では空文字列を path として rule の `path` の
#     正規表現と照合するため、path を条件にした rule (`[[rules]]` の `path`) は、その正規表現が
#     空文字列に当たらない限り働かず、その rule だけが当たる値を検出できない。`[extend]` の
#     `path` で別の設定を読み込む設定も同じ扱いになる (読み込んだ先の rule を確かめられない
#     ため)。照合は行ごとの正規表現 (`LC_ALL=C grep -Ei` で
#     `(^|[[:space:]{,.])["']?path["']?[[:space:]]*=`) で行う。gitleaks は設定の key の大文字
#     小文字を区別せずに読むため、照合も区別しない (`Path =` にも当たる)。引用符で囲んだ key
#     (`"path" =`)・inline table の中 (`{ id = "x", path = "..." }`)・dotted key
#     (`extend.path =`。`[extend]` の `path` と同じ意味) にも当たり、allowlist の `paths =` には
#     当たらない。TOML の二重引用符の key は escape を使える (`"p\u0061th"` は `path` になる)
#     ため、backslash を含む二重引用符の key への代入 (`"[^"]*\\[^"]*"[[:space:]]*=`) も同じ
#     扱いにする。comment の中の `path =` にも当たる (レビュー必須の側)。
#     `GITLEAKS_CONFIG` のファイルは gitleaks の cwd を移す前の cwd を
#     基準に読み、読めなければ exit code 2 にする。base の tree に `.gitleaks.toml` が無いときの
#     一時ファイル (既定のルールだけ) はこの key を持たない。既定のルールにも path を条件にした
#     rule はあるが (8.30.1 では 5 件)、`.p12`・`.pfx` の鍵ファイル、`.php`・`.tf`・`.hcl`・
#     `.yaml`・`.yml`・`nuget.config` を対象にしたもので、テキスト系文書の path には当たらない
#   - ignore (`.gitleaksignore`) は使わない。base の tree にあっても取り出さない。gitleaks は
#     `--gitleaks-ignore-path` の指定先に加えて cwd の `.gitleaksignore` も読むため、cwd と
#     `--gitleaks-ignore-path` の両方を同じ空の一時ディレクトリにする (cwd が repo の working
#     tree だと、未追跡の `.gitleaksignore` が読まれる)
#   - 一時ファイルと一時ディレクトリは `mktemp` で作り、関数を抜ける前に消す (source する
#     script の trap には手を加えない)。作成や取り出しに失敗した場合は exit code 2 にする
#
# gitleaks が PATH に無い環境では検査せず、1-4 を満たせば 0 を返す。
#
# ## push の予測 (is_text_only_push_in)
#
# push gate と push 前の codex review wrapper が使う。remote-tracking ref
# `refs/remotes/origin/<base>` があり、index と working tree が clean
# (`git diff-index --quiet --cached HEAD` と `git diff-files --quiet` がともに成功) で、
# かつ `refs/remotes/origin/<base>..HEAD` が is_text_only_range_in を満たすときだけ成立する。
# base を完全な名前で渡すのは、短い名前 `origin/<base>` は同名の ref (`refs/origin/<base>`・
# tag `origin/<base>`・branch `origin/<base>`) があると remote-tracking ref より先にそちらに
# 解決され、その ref をコードの commit に向けるだけで範囲からコードの commit を外せるため。
# exit code は is_text_only_range_in と同じ (remote-tracking ref が無い、または index / working
# tree が clean でなければ 1。範囲の判定の 2 はそのまま返す)。
#
# ## 利用側
#
#   - push gate (block-pre-push.sh / block-pre-push-codex.sh): 空 push の skip の直後に
#     is_text_only_push_in で判定し、0 ならマーカーを要求せずに通す
#   - push 前の codex review wrapper: 空 push の判定の直後に is_text_only_push_in で判定し、
#     0 なら codex review を実行せず、pending attestation も書かずに終了する
#   - merge gate (block-pre-merge.sh): gh が返す PR の base commit (`baseRefOid`) と head SHA
#     を is_text_only_range_in に渡し、0 ならレビュー記録を照合せずに通す
#   - merge 前の codex review wrapper: PR の base commit (`baseRefOid`) と HEAD を
#     is_text_only_range_in に渡し、0 なら codex review を実行せず、pending attestation も
#     書かずに終了する
#
# gate は 2 のとき、deny の理由文に秘匿情報の検査でレビューを要求した旨の 1 文を加える
# (認証情報の内容や gitleaks の出力は含めない)。wrapper は 2 のとき通常どおり codex review を
# 実行する。

# is_text_document_path <repo 相対 path>
# exit code: 0 = テキスト系文書、1 = それ以外。定義はファイルヘッダの「テキスト系文書の
# 定義」を参照。
is_text_document_path() {
  local path="$1"
  local basename="${path##*/}"
  local directories=""
  case "$path" in
    */*) directories="${path%/*}" ;;
  esac

  # filesystem が ASCII だけの名前と同一視しうる非 ASCII 文字を含まないこと。UTF-8 の
  # バイト列で照合する。1 つ目の case は大文字小文字の変換や畳み込みで ASCII になる文字
  # (順に İ ı ſ ß ẞ K と、合字 U+FB00〜U+FB06)、2 つ目は HFS+ が無視する文字 (U+200C〜U+200F・
  # U+202A〜U+202E・U+206A〜U+206F・U+FEFF)。
  case "$path" in
    *$'\xc4\xb0'* | *$'\xc4\xb1'* | *$'\xc5\xbf'* | *$'\xc3\x9f'* | \
      *$'\xe1\xba\x9e'* | *$'\xe2\x84\xaa'* | \
      *$'\xef\xac\x80'* | *$'\xef\xac\x81'* | *$'\xef\xac\x82'* | *$'\xef\xac\x83'* | \
      *$'\xef\xac\x84'* | *$'\xef\xac\x85'* | *$'\xef\xac\x86'*)
      return 1
      ;;
  esac
  case "$path" in
    *$'\xe2\x80\x8c'* | *$'\xe2\x80\x8d'* | *$'\xe2\x80\x8e'* | *$'\xe2\x80\x8f'* | \
      *$'\xe2\x80\xaa'* | *$'\xe2\x80\xab'* | *$'\xe2\x80\xac'* | *$'\xe2\x80\xad'* | \
      *$'\xe2\x80\xae'* | \
      *$'\xe2\x81\xaa'* | *$'\xe2\x81\xab'* | *$'\xe2\x81\xac'* | *$'\xe2\x81\xad'* | \
      *$'\xe2\x81\xae'* | *$'\xe2\x81\xaf'* | \
      *$'\xef\xbb\xbf'*)
      return 1
      ;;
  esac

  # 拡張子 (basename の最後の `.` より後ろ) が対象の 4 つのいずれかであること。
  case "$basename" in
    *.*) ;;
    *) return 1 ;;
  esac
  case "${basename##*.}" in
    [mM][dD] | \
      [mM][aA][rR][kK][dD][oO][wW][nN] | \
      [rR][sS][tT] | \
      [aA][dD][oO][cC]) ;;
    *) return 1 ;;
  esac

  # agent が指示として読み込む basename。
  case "$basename" in
    [cC][lL][aA][uU][dD][eE].[mM][dD] | \
      [cC][lL][aA][uU][dD][eE].[lL][oO][cC][aA][lL].[mM][dD] | \
      [aA][gG][eE][nN][tT][sS].[mM][dD] | \
      [aA][gG][eE][nN][tT][sS].[oO][vV][eE][rR][rR][iI][dD][eE].[mM][dD] | \
      [gG][eE][mM][iI][nN][iI].[mM][dD] | \
      [cC][oO][pP][iI][lL][oO][tT]-[iI][nN][sS][tT][rR][uU][cC][tT][iI][oO][nN][sS].[mM][dD])
      return 1
      ;;
  esac

  # ディレクトリ要素。前後に `/` を付けた文字列に対して照合し、要素の一部だけが一致する
  # 名前 (`agentsx/` 等) を除外しない。
  if [ -n "$directories" ]; then
    case "/${directories}/" in
      */[aA][gG][eE][nN][tT][sS]/* | \
        */[cC][oO][mM][mM][aA][nN][dD][sS]/* | \
        */[sS][kK][iI][lL][lL][sS]/* | \
        */[rR][uU][lL][eE][sS]/* | \
        */[oO][uU][tT][pP][uU][tT]-[sS][tT][yY][lL][eE][sS]/* | \
        */.[cC][lL][aA][uU][dD][eE]/* | \
        */.[aA][gG][eE][nN][tT][sS]/* | \
        */.[cC][uU][rR][sS][oO][rR]/* | \
        */.[gG][eE][mM][iI][nN][iI]/* | \
        */.[cC][oO][dD][eE][xX]/* | \
        */.[wW][iI][nN][dD][sS][uU][rR][fF]/* | \
        */.[cC][lL][iI][nN][eE][rR][uU][lL][eE][sS]/* | \
        */.[rR][oO][oO]/* | \
        */.[kK][iI][rR][oO]/* | \
        */.[cC][oO][nN][tT][iI][nN][uU][eE]/* | \
        */.[jJ][uU][nN][iI][eE]/* | \
        */.[aA][mM][aA][zZ][oO][nN][qQ]/* | \
        */[hH][oO][oO][kK][sS]/[pP][rR][oO][mM][pP][tT][sS]/* | \
        */.[gG][iI][tT][hH][uU][bB]/[iI][nN][sS][tT][rR][uU][cC][tT][iI][oO][nN][sS]/* | \
        */.[gG][iI][tT][hH][uU][bB]/[pP][rR][oO][mM][pP][tT][sS]/* | \
        */.[gG][iI][tT][hH][uU][bB]/[cC][hH][aA][tT][mM][oO][dD][eE][sS]/*)
        return 1
        ;;
    esac
  fi

  return 0
}

# _text_paths_raw_entries_are_text_only <deadline>
# 標準入力の `git diff-tree -r -z --no-renames --no-commit-id` の出力 (エントリごとに
# `:<変更前 mode> <変更後 mode> <変更前 blob> <変更後 blob> <status>` と path の 2 フィールドを
# NUL で区切る) を読み、全エントリが範囲の判定の条件 3 を満たすとき 0 を返す。想定外の
# 形式 (先頭が `:` でないフィールド、path の欠けたエントリ、NUL で終わらない末尾) は 1 に倒す。
# <deadline> は bash の `SECONDS` の値で表した判定の期限で、各エントリを読む前に確かめ、
# 期限に達していれば 1 を返す。
_text_paths_raw_entries_are_text_only() {
  local deadline="$1"
  local metadata=""
  local path old_mode new_mode
  while IFS= read -r -d '' metadata; do
    [ "$SECONDS" -lt "$deadline" ] || return 1
    IFS= read -r -d '' path || return 1
    case "$metadata" in
      :*) ;;
      *) return 1 ;;
    esac
    metadata="${metadata#:}"
    old_mode="${metadata%% *}"
    metadata="${metadata#* }"
    new_mode="${metadata%% *}"
    case "$old_mode" in
      000000 | 100644) ;;
      *) return 1 ;;
    esac
    case "$new_mode" in
      000000 | 100644) ;;
      *) return 1 ;;
    esac
    is_text_document_path "$path" || return 1
    metadata=""
  done
  # 最後の read が NUL で終わらないフィールドを読んで止まった場合は、出力が途中で切れている。
  [ -z "$metadata" ] || return 1
  return 0
}

# _text_paths_diff_is_text_only <target_cwd> <from_oid> <to_oid> <deadline>
# from と to の raw 差分の全エントリが範囲の判定の条件 3 を満たすとき 0 を返す。git の失敗も
# 解析の失敗も、<deadline> (bash の `SECONDS` の値) に達した場合も 1 に倒す (pipeline の
# 両側の終了状態を PIPESTATUS で確かめる)。
_text_paths_diff_is_text_only() {
  local target_cwd="$1"
  local from_oid="$2"
  local to_oid="$3"
  local deadline="$4"
  [ "$SECONDS" -lt "$deadline" ] || return 1
  local -a git_prefix=(--no-replace-objects -c core.commitGraph=false)
  if [ -n "$target_cwd" ]; then
    git_prefix+=(-C "$target_cwd")
  fi
  local -a statuses=()
  git "${git_prefix[@]}" diff-tree -r -z --no-renames --no-commit-id \
    --ignore-submodules=none "$from_oid" "$to_oid" </dev/null 2>/dev/null \
    | _text_paths_raw_entries_are_text_only "$deadline"
  statuses=("${PIPESTATUS[@]}")
  [ "${statuses[0]}" = "0" ] && [ "${statuses[1]}" = "0" ]
}

# _text_paths_extract_base_file <target_cwd> <base_oid> <path> <destination>
# base の tree の root からの <path> に entry があり、それが通常ファイル (`100644` / `100755`)
# の blob なら、その内容を <destination> に書き出す。
# exit code: 0 = 書き出した、1 = base の tree に entry が無い、2 = tree を読めない・entry が
# 通常ファイルでない (symlink・ディレクトリ・submodule)・blob を読めない (object が手元に無い
# 等)・書き出せなかった。
_text_paths_extract_base_file() {
  local target_cwd="$1"
  local base_oid="$2"
  local path="$3"
  local destination="$4"
  local -a git_prefix=(--no-replace-objects -c core.commitGraph=false)
  if [ -n "$target_cwd" ]; then
    git_prefix+=(-C "$target_cwd")
  fi
  # entry は `<mode> <type> <oid>` + TAB + `<path>` + NUL の形で出る。`--full-tree` は、
  # target_cwd が subdirectory でも <path> を tree の root から探すために付ける。NUL は
  # command substitution で落とすと bash が警告を出すため tr で改行に変え、pipe の途中の
  # git の失敗は pipefail で拾う。
  local entry
  entry=$(
    set -o pipefail
    git "${git_prefix[@]}" ls-tree --full-tree -z "$base_oid" -- "$path" 2>/dev/null \
      | tr '\0' '\n'
  ) || return 2
  [ -n "$entry" ] || return 1
  case "$entry" in
    *$'\n'*) return 2 ;;
  esac
  local mode="${entry%% *}"
  local rest="${entry#* }"
  local object_type="${rest%% *}"
  rest="${rest#* }"
  local oid="${rest%%$'\t'*}"
  case "$mode" in
    100644 | 100755) ;;
    *) return 2 ;;
  esac
  [ "$object_type" = "blob" ] || return 2
  case "$oid" in
    "" | *[!0123456789abcdef]*) return 2 ;;
  esac
  git "${git_prefix[@]}" cat-file blob "$oid" >"$destination" 2>/dev/null || return 2
  return 0
}

# _text_paths_run_with_time_limit <seconds> <command> [<arg>...]
# command を background で起動し、<seconds> 秒以内に終われば command の終了コードを返す。
# 終わらなければ command を KILL して 124 を返す。macOS には `timeout` コマンドが無いため、
# 0.1 秒ごとに `kill -0` で終了を確かめる。経過時間は bash の `SECONDS` で測る (`sleep` の
# 回数で数えると、`sleep` を起動する時間の分だけ遅れる)。`SECONDS` は整数秒のため、打ち切りは
# <seconds> 秒より最大 1 秒早まりうる。stdout と stderr の redirect は呼び出し側で関数呼び出しに
# 付ける (command はそれを引き継ぐ)。stdin はこの方法では届かない: job control が無効な shell
# では background の command の stdin は `/dev/null` になる。command に入力を渡すには、子の中で
# redirect する (_text_paths_exec_in_dir_with_input を command にする)。
_text_paths_run_with_time_limit() {
  local limit_seconds="$1"
  shift
  "$@" &
  local pid=$!
  local deadline=$((SECONDS + limit_seconds))
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      kill -KILL "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      return 124
    fi
    sleep 0.1
  done
  wait "$pid"
}

# _text_paths_exec_in_dir_with_input <dir> <input> <command> [<arg>...]
# cwd を <dir> に移し、stdin を <input> にして command を exec する。
# _text_paths_run_with_time_limit が background で起動する子の中でだけ呼ぶ (呼び出した shell
# 自身の cwd と stdin を変えないため。exec で子の process が command に置き換わる)。本体で
# subshell を使わないのは、_text_paths_run_with_time_limit の `$!` が指す process (期限切れで
# KILL する相手) を command そのものにするため。
_text_paths_exec_in_dir_with_input() {
  local dir="$1"
  local input="$2"
  shift 2
  cd "$dir" || return 1
  exec "$@" <"$input"
}

# _text_paths_config_has_path_key <file>
# gitleaks の設定ファイル (TOML) に key `path` の代入があるかを確かめ、当たる行があれば 0、
# 無ければ 1、読めなければ 2 (grep の exit code) を返す。次のどちらかに当たる行を「key `path`
# の代入がある」とみなす (理由はファイルヘッダの「設定と ignore の読み方」を参照):
#   - `path =` (大文字小文字を区別しない。引用符で囲んだ key・inline table の中・dotted key を
#     含み、allowlist の `paths =` は含まない)
#   - backslash を含む二重引用符の key への代入 (escape を展開した後の key を確かめられない
#     ため)
_text_paths_config_has_path_key() {
  local key_pattern="(^|[[:space:]{,.])[\"']?path[\"']?[[:space:]]*="
  local escaped_key_pattern="\"[^\"]*\\\\[^\"]*\"[[:space:]]*="
  local grep_status=0
  LC_ALL=C grep -Eqi -e "$key_pattern" -e "$escaped_key_pattern" -- "$1" 2>/dev/null \
    || grep_status=$?
  return "$grep_status"
}

# _text_paths_run_gitleaks <target_cwd> <base_oid> <head_oid> <work_dir>
# <work_dir> (呼び出し側が作り、消す一時ディレクトリ) に gitleaks の設定・canary を先頭に置いた
# 入力・report の template・空の cwd を用意して gitleaks を起動する。gitleaks が canary だけを
# 検出したとき (終了コード 3・stderr が空・report が canary の 1 行だけ) に限って 0 を返し、
# それ以外の結果や、用意や入力の書き出しに失敗した場合は 0 以外を返す。起動の形と設定の
# 決め方はファイルヘッダの「秘匿情報の検査」を参照。
_text_paths_run_gitleaks() {
  local target_cwd="$1"
  local base_oid="$2"
  local head_oid="$3"
  local work_dir="$4"
  local extract_status
  local -a git_prefix=(--no-replace-objects -c core.commitGraph=false)
  if [ -n "$target_cwd" ]; then
    git_prefix+=(-C "$target_cwd")
  fi

  local -a config_args=()
  # gitleaks が実際に読む設定のファイル (`path =` の key の検査に使う)。
  local -a used_config_files=()
  if [ -z "${GITLEAKS_CONFIG:-}" ] && [ -z "${GITLEAKS_CONFIG_TOML:-}" ]; then
    local config_path="${work_dir}/gitleaks.toml"
    extract_status=0
    _text_paths_extract_base_file "$target_cwd" "$base_oid" .gitleaks.toml "$config_path" \
      || extract_status=$?
    case "$extract_status" in
      0) ;;
      1) printf '[extend]\nuseDefault = true\n' >"$config_path" 2>/dev/null || return 1 ;;
      *) return 1 ;;
    esac
    config_args=(--config "$config_path")
    used_config_files+=("$config_path")
  fi
  # 利用者の環境変数の設定。`GITLEAKS_CONFIG` のファイルは、gitleaks の cwd を移す前のこの
  # shell の cwd を基準に読む。読めなければ検査しない。
  if [ -n "${GITLEAKS_CONFIG:-}" ]; then
    if [ ! -f "$GITLEAKS_CONFIG" ] || [ ! -r "$GITLEAKS_CONFIG" ]; then
      return 1
    fi
    used_config_files+=("$GITLEAKS_CONFIG")
  fi
  if [ -n "${GITLEAKS_CONFIG_TOML:-}" ]; then
    local environment_config_path="${work_dir}/gitleaks-config-toml.toml"
    printf '%s\n' "$GITLEAKS_CONFIG_TOML" >"$environment_config_path" 2>/dev/null || return 1
    used_config_files+=("$environment_config_path")
  fi

  # 使う設定に `path =` の key が 1 つでもあれば、gitleaks を起動せずに失敗を返す (理由は
  # ファイルヘッダの「設定と ignore の読み方」を参照)。期限を決める前に行う。
  local used_config_file key_status
  for used_config_file in "${used_config_files[@]}"; do
    key_status=0
    _text_paths_config_has_path_key "$used_config_file" || key_status=$?
    [ "$key_status" -eq 1 ] || return 1
  done

  # gitleaks の cwd と ignore path を兼ねる空のディレクトリ。設定・入力・template・report・
  # stderr のファイルはこの外 (<work_dir> の直下) に置き、起動時点で中身を空に保つ。
  local scan_dir="${work_dir}/scan"
  mkdir "$scan_dir" 2>/dev/null || return 1

  # canary は入力の 1 行目に置く偽 token (gitleaks の既定の `github-pat` rule に当たる形)。
  # 検査ごとに `/dev/urandom` から作る。読む量を先に決めておき (512 バイトから英数字を取り出すと
  # 平均 124 文字残る)、`tr` が入力の終わりを待ち続けないようにする。
  local canary_body
  canary_body=$(head -c 512 /dev/urandom 2>/dev/null | LC_ALL=C tr -dc 'A-Za-z0-9' 2>/dev/null)
  canary_body="${canary_body:0:36}"
  [ "${#canary_body}" -eq 36 ] || return 1
  local canary="ghp_${canary_body}"

  # report の template (検出ごとに rule と行番号だけを 1 行に書く) と、canary だけを検出した
  # ときの report の期待値。
  local template_path="${work_dir}/gitleaks-report.tmpl"
  local expected_report_path="${work_dir}/gitleaks-report.expected"
  local report_path="${work_dir}/gitleaks-report"
  printf '{{ range . }}{{ .RuleID }} {{ .StartLine }}\n{{ end }}' >"$template_path" \
    2>/dev/null || return 1
  printf 'github-pat 1\n' >"$expected_report_path" 2>/dev/null || return 1

  # `git log` と gitleaks で 1 つの期限 (bash の `SECONDS` の値) を共有する。
  local deadline=$((SECONDS + 20))

  # 範囲の commit の `encoding` header を 1 行ずつ書き出し、UTF-8 以外があれば検査しない
  # (理由はファイルヘッダの「秘匿情報の検査」を参照)。空の行は header の無い commit。
  local encoding_path="${work_dir}/gitleaks.encodings"
  local encoding_status=0
  _text_paths_run_with_time_limit "$((deadline - SECONDS))" \
    git "${git_prefix[@]}" log --no-show-signature --format=%e "${base_oid}..${head_oid}" \
    </dev/null >"$encoding_path" 2>/dev/null || encoding_status=$?
  [ "$encoding_status" -eq 0 ] || return "$encoding_status"
  local commit_encoding
  while IFS= read -r commit_encoding || [ -n "$commit_encoding" ]; do
    case "$commit_encoding" in
      "" | [uU][tT][fF]-8 | [uU][tT][fF]8) ;;
      *) return 1 ;;
    esac
  done <"$encoding_path" || return 1

  # 範囲内の commit の message と差分を書き出す。stderr は捨てる。
  local log_path="${work_dir}/gitleaks.log"
  local log_status=0
  _text_paths_run_with_time_limit "$((deadline - SECONDS))" \
    git "${git_prefix[@]}" -c diff.relative=false log -p -U0 --no-textconv --no-ext-diff \
    --text --no-color --no-show-signature --encoding=UTF-8 --format='commit %H%n%n%B' \
    "${base_oid}..${head_oid}" </dev/null >"$log_path" 2>/dev/null || log_status=$?
  [ "$log_status" -eq 0 ] || return "$log_status"
  # 空の出力は検査できていない入力として扱う。
  [ -s "$log_path" ] || return 1

  # gitleaks に渡す入力は、canary の行 + `git log` の出力。
  local input_path="${work_dir}/gitleaks.input"
  printf '%s\n' "$canary" >"$input_path" 2>/dev/null || return 1
  cat "$log_path" >>"$input_path" 2>/dev/null || return 1

  # 入力の大きさの上限 (バイト)。gitleaks stdin は最初に 100,000 バイトを読み、それを超える
  # 入力は分割して検査することがあり、分割の境界をまたぐ token を検出しない。通常の file から
  # 読む 100,000 バイト以下の入力は分割されない (理由はファイルヘッダの「秘匿情報の検査」を参照)。
  local max_input_bytes=100000
  local input_bytes
  input_bytes=$(wc -c <"$input_path" 2>/dev/null) || return 1
  # macOS の `wc` は数値の前に空白を付けるため、数字だけを残す。
  input_bytes="${input_bytes//[!0123456789]/}"
  [ -n "$input_bytes" ] || return 1
  [ "$input_bytes" -le "$max_input_bytes" ] || return 1

  # gitleaks には残り秒数を渡す。1 秒未満なら起動しない。
  local remaining=$((deadline - SECONDS))
  [ "$remaining" -ge 1 ] || return 124

  # stderr は cwd の外のファイルに受け、空かどうかだけを見る (内容は出力しない)。
  local stderr_path="${work_dir}/gitleaks.stderr"
  local gitleaks_status=0
  _text_paths_run_with_time_limit "$remaining" \
    _text_paths_exec_in_dir_with_input "$scan_dir" "$input_path" \
    gitleaks stdin --no-banner --redact --ignore-gitleaks-allow --log-level error \
    --exit-code 3 --timeout "$remaining" --report-format template \
    --report-template "$template_path" --report-path "$report_path" \
    "${config_args[@]}" --gitleaks-ignore-path "$scan_dir" \
    </dev/null >/dev/null 2>"$stderr_path" || gitleaks_status=$?
  # 検出なしとするのは、canary だけを検出したとき (終了コード 3・stderr が空・report が
  # canary の 1 行だけ) に限る。
  [ "$gitleaks_status" -eq 3 ] || return 1
  [ ! -s "$stderr_path" ] || return 1
  cmp -s "$expected_report_path" "$report_path"
}

# _text_paths_secrets_scan_passes <target_cwd> <base_oid> <head_oid>
# `<base_oid>..<head_oid>` の commit の message と差分を gitleaks で検査し、gitleaks が入力の
# 先頭に置いた canary だけを検出したとき (検査を実行し、秘匿情報の候補が無かったとき) に限って
# 0 を返す。gitleaks が PATH に無い場合は検査せずに 0 を返す。起動の形、設定と ignore の
# 決め方、出力を捨てる理由はファイルヘッダの「秘匿情報の検査」を参照。
_text_paths_secrets_scan_passes() {
  local target_cwd="$1"
  local base_oid="$2"
  local head_oid="$3"
  command -v gitleaks >/dev/null 2>&1 || return 0

  # 一時ディレクトリはこの関数の中で作って消す (source する script の trap に手を加えない)。
  local work_dir
  work_dir=$(mktemp -d "${TMPDIR:-/tmp}/text-paths-gitleaks.XXXXXX" 2>/dev/null) || return 1
  if [ -z "$work_dir" ] || [ ! -d "$work_dir" ]; then
    return 1
  fi
  local scan_status=0
  _text_paths_run_gitleaks "$target_cwd" "$base_oid" "$head_oid" "$work_dir" \
    || scan_status=$?
  rm -rf "$work_dir" 2>/dev/null
  return "$scan_status"
}

# is_text_only_range_in <target_cwd> <base_rev> <head_rev>
# exit code: 0 = 範囲内の変更がテキスト系文書だけで秘匿情報の検査も通った、1 = テキスト系
# 文書だけではない (git の失敗を含む)、2 = テキスト系文書だけだが秘匿情報の検査で止めた。
# 判定条件はファイルヘッダの「範囲の判定」と「秘匿情報の検査」を参照。target_cwd が空文字
# なら現在の cwd で、非空なら `git -C <target_cwd>` で git を実行する (diff-hash.sh の `*_in`
# 関数と同じ扱い)。
is_text_only_range_in() {
  local target_cwd="$1"
  local base_rev="$2"
  local head_rev="$3"
  # 手元に無い object を remote から取りに行かせない (理由はファイルヘッダの「範囲の判定」を
  # 参照)。`local -x` は関数の中から起動する git にだけ渡り、関数を抜けると元に戻る。
  local -x GIT_NO_LAZY_FETCH=1
  local -a git_prefix=(--no-replace-objects -c core.commitGraph=false)
  if [ -n "$target_cwd" ]; then
    git_prefix+=(-C "$target_cwd")
  fi
  # 判定 (1-4) の期限を bash の `SECONDS` の値で表す (理由はファイルヘッダの「範囲の判定」を
  # 参照)。
  local deadline=$((SECONDS + 10))

  # revision は git の引数になるため、空や `-` 始まり (option として解釈される値) は拒否する。
  case "$base_rev" in
    "" | -*) return 1 ;;
  esac
  case "$head_rev" in
    "" | -*) return 1 ;;
  esac

  # graft ファイルがあれば判定しない (理由はファイルヘッダの「範囲の判定」を参照)。
  # `--git-path info/grafts` は環境変数 `GIT_GRAFT_FILE` も考慮した path を返す。相対 path は
  # git を実行したディレクトリ (target_cwd か現在の cwd) からの path なので、target_cwd を補う。
  local graft_path
  graft_path=$(git "${git_prefix[@]}" rev-parse --git-path info/grafts 2>/dev/null) || return 1
  [ -n "$graft_path" ] || return 1
  case "$graft_path" in
    /*) ;;
    *)
      if [ -n "$target_cwd" ]; then
        graft_path="${target_cwd}/${graft_path}"
      fi
      ;;
  esac
  if [ -e "$graft_path" ] || [ -L "$graft_path" ]; then
    return 1
  fi

  # 以降の git 呼び出しには解決済みの commit OID (16 進数のみ) だけを渡す。
  local base_oid head_oid
  base_oid=$(git "${git_prefix[@]}" rev-parse --verify --quiet "${base_rev}^{commit}" 2>/dev/null) || return 1
  head_oid=$(git "${git_prefix[@]}" rev-parse --verify --quiet "${head_rev}^{commit}" 2>/dev/null) || return 1
  if [ -z "$base_oid" ] || [ -z "$head_oid" ]; then
    return 1
  fi

  # 判定は 1 → 2 → 4 → 3 の順に行う (理由はファイルヘッダの「範囲の判定」を参照)。

  # 条件 1: 範囲の commit 数が 1 以上、上限以下であること。上限 + 1 まで数えたら数えるのを
  # やめる (上限を超える範囲を全部たどらない)。
  local max_commits=100
  local commit_count
  commit_count=$(git "${git_prefix[@]}" rev-list --count --max-count=$((max_commits + 1)) \
    "${base_oid}..${head_oid}" 2>/dev/null) || return 1
  case "$commit_count" in
    "" | *[!0123456789]*) return 1 ;;
  esac
  if [ "$commit_count" -eq 0 ] || [ "$commit_count" -gt "$max_commits" ]; then
    return 1
  fi

  # 条件 2: 範囲内に merge commit が無いこと。
  local merge_count
  merge_count=$(git "${git_prefix[@]}" rev-list --min-parents=2 --count "${base_oid}..${head_oid}" 2>/dev/null) || return 1
  [ "$merge_count" = "0" ] || return 1

  # 条件 4: merge-base から head までの差分 (net diff)。git の呼び出しが 1 回で済むため、
  # 各 commit の走査より先に行う。
  local merge_base_oid
  merge_base_oid=$(git "${git_prefix[@]}" merge-base "$base_oid" "$head_oid" 2>/dev/null) || return 1
  [ -n "$merge_base_oid" ] || return 1
  _text_paths_diff_is_text_only "$target_cwd" "$merge_base_oid" "$head_oid" "$deadline" \
    || return 1

  # 条件 3: 範囲内の各 commit を親とともに列挙する (`<commit> <親>...` の 1 行ずつ)。親が
  # ちょうど 1 つでない行 (親を持たない commit と merge commit) があれば不成立。
  local listing
  listing=$(git "${git_prefix[@]}" rev-list --parents "${base_oid}..${head_oid}" 2>/dev/null) || return 1
  [ -n "$listing" ] || return 1
  local commit parent extra
  # here-string の redirect が失敗する (一時ファイルを作れない等) とループ本体が 1 回も
  # 実行されないまま終わるため、その失敗も不成立として扱う。
  while IFS=' ' read -r commit parent extra; do
    if [ -z "$commit" ] || [ -z "$parent" ] || [ -n "$extra" ]; then
      return 1
    fi
    _text_paths_diff_is_text_only "$target_cwd" "$parent" "$commit" "$deadline" || return 1
  done <<<"$listing" || return 1

  # 条件 5: 秘匿情報の候補が無いこと (gitleaks がある場合のみ検査する)。
  _text_paths_secrets_scan_passes "$target_cwd" "$base_oid" "$head_oid" || return 2

  return 0
}

# is_text_only_push_in <target_cwd> <base>
# exit code: is_text_only_range_in と同じ (0 = skip してよい、1 = テキスト系文書だけではない
# または git の失敗、2 = テキスト系文書だけだが秘匿情報の検査で止めた)。判定条件はファイル
# ヘッダの「push の予測」を参照。target_cwd の扱いは is_text_only_range_in と同じ。
is_text_only_push_in() {
  local target_cwd="$1"
  local base="$2"
  # 手元に無い object を remote から取りに行かせない (is_text_only_range_in と同じ)。
  local -x GIT_NO_LAZY_FETCH=1
  local -a git_prefix=(--no-replace-objects -c core.commitGraph=false)
  if [ -n "$target_cwd" ]; then
    git_prefix+=(-C "$target_cwd")
  fi
  [ -n "$base" ] || return 1

  # base は remote-tracking ref の完全な名前で指定する (理由はファイルヘッダの「push の
  # 予測」を参照)。ref が無ければ判定しない。
  local base_ref="refs/remotes/origin/${base}"
  git "${git_prefix[@]}" show-ref --verify --quiet "$base_ref" 2>/dev/null || return 1

  # index / working tree が clean であること。plumbing (textconv / external driver 非適用) で
  # 判定する。
  git "${git_prefix[@]}" diff-index --quiet --cached HEAD 2>/dev/null || return 1
  git "${git_prefix[@]}" diff-files --quiet 2>/dev/null || return 1

  is_text_only_range_in "$target_cwd" "$base_ref" HEAD
}

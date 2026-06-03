# pre-push-review プラグイン

`git push` を実行する前に `/simplify` → `/code-review` → `/codex:review --wait --scope branch` → `pre-push-review:security-reviewer` subagent (self-contained に branch 全差分のセキュリティレビューを実行; 詳細は下記 [Agents](#agents) を参照) を必ず実行させ、未レビューな commit が remote に到達するのを構造的にブロックするプラグインです。`/simplify` (cleanup を**適用** = コードを編集する bundled skill) はコード変更を伴うため先に走らせ、`/code-review` (read-only の correctness バグ検出) / `/codex:review` はその後の最終形をバグ観点でレビューし、 security レビューは同じ最終形を security 観点でレビューします。`/simplify` (Anthropic cleanup) → `/code-review` (Anthropic バグ検出) → `/codex:review` (OpenAI バグ検出) → security と、 **Anthropic と OpenAI の独立した 2 つのバグレビュー** + cleanup + security を重ねる defense-in-depth 構成です。修正により branch 全差分が変わると **レビューマーカーが自動的に失効** するため、Claude は各レビューを再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。Claude が「修正不要」と判断した時点で再レビュー後に push に進みます。Claude が「人間判断を仰ぐべき」と判断した場合のみユーザーへエスカレートします。

> **`/simplify` と `/code-review` は別 skill です**: Claude Code の bundled skill は履歴上 2 度反転しています。≤v2.1.145 では `/simplify` が cleanup-and-fix (編集)、v2.1.147 で `/code-review` に「リネーム」されましたが実態は read-only バグ検出器への**挙動変更**で `/simplify` は一旦消滅、v2.1.154 で `/simplify` が cleanup-only (編集) skill として再導入され両者が併存しました。本プラグイン v1.0.0 はこの分岐に追随し、`/simplify` (編集) と `/code-review` (read-only) を**別マーカー**として扱います。第一者 (Anthropic) レビューは Claude Code **v2.1.154+** を検出したとき `/simplify` と `/code-review` の**両方**を必須化し、旧 version / 検出不能時は fail-open でどちらか 1 本に緩めます (詳細は下記 [第一者レビューの version 依存](#第一者レビューの-version-依存))。

## バージョン

v1.0.0 (前身: `pre-commit-review` v0.4.0)

### v0.8.5 → v1.0.0 の変更点

- **Claude Code の bundled skill 分岐に追随し `/simplify` と `/code-review` を別マーカーに分離**: 一次情報 (公式 CHANGELOG + インストール済みバイナリ) で確認した通り、`/simplify` と `/code-review` は履歴上 2 度反転している。v0.7.0 は「v2.1.146 で `/simplify` が `/code-review` にリネーム = 同一 skill の改名」と仮定し両名を 1 マーカー `.claude-pre-push-code-reviewed` に conflate していたが、実態は **役割の分岐**だった (v2.1.147 で `/code-review` は read-only バグ検出器に変質し `/simplify` 消滅、v2.1.154 で `/simplify` が cleanup-only 編集 skill として再導入)。v1.0.0 は両者を別マーカー (`/simplify` → `.claude-pre-push-simplified` / `/code-review` → `.claude-pre-push-code-reviewed`) に分離する
- **push gate を 3 → 4 マーカーに拡張 (互換破壊のため major bump)**: cleanup (`/simplify`) + Anthropic バグ検出 (`/code-review`) + OpenAI バグ検出 (`/codex:review`) + security の defense-in-depth。Anthropic と OpenAI の **独立した 2 つのバグレビュー**でモデル別の盲点を冗長化する
- **第一者レビューの version 依存を fail-open で実装** (`lib/first-party-review.sh`): `/simplify` と `/code-review` が併存するのは Claude Code v2.1.154+ のみ。それ未満では片方の skill が存在せず、両方必須にすると永久 deny になる。そこで env 変数 (`CLAUDE_CODE_VERSION` / `AI_AGENT` / `CLAUDE_CODE_EXECPATH`) から version を fork なしで読み、**v2.1.154+ を肯定的に確認できたときだけ「両方必須」に昇格**、それ以外 (旧 version / 検出不能) はすべて fail-open で「どちらか 1 本で可」に降格する。version 検出は**緩める方向にのみ倒れ**、検出が壊れても永久 deny を生まない (MEMORY の prompt-hook-model-spof 教訓に整合)
- **`/simplify` の launch-time marking と編集の相互作用**: `/simplify` はコードを編集するため、launch 時点で書いた simplified マーカーは body の edit で即失効する。`/simplify` を **edits が無くなる (no-op) まで繰り返す**ことで simplified マーカーが最終差分に揃う。cleanup を先頭に置くことで、後段の read-only レビュー (`/code-review` / `/codex:review` / security) が安定した最終形を見る順序が成立する (現状 README の「編集するから先に走らせる」順序根拠を、実際に編集する `/simplify` に正しく付け替えた)
- **後方互換 / 移行**: `.claude-pre-push-code-reviewed` マーカー名は維持 (意味を read-only バグ検出に純化) するため、v0.8.5 で `/code-review` を実行済みのユーザの code-reviewed マーカーはそのまま有効。新たに `/simplify` ステップを 1 回走らせれば simplified マーカーが生成される。v2.1.153 以下で `/simplify` が存在しない環境では fail-open により `/code-review` 単独で第一者要件を満たせる

### v0.8.4 → v0.8.5 の変更点 (#90)

- **fail-open / fail-closed policy ラベルを 3 hook に付与**: `block-pre-push.sh` / `block-bg-codex-review.sh` (PreToolUse) に `policy: fail-closed`、`auto-mark.sh` (PostToolUse) に `policy: fail-open` のラベルを冒頭に明記。「同じ git / 環境失敗が Pre=deny / Post=skip」という非対称が意図的である旨を統一フォーマットで可視化し、レビュー時に対称性を即判定できるようにした
- **`auto-mark.sh` の matcher `"*"` のコストを注記**: 全 tool 完了発火で巨大 INPUT に ERE 評価が走る in-process コストと、問題化時の substring pre-filter 案を注記 (現状は早期離脱ロジックを変えるリスクを避け注記に留める)
- いずれもコメントのみの変更 (実行挙動は不変)。共有 `lib/cmd-parser.sh` の eval 間接代入については、衝突しうる local 変数名・injection 安全性 (`printf '%q'`)・caller prefix 推奨が既に同ファイルに詳細文書化されているため追加変更なし

### v0.8.3 → v0.8.4 の変更点 (#85, #86)

- **block-pre-push.sh のコメント自己矛盾を修正 (#85)**: 「push の後に置く `&` / `|` は許容する」という初期設計の名残コメントが、 実コード (位置を問わず `&` / `|` を deny) と矛盾していた。 コメントを実挙動 (前後を問わず deny、 logging は file redirection か別 Bash 呼び出しで) に合わせて修正
- **auto-mark.sh の cwd 前提を明文化 (#86)**: 本 hook は dirty 判定 / ハッシュ計算 / marker パスを発火時の cwd で行い、 push gate (block-pre-push.sh) は push target を解決する非対称がある。 これは「review した repo (cwd) を mark し、 別 repo を target-override push すると hash 不一致で deny (= fail-closed)」 という安全な設計であることを設計意図コメントに明記。 未レビュー push を通す bypass ではない
- いずれもコメントのみの変更 (実行挙動は不変)

### v0.8.2 → v0.8.3 の変更点 (#93)

- **`git push` の分離引数オプションの値 token 誤認を修正**: `-o` / `--push-option` / `--receive-pack` / `--exec` の **値 token** を refspec / remote と誤認し、 正当な push を false-positive で deny する bug を修正

### v0.8.1 → v0.8.2 の変更点 (#48)

- **`/codex:rescue --wait` ハング時の復旧手順を deny メッセージに追加**: rescue が時々ハングする件への対処 (シェル状態確認 → kill → やり直し) を案内文に追記

### v0.8.0 → v0.8.1 の変更点 (#47, cross-plugin)

- **末尾 `\<LF>` (line continuation) による検知 bypass を修正**: bash の `$(...)` が trailing newline を trim する仕様で `"command":"git push\<LF>"` が取得時点で `git push\` に壊れる問題を、 jq 取得直後に復元する処理を 3 hook (auto-mark.sh / block-bg-codex-review.sh / block-pre-push.sh) へ横展開

### v0.7.0 → v0.8.0 の変更点 (#46)

- **macOS デフォルト bash 3.2.57 の互換性 bug 2 件を修正**: line continuation 正規化を `${var//...}` パターン置換から純 bash + sed fallback 実装に置換、 `skip_env_assignments` 呼び出し側の `_idx` / `_n` 変数衝突を解消
- **3 つの hook script に診断 EXIT trap を追加** (`lib/exit-trap.sh`): 予期せぬ非ゼロ exit (jq クラッシュ / signal / シェル展開失敗等) を stderr にノンブロッキングで報告し、 hook 破損を可視化する

### v0.6.0 → v0.7.0 の変更点

- **Claude Code v2.1.146 で bundled skill `/simplify` が `/code-review` にリネームされたのに追随**: PostToolUse の検出ロジック (auto-mark.sh の `PRECHECK_RE` および Skill 分岐 case) を `code-review` / `simplify` の両方を受け付ける形に拡張。 v2.1.146 以降のユーザーは新名で、 v2.1.145 以下のユーザーは旧名のままで同じマーカーが書かれる
- **マーカーファイル名を `.claude-pre-push-simplified` → `.claude-pre-push-code-reviewed` にリネーム** (内部のシンボル名も整合性のため同期): v0.6.0 以前を使っていた環境では旧マーカーが disk に残るが、 v0.7.0 のコードからは参照されないため無害 (次回 `/code-review` 実行時に新ファイルが作成される。 気になる場合は `rm <git-dir>/.claude-pre-push-simplified` で手動削除可)
- **deny メッセージ / README / description を新名 `/code-review` に統一**: v2.1.145 以下のユーザー向けに「旧名 `/simplify` 可」の注記を残しつつ、 標準ガイドは新名に切り替え

### v0.4.0 → v0.5.0 の変更点

- **`/codex:adversarial-review` 連携を全廃**: 旧版は `/codex:review --wait --scope branch` の連続実行回数を `LOOP_COUNTER` で計測し、 閾値到達時に deny メッセージへ `/codex:adversarial-review --wait --scope branch` 起動を促す案内文を追加していた。 adversarial-review はサイクル時間が非常に長くなる問題があるため、 v0.5.0 で関連機能 (loop counter / 閾値判定 / 案内文 / `lib/loop-counter.sh`) を完全に削除した。 deny メッセージの手順 4 / rescue 壁打ち規律は `/codex:review` 単独で完結する形に整理。 表層レビューだけで収束しない場合の対応は引き続き Claude の自律判断 (人間判断を仰ぐ等) に委ねる

### v0.3.0 → v0.4.0 の変更点

- **`/codex:review` 指摘修正の前に `/codex:rescue` で方針を壁打ちする規律を deny メッセージに追記**: deny メッセージ (REASON) の手順 4 に、 review からの指摘に対して **いきなり修正実装に入らず、 まず `/codex:rescue --wait` で修正方針を壁打ちし、 approve 後に実装を開始する** 規律を明文化した。 観点は「指摘の根本原因に対する解として妥当か」「場当たり的な対処になっていないか」「全体設計と一貫しているか」の 3 つ。 これにより review ループでの修正が表層的な塗りつぶしに偏るのを抑制し、 設計レベルの一貫性を保つ。 `/codex:rescue` はマーカー対象外で push gate には影響しない (rescue は「修正前の方針壁打ち」であって「最終差分のレビュー」ではないため、 markers / gate と責務を分離する設計)
- **手順 4 を 2 レビュー全部に inclusive 化し、 rescue 壁打ち scope は codex review のみに限定**: push gate は security マーカーの書き込みのみを確認し report 内容まで verify しないため、 「指摘があれば必ず修正する」 remediation 義務は 2 レビュー (`/codex:review` / security-reviewer subagent) すべてに適用する。 一方 **`/codex:rescue` 壁打ちは `/codex:review` の指摘に対してのみ必須** で、 security-reviewer の指摘は通常具体的な脆弱性対処 (input validation 追加 / 秘匿情報削除 / injection 対策等) のため壁打ち optional (設計判断が絡む修正のみ rescue 推奨)
- **「/codex:review であって /codex:rescue ではない」の警告文を更新**: 本バージョンから両者を **両方使う** ループに変わるため、 「取り違えに注意」を残しつつ、 用途の対比 (`/codex:review` = レビュー取得 / `/codex:rescue` = 方針壁打ち) を明示する形に書き換え

### v0.2.0 → v0.3.0 の変更点

- **security review を self-contained subagent に切り出し** (詳細は [Agents](#agents) セクション): 主 session から直接 `/security-review` を呼ぶと skill の終端指示「Your final reply must contain the markdown report and nothing else.」で turn が終わり、 後続 `git push` まで進まない問題への対応。 `pre-push-review:security-reviewer` subagent を新設し、 deny メッセージは subagent 経由の呼び出しを推奨する。 subagent は **`/security-review` 標準 skill を呼び出さず**、 自前の prompt で同等のセキュリティレビューを self-contained に実行する。 これは Claude Code が subagent 内で別の subagent (= Task tool による sub-task) を spawn できない制約のため。 標準 skill 本体は内部で sub-task を spawn する設計だが、 subagent 内ではそれが機能しないため。
- **auto-mark.sh のマーカートリガを変更**: `Skill(security-review)` の launch ではなく、 `Agent` / `Task` tool で `pre-push-review:security-reviewer` subagent が完了したタイミングで security マーカーを書く。 launch ではなく completion を使うのは、 subagent がレビュー本体を完了させたことを確認した上でマーカーを書くため (= subagent 失敗時に silent-pass しない)。 hook は subagent 内の tool use にも発火する Claude Code の挙動に依存しない設計

### v0.1.0 → v0.2.0 の変更点

codex adversarial-review の指摘を踏まえた gate の精緻化:

- **tag-only push の reachability check**: `git push origin <tag>` で tag が指す commit が現在ブランチ HEAD から reachable でない場合 deny。 旧版は blanket-skip だったため別ブランチの未レビュー commit を tag 経由で push できた経路を塞いだ
- **default branch 解決失敗時に fail-closed**: `origin/HEAD` 未設定 / 非 origin remote / default が master/main 以外の環境で旧版は silent に exit 0 = gate 無効化していた。 v0.2.0 は明示 deny して setup を促す
- **redirection 構文を parser 前段で strip**: `git push 2>&1` の単独 redirection が `&` を含むため誤って parallel-separator deny に倒れていた。 v0.2.0 は `2>&1` / `>&N` / `<&N` / `&>file` 等を sed で事前 strip して redirection と並列性を切り分ける
- **単独 `&` / `|` を位置によらず deny**: bash は `cmd1 | cmd2` / `cmd & cmd2` 両側を同時起動するため、 markers gate 検証完了後に並走 cmd が index / refs / working tree を変更する race 経路が残る (例: `git push | git commit -m x`)。 race-free を hook 単独で保証するため downstream allowlist は採らず、 logging が必要な場合は file redirection (`git push > log.txt 2>&1`) や別 Bash 呼び出しでの後処理を使う設計

## 前身プラグインからの設計変更

本プラグインは `pre-commit-review` の後継として、レビュー強制の **境界を commit から push に移したもの** です。実体は別プラグインですが、機能と loop discipline は同等で、利用者の操作感は「commit は自由 / push 直前に 1 周ループ」に変わります。

### なぜ push 境界か

pre-commit 境界の課題:

- **commit ごとにループが回る**: N-commit PR では合計 N 回ループが走るため、多 commit PR ほど時間コストが増える
- **意味的に異なる変更が 1 commit に混入**: 初期実装 / `/code-review` edits / `/codex:review` 指摘修正がすべて同じ commit に圧縮され、`git log` / `git blame` / `git bisect` の解像度が失われる
- **中間 commit が残せない**: WIP / 探索 / checkpoint のような目的別 commit が deny によって作成不能
- **長時間 uncommitted 状態が常態化**: deny → 修正 → deny の繰り返しで数十分単位で未保存。ターミナルクラッシュや誤操作で作業損失リスク

push 境界に移すことで:

- **PR 全差分に対して 1 周のループ**: 1-commit PR では同等、多 commit PR で削減 (実測ベースで 40-48% の review 回数削減見込み)
- **意味的単位の commit が許容される**: 実装 / code-review / fix が独立 commit として記録できる。bisect / blame / archaeology が効くようになる
- **WIP commit が自由に重ねられる**: 中間 checkpoint をいつでも保存可能
- **未レビューな commit を remote に到達させない**: PR 作成手段 (gh CLI / Web UI / IDE / API) のいずれを使われても **precondition (remote branch の存在) を破壊** することで構造的に gate できる。「pre-PR matcher で `gh pr create` だけを止める」設計だと人間の Web UI 操作で bypass される問題が解消する

### 境界の選択: なぜ `git push` であって `gh pr create` ではないか

PR 作成側で gate する設計だと、以下の経路が捕捉できません:

- 人間がブラウザの GitHub Web UI で「Create Pull Request」をクリックする
- GitHub Desktop / IDE 拡張 / `gh api` / API スクリプトでの PR 作成
- Claude が `gh pr create` 以外の手段で PR を作る (例: `gh api` 経由)

これらはすべて「remote branch が存在する」ことを precondition にしているため、push 境界で未レビュー commit を remote に到達させなければ、PR 作成手段に関わらず PR が成立しません。**precondition gating** 戦略により攻撃面を 1 箇所 (push) に集約しています。

## 概要

`PreToolUse` フックで `Bash` ツール実行を監視し、`git push` コマンドを検出した場合、 **4 つのレビューマーカー** (`/simplify` (cleanup) / `/code-review` (バグ検出) / `/codex:review --wait --scope branch` / `/security-review` (subagent 経由) それぞれの実行完了マーカー) が現在の **branch 全差分 + 未コミット差分** のハッシュと一致しなければ `deny` を返して push を阻止します。第一者 (`/simplify` + `/code-review`) は Claude Code v2.1.154+ を検出したとき両方必須、 それ以外は fail-open でどちらか 1 本に緩めます ([第一者レビューの version 依存](#第一者レビューの-version-依存))。`/codex:review` と security は version に関わらず常に必須です。マーカーは `PostToolUse` フックが各ツールの実走完了を検知して自動的に書き込みます (PostToolUse は subagent 内の tool use にも発火するため、 security 用 subagent 経由でも `/security-review` の skill 起動が検知されます)。手動でスクリプトを呼び出す必要はありません。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install pre-push-review@natsuume-plugins
```

## 機能一覧

### Hooks

#### 1. block-pre-push (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-push.sh`

`git push` を含むコマンドを検出した際、現在のブランチ全差分 + 未コミット差分のハッシュと 4 つのレビューマーカー (`/simplify` / `/code-review` / `/codex:review` / security) のハッシュを比較し、必須マーカーがすべて一致しなければ `deny` を返します (第一者 `/simplify` + `/code-review` の必須化は version 依存)。

**動作**:

- `git push --dry-run` / `git push -n` (remote ref を更新しない診断 push) は markers の状態に関わらず通す (no-op なので gate 不要)
- 単独実行 (`git push`) と複合コマンド (`xxx && git push ...`, `cd dir && git push ...`) の双方を検出
- `git -C dir push` や `git --git-dir=... push`、`GIT_DIR=... git push` のような target-override 形式も許容 (cooperative 利用前提)
- カレントブランチが default branch (master/main) の場合は本フックでは gate せず、`git-guardrails` の `block-default-branch-push.sh` に委譲 (重複 deny メッセージを避けるため)
- ブランチ全差分 + 未コミット差分が空 (= base と同一) の場合は gate しない (空 push は通す)
- **working tree が dirty (staged または unstaged 変更あり) の場合は markers の状態に関わらず deny**: push される committed 部分とレビューされた working tree の乖離を防ぐため、push 前に commit 完了を要求する
- 必須マーカー (codex + security + 第一者 1〜2 本) がすべて一致した場合はそのまま push を許容する (markers は明示削除しない: PreToolUse は push 成功を確認できないため、 remote rejection / 認証失敗 / ネットワーク失敗時に同じ state での再 push がレビュー必須になる無駄ループを避ける。markers は次の編集で hash が変わったときに自然に失効する)
- ハッシュは `git diff origin/<base>...HEAD` (PR diff) と `git diff --cached`、`git diff` の連結に対して計算するため、未コミットの edit があると markers のハッシュが変わる仕組み。実際の push gate は dirty-tree 検出で行うが、ハッシュ算式に未コミット差分を含めることで「review 後に edit して push」のような経路もマーカー失効で再 review に倒せる
- `deny` 時の `permissionDecisionReason` には、各マーカーの状態 (`未実行` / `失効` / `✓ 最新の差分でレビュー済み`) と次に Claude が行うべき手順が記載される

**残っている deny 制約 (loop discipline 維持に必要な最小防御)**:

- `bash -c "..."` 等の **シェルラッパー** 経由 push は引き続き deny (クォート内のコマンドを本フックの文字列パーサで解析できず、postfix scan も成立しないため)
- 単独の `&` (background) と `|` (pipeline) は deny (並列実行になりマーカー検証完了後に状態が変更される経路になるため)
- `git push` の **後** にシェル区切り文字 (`;`, `&`, `&&`, `||`, `|`) を続ける複合コマンドは deny (1 マーカー = 1 push 保証のため)
- 引用符で囲まれた `git push` 文字列 (`grep "git push" README` など) はテキスト参照とみなしフックは介入しません
- **`git push` の引数に引用符 (`"` / `'`) が含まれる形** は deny (例: `git push origin "other-branch"`)。本フックの parser は引用符付き引数を確実に解析できないため、refspec/オプションチェックを素通りさせる経路を保守的に塞ぐ。引用符なしで `git push origin feat/x` のように渡す運用
- `time git push ...` / `env git push ...` のように本フックが認識していない wrapper を介して push する形式は deny (postfix scan の起点が取れず未レビュー push を素通しさせるリスクを保守的に塞ぐため)
- **`--all` / `--mirror` / `--tags`** は deny (複数参照 / tag 一括 push でマーカー検証対象外のコミットが混入するため)
  - tag を push したい場合は、tag が指す commit を含むブランチを通常通りレビューして push し、別の Bash 呼び出しで `git push origin <tag-name>` のように個別 tag を push する運用
- **現在ブランチと一致しない refspec を明示する形 (`git push origin other-branch` 等)** は deny (本プラグインは現在ブランチの差分でマーカー検証するため、別ブランチを引数指定する形では未レビュー commit が remote に到達する。push したい場合は `git switch` で切り替えてから `git push` する運用)
  - `git push` / `git push origin` / `git push origin HEAD` / `git push -u origin <現在ブランチ名>` は引き続き許容 (いずれも現在ブランチを push する形)
  - `git push origin :branch` (削除、source 空) はローカルレビュー対象外なので許容
  - `git push --delete origin <branch>` / `git push -d origin <branch>` (削除フラグ) は新規 commit を送らないので許容
  - `git push origin <tag-name>` (個別 tag push) は 2 段階の reachability check で扱う。 tag が HEAD から reachable で、 かつ origin/<base> からも reachable (= 既に remote に到達済 = 過去の review を経ている) なら markers gate を skip。 HEAD から reachable だが origin/<base> から未到達なら通常の real push 扱い (markers gate 要求)。 HEAD から reachable でなければ deny
- **working tree が dirty (staged または unstaged 変更あり) のまま push** は deny (push される committed 部分とレビューされた working tree の乖離を防ぐ。`git status` で変更を確認 → `git add` / `git commit` してから再 review → push)
- **`git config push.default=matching` 環境での refspec 省略 push** は deny (`matching` モードでは bare push が複数ブランチを一括送信するため、現在ブランチ以外の未レビュー commit が gate を素通りする。`git push origin HEAD` で明示するか、`git config push.default simple` に変更する運用)
  - 現代の git デフォルト (`simple`, 2014 年以降) では bare push は現在ブランチのみ送るため影響なし。明示的に `matching` を設定している環境のみ deny する

**サポート外 (本プラグインの範囲外で別レイヤーが必要)**:

- 別端末・別 clone から行われる `git push` は Claude Code hook の原理的範囲外で gate できない (本気で塞ぐなら `.git/hooks/pre-push` real git hook を別レイヤーで併設)
- GitHub サーバ側で実施される操作 (Web UI のマージ / rebase 等) も Claude Code hook 範囲外
- **default branch (master/main) 上での push は本プラグイン単独では gate されない**: 本プラグインは `git-guardrails` の `block-default-branch-push.sh` が default branch push を deny する前提で gate を skip する。 `git-guardrails` を併用していない環境では default branch 上の push が review なしで通る経路が残る。 default branch 保護を確実にしたい場合は `git-guardrails` を必ず併用すること
- **個別 tag push の reachability check**: `git push origin <tag-name>` は tag が指す commit が HEAD から reachable かつ origin/<base> からも reachable な場合に限り markers gate を skip する。 origin/<base> から未到達な (= 現在ブランチに固有の) commit を指す tag は markers gate を要求し、 別ブランチの commit を指す tag は deny する

> **target-mismatch の構造的解決**: 本プラグインは独自の bash command parser (`lib/cmd-parser.sh`) と target resolver (`lib/target-resolver.sh`) で `cd dir && git push` / `git -C dir push` / `GIT_DIR=path/.git git push` の **実 push target を決定的に解決** し、 解決した target cwd の `.git` 配下に対して markers / hash 比較を行う。 「hook 検証時の cwd と実 push 時の cwd が乖離」する旧来の問題は、 positive list 設計 (実 target を取り出して直接検証) によって構造的に塞がれている。 解析不能な形式 (subshell `(...)`, brace group `{...}`, `bash -c "..."`, `pushd`/`popd`, `export GIT_DIR=...`, `--work-tree=...`, `time` / `env` 等の未対応 wrapper) は **保守的に deny** する (parser が target を確定できないため)。

> **順序の意図**: チェーンで**コードを編集するのは `/simplify` (cleanup) のみ**です。`/simplify` を先に走らせて cleanup を適用し、`/code-review` (read-only バグ検出) / `/codex:review` / security はその後の最終形を対象にレビューします。逆順だと read-only レビューが `/simplify` によって書き換わる前のコードを見ることになり、レビュー結果が陳腐化します。`/simplify` が edits を行うと branch 差分が変わり全マーカーが失効するため、cleanup が安定 (no-op) するまで `/simplify` を繰り返してから後段の read-only 3 本を最終差分に対して走らせると無駄な再ループを減らせます。マーカー方式上は順序を強制していませんが、編集する `/simplify` を先頭に置くのが合理的です。
>
> **(v0.7.0〜v0.8.x の旧 README はこの順序根拠を `/code-review` に付けていましたが、それは誤りでした。** v2.1.147 以降の `/code-review` は read-only でコードを編集しないため「編集するから先に走らせる」根拠が空振りしていました。v1.0.0 で、実際に編集する `/simplify` に付け替えて整合させています。)

#### 第一者レビューの version 依存

`/simplify` (cleanup・編集) と `/code-review` (read-only バグ検出) はどちらも Anthropic 第一者 skill ですが、両者が**併存するのは Claude Code v2.1.154+ のみ**です (履歴は本 README 冒頭の注記を参照)。それ未満の version では片方の skill しか存在しないため、両方を無条件に必須化すると存在しない skill のマーカーが永遠に埋まらず **永久 deny** になります。

これを避けるため `lib/first-party-review.sh` が **fail-open の version エスカレーション**を行います:

| Claude Code version | `/simplify` | `/code-review` | 第一者レビュー要件 |
|---|---|---|---|
| ≤ v2.1.145 | cleanup-and-fix (編集) | 存在しない | **どちらか 1 本** (fail-open) — 実際は `/simplify` のみ可 |
| v2.1.147〜v2.1.153 | 存在しない | read-only バグ検出 | **どちらか 1 本** (fail-open) — 実際は `/code-review` のみ可 |
| **v2.1.154+** (現行) | cleanup-only (編集) | read-only バグ検出 | **両方必須** (昇格) |
| 検出不能 (npm 配置 / env 欠落 等) | — | — | **どちらか 1 本** (fail-open) |

- version は env 変数 (`CLAUDE_CODE_VERSION` → `AI_AGENT` → `CLAUDE_CODE_EXECPATH` の順) から **fork なし**で読みます (`claude --version` の再入・起動コスト・フォーマット依存を避ける)。
- **version 検出は「両方必須」に昇格する方向にのみ使われます**。検出が成功して v2.1.154+ と確定できたときだけ両方必須に昇格し、それ以外 (旧 version / env 欠落 / 取得失敗) はすべて「1 本で可」に降格します。
- この非対称が安全性の肝です: 検出が将来壊れても最悪「v2.1.154+ なのに 1 本で通せてしまう (= 第一者レビュー 1 本 + codex + security は依然必須)」に**緩む**だけで、未レビュー push は通らず、存在しない skill を要求して**永久 deny にもなりません**。「外部の可用性に依存する判定は SPOF にしない」という設計方針 (`reference_prompt_hook_model_spof` の教訓) に沿っています。

> **`--wait` 限定の理由**: `/codex:review --background` だと Bash tool の `run_in_background: true` 起動直後に PostToolUse が発火し、レビューが完了する前に auto-mark.sh が呼ばれます。auto-mark.sh は background 起動を検知してマーカー更新をスキップするため、background 経由ではマーカーが永遠に更新されず push が通りません。pre-push-review の文脈では必ず `--wait` を渡してください。

> **`--scope branch` 限定の理由**: pre-push-review が gate するのは「branch の commit 列 (= PR diff)」の品質保証で、 `--scope working-tree` (staged+unstaged のみレビュー / committed 部分を見ない) や `--scope auto` (dirty 時に working-tree にフォールバック) では PR diff の review 保証として不十分です。auto-mark.sh は `--scope branch` を含む codex 起動のみマーカーを更新します。

> **ループの意図**: 修正を加えた瞬間、その修正自体は未レビューになります。`/codex:review` の指摘を修正した結果として `/code-review` の対象 (重複・冗長コメント等) が新規発生する可能性も、`/code-review` の修正により `/codex:review` の新規指摘が出る可能性も、いずれもゼロではないため、修正があれば `/code-review` から再度ループします。プラグインは「マーカーのハッシュ = `git push` 時の branch 全差分 + 未コミット差分」だけを検証するため、ループ回数の push 強制ブロックは行いません。

> **終端の判断**: ループ回数による push 強制ブロックは行いません。表層レビューだけで収束しない場合の対応 (実装方針の見直し / 人間判断のエスカレーション等) は Claude の自律判断に委ねます。

> **`/codex:review` と `/codex:rescue` の役割分担**: 本プラグインは公式 codex プラグインの 2 つのコマンドを **両方** 使います。 用途を取り違えないこと:
>
> - **`/codex:review --wait --scope branch`**: branch 全差分への read-only レビュー取得。 PostToolUse の auto-mark.sh がこの完了でマーカーを書き、 push gate の検証対象になる。 frontmatter で `disable-model-invocation: true` が指定されており本来 Skill tool から呼び出せないが、 姉妹プラグイン [codex-review-customize](../codex-review-customize/) を導入してパッチを適用すると Skill tool 経由でも呼び出し可能になる。
> - **`/codex:rescue --wait`**: review からの指摘に対する **修正方針の壁打ち** に使う (v0.4.0 で導入された規律)。 deny メッセージの手順 4 が要求する形で、 「指摘の根本原因に対する解として妥当か」「場当たり的でないか」「全体設計と一貫しているか」を rescue に問い、 approve が出てから実装を開始する。 `/codex:rescue` 自体はマーカー対象外で push gate には影響しない (rescue は「修正前の方針壁打ち」で「最終差分のレビュー」ではないため、 markers / gate と責務を分離する設計)。 `/codex:rescue` は `disable-model-invocation` が指定されていないため、 codex-review-customize パッチなしでも Skill tool から直接呼び出し可能。

> **security review は subagent 経由で呼ぶ**: 詳細は下記 [Agents](#agents) セクション。

#### 2. auto-mark (PostToolUse, matcher: `*` — wildcard)

**ファイル**: `hooks/scripts/auto-mark.sh`

`/simplify` / `/code-review` / `/codex:review --wait --scope branch` / `pre-push-review:security-reviewer` subagent の実行完了を PostToolUse hook で自動検知し、対応するマーカーファイルに「現在の branch 全差分 + 未コミット差分のハッシュ」を書き込みます。 v1.0.0 で `/simplify` (cleanup・編集) と `/code-review` (read-only バグ検出) は別マーカーに書き分けます。

hooks.json の matcher は `"*"` (wildcard) で、すべての tool 完了時に本フックが呼ばれます。`Skill` matcher の挙動が公式ドキュメント上完全に明記されていないため、tool 名に依存しない構造にしてあります。フィルタリングはスクリプト側の bash 内蔵正規表現マッチが行うため、対象外 tool は subprocess を立てずに即離脱します。

**検知ルール**:

| 検知対象                                                | tool 名 | 判定                                                                                                                                                                            | 書き込むマーカー                              | 副作用                  |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | ----------------------- |
| `/simplify` skill の launch (cleanup・編集) | `Skill` | `tool_input.skill == "simplify"` | `<git-dir>/.claude-pre-push-simplified`    | (なし)                  |
| `/code-review` skill の launch (read-only バグ検出) | `Skill` | `tool_input.skill == "code-review"` | `<git-dir>/.claude-pre-push-code-reviewed`    | (なし)                  |
| `pre-push-review:security-reviewer` subagent の完了 (推奨) | `Agent` / `Task` | `tool_input.subagent_type` が `pre-push-review:security-reviewer` または `security-reviewer` (name-only 形式も許容) | `<git-dir>/.claude-pre-push-security-reviewed` | (なし)                  |
| `/security-review` skill の launch (後方互換)        | `Skill` | `tool_input.skill == "security-review"` (主 session 直接呼び出しのみ。 subagent は tools から Skill を外しているため呼べない) | `<git-dir>/.claude-pre-push-security-reviewed` | (なし)                  |
| `/codex:review --wait --scope branch` の Bash 完了      | `Bash`  | コマンドが `^node` で始まる (env-prefix 許容) / `codex-companion.m[jt]s review` を含む / `--scope branch` を含む / `run_in_background == false` / 失敗・中断ではない                | `<git-dir>/.claude-pre-push-codex-reviewed`   | (なし)                  |

**skill を launch タイミングで検知する設計上のトレードオフ**:

`Skill` tool の `PostToolUse` は `Launching skill: <name>` を返した瞬間 (= skill body 実行 **前**) に発火します。本プラグインはこの timing でマーカーに **launch 時点の差分ハッシュ** (= skill が見ることになる state) を書き込みます。

- メリット (loop discipline / 主に `/simplify`): **`/simplify` はコードを編集する**ため、body が edits を行うと current hash は launch 時点と異なる値になります。block-pre-push.sh はこの hash と current hash を比較するため、edit 後は marker stale → DENY となり、Claude は **edits が無くなる (no-op) まで `/simplify` を再実行する** 必要が生じます。これにより「cleanup 適用後の差分は必ず再 cleanup で確認させる」という loop discipline が構造的に強制されます。`/code-review` は read-only なので自己失効はしませんが、`/simplify` の編集で `/code-review` マーカーも失効するため、cleanup 後に `/code-review` を再走させる必要が生じます。
- 既知の限界 (lie attack): Claude が `Skill(simplify)` / `Skill(code-review)` を呼んでも skill body の meta prompt を実際に実行せず、その後 push する経路では、マーカーが launch 時点の hash で揃ってしまい push が通ってしまいます。これは Claude が instructions を真摯に follow するという信頼を前提とした設計で、構造的には防げません。

**security-reviewer subagent を completion タイミングで検知する理由**:

subagent は内部で `/security-review` 標準 skill を呼ばずに self-contained でレビューを実行します。 PostToolUse hook が Skill launch ではなく Task 完了で発火するように倒すことで、 subagent が **実際にレビューを完了させた** ことを確認した上でマーカーを書きます。 subagent が途中で失敗した場合 (`tool_response.is_error` / `interrupted`) はマーカーが書かれないため、 push gate がそのまま deny を返してループが続きます (silent-pass しない設計)。

**書き込みをスキップする条件**:

- `tool_response.is_error` または `tool_response.interrupted` が `true` (失敗した review 結果でマーカーを書かない)
- `tool_input.run_in_background` が `true` (background 起動は完了タイミングを捉えられないため)
- `tool_input.skill` が `code-review` / `simplify` / `security-review` 以外 (namespace 付き skill `code-review:code-review` 等は別物として扱う)
- `tool_input.subagent_type` が `pre-push-review:security-reviewer` / `security-reviewer` 以外 (別の subagent 起動はマーカー対象外)
- Bash codex 起動でコマンドに `--scope branch` が含まれていない (PR diff レビュー保証として不十分)
- **Bash codex 起動時に working tree が dirty (staged または unstaged 変更あり)** (`/codex:review --scope branch` は committed 部分のみ review するため、dirty 状態で marker を書くと commit 後のハッシュと衝突して未レビュー commit を通す経路ができる。clean なときに review してから marker を書く運用に倒す)
- カレントブランチが default branch (master/main)
- default branch (origin/HEAD) が検出できない (origin が無い等)

## マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-push-simplified` | `/simplify` (cleanup・コード編集) 実行時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-code-reviewed` | `/code-review` (read-only バグ検出) 実行時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed` | `/codex:review --wait --scope branch` 完了時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-security-reviewed` | `pre-push-review:security-reviewer` subagent 完了時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |

> **v1.0.0 アップグレード時の注意**: `.claude-pre-push-code-reviewed` の**意味が変わりました** (旧: `/simplify` または `/code-review` の共用マーカー → 新: `/code-review` (read-only バグ検出) 専用)。`/simplify` (cleanup・編集) は新設の `.claude-pre-push-simplified` に書かれます。v0.8.5 以前で `/code-review` を実行済みなら code-reviewed マーカーはそのまま有効ですが、v1.0.0 では新たに `/simplify` の実行 (および codex / security の再走) が必要です。`/simplify` と `/code-review` の両方が必須化されるのは Claude Code v2.1.154+ のみで、それ未満では fail-open でどちらか 1 本に緩みます ([第一者レビューの version 依存](#第一者レビューの-version-依存))。
>
> **v0.6.0 以前からの孤児マーカー**: v0.6.0 以前で作成された `.claude-pre-push-simplified` ファイルが残っている環境では、v1.0.0 がこのファイル名を `/simplify` 用に**再利用**します。古いハッシュは次回 `/simplify` 実行時に上書きされるため無害です。

### Agents

#### `pre-push-review:security-reviewer` (subagent)

**ファイル**: `agents/security-reviewer.md`

branch 全差分に対するセキュリティレビューを **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。 v0.3.0 で追加されました。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/security-review` skill を invoke できないようにしている (理由は下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため、 列挙しても動かない
- subagent body には input validation / authn-authz / crypto-secrets / injection / data-exposure の各カテゴリと exclusion ルール (DoS / 既存依存 CVE / テストファイル等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として markdown report を受け取り、 後続フロー (`git push` 等) を継続できる。 subagent の system prompt 終端で `Return the report as your final reply. No tool use, no further actions after composing the report.` と明示しているため、 親 session のフローは止まらない
- PostToolUse hook (auto-mark.sh) は subagent **完了時** に発火する Agent / Task tool 検知ロジックで security マーカーを更新する (launch 時点ではなく completion で書くことで、 subagent 失敗時の silent-pass を防ぐ)
- model は `inherit` で親 session と同じモデルを使用

**標準 `/security-review` skill を invoke しない理由**:

(1) 主 session の Claude が直接呼ぶと skill prompt 末尾「Your final reply must contain the markdown report and nothing else.」によって turn が終了し、 後続フロー (`git push`) まで進まない。
(2) subagent 内から invoke しても、 標準 skill 本体は内部で sub-task (Task tool) を spawn する設計だが、 Claude Code は **subagent 内での nested subagent 起動を禁止** している (公式ドキュメント `subagents cannot spawn other subagents`)。 sub-task が動かないため degraded mode で実行されるが、 PostToolUse は Skill launch 時点で発火するためマーカーは書かれてしまい、 silent-pass の経路ができる。
(3) このため subagent は **同等のレビュー内容を self-contained な prompt として持ち**、 標準 skill を invoke しない設計に倒している。 標準 skill の prompt とは別管理になるため、 Anthropic 側の今後の改善は手動で追随する必要がある (トレードオフ)

**呼び出しタイミング**: block-pre-push.sh の deny メッセージで「security マーカー未実行 / 失効」と指摘された際に subagent invocation tool (Claude Code では `Task` または `Agent` という名前 — 同じ tool を指す) で起動する。 Claude が直接 `/security-review` を Skill tool で呼ばないよう deny メッセージに明示の警告がある

## 関連プラグイン

- [codex-review-customize](../codex-review-customize/): `/codex:review` を Skill tool から呼べるようにパッチを適用する setup プラグイン
- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。本プラグインは default branch 上の push を git-guardrails に委譲します

#!/bin/bash
# inject-always.sh
# SessionStart で「permission_mode に依らず常時適用」 すべき agent-discipline ルールを
# まとめて additionalContext として注入する。
#
# 注入対象:
#   1. 物理層: Bash コマンド分解 (PreToolUse hook 取りこぼし防止)
#   2. before 系: 設計 / 仕様の事前壁打ち (AskUserQuestion) + 「思考は自由、 成果物への固定化は要承認」 非対称ルール (2.1)
#   3. before 系: issue 起票時の詳細化 / 起票直前 + pick up 時の self-check / PR 説明 + plan + commit への適用 (3.1 / 3.2)
#   4. before 系: issue の粒度と関係性 (sub-issue 親子 + #N 相互参照)
#   5. PR 作成時の closing keyword 規約
#   6. during 系: 自律作業中の判断境界 (permission_mode 非依存の行動指針)
#   7. 連続 issue 解決時の排他制御 (auto mode + /goal 等のフロー想定、 claim comment + branch push の二段排他)
#
# auto mode 限定の方針 (after 系 = commit→push→PR→merge 自走) は inject-auto.sh が別途配送する。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name は入力からそのまま読み取る (既定値を埋めると別 event の
# 文脈に誘導する恐れがあるため)。INPUT が不正な JSON / 空の場合 jq は parse error を
# stderr に吐くため 2>/dev/null で抑制し、HOOK_EVENT 空判定でフォールバックさせる
# (hook の stderr は利用者に見えるため、解析失敗をノイズとして表に出さない)。
HOOK_EVENT=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // ""' 2>/dev/null)
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

CONTEXT=$(cat <<'EOF'
# agent-discipline: 常時適用ルール

以下のルールは permission_mode に依らず常時適用される。 auto mode 時の commit→push→PR→merge 自走方針は別途 inject-auto.sh が配送する。

## 1. Bash コマンド分解 (物理層)

Bash コマンドは可能な限り分解し、それぞれを独立した Bash ツール呼び出しとして実行してください。

**なぜ**: Claude Code の PreToolUse hook は Bash ツールの `command` 文字列に対するパターンマッチで判定されます。複数コマンドを合成すると 1 回の Bash 呼び出しとして扱われ、先頭以外の部分が hook 検知から外れる可能性があります。例えば `git add foo.txt && git commit -m "..." && git push` は、`git push` を deny したい hook が先頭の `git add` パターンしか見ずに通過させてしまう恐れがあります。各コマンドを独立した Bash 呼び出しに分解すれば、リポジトリのガードレール (git-guardrails / pre-push-review / auto-lint-check 等) が意図どおり機能します。

**分解すべきパターン**:

- **異なる目的のコマンドを `&&` / `||` / `;` / `&` で連結しない**。サブシェル `(cmd1; cmd2)` やブレースグループ `{ cmd1; cmd2; }` も同等に扱う。`git add foo && git commit -m "msg" && git push` は **3 回の独立した Bash 呼び出し** に分解する (依存が無い独立コマンドは並列 Bash 呼び出しも可)
- **コマンド置換 `$(...)` / バッククォートで別コマンドを埋め込まない**。例えば `cat $(find . -name '.env')` ではなく、`find` で対象を確認したうえで個別に `Read` ツールで開く
- **ラッパー経由でコマンドを隠さない** (`eval "..."`, `bash -c "..."`, `sh -c "..."`, `sudo sh -c "..."` 等)。内側コマンドが `command` 文字列のパターンマッチから外れ、hook を素通りさせる典型経路になる
- **`xargs <cmd>` / `find -exec <cmd> {}` も hook 検知の観点ではシェル合成と同等**として扱う
- **パイプライン `|`** は単一論理操作 (例: `git log --oneline | head -20`, `grep foo | wc -l`) で使う場合のみ許容。必然性がなければ分解する

**例外** (連結を許容するケース):

- ディレクトリを移動した状態でコマンドを実行したい場合の `cd $dir && do_something` (`cwd` 制約のため)
- 前段の成功/失敗で後段を確実に制御する必要があるトランザクション的合成 (例: `make build && make test` のように、ビルド失敗時に必ずテストを止めたい場合)

例外時は合成を使う必然性が明確であることを前提とすること。汎用的な「タイプ数を減らす」「効率化」目的の連結は例外に該当しない。

## 2. 設計 / 仕様検討の事前明確化

設計や仕様レベルの判断 (= 後戻りコストが大きい決定) は **ユーザの専権事項** であり、 Claude が独断で決めて成果物 (issue body / PR 説明 / plan / 実装) に固定化してはならない。 **検討段階で複数案を比較したり推奨案を考えること自体は許容される** が、 その結論を成果物として書き出す前 / 実装着手前に必ず `AskUserQuestion` を発行してユーザの意思決定を受ける。

**明確化の対象** (= 聞くべき):

- スコープ / 要件 / 受入基準
- I/O 契約、 公開 API のシグネチャ
- 既存 system との関係 (拡張なのか置換なのか、 互換性をどこまで保つか)
- 命名 (公開シンボル / file path / package 名など外部から参照されるもの)
- アーキテクチャ上の選択 (state の持ち方、 同期 / 非同期、 永続化方式など)

**対象外** (= auto mode の reasonable assumption に委ねる):

- 変数名 / import の並び順 / docstring の有無
- 関数を 1 個に書くか 2 個に分けるか等の局所的な内部分割
- 「これ最終的に PR にするか」 など次ステップが自明な事項

`AskUserQuestion` は 1 turn あたり最大 4 questions の制約があるため、 複雑な仕様では **大枠 → 詳細の iterative** で進めて良い (1 回で全て詰める必要はない)。

### 2.1 思考は自由、 成果物への固定化は要承認 (非対称ルール)

設計判断の checkpoint は **思考の中ではなく成果物書き出しの瞬間** にある。 思考の中で「A の方が良さそう」 「B はやりすぎ」 などを検討すること自体は問題ない (= 検討プロセス)。 しかし、 その結論を **issue body / PR 説明 / plan / commit message / 実装コード** に書き出す瞬間、 ユーザ未承認の選択を後続 session の Claude が「既決事項」 として読み取ってしまうため、 書き出し直前に必ず `AskUserQuestion` を通す。 auto mode 中であっても、 **設計選択点は `reasonable call` の対象外** として扱い、 本節の self-check を必ず通す。

#### 自己検知トリガー (思考 → 成果物の遷移点で発火)

成果物に書き出す draft を組み立てる際、 以下のいずれかが draft または直前の思考に現れたら、 公開前に手を止めて `AskUserQuestion` でユーザの decision を取り、 確定した 1 案だけを残す。 思考に現れた段階で考えるのを止める必要はないが、 **draft に書く / 実装に着手する前** に必ず確認する:

- **推奨マーキング**: 「(推奨)」 「(default)」 「first choice」 「望ましい」 「自然」 「Recommended: ...」 のような、 複数案を併記しつつ一方を強調する表現
- **独断の正当化**: 「迷ったが A にした」 「A の方が筋が良いので採用」 「シンプルさを優先して B」 のような、 Claude 自身による選択結果の事後正当化
- **比較表で勝者を決める**: pros/cons 表を作って「総合的に A」 と結論付ける構造 (= 比較表は提示までで止め、 結論はユーザに委ねる)
- **暗黙の決め打ち**: 複数案が成立する論点で 1 案のみ詳細記載し他案に言及しない / 1 案だけ詳細・他案 1 行という **粒度差** で暗に推奨する
- **「とりあえず」 系**: 「とりあえず A で実装」 「一旦 A で進めて後で見直す」 (= 後続 session が既決事項として読み取る危険語)
- **暫定マーク残置**: 「(暫定)」 「(仮)」 「TODO: A or B を決める」 を draft に残したまま起票 / 着手する
- **ユーザ判断の先回り代弁**: 「ユーザはどちらでも良いと言うはず」 「明らかに A だろう」 で `AskUserQuestion` を省略する判断
- **「選択点なし」 の即断**: 「これは聞くまでもない」 と感じたら、 反対案 (やらない / 既存維持 / 逆方向) を 1 つ仮想し、 それでも一意かを確認する (= 仮想反対案が成立するなら複数案あり = `AskUserQuestion` 対象)

#### 提示の仕方

`AskUserQuestion` で投げる際は、 各案を **粒度を揃えて** (メリット / デメリット / 想定影響範囲を等しく書く)、 **序列・推奨を付けず** 中立に列挙する。 ただしユーザから明示的に「Claude の意見を聞かせて」 と問われた場合に限り、 別 turn で意見を述べてよい。

なお **ユーザが既に決定済みの選択** に対する rationale 記述 (PR 説明での「なぜ A を選んだか」 等) は文書化価値があり、 本節の禁止対象ではない。 禁止されるのは **未確認の選択肢を Claude 側で固定化する** 表現のみ。

## 3. issue 起票時の詳細化

issue を起票する場合、 **実装時に判断や疑問点が発生しないように** issue 起票前 / 起票時に `AskUserQuestion` で詳細化する。 issue 駆動開発の前提として、 issue body は後続 session の AI agent が実装する際の **唯一の信頼ソース** になるため、 Claude の独断が最も強く固定化される局面である。 **「issue body はユーザが承認した契約書」** と捉え、 未承認の選択 / 暗黙の推奨を絶対に混入させない。

- 起票内容は **issue body に全埋め込み** する。 補助 file (`.claude/issues/N.md` 等) には書かない
  - 目標: `gh issue view <N>` 1 発で、 別 session の Claude が完全 self-contained に実装着手できる
  - 推奨 template: 背景 / 受入基準 / I/O 契約 / 制約 / 想定 file / 関連 issue
- 起票後に issue を pick up した時点で不足が判明した場合は、 追加質問してから実装に入る (= 起票時の壁打ちが不完全だった場合のリカバリ)

### 3.1 起票直前 / pick up 時の self-check

`gh issue create` のコマンドを組み立てる、 または body 用 heredoc / file を書き始める **直前** に、 以下を点検する。 1 つでも該当したら body 作成を中断し、 該当論点を `AskUserQuestion` の選択肢に変換してユーザに発行する:

- セクション 2.1 の **禁止表現** (推奨マーク / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち / 「とりあえず」 / 暫定マーク) が draft に含まれていないか
- **受入基準** に「A 案で実装されていること」 のような、 ユーザ未承認の選択を完了条件として埋め込んでいないか (= 受入基準は強い拘束力を持つため、 ここへの独断 leak は最も巧妙)
- 別 session の Claude が `gh issue view <N>` だけを読んで実装した場合、 ユーザが意図しない案を「既決事項」 として読み取る余地が残っていないか
- 「2 通り考えられる」 「いずれかを選ぶ」 のような **未決定表現** を body に残していないか (= 残すなら起票前に `AskUserQuestion` で潰す)

**遡及適用**: pick up 時に issue body 内へ過去 session が埋め込んだ禁止表現 (推奨マーク / 暫定マーク / 「とりあえず」 等) を発見した場合も同様に追加質問してから実装に入る (= 過去の自分または別 session の独断を既決事項として継承しない)。 これはセクション 6 の自律作業中判断境界とも整合する。

### 3.2 PR 説明 / plan / commit への適用

本節の規律は issue body 限定ではない。 PR 説明、 plan ファイル (`.claude/plans/`)、 commit message にもセクション 2.1 の禁止表現を持ち込まない:

- PR 説明に「A 案で実装した。 B 案も検討したが ... の理由で A にした」 のような **未承認の独断正当化** を書かない (= 比較検討が必要なら PR を draft に戻して `AskUserQuestion` で詰め直す)
- plan に「Option A / Option B」 を併記したまま実装に進まない (= plan 確定時点で 1 案に絞る)

## 4. issue の粒度と関係性

- 1 issue は **独立して並列で作業できる粒度** で起票する。 1 PR で閉じられないほど大きい場合は **sub-issues に分割** する
- issue 間の関係性は以下を両方併用する:
  - **(a) sub-issue 親子リンク**: GitHub の sub-issue 機能 (UI または `gh sub-issue` 拡張) で親子を張る
  - **(b) 本文中の `#N` 相互参照**: issue body に `関連: #12, #13` のように記載する (GitHub が自動で双方向リンクを生成する)

## 5. PR 作成時の closing keyword

PR が issue を **完全に解決** する場合、 PR body に closing keyword を書いて issue が auto-close されるようにする。

- 有効なキーワード (9 種、 case-insensitive): `close` / `closes` / `closed` / `fix` / `fixes` / `fixed` / `resolve` / `resolves` / `resolved`
- 推奨形式: `Closes #<N>` (colon 有無は GitHub parser がどちらも受理するが、 表記は `Closes #N` で統一)
- **PR title では reference は作るが close 動作しない**。 必ず PR body に書く
- **部分対応** (issue 全体ではなく一部のみ解決する PR) では closing keyword を使わず、 `Refs #N` / `Part of #N` と書いて issue は手動 close に残す
- cross-repo の close は `owner/repo#N` 形式が必要

## 6. 自律作業中の判断境界

実装フェーズに入ったら、 以下の規律で判断する (`permission_mode` に依らず適用):

- **設計 / 仕様レベルの事項 (= issue 起票時の壁打ちで決まっているはずの内容) を再確認しない**。 issue body を信頼して進める
- 以下の場合は一度止まる:
  - issue に明記されていない要件を発見した場合 (= 起票時の壁打ちで見落とされた事項) → 追加で `AskUserQuestion` で確認する
  - 既存実装と矛盾する判断が必要で、 後戻りコストが大きい場合 → ユーザに方針確認する
- 軽微な判断 (変数名 / import 順 / docstring の有無 / 関数を 1 個か 2 個に分けるかなど局所的内部分割) は逐一確認しない
  - `permission_mode == "auto"` のときは auto mode の reasonable assumption 規範に従う
  - それ以外の mode では、 確認が必要な操作 (tool 起動など) は harness が permission prompt として自動的に挟むので、 Claude 側で追加の躊躇は不要

## 7. 連続 issue 解決時の排他制御 (claim comment + branch push の二段排他)

`/goal` のように **複数 issue を順次解決するフロー**、 もしくは同じ repo で **他 session が並列稼働している可能性がある場面** では、 同 issue への重複着手と他 session の作業破壊を防ぐため以下の手順を必ず守る。

GitHub API には真の atomic compare-and-swap がほぼ無いため、 `ai:in-progress` ラベル単独運用では TOCTOU race が残る (= 「ラベル確認 → ラベル付与」 の間に他 session が割り込む)。 そこで以下 2 つの確定的な排他基盤を併用する:

- **claim comment**: GitHub comment の serial ID + timestamp で先着判定 (= 早期 detection)
- **branch push**: git server-side で同名 branch は 1 つしか存在できず、 並列 push の片方は確定的に fail する (= 最終確定)

### 着手手順

以下を上から順に実行する:

1. **早期判定**: `gh issue view <N> --json labels,comments` で確認
   - `ai:in-progress` ラベル付与済 or 未削除の claim comment 存在 → **撤退** (= 別 issue 候補をユーザに提示するか、 別 issue に切替え)
   - いずれも無ければ次のステップへ

2. **claim comment を投稿** (排他基盤 1: comment 先着判定):
   ```
   gh issue comment <N> --body "🔒 ai:claim branch=<prefix>/issue-<N>-<slug> ts=<UTC ISO 8601>"
   ```
   - branch 名は次ステップで使う予定の名前を先に決めてここに埋め込む (= claim と branch を 1:1 で対応させる)
   - branch 名規約: `<prefix>/issue-<N>-<slug>` (`<prefix>` = `feat` / `fix` / `chore` / `docs` / `refactor` 等、 `<slug>` = issue タイトルから kebab-case で抽出した短縮形)
   - 例: `feat/issue-12-add-auth`, `fix/issue-25-null-deref`

3. **3 秒待機**: 他 session の claim comment が到着する余裕を確保 (`sleep 3`)

4. **comment 再取得 + 先着判定**: `gh issue view <N> --json comments` で comment 一覧を再取得
   - 自分の claim より **timestamp が古い別 session の claim comment** が存在 → **競合発生**。 自分の claim comment を削除して撤退:
     ```
     gh api -X DELETE /repos/<owner>/<repo>/issues/comments/<comment-id>
     ```
   - 存在しなければ次のステップへ

5. **作業 branch 作成 + 即 push** (排他基盤 2: branch 名 uniqueness の確定判定):
   ```
   git switch -c <prefix>/issue-<N>-<slug>
   git commit --allow-empty -m "wip: claim issue #<N>"
   git push -u origin <prefix>/issue-<N>-<slug>
   ```
   - push **失敗** (= 同名 branch 既存) → 他 session が先着していた (claim comment 経路では検知できなかったケース)。 自分の claim comment を削除 + ローカル branch を削除して撤退
   - push **成功** → **独占権確定**

6. **ラベル付与** (人間向けの目印として補助運用): `gh issue edit <N> --add-label ai:in-progress`

7. 通常の implementation フローへ移行 (= draft PR 作成 → 実装 → after 系の commit→push→PR→merge 自走)

### ラベル削除規律 (誤削除事故防止)

- `ai:in-progress` ラベルは **対応する PR が merge された時のみ削除する** (= issue 完了時)
- 着手中断 / 撤退時はラベルを残し、 claim comment と branch のみ削除する
  - ラベルを残す理由: 「中断したが復帰予定」 の状態が人間に見える + 後続 session が `ai:in-progress` を見て撤退 → 二重着手の保険として機能
  - 古い stale なラベルは人間が判定して手動削除する運用に委ねる
- **他 session の claim comment / branch / ラベルは絶対に削除しない**
- 「自分の claim か」 の判定基準: claim comment 本文の `branch=` 値が **自分が今いる作業 branch と一致するか**
  - 一致 → 自分の claim、 削除可
  - 不一致 → 他 session の claim、 削除禁止

### 撤退時のクリーンアップ手順

撤退判定 (step 1, 4, 5 のいずれか) が出たら以下を実行:

1. 自分の claim comment を削除: `gh api -X DELETE /repos/<owner>/<repo>/issues/comments/<comment-id>`
2. 自分が作った branch があれば削除: `git switch master && git push origin --delete <branch> && git branch -D <branch>`
3. ユーザに撤退理由を **1 行で必ず報告** する (例: 「issue #12 は他 session が先着のため撤退しました」)。 auto mode 中でもこの報告は省略しない (= ユーザが進捗状況を把握できなくなるため)

### よくある誤操作 (= 過去事例) と回避

- **誤着手**: 「ラベル確認 → ラベル付与」 だけで判定したため race condition で同 issue に複数 session が着手 → step 2-5 の二段排他で防ぐ
- **ラベル誤削除**: 「ラベル単独だと誰が付けたか不明」 でつい削除 → claim comment の `branch=` 値で持ち主を識別、 自分のものでなければ触らない
- **撤退時の clean-up 忘れ**: claim comment が残ったまま次の issue へ進む → ゴーストの claim が後続 session の撤退判定を誤らせる → step 1-3 を必ずセットで実行
EOF
)

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'

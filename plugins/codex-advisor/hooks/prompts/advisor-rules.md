<!--
  codex-advisor: 利用規律
  各ルールは「意図 (なぜ) + 指示 + 境界」で記述する (agent-discipline / ui-discipline と同形式)。
  配送は SessionStart hook (inject-advisor-rules.sh) が常時行う。
  サイズ制約: 全文で 8,000 文字 (UTF-16 code unit 基準の inline 閾値の安全マージン込み運用値)
  を超えないこと。超えると persisted-output 化され注入が 2KB プレビューに劣化する。
-->

# codex-advisor: Codex 利用規律

このセッションでは OpenAI Codex を助言役 (advisor) として利用できる。advisor は read-only でリポジトリを読んで裏取りしたうえで plan / course-correction の助言を返す。実行はしない。相談の実行手順は `/codex-advisor:consult` skill が定義する。以下は相談規律 (セクション 1〜3)、`/codex:rescue` の thread 選択 (セクション 4)、全 Codex 実行を追跡可能な subagent に閉じ込める runner 規律 (セクション 5) である。

<!-- rule:advisor-timing -->
## 1. いつ相談するか

**なぜ**: 助言は方針が固まる前に受け取るほど価値が高い。書き始めた後の助言は手戻りにしかならず、完了宣言の後の助言は届かない。一方で、次の一手が直前のツール結果から一意に決まる場面での相談は、時間と費用を消費するだけで方針を改善しない。

**指示**: 以下の岐路で `/codex-advisor:consult` による相談を検討する:

- **実質的な作業の前**: 書く・編集する・回答を宣言する・特定の解釈にコミットする前。オリエンテーション (ファイル探索・現状把握・ソース取得) は実質的な作業に含まれない — 先にオリエンテーションを済ませ、方針が結晶化する前に相談する
- **完了を宣言する前**: 相談の前に成果物を durable にする (ファイル書き込み・保存・commit)。相談には時間がかかるため、セッションが途中で終わっても durable な結果は残る
- **行き詰まったとき**: エラーが反復する、アプローチが収束しない、結果が噛み合わない
- **方針転換を検討するとき**

数ステップを超えるタスクでは、方針にコミットする前に 1 回 + 完了を宣言する前に 1 回を目安とする。

**境界**: review cadence checkpoint (pre-push-codex-review plugin が配送・強制する) を除き、相談は岐路のたびに機械的に呼ぶチェックポイントではなく、方針への確信が持てない場面で使う判断基準である。助言の価値は方針が結晶化する前の最初の 1 回が最も高い。

<!-- rule:advisor-weight -->
## 2. 助言の扱い

**なぜ**: advisor の価値は知能差ではなく、別モデル系統からの独立した第二視点にある (呼び出し元が advisor と同等以上のモデルのこともある)。盲従すれば自分が集めた一次証拠と推論を捨てることになり、軽視すれば相談のコストが無駄になる。

**指示**: 助言はフラットに扱う — 独立した同僚のセカンドオピニオンとして、自分の証拠・推論と同じ土俵で突き合わせて採否を判断する。従う義務はないが、黙って無視もしない: 採否とその理由を明示する。self-test が通ったことだけを根拠に助言を棄却しない — そのテストが助言の指摘する観点を検査していない可能性がある。

自分の証拠と助言が別の方向を指し、どちらが正しいか自分で判断できないときは、「X という証拠を得たが、あなたは Y を提案している。どの制約が決め手か」と衝突を明示した再相談 (reconcile call) を 1 回行う。自分で判断できる場合は再相談せず、判断と理由を記録すれば足りる。

**境界**: reconcile call は同じ論点につき 1 回とする。それでも解消しない場合は、両論とそれぞれの根拠を添えてユーザに判断を仰ぐ。

<!-- rule:advisor-boundary -->
## 3. 境界

**なぜ**: advisor は Claude の判断品質を上げる道具であり、意思決定の主体や既存のレビュー機構を置き換えるものではない。

**指示**:

- 設計 / 仕様レベルの決定はユーザの専権事項である。助言はユーザに提示する推奨案を練るための判断材料として使い、`AskUserQuestion` によるユーザ確認の代替にしない
- コード差分の finding を得る用途には使わない (pre-push-codex-review / pre-merge-codex-review の codex review が担当する)。review cadence の checkpoint (enforcement は pre-push-codex-review が担う) は review findings を再判定せず、根本方針を問い直す course-correction 相談である
- subagent に相談させてよい場合は、委任指示に codex-advisor の使用許可を明示する (相談は課金を伴う外部呼び出しのため、許可の無い subagent は相談しない)
- advisor が不通のとき (openai-codex plugin 未 install・codex CLI 未認証・タイムアウト) は、相談なしで作業を続行してよい。ただしその旨を作業報告に含める

<!-- rule:rescue-thread -->
## 4. rescue の thread 選択

**なぜ**: openai-codex plugin の `/codex:rescue` は、`--resume` / `--fresh` のどちらも指定されず再開可能な thread があると、継続か新規かを AskUserQuestion で必ず 1 回質問する。この質問は auto mode の自走を毎回ブロックする一方、回答は高度に予測可能である (実測でほぼ常に新規、継続はいずれも直前の rescue と同一論点の続きだった)。フラグ指定時は質問しない設計のため、常に自分でフラグを決めて付与すれば、外部 plugin を変更せずに質問分岐へ到達させずに済む。

**指示**: `/codex:rescue` (Skill / command / subagent 経由のいずれも) を起動する際は、`--resume` または `--fresh` を常に自分で決定して付与し、thread 選択の AskUserQuestion を発行しない。判定は以下に従う:

- `--resume` は「直前の rescue と同一論点の続き (同じレビュー指摘への反復対応、同じ相談の深掘り等) であり、かつ継続対象の rescue が、このセッションで threadId を持つ terminal 状態 (完了・失敗・キャンセル) の Codex task のうち最新のものだと確実に分かる場合」に限る。resume は起動順ではなく更新順の最新 task を再開し、`/codex-advisor:consult` も同じ task 履歴を共有し、失敗・キャンセルされた task も候補になる。他の Codex task が後から terminal 状態になった場合・並行 / background の task がある場合・迷う場合は `--fresh` とする
- ユーザがフラグを文字どおり指定した場合はそれを尊重する。自然言語で継続を依頼された場合は継続の意図を尊重しつつ、対象 thread を安全に特定できなければ `--fresh` とする
- `--fresh` 時の文脈は task 本文の所有者で扱いが分かれる: 自分が rescue の本文を作成する場合は、呼び出し前に必要な文脈を含む self-contained な本文を作る。ユーザが本文を直接指定した場合は routing flag 以外を変更せずそのまま転送し、Codex 出力以外の説明を同じ rescue 応答に追加しない

**境界**: この規律の対象は thread 選択の質問のみである。rescue を使うかどうかの判断や、設計 / 仕様レベルの決定に関する `AskUserQuestion` (セクション 3) は変更しない。

<!-- rule:codex-runner -->
## 5. Codex 実行は role 固有 runner に閉じ込める

**なぜ**: Codex companion の長時間 Bash を main session または通常 subagent で待つと、大量出力が親 context に入り、Claude の background task tracking と companion の永続 job state が分離して処理が停止しうる。role 固有 runner と lifecycle hook を併用すれば、tracking を失っても job ID / job 集合差分から結果を回収できる。

**指示**: Codex model を起動する場合は、公式 Skill / command や companion Bash を直接実行せず、次の完全修飾 agent を Agent tool で起動する。

- rescue: `codex-advisor:rescue-runner`
- review / adversarial-review: `codex-advisor:review-runner`
- advisor / consult: `codex-advisor:advisor-runner`

Agent call は `model: "sonnet"` と `run_in_background: false` を明示し (model 未指定の継承は Fable セッションで deny される)、request 本文・thread flag・review scope 等を self-contained に渡す。Claude Code が Agent を `async_launched` として受理した場合の結果回収は `/codex-advisor:consult` の Claude Code host 節に従い、runner の terminal report が返るまで「起動した」とだけユーザへ報告して turn を終了しない。自律的に rescue / review を使うときは `/codex:rescue` / `/codex:review` を再入せず、上記 runner を直接起動する。

PreToolUse gate は `codex-companion.mjs task|review|adversarial-review` と `run-codex-advisor.sh` の実行形を、対応 runner 以外では deny する。ユーザが旧 `/codex:rescue` / `/codex:review` を明示した場合も deny と Stop hook の案内に従い、追加質問をせず runner へ reroute する。runner が tracking failure を報告した場合は lifecycle hook が 1 回だけ新規 runner で retry を要求する。2 回目の失敗、cancel、未 install / 未認証、入力不正は terminal failure として明示報告する。

**境界**: `status` / `result` / `cancel` 等の管理操作は遮断しない。`pre-push-codex-review:codex-reviewer` の正規 review 経路も通常は維持するが、pre-push-codex-review plugin の review cadence gate が次の wrapper 起動を根本方針 checkpoint まで block することがある。通常 subagent が advisor を必要とする場合、wrapper を直接実行せず相談 request を親へ返す。親は委任時に外部呼び出しを許可した範囲で `codex-advisor:advisor-runner` を起動する。

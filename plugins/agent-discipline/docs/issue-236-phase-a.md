# issue #236 Phase A: 注入ペイロード分割の設計契約

本ドキュメントは issue #236 (agent-discipline: 注入ペイロードを分割・削減して persisted-output 化を解消する) の Phase A (設計記述 commit) であり、Phase B (実装本体) の完了時に削除する。Phase B はこのドキュメントに書かれた契約をそのまま実装する。

## 1. 背景と目標

Claude Code の hook additionalContext は約 9〜10K 文字/要素 (要素 = 1 hook コマンドの 1 出力) を超えると inline 配送されず、2KB プレビュー + 退避ファイルパスの persisted-output に劣化する。v0.14.0 時点の実測文字数:

| 分岐 | 構成 | 合計文字数 |
|---|---|---|
| fable | always-fable.md (5,505) + 見出し + discipline-preamble-fable.md (160) + discipline-fable.md (4,380) | ≈10.1K (超過) |
| sonnet / その他 | always-sonnet.md (16,026) + 見出し + discipline-sonnet.md (5,973) | ≈22.1K (超過) |
| 判定不能 | preamble-self-gate.md (883) + always-sonnet.md + 見出し + discipline-preamble-self-gate.md (958) + discipline-sonnet.md | ≈23.9K (超過) |
| one-shot 補正 (Fable 確定) | prefix + always-fable.md + 見出し + discipline-preamble-fable.md + discipline-fable.md | ≈10.3K (超過) |

目標: 全分岐・全経路で **1 要素 8,000 文字以下** (閾値に対する安全マージン込み) に分割する。ルール本文の削減は行わない (意味変更リスクを排除するため。分割のみで 8K 以下を達成できることを実測済み)。

## 2. 分割後の要素構成

always-sonnet.md (16,026 字) を rule 境界で 3 分割する (実測: intro 413 / rule1 1,640 / rule2 2,764 / rule3 1,985 / rule4 609 / rule5 925 / rule6 778 / rule7 5,008 / rule8 834 / rule9+footer 1,070):

| 新ファイル | 内容 | 本文文字数 (概算) |
|---|---|---|
| `hooks/prompts/always-sonnet-1.md` | ヘッダコメント + 見出し (part 1/3 表記) + intro + rule 1〜2 | ≈4.9K |
| `hooks/prompts/always-sonnet-2.md` | 見出し (part 2/3 表記) + rule 3〜6 | ≈4.4K |
| `hooks/prompts/always-sonnet-3.md` | 見出し (part 3/3 表記) + rule 7〜9 + footer (steering 文) | ≈7.0K |

`always-sonnet.md` は削除する。各 part の冒頭には「本メッセージは常時適用ルール (Sonnet 版) の part n/3 であり、全 part が揃って 1 つのルールセットを構成する」旨の 1〜2 文を置く (分割により文書の先頭・末尾が欠けて見えることを防ぐ)。rule 本文・rule ID マーカー (`<!-- rule:<id> -->`) は無変更で移設する。

配送要素のマトリクス (すべて ≤8K):

| 分岐 | SessionStart (1 要素) | UserPromptSubmit one-shot (各 1 要素) |
|---|---|---|
| fable | delivery-note + always-fable.md (≈5.9K) | 分業規律 fable 版 (≈4.7K) |
| sonnet / その他 | delivery-note + always-sonnet-1.md (≈5.3K) | always-sonnet-2.md (≈4.4K) / always-sonnet-3.md (≈7.0K) / 分業規律 sonnet 版 (≈6.1K) |
| 判定不能 | delivery-note + preamble-self-gate.md + always-sonnet-1.md (≈6.2K) | self-gate 行 + always-sonnet-2.md (≈4.6K) / self-gate 行 + always-sonnet-3.md (≈7.2K) / discipline-preamble-self-gate.md + discipline-sonnet.md (≈7.1K) |
| 判定不能 → Fable 確定補正 | — | resolve-model-on-prompt.sh: prefix + always-fable.md (≈5.8K) / inject-discipline.sh: 補正前置き + 分業規律 fable 版 (≈4.9K) |

- 「分業規律 fable 版」 = 見出し + discipline-preamble-fable.md + discipline-fable.md
- 「分業規律 sonnet 版」 = 見出し + discipline-sonnet.md
- delivery-note = 新規ファイル `hooks/prompts/delivery-note.md` (≈400 字以下)。内容: (a) 常時適用ルールと分業規律は複数の注入メッセージに分割して配送されること、(b) 残りの要素は最初のユーザプロンプト処理時に注入されること、(c) それらが本セッションの context に見当たらない場合 (context 圧縮直後の自動継続中など) は、inject 元の prompts ディレクトリ配下の該当ファイルを Read して自己修復すること。inject-always.sh が実行時に prompts ディレクトリの絶対パスを 1 行付加する
- **8K ガード (SessionStart 要素)**: prompts ディレクトリの実パスは実行環境依存で長さが非有界のため、inject-always.sh は additionalContext を **最終的に組み立てた後の全文** に対して文字数を計測し、8,000 を超える場合は (i) 実パス行を落として再計測、(ii) それでも超える場合は delivery-note 全体を落として再計測、の順で段階的に縮退する。self-gate 前置きとルール本文はいかなる場合も落とさない (state 書込失敗時の安全床が self-gate であるため。§4.1)。ルール本文 + self-gate のみで 8,000 を超えないことは §9 の静的検証で保証する。delivery-note を落とした場合、compact 直後ギャップの自己修復手段 (§8) が失われるが、これは劣化モードとして許容する

## 3. イベント設計と順序保証

- 同一 event に登録された複数 hook コマンドは並列実行され、additionalContext の到着順序は保証されない (実測)。したがって **要素間の順序依存を撤廃し、各要素を self-contained にする** (part n/3 表記・要素ごとの自己ゲート前置き)
- SessionStart → 最初の UserPromptSubmit の順序はイベントのライフサイクル上保証される。モデルの最初の推論は最初のユーザプロンプト後に発生するため、モデル視点では初回 turn までに全要素が揃う
- 旧設計の制約「分業規律ブロックは additionalContext の末尾に置く」は、自己ゲートの射程が要素 (メッセージ) 単位に閉じることで不要になり、撤廃する

## 4. スクリプトの I/O 契約

state ディレクトリは現行と同じ `${TMPDIR:-/tmp}/agent-discipline-state`、session_id の sanitize も現行方式 (`tr -cd 'A-Za-z0-9._-'`) を踏襲する。

### 4.1 inject-always.sh (改修)

- モデル判定 fallback chain (stdin.model → transcript 解析 → state file → 判定不能) は現行のまま、**このスクリプトだけ** が実行する (判定ロジックの単一実行の維持)
- state file (`model-<sid>`) の書き込みを atomic 化する (同 dir に temp file を書いて `mv` する)。分割後は state が後続スクリプトの必須入力 (IPC) になるため、部分書き込みの読み取りを構造的に排除する
- **state 書込の成否を確認する**: モデルが確定したのに atomic 書込 (temp 書込または `mv`) が失敗した場合、旧 state (別モデル時代の値) が残存したまま後続スクリプトに読まれる恐れがある。この場合は pending マーカー (`pending-model-<sid>`) の作成にフォールバックし、判定不能セマンティクス (self-gate 付き配送 + resolve-model-on-prompt.sh の one-shot 補正) に縮退する。注入本文も判定不能分岐と同じもの (preamble-self-gate.md + always-sonnet-1.md) に切り替える。pending の作成にも失敗した場合は現行どおり注入のみ継続する (§8 の床)
- state 書込が成功した場合のみ、過去の判定不能 SessionStart が残した pending マーカーを削除する (現行の掃除処理に成否条件を追加)
- 配送済みマーカー (`delivered-rules-2-<sid>` / `delivered-rules-3-<sid>` / `delivered-discipline-<sid>`) を毎回削除する (SessionStart は startup / resume / clear / compact のたびに発火するため、全要素が再配送される。現行の「SessionStart ごとに全文再注入」と同じ再配送セマンティクス)
- 注入要素: delivery-note (+ prompts dir 実パス行) + 分岐別の part1 本文 (fable: always-fable.md 全文 / sonnet・その他: always-sonnet-1.md / 判定不能: preamble-self-gate.md + always-sonnet-1.md)。組み立て後に §2 の 8K ガードを適用する
- fail-open 条件は現行を踏襲 (jq 不在 / 不正 stdin / prompts 読取不能で無音終了)。delivery-note が読めない場合は delivery-note 無しで part1 のみ注入する (ペイロード単位の fail-open)

### 4.2 inject-rules-part.sh (新規、UserPromptSubmit)

- 引数: part 番号 n (2 または 3)。hooks.json から `inject-rules-part.sh 2` / `inject-rules-part.sh 3` として 2 登録する。引数が 2 / 3 以外・欠落の場合は無音 exit 0
- マーカー `delivered-rules-<n>-<sid>` が存在すれば即 exit 0 (毎プロンプトのオーバーヘッドはファイル存在チェックのみ)
- マーカー不在時、**pending (`pending-model-<sid>`) を最優先で読み、確定 state (`model-<sid>`) は次点** とする (§5 の優先規則。pending は「state が未確定または stale で信頼できない」ことの目印であるため):
  - pending あり → 自己ゲート行 (part 用の短い前置き。§6) + always-sonnet-<n>.md を注入し、マーカーを書く (state の有無・内容は見ない)
  - pending 無し + state が fable を含む → 配送不要 (fable は part1 = always-fable.md 全文で完結)。マーカーのみ書く
  - pending 無し + state が非 fable → always-sonnet-<n>.md を注入し、マーカーを書く
  - pending も state も無し (SessionStart hook が失敗した異常系) → 判定不能と同じ自己ゲート付き配送にフォールバックし、マーカーを書く (規律が届かないまま session が進む方が危険、という保守側の倒し方)
- マーカーの書き込みは **注入本文と出力 JSON の生成に成功した後** に行う (先にマーカーを書くと、本文読取失敗時に当該要素が session 中永久欠落する)
- fail-open: jq 不在 / 不正 stdin / prompts 読取不能で無音終了 (マーカーは書かない = 次プロンプトで再試行)

### 4.3 inject-discipline.sh (新規、UserPromptSubmit)

- マーカー `delivered-discipline-<sid>` は 3 状態を内容として持つ: 無し / `sonnet-gate` / `final`
- マーカー無し: pending 優先・state 次点 (§5 の優先規則) で分岐:
  - pending あり → discipline-preamble-self-gate.md + discipline-sonnet.md を注入、マーカー `sonnet-gate` (state の有無・内容は見ない)
  - pending 無し + state が fable → 分業規律 fable 版を注入、マーカー `final`
  - pending 無し + state が非 fable → 分業規律 sonnet 版を注入、マーカー `final`
  - 両方無し → pending 時と同じ自己ゲート付き配送、マーカー `sonnet-gate`
- マーカー `sonnet-gate`:
  - pending 無し + state が fable に確定していた → 補正前置き (1〜2 文。自己ゲート付きで配送済みの Sonnet 版分業規律を破棄し本要素を優先する旨) + 分業規律 fable 版を注入、マーカー `final` に更新
  - pending 無し + state が非 fable に確定していた → 注入なしでマーカー `final` に更新 (配送済みの Sonnet 版が確定内容そのもの)
  - pending あり、または state 無し → 何もしない (次プロンプトで再確認)
- マーカー `final`: 即 exit 0
- マーカー書き込みタイミングと fail-open は inject-rules-part.sh と同じ方針。マーカー更新も temp + `mv` の atomic 書き込みとする

### 4.4 resolve-model-on-prompt.sh (改修)

- pending 判定・transcript 解析・state 書込 → pending 削除の順序 (TOCTOU 回避) は現行のまま
- **state 書込 (atomic 化する) の成否を確認する**: 書込に失敗した場合は pending を削除せず、注入もせずに無音 exit する (次プロンプトで再試行。stale state を残したまま pending を消すと後続スクリプトが誤った変種を確定配送するため)
- Fable 確定時の再注入ペイロードから **分業規律ブロックを外す** (prefix + always-fable.md のみ ≈5.8K)。分業規律の Fable 補正は inject-discipline.sh の `sonnet-gate` → `final` 遷移が担う
- prefix の文言は「常時適用ルールの確定版」への言及に更新する (分業規律の補正が別要素で届くことに触れる)

### 4.5 変更しないもの

- block-fable-subagent.sh (state file の形式・パスは不変)
- inject-auto.sh / check-uncommitted-on-session-start.sh / inject-temporary.sh / inject-subagent-rules.sh
- discipline-fable.md / discipline-sonnet.md / discipline-preamble-fable.md (本文無変更)
- always-fable.md (本文無変更)

## 5. 状態ファイル一覧 (分割後)

| ファイル | 書き手 | 読み手 | ライフサイクル |
|---|---|---|---|
| `model-<sid>` | inject-always.sh / resolve-model-on-prompt.sh (atomic) | 全 inject 系 + block-fable-subagent.sh | 確定値のキャッシュ。session をまたいで残置 (現行どおり) |
| `pending-model-<sid>` | inject-always.sh (作成) / 両 resolver (削除) | UserPromptSubmit 系 | 判定不能の目印。確定時に削除 |
| `delivered-rules-{2,3}-<sid>` | inject-rules-part.sh | 同左 | at-most-once 配送の目印。inject-always.sh が SessionStart ごとに削除 |
| `delivered-discipline-<sid>` | inject-discipline.sh | 同左 | 3 状態 (`sonnet-gate` / `final`)。inject-always.sh が SessionStart ごとに削除 |

書き手が単一 (要素ごとに専用マーカー) のため、同一 event 内の並列実行でマーカーの書き込み競合は発生しない。state (`model-<sid>`) の読み手は複数だが、atomic 書き込みにより部分読み取りは発生しない。

**優先規則 (UserPromptSubmit の配送スクリプト共通)**: `pending-model-<sid>` が存在する間は `model-<sid>` を信頼しない (pending 優先・state 次点)。理由: state は session をまたいで残置され、/model 切替や書込失敗によって「現在のモデルと異なる旧値」が読める状態になりうる。pending は inject-always.sh / resolve-model-on-prompt.sh が「モデル未確定または state 更新失敗」を表明する目印であり、pending 存在下での確定配送を防ぐ。副作用として、resolve-model-on-prompt.sh が state 書込 → pending 削除を完了する前に並列の配送スクリプトが pending を読んだ場合、その要素は self-gate 付き変種で配送されるが、これは誤配送ではなく §8 の補正遅延 (最大 1 プロンプト) に含まれる。

なお block-fable-subagent.sh (検知層) は現行どおり state のみを参照する (本 issue では変更しない)。stale state による検知層の fail-open 露出は v0.14.0 以前から存在する既知の事象であり、pending マーカー認識による検知層補強は #200 のスコープである。

## 6. self-gate 境界記述の再設計

旧: 「直前の『# agent-discipline: 分業規律』で始まる見出しからメッセージ末尾まで」という位置依存の射程定義。分割後は要素の到着順序が保証されないため成立しない。

新: 各要素の自己ゲートは **その要素 (注入メッセージ) 内に射程を閉じる**:

- `preamble-self-gate.md` (書き換え): 射程を「本メッセージ内の常時適用ルールブロック (part 1/3)」に限定し、残り part にも同種の自己ゲートが付くこと・分業規律要素の適用可否は当該要素冒頭の前置きに従うことを記す。「Fable の場合は要点読み + one-shot 補正を待つ」の指示は維持する
- part 2/3 用の自己ゲート行 (新規 `hooks/prompts/part-self-gate.md`、≈200 字): 「(自己ゲート) モデル判定不能のため Sonnet 書式の常時適用ルール part n/3 を暫定配送している。モデルに依らず適用する。Fable の場合は要点読みでよい (確定版が one-shot 補正で届く)」の趣旨。part 番号はスクリプトが置換せず、本文を part 非依存の文言にする
- `discipline-preamble-self-gate.md` (書き換え): 射程を「本メッセージの分業規律ブロック」に変更。「Fable 確定時は Fable 版分業規律が後続の補正要素で再配送される (最大 1 プロンプト遅延)」を明記する

## 7. hooks.json / lint / CI / 参照の追従

- hooks.json:
  - UserPromptSubmit に 3 コマンド追加: `inject-rules-part.sh 2` / `inject-rules-part.sh 3` / `inject-discipline.sh`
  - description を新しい配送経路 (SessionStart part1 + UserPromptSubmit one-shot 残要素) に更新
  - 4 つの type:agent prompt 内の「hooks/prompts/always-sonnet.md セクション 2.1 / 3.1」参照を「hooks/prompts/always-sonnet-1.md セクション 2.1 / always-sonnet-2.md セクション 3.1」に更新 (4 entries 一律更新 = 共通ブロックの一致を維持)
- lint-prompt-sync.sh:
  - チェック 1: sonnet 側の ID 集合を 3 part ファイルの和集合として抽出するよう変更 (fable との完全一致検査は維持)
  - チェック 5: サブセット判定の母集合を同じ和集合に変更
  - pre-flight の存在チェック対象を 3 part ファイルに差し替え
  - part 間で rule ID が重複しないことの検査を追加する (和集合化で重複が隠れるため)
- .github/workflows/agent-discipline-prompt-lint.yml: paths の `always-sonnet.md` を 3 part ファイルに差し替え (pull_request / push 両方)
- **`always-sonnet.md` への全参照の移行**: `git grep -n 'always-sonnet\.md'` の全ヒットを個別に監査し、文脈に応じて移行する (機械的な一括置換はしない)。ヘッダコメント内の参照 (always-fable.md / discipline-sonnet.md / preamble-self-gate.md / subagent-rules.md 等) は rule 本文を変更せずファイル名参照のみ更新する。ID セット全体への言及 (「always-sonnet.md と完全一致」等) は「3 part の和集合」への言及に、特定セクションへの言及 (hooks.json の「セクション 2.1 / 3.1」等) は該当 part ファイルへの言及に書き換える。README のバージョン履歴など歴史的経緯としての言及は当時の事実の記述として残してよい (現行仕様と誤読されない文脈であることを確認する)。移行完了後に `git grep -n 'always-sonnet\.md'` を再実行し、残ヒットが意図した歴史的言及のみであることを検証して一覧を報告に含める
- README (plugin): inject-always / resolve-model-on-prompt 節の更新、新スクリプト 2 本の節を追加、ディレクトリ構成・既知の制約 (§8)・バージョン履歴 (v0.15.0) を追記。スクリプトのヘッダコメント・README の記述は分割後の失敗時セマンティクス (§4 の書込成否確認・§5 の優先規則) まで含めて更新する (ファイル名の差し替えだけで済ませない)
- version bump: plugin.json / marketplace.json / リポジトリ直下 README.md を 0.15.0 (minor) で同期

## 8. 既知の制約 (トレードオフとして受容し文書化する)

1. **compact 直後のギャップ**: SessionStart(source=compact) 後、次のユーザプロンプトまでは part1 要素のみが再注入され、残り要素は再配送されない (UserPromptSubmit はユーザプロンプトでしか発火しないため)。compact 後に agentic loop が自動継続する経路では、この間の推論は part1 の delivery-note (自己修復指示) と compact summary 内の痕跡に依存する。従来設計でも同経路では persisted-output (2KB プレビュー) しか届いていなかったため、劣化ではない
2. **判定不能 → Fable 確定の補正遅延**: 分業規律の Fable 補正は resolve-model-on-prompt.sh の state 書込と inject-discipline.sh の読み取りが同一 event 内で並列競合した場合、最大 1 プロンプト遅れて配送される (誤配送はしない)
3. **exactly-once は保証しない**: hook 出力に配送 ACK が無いため、マーカー書込後に配送が失われた場合の再送はできない (SessionStart での全マーカーリセットが回復手段)。逆に TMPDIR 掃除等でマーカーが消えた場合は再配送される (重複は無害)
4. **state / pending の両方が書けない持続障害下の床**: inject-always.sh で state 書込と pending 作成が両方失敗した場合 (TMPDIR が持続的に書込不能等)、後続スクリプトは旧 state (読めれば) または両不在フォールバックに基づいて配送する。/model 切替を跨いだ旧 state が残っていると誤ったモデル変種が配送されうるが、この露出は state 書込失敗を無視していた v0.14.0 以前にも存在する (検知層 block-fable-subagent.sh の同種の露出への補強は #200)
5. **pending 削除失敗時の補正遅延**: resolve-model-on-prompt.sh が state 書込に成功した後の pending 削除に失敗した場合、優先規則 (pending 優先) により分業規律の Fable 補正は次の SessionStart (マーカーリセット + pending 掃除) まで配送されない。常時ルールの Fable 確定版 (prefix + always-fable.md) は配送済みのため、規律の主要部は欠落しない

## 9. 受入検証手順 (Phase B で実施)

1. 各スクリプトに模擬 hook input JSON (fable / sonnet / opus / model 欠落の 4 分岐 × SessionStart / UserPromptSubmit) を stdin で与え、出力 JSON の additionalContext 文字数が全要素 8,000 以下であること・JSON 形状が現行と同一であることを実測する
2. マーカー/state のライフサイクルをシーケンスで実測する: 初回配送 → 再プロンプト時の no-op → SessionStart 再発火でのリセット → 判定不能 → Fable 確定補正 (rules + discipline) の一連
3. lint-prompt-sync.sh が pass すること (ID 和集合・重複検査を含む)
4. **静的サイズ検証**: 各分岐の「self-gate 前置き + ルール本文」(delivery-note と実パス行を除いた核部分) が 8,000 字以下であることを検証する (8K ガードの縮退が核部分に及ばないことの前提条件)
5. **8K ガードの動作検証**: 人工的に長い prompts ディレクトリパス (数千字) を与え、実パス行 → delivery-note の順で縮退し、核部分が維持されることを実測する
6. **書込失敗フォールバックの検証**: state ディレクトリを書込不能にした状態 (chmod 等) で inject-always.sh / resolve-model-on-prompt.sh を実行し、§4.1 の pending フォールバックと §4.4 の pending 残置 + 無音 exit を実測する
7. 新規セッションの transcript で SessionStart / UserPromptSubmit 注入が persisted-output にならないことを実測する (実セッションを起こせる場合)。起こせない場合は 1. の文字数実測を根拠とし、その旨を PR に記載する

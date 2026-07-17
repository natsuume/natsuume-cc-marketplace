# Fable risk rubric

## 目的

`model:prefer-gpt-5.6-sol` は「難しい issue」全般ではなく、Claude Fable 5 が実装・調査すると正規操作を誤って止める false deny / false block を作り込みやすい issue に付ける。現行 label description は「Claude Fable 5 の誤ブロック可能性が高いため GPT-5.6 Sol での作業を推奨」である。

この rubric は既存の付与例から抽象化した repository policy であり、Fable の能力に関する公式 benchmark ではない。既存 label は calibration evidence として読み、親子 issue から機械的に継承しない。

## 必要条件

`label` と判定するには、次の両方を満たす。

1. 下記の strong signal が 1 つ以上ある。
2. 正規操作の false deny、または safety gate の fail-open / fail-closed 境界を誤って設計する具体的な failure scenario を説明できる。

さらに、issue 本文、関連 code、実測、公式仕様のいずれかで scenario を裏付け、high confidence に到達していることを必須とする。

## Strong signals

### A. shell parser と command semantics

- quote、escape、heredoc、separator、subshell、option scope、cwd / target resolution を扱う shell parser や regex
- 文字列一致と実際の shell / git / gh semantics のずれが、正規コマンドの false deny または gate bypass を生む変更
- 複数の parser が同じ command を別々に再解釈し、判定結果が非対称になる変更

### B. guardrail の availability / safety 境界

- fail-open / fail-closed を切り替える hook、preflight、validator、permission gate
- 安全側に倒す修正が通常 workflow 全体を止める可能性と、緩和が bypass を作る可能性を同時に持つ変更
- label、marker、hash、target の同一性が崩れると、未検証操作を通すか正規操作を恒久 deny する変更

### C. lifecycle・concurrency・state machine

- lifecycle event の順序、resume、background completion、race、one-shot state、attestation、tombstone を扱う変更
- ある actor の cleanup が別 actor の state を破棄し、誤ブロックや誤許可を起こす変更
- timeout / retry / stale cleanup の境界が正規操作の継続可否を左右する変更

### D. provider・model・runtime の保証

- Claude / Codex / GitHub app / companion / hook payload の capability や identity 保証を比較する調査
- provider をまたぐ委任、model selector、認証、rate limit、tool surface の不足を前提に安全な fallback を設計する変更
- 当事者モデル自身の制約評価では独立性が弱く、公式 docs または実機 evidence との照合が必要な変更

### E. reviewer finding の検証と safety trade-off

- 外部 reviewer の未検証 finding を再現して、valid / invalid / needs-user-decision を分ける調査
- 防御強化が false deny を増やす一方、緩和が gate bypass を作るため、片側だけを採用できない変更

## High confidence の証拠

次のいずれかを 1 つ以上示し、issue の scenario と直接結び付ける。

- 正規入力での再現手順と観測結果
- 対象 code の具体的な分岐・parser・state transition
- 公式 runtime / API 仕様
- 同根の修正済み issue / PR と、今回にも適用できる理由

仮説だけ、関連 repository を読めない、外部仕様が不明、相反する証拠がある場合は `defer` とする。

## 単独では signal にしないもの

- P1 / P2 / P3 などの優先度。P1 であるだけでは label 対象外である。
- 実装が複雑、変更ファイルが多い、security に関係するという一般論。
- title や body に Codex、Fable という語があること。
- typo、通常の dependency update、局所的な定数変更、機械的な docs 整備。
- 親 issue、関連 issue、同じ plugin に label が付いていること。

docs-only issue でも、guardrail の保証境界や reviewer finding の扱いを変更して将来の false deny を誘発する場合は strong signal を別途満たし得る。docs-only という形式だけで除外しない。

## Calibration examples

- `#127`: quote-aware parser が確定した push segment を別の quote 非対応 regex が再探索し、誤った repository を検査する。A と B に該当する。
- `#128`: `jq` 不在を無条件 fail-open にしていた gate を直す際、依存不足だけを deny し無関係な command は通す境界が必要である。B に該当する。
- 「P1 の脆弱性を通常の library update で直す」だけなら、priority と security 以外の strong signal がないため label 対象外である。

## Decision

| 判定 | 条件 | Action |
|---|---|---|
| `label` | 必要条件を満たし high confidence | additive に label を追加 |
| `no-label` | strong signal または具体的 failure scenario が無い | 変更しない |
| `defer` | 証拠不足、仕様判断、相反する証拠 | 変更せず判断材料を報告 |

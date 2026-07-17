---
name: label-issues
description: Codex で GitHub issue と関連実装を調査し、Claude Fable 5 が正規操作を誤ブロックする可能性が高い作業だけを evidence-based に判定して、明示依頼時に model:prefer-gpt-5.6-sol ラベルを additive に付与する。「Fable では危険な issue を探す」「Sol 推奨ラベルを付ける」「model:prefer-gpt-5.6-sol を棚卸しする」依頼で使う
---

# Fable Risk Labeler

この workflow の判定主体は Codex とする。Claude / Fable session で読み込まれた場合は GitHub write を行わず、Codex で `$fable-risk-labeler:label-issues` を実行するよう案内して停止する。この制限は instruction contract であり、runtime が強制する hard security boundary ではない。

## 1. Scope を確定する

- repository はユーザー指定を優先し、無ければ現在の git remote から解決する。解決できなければ質問して停止する。
- issue 番号が指定されていればその集合だけを調べる。指定が無ければ open issue を対象にする。closed / all はユーザーが明示した場合だけ含める。
- pull request は issue 検索結果から除外する。
- 既に `model:prefer-gpt-5.6-sol` がある issue は再判定対象から外し、既存として集計する。

## 2. Read-only preflight を行う

1. connected GitHub app を優先し、利用できない場合だけ認証済み `gh` を使う。
2. repository の label 一覧から `model:prefer-gpt-5.6-sol` が存在することと description を確認する。ラベルが存在しない場合は作成せず、対象 repository と不足ラベルを報告して停止する。
3. candidate issue の title、body、現在の labels を取得する。判定根拠に必要な場合だけ comments、linked issue / PR、関連ファイル、公式 runtime docs を追加で読む。
4. [Fable risk rubric](references/fable-risk-rubric.md) を最初から最後まで読み、その基準だけで判定する。

取得失敗や repository code を確認できない状態を high confidence と扱わない。priority label、title の単語、既存の親 issue label だけから推測しない。

## 3. Evidence table を作る

各 candidate について次を 1 行にまとめる。

| Issue | Decision | Fable-risk mechanism | Evidence | Confidence |
|---|---|---|---|---|
| `#N` | `label` / `no-label` / `defer` | 想定する誤ブロック経路 | issue・code・実測・公式仕様 | `high` / `medium` / `low` |

- `label`: rubric の必要条件を満たし、high confidence のものだけ。
- `no-label`: 必要条件を満たさないことを確認できたもの。
- `defer`: 情報不足、仕様判断、相反する証拠があるもの。ラベルは付けない。

write 前に、table と exact target (`owner/repo`、issue 番号、label 名) をユーザーへ短く提示する。

## 4. Write boundary を守る

- ユーザーがラベル付与を明示的に依頼した場合だけ write する。「調査」「候補を挙げる」だけなら evidence table を返して終了する。
- `label` 判定の issue へ 1 件ずつ additive に追加する。connected GitHub app では `github_add_issue_labels`、fallback では `gh issue edit <N> --repo <owner/repo> --add-label model:prefer-gpt-5.6-sol` を使う。
- full label set を置換する `update_issue(labels=...)` は使わない。`remove_issue_label` と label 作成も本 Skill の scope 外とする。
- `no-label` / `defer`、PR、対象外 state、既に label 済みの issue は変更しない。
- 1 件の失敗を成功扱いせず、その issue のエラーを記録して残りの独立 target を続ける。

## 5. 結果を検証する

write 後に各 issue を再取得し、対象 label が追加され、既存 labels が保持されていることを確認する。最終報告には次を含める。

- 調査件数、`label` / `no-label` / `defer` / 既存 label の件数
- 実際に変更した issue 番号
- 失敗または未確認の target と理由
- 判定が semantic review に依存する instruction contract であり、将来の Fable 挙動を保証するものではないという限界

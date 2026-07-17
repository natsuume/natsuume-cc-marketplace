# fable-risk-labeler プラグイン

GitHub issue と関連実装を Codex で調査し、Claude Fable 5 が正規操作を誤ブロックする可能性が高い作業へ `model:prefer-gpt-5.6-sol` ラベルを付与する Skill を提供します。

## バージョン

v0.1.0

### v0.1.0

- 既存の label description と付与例から、shell parser、guardrail の fail-open / fail-closed、lifecycle / concurrency、provider / runtime 保証を strong signal とする rubric を定義した
- priority や複雑さだけでは label を付けず、具体的な false deny / safety boundary と high-confidence evidence がそろった issue だけを対象にした
- connected GitHub app の additive label API を優先し、`gh issue edit --add-label` を fallback とした。full label set の置換、label の作成・削除は行わない
- write 前の candidate table と target 明示、write 後の再取得による既存 label 保持確認を契約にした

## 利用方法

Codex で次の Skill を呼び出します。

```text
$fable-risk-labeler:label-issues
```

issue 番号を指定した場合はその集合だけを調査し、指定がなければ current repository の open issue を対象にします。調査だけを依頼した場合は candidate table までで停止し、ラベル付与が明示された場合だけ GitHub を更新します。

Claude / Fable session で Skill が見えた場合は write せず Codex での再実行を案内します。これは Skill の instruction contract であり、runtime による hard security boundary ではありません。LLM の semantic 判定品質、将来の Fable 挙動、GitHub service の可用性は保証しません。

## 依存

- connected GitHub app、または認証済み `gh` CLI
- 対象 repository に既存の `model:prefer-gpt-5.6-sol` label
- label を追加できる GitHub 権限

## 機能

| Skill | Codex invocation | 説明 |
|---|---|---|
| label-issues | `$fable-risk-labeler:label-issues` | issue と関連実装を evidence-based に調査し、明示依頼時だけ high-confidence target へ additive に label を付与する |

## スコープ外

- label の新規作成・削除
- 既存 `model:prefer-gpt-5.6-sol` label の妥当性を自動で剥がすこと
- priority label の変更
- pull request の分類

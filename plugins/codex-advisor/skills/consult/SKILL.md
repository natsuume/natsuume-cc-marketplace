---
name: consult
description: OpenAI Codex を advisor として相談し、plan / course-correction の助言を受け取る
user-invocable: true
when_to_use: |
  以下の場合に使用:
  - advisor-rules (SessionStart 注入) の相談タイミングに合致したとき (実質的な作業前・完了宣言前・行き詰まり・方針転換の検討時)
  - ユーザが「Codex に相談」「セカンドオピニオン」「Codex の意見を聞いて」等を求めたとき
  - 助言と証拠の衝突を解消する再相談 (reconcile call) を行うとき
---

# /codex-advisor:consult — Codex への相談

OpenAI Codex に相談プロンプトを渡し、助言テキストを受け取る。Codex は read-only sandbox で動作し、リポジトリを自分で読んで主張を裏取りできるが、ファイル変更・コマンドによる状態変更は行わない (助言のみ)。reasoning effort とモデルは wrapper 側で固定されており、呼び出し時に指定するものはない。

## 1. 相談プロンプトの組み立て

相談プロンプトは self-contained に書く (Codex はこの会話のコンテキストを一切持たない)。以下の XML ブロック構成に従う:

```
<task>
取り組んでいるタスクと現在の状況 (1〜3 文)。
</task>

<context>
- 試したこと・観測した証拠 (事実のみ、簡潔に)
- 関連ファイルの絶対パス (Codex は read-only でリポジトリを読める。ファイル本文の貼り込みではなくパス参照でよい)
</context>

<question>
相談したい具体的な質問を 1 つ。
</question>

<output_contract>
推奨方針・理由・リスク・次の一手を簡潔に述べる。目安 500 語以内。
</output_contract>

<grounding_rules>
主張は参照したファイル・観測した事実に接地させる。推測は推測とラベル付けする。確認できないことは確認できないと述べる。
</grounding_rules>
```

- 1 相談 1 質問。複数の論点があるときは別々の相談に分ける
- reconcile call (助言と証拠の衝突解消) では `<context>` に前回助言の要点と、それと衝突する証拠を明記し、`<question>` を「どの制約が決め手か」の形にする。Codex 側の thread 継続 (resume) には依存しない — 毎回 self-contained な新規相談として発行する

## 2. wrapper の起動

Bash ツールで以下を実行する。**timeout パラメータに 600000 (10 分) を明示指定し、foreground で実行する**。`run_in_background: true` は使わない — 助言を観察しないまま作業を進める経路を作らないため。

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-codex-advisor.sh" <<'EOF'
<task>
...
</task>
...
EOF
```

- stdout に Codex の助言テキストがそのまま返る。stderr は wrapper の状態メッセージ (companion パス・実行開始/終了)
- `${CLAUDE_PLUGIN_ROOT}` が空、または該当パスが存在しない場合は、plugin cache から最新版を解決して同じ heredoc で実行する (置換であって再試行ではない):

```bash
WRAPPER=$(find "$HOME/.claude/plugins/cache" -path '*codex-advisor*/scripts/run-codex-advisor.sh' -type f 2>/dev/null | awk -F'codex-advisor/' '{split($2,p,"/");split(p[1],v,".");if(length(v)==3)printf "%06d.%06d.%06d %s\n",v[1],v[2],v[3],$0}' | sort -r | head -1 | cut -d' ' -f2-) && [ -n "$WRAPPER" ] && bash "$WRAPPER" <<'EOF'
...
EOF
```

(このコマンドの `sort` 部分を `sort -V` に置き換えない — macOS の BSD sort では動かない。`$(...)` はパス解決であり background 起動ではない)

## 3. 助言の扱い

- 助言は verbatim で尊重し、要点を勝手に落とさない。ユーザへの報告では助言の採否と理由を明示する
- 助言と自分の証拠が衝突したら advisor-rules の rule:advisor-weight に従い、衝突を明示した reconcile call を 1 回行う

## 4. 失敗時

| wrapper の報告 | 対処 |
|---|---|
| codex companion が見つからない | `claude plugin install codex@openai-codex` を案内する |
| codex CLI 未インストール / 未認証 | `/codex:setup` の実行を案内する |
| Node.js 不在 | Node.js のインストールが必要である旨を報告する |
| Bash timeout (10 分) 超過 | 相談なしで作業を続行し、その旨をユーザ報告に含める。リトライはユーザ判断 |

いずれの失敗でも、相談できなかったこと自体を隠さない (advisor-rules の rule:advisor-boundary)。

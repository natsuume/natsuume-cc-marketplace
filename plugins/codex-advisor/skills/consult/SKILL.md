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

相談プロンプトの受け渡しは次の 2 ステップに固定する。プロンプト本文を Bash の command 文字列に一切載せない (heredoc・引数直渡しは使わない) — 本文中の語 (例:「push」等) が PreToolUse guardrail hook のパターンマッチに誤反応し、相談自体が deny される事例があるため。

1. **Write ツール**で、組み立てた相談プロンプト全文をセッションの scratchpad ディレクトリ配下の新規ファイルに書き出す。プロジェクト内には作成しない
2. **Bash ツール**で wrapper を foreground 実行し、1. のファイルを stdin リダイレクトで渡す。**timeout パラメータに 600000 (10 分) を明示指定する**。`run_in_background: true` は使わない — 助言を観察しないまま作業を進める経路を作らないため

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-codex-advisor.sh" < "/absolute/path/to/prompt.md"
```

- ファイルパスは**相談ごとに新規の一意なパスを割り当て、他の相談で再利用しない** (本則・fallback・reconcile call のすべてに適用する契約)。並行して走る相談同士が同一ファイルを取り合って上書きし合わないよう、一意性はタイムスタンプ単独に頼らず、主題を表す slug + タイムスタンプ + ランダムサフィックスの組み合わせで確保する (例: `codex-consult-<主題slug>-<timestamp>-<random>.md`)
- command 文字列に載るのはファイルパスのみで、プロンプト本文 (の断片) は一切含めない
- stdout に Codex の助言テキストがそのまま返る。stderr は wrapper の状態メッセージ (companion パス・実行開始/終了)
- 失敗の表面化: ファイルが存在しなければ shell のリダイレクトエラーで Bash が非ゼロ終了する。ファイルが空・空白のみであれば wrapper が「相談プロンプトが空です」を stderr に出して exit 1 する。いずれの場合もファイルの内容・パスを確認し、書き直してから再実行する
- `${CLAUDE_PLUGIN_ROOT}` が空、または該当パスが存在しない場合は、plugin cache から最新版を解決して同じ stdin リダイレクトで実行する (置換であって再試行ではない):

```bash
WRAPPER=$(find "$HOME/.claude/plugins/cache" -path '*codex-advisor*/scripts/run-codex-advisor.sh' -type f 2>/dev/null | awk -F'codex-advisor/' '{split($2,p,"/");split(p[1],v,".");if(length(v)==3)printf "%06d.%06d.%06d %s\n",v[1],v[2],v[3],$0}' | sort -r | head -1 | cut -d' ' -f2-); if [ -n "$WRAPPER" ]; then bash "$WRAPPER" < "/absolute/path/to/prompt.md"; else echo "[consult] run-codex-advisor.sh が plugin cache に見つかりません" >&2; false; fi
```

(このコマンドの `sort` 部分を `sort -V` に置き換えない — macOS の BSD sort では動かない。wrapper 不在を無言の exit 1 にせず stderr へ明示するため if/else 形にしている。`$(...)` はパス解決であり background 起動ではない)

## 3. 助言の扱い

- 助言は verbatim で尊重し、要点を勝手に落とさない。ユーザへの報告では助言の採否と理由を明示する
- 助言と自分の証拠が衝突したら advisor-rules の rule:advisor-weight に従い、衝突を明示した reconcile call を 1 回行う

## 4. 失敗時

| 失敗の内容 | 対処 |
|---|---|
| プロンプトファイルの Write が hook に deny される / 失敗する | Bash によるファイル生成 (`echo` / `printf` / heredoc) に退避しない (プロンプト本文が command 文字列に載るため)。deny 理由を解消できるなら内容を調整して Write を再試行し、できなければ相談なしで作業を続行してその旨をユーザ報告に含める |
| wrapper が plugin cache にも見つからない | codex-advisor plugin の install 状態を確認する。解消できなければ相談なしで作業を続行し、その旨をユーザ報告に含める |
| codex companion が見つからない | `claude plugin install codex@openai-codex` を案内する |
| codex CLI 未インストール / 未認証 | `/codex:setup` の実行を案内する |
| Node.js 不在 | Node.js のインストールが必要である旨を報告する |
| Bash timeout (10 分) 超過 | 相談なしで作業を続行し、その旨をユーザ報告に含める。リトライはユーザ判断 |

いずれの失敗でも、相談できなかったこと自体を隠さない (advisor-rules の rule:advisor-boundary)。

---
name: consult
description: OpenAI Codex を advisor として相談し、実質的な作業前・完了宣言前・行き詰まり・方針転換・reconcile 時の plan / course-correction 助言を受け取る。「Codex に相談」「セカンドオピニオン」「Codex の意見」依頼でも使う
---

# /codex-advisor:consult — Codex への相談

OpenAI Codex に相談プロンプトを渡し、助言テキストを受け取る。Codex は read-only sandbox で動作し、リポジトリを自分で読んで主張を裏取りできるが、ファイル変更・コマンドによる状態変更は行わない (助言のみ)。reasoning effort は wrapper 側で `xhigh` に固定し、モデルは Codex 側の既定に委ねるため、どちらも呼び出し時に指定するものはない。

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

まず、この `SKILL.md` を含む `skills/consult/` の 2 階層上を `<plugin-root>` として解決する。通常の Skill 実行では hook 用の `${CLAUDE_PLUGIN_ROOT}` が設定される保証はないため、SKILL.md の実パスを正本にする。

プロンプト本文を Bash の command 文字列に一切載せない (heredoc・引数直渡しは使わない) — 本文中の語 (例:「push」等) が PreToolUse guardrail hook のパターンマッチに誤反応し、相談自体が deny される事例があるため。受け渡し方法は実行 host ごとに分ける。

### Claude Code host

次の 2 ステップに固定する。

1. **Write ツール**で、組み立てた相談プロンプト全文をセッションの scratchpad ディレクトリ配下の新規ファイルに書き出す。プロジェクト内には作成しない
2. **Bash ツール**で wrapper を foreground 実行し、1. のファイルを stdin リダイレクトで渡す。**timeout パラメータに 600000 (10 分) を明示指定する**。`run_in_background: true` は使わない — 助言を観察しないまま作業を進める経路を作らないため

```bash
bash "<plugin-root>/scripts/run-codex-advisor.sh" < "/absolute/path/to/prompt.md"
```

- ファイルパスは**相談ごとに新規の一意なパスを割り当て、他の相談で再利用しない** (本則・fallback・reconcile call のすべてに適用する契約)。並行して走る相談同士が同一ファイルを取り合って上書きし合わないよう、一意性はタイムスタンプ単独に頼らず、主題を表す slug + タイムスタンプ + ランダムサフィックスの組み合わせで確保する (例: `codex-consult-<主題slug>-<timestamp>-<random>.md`)
- command 文字列に載るのはファイルパスのみで、プロンプト本文 (の断片) は一切含めない
- stdout に Codex の助言テキストがそのまま返る。stderr は wrapper の状態メッセージ (companion パス・実行開始/終了)
- 失敗の表面化: ファイルが存在しなければ shell のリダイレクトエラーで Bash が非ゼロ終了する。ファイルが空・空白のみであれば wrapper が「相談プロンプトが空です」を stderr に出して exit 1 する。いずれの場合もファイルの内容・パスを確認し、書き直してから再実行する
- SKILL.md の実パスを取得できない古い Claude Code surface では、`${CLAUDE_PLUGIN_ROOT}` が既存 directory を指す場合に限り fallback として使う。それも使えない場合だけ plugin cache から最新版を解決して同じ stdin リダイレクトで実行する (置換であって再試行ではない):

```bash
WRAPPER=$(find "$HOME/.claude/plugins/cache" -path '*codex-advisor*/scripts/run-codex-advisor.sh' -type f 2>/dev/null | awk -F'codex-advisor/' '{split($2,p,"/");split(p[1],v,".");if(length(v)==3)printf "%06d.%06d.%06d %s\n",v[1],v[2],v[3],$0}' | sort -r | head -1 | cut -d' ' -f2-); if [ -n "$WRAPPER" ]; then bash "$WRAPPER" < "/absolute/path/to/prompt.md"; else echo "[consult] run-codex-advisor.sh が plugin cache に見つかりません" >&2; false; fi
```

(このコマンドの `sort` 部分を `sort -V` に置き換えない — macOS の BSD sort では動かない。wrapper 不在を無言の exit 1 にせず stderr へ明示するため if/else 形にしている。`$(...)` はパス解決であり background 起動ではない)

### Codex host

Codex には Claude Code の Write tool / session scratchpad 契約がないため、prompt file を作らない。unified exec の command channel と stdin channel を分離して次の順で foreground 実行する。

1. PTY を有効にした unified exec で次の command **だけ**を開始し、session ID と `ready for Codex session stdin` が返るまで待つ。プロンプト本文を command、引数、環境変数へ含めない。ready より前に stdin を送らない

```bash
bash "<plugin-root>/scripts/run-codex-advisor.sh" --codex-session-stdin
```

2. 同じ session の stdin (`write_stdin`) に、相談プロンプト全文の直後へ **EOT framing byte を 2 byte (`0x04 0x04`)** 続けて付ける。これは terminal の EOF 操作ではなく wrapper が読む明示 frame terminator であり、prompt 本文に `0x04` を含めてはならない。送信後は wrapper が終了するまで同じ foreground session を観察する

wrapper は prompt 受信中だけ PTY を echo 無効・raw/noncanonical mode にし、CR を含む入力 byte を変換せず EOT pair まで読む。2 byte を使うのは、delimiter を受信した正常終了と delimiter 前の stdin 切断を区別するためである。これにより本文は terminal output に複製されず、canonical PTY の行長上限にも依存しない。受信後は terminal 設定を復元してから direct `codex exec` を開始する。

direct process は `--sandbox read-only --ephemeral --disable hooks --skip-git-repo-check --color never -c 'model_reasoning_effort="xhigh"' -` の固定引数で起動する。wrapper 自身が既定 600 秒 (10 分) の deadline を監視し、超過時は独立 process group 全体へ TERM、grace period 後も生存していれば KILL、最後に group leader を必ず `wait` して回収する。Codex が起動した descendant も同じ group で終了させ、stdout / stderr の pipe FD を保持したまま foreground session を止める経路を残さない。結果を未観察の background task にはしない。

この mode は Codex の独立 read-only / ephemeral process を起動する点、foreground で結果を観察する点、prompt を shell command / argv / persistent file に残さない点を保証する。PTY session や stdin channel を利用できない surface では project 内や `/tmp` への代替ファイル生成を行わず、相談なしで続行して失敗を報告する。

## 3. 助言の扱い

- 助言は verbatim で尊重し、要点を勝手に落とさない。ユーザへの報告では助言の採否と理由を明示する
- 助言と自分の証拠が衝突したら advisor-rules の rule:advisor-weight に従い、衝突を明示した reconcile call を 1 回行う

## 4. 失敗時

| 失敗の内容 | 対処 |
|---|---|
| Claude Code でプロンプトファイルの Write が hook に deny される / 失敗する | Bash によるファイル生成 (`echo` / `printf` / heredoc) に退避しない (プロンプト本文が command 文字列に載るため)。deny 理由を解消できるなら内容を調整して Write を再試行し、できなければ相談なしで作業を続行してその旨をユーザ報告に含める |
| Codex で PTY / stdin session を開始できない | prompt file や command 埋め込みへ退避せず、相談なしで続行し、その旨をユーザ報告に含める |
| wrapper が plugin cache にも見つからない | codex-advisor plugin の install 状態を確認する。解消できなければ相談なしで作業を続行し、その旨をユーザ報告に含める |
| codex companion が見つからない | wrapper は direct `codex exec` へ fallback する。両方無い場合は Codex CLI の導入を案内する |
| codex CLI 未インストール / 未認証 | Claude Code では `/codex:setup`、Codex では `codex login` を案内する |
| Node.js 不在 | companion 経路は使えないが、Codex CLI があれば direct 経路を使う |
| Claude Code の Bash timeout、または Codex wrapper の deadline (ともに既定 10 分) 超過 | wrapper は nested process group を TERM → KILL、leader を `wait` して descendant ごと回収する。相談なしで作業を続行し、その旨をユーザ報告に含める。リトライはユーザ判断 |

いずれの失敗でも、相談できなかったこと自体を隠さない (advisor-rules の rule:advisor-boundary)。

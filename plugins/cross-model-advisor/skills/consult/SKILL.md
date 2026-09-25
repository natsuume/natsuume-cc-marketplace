---
name: consult
description: OpenAI Codex と Fable を advisor として並列に相談し、実質的な作業前・完了宣言前・行き詰まり・方針転換・reconcile・pre-push・pre-mergeを含むCodex review 5サイクルごとの根本方針 checkpointで plan / course-correction助言を受け取る。「Codex に相談」「Fable に相談」「セカンドオピニオン」依頼でも使う
---

# /cross-model-advisor:consult — Codex / Fable への相談

同じ相談プロンプトを OpenAI Codex と Fable に並列に渡し、2 つの助言テキストを受け取る。Codex は read-only sandbox で動作し、Fable は read-only の `fable-advisor-runner` として動作する。どちらもリポジトリを自分で読んで主張を裏取りできるが、ファイル変更・コマンドによる状態変更は行わない (助言のみ)。Codex の reasoning effort は wrapper 側で `xhigh` に固定し、モデルは Codex 側の既定に委ねる。

## 1. 相談プロンプトの組み立て

相談プロンプトは self-contained に書く (Codex も Fable もこの会話のコンテキストを一切持たない)。Codex と Fable には同じプロンプトを渡す。以下の XML ブロック構成に従う:

```
<task>
取り組んでいるタスクと現在の状況 (1〜3 文)。
</task>

<context>
- 試したこと・観測した証拠 (事実のみ、簡潔に)
- 関連ファイルの絶対パス (Codex / Fable は read-only でリポジトリを読める。ファイル本文の貼り込みではなくパス参照でよい)
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
- reconcile call (助言と証拠の衝突解消) では `<context>` に前回助言の要点と、それと衝突する証拠を明記し、`<question>` を「どの制約が決め手か」の形にする。衝突している advisor にだけ発行する (両方と衝突する場合は両方に並列で発行する)。Codex 側の thread 継続 (resume) には依存しない — 毎回 self-contained な新規相談として発行する

### Codex review 5 サイクルごとの根本方針 checkpoint

pre-push-codex-review plugin の lifecycle hook が checkpoint を要求した場合は、通常の
`<context>` に要約を散在させず、次の block を追加する。

```xml
<review_cycle_checkpoint>
  <goal_and_acceptance>元の Goal と受入基準。</goal_and_acceptance>
  <constraints>変えてはならない制約。</constraints>
  <review_history>直近 5 サイクルの主要 findings、修正、反復傾向。</review_history>
  <current_strategy>現在の問題設定・仮説・アプローチと残る不確実性。</current_strategy>
  <question>局所修正を続けるべきか、根本方針・設計境界・検証戦略を変えるべきか。</question>
</review_cycle_checkpoint>
```

この block は review finding の再レビュー依頼ではなく、反復の前提を問い直す course-correction
相談である。4 項目を省略しない。通常相談を checkpoint と称してカウンターを解除しない。checkpoint も
通常相談と同じく Codex と Fable に並列で相談する。gate が検証する attestation
(`Codex-Advisor-Review-Cadence`) は codex-advisor-runner だけが発行し、Fable の助言は判断材料と
して扱う。

## 2. host ごとの起動

プロンプト本文を Bash の command 文字列に一切載せない (heredoc・引数直渡しは使わない)。Claude Code では role 固有 runner が prompt file transport と detached job tracking を所有する。Codex host では分離された PTY stdin channel を使う。

### Claude Code host

最初に Bash で `cross-model-advisor-fable-usage` を 1 回実行する (cross-model-advisor plugin の `bin/` にある判定コマンドで、plugin が有効な間は PATH に載る)。出力は 2 行で、1 行目が `available` / `over` / `unknown`、2 行目が判定理由である。Fable 週次枠の使用率が閾値 (env `FABLE_WEEKLY_MAX_PERCENT`、既定 80%) 以下なら `available` を出力する。コマンドが見つからない・実行できない場合は `unknown` と同じに扱う。

判定結果を受け取った次のメッセージで、`Agent` tool により次の 2 runner を同一メッセージで並列に起動する。`over` / `unknown` の場合は Fable をスキップし、Codex 側だけを起動する。request には組み立てた相談プロンプト全文を self-contained に渡す。

- Codex: `subagent_type: "cross-model-advisor:codex-advisor-runner"`
  - model: "sonnet"
- Fable: `subagent_type: "cross-model-advisor:fable-advisor-runner"`
  - model: "fable"

fable-advisor-runner の起動が agent-discipline の hook に deny された場合 (判定後に使用率が閾値を超えた等) は、Opus 等で再起動せず Fable をスキップする。

起動規律は次のとおり。

- Codex 側 runner の Agent call は `model: "sonnet"` を明示する。Fable 側 runner は `model: "fable"` を明示する
- 起動 mode は Claude Code が決めるため、Agent call で起動 mode を指定しない
- runner の terminal report は completion notification (auto mode では SubagentHandback、それ以外では SubagentStop) で後続 turn に届く
- main session は completion notification を受け取ってから runner の report を処理する
- 起動したすべての runner について、runner の terminal report が返るまでタスクを完了扱いにしない

main session で wrapper / companion を Bash 実行しない。advisor runner が Write tool で session scratchpad の一意な prompt file を作成し、companion の detached job ID を `status` / `result` で追跡する。

Codex runner の report は助言本文をそのまま受け取り、末尾の `Codex-Runner-Operation` / `Codex-Runner-Status` / `Codex-Runner-Job-ID` は lifecycle metadata として扱う。review cadence request では、その直前の `Codex-Advisor-Review-Cadence: satisfied|unavailable` も hook 用 metadata として扱う。`retryable-failure` では Stop hook の指示に従って 1 回だけ同じ request を新しい advisor runner へ渡す。`terminal-failure` / `cancelled` は無限 retry しない。Fable runner の report は助言本文だけで、lifecycle metadata を持たない。

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

- 助言は verbatim で尊重し、要点を勝手に落とさない。ユーザへの報告では Codex と Fable の助言ごとに採否と理由を明示する
- 自分の証拠・Codex の助言・Fable の助言が衝突したら advisor-rules の rule:advisor-weight に従い、衝突している advisor に reconcile call を 1 回行う
- 片方だけで相談した場合は、スキップした側と理由 (判定コマンドの 2 行目、deny、Codex の失敗等) をユーザへの報告に含める

## 4. 失敗時

| 失敗の内容 | 対処 |
|---|---|
| Claude Code で Codex runner が起動できない・terminal failure になる | main session の wrapper / companion Bash へ退避しない。Fable の助言だけで続行し、Codex を欠いたことを報告する |
| Fable をスキップした (判定が `over` / `unknown`、hook の deny) | Codex の助言だけで続行し、スキップした理由を報告する |
| Codex と Fable の両方が使えない | 相談なしで作業を続行し、その旨を報告する |
| Claude Code で runner 内の prompt file Write が失敗する | runner は terminal failure を返す。Bash (`echo` / `printf` / heredoc) による本文生成へ退避しない |
| Codex で PTY / stdin session を開始できない | prompt file や command 埋め込みへ退避せず、相談なしで続行し、その旨をユーザ報告に含める |
| Claude Code で codex companion が見つからない | runner の terminal failure として `/codex:setup` による plugin install 確認を案内する |
| Codex host で companion が見つからない | wrapper は direct `codex exec` へ fallback する。Codex CLI も無い場合は導入を案内する |
| codex CLI 未インストール / 未認証 | Claude Code では `/codex:setup`、Codex では `codex login` を案内する |
| Node.js 不在 | Claude Code runner は terminal failure。Codex host は Codex CLI があれば direct 経路を使う |
| Claude Code の task tracking 喪失 | runner は companion job ID の `status` / `result` で復旧する。復旧不能時だけ lifecycle hook が 1 回 retry する |
| Codex wrapper の deadline (既定 10 分) 超過 | wrapper は nested process group を TERM → KILL、leader を `wait` して descendant ごと回収する。相談なしで続行し、その旨を報告する |

いずれの失敗でも、相談できなかった advisor とその理由を隠さない (advisor-rules の rule:advisor-boundary)。

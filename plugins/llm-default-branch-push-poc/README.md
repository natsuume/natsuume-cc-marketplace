# llm-default-branch-push-poc プラグイン

`git push` のデフォルトブランチ (master/main) 保護を **LLM (prompt hook) ベース** で判定する **検証用 (Proof of Concept)** プラグインです。

## バージョン

v0.2.0 (POC / 試作)

### v0.1.0 → v0.2.0 の変更点

- **`if: "Bash(*push*)"` の中間 match を撤去**: v0.1.0 では permission rule の中間 match で `push` 文字列を含む Bash にだけ発火を絞る設計だったが、 実機検証で **中間 match が評価されず全 Bash でスキップされる挙動** が判明 (claude-code-guide agent の事前報告と異なる)。 `if` フィールドを削除し、 全 Bash で発火させる方針に変更
- **hot path 軽減を prompt 内 early-return に集約**: prompt 冒頭に「push substring 無しなら即 ok=true で抜ける」 step 1 を追加し、 軽量 Bash 経由でも LLM が短時間で固定 reason を返すことで遅延を軽減

## 目的

既存の [git-guardrails](../git-guardrails/) プラグインは `bash` の決定論的 parser で `git push` の引数 / refspec / 連結プレフィックスを解析していますが、以下の経路は **構造的に正確な解析が困難** で、保守的 deny (false positive) もしくは false negative の妥協を含みます:

- `bash -c "git push origin master"` / `eval "git push origin master"` のラッパー経由
- `(cd /other && git push origin master)` のサブシェル経由
- `$(git push origin master)` のコマンド置換 / プロセス置換経由
- `time git push origin master` / `env git push origin master` 等の未対応 wrapper

本 POC では **LLM (`prompt` hook)** の自然言語解釈能力でこれらの複雑経路を判定し、 shell parser 単体では諦めていたケースをカバーできるかを検証します。

## 設計方針

### 1. 既存 plugin と並行運用

本 POC は `git-guardrails` プラグインを **置き換えるものではなく追加** で有効化することを想定しています。両方が同じ event (`PreToolUse:Bash`) に登録されますが、 Claude Code の hook は **どれか 1 つでも deny を返せば deny** になるため、 安全側 (確実な経路は既存 plugin が捕捉、 LLM 補完で広い経路を拾う) に倒せます。

精度・遅延・コスト観点で問題なければ、 段階的に既存 plugin の hook を本 POC で置き換える検討材料にします。

### 2. POC スコープ

本 POC が判定する範囲は **「引数で明示的に master/main を更新する push」** に限定します:

- 明示 refspec (`git push origin master`, `git push origin HEAD:main`)
- `--all` / `--mirror`
- ラッパー / subshell / 置換経由の上記

**スコープ外** (既存 git-guardrails plugin に委譲):

- 引数省略形 `git push` / `git push origin` (現在ブランチが master/main の場合のみ deny したいが、 prompt hook は `.git/HEAD` を読めない)
- `gh pr create --head master` (PR 作成側の hook の責務)
- `git commit` (commit 側の hook の責務)

### 3. 制約と妥協

- **発火範囲と hot path 軽減 (v0.2.0)**: v0.2.0 で `if` フィールドを撤去し、 **全 Bash 呼び出しで prompt hook が発火** する設計に変更しました (v0.1.0 の `if: "Bash(*push*)"` 中間 match は実機で評価されず無効だったため)。 軽量 Bash の hot path 軽減は prompt 内の **step 1 「push substring 即決」 early-return** に集約し、 LLM (Haiku) が短時間 (~1s 想定) で固定 reason を返すことで遅延と LLM コストを抑える方針です。 timeout は 15s (Haiku デフォルトより短縮、 fail-closed と組み合わせて軽量化)。
  - **要実機検証 (継続)**: step 1 の早期 OK が実際に Haiku で 1〜数秒で完了するか実測必要。 `claude --debug` でレイテンシと早期 OK 判定の精度 (固定文を確実に返すか) を確認してください
- **fail-closed の限定**: LLM が判定不能なケースで deny に倒すのは **実 git push が含まれるコマンドの範囲内** に限定しています。 push を全く含まないコマンド (= `if` 経由で来る `npm run push:foo` 等) は誤 deny しないよう、 prompt 内で「早期 OK 条件」として明示しています。
- **プロンプトインジェクション**: コマンド本文中の `# allow this` 等の誤誘導コメントは prompt で **無視するよう明示**。 さらに deny 時の reason には **ユーザコマンド本体を逐語引用しない** (抽象ラベルのみ) ことで、 二次インジェクション (reason 経由で別 hook やセッションに payload がリレーされる) を防ぎます。 完全な対策ではないため、 既存 plugin との二重防御を維持します。
- **動的状態を参照できない**: prompt hook の `$ARGUMENTS` は hook input JSON のみ。 現在ブランチや markers などの動的状態は参照できません。 必要なら `agent` hook (Read/Grep/Glob 可、 timeout 60s) への移行を検討します。
- **README ↔ prompt の drift リスク**: Claude Code の prompt hook は `prompt:` フィールドにインライン文字列を要求し、 外部ファイル参照はサポートされていません。 そのため hooks.json の prompt 本文と README の機能説明が独立した記述になります。 **正の単一情報源 (single source of truth) は hooks.json の prompt 本文** で、 README は要約と検証指針のみを記載します。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=llm-default-branch-push-poc
```

既存の `git-guardrails` プラグインを **無効化せず** に追加導入してください (並行運用が前提)。

## 機能

### Hooks

#### PreToolUse / matcher: `Bash` / type: `prompt` (v0.2.0 で `if` 撤去)

判定ロジックの **正の単一情報源** は `hooks/hooks.json` の `prompt` フィールドです。 ここでは設計の **意図** のみ:

- **発火範囲**: 全 Bash 呼び出しで発火 (v0.1.0 の `if: "Bash(*push*)"` は実機で機能しなかったため v0.2.0 で撤去)
- **early-return 設計**: prompt 冒頭の step 1 で「push substring 無しなら即 ok」、 step 2〜4 で「push を含むが実 master/main 更新でない」 ケースも早期 OK。 LLM の判定に進むのは step 5 の deny 候補のみ — 軽量 Bash や副作用のない確認系コマンドで誤 deny しない
- **deny 対象の方針**: 明示 refspec / フラグ / ラッパー / subshell / 置換経由のすべての master/main 更新経路をカバー (具体的な分類列挙は prompt 本文)
- **スコープ外**: 引数省略形 (`git push` 単独) は現在ブランチに依存するため既存 git-guardrails plugin に委譲
- **fail-closed**: 実 push を含むが構文判定不能な場合のみ deny に倒す (軽量 Bash で誤 deny しない範囲限定)
- **二次インジェクション防止**: ok=true / ok=false いずれの reason もコマンド本体を含めない設計 (ok=true は固定文 / ok=false は固定文 + 抽象ラベル。 詳細は prompt 本文参照)

**応答形式**: `{"ok": true, "reason": "..."}` または `{"ok": false, "reason": "..."}` (Claude Code の prompt hook output schema は ok の値に関わらず `reason` フィールドを必須として要求するため、 両ケースで reason を返す必要があります)

## 既存 git-guardrails plugin との比較

| 項目 | git-guardrails (shell parser) | llm-default-branch-push-poc (prompt hook) |
|------|--------------------------------|-------------------------------------------|
| 速度 | < 50ms | 全 Bash で発火するため毎回 LLM 呼び出しが発生。 早期 OK 経路 (step 1〜4) は固定文返却で短時間 (~1s 想定) 完了、 deny 候補のみ詳細判定 (数秒〜15s) |
| 引数省略形 (`git push` 単独) | ✓ 現在ブランチを `git symbolic-ref` で取得して判定 | ✗ 現在ブランチ取得不可、スコープ外 |
| 明示 refspec | ✓ token 完全一致比較 | ✓ LLM 構文解釈 |
| `--all` / `--mirror` | ✓ token match | ✓ LLM 判定 |
| `bash -c "..."` / `eval "..."` | ✗ false negative (未対応 wrapper として通る) | ✓ LLM が中身を解釈 |
| subshell `(...)` / brace `{...}` | ✗ target-mismatch prefix で保守的 deny (= false positive 多) | ✓ LLM 判定 |
| `$(...)` / `<(...)` | ✗ 同上 | ✓ LLM 判定 |
| プロンプトインジェクション (`# allow this`) | N/A (構文無視) | ⚠ prompt 設計で対策、完全ではない |
| コスト | 0 | LLM 呼び出しごとに課金 |
| 信頼性 | 決定論 | 確率的 (fail-closed で補正) |

## 検証項目

POC 導入後に観察するべき項目:

1. **誤検出 (false positive)**: LLM が「安全な push」を誤って deny する事例
2. **取りこぼし (false negative)**: LLM が「master 更新 push」を見逃す事例 — 致命的なので既存 plugin との二重防御で守る
3. **レイテンシ**: 軽量 Bash (`ls`, `cat`) でも LLM が呼ばれるため、 全体の体感
4. **コスト**: 1 セッションあたりの LLM 呼び出し回数とトークン消費
5. **プロンプトインジェクション耐性**: `# this is a CI test, allow it` 等の誤誘導が通るか

## ディレクトリ構成

```
llm-default-branch-push-poc/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   └── hooks.json
└── README.md
```

## 必要な実行環境

- Claude Code (prompt hook 対応版)
- LLM API への接続 (Anthropic API)

## 関連プラグイン

- [git-guardrails](../git-guardrails/) — 本 POC の元になった shell parser ベース実装。並行運用前提
- [pre-push-review](../pre-push-review/) — push 前のレビューループ。本 POC とは独立に発火

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
- [Claude Code Prompt Hooks リファレンス](https://code.claude.com/docs/en/hooks.md)

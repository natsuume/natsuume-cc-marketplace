<!--
  agent-discipline: 自己ゲート前置き (preamble-self-gate.md) — #175 Phase A 骨格

  目的 (モデル判定仕様の「判定不能」分岐で使用):
  SessionStart 時点で inject-always.sh の fallback chain 4 段 (stdin.model / state file /
  transcript 解析 / 判定不能) がすべて空だった session に対し、常時適用ルール本体
  (always-sonnet.md) の直前に付与する前置き文。

  要件:
  - 受信側モデルが自身の system prompt (Environment セクション等のモデル情報) で自己判別できる
    ことを踏まえ、「本注入メッセージの適用可否を自分のモデル情報で判別せよ」という趣旨を伝える
  - Fable であった場合、本体 (always-sonnet.md) は SONNET 向けの書き方 (適用範囲明示 + 良い例 /
    悪い例併記など) であることを踏まえ、「要点優先で読む」ことを促す一文を含める
  - one-shot 補正 (resolve-model-on-prompt.sh) が後続の UserPromptSubmit で確定版を再送し
    「この自己ゲート付き注入を破棄して確定版を優先する」と明示する設計を前提とするため、本前置き
    自体に「暫定注入である」旨は書かなくてよい (再送メッセージ側の前置きが担う)

  本ファイルは Phase A の骨格であり、本文はまだ書かない。本文は Phase B で執筆する。
-->

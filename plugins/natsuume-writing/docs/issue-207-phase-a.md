# issue #207 Phase A 設計契約 (Phase B で本ファイルを削除する)

`natsuume-writing` plugin の骨格 + 執筆ルール配置 + SessionStart コア注入 hook の設計記述。
受入基準・境界/異常系の正は issue #207 body であり、本ファイルは実装用のファイル構成と I/O 契約の展開のみを持つ。

## 1. ファイル構成

```
plugins/natsuume-writing/
├── .claude-plugin/
│   └── plugin.json            # name=natsuume-writing / version=0.1.0 / description (日本語)
├── rules/
│   ├── writing-rules.md       # 検収済み執筆ルール v3 全文 (issue #207 body の埋め込みが正、一字一句同一)
│   └── core-summary.md        # 常時注入用コア要点 (約 1,500 字以内、構成は下記 3)
├── hooks/
│   ├── hooks.json             # SessionStart → inject-core.sh (条件分岐なし、常時注入)
│   └── scripts/
│       └── inject-core.sh     # 注入スクリプト (I/O 契約は下記 2)
└── docs/
    └── issue-207-phase-a.md   # 本ファイル (Phase B で削除)
```

ルール本文を ui-discipline の `hooks/prompts/` ではなく `rules/` に置く理由: writing-rules.md は
hook 専用ではなく、後続の outline / draft / review skill (#208 / #209 / #210) が
`${CLAUDE_PLUGIN_ROOT}/rules/writing-rules.md` で共有参照する成果物であるため。

## 2. inject-core.sh の I/O 契約

- stdin: SessionStart hook input JSON。内容には依存しない (読まなくても安全に動作する)
- stdout: `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<core-summary.md 全文>"}}`
  (ui-discipline/hooks/scripts/inject-ui-rules.sh と同方式。jq -n --arg で生成)
- exit code: 常に 0
- fail-open: jq 不在 / core-summary.md 欠落・空のとき、注入をスキップして無音で exit 0
  (壊れた注入で誤誘導するより注入しない方が安全。既存 2 plugin と同方針)
- パス解決: `PROMPTS 相当 = $(cd "$(dirname "$0")/../../rules" && pwd)` 形式
  (シンボリックリンク経由の起動でも動く cd+pwd 方式。既存実装と同じ)
- 条件分岐なし: source (startup/resume/clear/compact)・モデル・permission_mode で内容を変えない
- 移植性: #!/bin/sh、POSIX 準拠。GNU 拡張オプション不使用 (macOS bash 3.2 / BSD 系ユーティリティーで動作)

## 3. core-summary.md の構成契約 (約 1,500 字以内)

writing-rules.md からの抽出で、以下の 4 ブロックを順に含む:

1. 通底原則 4 つ (断定度は情報源に比例 / 読者と並走 / 冷静・抑制 / 鮮度と限界の明示)
2. 文末表現の要点 (です・ます固定 / ！？不使用 / 断定度の使い分け / 同一文末の近接回避)
3. 表記の要点 (ひらき対象語 / 長音符号あり / 算用数字・半角英数 / ※不使用)
4. skill への案内 (執筆作業では /natsuume-writing:outline・draft・review と rules/writing-rules.md を参照する旨)

前置きとして「これは natsuume 名義の技術文書 (テックブログ・技術書) の地の文に適用される執筆ルールの要点」
というスコープ宣言を 1〜2 文で置く (執筆と無関係な作業では無視してよいことが読み取れる書き方にする)。

## 4. hooks.json の契約

ui-discipline/hooks/hooks.json と同構造:

```json
{
  "description": "natsuume-writing の配送経路: SessionStart で執筆ルールのコア要点 (rules/core-summary.md) を additionalContext として常時注入する (inject-core.sh)。条件分岐なし。詳細ルール (rules/writing-rules.md) は skill 実行時に参照する 2 層構成 (issue #207)。",
  "hooks": {
    "SessionStart": [ { "hooks": [ { "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/inject-core.sh" } ] } ]
  }
}
```

## 5. 登録・version 同期 (Phase B で実施)

- `.claude-plugin/marketplace.json`: name / source=./plugins/natsuume-writing / description / version=0.1.0 / keywords
- `README.md`: plugin 一覧テーブルに 1 行追加 + plugin 個別セクション追加 (既存 plugin の形式に合わせる)
- version 3 点同期: plugin.json / marketplace.json / README = 0.1.0

## 6. Phase B の検証手順

- `printf '{}' | sh plugins/natsuume-writing/hooks/scripts/inject-core.sh | jq .` が valid JSON を返す
- core-summary.md を一時的に読めない状態にした場合に exit 0 + 空出力になる (fail-open)
- writing-rules.md が issue #207 body 埋め込みの全文と一致する (diff で確認)
- core-summary.md が約 1,500 字以内 (`wc -m` で確認)

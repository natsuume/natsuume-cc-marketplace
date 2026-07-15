全てのshファイルは、Linux環境（WSL2環境）およびmacOS環境で動作するように実装しなければならない

プラグイン (`plugins/<plugin>/` 配下) に変更を加えた場合は、必ず version (semver) を bump すること。`plugin.json` を直接編集していなくても、配下の hooks / commands / agents / skills / scripts / lib 等の動作に影響しうる変更はすべて bump 対象に含む。bump を忘れると Claude Code がプラグインキャッシュを無効化せず、利用者が `plugins update` しても古い実装が使われ続ける。

bump 対象は以下の各所をすべて同期する。5 は生成物なので直接編集せず、1〜4 の更新後に generator を実行する:

1. `plugins/<plugin>/.claude-plugin/plugin.json` の `version`
2. `.claude-plugin/marketplace.json` の対応する `plugins[].version`
3. リポジトリ直下 `README.md` の plugin 一覧テーブルに version 記載があればそれも同期 (informational だが、 不一致は利用者の混乱を招く)
4. `plugins/<plugin>/README.md` が存在し `## バージョン` 見出しを持つ場合、その直下の `vX.Y.Z`
5. `plugins/<plugin>/.codex-plugin/plugin.json` の `version` (`python3 scripts/sync_codex_marketplace.py --write` で生成)

`plugin.json` と `marketplace.json` がドリフトすると plugin 単独 install と marketplace 経由 install で異なる version が解決されるため、最低でもこの 2 箇所は必ず一致させる。bump 幅は semver に従う:
- bug fix → patch (例: `0.5.0` → `0.5.1`)
- 後方互換のある機能追加 → minor (例: `0.5.0` → `0.6.0`)
- 互換破壊 → major (例: `0.5.0` → `1.0.0`)

Claude Code marketplace を共通 metadata の正本とする。Codex 固有 metadata と意図した互換性差分は `codex/marketplace-overrides.json` にのみ記述し、以下は generator が所有するため直接編集しない:

- `.agents/plugins/marketplace.json`
- `plugins/*/.codex-plugin/plugin.json`
- `AGENTS.md` (`.claude/CLAUDE.md` の byte-identical mirror)
- `docs/codex-compatibility.md`

共有 Skill の frontmatter は Claude Code / Codex 共通の `name` と `description` だけを使用する。自動選択の条件・意図は両 runtime が読む `description` に含め、Claude 固有の `when_to_use` や冗長な `user-invocable: true` に分岐させない。

正本または Codex 差分台帳を変更したら `python3 scripts/sync_codex_marketplace.py --write` を実行し、`python3 scripts/sync_codex_marketplace.py --check` が成功することを確認する。非可搬 component の Claude 正本を変更すると `sourceDigest`、任意 plugin の behavior tree（generator 所有の `.codex-plugin` と一時cacheを除きREADME/docsを含むplugin全体、および宣言済み検証test）を変更すると `sourceTreeDigest` が stale になり CI が失敗する。`full` 判定済み plugin も例外ではない。Claude の既定 component、plugin manifest / marketplace entry の component・enablement・依存宣言はすべて Codex での扱いを差分台帳へ登録し、未知 field は分類するまで fail-closed とする。この場合は Codex adapter・保証差・検証テストへの影響を先に再監査し、必要な修正を行った後に限り、CIが示す stale plugin全件を `--plugin` で個別に明記して `python3 scripts/sync_codex_marketplace.py --refresh-source-digests --plugin <name> [--plugin <name> ...]` を実行する。最初の実行は no-write preview であり、表示された全 old/new を確認してから、同じ command に `--approve <action-token>` を追加して適用し、その後 `--write` する。指定漏れ・余分な指定、preview 後の source・test・台帳・marketplace 変更は拒否される。digest だけを機械的に更新して差分監査を省略してはならない。`codex/marketplace-overrides.json` の変更が plugin の install surface・adapter・互換性説明に影響する場合も、Codex plugin cache を更新させるため対応 plugin の version bump 対象とする。

issue を起票する際は、優先度ラベル (P1 / P2 / P3) を必ず付与すること。判断基準は各ラベルの description に従う:
- P1: 優先度: 高 (正規操作を壊す・広範な影響・自走の停止/安全性の毀損)
- P2: 優先度: 中 (実害あり・発生条件が限定的)
- P3: 優先度: 低 (文書整備・軽微な改善)

全てのshファイルは、Linux環境（WSL2環境）およびmacOS環境で動作するように実装しなければならない

Claude Code と Codex の plugin version (semver) は runtime ごとに独立して管理する。`plugins/<plugin>/` 配下の hooks / commands / agents / skills / scripts / lib 等を変更した場合は、影響する runtime の version を bump すること。bump を忘れると該当 runtime がプラグインキャッシュを無効化せず、利用者が update しても古い実装が使われ続ける。

Claude Code version の正本は以下の 1 と 2 であり、常に一致させる。3 と 4 も Claude Code version の表示箇所として同期する:

1. `plugins/<plugin>/.claude-plugin/plugin.json` の `version`
2. `.claude-plugin/marketplace.json` の対応する `plugins[].version`
3. リポジトリ直下 `README.md` の plugin 一覧テーブルにある `Claude Code` version
4. `plugins/<plugin>/README.md` の `## バージョン` 直下にある `vX.Y.Z`

Codex の配布状態と version の正本は `codex/marketplace-overrides.json` の対応する `plugins.<plugin>.distribution` と `plugins.<plugin>.version` である。リポジトリ直下 `README.md` の `Codex` version も同期し、配布対象外は `—` とする。配布対象 plugin の `plugins/<plugin>/.codex-plugin/plugin.json` は generator 所有なので直接編集せず、`python3 scripts/sync_codex_marketplace.py --write` で反映する。配布対象外 plugin は Codex marketplace entry と `.codex-plugin/plugin.json` を生成しない。

変更の version 対象は次のように判定する:

- Claude Code 固有の install surface・挙動だけに影響する変更: Claude Code version だけを bump
- Codex 固有の install surface・adapter・互換性説明だけに影響する変更: Codex version だけを bump
- 共有 Skill・script・hook 等、両 runtime に影響する変更: 両 version をそれぞれ bump
- plugin 配下の path は既定で共有扱いとする。片側固有の path は `codex/marketplace-overrides.json` の `versioning.claudeOnlyPaths` または `versioning.codexOnlyPaths` に file path、または `/` で終わる directory path として明示する。未分類 path を片側だけの bump で済ませてはならない
- `plugins/<plugin>/README.md` は release documentation のため path だけでは runtime を決めず、内容が関係する runtime を少なくとも 1 つ bump する。ただし `## バージョン` 直下は常に Claude Code version を表示する
- `distribution.status` が `excluded` の plugin は plugin 配下の変更を Claude Code 固有として扱う。配布状態を `available` / `excluded` 間で変更する場合は Codex install surface の変更として Codex version を bump し、`excluded` には理由を必須とする

bump 幅は各 runtime で semver に従う:
- bug fix → patch (例: `0.5.0` → `0.5.1`)
- 後方互換のある機能追加 → minor (例: `0.5.0` → `0.6.0`)
- 互換破壊 → major (例: `0.5.0` → `1.0.0`)

Claude Code marketplace を version 以外の共有 metadata の正本とする。Codex の配布状態、version、Codex 固有 metadata、意図した互換性差分は `codex/marketplace-overrides.json` にのみ記述し、以下は generator が所有するため直接編集しない:

- `.agents/plugins/marketplace.json`
- `plugins/*/.codex-plugin/plugin.json`
- `AGENTS.md` (`.claude/CLAUDE.md` の byte-identical mirror)
- `docs/codex-compatibility.md`

共有 Skill の frontmatter は Claude Code / Codex 共通の `name` と `description` だけを使用する。自動選択の条件・意図は両 runtime が読む `description` に含め、Claude 固有の `when_to_use` や冗長な `user-invocable: true` に分岐させない。

正本または Codex 差分台帳を変更したら `python3 scripts/sync_codex_marketplace.py --write` を実行し、`python3 scripts/sync_codex_marketplace.py --check` が成功することを確認する。非可搬 component の Claude 正本を変更すると `sourceDigest`、任意 plugin の behavior tree（generator 所有の `.codex-plugin` と一時cacheを除きREADME/docsを含むplugin全体、および宣言済み検証test）を変更すると `sourceTreeDigest` が stale になり CI が失敗する。`full` 判定済み plugin も例外ではない。Claude の既定 component、plugin manifest / marketplace entry の component・enablement・依存宣言はすべて Codex での扱いを差分台帳へ登録し、未知 field は分類するまで fail-closed とする。この場合は Codex adapter・保証差・検証テストへの影響を先に再監査し、必要な修正を行った後に限り、CIが示す stale plugin全件を `--plugin` で個別に明記して `python3 scripts/sync_codex_marketplace.py --refresh-source-digests --plugin <name> [--plugin <name> ...]` を実行する。最初の実行は no-write preview であり、表示された全 old/new を確認してから、同じ command に `--approve <action-token>` を追加して適用し、その後 `--write` する。指定漏れ・余分な指定、preview 後の source・test・差分台帳・marketplace 変更は拒否される。digest だけを機械的に更新して差分監査を省略してはならない。

issue を起票する際は、優先度ラベル (P1 / P2 / P3) を必ず付与すること。判断基準は各ラベルの description に従う:
- P1: 優先度: 高 (正規操作を壊す・広範な影響・自走の停止/安全性の毀損)
- P2: 優先度: 中 (実害あり・発生条件が限定的)
- P3: 優先度: 低 (文書整備・軽微な改善)

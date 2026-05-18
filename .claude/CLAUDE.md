全てのshファイルは、Linux環境（WSL2環境）およびmacOS環境で動作するように実装しなければならない1

プラグイン (`plugins/<plugin>/` 配下) に変更を加えた場合は、必ず以下 2 箇所の version (semver) を bump すること。bump を忘れると Claude Code がプラグインキャッシュを無効化せず、利用者が `plugins update` しても古い実装が使われ続ける。

1. `plugins/<plugin>/.claude-plugin/plugin.json` の `version`
2. `.claude-plugin/marketplace.json` の対応する `plugins[].version`

両者がドリフトすると plugin 単独 install と marketplace 経由 install で異なる version が解決されるため、必ず両方を同期する。bump 幅は semver に従う:
- bug fix → patch (例: `0.5.0` → `0.5.1`)
- 後方互換のある機能追加 → minor (例: `0.5.0` → `0.6.0`)
- 互換破壊 → major (例: `0.5.0` → `1.0.0`)

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RateLimitCodexStatusVersionSyncTest(unittest.TestCase):
    """plugins/rate-limit/scripts/codex-rate-limit.sh (系統 B、存続) の検証。

    natsuume-statusline / rate-limit の skills/setup-codex/ (Phase B で削除) は対象外。
    ここでは plugin.json の version と codex-rate-limit.sh に埋め込まれた version 文字列の
    一致だけを検査する。Phase B で rate-limit が 0.4.0 -> 0.5.0 に bump され、埋め込み
    version も同時更新される想定のため、この「一致」検査は Phase A/B どちらの時点でも pass する。
    """

    def test_rate_limit_app_server_client_version_matches_manifest(self) -> None:
        manifest_path = (
            ROOT / "plugins" / "rate-limit" / ".claude-plugin" / "plugin.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        script = (
            ROOT / "plugins" / "rate-limit" / "scripts" / "codex-rate-limit.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(f'"version":"{manifest["version"]}"', script)


if __name__ == "__main__":
    unittest.main()

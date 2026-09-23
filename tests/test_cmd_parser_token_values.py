"""cmd-parser.sh の `tokenize_segment` が返す token 値の platform 非依存性の契約テスト。

契約: `tokenize_segment` が呼び出し元の配列へ書き戻す token 値は、切り出した文字列と
完全に一致する。書き戻しの過程で tilde expansion などの追加の展開・再解釈を受けず、
bash 3.2 系 (macOS の `/bin/bash`) と bash 5 系で同じ値になる。

`printf '%q'` の出力は bash のバージョンで異なり (bash 3.2 は先頭や `=` 直後の `~` を
escape しない)、その出力を `eval` で書き戻すと bash 3.2 だけ tilde expansion が起きる。
本テストは PATH 上の `bash` と `/bin/bash` のうち実在するものすべてで検査するため、
`/bin/bash` が 3.2 系の macOS では書き戻し方式の platform 差を検出できる。

wrapper hook (block-bg-codex-wrapper.sh) の分類結果も、同じ bash 群で token 値の差に
左右されないことを end-to-end で検査する。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CMD_PARSER_LIB = (
    ROOT / "plugins" / "pre-push-review" / "hooks" / "scripts" / "lib" / "cmd-parser.sh"
)
WRAPPER_HOOK = (
    ROOT
    / "plugins"
    / "pre-push-codex-review"
    / "hooks"
    / "scripts"
    / "block-bg-codex-wrapper.sh"
)
WRAPPER = "run-pre-push-codex-review.sh"


def available_bash_binaries() -> list[str]:
    """PATH 上の `bash` と `/bin/bash` のうち実在するものを、実体の重複を除いて返す。"""
    candidates = [shutil.which("bash"), "/bin/bash"]
    binaries: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None or not os.access(candidate, os.X_OK):
            continue
        real = os.path.realpath(candidate)
        if real in seen:
            continue
        seen.add(real)
        binaries.append(candidate)
    return binaries


BASH_BINARIES = available_bash_binaries()


def tokenize_with(bash: str, segment: str) -> list[str]:
    """<bash> で cmd-parser.sh を source し、tokenize_segment の出力配列を NUL 区切りで受け取る。

    segment は argv としてそのまま渡し、シェルの再クオートを経由させない。
    """
    result = subprocess.run(
        [
            bash,
            "-c",
            'source "$1"; tokenize_segment "$2" _toks; '
            'for _t in "${_toks[@]}"; do printf "%s\\0" "$_t"; done',
            "cmd-parser-test",
            str(CMD_PARSER_LIB),
            segment,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{bash}: tokenize_segment が失敗した: {result.stderr.decode('utf-8')}"
        )
    output = result.stdout.decode("utf-8")
    return output.split("\0")[:-1] if output else []


@unittest.skipUnless(BASH_BINARIES, "cmd-parser tests require bash")
class TokenizeSegmentTokenValueTest(unittest.TestCase):
    """token 値が書き戻しで変化しない (全 bash で入力どおりの値になる)。"""

    CASES = (
        ("cat ~root/x", ["cat", "~root/x"]),
        ("cat ~/x", ["cat", "~/x"]),
        ("cat ~", ["cat", "~"]),
        ("cat ~x", ["cat", "~x"]),
        ("a=~/x cmd", ["a=~/x", "cmd"]),
        ("b=x:~/y cmd", ["b=x:~/y", "cmd"]),
        ("~/bin/cat f", ["~/bin/cat", "f"]),
        ("echo a* $HOME `id` \\~", ["echo", "a*", "$HOME", "`id`", "\\~"]),
        ("echo '~/x' \"~/y\"", ["echo", "'~/x'", '"~/y"']),
        ("", []),
    )

    def test_token_values_are_preserved_on_every_bash(self) -> None:
        for bash in BASH_BINARIES:
            for segment, expected in self.CASES:
                with self.subTest(bash=bash, segment=segment):
                    self.assertEqual(tokenize_with(bash, segment), expected)


@unittest.skipUnless(
    BASH_BINARIES and shutil.which("jq"), "hook integration requires bash and jq"
)
class WrapperClassificationTildeConsistencyTest(unittest.TestCase):
    """block-bg-codex-wrapper.sh の分類が全 bash で同じ決定になる (`~` を含む形)。

    agent_type を持たない payload なので、実行形と分類された segment は deny、mention
    候補と分類された segment は allow になる。
    """

    CASES = (
        (f"cat ~root/{WRAPPER}", "allow"),
        (f"cat ~ {WRAPPER}", "allow"),
        (f"~/bin/frobnicate {WRAPPER}", "deny"),
        (f"~/bin/cat {WRAPPER}", "deny"),
        (f"~/bin/bash {WRAPPER}", "deny"),
        (f"~root/bin/bash {WRAPPER}", "deny"),
        (f"~/plugins/pre-push-codex-review/hooks/scripts/{WRAPPER}", "deny"),
        (f"sed -n 1p ~root/{WRAPPER}", "deny"),
        (f"cat ~/x/{WRAPPER}", "allow"),
    )

    def decision(self, bash: str, command: str, cwd: Path) -> str:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        result = subprocess.run(
            [bash, str(WRAPPER_HOOK)],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            cwd=cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        if result.stdout == b"":
            return "allow"
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]

    def test_classification_matches_on_every_bash(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            for bash in BASH_BINARIES:
                for command, expected in self.CASES:
                    with self.subTest(bash=bash, command=command):
                        self.assertEqual(
                            self.decision(bash, command, Path(name)), expected
                        )


if __name__ == "__main__":
    unittest.main()

"""サブエージェントのモデル解決順序に関する agent-discipline の契約を固定する。

Claude Code はサブエージェントのモデルを
`明示 model > agent 定義の frontmatter > CLAUDE_CODE_SUBAGENT_MODEL > メインセッション継承`
の順に解決し、`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` が設定されているときだけ env
(未設定なら main model) が全てを上書きする。`subagent_type: "fork"` のサブエージェントは
model 指定にも env にも依らずメインセッションのモデルを継承する。本ファイルは、この前提に
立つ 5 つの契約を固定する。

- hook 判定表 (``BlockFableSubagentDecisionTableTest``): agent-discipline の
  `block-fable-subagent.sh` を隔離環境の subprocess で実行し、FORCE の有無 × 明示 model ×
  env × session model state × fork の組み合わせごとの deny / allow を ``DECISION_TABLE`` で
  固定する。deny メッセージが「env は明示指定より優先される」という誤った説明を持たず、
  自己修復誘導 (`sonnet` の明示) を保つことも固定する。
- PostModelSwitch 追随 (``PostModelSwitchHookRegistrationTest`` /
  ``UpdateModelOnSwitchScriptTest``): 両 plugin の hooks.json が
  `update-model-on-switch.sh` を `PostModelSwitch` に 1 本登録し、スクリプトが
  `to_model` で session model state を上書きし、pending マーカーを消し、fable ⇄ 非 fable の
  切替でだけ `additionalContext` を出すこと。
- 配送文言 (``DisciplinePromptResolutionOrderTest`` /
  ``SubagentRulesInjectionPremiseTest``): 両 plugin の分業規律 3 種 (計 6 ファイル) の
  `rule:delegation-rules` 節が ``MODEL_RESOLUTION_CANONICAL_SENTENCE`` を持ち、旧前提の
  文言 (``FORBIDDEN_PROMPT_PHRASES``) を持たないこと。`inject-subagent-rules.sh` が
  「subagent は Fable になり得ない」前提を持たないこと。
- 文書 (``AgentDisciplineReadmeDefenseTest`` /
  ``ExperimentalDependencyStatementTest`` / ``HookCommentCurrencyTest``): 主防御が
  `permissions.deny` の `Agent(model:fable)` / `Agent(fork)` であることと既知制約が
  agent-discipline README にあること、experimental 側の env 記述が FORCE 基準であること、
  hook の comment に旧解決順序が残っていないこと。
- version (``AgentDisciplineVersionConsistencyTest``): plugin.json / marketplace.json /
  リポジトリ直下 README / plugin README の 4 箇所が ``PLUGIN_VERSION`` で一致すること。

観測点は public boundary (hook script の stdin / stdout / exit code と state file、
リポジトリ内のファイル内容) に限る。hook を実行するテストは ``TMPDIR`` / ``HOME`` /
``XDG_CACHE_HOME`` を一時ディレクトリへ向け、``CLAUDE_CODE_SUBAGENT_MODEL`` /
``CLAUDE_CODE_SUBAGENT_MODEL_FORCE`` を明示的に設定または未設定にした最小の env で実行する
ため、実リポジトリと利用者の state には触れない。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_PLUGIN = ROOT / "plugins" / "agent-discipline"
FORK_PLUGIN = ROOT / "plugins" / "experimental-agent-discipline"

PLUGIN_NAME = "agent-discipline"
PLUGIN_VERSION = "0.29.0"

MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
REPO_README = ROOT / "README.md"
BASE_README = BASE_PLUGIN / "README.md"
FORK_README = FORK_PLUGIN / "README.md"
BASE_PLUGIN_JSON = BASE_PLUGIN / ".claude-plugin" / "plugin.json"

BLOCK_FABLE = BASE_PLUGIN / "hooks" / "scripts" / "block-fable-subagent.sh"
UPDATE_MODEL_ON_SWITCH = BASE_PLUGIN / "hooks" / "scripts" / "update-model-on-switch.sh"
# hooks.json が登録する command の実体。登録する plugin ごとに実行可能ファイルとして存在する。
UPDATE_MODEL_ON_SWITCH_SCRIPTS = {
    "agent-discipline": UPDATE_MODEL_ON_SWITCH,
    "experimental-agent-discipline": (
        FORK_PLUGIN / "hooks" / "scripts" / "update-model-on-switch.sh"
    ),
}

# plugin ごとの対応ファイル (同じ契約を両 plugin に課す検査で使う)。
HOOKS_JSON = {
    "agent-discipline": BASE_PLUGIN / "hooks" / "hooks.json",
    "experimental-agent-discipline": FORK_PLUGIN / "hooks" / "hooks.json",
}
BLOCK_FABLE_SCRIPTS = {
    "agent-discipline": BLOCK_FABLE,
    "experimental-agent-discipline": (
        FORK_PLUGIN / "hooks" / "scripts" / "block-fable-subagent.sh"
    ),
}
INJECT_SUBAGENT_RULES_SCRIPTS = {
    "agent-discipline": BASE_PLUGIN / "hooks" / "scripts" / "inject-subagent-rules.sh",
    "experimental-agent-discipline": (
        FORK_PLUGIN / "hooks" / "scripts" / "inject-subagent-rules.sh"
    ),
}
FORK_AGENT_DEFINITIONS = {
    "fable-low-worker": FORK_PLUGIN / "agents" / "fable-low-worker.md",
    "fable-low-explorer": FORK_PLUGIN / "agents" / "fable-low-explorer.md",
}

# 分業規律 3 種 × 2 plugin = 6 ファイル。モデル解決順序の記述は全ファイル共通の canonical 文。
DISCIPLINE_PROMPTS = {
    f"{plugin}/{name}": plugin_dir / "hooks" / "prompts" / name
    for plugin, plugin_dir in (
        ("agent-discipline", BASE_PLUGIN),
        ("experimental-agent-discipline", FORK_PLUGIN),
    )
    for name in ("discipline-fable.md", "discipline-sonnet.md", "discipline-opus.md")
}

# PostModelSwitch hook の登録内容 (既存 entry と同じ ${CLAUDE_PLUGIN_ROOT} 相対の書き方)。
POST_MODEL_SWITCH_EVENT = "PostModelSwitch"
POST_MODEL_SWITCH_COMMAND = (
    "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/update-model-on-switch.sh"
)

# session model state / pending マーカーの置き場 (両 hook が共有する)。
STATE_DIR_NAME = "agent-discipline-state"

# 分業規律 6 ファイルの rule:delegation-rules 節に必須の canonical 文 (空白を無視して照合)。
MODEL_RESOLUTION_CANONICAL_SENTENCE = (
    "サブエージェントのモデルは 明示 model > agent 定義の frontmatter >"
    " `CLAUDE_CODE_SUBAGENT_MODEL` > メインセッション継承 の順に解決される。"
    "`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` が設定されている場合のみ、"
    "env (未設定なら main model) が全てを上書きする"
)

# 分業規律 6 ファイル全文から消えていること (空白を無視して照合)。
FORBIDDEN_PROMPT_PHRASES = (
    "model の明示指定や agent 定義の frontmatter より優先され",
    "env が `sonnet` の間は opus を指定しても sonnet で走る",
    "全サブエージェント (Workflow 内部の `agent()` 含む) がその値で実行される",
)

# inject-subagent-rules.sh から消えていること / 書かれていること。
FORBIDDEN_SUBAGENT_RULES_PHRASE = "subagent は Fable になり得ず"
REQUIRED_SUBAGENT_RULES_PHRASE = "fork / frontmatter 経路では Fable になりうる"

# 利用者が user settings に置く主防御の permission rule。
PERMISSION_DENY_RULES = ("Agent(model:fable)", "Agent(fork)")

# agent-discipline README の「既知の制約」節に必要なキーワード。
README_KNOWN_LIMITATION_KEYWORDS = (
    "Agent(model:fable)",
    "Agent(fork)",
    "claude-fable-5-1",
    "frontmatter",
    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE",
)

# agent-discipline README 全文から消えていること (空白を無視して照合)。
FORBIDDEN_README_PHRASES = (
    "主防御はあくまで `CLAUDE_CODE_SUBAGENT_MODEL` env 設定",
    "env 側でカバー",
    "`CLAUDE_CODE_SUBAGENT_MODEL` env > `tool_input.model` 明示指定",
)

# experimental 側 (README / 2 agent 定義) から消えていること (空白を無視して照合)。
FORBIDDEN_EXPERIMENTAL_ENV_PHRASES = (
    "この env は model の明示指定より優先されるため",
    "明示した model より env が優先されて",
    "| `CLAUDE_CODE_SUBAGENT_MODEL` が設定されている |",
    "0. `CLAUDE_CODE_SUBAGENT_MODEL` が fable を指す",
)
REQUIRED_EXPERIMENTAL_DEPENDENCY_PHRASE = (
    "`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` が設定されていないこと"
)

# block-fable-subagent.sh (両 plugin) の comment から消えていること (空白を無視して照合)。
# 判定ステップの番号付けにも `Step 0` の表記は使わない。
FORBIDDEN_HOOK_COMMENT_PHRASES = (
    "CLAUDE_CODE_SUBAGENT_MODEL env > tool_input.model",
    "Step 0",
    "主防御はあくまで",
    "すべてより優先されることを実測検証済み",
)

# 説明文書に書かない経緯記述 (契約対象ファイル全体を対象に、空白を無視して照合)。
FORBIDDEN_HISTORY_PHRASES = ("以前は", "かつては", "旧順序", "2.1.251 で反転")

# deny メッセージに書かない旧解決順序の説明 (FORCE 無効時の deny を対象に照合)。
FORBIDDEN_DENY_PHRASES = ("model の明示指定より優先されて", "env 値に上書きされ")

# UNSET: 引数を「与えなかった」(env 未設定 / key 自体を書かない) ことを表す番兵。
UNSET = object()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def squeeze(text: str) -> str:
    """行頭の comment 記号 (`#` の並び) と空白をすべて除去した文字列を返す。

    soft line-wrap や行頭の comment インデントで文が分断されていても、同じ文言なら
    一致すると判定するための正規化。shell comment の途中で折り返された文言は各行の
    `#` を挟んで連結されるため、行頭の `#` も除いてから空白を落とす。照合する両辺に
    同じ正規化を掛けて使う。
    """
    without_comment_markers = re.sub(r"(?m)^[ \t]*#+", "", text)
    return re.sub(r"\s+", "", without_comment_markers)


def delegation_rules_section(body: str) -> str:
    """`<!-- rule:delegation-rules -->` から次の rule マーカー直前までを返す。"""
    match = re.search(
        r"<!--\s*rule:delegation-rules\s*-->(.*?)(?=<!--\s*rule:|\Z)", body, re.DOTALL
    )
    return "" if match is None else match.group(1)


def markdown_section(body: str, heading: str) -> str:
    """`heading` 行から同レベル以上の次の見出し直前までを返す (見つからなければ空文字)。

    コードフェンス (``` / ~~~) の内側は見出しとして扱わない (フェンス内の shell comment
    `# ...` で節が途切れないようにする)。見出しは `#` の並びの直後に空白がある行だけ。
    """
    lines = body.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    collected: list[str] = []
    inside = False
    fence: str | None = None
    for line in lines:
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
        if not inside:
            if fence is None and line.rstrip() == heading:
                inside = True
            continue
        heading_match = re.match(r"^(#{1,6})\s", line)
        if fence is None and heading_match and len(heading_match.group(1)) <= level:
            break
        collected.append(line)
    return "\n".join(collected)


def json_code_blocks(body: str) -> list[object]:
    """markdown の ```json フェンス内を JSON として読めたものだけ返す。"""
    parsed: list[object] = []
    for block in re.findall(r"```json\n(.*?)```", body, re.DOTALL):
        try:
            parsed.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    return parsed


def row(
    label: str,
    *,
    expect: str,
    force: object = UNSET,
    env: object = UNSET,
    model: object = UNSET,
    subagent_type: object = UNSET,
    session_state: str | None = None,
    pending: bool = False,
    session_id: str = "model-resolution-gate",
    keywords: tuple[str, ...] = (),
) -> dict[str, object]:
    """判定表の 1 行。``expect`` は ``"deny"`` / ``"allow"``。

    ``force`` は env ``CLAUDE_CODE_SUBAGENT_MODEL_FORCE``、``env`` は env
    ``CLAUDE_CODE_SUBAGENT_MODEL``、``model`` / ``subagent_type`` は hook 入力の
    ``tool_input`` の各フィールド (UNSET はいずれも未設定)。``session_state`` は
    ``${TMPDIR}/agent-discipline-state/model-<session_id>`` の内容、``pending`` は同
    ``pending-model-<session_id>`` の有無。``keywords`` は deny 理由に一致すべき正規表現。
    """
    return {
        "label": label,
        "expect": expect,
        "force": force,
        "env": env,
        "model": model,
        "subagent_type": subagent_type,
        "session_state": session_state,
        "pending": pending,
        "session_id": session_id,
        "keywords": keywords,
    }


# deny 理由に求めるキーワード (正規表現)。SELF_REPAIR は「model に sonnet を明示して起動し
# 直す」自己修復誘導 (FORCE 無効時と fork の代替手段として有効な誘導)、NAMES_ENV / NAMES_FORCE
# は実効モデルを決めている env を名指しすること (FORCE 有効時は model の明示では直らないため)。
# NAMES_ENV は `_FORCE` が続かない出現を要求する (FORCE 変数名の接頭辞として現れただけでは
# env を名指ししたことにならない)。
SELF_REPAIR = (r"sonnet",)
NAMES_ENV = (r"CLAUDE_CODE_SUBAGENT_MODEL(?!_FORCE)",)
NAMES_FORCE = (r"CLAUDE_CODE_SUBAGENT_MODEL_FORCE",)

# 判定表。FORCE 有効 (Claude Code の boolean env と同じ `1` / `true` / `yes` / `on`、大文字
# 小文字を区別しない) では実効モデルを
# env (非空ならその値、空なら session model state) とみなし、FORCE 無効 (`0` / `false` /
# 空 / 未設定) では 明示 model > env > 継承 の順に判定する。`fork` は model / env に依らず
# メインセッションのモデルを継承する経路として扱う。
DECISION_TABLE = (
    # --- FORCE 有効: 実効モデルは env (非空) で決まり、明示 model は無視される ---
    row(
        "force-on/env-fable/model-unspecified",
        force="1",
        env="fable",
        expect="deny",
        keywords=NAMES_ENV,
    ),
    row(
        "force-on/env-fable/model-sonnet-explicit",
        force="1",
        env="fable",
        model="sonnet",
        expect="deny",
        keywords=NAMES_ENV,
    ),
    row(
        "force-true/env-full-fable-id/model-opus-explicit",
        force="true",
        env="claude-fable-5-1",
        model="opus",
        expect="deny",
        keywords=NAMES_ENV,
    ),
    row(
        "force-TRUE-uppercase/env-Fable-mixed-case",
        force="TRUE",
        env="Fable",
        expect="deny",
        keywords=NAMES_ENV,
    ),
    row(
        "force-on/env-sonnet/model-fable-explicit",
        force="1",
        env="sonnet",
        model="fable",
        expect="allow",
    ),
    row(
        "force-on/env-sonnet/model-unspecified",
        force="1",
        env="sonnet",
        expect="allow",
    ),
    row(
        "force-on/env-opus-overrides-fable-session-state",
        force="1",
        env="opus",
        session_state="claude-fable-5-1",
        expect="allow",
    ),
    # --- FORCE 有効 + env 空: 実効モデルは session model state (= main model) ---
    row(
        "force-on/env-absent/fable-session/model-sonnet-explicit",
        force="1",
        model="sonnet",
        session_state="claude-fable-5-1",
        expect="deny",
        keywords=NAMES_FORCE,
    ),
    row(
        "force-on/env-empty/fable-session/model-unspecified",
        force="1",
        env="",
        session_state="claude-fable-5-1",
        expect="deny",
        keywords=NAMES_FORCE,
    ),
    row(
        "force-on/env-absent/sonnet-session/model-fable-explicit",
        force="1",
        model="fable",
        session_state="claude-sonnet-5",
        expect="allow",
    ),
    # --- FORCE 無効: 明示 fable は deny ---
    row(
        "force-absent/model-fable-alias",
        model="fable",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "force-0/env-sonnet/model-full-fable-id",
        force="0",
        env="sonnet",
        model="claude-fable-5-1",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "force-false/model-FABLE-uppercase",
        force="false",
        model="FABLE",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "force-empty/model-fable-padded",
        force="",
        model="  fable  ",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    # --- FORCE 無効: 明示非 fable は env が fable でも allow (明示が env に優先する) ---
    row(
        "force-absent/env-fable/model-sonnet-explicit",
        env="fable",
        model="sonnet",
        expect="allow",
    ),
    row(
        "force-0/env-full-fable-id/model-opus-explicit",
        force="0",
        env="claude-fable-5-1",
        model="opus",
        expect="allow",
    ),
    row(
        "force-absent/env-fable/model-haiku-explicit",
        env="fable",
        model="haiku",
        expect="allow",
    ),
    row(
        "force-false/fable-session/model-sonnet-explicit",
        force="false",
        model="sonnet",
        session_state="claude-fable-5-1",
        expect="allow",
    ),
    # --- FORCE 無効: model 未指定 (継承経路) ---
    row(
        "force-absent/env-fable/model-unspecified",
        env="fable",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "force-absent/env-full-fable-id/model-empty-string",
        env="claude-fable-5-1",
        model="",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "force-absent/env-fable/model-inherit",
        env="fable",
        model="inherit",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "force-absent/env-sonnet/fable-session/model-unspecified",
        env="sonnet",
        session_state="claude-fable-5-1",
        expect="allow",
    ),
    row(
        "force-absent/env-absent/fable-session/model-unspecified",
        session_state="claude-fable-5-1",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "force-absent/env-absent/sonnet-session/model-unspecified",
        session_state="claude-sonnet-5",
        expect="allow",
    ),
    row(
        "force-absent/env-absent/state-unknown/pending-marker",
        pending=True,
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "force-absent/env-absent/state-unknown/no-pending-marker",
        expect="allow",
    ),
    # FORCE の真値は Claude Code の boolean env と同じ集合 (`yes` / `on` も有効)。狭く解釈すると
    # host が FORCE している起動を非 FORCE の順序で判定し、明示非 fable を誤って allow する。
    row(
        "force-yes/env-fable/model-sonnet-explicit",
        force="yes",
        env="fable",
        model="sonnet",
        expect="deny",
        keywords=NAMES_ENV,
    ),
    row(
        "force-ON-uppercase/env-sonnet/model-fable-explicit",
        force="ON",
        env="sonnet",
        model="fable",
        expect="allow",
    ),
    # FORCE 有効 + env 空 + state 不明 (pending): 継承先が Fable かどうかを検知できないため
    # deny する。model の明示では直らないため、理由は継承経路の文言ではなく FORCE を名指しして
    # 有効な対処 (one-shot 補正を待つ / env の設定をユーザに依頼) を案内する。
    row(
        "force-on/env-absent/state-unknown/pending-marker/model-sonnet-explicit",
        force="1",
        model="sonnet",
        pending=True,
        expect="deny",
        keywords=NAMES_FORCE,
    ),
    # session_id が空に正規化される経路は state / pending を参照できないため allow
    # (fail-open)。sanitization の有無で結果が反転する入力は隔離 TMPDIR 内では構成できず、
    # この行は結果の契約だけを固定する。
    row(
        "force-absent/unusable-session-id",
        session_id="///",
        expect="allow",
    ),
    # --- fork: model / env を無視してメインセッションのモデルを継承する ---
    row(
        "fork/fable-session/model-unspecified",
        subagent_type="fork",
        session_state="claude-fable-5-1",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "fork/fable-session/model-sonnet-explicit",
        subagent_type="fork",
        model="sonnet",
        session_state="claude-fable-5-1",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "fork/env-sonnet/fable-session/model-unspecified",
        subagent_type="fork",
        env="sonnet",
        session_state="claude-fable-5-1",
        expect="deny",
        keywords=SELF_REPAIR,
    ),
    row(
        "fork/sonnet-session/model-unspecified",
        subagent_type="fork",
        session_state="claude-sonnet-5",
        expect="allow",
    ),
    row(
        "fork/opus-session/model-sonnet-explicit",
        subagent_type="fork",
        model="sonnet",
        session_state="claude-opus-5",
        expect="allow",
    ),
)


class HookSubprocessTestBase(unittest.TestCase):
    """hook を隔離した env / TMPDIR / HOME で実行する共通基盤。"""

    def isolated_env(self, temp: Path, **extra: str) -> dict[str, str]:
        """親プロセスの env を継承せず、PATH と隔離ディレクトリだけを渡す env を作る。"""
        home = temp / "home"
        tmpdir = temp / "tmp"
        cache_home = temp / "cache"
        for directory in (home, tmpdir, cache_home):
            directory.mkdir(exist_ok=True)
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "TMPDIR": str(tmpdir),
            "XDG_CACHE_HOME": str(cache_home),
        }
        env.update(extra)
        return env

    def prepare_state(
        self, tmpdir: Path, session_id: str, *, session_state: str | None, pending: bool
    ) -> Path:
        """session model state / pending マーカーを一時 TMPDIR 配下に用意する。"""
        state_dir = tmpdir / STATE_DIR_NAME
        if session_state is not None or pending:
            state_dir.mkdir(parents=True, exist_ok=True)
        if session_state is not None:
            (state_dir / f"model-{session_id}").write_text(
                session_state, encoding="utf-8"
            )
        if pending:
            (state_dir / f"pending-model-{session_id}").write_text("", encoding="utf-8")
        return state_dir


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockFableSubagentDecisionTableTest(HookSubprocessTestBase):
    """block-fable-subagent.sh の deny / allow を FORCE × 明示 × env × 継承 × fork で固定する。"""

    def run_gate(self, case: dict[str, object]) -> subprocess.CompletedProcess[str]:
        """判定表の 1 行を隔離環境で実行して CompletedProcess を返す。"""
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            env = self.isolated_env(temp)
            if case["force"] is not UNSET:
                env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"] = str(case["force"])
            if case["env"] is not UNSET:
                env["CLAUDE_CODE_SUBAGENT_MODEL"] = str(case["env"])

            self.prepare_state(
                Path(env["TMPDIR"]),
                str(case["session_id"]),
                session_state=case["session_state"],  # type: ignore[arg-type]
                pending=bool(case["pending"]),
            )

            tool_input: dict[str, object] = {}
            if case["model"] is not UNSET:
                tool_input["model"] = case["model"]
            if case["subagent_type"] is not UNSET:
                tool_input["subagent_type"] = case["subagent_type"]
            payload = {
                "hook_event_name": "PreToolUse",
                "session_id": case["session_id"],
                "tool_input": tool_input,
            }

            return subprocess.run(
                ["/bin/bash", str(BLOCK_FABLE)],
                cwd=ROOT,
                env=env,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

    def deny_reason(
        self, result: subprocess.CompletedProcess[str], label: str
    ) -> str:
        self.assertEqual(0, result.returncode, f"{label}: {result.stderr}")
        self.assertTrue(result.stdout.strip(), f"{label}: deny JSON が出力されていない")
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual("PreToolUse", output["hookEventName"], label)
        self.assertEqual("deny", output["permissionDecision"], label)
        return output["permissionDecisionReason"]

    def test_decision_table(self) -> None:
        """判定表の全行が期待どおり deny / allow になる。"""
        for case in DECISION_TABLE:
            label = str(case["label"])
            with self.subTest(case=label):
                result = self.run_gate(case)
                if case["expect"] == "allow":
                    self.assertEqual(0, result.returncode, f"{label}: {result.stderr}")
                    self.assertEqual(
                        "", result.stdout, f"{label}: allow なのに出力がある"
                    )
                    continue
                reason = self.deny_reason(result, label)
                for keyword in case["keywords"]:  # type: ignore[union-attr]
                    self.assertRegex(reason, keyword, label)

    def test_deny_reasons_drop_the_superseded_priority_claim(self) -> None:
        """FORCE 無効時の deny 理由に「env は明示指定より優先」の説明が残っていない。"""
        for case in DECISION_TABLE:
            if case["expect"] != "deny" or case["force"] not in (UNSET, "", "0", "false"):
                continue
            label = str(case["label"])
            with self.subTest(case=label):
                reason = self.deny_reason(self.run_gate(case), label)
                for phrase in FORBIDDEN_DENY_PHRASES:
                    self.assertNotIn(phrase, reason, f"{label}: {phrase}")

    def test_non_pretooluse_event_produces_no_output(self) -> None:
        """`hook_event_name` が PreToolUse 以外なら無出力で exit 0 になる。"""
        for event in ("PostToolUse", "SessionStart", ""):
            with self.subTest(hook_event_name=event):
                with tempfile.TemporaryDirectory() as temporary:
                    env = self.isolated_env(Path(temporary))
                    env["CLAUDE_CODE_SUBAGENT_MODEL"] = "fable"
                    payload = {
                        "hook_event_name": event,
                        "session_id": "model-resolution-gate",
                        "tool_input": {"model": "fable"},
                    }
                    result = subprocess.run(
                        ["/bin/bash", str(BLOCK_FABLE)],
                        cwd=ROOT,
                        env=env,
                        input=json.dumps(payload, ensure_ascii=False),
                        text=True,
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("", result.stdout)


class PostModelSwitchHookRegistrationTest(unittest.TestCase):
    """両 plugin の hooks.json が PostModelSwitch hook を 1 本登録する。"""

    def test_both_plugins_register_the_update_script_once(self) -> None:
        """`PostModelSwitch` に update-model-on-switch.sh の command hook が 1 本ある。"""
        for plugin, path in HOOKS_JSON.items():
            with self.subTest(plugin=plugin):
                hooks = json.loads(read(path))["hooks"]
                self.assertIn(POST_MODEL_SWITCH_EVENT, hooks, path)
                groups = hooks[POST_MODEL_SWITCH_EVENT]
                self.assertEqual(1, len(groups), path)
                self.assertEqual({"hooks"}, set(groups[0]), path)
                self.assertEqual(
                    [
                        {
                            "type": "command",
                            "command": POST_MODEL_SWITCH_COMMAND,
                            "args": [],
                        }
                    ],
                    groups[0]["hooks"],
                    path,
                )

    def test_update_script_exists_and_is_executable(self) -> None:
        """hook を登録する両 plugin で update-model-on-switch.sh が実行可能ファイルとして存在する。"""
        for plugin, path in UPDATE_MODEL_ON_SWITCH_SCRIPTS.items():
            with self.subTest(plugin=plugin):
                self.assertTrue(path.is_file(), path)
                self.assertTrue(os.access(path, os.X_OK), path)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class UpdateModelOnSwitchScriptTest(HookSubprocessTestBase):
    """update-model-on-switch.sh の入出力契約 (state 更新・pending 削除・通知) を固定する。"""

    SESSION_ID = "model-switch-session"

    def run_switch(
        self,
        *,
        from_model: object = "claude-sonnet-5",
        to_model: object = "claude-fable-5-1",
        hook_event_name: str = POST_MODEL_SWITCH_EVENT,
        session_id: str | None = None,
        session_state: str | None = None,
        pending: bool = False,
        state_dir_mode: int | None = None,
    ) -> dict[str, object]:
        """hook を 1 回実行し、結果と state file の内容・pending マーカーの有無を返す。

        ``state_dir_mode`` を与えると state directory の permission をその値にして実行する
        (書込不可の directory で書込失敗経路を再現するため)。実行後は元に戻す。
        """
        session = self.SESSION_ID if session_id is None else session_id
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            env = self.isolated_env(temp, CLAUDE_PLUGIN_ROOT=str(BASE_PLUGIN))
            tmpdir = Path(env["TMPDIR"])
            state_dir = self.prepare_state(
                tmpdir, session, session_state=session_state, pending=pending
            )
            if state_dir_mode is not None:
                os.chmod(state_dir, state_dir_mode)

            payload: dict[str, object] = {
                "hook_event_name": hook_event_name,
                "session_id": session,
            }
            if from_model is not UNSET:
                payload["from_model"] = from_model
            if to_model is not UNSET:
                payload["to_model"] = to_model

            result = subprocess.run(
                ["/bin/bash", str(UPDATE_MODEL_ON_SWITCH)],
                cwd=ROOT,
                env=env,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

            if state_dir_mode is not None:
                os.chmod(state_dir, 0o700)
            state_file = state_dir / f"model-{session}"
            state = (
                state_file.read_text(encoding="utf-8") if state_file.is_file() else None
            )
            pending_left = (state_dir / f"pending-model-{session}").exists()
        return {"result": result, "state": state, "pending": pending_left}

    def additional_context(self, outcome: dict[str, object]) -> str | None:
        """stdout から `hookSpecificOutput.additionalContext` を取り出す (無ければ None)。"""
        result: subprocess.CompletedProcess[str] = outcome["result"]  # type: ignore[assignment]
        self.assertEqual(0, result.returncode, result.stderr)
        if not result.stdout.strip():
            return None
        payload = json.loads(result.stdout)
        hook_output = payload.get("hookSpecificOutput", {})
        context = hook_output.get("additionalContext")
        if context is not None:
            # additionalContext は対象 event 名を伴って初めて session に届く。
            self.assertEqual(POST_MODEL_SWITCH_EVENT, hook_output.get("hookEventName"))
        return context

    def test_to_model_overwrites_the_session_model_state(self) -> None:
        """`to_model` が session model state file に書かれる (state dir が無くても作る)。"""
        outcome = self.run_switch(to_model="claude-fable-5-1")
        self.assertEqual("claude-fable-5-1", str(outcome["state"]).strip())

    def test_existing_state_is_replaced(self) -> None:
        """既存の state file は `to_model` で上書きされる。"""
        outcome = self.run_switch(
            from_model="claude-fable-5-1",
            to_model="claude-sonnet-5",
            session_state="claude-fable-5-1",
        )
        self.assertEqual("claude-sonnet-5", str(outcome["state"]).strip())

    def test_pending_marker_is_removed_and_the_definitive_rules_are_pointed_at(
        self,
    ) -> None:
        """pending マーカーがあれば削除し、確定版ルールの所在を additionalContext で案内する。

        pending は「モデル未確定」であると同時に、常時適用ルールと分業規律の確定版が
        まだ配送されていない (UserPromptSubmit の one-shot 補正が pending を発火条件にする)
        ことを表す。PostModelSwitch が pending を消すと one-shot 補正は走らないので、
        代わりに確定版 (always-<版>.md / discipline-<版>.md) を prompts ディレクトリから
        Read して自己修復するよう案内する。fable 境界をまたがない切替でも案内する。
        """
        prompts_dir = str(BASE_PLUGIN / "hooks" / "prompts")
        cases = {
            "to-sonnet": ("claude-opus-5", "claude-sonnet-5"),
            "to-fable": ("claude-sonnet-5", "claude-fable-5-1"),
        }
        for label, (from_model, to_model) in cases.items():
            with self.subTest(case=label):
                outcome = self.run_switch(
                    from_model=from_model, to_model=to_model, pending=True
                )
                self.assertFalse(outcome["pending"])
                self.assertEqual(to_model, str(outcome["state"]).strip())
                context = self.additional_context(outcome)
                self.assertIsNotNone(context, label)
                assert context is not None
                self.assertIn(prompts_dir, context, label)
                for keyword in ("Read", "always-", "discipline-"):
                    self.assertIn(keyword, context, label)

    @unittest.skipIf(
        os.geteuid() == 0, "root は書込不可の directory でも書けるため判定できない"
    )
    def test_failed_state_write_leaves_the_pending_marker(self) -> None:
        """state の書込に失敗したら pending を消さず、通知も出さずに無音で exit 0 になる。

        pending は「state を信頼しない期間」の合図で、state 無し + pending 無しの状態を
        作ると下流の注入スクリプトと gate が判定不能を検知できなくなる。書込成功後にのみ
        pending を削除する順序を固定する。
        """
        outcome = self.run_switch(
            to_model="claude-fable-5-1", pending=True, state_dir_mode=0o500
        )
        result: subprocess.CompletedProcess[str] = outcome["result"]  # type: ignore[assignment]
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout.strip())
        self.assertIsNone(outcome["state"])
        self.assertTrue(outcome["pending"])

    def test_switch_across_the_fable_boundary_notifies_the_prompts_directory(
        self,
    ) -> None:
        """fable ⇄ 非 fable の切替では分業規律の版の切替を prompts の絶対パス付きで通知する。"""
        prompts_dir = str(BASE_PLUGIN / "hooks" / "prompts")
        cases = {
            "to-fable": ("claude-sonnet-5", "claude-fable-5-1"),
            "from-fable": ("claude-fable-5-1", "claude-opus-5"),
        }
        for label, (from_model, to_model) in cases.items():
            with self.subTest(switch=label):
                context = self.additional_context(
                    self.run_switch(from_model=from_model, to_model=to_model)
                )
                self.assertIsNotNone(context, "additionalContext が出力されていない")
                self.assertIn("分業規律", str(context))
                self.assertIn(prompts_dir, str(context))

    def test_switch_within_non_fable_models_is_silent(self) -> None:
        """非 fable → 非 fable の切替では additionalContext を出さない。"""
        for from_model, to_model in (
            ("claude-sonnet-5", "claude-opus-5"),
            ("claude-opus-5", "claude-haiku-4-5"),
        ):
            with self.subTest(switch=f"{from_model}->{to_model}"):
                self.assertIsNone(
                    self.additional_context(
                        self.run_switch(from_model=from_model, to_model=to_model)
                    )
                )

    def test_missing_or_empty_to_model_leaves_state_and_pending_untouched(self) -> None:
        """`to_model` が空・欠落なら state を更新せず pending も消さずに exit 0 になる。"""
        for label, to_model in (("empty", ""), ("absent", UNSET)):
            with self.subTest(to_model=label):
                outcome = self.run_switch(
                    to_model=to_model, session_state="claude-fable-5-1", pending=True
                )
                result: subprocess.CompletedProcess[str] = outcome["result"]  # type: ignore[assignment]
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("claude-fable-5-1", str(outcome["state"]).strip())
                self.assertTrue(outcome["pending"])

    def test_empty_session_id_does_nothing(self) -> None:
        """`session_id` が空なら state file 名を作れないため何もせず exit 0 になる。"""
        outcome = self.run_switch(session_id="", to_model="claude-fable-5-1")
        result: subprocess.CompletedProcess[str] = outcome["result"]  # type: ignore[assignment]
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNone(outcome["state"])
        self.assertEqual("", result.stdout)

    def test_other_hook_events_do_nothing(self) -> None:
        """`hook_event_name` が PostModelSwitch 以外なら何もせず exit 0 になる。"""
        for event in ("PreModelSwitch", "SessionStart", ""):
            with self.subTest(hook_event_name=event):
                outcome = self.run_switch(
                    hook_event_name=event, to_model="claude-fable-5-1", pending=True
                )
                result: subprocess.CompletedProcess[str] = outcome["result"]  # type: ignore[assignment]
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIsNone(outcome["state"])
                self.assertTrue(outcome["pending"])
                self.assertEqual("", result.stdout)


class DisciplinePromptResolutionOrderTest(unittest.TestCase):
    """分業規律 6 ファイルのモデル解決順序の記述を固定する。"""

    def test_delegation_rules_state_the_canonical_resolution_order(self) -> None:
        """rule:delegation-rules 節に canonical 文がある (空白を無視して照合)。"""
        needle = squeeze(MODEL_RESOLUTION_CANONICAL_SENTENCE)
        for label, path in DISCIPLINE_PROMPTS.items():
            with self.subTest(prompt=label):
                section = delegation_rules_section(read(path))
                self.assertTrue(section, f"{label}: rule:delegation-rules 節が無い")
                self.assertIn(
                    needle,
                    squeeze(section),
                    f"{label}: MODEL_RESOLUTION_CANONICAL_SENTENCE が無い",
                )

    def test_superseded_priority_claims_are_absent(self) -> None:
        """env が明示指定・frontmatter より優先されるという記述が残っていない。"""
        for label, path in DISCIPLINE_PROMPTS.items():
            body = squeeze(read(path))
            for phrase in FORBIDDEN_PROMPT_PHRASES:
                with self.subTest(prompt=label, phrase=phrase):
                    self.assertNotIn(squeeze(phrase), body, label)


class SubagentRulesInjectionPremiseTest(unittest.TestCase):
    """inject-subagent-rules.sh が Fable subagent の存在しうる経路を前提に書かれている。"""

    def test_fable_is_described_as_reachable_through_fork_and_frontmatter(self) -> None:
        """「fork / frontmatter 経路では Fable になりうる」旨が書かれている。"""
        for plugin, path in INJECT_SUBAGENT_RULES_SCRIPTS.items():
            with self.subTest(plugin=plugin):
                self.assertIn(
                    squeeze(REQUIRED_SUBAGENT_RULES_PHRASE), squeeze(read(path)), path
                )

    def test_unreachable_premise_is_absent(self) -> None:
        """「subagent は Fable になり得ない」前提が残っていない。"""
        for plugin, path in INJECT_SUBAGENT_RULES_SCRIPTS.items():
            with self.subTest(plugin=plugin):
                self.assertNotIn(
                    squeeze(FORBIDDEN_SUBAGENT_RULES_PHRASE), squeeze(read(path)), path
                )


class AgentDisciplineReadmeDefenseTest(unittest.TestCase):
    """agent-discipline README が主防御 (permission rule) と既知制約を書いている。"""

    def test_readme_shows_the_permission_deny_settings_example(self) -> None:
        """`permissions.deny` に 2 つの rule を置く JSON の設定例がある。"""
        denies = [
            block["permissions"]["deny"]
            for block in json_code_blocks(read(BASE_README))
            if isinstance(block, dict)
            and isinstance(block.get("permissions"), dict)
            and isinstance(block["permissions"].get("deny"), list)
        ]
        self.assertTrue(denies, "permissions.deny を含む json コードブロックが無い")
        self.assertTrue(
            any(set(PERMISSION_DENY_RULES) <= set(deny) for deny in denies),
            f"{PERMISSION_DENY_RULES} を並べた設定例が無い (検出: {denies})",
        )

    def test_primary_defense_paragraph_points_at_the_permission_rules(self) -> None:
        """主防御を説明する段落が `permissions.deny` を指している。"""
        paragraphs = [
            paragraph
            for paragraph in read(BASE_README).split("\n\n")
            if "主防御" in paragraph
        ]
        self.assertTrue(paragraphs, "主防御を説明する段落が無い")
        self.assertTrue(
            any("permissions.deny" in paragraph for paragraph in paragraphs),
            "主防御の段落が permissions.deny を指していない",
        )

    def test_known_limitations_cover_the_uncatchable_paths(self) -> None:
        """「既知の制約」節が permission rule と hook の捕捉範囲を説明している。"""
        section = markdown_section(read(BASE_README), "## 既知の制約")
        self.assertTrue(section, "## 既知の制約 節が無い")
        for keyword in README_KNOWN_LIMITATION_KEYWORDS:
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, section)

    def test_superseded_claims_are_absent(self) -> None:
        """旧解決順序・「主防御は env」・「env 側でカバー」の記述が残っていない。"""
        body = squeeze(read(BASE_README))
        for phrase in FORBIDDEN_README_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(squeeze(phrase), body)


class ExperimentalDependencyStatementTest(unittest.TestCase):
    """experimental 側の env 前提が FORCE 基準で書かれている。"""

    def test_readme_dependency_section_requires_force_to_be_unset(self) -> None:
        """「依存・前提」節が FORCE 未設定を前提として挙げている。"""
        section = markdown_section(read(FORK_README), "## 依存・前提")
        self.assertTrue(section, "## 依存・前提 節が無い")
        self.assertIn(
            squeeze(REQUIRED_EXPERIMENTAL_DEPENDENCY_PHRASE), squeeze(section)
        )

    def test_agent_definitions_reference_force(self) -> None:
        """2 つの agent 定義が FORCE を起動条件として書いている。"""
        for name, path in FORK_AGENT_DEFINITIONS.items():
            with self.subTest(agent=name):
                self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", read(path), path)

    def test_env_priority_claims_are_absent(self) -> None:
        """env が明示指定より優先されることを理由にした記述が残っていない。"""
        targets = {"README.md": FORK_README, **FORK_AGENT_DEFINITIONS}
        for label, path in targets.items():
            body = squeeze(read(path))
            for phrase in FORBIDDEN_EXPERIMENTAL_ENV_PHRASES:
                with self.subTest(target=label, phrase=phrase):
                    self.assertNotIn(squeeze(phrase), body, label)


class HookCommentCurrencyTest(unittest.TestCase):
    """契約対象ファイルの説明文が現在の解決順序だけを書いている。"""

    def test_block_fable_comments_drop_the_superseded_order(self) -> None:
        """block-fable-subagent.sh の comment に旧解決順序が残っていない。"""
        for plugin, path in BLOCK_FABLE_SCRIPTS.items():
            body = squeeze(read(path))
            for phrase in FORBIDDEN_HOOK_COMMENT_PHRASES:
                with self.subTest(plugin=plugin, phrase=phrase):
                    self.assertNotIn(squeeze(phrase), body, path)

    def test_contract_files_have_no_history_notes(self) -> None:
        """契約対象ファイルに経緯記述が書かれていない。"""
        targets: dict[str, Path] = {
            **DISCIPLINE_PROMPTS,
            **{f"block-fable/{k}": v for k, v in BLOCK_FABLE_SCRIPTS.items()},
            **{f"inject-subagent/{k}": v for k, v in INJECT_SUBAGENT_RULES_SCRIPTS.items()},
            **{f"agents/{k}": v for k, v in FORK_AGENT_DEFINITIONS.items()},
            "update-model-on-switch.sh": UPDATE_MODEL_ON_SWITCH,
        }
        for label, path in targets.items():
            if not path.is_file():
                continue
            body = squeeze(read(path))
            for phrase in FORBIDDEN_HISTORY_PHRASES:
                with self.subTest(target=label, phrase=phrase):
                    self.assertNotIn(squeeze(phrase), body, label)


class AgentDisciplineVersionConsistencyTest(unittest.TestCase):
    """version 整合: plugin.json / marketplace.json / 2 つの README が一致する。"""

    def test_plugin_json_declares_name_and_version(self) -> None:
        """plugin.json の name と version が agent-discipline / 0.29.0 である。"""
        manifest = json.loads(read(BASE_PLUGIN_JSON))
        self.assertEqual(PLUGIN_NAME, manifest["name"])
        self.assertEqual(PLUGIN_VERSION, manifest["version"])

    def test_marketplace_entry_matches_plugin_json_version(self) -> None:
        """marketplace.json に同名 entry があり version が plugin.json と一致する。"""
        marketplace = json.loads(read(MARKETPLACE_JSON))
        entries = [
            plugin for plugin in marketplace["plugins"] if plugin["name"] == PLUGIN_NAME
        ]
        self.assertEqual(1, len(entries))
        self.assertEqual(PLUGIN_VERSION, entries[0]["version"])

    def test_repository_readme_table_lists_the_plugin_version(self) -> None:
        """リポジトリ直下 README の plugin 一覧テーブルに version 行がある。"""
        self.assertIn(
            f"[{PLUGIN_NAME}](#{PLUGIN_NAME}) | {PLUGIN_VERSION}", read(REPO_README)
        )

    def test_plugin_readme_version_heading_declares_the_version(self) -> None:
        """plugin README の `## バージョン` 直下の行が v0.29.0 である。"""
        lines = read(BASE_README).splitlines()
        self.assertIn("## バージョン", lines)
        index = lines.index("## バージョン")
        following = [line.strip() for line in lines[index + 1 :] if line.strip()]
        self.assertTrue(following, "## バージョン の後に本文が無い")
        self.assertEqual(f"v{PLUGIN_VERSION}", following[0])


if __name__ == "__main__":
    unittest.main()

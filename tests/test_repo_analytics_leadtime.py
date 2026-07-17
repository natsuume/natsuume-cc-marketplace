"""repo-analytics `leadtime` skill の compute_leadtime.py に対する契約テスト (issue #288)。

Phase A (spec-first) の位置づけ:

- `compute_leadtime.py` は公開契約 (定数・dataclass・例外・CLI 引数定義・関数
  シグネチャ・docstring) のみが実体を持ち、`load_claim_patterns` /
  `resolve_start` / `resolve_ready` / `resolve_close_linkage` / `compute` /
  `main` の処理本体は `NotImplementedError` を送出する (Phase B で実装する)。
- このファイルは issue #288 Phase A 契約ドキュメント セクション 8 の
  test matrix T1〜T42、Phase B 着手前に rescue 壁打ちで精密化された契約改訂
  8 件に対応する T43〜T47、および Phase B 後の codex review 指摘 6 件
  (rescue 壁打ちで精密化済み) に対応する T48〜T50 を全件実装する。各テスト
  メソッド名・コメントに `T<n>` を付与し対応関係を明示する (T42: 収集範囲外
  リポジトリの merged PR を closer とする issue が `resolve_close_linkage` で
  `"merged_pr_external"` に分類され、`compute` の出力で
  `auxiliarySeries["externalMergedClose"]` に入ることの検証。T43:
  negativeInterval が丸め前の各 phase / leadTimeHours のいずれかの負符号で
  判定され、中央値からの除外が metric 単位であることの検証。T44:
  `exclusions.prTimelineOverflow[].linkedIssues` が `(repo, issue)` の
  複合キーで重複排除されることの検証。T45: closingIssuesReferences 逆引きで
  見つかる qualifying completion PR が manual close より優先して
  `mainSeries` に編入されることの検証。T46: `--boundaries-file` の検証失敗が
  `main` を exit code 2 にすることの検証。T47 (任意): 同一 `at` を持つ
  boundaries が `(at, id)` で安定に区間化されることの検証。T48: `boundaries`
  未指定時に出力の `boundaries` が `[]` になること、および順不同の
  boundaries 入力が `(at, id)` 順に正規化されて `id`/`label`/`at` (UTC `Z`
  文字列) のみを持つ要素として echo されることの検証。T49: issue と選択
  qualifying PR が別リポジトリ (cross-repo closing reference) のとき、
  `mainSeries` レコードの `repo` が issue 側、`prRepo` が選択 PR 側の
  canonical repo になることの検証。T50: JSONL 入力 (comment / PR の
  createdAt 等) に tz 情報の無い naive timestamp が含まれる行があると
  `main` が exit code 2 を返し、stdout に結果 JSON を出力しないことの検証)。
- 契約の「存在」を検証するテスト (`ContractExistenceTests`) は Phase A 時点で
  pass する。挙動を検証するテスト (T1〜T39) は本物の期待値アサーションを
  書いたうえで実装本体を直接呼び出す (`assertRaises(NotImplementedError)` で
  くるまない)。そのため Phase A では `NotImplementedError` が捕捉されずに
  伝播し、unittest 上は ERROR として報告される。これは意図した red 状態であり、
  Phase B で実装が入るまで修正しない。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "repo-analytics"
    / "skills"
    / "leadtime"
    / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _load_compute_leadtime():
    """`_SCRIPTS_DIR` を sys.path に加えたうえで compute_leadtime モジュールを import する。

    通常の `import compute_leadtime` は「sys.path 操作より前に import 文が来る」
    という lint 制約 (E402) に抵触するため、既存テスト
    (`tests/test_sync_codex_marketplace.py`) と同じ `importlib.util` 経由の
    明示 import で置き換える。
    """
    spec = importlib.util.spec_from_file_location(
        "compute_leadtime", _SCRIPTS_DIR / "compute_leadtime.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import compute_leadtime from {_SCRIPTS_DIR}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses は forward annotation を sys.modules 経由で解決するため登録しておく。
    sys.modules["compute_leadtime"] = module
    spec.loader.exec_module(module)
    return module


compute_leadtime = _load_compute_leadtime()


# ---------------------------------------------------------------------------
# fixture helpers (dict を組み立てるだけ。外部ファイル・ネットワーク・git は使わない)
# ---------------------------------------------------------------------------


def make_issue(
    *,
    number,
    repo="owner/name",
    title="t",
    state="OPEN",
    state_reason=None,
    created_at="2026-01-01T00:00:00Z",
    closed_at=None,
    timeline_nodes=None,
    total_count=None,
):
    nodes = timeline_nodes or []
    return {
        "repo": repo,
        "number": number,
        "title": title,
        "state": state,
        "stateReason": state_reason,
        "createdAt": created_at,
        "closedAt": closed_at,
        "timelineItems": {
            "totalCount": total_count if total_count is not None else len(nodes),
            "nodes": nodes,
        },
    }


def make_pr(
    *,
    number,
    repo="owner/name",
    state="MERGED",
    is_draft=False,
    created_at="2026-01-01T00:00:00Z",
    merged_at=None,
    additions=0,
    deletions=0,
    timeline_nodes=None,
    closing_issues=None,
    total_count=None,
    closing_issues_total_count=None,
):
    nodes = timeline_nodes or []
    closing_issue_nodes = closing_issues or []
    return {
        "repo": repo,
        "number": number,
        "state": state,
        "isDraft": is_draft,
        "createdAt": created_at,
        "mergedAt": merged_at,
        "additions": additions,
        "deletions": deletions,
        "timelineItems": {
            "totalCount": total_count if total_count is not None else len(nodes),
            "nodes": nodes,
        },
        "closingIssuesReferences": {
            "totalCount": (
                closing_issues_total_count
                if closing_issues_total_count is not None
                else len(closing_issue_nodes)
            ),
            "nodes": closing_issue_nodes,
        },
    }


def labeled_event(created_at, label="ai:in-progress"):
    return {
        "__typename": "LabeledEvent",
        "createdAt": created_at,
        "label": {"name": label},
    }


def unlabeled_event(created_at, label="ai:in-progress"):
    return {
        "__typename": "UnlabeledEvent",
        "createdAt": created_at,
        "label": {"name": label},
    }


def issue_comment(created_at, body):
    return {"__typename": "IssueComment", "createdAt": created_at, "body": body}


def closed_event(created_at, closer=None):
    return {"__typename": "ClosedEvent", "createdAt": created_at, "closer": closer}


def reopened_event(created_at):
    return {"__typename": "ReopenedEvent", "createdAt": created_at}


def pr_closer(number, repo="owner/name", merged=True):
    return {
        "__typename": "PullRequest",
        "number": number,
        "repository": {"nameWithOwner": repo},
        "merged": merged,
    }


def commit_closer(oid="deadbeef"):
    return {"__typename": "Commit", "oid": oid}


def ready_event(created_at):
    return {"__typename": "ReadyForReviewEvent", "createdAt": created_at}


def draft_event(created_at):
    return {"__typename": "ConvertToDraftEvent", "createdAt": created_at}


def make_claim_patterns(*, in_progress_label="ai:in-progress", strict=None, loose=None):
    """SKILL.md の「claim 判定パターン (正本)」JSON block と同じ内容を dataclass 化する。"""
    strict = (
        strict
        if strict is not None
        else [
            compute_leadtime.ClaimPattern(
                id="lock-claim", regex_template=r"^🔒 ai:claim branch="
            ),
            compute_leadtime.ClaimPattern(
                id="legacy-backtick-claim",
                regex_template=r"^Claim: `claim-{issue_number}-",
            ),
        ]
    )
    loose = (
        loose
        if loose is not None
        else [
            compute_leadtime.ClaimPattern(
                id="loose-ai-claim", regex_template=r"ai:claim"
            ),
            compute_leadtime.ClaimPattern(
                id="loose-claim-prefix", regex_template=r"claim-{issue_number}-"
            ),
        ]
    )
    return compute_leadtime.ClaimPatterns(
        in_progress_label=in_progress_label, strict=strict, loose=loose
    )


def make_started_case(
    *,
    issue_number,
    pr_number,
    repo="owner/name",
    start_at,
    pr_created_at=None,
    ready_at=None,
    merged_at=None,
    additions=0,
    deletions=0,
):
    """1 件の『着手済み issue → merged PR』の主系列 fixture (issue, pr) を組み立てる。"""
    pr_created_at = pr_created_at or start_at
    ready_at = ready_at or pr_created_at
    merged_at = merged_at or ready_at
    issue = make_issue(
        repo=repo,
        number=issue_number,
        state="CLOSED",
        state_reason="COMPLETED",
        created_at=start_at,
        closed_at=merged_at,
        timeline_nodes=[
            issue_comment(start_at, "🔒 ai:claim branch=x"),
            closed_event(merged_at, pr_closer(pr_number, repo=repo, merged=True)),
        ],
    )
    pr = make_pr(
        repo=repo,
        number=pr_number,
        created_at=pr_created_at,
        merged_at=merged_at,
        additions=additions,
        deletions=deletions,
        timeline_nodes=[ready_event(ready_at)],
        closing_issues=[
            {"number": issue_number, "repository": {"nameWithOwner": repo}}
        ],
    )
    return issue, pr


def write_claim_patterns_file(path: Path, patterns_dict: dict) -> None:
    path.write_text(json.dumps(patterns_dict), encoding="utf-8")


DEFAULT_PATTERNS_DICT = {
    "inProgressLabel": "ai:in-progress",
    "strict": [
        {"id": "lock-claim", "regex": "^🔒 ai:claim branch="},
        {"id": "legacy-backtick-claim", "regex": "^Claim: `claim-{issue_number}-"},
    ],
    "loose": [
        {"id": "loose-ai-claim", "regex": "ai:claim"},
        {"id": "loose-claim-prefix", "regex": "claim-{issue_number}-"},
    ],
}


# ---------------------------------------------------------------------------
# 契約の「存在」テスト (Phase A で pass する)
# ---------------------------------------------------------------------------


class ContractExistenceTests(unittest.TestCase):
    def test_module_importable_and_has_public_seam(self):
        for name in (
            "SCHEMA_VERSION",
            "SIZE_BANDS",
            "ClaimPatternsError",
            "ClaimPatterns",
            "StartResolution",
            "ReadyResolution",
            "CloseLinkage",
            "load_claim_patterns",
            "resolve_start",
            "resolve_ready",
            "resolve_close_linkage",
            "compute",
            "parse_args",
            "main",
        ):
            self.assertTrue(
                hasattr(compute_leadtime, name), f"missing contract symbol: {name}"
            )

    def test_schema_version_is_1(self):
        self.assertEqual(compute_leadtime.SCHEMA_VERSION, 1)

    def test_size_bands_definition(self):
        self.assertEqual(
            compute_leadtime.SIZE_BANDS,
            [("S", 0, 50), ("M", 51, 300), ("L", 301, 1000), ("XL", 1001, None)],
        )

    def test_claim_patterns_dataclass_holds_fields(self):
        patterns = make_claim_patterns()
        self.assertEqual(patterns.in_progress_label, "ai:in-progress")
        self.assertEqual(len(patterns.strict), 2)
        self.assertEqual(len(patterns.loose), 2)
        self.assertEqual(patterns.strict[0].id, "lock-claim")

    def test_start_resolution_dataclass_holds_fields(self):
        resolution = compute_leadtime.StartResolution(
            first_start=None, last_start=None, strict_sources=[], loose_only_matches=[]
        )
        self.assertIsNone(resolution.first_start)
        self.assertIsNone(resolution.last_start)
        self.assertEqual(resolution.strict_sources, [])
        self.assertEqual(resolution.loose_only_matches, [])

    def test_ready_resolution_dataclass_holds_fields(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        resolution = compute_leadtime.ReadyResolution(
            ready_at=now, redraft_count=2, via_draft=True
        )
        self.assertEqual(resolution.ready_at, now)
        self.assertEqual(resolution.redraft_count, 2)
        self.assertTrue(resolution.via_draft)

    def test_close_linkage_dataclass_holds_fields(self):
        linkage = compute_leadtime.CloseLinkage(
            category="merged_pr", linked_pr={"repo": "owner/name", "number": 1}
        )
        self.assertEqual(linkage.category, "merged_pr")
        self.assertEqual(linkage.linked_pr, {"repo": "owner/name", "number": 1})

        open_linkage = compute_leadtime.CloseLinkage(category="open", linked_pr=None)
        self.assertIsNone(open_linkage.linked_pr)

    def test_claim_patterns_error_is_exception_subclass(self):
        self.assertTrue(issubclass(compute_leadtime.ClaimPatternsError, Exception))

    def test_parse_args_required_fields(self):
        namespace = compute_leadtime.parse_args(
            [
                "--issues",
                "issues.jsonl",
                "--prs",
                "prs.jsonl",
                "--claim-patterns-file",
                "patterns.json",
                "--as-of",
                "2026-07-17T04:00:00Z",
            ]
        )
        self.assertEqual(namespace.issues, Path("issues.jsonl"))
        self.assertEqual(namespace.prs, Path("prs.jsonl"))
        self.assertEqual(namespace.claim_patterns_file, Path("patterns.json"))
        self.assertEqual(namespace.as_of, "2026-07-17T04:00:00Z")
        self.assertIsNone(namespace.since)
        self.assertIsNone(namespace.boundaries_file)

    def test_parse_args_optional_fields(self):
        namespace = compute_leadtime.parse_args(
            [
                "--issues",
                "issues.jsonl",
                "--prs",
                "prs.jsonl",
                "--claim-patterns-file",
                "patterns.json",
                "--as-of",
                "2026-07-17T04:00:00Z",
                "--since",
                "2026-01-01",
                "--boundaries-file",
                "boundaries.json",
            ]
        )
        self.assertEqual(namespace.since, "2026-01-01")
        self.assertEqual(namespace.boundaries_file, Path("boundaries.json"))

    def test_parse_args_missing_required_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            compute_leadtime.parse_args(["--issues", "issues.jsonl"])


# ---------------------------------------------------------------------------
# T1〜T8: resolve_start
# ---------------------------------------------------------------------------


class ResolveStartTests(unittest.TestCase):
    def setUp(self):
        self.patterns = make_claim_patterns()

    def test_t1_strict_comment_only_sets_first_and_last(self):
        issue = make_issue(
            number=1,
            timeline_nodes=[
                issue_comment("2026-01-02T03:04:05Z", "🔒 ai:claim branch=foo")
            ],
        )
        result = compute_leadtime.resolve_start(issue, self.patterns)
        expected = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        self.assertEqual(result.first_start, expected)
        self.assertEqual(result.last_start, expected)
        self.assertEqual(len(result.strict_sources), 1)
        self.assertEqual(result.strict_sources[0]["kind"], "comment")
        self.assertEqual(result.strict_sources[0]["patternId"], "lock-claim")

    def test_t2_labeled_event_only_sets_first_and_last(self):
        issue = make_issue(
            number=2, timeline_nodes=[labeled_event("2026-01-03T00:00:00Z")]
        )
        result = compute_leadtime.resolve_start(issue, self.patterns)
        expected = datetime(2026, 1, 3, tzinfo=timezone.utc)
        self.assertEqual(result.first_start, expected)
        self.assertEqual(result.last_start, expected)
        self.assertEqual(result.strict_sources[0]["kind"], "label")
        self.assertIsNone(result.strict_sources[0]["patternId"])

    def test_t3_both_present_earlier_wins_first(self):
        issue = make_issue(
            number=3,
            timeline_nodes=[
                labeled_event("2026-01-05T00:00:00Z"),
                issue_comment("2026-01-01T00:00:00Z", "🔒 ai:claim branch=x"),
            ],
        )
        result = compute_leadtime.resolve_start(issue, self.patterns)
        self.assertEqual(result.first_start, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result.last_start, datetime(2026, 1, 5, tzinfo=timezone.utc))

    def test_t4_issue_number_placeholder_substitution(self):
        # legacy-backtick-claim: "^Claim: `claim-{issue_number}-" は
        # issue 42 の comment には一致するが issue 43 には一致しない。
        issue_42 = make_issue(
            number=42,
            timeline_nodes=[
                issue_comment("2026-01-01T00:00:00Z", "Claim: `claim-42-abcdef`")
            ],
        )
        issue_43 = make_issue(
            number=43,
            timeline_nodes=[
                issue_comment("2026-01-01T00:00:00Z", "Claim: `claim-42-abcdef`")
            ],
        )
        result_42 = compute_leadtime.resolve_start(issue_42, self.patterns)
        result_43 = compute_leadtime.resolve_start(issue_43, self.patterns)
        self.assertIsNotNone(result_42.first_start)
        self.assertIsNone(result_43.first_start)

    def test_t5_labeled_unlabeled_labeled_first_and_last_differ(self):
        issue = make_issue(
            number=5,
            timeline_nodes=[
                labeled_event("2026-01-01T00:00:00Z"),
                unlabeled_event("2026-01-02T00:00:00Z"),
                labeled_event("2026-01-03T00:00:00Z"),
            ],
        )
        result = compute_leadtime.resolve_start(issue, self.patterns)
        self.assertEqual(result.first_start, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result.last_start, datetime(2026, 1, 3, tzinfo=timezone.utc))

    def test_t6_loose_only_is_not_a_start_candidate(self):
        issue = make_issue(
            number=6,
            timeline_nodes=[
                issue_comment("2026-01-01T00:00:00Z", "started work, ai:claim soon")
            ],
        )
        result = compute_leadtime.resolve_start(issue, self.patterns)
        self.assertIsNone(result.first_start)
        self.assertIsNone(result.last_start)
        self.assertEqual(len(result.loose_only_matches), 1)
        self.assertEqual(
            result.loose_only_matches[0]["commentCreatedAt"],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def test_t7_no_marker_yields_none(self):
        issue = make_issue(number=7, timeline_nodes=[])
        result = compute_leadtime.resolve_start(issue, self.patterns)
        self.assertIsNone(result.first_start)
        self.assertIsNone(result.last_start)
        self.assertEqual(result.strict_sources, [])
        self.assertEqual(result.loose_only_matches, [])

    def test_t8_strict_requires_line_start_but_multiline_applies(self):
        # 1 行目は先頭に空白があるため "^" に不一致。2 行目は行頭で一致 (re.MULTILINE)。
        body = " 🔒 ai:claim branch=x\n🔒 ai:claim branch=y"
        issue = make_issue(
            number=8, timeline_nodes=[issue_comment("2026-01-05T00:00:00Z", body)]
        )
        result = compute_leadtime.resolve_start(issue, self.patterns)
        expected = datetime(2026, 1, 5, tzinfo=timezone.utc)
        self.assertEqual(result.first_start, expected)
        self.assertEqual(len(result.strict_sources), 1)
        self.assertEqual(result.strict_sources[0]["patternId"], "lock-claim")


# ---------------------------------------------------------------------------
# T9〜T11: resolve_ready
# ---------------------------------------------------------------------------


class ResolveReadyTests(unittest.TestCase):
    def test_t9_single_ready_event(self):
        pr = make_pr(
            number=9,
            created_at="2026-01-01T00:00:00Z",
            timeline_nodes=[ready_event("2026-01-02T00:00:00Z")],
        )
        result = compute_leadtime.resolve_ready(pr)
        self.assertEqual(result.ready_at, datetime(2026, 1, 2, tzinfo=timezone.utc))
        self.assertTrue(result.via_draft)
        self.assertEqual(result.redraft_count, 0)

    def test_t10_ready_at_creation_then_draft_then_ready_counts_redraft(self):
        # T10: PR 作成時点で ready → ConvertToDraftEvent (1 件) → ReadyForReviewEvent
        # (1 件、CTD の createdAt < RFR の createdAt)。旧定義 (RFR 数 - 1 = 0) と
        # 新定義 (CTD 数 = 1) で結果が異なるため、新定義 (redraft_count は
        # ConvertToDraftEvent の件数) の回帰テストとして機能する。
        pr = make_pr(
            number=10,
            created_at="2026-01-01T00:00:00Z",
            timeline_nodes=[
                draft_event("2026-01-02T00:00:00Z"),
                ready_event("2026-01-03T00:00:00Z"),
            ],
        )
        result = compute_leadtime.resolve_ready(pr)
        self.assertEqual(result.ready_at, datetime(2026, 1, 3, tzinfo=timezone.utc))
        self.assertEqual(result.redraft_count, 1)
        self.assertTrue(result.via_draft)

    def test_t11_no_events_falls_back_to_created_at(self):
        pr = make_pr(number=11, created_at="2026-01-01T00:00:00Z", timeline_nodes=[])
        result = compute_leadtime.resolve_ready(pr)
        self.assertEqual(result.ready_at, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertFalse(result.via_draft)
        self.assertEqual(result.redraft_count, 0)

    def test_t39_resolve_ready_open_draft_returns_none(self):
        # T39: state="OPEN" かつ isDraft=True (現在 draft に戻っている PR) は
        # 過去に ReadyForReviewEvent があっても ready_at が None になる
        # (改訂 1: draft に戻った PR は ready 未到達として扱う)。
        pr = make_pr(
            number=39,
            state="OPEN",
            is_draft=True,
            created_at="2026-01-01T00:00:00Z",
            timeline_nodes=[ready_event("2026-01-02T00:00:00Z")],
        )
        result = compute_leadtime.resolve_ready(pr)
        self.assertIsNone(result.ready_at)


# ---------------------------------------------------------------------------
# T12〜T17: resolve_close_linkage
# ---------------------------------------------------------------------------


class ResolveCloseLinkageTests(unittest.TestCase):
    def test_t12_final_closed_event_merged_pr(self):
        issue = make_issue(
            number=12,
            state="CLOSED",
            state_reason="COMPLETED",
            timeline_nodes=[closed_event("2026-01-05T00:00:00Z", pr_closer(20))],
        )
        prs_by_key = {
            ("owner/name", 20): make_pr(
                number=20,
                created_at="2026-01-01T00:00:00Z",
                merged_at="2026-01-05T00:00:00Z",
            )
        }
        result = compute_leadtime.resolve_close_linkage(issue, prs_by_key)
        self.assertEqual(result.category, "merged_pr")
        self.assertEqual(result.linked_pr, {"repo": "owner/name", "number": 20})

    def test_t13_close_reopen_close_uses_last_closed_event(self):
        issue = make_issue(
            number=13,
            state="CLOSED",
            state_reason="COMPLETED",
            timeline_nodes=[
                closed_event("2026-01-02T00:00:00Z", pr_closer(21, merged=False)),
                reopened_event("2026-01-03T00:00:00Z"),
                closed_event("2026-01-04T00:00:00Z", pr_closer(22)),
            ],
        )
        prs_by_key = {
            ("owner/name", 22): make_pr(
                number=22,
                created_at="2026-01-01T00:00:00Z",
                merged_at="2026-01-04T00:00:00Z",
            )
        }
        result = compute_leadtime.resolve_close_linkage(issue, prs_by_key)
        self.assertEqual(result.category, "merged_pr")
        self.assertEqual(result.linked_pr, {"repo": "owner/name", "number": 22})

    def test_t14_commit_closer(self):
        issue = make_issue(
            number=14,
            state="CLOSED",
            state_reason="COMPLETED",
            timeline_nodes=[closed_event("2026-01-02T00:00:00Z", commit_closer())],
        )
        result = compute_leadtime.resolve_close_linkage(issue, {})
        self.assertEqual(result.category, "commit")
        self.assertIsNone(result.linked_pr)

    def test_t15_no_closer_is_manual(self):
        issue = make_issue(
            number=15,
            state="CLOSED",
            state_reason="COMPLETED",
            timeline_nodes=[closed_event("2026-01-02T00:00:00Z", None)],
        )
        result = compute_leadtime.resolve_close_linkage(issue, {})
        self.assertEqual(result.category, "manual")
        self.assertIsNone(result.linked_pr)

    def test_t16_state_reason_not_planned(self):
        issue = make_issue(
            number=16,
            state="CLOSED",
            state_reason="NOT_PLANNED",
            timeline_nodes=[closed_event("2026-01-02T00:00:00Z", None)],
        )
        result = compute_leadtime.resolve_close_linkage(issue, {})
        self.assertEqual(result.category, "not_planned")

    def test_t17_unmerged_pr_closer(self):
        issue = make_issue(
            number=17,
            state="CLOSED",
            state_reason="COMPLETED",
            timeline_nodes=[
                closed_event("2026-01-02T00:00:00Z", pr_closer(23, merged=False))
            ],
        )
        result = compute_leadtime.resolve_close_linkage(issue, {})
        self.assertEqual(result.category, "unmerged_pr")


# ---------------------------------------------------------------------------
# T18〜T27, T31〜T34: compute
# ---------------------------------------------------------------------------


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self.patterns = make_claim_patterns()

    def test_t18_open_started_issue_is_censored_with_elapsed_lower_bound(self):
        issue = make_issue(
            repo="owner/name",
            number=300,
            state="OPEN",
            created_at="2026-07-01T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-10T00:00:00Z", "🔒 ai:claim branch=x")
            ],
        )
        as_of = datetime(2026, 7, 17, 0, 0, 0, tzinfo=timezone.utc)
        result = compute_leadtime.compute([issue], [], self.patterns, as_of, None, None)
        self.assertEqual(result["mainSeries"], [])
        self.assertEqual(len(result["censored"]), 1)
        entry = result["censored"][0]
        self.assertEqual(entry["repo"], "owner/name")
        self.assertEqual(entry["issue"], 300)
        expected_hours = round(
            (as_of - datetime(2026, 7, 10, tzinfo=timezone.utc)).total_seconds() / 3600,
            2,
        )
        self.assertEqual(entry["elapsedHoursLowerBound"], expected_hours)

    def test_t19_zero_marker_repo_has_zero_coverage_no_exception(self):
        issue = make_issue(
            repo="owner/name",
            number=1,
            state="CLOSED",
            state_reason="COMPLETED",
            created_at="2026-07-01T00:00:00Z",
            closed_at="2026-07-02T00:00:00Z",
            timeline_nodes=[closed_event("2026-07-02T00:00:00Z", None)],
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute([issue], [], self.patterns, as_of, None, None)
        coverage_entries = [
            row for row in result["markerCoverage"] if row["repo"] == "owner/name"
        ]
        self.assertEqual(len(coverage_entries), 1)
        self.assertEqual(coverage_entries[0]["withMarker"], 0)
        self.assertEqual(coverage_entries[0]["coverage"], 0.0)

    def test_t20_no_closed_issues_still_yields_pr_series(self):
        pr = make_pr(
            repo="owner/name",
            number=50,
            created_at="2026-07-01T00:00:00Z",
            merged_at="2026-07-03T00:00:00Z",
            timeline_nodes=[ready_event("2026-07-02T00:00:00Z")],
            additions=10,
            deletions=5,
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute([], [pr], self.patterns, as_of, None, None)
        self.assertEqual(result["mainSeries"], [])
        self.assertEqual(len(result["prSeries"]), 1)
        self.assertEqual(result["prSeries"][0]["pr"], 50)

    def test_t21_timeline_overflow_excludes_from_main_series(self):
        # T21 (拡張): timeline overflow の issue は mainSeries だけでなく
        # markerCoverage (unknownTimeline に計上・coverage 分母から除外)・
        # claimDetection・dataQuality.notStartedClosedIssues からも除外される。
        # このテストの repo・月は overflow issue 2 件 (issue 5: マーカーあり、
        # issue 6: マーカーなし) だけがクローズ済み issue であるため、
        # markerCoverage の coverage は「overflow issue しか無い repo×月」の
        # ケースとして None になる。
        issue, pr = make_started_case(
            issue_number=5,
            pr_number=60,
            start_at="2026-07-01T00:00:00Z",
            merged_at="2026-07-02T00:00:00Z",
        )
        fetched = len(issue["timelineItems"]["nodes"])
        issue["timelineItems"]["totalCount"] = fetched + 500

        issue_no_marker = make_issue(
            repo="owner/name",
            number=6,
            state="CLOSED",
            state_reason="COMPLETED",
            created_at="2026-07-01T00:00:00Z",
            closed_at="2026-07-02T00:00:00Z",
            timeline_nodes=[closed_event("2026-07-02T00:00:00Z", None)],
        )
        fetched_no_marker = len(issue_no_marker["timelineItems"]["nodes"])
        issue_no_marker["timelineItems"]["totalCount"] = fetched_no_marker + 500

        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue, issue_no_marker], [pr], self.patterns, as_of, None, None
        )
        self.assertEqual(result["mainSeries"], [])
        overflow = result["exclusions"]["timelineOverflow"]
        self.assertEqual(len(overflow), 2)
        overflow_by_issue = {entry["issue"]: entry for entry in overflow}
        self.assertEqual(overflow_by_issue[5]["totalCount"], fetched + 500)
        self.assertEqual(overflow_by_issue[5]["fetched"], fetched)
        self.assertEqual(overflow_by_issue[6]["totalCount"], fetched_no_marker + 500)
        self.assertEqual(overflow_by_issue[6]["fetched"], fetched_no_marker)

        coverage_entries = [
            row for row in result["markerCoverage"] if row["repo"] == "owner/name"
        ]
        self.assertEqual(len(coverage_entries), 1)
        coverage_entry = coverage_entries[0]
        self.assertEqual(coverage_entry["month"], "2026-07")
        self.assertEqual(coverage_entry["closedIssues"], 0)
        self.assertEqual(coverage_entry["withMarker"], 0)
        self.assertEqual(coverage_entry["unknownTimeline"], 2)
        self.assertIsNone(coverage_entry["coverage"])

        self.assertEqual(result["claimDetection"]["strictIssues"], 0)
        self.assertEqual(result["claimDetection"]["looseOnlyIssues"], [])
        self.assertEqual(result["dataQuality"]["notStartedClosedIssues"], 0)

    def test_t22_since_boundary_is_inclusive_on_first_start_date(self):
        issue_included, pr_included = make_started_case(
            issue_number=1,
            pr_number=101,
            start_at="2026-07-10T00:00:00Z",
            merged_at="2026-07-11T00:00:00Z",
        )
        issue_excluded, pr_excluded = make_started_case(
            issue_number=2,
            pr_number=102,
            start_at="2026-07-09T12:00:00Z",
            merged_at="2026-07-11T00:00:00Z",
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue_included, issue_excluded],
            [pr_included, pr_excluded],
            self.patterns,
            as_of,
            date(2026, 7, 10),
            None,
        )
        issues_in_main = {entry["issue"] for entry in result["mainSeries"]}
        self.assertIn(1, issues_in_main)
        self.assertNotIn(2, issues_in_main)

    def test_t23_weekly_cohort_is_keyed_by_first_start_week_not_closed_week(self):
        issue, pr = make_started_case(
            issue_number=1,
            pr_number=201,
            start_at="2026-07-10T00:00:00Z",
            merged_at="2026-07-20T00:00:00Z",
        )
        start_week = date(2026, 7, 10).strftime("%G-W%V")
        closed_week = date(2026, 7, 20).strftime("%G-W%V")
        self.assertNotEqual(start_week, closed_week, "fixture must span two ISO weeks")
        as_of = datetime(2026, 7, 21, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )
        weeks = {cohort["week"] for cohort in result["weeklyCohorts"]}
        self.assertIn(start_week, weeks)
        self.assertNotIn(closed_week, weeks)

    def test_t24_negative_interval_excluded_from_median_per_metric(self):
        # T24 (metric 単位除外の契約に更新): このレコードは
        # prCreatedToReady (-3h) と leadTimeHours (-2h) が丸め前で負のため
        # negativeInterval == True になるが、startToPrCreated (+1h) と
        # readyToMerge (+4h) はそれぞれ正であり、「レコード全体を全中央値
        # から除外する」のではなく metric 単位で除外することを検証する:
        # medianLeadTimeHours (leadTimeHours < 0 のため除外) と
        # phaseMedians.prCreatedToReady (自身が負のため除外) は None になる
        # 一方、phaseMedians.startToPrCreated / phaseMedians.readyToMerge は
        # 自身が非負のためこのレコードを含めて計算される。
        issue, pr = make_started_case(
            issue_number=1,
            pr_number=301,
            start_at="2026-07-10T12:00:00Z",
            pr_created_at="2026-07-10T13:00:00Z",
            ready_at="2026-07-10T10:00:00Z",  # firstStartAt より前 (異常データ)
            merged_at="2026-07-10T14:00:00Z",
            additions=30,
            deletions=0,
        )
        boundaries = [
            {
                "id": "evt-1",
                "label": "event 1",
                "at": datetime(2026, 7, 5, tzinfo=timezone.utc),
            },
        ]
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, boundaries
        )
        self.assertEqual(len(result["mainSeries"]), 1)
        self.assertTrue(result["mainSeries"][0]["negativeInterval"])
        self.assertEqual(result["dataQuality"]["negativeIntervalCount"], 1)
        self.assertEqual(len(result["weeklyCohorts"]), 1)
        cohort = result["weeklyCohorts"][0]
        self.assertIsNone(cohort["medianLeadTimeHours"])
        self.assertEqual(cohort["phaseMedians"]["startToPrCreated"], 1.0)
        self.assertIsNone(cohort["phaseMedians"]["prCreatedToReady"])
        self.assertEqual(cohort["phaseMedians"]["readyToMerge"], 4.0)

        self.assertEqual(len(result["intervalStats"]), 2)
        bucket = result["intervalStats"][1]
        self.assertEqual(bucket["from"], "evt-1")
        self.assertIsNone(bucket["to"])
        self.assertEqual(bucket["n"], 1)
        self.assertIsNone(bucket["medianLeadTimeHours"])
        self.assertEqual(bucket["medianSizeLines"], 30)

    def test_t43_negative_interval_when_start_after_pr_created(self):
        # T43 (時系列逆転 1/3): firstStartAt (05:00) が prCreatedAt (01:00)
        # より後ろ (startToPrCreated が丸め前で負) でも leadTimeHours
        # (firstStartAt → readyAt) 自体は正になりうる。negativeInterval は
        # leadTimeHours の符号だけでなく各 phase の符号も見て判定することを
        # 確認する。
        issue, pr = make_started_case(
            issue_number=1,
            pr_number=1001,
            start_at="2026-07-10T05:00:00Z",
            pr_created_at="2026-07-10T01:00:00Z",
            ready_at="2026-07-10T06:00:00Z",
            merged_at="2026-07-10T07:00:00Z",
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )
        entry = result["mainSeries"][0]
        self.assertTrue(entry["negativeInterval"])
        self.assertEqual(entry["phaseHours"]["startToPrCreated"], -4.0)
        self.assertEqual(entry["leadTimeHours"], 1.0)

    def test_t43_negative_interval_when_pr_created_after_ready(self):
        # T43 (時系列逆転 2/3): prCreatedAt (05:00) が readyAt (02:00) より
        # 後ろ (prCreatedToReady が丸め前で負)。
        issue, pr = make_started_case(
            issue_number=1,
            pr_number=1002,
            start_at="2026-07-10T00:00:00Z",
            pr_created_at="2026-07-10T05:00:00Z",
            ready_at="2026-07-10T02:00:00Z",
            merged_at="2026-07-10T06:00:00Z",
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )
        entry = result["mainSeries"][0]
        self.assertTrue(entry["negativeInterval"])
        self.assertEqual(entry["phaseHours"]["prCreatedToReady"], -3.0)
        self.assertEqual(entry["leadTimeHours"], 2.0)

    def test_t43_ready_to_merge_negative_excluded_from_phase_median_only(self):
        # T43 (時系列逆転 3/3、正式な T43): readyAt (02:00) が mergedAt
        # (01:30) より後ろ (readyToMerge が丸め前で負) だが、leadTimeHours
        # (firstStartAt → readyAt) は正。medianLeadTimeHours (metric 単位)
        # にはこのレコードの leadTimeHours が含まれる一方、
        # phaseMedians.readyToMerge の中央値からはこの 1 件だけが除外され
        # null になる (該当週に他の readyToMerge 値が無いため)。
        issue, pr = make_started_case(
            issue_number=1,
            pr_number=1003,
            start_at="2026-07-10T00:00:00Z",
            pr_created_at="2026-07-10T01:00:00Z",
            ready_at="2026-07-10T02:00:00Z",
            merged_at="2026-07-10T01:30:00Z",
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )
        entry = result["mainSeries"][0]
        self.assertTrue(entry["negativeInterval"])
        self.assertEqual(entry["leadTimeHours"], 2.0)
        self.assertEqual(entry["phaseHours"]["readyToMerge"], -0.5)

        self.assertEqual(len(result["weeklyCohorts"]), 1)
        cohort = result["weeklyCohorts"][0]
        self.assertEqual(cohort["medianLeadTimeHours"], 2.0)
        self.assertIsNone(cohort["phaseMedians"]["readyToMerge"])
        self.assertEqual(cohort["phaseMedians"]["startToPrCreated"], 1.0)
        self.assertEqual(cohort["phaseMedians"]["prCreatedToReady"], 1.0)

    def test_t25_size_band_boundaries(self):
        cases = [
            (50, "S"),
            (51, "M"),
            (300, "M"),
            (301, "L"),
            (1000, "L"),
            (1001, "XL"),
        ]
        issues = []
        prs = []
        for idx, (lines, _band) in enumerate(cases, start=1):
            issue, pr = make_started_case(
                issue_number=idx,
                pr_number=400 + idx,
                start_at=f"2026-07-{10 + idx:02d}T00:00:00Z",
                merged_at=f"2026-07-{10 + idx:02d}T01:00:00Z",
                additions=lines,
                deletions=0,
            )
            issues.append(issue)
            prs.append(pr)
        as_of = datetime(2026, 7, 25, tzinfo=timezone.utc)
        result = compute_leadtime.compute(issues, prs, self.patterns, as_of, None, None)
        band_by_issue = {
            entry["issue"]: entry["sizeBand"] for entry in result["mainSeries"]
        }
        for idx, (_lines, band) in enumerate(cases, start=1):
            self.assertEqual(band_by_issue[idx], band, f"lines={cases[idx - 1][0]}")

    def test_t26_boundaries_split_main_series_into_three_intervals(self):
        starts = [
            "2026-07-01T00:00:00Z",
            "2026-07-10T00:00:00Z",
            "2026-07-20T00:00:00Z",
        ]
        issues = []
        prs = []
        for idx, start_at in enumerate(starts, start=1):
            issue, pr = make_started_case(
                issue_number=idx,
                pr_number=500 + idx,
                start_at=start_at,
                merged_at=start_at,
            )
            issues.append(issue)
            prs.append(pr)
        # T26 (拡張): censored な着手済み open issue を 1 件、2 番目の区間
        # ([evt-1, evt-2)) の週に追加し、その区間だけ censoredN == 1、他の
        # 区間は 0 になることを検証する。
        censored_issue = make_issue(
            repo="owner/name",
            number=99,
            state="OPEN",
            created_at="2026-07-12T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-12T00:00:00Z", "🔒 ai:claim branch=x")
            ],
        )
        issues.append(censored_issue)
        boundaries = [
            {
                "id": "evt-1",
                "label": "event 1",
                "at": datetime(2026, 7, 5, tzinfo=timezone.utc),
            },
            {
                "id": "evt-2",
                "label": "event 2",
                "at": datetime(2026, 7, 15, tzinfo=timezone.utc),
            },
        ]
        as_of = datetime(2026, 7, 25, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            issues, prs, self.patterns, as_of, None, boundaries
        )
        self.assertEqual(len(result["intervalStats"]), 3)
        self.assertEqual([bucket["n"] for bucket in result["intervalStats"]], [1, 1, 1])
        self.assertIsNone(result["intervalStats"][0]["from"])
        self.assertEqual(result["intervalStats"][0]["to"], "evt-1")
        self.assertEqual(result["intervalStats"][1]["from"], "evt-1")
        self.assertEqual(result["intervalStats"][1]["to"], "evt-2")
        self.assertEqual(result["intervalStats"][2]["from"], "evt-2")
        self.assertIsNone(result["intervalStats"][2]["to"])
        self.assertEqual(
            [bucket["censoredN"] for bucket in result["intervalStats"]], [0, 1, 0]
        )
        # label は boundaries[].label (evt-1="event 1", evt-2="event 2") から
        # 先頭区間 "〜 <label>"、内部区間 "<label> 〜 <label>"、末尾区間
        # "<label> 〜" の 3 形式で導出される。
        self.assertEqual(result["intervalStats"][0]["label"], "〜 event 1")
        self.assertEqual(result["intervalStats"][1]["label"], "event 1 〜 event 2")
        self.assertEqual(result["intervalStats"][2]["label"], "event 2 〜")

    def test_t47_boundaries_with_equal_at_are_tie_broken_by_id(self):
        # T47 (任意): compute レベルで、同一 at を持つ 2 つの boundary が
        # (at, id) の辞書順で安定に区間化されることを検証する (入力順は
        # 意図的に id 降順で渡す。並び替え後は "alpha" が "zeta" より先に
        # 来る)。
        boundaries = [
            {
                "id": "zeta",
                "label": "Z",
                "at": datetime(2026, 7, 5, tzinfo=timezone.utc),
            },
            {
                "id": "alpha",
                "label": "A",
                "at": datetime(2026, 7, 5, tzinfo=timezone.utc),
            },
        ]
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [], [], self.patterns, as_of, None, boundaries
        )
        self.assertEqual(len(result["intervalStats"]), 3)
        self.assertIsNone(result["intervalStats"][0]["from"])
        self.assertEqual(result["intervalStats"][0]["to"], "alpha")
        self.assertEqual(result["intervalStats"][1]["from"], "alpha")
        self.assertEqual(result["intervalStats"][1]["to"], "zeta")
        self.assertEqual(result["intervalStats"][2]["from"], "zeta")
        self.assertIsNone(result["intervalStats"][2]["to"])

    def test_t48_boundaries_echo_defaults_empty_and_normalizes_unordered_input(self):
        # T48 (契約改訂): (i) --boundaries-file 未指定 (boundaries=None) の
        # compute は出力の boundaries == [] を返す。(ii) 順不同の boundaries
        # 入力は (at, id) の辞書順に正規化されて echo され、各要素は
        # id / label / at (UTC "Z" 文字列) のみを持つ。
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)

        result_without = compute_leadtime.compute(
            [], [], self.patterns, as_of, None, None
        )
        self.assertEqual(result_without["boundaries"], [])

        # 入力順は意図的に (at 降順・id 降順) で渡し、正規化されることを検証する。
        boundaries = [
            {
                "id": "evt-2",
                "label": "event 2",
                "at": datetime(2026, 7, 15, tzinfo=timezone.utc),
            },
            {
                "id": "evt-1",
                "label": "event 1",
                "at": datetime(2026, 7, 5, tzinfo=timezone.utc),
            },
        ]
        result_with = compute_leadtime.compute(
            [], [], self.patterns, as_of, None, boundaries
        )
        self.assertEqual(
            result_with["boundaries"],
            [
                {"id": "evt-1", "label": "event 1", "at": "2026-07-05T00:00:00Z"},
                {"id": "evt-2", "label": "event 2", "at": "2026-07-15T00:00:00Z"},
            ],
        )
        for entry in result_with["boundaries"]:
            self.assertEqual(set(entry.keys()), {"id", "label", "at"})

    def test_t49_cross_repo_qualifying_pr_sets_prrepo_distinct_from_repo(self):
        # T49 (契約改訂): issue の repo ("a/x") と選択された qualifying
        # completion PR の repo ("b/y") が異なる (cross-repo closing
        # reference)。mainSeries レコードの repo は issue 側 ("a/x")、prRepo
        # は選択 PR 側 ("b/y") になる。同じ PR 番号を持つ "a/x" 側の無関係な
        # PR (この issue を close しない) も fixture に含め、(repo, number) の
        # 複合キーで正しく判別されることを合わせて検証する。
        issue_repo = "a/x"
        pr_repo = "b/y"
        issue_number = 700
        pr_number = 700  # issue 番号と同じ値をあえて使い、number 単独ではなく
        # (repo, number) で判別されることを確認する。
        issue = make_issue(
            repo=issue_repo,
            number=issue_number,
            state="OPEN",
            created_at="2026-07-01T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00Z", "🔒 ai:claim branch=x")
            ],
        )
        qualifying_pr = make_pr(
            repo=pr_repo,
            number=pr_number,
            state="MERGED",
            is_draft=False,
            created_at="2026-07-02T00:00:00Z",
            merged_at="2026-07-05T00:00:00Z",
            timeline_nodes=[ready_event("2026-07-03T00:00:00Z")],
            closing_issues=[
                {"number": issue_number, "repository": {"nameWithOwner": issue_repo}}
            ],
        )
        # "a/x" 側の同番号 PR。この issue を closingIssuesReferences で
        # 指さないため qualifying 候補にならない (無関係な decoy)。
        unrelated_pr = make_pr(
            repo=issue_repo,
            number=pr_number,
            state="MERGED",
            is_draft=False,
            created_at="2026-06-01T00:00:00Z",
            merged_at="2026-06-02T00:00:00Z",
            timeline_nodes=[ready_event("2026-06-01T12:00:00Z")],
            closing_issues=[],
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [qualifying_pr, unrelated_pr], self.patterns, as_of, None, None
        )

        self.assertEqual(len(result["mainSeries"]), 1)
        entry = result["mainSeries"][0]
        self.assertEqual(entry["repo"], issue_repo)
        self.assertEqual(entry["prRepo"], pr_repo)
        self.assertEqual(entry["pr"], pr_number)

    def test_t27_median_of_even_count_is_average_of_middle_two(self):
        issue1, pr1 = make_started_case(
            issue_number=1,
            pr_number=601,
            start_at="2026-07-10T00:00:00Z",
            ready_at="2026-07-10T02:00:00Z",
            merged_at="2026-07-10T02:00:00Z",
        )
        issue2, pr2 = make_started_case(
            issue_number=2,
            pr_number=602,
            start_at="2026-07-10T00:00:00Z",
            ready_at="2026-07-10T06:00:00Z",
            merged_at="2026-07-10T06:00:00Z",
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue1, issue2], [pr1, pr2], self.patterns, as_of, None, None
        )
        self.assertEqual(len(result["weeklyCohorts"]), 1)
        # statistics.median([2.0, 6.0]) == 4.0 (中央 2 値の算術平均)
        self.assertEqual(result["weeklyCohorts"][0]["medianLeadTimeHours"], 4.0)

    def test_t31_claim_detection_strict_and_loose_only_counts(self):
        issues = []
        prs = []
        for i in range(1, 43):
            day = (i % 28) + 1
            issue, pr = make_started_case(
                issue_number=i,
                pr_number=1000 + i,
                start_at=f"2026-01-{day:02d}T00:00:00Z",
                merged_at=f"2026-01-{day:02d}T01:00:00Z",
            )
            issues.append(issue)
            prs.append(pr)
        loose_issue = make_issue(
            repo="owner/name",
            number=99,
            state="OPEN",
            created_at="2026-01-01T00:00:00Z",
            timeline_nodes=[issue_comment("2026-01-05T00:00:00Z", "ai:claim soon")],
        )
        issues.append(loose_issue)
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(issues, prs, self.patterns, as_of, None, None)
        self.assertEqual(result["claimDetection"]["strictIssues"], 42)
        loose_only = result["claimDetection"]["looseOnlyIssues"]
        self.assertEqual(len(loose_only), 1)
        self.assertEqual(loose_only[0]["issue"], 99)

    def test_t32_via_draft_rate_three_of_four(self):
        issues = []
        prs = []
        for i in range(1, 5):
            via_draft = i <= 3
            timeline_nodes = (
                [ready_event(f"2026-07-10T0{i}:00:00Z")] if via_draft else []
            )
            issue = make_issue(
                repo="owner/name",
                number=i,
                state="CLOSED",
                state_reason="COMPLETED",
                created_at="2026-07-10T00:00:00Z",
                closed_at="2026-07-10T02:00:00Z",
                timeline_nodes=[
                    issue_comment("2026-07-10T00:00:00Z", "🔒 ai:claim branch=x"),
                    closed_event("2026-07-10T02:00:00Z", pr_closer(700 + i)),
                ],
            )
            pr = make_pr(
                repo="owner/name",
                number=700 + i,
                created_at="2026-07-10T00:30:00Z",
                merged_at="2026-07-10T02:00:00Z",
                timeline_nodes=timeline_nodes,
                closing_issues=[
                    {"number": i, "repository": {"nameWithOwner": "owner/name"}}
                ],
            )
            issues.append(issue)
            prs.append(pr)
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(issues, prs, self.patterns, as_of, None, None)
        self.assertEqual(len(result["weeklyCohorts"]), 1)
        self.assertEqual(result["weeklyCohorts"][0]["viaDraftRate"], 0.75)

    def test_t33_output_contains_schema_version_and_size_bands(self):
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute([], [], self.patterns, as_of, None, None)
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(
            result["sizeBands"],
            [
                {"band": "S", "min": 0, "max": 50},
                {"band": "M", "min": 51, "max": 300},
                {"band": "L", "min": 301, "max": 1000},
                {"band": "XL", "min": 1001, "max": None},
            ],
        )

    def test_t34_two_repos_are_reported_separately(self):
        issue_a, pr_a = make_started_case(
            issue_number=1,
            pr_number=801,
            repo="owner/repo-a",
            start_at="2026-07-10T00:00:00Z",
            merged_at="2026-07-11T00:00:00Z",
        )
        issue_b, pr_b = make_started_case(
            issue_number=1,
            pr_number=802,
            repo="owner/repo-b",
            start_at="2026-07-10T00:00:00Z",
            merged_at="2026-07-11T00:00:00Z",
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue_a, issue_b], [pr_a, pr_b], self.patterns, as_of, None, None
        )
        self.assertEqual(
            {row["repo"] for row in result["repos"]}, {"owner/repo-a", "owner/repo-b"}
        )
        self.assertEqual(
            {row["repo"] for row in result["markerCoverage"]},
            {"owner/repo-a", "owner/repo-b"},
        )

    def test_t35_pr_timeline_overflow_excludes_pr_and_linked_issue(self):
        # T35: PR の timelineItems.totalCount (150) > len(nodes) (2) で timeline
        # 取得が不完全な merged PR。この PR を closer とする最終 ClosedEvent を
        # 持つ、着手マーカー付き closed issue も ready 時刻を信頼できないため、
        # mainSeries / auxiliarySeries いずれのカテゴリにも再分類されず
        # exclusions.prTimelineOverflow にのみ列挙される。
        repo = "owner/name"
        issue_number = 70
        pr_number = 900
        issue = make_issue(
            repo=repo,
            number=issue_number,
            state="CLOSED",
            state_reason="COMPLETED",
            created_at="2026-07-01T00:00:00Z",
            closed_at="2026-07-05T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00Z", "🔒 ai:claim branch=x"),
                closed_event(
                    "2026-07-05T00:00:00Z",
                    pr_closer(pr_number, repo=repo, merged=True),
                ),
            ],
        )
        pr = make_pr(
            repo=repo,
            number=pr_number,
            created_at="2026-07-02T00:00:00Z",
            merged_at="2026-07-05T00:00:00Z",
            timeline_nodes=[
                ready_event("2026-07-03T00:00:00Z"),
                draft_event("2026-07-04T00:00:00Z"),
            ],
            total_count=150,
            closing_issues=[
                {"number": issue_number, "repository": {"nameWithOwner": repo}}
            ],
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )

        self.assertEqual(result["prSeries"], [])
        self.assertEqual(result["mainSeries"], [])
        aux = result["auxiliarySeries"]
        for category in (
            "manualClose",
            "commitClose",
            "notPlanned",
            "unmergedPr",
            "externalMergedClose",
        ):
            issue_numbers = {entry["issue"] for entry in aux[category]}
            self.assertNotIn(issue_number, issue_numbers)

        overflow = result["exclusions"]["prTimelineOverflow"]
        self.assertEqual(len(overflow), 1)
        entry = overflow[0]
        self.assertEqual(
            set(entry.keys()), {"repo", "pr", "totalCount", "fetched", "linkedIssues"}
        )
        self.assertEqual(entry["repo"], repo)
        self.assertEqual(entry["pr"], pr_number)
        self.assertEqual(entry["totalCount"], 150)
        self.assertEqual(entry["fetched"], 2)
        self.assertEqual(entry["linkedIssues"], [{"repo": repo, "issue": issue_number}])

    def test_t44_linked_issues_same_number_different_repos_kept_separate(self):
        # T44: 1 件の overflow PR の closingIssuesReferences が、同じ issue
        # 番号 (5) を持つ別リポジトリの 2 issue (owner/repo-a, owner/repo-b)
        # を指す場合、linkedIssues は (repo, issue) の複合キーで両方を別
        # レコードとして列挙する (issue 番号だけで重複排除すると片方が
        # 失われる回帰の防止)。着手マーカー付きだが qualifying 候補が
        # overflow PR しか無い issue は OPEN のまま censored に入る。
        repo = "owner/name"
        pr_number = 950
        issue_a = make_issue(
            repo="owner/repo-a",
            number=5,
            state="OPEN",
            created_at="2026-07-01T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00Z", "🔒 ai:claim branch=x")
            ],
        )
        issue_b = make_issue(
            repo="owner/repo-b",
            number=5,
            state="OPEN",
            created_at="2026-07-01T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00Z", "🔒 ai:claim branch=x")
            ],
        )
        pr = make_pr(
            repo=repo,
            number=pr_number,
            created_at="2026-07-02T00:00:00Z",
            merged_at="2026-07-05T00:00:00Z",
            timeline_nodes=[ready_event("2026-07-03T00:00:00Z")],
            total_count=150,
            closing_issues=[
                {"number": 5, "repository": {"nameWithOwner": "owner/repo-a"}},
                {"number": 5, "repository": {"nameWithOwner": "owner/repo-b"}},
            ],
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue_a, issue_b], [pr], self.patterns, as_of, None, None
        )

        self.assertEqual(result["mainSeries"], [])
        censored_keys = {
            (entry["repo"], entry["issue"]) for entry in result["censored"]
        }
        self.assertIn(("owner/repo-a", 5), censored_keys)
        self.assertIn(("owner/repo-b", 5), censored_keys)

        overflow = result["exclusions"]["prTimelineOverflow"]
        self.assertEqual(len(overflow), 1)
        self.assertEqual(
            overflow[0]["linkedIssues"],
            [
                {"repo": "owner/repo-a", "issue": 5},
                {"repo": "owner/repo-b", "issue": 5},
            ],
        )

    def test_t36_open_ready_pr_links_issue_into_main_series(self):
        # T36 (改訂 1): OPEN + isDraft=False の PR (ReadyForReviewEvent 1 件、
        # closingIssuesReferences に着手済み OPEN issue) は ready 到達済みと
        # みなされ、リンクされた issue が mainSeries に
        # completionBasis="ready_unmerged" として入る (censored には入らない)。
        # PR 自体も prSeries に state="OPEN"、mergedAt=None で入る。
        repo = "owner/name"
        issue_number = 400
        pr_number = 900
        issue = make_issue(
            repo=repo,
            number=issue_number,
            state="OPEN",
            created_at="2026-07-01T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00Z", "🔒 ai:claim branch=x")
            ],
        )
        pr = make_pr(
            repo=repo,
            number=pr_number,
            state="OPEN",
            is_draft=False,
            created_at="2026-07-02T00:00:00Z",
            merged_at=None,
            timeline_nodes=[ready_event("2026-07-03T00:00:00Z")],
            closing_issues=[
                {"number": issue_number, "repository": {"nameWithOwner": repo}}
            ],
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )

        self.assertEqual(len(result["mainSeries"]), 1)
        entry = result["mainSeries"][0]
        self.assertEqual(entry["issue"], issue_number)
        self.assertEqual(entry["completionBasis"], "ready_unmerged")
        self.assertIsNone(entry["mergedAt"])
        self.assertIsNone(entry["phaseHours"]["readyToMerge"])
        self.assertEqual(result["censored"], [])
        # prRepo (契約改訂): 同一 repo ケースでは repo == prRepo になる
        # (cross-repo ケースは T49 で別途検証する)。
        self.assertEqual(entry["prRepo"], repo)

        self.assertEqual(len(result["prSeries"]), 1)
        pr_entry = result["prSeries"][0]
        self.assertEqual(pr_entry["pr"], pr_number)
        self.assertEqual(pr_entry["state"], "OPEN")
        self.assertIsNone(pr_entry["mergedAt"])

    def test_t37_open_draft_pr_does_not_complete_issue(self):
        # T37 (改訂 1): OPEN + isDraft=True の PR (過去に ReadyForReviewEvent が
        # あってもよい) は prSeries に入らず、リンクされた着手済み OPEN issue は
        # mainSeries に入らず censored に残る。
        repo = "owner/name"
        issue_number = 401
        pr_number = 901
        issue = make_issue(
            repo=repo,
            number=issue_number,
            state="OPEN",
            created_at="2026-07-01T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00Z", "🔒 ai:claim branch=x")
            ],
        )
        pr = make_pr(
            repo=repo,
            number=pr_number,
            state="OPEN",
            is_draft=True,
            created_at="2026-07-02T00:00:00Z",
            merged_at=None,
            timeline_nodes=[
                ready_event("2026-07-03T00:00:00Z"),
                draft_event("2026-07-04T00:00:00Z"),
            ],
            closing_issues=[
                {"number": issue_number, "repository": {"nameWithOwner": repo}}
            ],
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )

        self.assertEqual(result["prSeries"], [])
        self.assertEqual(result["mainSeries"], [])
        censored_issues = {entry["issue"] for entry in result["censored"]}
        self.assertIn(issue_number, censored_issues)

    def test_t38_censored_only_week_appears_in_weekly_cohorts(self):
        # T38 (改訂 2): mainSeries が 0 件で censored しか無い週も
        # weeklyCohorts に行が出て、n=0・medianLeadTimeHours=None・
        # censoredN>=1 になる。
        issue = make_issue(
            repo="owner/name",
            number=402,
            state="OPEN",
            created_at="2026-07-06T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-06T00:00:00Z", "🔒 ai:claim branch=x")
            ],
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute([issue], [], self.patterns, as_of, None, None)

        start_week = date(2026, 7, 6).strftime("%G-W%V")
        cohorts_by_week = {cohort["week"]: cohort for cohort in result["weeklyCohorts"]}
        self.assertIn(start_week, cohorts_by_week)
        cohort = cohorts_by_week[start_week]
        self.assertEqual(cohort["n"], 0)
        self.assertIsNone(cohort["medianLeadTimeHours"])
        self.assertGreaterEqual(cohort["censoredN"], 1)

    def test_t40_open_issue_with_merged_pr_keeps_merge_data(self):
        # T40: OPEN issue に MERGED PR が closingIssuesReferences でリンク
        # されている (issue 自体は何らかの理由でまだ OPEN のまま)。選択された
        # PR (qualifying completion PR) の実状態が MERGED であるため、
        # mainSeries (b) 経路でも completionBasis="merged" とし、mergedAt /
        # phaseHours.readyToMerge を実データで埋める。
        repo = "owner/name"
        issue_number = 410
        pr_number = 910
        issue = make_issue(
            repo=repo,
            number=issue_number,
            state="OPEN",
            created_at="2026-07-01T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00Z", "🔒 ai:claim branch=x")
            ],
        )
        pr = make_pr(
            repo=repo,
            number=pr_number,
            state="MERGED",
            is_draft=False,
            created_at="2026-07-02T00:00:00Z",
            merged_at="2026-07-05T00:00:00Z",
            timeline_nodes=[ready_event("2026-07-03T00:00:00Z")],
            closing_issues=[
                {"number": issue_number, "repository": {"nameWithOwner": repo}}
            ],
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )

        self.assertEqual(len(result["mainSeries"]), 1)
        entry = result["mainSeries"][0]
        self.assertEqual(entry["issue"], issue_number)
        self.assertEqual(entry["completionBasis"], "merged")
        # prRepo (契約改訂): 同一 repo ケースでは repo == prRepo になる
        # (cross-repo ケースは T49 で別途検証する)。
        self.assertEqual(entry["prRepo"], repo)
        self.assertIsNotNone(entry["mergedAt"])
        merged_at = datetime.fromisoformat(
            str(entry["mergedAt"]).replace("Z", "+00:00")
        )
        self.assertEqual(merged_at, datetime(2026, 7, 5, tzinfo=timezone.utc))
        self.assertEqual(entry["phaseHours"]["readyToMerge"], 48.0)
        self.assertEqual(result["censored"], [])

    def test_t42_cross_repo_merged_closer_is_external(self):
        # T42: 最終 ClosedEvent の closer が merged=true の PullRequest だが、
        # その PR は収集範囲外の別リポジトリの PR であり prs_by_key (prs.jsonl
        # から構築) には存在しない。resolve_close_linkage は closer["merged"]
        # を一次情報として "merged_pr_external" に分類する (prs_by_key 不在
        # だけで "unmerged_pr" に誤分類しない)。compute の出力では当該 issue
        # が auxiliarySeries["externalMergedClose"] にのみ入り、mainSeries /
        # censored / auxiliarySeries["unmergedPr"] のいずれにも入らない。
        repo = "owner/name"
        issue_number = 500
        external_pr_repo = "owner/other-repo"
        external_pr_number = 999
        issue = make_issue(
            repo=repo,
            number=issue_number,
            state="CLOSED",
            state_reason="COMPLETED",
            created_at="2026-07-01T00:00:00Z",
            closed_at="2026-07-05T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00Z", "🔒 ai:claim branch=x"),
                closed_event(
                    "2026-07-05T00:00:00Z",
                    pr_closer(external_pr_number, repo=external_pr_repo, merged=True),
                ),
            ],
        )

        # resolve_close_linkage 単体でも、prs_by_key に該当 PR が無い状態で
        # "merged_pr_external" を検証する。
        linkage = compute_leadtime.resolve_close_linkage(issue, {})
        self.assertEqual(linkage.category, "merged_pr_external")

        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute([issue], [], self.patterns, as_of, None, None)

        self.assertEqual(result["mainSeries"], [])
        self.assertEqual(result["censored"], [])
        aux = result["auxiliarySeries"]
        unmerged_issue_numbers = {entry["issue"] for entry in aux["unmergedPr"]}
        self.assertNotIn(issue_number, unmerged_issue_numbers)
        external_issue_numbers = {
            entry["issue"] for entry in aux["externalMergedClose"]
        }
        self.assertIn(issue_number, external_issue_numbers)

    def test_t45_manual_close_with_qualifying_ready_pr_joins_main_series(self):
        # T45: issue の最終 ClosedEvent の closer が無い (closer=None、単体
        # なら resolve_close_linkage は "manual" に分類する) が、別の PR の
        # closingIssuesReferences 経由でこの issue を指す qualifying
        # completion PR (MERGED) が存在する。qualifying 候補集合パイプライン
        # のステップ 3 (closingIssuesReferences 逆引き) がこの PR を候補として
        # 見つけるため、issue は mainSeries に編入され、
        # auxiliarySeries.manualClose には入らない (T42 の「候補なし」ケースと
        # 対照的な回帰)。
        repo = "owner/name"
        issue_number = 600
        pr_number = 1100
        issue = make_issue(
            repo=repo,
            number=issue_number,
            state="CLOSED",
            state_reason="COMPLETED",
            created_at="2026-07-01T00:00:00Z",
            closed_at="2026-07-05T00:00:00Z",
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00Z", "🔒 ai:claim branch=x"),
                closed_event("2026-07-05T00:00:00Z", None),
            ],
        )

        # resolve_close_linkage 単体では、この issue は closer が無いため
        # "manual" に分類されることを確認しておく (mainSeries 編入は
        # closingIssuesReferences 側の候補が優先するためであり、
        # resolve_close_linkage 自体の判定を変えるわけではない)。
        linkage = compute_leadtime.resolve_close_linkage(issue, {})
        self.assertEqual(linkage.category, "manual")

        pr = make_pr(
            repo=repo,
            number=pr_number,
            state="MERGED",
            created_at="2026-07-02T00:00:00Z",
            merged_at="2026-07-04T00:00:00Z",
            timeline_nodes=[ready_event("2026-07-03T00:00:00Z")],
            closing_issues=[
                {"number": issue_number, "repository": {"nameWithOwner": repo}}
            ],
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )

        self.assertEqual(len(result["mainSeries"]), 1)
        entry = result["mainSeries"][0]
        self.assertEqual(entry["issue"], issue_number)
        self.assertEqual(entry["completionBasis"], "merged")

        manual_close_issue_numbers = {
            e["issue"] for e in result["auxiliarySeries"]["manualClose"]
        }
        self.assertNotIn(issue_number, manual_close_issue_numbers)


# ---------------------------------------------------------------------------
# T28〜T30: main (tempfile 経由。subprocess は使わない)
# ---------------------------------------------------------------------------


class MainTests(unittest.TestCase):
    def _run_main(
        self, tmp_path: Path, *, issues_content: str, patterns_dict: dict
    ) -> int:
        issues_path = tmp_path / "issues.jsonl"
        prs_path = tmp_path / "prs.jsonl"
        patterns_path = tmp_path / "patterns.json"
        issues_path.write_text(issues_content, encoding="utf-8")
        prs_path.write_text("", encoding="utf-8")
        write_claim_patterns_file(patterns_path, patterns_dict)
        return compute_leadtime.main(
            [
                "--issues",
                str(issues_path),
                "--prs",
                str(prs_path),
                "--claim-patterns-file",
                str(patterns_path),
                "--as-of",
                "2026-07-17T04:00:00Z",
            ]
        )

    def test_t28_empty_jsonl_input_exits_zero_with_empty_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            exit_code = self._run_main(
                Path(tmp), issues_content="", patterns_dict=DEFAULT_PATTERNS_DICT
            )
            self.assertEqual(exit_code, 0)

    def test_t29_malformed_jsonl_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            exit_code = self._run_main(
                Path(tmp),
                issues_content="{not valid json\n",
                patterns_dict=DEFAULT_PATTERNS_DICT,
            )
            self.assertEqual(exit_code, 2)

    def test_t30_claim_patterns_missing_key_exits_three(self):
        broken_patterns = {
            "inProgressLabel": "ai:in-progress",
            "strict": [{"id": "lock-claim", "regex": "^🔒 ai:claim branch="}],
            # "loose" キーが欠落している
        }
        with tempfile.TemporaryDirectory() as tmp:
            exit_code = self._run_main(
                Path(tmp), issues_content="", patterns_dict=broken_patterns
            )
            self.assertEqual(exit_code, 3)

    def test_t41_closing_refs_overflow_exits_two(self):
        # T41: prs.jsonl の 1 行が closingIssuesReferences.totalCount (25) >
        # len(nodes) (1) を持つ (ページング未完了)。この行が存在する限り
        # main は入力エラーとして fail-closed に exit code 2 を返す。
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            issues_path = tmp_path / "issues.jsonl"
            prs_path = tmp_path / "prs.jsonl"
            patterns_path = tmp_path / "patterns.json"
            issues_path.write_text("", encoding="utf-8")
            pr = make_pr(
                number=920,
                closing_issues=[
                    {"number": 1, "repository": {"nameWithOwner": "owner/name"}}
                ],
                closing_issues_total_count=25,
            )
            prs_path.write_text(json.dumps(pr) + "\n", encoding="utf-8")
            write_claim_patterns_file(patterns_path, DEFAULT_PATTERNS_DICT)
            exit_code = compute_leadtime.main(
                [
                    "--issues",
                    str(issues_path),
                    "--prs",
                    str(prs_path),
                    "--claim-patterns-file",
                    str(patterns_path),
                    "--as-of",
                    "2026-07-17T04:00:00Z",
                ]
            )
            self.assertEqual(exit_code, 2)

    def test_t46_invalid_boundaries_file_exits_two(self):
        # T46: --boundaries-file の内容が契約 (id/label の必須・at の
        # ISO8601 UTC・id の一意性等) に違反する代表的な 2 ケース (id の
        # 重複、naive datetime の at) のいずれでも main は exit code 2 を
        # 返す (fail-closed)。
        cases = {
            "duplicate_id": [
                {"id": "evt-1", "label": "event 1", "at": "2026-07-01T00:00:00Z"},
                {
                    "id": "evt-1",
                    "label": "event 1 (dup)",
                    "at": "2026-07-02T00:00:00Z",
                },
            ],
            "naive_at": [
                {"id": "evt-1", "label": "event 1", "at": "2026-07-01T00:00:00"},
            ],
        }
        for case_name, boundaries_payload in cases.items():
            with self.subTest(case=case_name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    issues_path = tmp_path / "issues.jsonl"
                    prs_path = tmp_path / "prs.jsonl"
                    patterns_path = tmp_path / "patterns.json"
                    boundaries_path = tmp_path / "boundaries.json"
                    issues_path.write_text("", encoding="utf-8")
                    prs_path.write_text("", encoding="utf-8")
                    write_claim_patterns_file(patterns_path, DEFAULT_PATTERNS_DICT)
                    boundaries_path.write_text(
                        json.dumps(boundaries_payload), encoding="utf-8"
                    )
                    exit_code = compute_leadtime.main(
                        [
                            "--issues",
                            str(issues_path),
                            "--prs",
                            str(prs_path),
                            "--claim-patterns-file",
                            str(patterns_path),
                            "--as-of",
                            "2026-07-17T04:00:00Z",
                            "--boundaries-file",
                            str(boundaries_path),
                        ]
                    )
                    self.assertEqual(exit_code, 2)

    def test_t50_naive_input_timestamp_exits_two_without_stdout_json(self):
        # T50 (契約改訂): --issues / --prs の JSONL 行のうち実際に処理される
        # 日時フィールド (IssueComment.createdAt または PR createdAt) が tz
        # 情報の無い naive timestamp のとき、main は exit code 2 を返し、
        # stdout には結果 JSON を一切出力しない (fail-closed。診断メッセージは
        # stderr のみに出る想定であり、本テストは stdout の空を検証する)。
        naive_comment_issue = make_issue(
            number=1,
            timeline_nodes=[
                issue_comment("2026-07-01T00:00:00", "🔒 ai:claim branch=x")
            ],
        )
        naive_created_at_pr = make_pr(
            number=930,
            state="OPEN",
            is_draft=False,
            created_at="2026-07-01T00:00:00",
        )

        cases = {
            "issue_comment_created_at": ([naive_comment_issue], []),
            "pr_created_at": ([], [naive_created_at_pr]),
        }
        for case_name, (issues, prs) in cases.items():
            with self.subTest(case=case_name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    issues_path = tmp_path / "issues.jsonl"
                    prs_path = tmp_path / "prs.jsonl"
                    patterns_path = tmp_path / "patterns.json"
                    issues_path.write_text(
                        "".join(json.dumps(issue) + "\n" for issue in issues),
                        encoding="utf-8",
                    )
                    prs_path.write_text(
                        "".join(json.dumps(pr) + "\n" for pr in prs),
                        encoding="utf-8",
                    )
                    write_claim_patterns_file(patterns_path, DEFAULT_PATTERNS_DICT)
                    captured_stdout = io.StringIO()
                    with contextlib.redirect_stdout(captured_stdout):
                        exit_code = compute_leadtime.main(
                            [
                                "--issues",
                                str(issues_path),
                                "--prs",
                                str(prs_path),
                                "--claim-patterns-file",
                                str(patterns_path),
                                "--as-of",
                                "2026-07-17T04:00:00Z",
                            ]
                        )
                    self.assertEqual(exit_code, 2)
                    self.assertEqual(captured_stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

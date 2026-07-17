"""repo-analytics `leadtime` skill の compute_leadtime.py に対する契約テスト (issue #288)。

Phase A (spec-first) の位置づけ:

- `compute_leadtime.py` は公開契約 (定数・dataclass・例外・CLI 引数定義・関数
  シグネチャ・docstring) のみが実体を持ち、`load_claim_patterns` /
  `resolve_start` / `resolve_ready` / `resolve_close_linkage` / `compute` /
  `main` の処理本体は `NotImplementedError` を送出する (Phase B で実装する)。
- このファイルは issue #288 Phase A 契約ドキュメント セクション 8 の
  test matrix T1〜T34 を全件実装する。各テストメソッド名・コメントに
  `T<n>` を付与し対応関係を明示する。
- 契約の「存在」を検証するテスト (`ContractExistenceTests`) は Phase A 時点で
  pass する。挙動を検証するテスト (T1〜T34) は本物の期待値アサーションを
  書いたうえで実装本体を直接呼び出す (`assertRaises(NotImplementedError)` で
  くるまない)。そのため Phase A では `NotImplementedError` が捕捉されずに
  伝播し、unittest 上は ERROR として報告される。これは意図した red 状態であり、
  Phase B で実装が入るまで修正しない。
"""

from __future__ import annotations

import importlib.util
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
    created_at="2026-01-01T00:00:00Z",
    merged_at=None,
    additions=0,
    deletions=0,
    timeline_nodes=None,
    closing_issues=None,
    total_count=None,
):
    nodes = timeline_nodes or []
    return {
        "repo": repo,
        "number": number,
        "createdAt": created_at,
        "mergedAt": merged_at,
        "additions": additions,
        "deletions": deletions,
        "timelineItems": {
            "totalCount": total_count if total_count is not None else len(nodes),
            "nodes": nodes,
        },
        "closingIssuesReferences": {"nodes": closing_issues or []},
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

    def test_t10_ready_draft_ready_sets_redraft_count(self):
        pr = make_pr(
            number=10,
            created_at="2026-01-01T00:00:00Z",
            timeline_nodes=[
                ready_event("2026-01-02T00:00:00Z"),
                draft_event("2026-01-03T00:00:00Z"),
                ready_event("2026-01-04T00:00:00Z"),
            ],
        )
        result = compute_leadtime.resolve_ready(pr)
        self.assertEqual(result.ready_at, datetime(2026, 1, 4, tzinfo=timezone.utc))
        self.assertEqual(result.redraft_count, 1)
        self.assertTrue(result.via_draft)

    def test_t11_no_events_falls_back_to_created_at(self):
        pr = make_pr(number=11, created_at="2026-01-01T00:00:00Z", timeline_nodes=[])
        result = compute_leadtime.resolve_ready(pr)
        self.assertEqual(result.ready_at, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertFalse(result.via_draft)
        self.assertEqual(result.redraft_count, 0)


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
        issue, pr = make_started_case(
            issue_number=5,
            pr_number=60,
            start_at="2026-07-01T00:00:00Z",
            merged_at="2026-07-02T00:00:00Z",
        )
        fetched = len(issue["timelineItems"]["nodes"])
        issue["timelineItems"]["totalCount"] = fetched + 500
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )
        self.assertEqual(result["mainSeries"], [])
        overflow = result["exclusions"]["timelineOverflow"]
        self.assertEqual(len(overflow), 1)
        self.assertEqual(overflow[0]["issue"], 5)
        self.assertEqual(overflow[0]["totalCount"], fetched + 500)
        self.assertEqual(overflow[0]["fetched"], fetched)

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

    def test_t24_negative_interval_kept_in_main_series_but_excluded_from_median(self):
        issue, pr = make_started_case(
            issue_number=1,
            pr_number=301,
            start_at="2026-07-10T12:00:00Z",
            pr_created_at="2026-07-10T13:00:00Z",
            ready_at="2026-07-10T10:00:00Z",  # firstStartAt より前 (異常データ)
            merged_at="2026-07-10T14:00:00Z",
        )
        as_of = datetime(2026, 7, 17, tzinfo=timezone.utc)
        result = compute_leadtime.compute(
            [issue], [pr], self.patterns, as_of, None, None
        )
        self.assertEqual(len(result["mainSeries"]), 1)
        self.assertTrue(result["mainSeries"][0]["negativeInterval"])
        self.assertEqual(result["dataQuality"]["negativeIntervalCount"], 1)
        self.assertEqual(len(result["weeklyCohorts"]), 1)
        self.assertIsNone(result["weeklyCohorts"][0]["medianLeadTimeHours"])

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


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Warn when the Claude Code version pinned in CI falls behind the latest release.

Usage: check_claude_code_pin_age.py <pinned_version>

The script asks the npm registry for the release dates of @anthropic-ai/claude-code
(`npm view <pkg> time --json`) and for its latest version (`npm view <pkg> version`).
When the latest release was published MAX_LAG_DAYS or more days after the pinned
release, it prints a GitHub Actions warning annotation (`::warning::...`).
Otherwise it prints a plain status line. When npm cannot be run, its output
cannot be parsed, or a release date is missing, it prints a skip message.

The script always exits 0: it only reports, and never fails the CI job.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime

PACKAGE_NAME = "@anthropic-ai/claude-code"
MAX_LAG_DAYS = 30
NPM_TIMEOUT_SECONDS = 60
WARNING_PREFIX = "::warning::"

NpmRunner = Callable[[list[str]], str]


def run_npm_subprocess(args: list[str]) -> str:
    """Run `npm <args>` and return its stdout, raising on failure."""

    completed = subprocess.run(
        ["npm", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=NPM_TIMEOUT_SECONDS,
    )
    return completed.stdout


def parse_release_time(value: str) -> datetime:
    """Parse an npm ISO 8601 timestamp such as `2026-07-14T10:00:00.000Z`."""

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def pin_lag_days(release_times: dict[str, str], pinned: str, latest: str) -> int | None:
    """Return whole days from the pinned release to the latest release.

    Returns None when either version has no (parsable) release time.
    """

    pinned_time = release_times.get(pinned)
    latest_time = release_times.get(latest)
    if not isinstance(pinned_time, str) or not isinstance(latest_time, str):
        return None
    try:
        lag = parse_release_time(latest_time) - parse_release_time(pinned_time)
    except (TypeError, ValueError):
        return None
    return lag.days


def skip_message(reason: str) -> str:
    return f"Skipped Claude Code pin age check: {reason}"


def build_message(pinned: str, latest: str, lag_days: int | None) -> str:
    """Build the single output line for the given lag."""

    if lag_days is None:
        return skip_message(
            f"release time of pinned {pinned} or latest {latest} is unknown."
        )
    if lag_days >= MAX_LAG_DAYS:
        return (
            f"{WARNING_PREFIX}CI pins Claude Code {pinned}, released {lag_days} days "
            f"before the latest {latest} (threshold: {MAX_LAG_DAYS} days). "
            "Update CLAUDE_CODE_VERSION in .github/workflows/ci.yml."
        )
    return (
        f"OK: CI pins Claude Code {pinned}, released {lag_days} days before "
        f"the latest {latest} (threshold: {MAX_LAG_DAYS} days)."
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pinned_version", help="Claude Code version pinned in CI")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, run_npm: NpmRunner = run_npm_subprocess) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    pinned = args.pinned_version
    try:
        release_times_output = run_npm(["view", PACKAGE_NAME, "time", "--json"])
        latest = run_npm(["view", PACKAGE_NAME, "version"]).strip()
    except (subprocess.SubprocessError, OSError) as exc:
        print(skip_message(f"npm view {PACKAGE_NAME} failed ({exc})."))
        return 0
    try:
        release_times = json.loads(release_times_output)
    except json.JSONDecodeError as exc:
        print(skip_message(f"cannot parse npm release times as JSON ({exc})."))
        return 0
    if not isinstance(release_times, dict):
        print(skip_message("npm release times are not a JSON object."))
        return 0
    print(build_message(pinned, latest, pin_lag_days(release_times, pinned, latest)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

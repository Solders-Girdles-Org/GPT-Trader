#!/usr/bin/env python3
"""Post the trading trigger's verdict on a PR as the required ``trading-trigger/base`` status.

``agent-pr-ready`` applies the trading trigger (reserved item e) in the PR's own
checkout, so code in the PR can bend its report. This runner cannot be bent
that way: ``.github/workflows/trading-trigger.yml`` fetches the base branch,
never the PR's head, and runs this file with ``python3 -I -S``. It reads the
PR's file list and body from the GitHub API, loads the base branch's
``src/gpt_trader/agents/trading_trigger.py`` by path and posts a commit status.
No file from the PR runs. Standard library only.

For a pull request event it posts on the PR's head. For a merge-queue entry it
judges the queued PR again from the base branch and posts on the merge group's
commit, which the queue also requires.

Locally, print the verdict without posting it::

    python3 scripts/ci/trading_trigger_status.py --pr 1417 --print-only
"""

from __future__ import annotations

import argparse
import importlib.util  # naming: allow
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TRIGGER_PATH = ROOT / "src" / "gpt_trader" / "agents" / "trading_trigger.py"
CONTEXT = "trading-trigger/base"
DEFAULT_REPOSITORY = "Solders-Girdles-Org/GPT-Trader"
# GitHub caps a status description at 140 characters.
_DESCRIPTION_LIMIT = 140
# Merge-queue branches are gh-readonly-queue/<base branch>/pr-<number>-<base sha>.
_QUEUE_REF = re.compile(r"(?:refs/heads/)?gh-readonly-queue/.+/pr-(\d+)-[0-9a-f]{40}")


@dataclass(frozen=True)
class Verdict:
    state: str  # success / failure / error
    message: str
    paths: tuple[str, ...] = ()


class Skip(Exception):
    """The event is obsolete; a newer event judges the current head."""


def load_trigger(path: Path = TRIGGER_PATH) -> ModuleType:
    """Load the trigger from its file, without importing the gpt_trader package."""
    spec = importlib.util.spec_from_file_location("_base_trading_trigger", path)  # naming: allow
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)  # naming: allow
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gh {args[0]} failed")
    return result.stdout


def fetch_pr(repo: str, number: int) -> dict[str, Any]:
    payload = json.loads(_gh(["api", f"repos/{repo}/pulls/{number}"]))
    if not isinstance(payload, dict):
        raise RuntimeError("unexpected pull request payload")
    return payload


def fetch_files(repo: str, number: int) -> tuple[list[str], int]:
    """Changed paths (a rename lists both) and how many files GitHub listed."""
    pages = json.loads(
        _gh(["api", "--paginate", "--slurp", f"repos/{repo}/pulls/{number}/files?per_page=100"])
    )
    paths: list[str] = []
    listed = 0
    for page in pages:
        for item in page:
            listed += 1
            paths.append(item["filename"])
            if item.get("previous_filename"):
                paths.append(item["previous_filename"])
    return paths, listed


def queued_pr_number(ref: str) -> int:
    match = _QUEUE_REF.fullmatch(ref or "")
    if not match:
        raise ValueError(f"not a merge-queue branch: {ref!r}")
    return int(match.group(1))


def judge(
    trigger: ModuleType,
    pr: dict[str, Any],
    paths: list[str],
    listed: int,
    repo: str,
) -> Verdict:
    """The base trigger's verdict on this PR, as a commit status state."""
    expected = int(pr.get("changed_files") or 0)
    if listed < expected:
        # The files API stops at 3,000; an unlisted file could be a trading path.
        return Verdict(
            "failure",
            f"GitHub listed {listed} of {expected} changed files; "
            "the trigger must see every file. Split the PR.",
        )
    result = trigger.assess(
        paths,
        str(pr.get("body") or ""),
        str(pr["head"]["sha"]),
        repo=repo,
        number=int(pr["number"]),
    )
    state = "failure" if result.severity == "blocker" else "success"
    return Verdict(state, str(result.message), tuple(result.paths))


def evaluate(
    trigger: ModuleType,
    repo: str,
    number: int,
    *,
    expected_head: str | None = None,
    queued: bool = False,
) -> tuple[Verdict, str]:
    """Judge the PR as it stands now; returns the verdict and the head it covers."""
    pr = fetch_pr(repo, number)
    if not queued and pr.get("state") != "open":
        raise Skip(f"PR #{number} is {pr.get('state')}")
    head = str(pr["head"]["sha"])
    if expected_head and head != expected_head:
        raise Skip(f"PR #{number} head moved from {expected_head[:7]} to {head[:7]}")
    paths, listed = fetch_files(repo, number)
    # Read the PR again: the files must belong to the head and body being judged.
    again = fetch_pr(repo, number)
    if str(again["head"]["sha"]) != head:
        if not queued:
            raise Skip(f"PR #{number} head moved while reading its files")
        return Verdict("failure", f"PR #{number} head moved while queued."), head
    return judge(trigger, again, paths, listed, repo), head


def description(text: str) -> str:
    text = " ".join(text.split()).replace("`", "")
    if len(text) <= _DESCRIPTION_LIMIT:
        return text
    return text[: _DESCRIPTION_LIMIT - 1] + "…"


def post_status(repo: str, sha: str, verdict: Verdict, target_url: str | None) -> None:
    args = [
        "api",
        "--method",
        "POST",
        f"repos/{repo}/statuses/{sha}",
        "-f",
        f"state={verdict.state}",
        "-f",
        f"context={CONTEXT}",
        "-f",
        f"description={description(verdict.message)}",
    ]
    if target_url:
        args += ["-f", f"target_url={target_url}"]
    _gh(args)


def write_summary(path: str | None, verdict: Verdict, sha: str, number: int | None) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as out:
        # JSON quoting keeps PR-controlled paths and body text from adding markup.
        out.write(f"`{CONTEXT}`: {verdict.state} on `{sha}` (PR #{number})\n\n")
        out.write("    message: " + json.dumps(verdict.message, ensure_ascii=True) + "\n")
        out.write("    paths: " + json.dumps(list(verdict.paths), ensure_ascii=True) + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post the trading trigger's verdict as a status.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY))
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int, help="Judge this pull request.")
    target.add_argument(
        "--merge-group-ref", help="Judge the PR this merge-queue branch is testing."
    )
    parser.add_argument("--expected-head", help="Skip when the PR's head is no longer this SHA.")
    parser.add_argument(
        "--status-sha", help="Commit to post the status on (default: the PR's head)."
    )
    parser.add_argument("--target-url", help="Link for the status, such as the workflow run.")
    parser.add_argument("--print-only", action="store_true", help="Print; post nothing.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    queued = args.merge_group_ref is not None
    number: int | None = None
    sha: str | None = args.status_sha or args.expected_head
    try:
        number = queued_pr_number(args.merge_group_ref) if queued else int(args.pr)
        verdict, head = evaluate(
            load_trigger(), args.repo, number, expected_head=args.expected_head, queued=queued
        )
        sha = sha or head
    except Skip as skip:
        print(f"Skipped: {skip}")
        return 0
    except Exception as error:  # noqa: BLE001 - any failure must not leave an old success
        verdict = Verdict("error", f"Trading trigger failed to run: {error}")
        if sha is None:
            print(verdict.message, file=sys.stderr)
            return 1
        if not args.print_only:
            post_status(args.repo, sha, verdict, args.target_url)
        write_summary(os.environ.get("GITHUB_STEP_SUMMARY"), verdict, sha, number)
        print(verdict.message, file=sys.stderr)
        return 1

    print(f"{CONTEXT}: {verdict.state} on {sha[:12]} (PR #{number}): {verdict.message}")
    if not args.print_only:
        post_status(args.repo, sha, verdict, args.target_url)
    write_summary(os.environ.get("GITHUB_STEP_SUMMARY"), verdict, sha, number)
    return 0


if __name__ == "__main__":
    sys.exit(main())

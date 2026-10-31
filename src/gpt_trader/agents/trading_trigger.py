"""Trading trigger: flag PRs that can reach real trades, broker accounts or their keys.

RJ's standing merge policy (Workspace Operations ``HANDOFF-CONTRACT.md``, "What
needs review") lets agents merge their own PRs. In GPT-Trader one more change
stays RJ's (reserved item e): anything that can place, change or cancel a
trade, or that touches a broker or exchange account or its keys.

Reserved is judged by what a change does, not by file names. These paths are
the trigger: a PR that changes one must say so in its body, either declaring
``Reserved: e ...`` (RJ authorizes the head) or ``Reserved: none`` with a clean
cross-family ``Review:`` line for the current head that confirms it.
``agent-pr-ready`` reports the result.

Replay the trigger over recent merged PRs::

    uv run python -m gpt_trader.agents.trading_trigger --replay 20
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# Paths whose changes can reach a real venue, a broker account or its keys.
TRADING_PATTERNS: tuple[str, ...] = (
    "src/gpt_trader/features/brokerages/**",
    "src/gpt_trader/features/live_trade/**",
    "src/gpt_trader/features/idea_execution/**",
    "src/gpt_trader/cli/commands/orders.py",
    "src/gpt_trader/cli/commands/coinbase.py",
    "src/gpt_trader/cli/commands/broker_connectivity.py",
    "src/gpt_trader/security/secrets_manager.py",
    "src/gpt_trader/security/order_validator.py",
    "src/gpt_trader/app/config/bot_config.py",
    "src/gpt_trader/app/config/profile_loader.py",
    "config/brokers/**",
    "config/risk/**",
    "config/profiles/prod.yaml",
    "config/profiles/canary.yaml",
    "config/profiles/spot.yaml",
    "config/environments/.env.template",
)

# Inside those paths, these cannot reach a real venue or account.
TRADING_EXCEPT: tuple[str, ...] = (
    "**/*.md",
    "src/gpt_trader/features/brokerages/mock/**",
    "src/gpt_trader/features/brokerages/paper/**",
    "src/gpt_trader/features/live_trade/telemetry/**",
)

# The contract's PR-body lines. Like the Workspace Operations gate, read raw
# lines: a line counts wherever it sits, so a declaration can't be hidden.
_DECLARATION = re.compile(
    r"^(?:[-*] )?(?:\*\*)?(reserved|review)(?:\*\*)?:(?:\*\*)?(.*)$", re.IGNORECASE
)
_NONE = re.compile(r"\s*none\.?\s*", re.IGNORECASE)
_CLEAN_REVIEW = re.compile(
    r"^\s*\S+\s+\((?:Codex|Claude)\)\s+at\s+([0-9a-f]{7,40}):\s*clean\b", re.IGNORECASE
)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


_INCLUDE = tuple(_glob_regex(p) for p in TRADING_PATTERNS)
_EXCLUDE = tuple(_glob_regex(p) for p in TRADING_EXCEPT)


def trading_paths(paths: Iterable[str]) -> list[str]:
    """Changed paths that hit the trading trigger, sorted and deduplicated."""
    hits = {
        path
        for path in paths
        if any(rx.match(path) for rx in _INCLUDE) and not any(rx.match(path) for rx in _EXCLUDE)
    }
    return sorted(hits)


def _declarations(body: str, name: str) -> list[str]:
    values = []
    for line in (body or "").splitlines():
        match = _DECLARATION.match(line.strip())
        if match and match.group(1).lower() == name:
            values.append(match.group(2).strip().strip("*").strip())
    return values


def reserved_values(body: str) -> list[str]:
    """Values of every ``Reserved:`` line; empty when the body has none."""
    return _declarations(body, "reserved")


def clean_review_for_head(body: str, head_oid: str) -> bool:
    """Whether a ``Review:`` line records a clean review of this exact head."""
    head = head_oid.lower()
    for value in _declarations(body, "review"):
        match = _CLEAN_REVIEW.match(value)
        if match and head and head.startswith(match.group(1).lower()):
            return True
    return False


@dataclass(frozen=True)
class TriggerResult:
    severity: str  # blocker / warning / info
    message: str
    paths: tuple[str, ...] = ()


def assess(
    paths: Sequence[str],
    body: str,
    head_oid: str,
    rj_authorized_head: str | None = None,
) -> TriggerResult:
    """Apply reserved item (e) and the contract's Reserved line to one PR."""
    hits = tuple(trading_paths(paths))
    values = reserved_values(body)
    reserved = [v for v in values if not _NONE.fullmatch(v)]
    blank = [v for v in values if not v]
    authorized = bool(
        rj_authorized_head
        and head_oid
        and head_oid.lower().startswith(rj_authorized_head.lower())
        and len(rj_authorized_head) >= 7
    )
    hit_note = f" Trading paths: {', '.join(hits)}." if hits else ""

    if reserved or blank:
        label = reserved[0] if reserved else "blank"
        if authorized:
            return TriggerResult(
                "info", f"Reserved ({label}): RJ authorized head {head_oid[:7]}.{hit_note}", hits
            )
        return TriggerResult(
            "blocker",
            f"Reserved ({label}): RJ must authorize head {head_oid[:7]} before merge; "
            f"pass --rj-authorized-head once he has.{hit_note}",
            hits,
        )
    if hits:
        if not values:
            return TriggerResult(
                "blocker",
                "Trading or broker paths changed, and the PR body has no `Reserved:` line. "
                "Declare `Reserved: e - <effect>` for RJ, or `Reserved: none` with a clean "
                f"cross-family review of this head confirming it.{hit_note}",
                hits,
            )
        if not clean_review_for_head(body, head_oid):
            return TriggerResult(
                "blocker",
                "Trading or broker paths changed with `Reserved: none`; a clean cross-family "
                f"`Review:` line for head {head_oid[:7]} must confirm that.{hit_note}",
                hits,
            )
        return TriggerResult(
            "info",
            f"Trading or broker paths changed; `Reserved: none` confirmed by a clean review "
            f"of {head_oid[:7]}.{hit_note}",
            hits,
        )
    if not values:
        return TriggerResult(
            "warning",
            "PR body has no `Reserved:` line; the shared handoff contract asks every PR "
            "for `Reserved: none` or the reserved effect.",
        )
    return TriggerResult("info", "Trading trigger: no trading or broker paths; Reserved: none.")


# --------------------------------------------------------------------------- #
# Replay over merged PRs (evidence for tuning the trigger).
# --------------------------------------------------------------------------- #
def _gh_json(args: list[str]) -> Any:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh failed")
    return json.loads(result.stdout)


def pr_files(repo: str, number: int) -> list[str]:
    pages = _gh_json(
        ["api", "--paginate", "--slurp", f"repos/{repo}/pulls/{number}/files?per_page=100"]
    )
    return [item["filename"] for page in pages for item in page]


def replay(repo: str, limit: int) -> list[dict[str, Any]]:
    merged = _gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,title",
        ]
    )
    rows: list[dict[str, Any]] = []
    for pr in merged:
        hits = trading_paths(pr_files(repo, int(pr["number"])))
        rows.append({"number": pr["number"], "title": pr["title"], "paths": hits})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Flag PRs that can reach real trades, broker accounts or their keys."
    )
    parser.add_argument("--replay", type=int, metavar="N", help="Classify the last N merged PRs.")
    parser.add_argument("--repo", default="Solders-Girdles-Org/GPT-Trader")
    parser.add_argument("paths", nargs="*", help="Changed paths to classify.")
    args = parser.parse_args(argv)
    if args.replay:
        print("| PR | Title | Trigger | Trading paths |")
        print("| --- | --- | --- | --- |")
        for row in replay(args.repo, args.replay):
            paths = row["paths"]
            hit = "yes" if paths else "no"
            listed = ", ".join(f"`{p}`" for p in paths) or "-"
            title = str(row["title"]).replace("|", "\\|")
            print(f"| #{row['number']} | {title} | {hit} | {listed} |")
        return 0
    for path in trading_paths(args.paths):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

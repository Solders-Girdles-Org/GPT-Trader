"""Trading trigger: flag PRs that can reach real trades, broker accounts or their keys.

RJ's standing merge policy (Workspace Operations ``HANDOFF-CONTRACT.md``, "What
needs review") lets agents merge their own PRs. In GPT-Trader one more change
stays RJ's (reserved item e): anything that can place, change or cancel a
trade, or that touches a broker or exchange account or its keys.

Reserved is judged by what a change does, not by file names. These paths are
the trigger: a PR that changes one must say so in its body, either declaring
``Reserved: e ...`` (RJ authorizes the head) or ``Reserved: none`` with a clean
cross-family ``Review:`` line for the current head that confirms it. Every PR
needs a ``Reserved:`` line; one without it is blocked even off these paths.
Once RJ authorizes a reserved head, an ``RJ-authorized: <head SHA>`` line
records it; it clears the reserved blocker for that head only.

The trigger flags directories by default and lists its exceptions with
reasons; anything under ``src/`` that no exception names, such as a new
package, is flagged. So is any path whose top-level directory or root file is
not in TOP_LEVEL_REVIEWED, such as a new ``newdir/run.py``. The trigger also flags itself and the readiness check
that applies it, and ``agent-pr-ready`` judges each PR with the base branch's
trigger as well as the checkout's, so a PR cannot narrow the rule it is judged
by. A token scan backs it up: a tracked file that reads broker keys or builds
a broker outside the trigger fails the unit test until classified. A file
reviewed as holding such a token harmlessly is flagged by its exact path, so
any later edit to it is reviewed again. The scan also fails on a tracked
symlink or submodule that REVIEWED_LINKS does not name.

Two runners apply the rule. ``agent-pr-ready`` runs in the PR's checkout, and
the merging agent runs it: it catches a mistaken ``Reserved: none`` but cannot
stop code written to fool the process it runs in. The required
``trading-trigger/base`` status can't be fooled that way: a
``pull_request_target`` workflow runs this file from the base branch over the
PR's file list and body (``scripts/ci/trading_trigger_status.py``), and no file
from the PR runs. Its limit: GitHub identifies a status by name and the app
that posted it, so a PR that adds its own workflow could post the same name.

Replay the trigger over recent merged PRs, or run the scan::

    uv run python -m gpt_trader.agents.trading_trigger --replay 60
    uv run python -m gpt_trader.agents.trading_trigger --self-check
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The trigger flags whole directories that hold live-capable code, so a new
# file there is caught by default; TRADING_EXCEPT then names, with a reason,
# what inside them cannot reach a real venue, account or key. Widening the
# trigger is not reserved; every exception added here must say why.
TRADING_PATTERNS: tuple[str, ...] = (
    # Broker adapters, the live loop and idea execution, including any sibling
    # package a refactor adds next to them (brokerages_v2, live_trade_v2, ...).
    "src/gpt_trader/features/brokerages*/**",
    "src/gpt_trader/features/live_trade*/**",
    "src/gpt_trader/features/idea_execution*/**",
    # Entry points and runtime wiring: the CLI parses --profile and --dry-run
    # and runs the bot; app/ bootstraps, picks mock or real broker and builds
    # the container; config/types.py names the profiles; preflight reads real
    # keys and calls the account; security/ holds secrets and order checks.
    "src/gpt_trader/cli/**",
    "src/gpt_trader/app/**",
    "src/gpt_trader/config/**",
    "src/gpt_trader/preflight/**",
    "src/gpt_trader/security/**",
    # Profiles, broker specs, risk limits and the env template: any profile can
    # set mock_broker false (real client, real keys) and dry_run false.
    "config/**",
    # Operator scripts that launch the canary, read real keys or place orders.
    "scripts/ops/**",
    "scripts/monitoring/**",
    "scripts/production_preflight.py",
    # Deployment: what the image runs, which profile, which keys it gets.
    "deploy/**",
    "Makefile",
    "**/Dockerfile*",
    "**/docker-compose*",
    "**/.env*",
    # Tests that read real keys and call a real account, and the suites that
    # build the app container or broker clients: one flipped mock_broker or
    # auth argument there reaches the real account when run with keys.
    "tests/real_api/**",
    "tests/integration/**",
    "tests/contract/**",
    "tests/property/**",
    "tests/conftest.py",
    "tests/constants.py",
    # The gate itself. A PR that edits the trigger, its exceptions, the
    # readiness check that applies it, the entry point that runs that check,
    # or the tests that pin them could narrow the rule it is judged by.
    "src/gpt_trader/agents/**",
    "**/sitecustomize.py",
    "**/usercustomize.py",
    "scripts/agents/**",
    "scripts/ci/check_branch_protection.py",
    # The required status that applies this rule from base-branch code.
    ".github/workflows/trading-trigger.yml",
    "scripts/ci/trading_trigger_status.py",
    "tests/unit/scripts/test_trading_trigger_status.py",
    "tests/unit/gpt_trader/agents/test_trading_trigger*.py",
    "tests/unit/scripts/test_pr_readiness_base_trigger.py",
    # Root files that decide what other code runs (RJ-1143): pytest loads the
    # root conftest.py and pytest.ini plugins and options on every run,
    # including the real-API suites; pyproject.toml names the ``gpt-trader``
    # entry point and what the image installs; coverage and pre-commit load
    # plugins and hooks.
    "conftest.py",
    "pytest.ini",
    "pyproject.toml",
    ".coveragerc",
    ".pre-commit-config.yaml",
    # docs/ holds only Markdown, which the token scan skips, so anything else
    # there is flagged (the **/*.md exception keeps prose unflagged).
    "docs/**",
    # Everything else under src/, so a new package or module (a
    # ``src/gpt_trader/live/`` runner, or a sibling package setuptools would
    # install next to gpt_trader) is flagged until an exception below names it
    # with a reason.
    "src/**",
)

# Fail closed at the top of the tree (RJ-1143): a path whose first segment is
# not named here, such as a new top-level directory or root file, is flagged.
# Each entry says why paths under it are handled. A directory ends in "/"; a
# root file matches only itself, so ``LICENSE/run.py`` is still flagged. The full fail-closed design
# (flag everything outside a short allowlist) was measured and declined: over
# the last 60 merged PRs it flagged 50 against 14, mostly Dependabot action
# bumps and offline experiment code, for a gain only against code that avoids
# every LIVE_CAPABLE token.
TOP_LEVEL_REVIEWED: dict[str, str] = {
    "src/": "flagged by default above; exceptions name what cannot trade",
    "config/": "profiles and broker specs flagged above; exceptions name the rest",
    "deploy/": "flagged above; exceptions name dashboards and scrape settings",
    "scripts/": "launchers and the gate flagged above; the token scan covers the rest",
    "tests/": "suites that can reach a real account flagged above; unit tests run on mocks",
    "docs/": "Markdown only; anything else there is flagged above",
    ".github/": (
        "CI only: GitHub holds no broker keys and the image publishes on a tag or "
        "manual dispatch; trading-trigger.yml is flagged above"
    ),
    "var/": "local runtime state placeholder",
    "review_artifacts/": "local review output placeholder",
    ".gitignore": "which files git ignores; a new .env file is flagged by its own path",
    ".python-version": "the interpreter version uv selects",
    "LICENSE": "license text",
    "uv.lock": "pins versions of what pyproject.toml, flagged above, declares",
}

# Inside those directories, what cannot reach a real venue, account or key.
# A wildcard exception's files stay under the LIVE_CAPABLE scan below, so a new
# file there that builds a broker still fails the unit test.
TRADING_EXCEPT: dict[str, str] = {
    "**/*.md": "documentation",
    # Packages reviewed under the src/gpt_trader/** default (RJ-1118). None
    # builds a broker or the app container; the scan backs each one up.
    "src/gpt_trader/backtesting/**": "simulated broker over recorded or public candles",
    "src/gpt_trader/ci/**": "CI helper scripts",
    "src/gpt_trader/core/**": "shared types and protocols",
    "src/gpt_trader/errors/**": "error types",
    "src/gpt_trader/logging/**": "log setup and formatting",
    "src/gpt_trader/monitoring/**": "health, metrics and alerts over local state",
    "src/gpt_trader/persistence/**": "local event and state stores",
    "src/gpt_trader/utilities/**": "generic helpers",
    "src/gpt_trader/validation/**": "input validators",
    "src/gpt_trader/web/**": "local read-only web views",
    "src/gpt_trader/features/data/**": "market data access over public endpoints",
    "src/gpt_trader/features/experiment/**": "offline experiments over recorded inputs",
    "src/gpt_trader/features/intelligence/**": "signal and model helpers",
    # equities_candles.py and snapshot_source.py read Alpaca account keys, so stay flagged.
    "src/gpt_trader/features/recorder/__init__.py": "re-exports recorder names",
    "src/gpt_trader/features/recorder/market_data_recorder.py": "records public market data",
    "src/gpt_trader/features/recorder/price_tick_store.py": "local tick store",
    "src/gpt_trader/features/recorder/snapshot_builder.py": "builds snapshots from fetched data",
    "src/gpt_trader/features/strategy_dev/**": "strategy development helpers",
    "src/gpt_trader/features/strategy_tools/**": "strategy helper functions",
    "src/gpt_trader/features/trade_ideas/**": "trade idea records; execution is idea_execution",
    "src/gpt_trader/features/live_trade/telemetry/**": "metrics and event emission only",
    "src/gpt_trader/cli/response.py": "CLI output formatting",
    "src/gpt_trader/cli/commands/console.py": (
        "operator console over TradeIdeaService; never contacts a broker"
    ),
    "src/gpt_trader/cli/commands/experiment.py": (
        "offline experiments over recorded inputs; imports no broker or container"
    ),
    "src/gpt_trader/cli/commands/ideas_input.py": "parses idea inputs from files",
    "src/gpt_trader/cli/commands/report.py": "reports from local event files",
    "src/gpt_trader/cli/commands/strategy_profile.py": "diffs strategy-profile JSON",
    "src/gpt_trader/preflight/checks/risk.py": "checks local risk settings only",
    "config/agents/**": "agent tooling settings (naming, docs currency)",
    "config/experiments/**": "pinned experiment protocols; replayed offline",
    "scripts/ops/formatting.py": "output helpers for the ops scripts",
    "scripts/ops/liveness_check.py": "reads the local event store",
    "scripts/ops/readiness_window.py": "reads the local event store",
    "scripts/ops/runtime_fingerprint.py": "reads the local event store",
    "scripts/ops/tail_decision_traces.py": "reads the local event store",
    "scripts/ops/replay_evidence.sh": "broker-free replay over public candles",
    "scripts/ops/stage1_rails_smoke.py": "offline smoke on the deterministic paper broker",
    "scripts/monitoring/export_metrics.py": "serves local metrics over HTTP",
    "scripts/monitoring/manage_logs.py": "rotates local logs",
    "deploy/gpt_trader/docker/grafana/**": "dashboards over local metrics",
    "deploy/gpt_trader/docker/prometheus*.yml": "metrics scrape settings",
}

# Backstop for the path rule: text that reads real broker keys, builds a broker
# client or container, or picks mock versus real broker and dry run. Every
# tracked file outside the skipped areas that contains one must be flagged
# (listing it in REVIEWED_LIVE_TOKEN flags it), so a new live-capable file,
# including an excepted one, fails the unit test (``--self-check``) until
# someone classifies it. Building the app
# container counts: it holds whatever broker the profile picks, so one added
# call in a script that builds it could place an order.
LIVE_CAPABLE = re.compile(
    r"COINBASE_[A-Z_]*(?:API_KEY|PRIVATE_KEY|API_SECRET|CDP|CREDENTIALS|SANDBOX|PASSPHRASE)"
    r"|ROBINHOOD_[A-Z_]*(?:KEY|SECRET|TOKEN)|ALPACA_[A-Z_]*(?:KEY|SECRET)"
    r"|resolve_coinbase_credentials|create_brokerage|CoinbaseClient\(|CDPJWTAuth\("
    r"|SimpleAuth\(|HybridPaperBroker\(|instantiate_container|create_application_container"
    # Ways around the names above (RJ-1118): the container class itself, any
    # import or module path into the broker adapters or the container (which
    # also catches ``CoinbaseClient as C``), and dynamic imports that can
    # assemble those names at run time.
    r"|\bApplicationContainer\b|\bfeatures[./]brokerages\b|\bapp[./]container\b"
    r"|\bfrom\s+[.\w]*\b(?:features|app)\s+import\b[^\n]*\b(?:brokerages|container)\b"
    r"|\bimport_module\b|\b__import__\b|\bspec_from_file_location\b|\brunpy\b"
    r"|MOCK_BROKER|\bDRY_RUN\b|--dry-run(?![-\w])|gpt-trader run|gpt_trader\.cli.{0,20}\brun\b"
    r"""|["']run["'],\s*["']--(?:profile|dry-run)"""
    # A YAML or JSON file that turns live off-switches off. ``run --config``
    # takes a file from anywhere and sets each switch to true unless the file
    # sets it to a boolean false, so a file outside config/ trades live only
    # by carrying one of these lines. Empty and null values are flagged too
    # (RJ-1127): another loader may read them as false. A bare key line is
    # matched only at line start, so Python's ``if dry_run:`` is not.
    r"""|(?i:\b(?:dry_run|mock_broker)["']?\s*:\s*"""
    r"""(?:["']?(?:false|no|off|0|null|none)\b|~|""|''))"""
    r"""|(?im:^[ \t-]*["']?(?:dry_run|mock_broker)["']?[ \t]*:[ \t]*(?:#.*)?$)"""
)

# Areas the backstop does not scan: prose, and unit tests (mock-only in CI).
SCAN_SKIP: tuple[str, ...] = ("**/*.md", "docs/**", "tests/unit/**", "uv.lock")

# Files that contain a LIVE_CAPABLE token but were reviewed as unable to reach
# a real venue, account or key, and why. The review covers the content it saw,
# so each is flagged by its exact path, ahead of any exception (RJ-1120): an
# edit that turned ``auth=None`` into a real key would otherwise pass as
# routine. Measured over the last 60 merged PRs on 2026-11-01, none of these
# changed outside a PR already flagged; a busy file with only a passing
# mention should drop the token instead (as ci.yml did).
REVIEWED_LIVE_TOKEN: dict[str, str] = {
    ".github/CODEOWNERS": "review ownership by path",
    ".github/actions/setup-coinbase-creds/action.yml": "writes placeholder test values",
    "scripts/analysis/backtest_runner.py": "unauthenticated client for public candles",
    "scripts/ci/check_import_boundaries.py": "import-lint rules that name module paths",
    "scripts/ci/check_orchestration_imports.py": "import-lint rules that name module paths",
    "scripts/maintenance/docs_currency_scan.py": "imports the modules docs name, to check they exist",
    "scripts/maintenance/feature_slice_scaffold.py": "its own --dry-run for file scaffolding",
    "src/gpt_trader/backtesting/data/manager.py": (
        "public historical candles; CoinbaseClient appears only as a type and docstring example"
    ),
    "src/gpt_trader/features/brokerages/mock/deterministic.py": (
        "in-memory broker; names MOCK_BROKER only in its docs"
    ),
    "src/gpt_trader/features/data/data.py": "docstring example only",
    "src/gpt_trader/features/recorder/candle_capture.py": "client built with auth=None",
    "src/gpt_trader/logging/setup.py": "sets a log level by logger name",
    "src/gpt_trader/monitoring/configuration_guardian/environment.py": (
        "watches MOCK_BROKER for drift; does not set it"
    ),
    "src/gpt_trader/monitoring/status_reporter.py": "type-checking import of market data only",
    "src/gpt_trader/utilities/importing/lazy.py": "generic lazy import; callers name the module",
    "src/gpt_trader/utilities/importing/optional.py": (
        "generic optional import; callers name the module"
    ),
    "src/gpt_trader/utilities/importing/profiling.py": "times imports by wrapping __import__",
}

# Tracked symlinks and submodules, and why each is safe (RJ-1143). Either makes
# a path's content come from somewhere else, so a path rule over the link's own
# name can't judge it; ``--self-check`` fails on any link not named here.
REVIEWED_LINKS: dict[str, str] = {
    "CLAUDE.md": "symlink to AGENTS.md",
}

# The contract's PR-body lines. Like the Workspace Operations gate, read raw
# lines: a line counts wherever it sits, so a declaration can't be hidden.
_DECLARATION = re.compile(
    r"^(?:[-*] )?(?:\*\*)?(reserved|review|rj-authorized)(?:\*\*)?:(?:\*\*)?(.*)$",
    re.IGNORECASE,
)
# ``RJ-authorized: <head SHA> ...``: the head RJ authorized, then free text
# (his words or where they are recorded).
_AUTHORIZED_HEAD = re.compile(r"([0-9a-f]{7,40})(?![0-9a-z])", re.IGNORECASE)
_NONE = re.compile(r"\s*none\.?\s*", re.IGNORECASE)
# The contract's only endings for a clean outcome: non-blocking notes, or a head
# carried across a rebase for one of the listed reasons. Anything else is malformed.
_CLEAN_ENDINGS = re.compile(
    r"(?:[,;] (?:with )?(?:\d+ |one |two |three |some )?non-blocking (?:notes?|findings?)"
    r"|; no blocking findings"
    r"| \(carried from [0-9a-f]{7,40}: "
    r"(?:clean rebase|rebase only|base merge only|no behavior change)\))?",
    re.IGNORECASE,
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
    # Case-insensitive: the runtime Mac's default APFS volume is, so
    # ``Config/profiles/x.yaml`` there is the flagged file.
    return re.compile(out + r"\Z", re.IGNORECASE)


_INCLUDE = tuple(_glob_regex(p) for p in TRADING_PATTERNS)
_TOP_LEVEL = frozenset(name.lower() for name in TOP_LEVEL_REVIEWED)
_EXCLUDE = tuple(_glob_regex(p) for p in TRADING_EXCEPT)
_REVIEWED = tuple(_glob_regex(p) for p in REVIEWED_LIVE_TOKEN)


def _top_level(path: str) -> str:
    """``src/`` for a path under a directory, the name itself for a root file."""
    head, sep, _ = path.partition("/")
    return head + sep


def _unreviewed_top_level(path: str) -> bool:
    # Lowercased like the globs: the runtime Mac's volume is case-insensitive.
    return _top_level(path).lower() not in _TOP_LEVEL


def _flagged(path: str) -> bool:
    if any(rx.match(path) for rx in _REVIEWED):
        return True
    if not (any(rx.match(path) for rx in _INCLUDE) or _unreviewed_top_level(path)):
        return False
    return not any(rx.match(path) for rx in _EXCLUDE)


def trading_paths(paths: Iterable[str]) -> list[str]:
    """Changed paths that hit the trading trigger, sorted and deduplicated."""
    return sorted({path for path in paths if _flagged(path)})


_SKIP = tuple(_glob_regex(p) for p in SCAN_SKIP)


def _skipped(path: str) -> bool:
    return any(rx.match(path) for rx in _SKIP)


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    )
    return [p for p in result.stdout.split("\0") if p]


_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)
_CODING = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)", re.MULTILINE)


def scan_text(data: bytes) -> str:
    """Every plausible reading of a file's bytes, so an encoding can't hide a token.

    UTF-8 always; the codec a byte-order mark names; UTF-16 both ways when the
    bytes hold NULs (UTF-16 text without a mark); and a Python coding cookie.
    """
    readings = [data.decode("utf-8", errors="ignore")]
    codecs = [codec for bom, codec in _BOMS if data.startswith(bom)][:1]
    if b"\x00" in data:
        codecs += ["utf-16-le", "utf-16-be"]
    cookie = _CODING.search(b"\n".join(data.splitlines()[:2]))
    if cookie:
        codecs.append(cookie.group(1).decode("ascii", errors="ignore"))
    for codec in codecs:
        try:
            readings.append(data.decode(codec, errors="ignore"))
        except Exception:  # noqa: BLE001 - any codec a cookie names; keep the other readings
            continue
    return "\n".join(readings)


def unflagged_live_capable(root: Path, paths: Iterable[str] | None = None) -> list[str]:
    """Files with a LIVE_CAPABLE token that the trigger does not flag.

    Exceptions in TRADING_EXCEPT are scanned too, exact names included, so an
    excepted file that starts building a broker fails until reclassified.
    """
    misses = []
    for path in tracked_files(root) if paths is None else paths:
        if _skipped(path) or trading_paths([path]):
            continue
        try:
            data = (root / path).read_bytes()
        except OSError:
            continue
        if LIVE_CAPABLE.search(scan_text(data)):
            misses.append(path)
    return sorted(misses)


_LINK_MODES = ("120000", "160000")  # git's symlink and submodule modes


def unreviewed_links(root: Path) -> list[str]:
    """Tracked symlinks and submodules that REVIEWED_LINKS does not name."""
    result = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=root, capture_output=True, text=True, check=True
    )
    links = []
    for entry in filter(None, result.stdout.split("\0")):
        meta, path = entry.split("\t", 1)
        if meta.split()[0] in _LINK_MODES and path not in REVIEWED_LINKS:
            links.append(path)
    return sorted(links)


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


def rj_authorized_heads(body: str) -> list[str]:
    """Heads named by ``RJ-authorized:`` lines, lowercased; malformed lines are ignored."""
    heads = []
    for value in _declarations(body, "rj-authorized"):
        match = _AUTHORIZED_HEAD.match(value)
        if match:
            heads.append(match.group(1).lower())
    return heads


def _authorizes(sha: str | None, head_oid: str) -> bool:
    return bool(sha and head_oid and len(sha) >= 7 and head_oid.lower().startswith(sha.lower()))


def parse_review(value: str, repo: str | None = None, number: int | None = None) -> dict | None:
    """``<review link> (Codex|Claude) at <sha>: clean[ allowed ending]`` or ``...: blocking``.

    Returns None for anything else, so a qualified or unfinished outcome never counts.
    With ``repo`` and ``number`` the link must be a review on that pull request.
    """
    if repo and number:
        link = re.escape(f"https://github.com/{repo}/pull/{number}")
    else:
        link = r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+"
    match = re.fullmatch(
        link + r"#pullrequestreview-\d+ \((codex|claude)\) at ([0-9a-f]{7,40}): "
        r"(clean|blocking)(.*)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    outcome, ending = match.group(3).lower(), match.group(4)
    if outcome == "clean" and not _CLEAN_ENDINGS.fullmatch(ending):
        return None
    if outcome == "blocking" and ending and not re.match(r"\W", ending):
        return None  # "blockingly" and the like are not an outcome
    return {"family": match.group(1).lower(), "sha": match.group(2).lower(), "outcome": outcome}


def clean_review_for_head(
    body: str, head_oid: str, repo: str | None = None, number: int | None = None
) -> bool:
    """Whether the body's ``Review:`` lines confirm a clean review of this exact head.

    Every Review line must be well formed (or ``none``), none may record a blocking
    outcome for this head, and at least one must record a clean one.
    """
    head = (head_oid or "").lower()
    if not head:
        return False
    values = [v for v in _declarations(body, "review") if not _NONE.fullmatch(v)]
    reviews = [parse_review(v, repo, number) for v in values]
    if any(r is None for r in reviews):
        return False
    current = [r for r in reviews if r and head.startswith(r["sha"])]
    return bool(current) and all(r["outcome"] == "clean" for r in current)


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
    repo: str | None = None,
    number: int | None = None,
) -> TriggerResult:
    """Apply reserved item (e) and the contract's Reserved line to one PR.

    RJ's authorization comes from ``rj_authorized_head`` or an ``RJ-authorized:``
    line in the body; either counts only for the current head.
    """
    hits = tuple(trading_paths(paths))
    values = reserved_values(body)
    reserved = [v for v in values if not _NONE.fullmatch(v)]
    blank = [v for v in values if not v]
    authorized = any(
        _authorizes(sha, head_oid) for sha in (rj_authorized_head, *rj_authorized_heads(body))
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
            f"once he has, add `RJ-authorized: {head_oid[:7]}` to the PR body.{hit_note}",
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
        if not clean_review_for_head(body, head_oid, repo, number):
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
        # A path the trigger misses must not merge undeclared: the line is the
        # author's own check that the change cannot reach a trade or key.
        return TriggerResult(
            "blocker",
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
    # A rename lists both paths, so moving a trading module out of the
    # trigger's directories still counts as a trading change.
    paths: list[str] = []
    for page in pages:
        for item in page:
            paths.append(item["filename"])
            if item.get("previous_filename"):
                paths.append(item["previous_filename"])
    return paths


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
    parser.add_argument(
        "--self-check",
        action="store_true",
        help=(
            "List tracked files that look live-capable but sit outside the trigger, "
            "and symlinks or submodules no one has reviewed."
        ),
    )
    parser.add_argument("paths", nargs="*", help="Changed paths to classify.")
    args = parser.parse_args(argv)
    if args.self_check:
        misses = unflagged_live_capable(Path.cwd())
        links = unreviewed_links(Path.cwd())
        for path in misses:
            print(path)
        for path in links:
            print(f"{path} (unreviewed symlink or submodule)")
        return 1 if misses or links else 0
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

from __future__ import annotations

from pathlib import Path

import pytest

from gpt_trader.agents import trading_trigger as tt

HEAD = "abcdef1234567890"


@pytest.mark.parametrize(
    "path",
    [
        "src/gpt_trader/features/brokerages/coinbase/rest/order_service.py",
        "src/gpt_trader/features/brokerages/coinbase/credentials.py",
        "src/gpt_trader/features/brokerages/robinhood/agentic/transport.py",
        "src/gpt_trader/features/brokerages/factory.py",
        "src/gpt_trader/features/live_trade/execution/order_submission.py",
        "src/gpt_trader/features/live_trade/risk/manager.py",
        "src/gpt_trader/features/idea_execution/executor.py",
        "src/gpt_trader/cli/commands/orders.py",
        "src/gpt_trader/security/secrets_manager.py",
        "src/gpt_trader/app/config/bot_config.py",
        "config/brokers/coinbase_perp_specs.yaml",
        "config/profiles/prod.yaml",
        "config/profiles/canary.yaml",
        "config/profiles/spot.yaml",
        "config/profiles/demo.yaml",
        "config/profiles/observe.yaml",
        "config/profiles/paper.yaml",
        "config/profiles/dev.yaml",
        "src/gpt_trader/app/config/profile_schema.py",
        "src/gpt_trader/cli/services.py",
        "config/environments/.env.template",
        "scripts/monitoring/canary_reduce_only_test.py",
        "scripts/ops/api_connectivity_smoke.py",
        "scripts/ops/hybrid_paper_broker_smoke.py",
        "src/gpt_trader/app/container.py",
        "src/gpt_trader/app/containers/brokerage.py",
        "src/gpt_trader/cli/commands/account.py",
        "src/gpt_trader/preflight/core.py",
        "src/gpt_trader/preflight/context.py",
        "src/gpt_trader/preflight/diagnostics_bundle.py",
        "src/gpt_trader/preflight/checks/connectivity.py",
        "src/gpt_trader/preflight/checks/diagnostics.py",
        "src/gpt_trader/preflight/checks/environment.py",
        "src/gpt_trader/preflight/checks/system.py",
        # RJ-1116: run entry points, canary launch, deploy, real-API tests,
        # sibling packages, and scripts that build the app container.
        "src/gpt_trader/cli/commands/run.py",
        "src/gpt_trader/cli/options.py",
        "src/gpt_trader/cli/__init__.py",
        "src/gpt_trader/cli/__main__.py",
        "src/gpt_trader/cli/commands/record.py",
        "src/gpt_trader/app/bootstrap.py",
        "src/gpt_trader/config/types.py",
        "src/gpt_trader/features/brokerages/paper/hybrid.py",
        "src/gpt_trader/features/brokerages/mock/__init__.py",
        "src/gpt_trader/features/brokerages_v2/coinbase/client.py",
        "src/gpt_trader/features/live_trade_v2/engine.py",
        "scripts/ops/canary_process.py",
        "scripts/ops/canary_watchdog.py",
        "scripts/ops/product_catalog_probe.py",
        "scripts/ops/decision_trace_probe.py",
        "scripts/ops/stage1_cycle_turn.sh",
        "scripts/monitoring/perps_dashboard.py",
        "scripts/production_preflight.py",
        "Makefile",
        "deploy/gpt_trader/docker/Dockerfile",
        "deploy/gpt_trader/docker/docker-compose.yaml",
        "deploy/scripts/deploy.sh",
        "docker-compose.yaml",
        ".env.production",
        "tests/real_api/test_coinbase_permissions.py",
        "tests/real_api/test_coinbase_read_preview_non_mutation.py",
        "tests/real_api/test_robinhood_agentic_read_review_non_mutation.py",
        "tests/real_api/test_robinhood_crypto_read_preview_non_mutation.py",
        # New files in a flagged directory are caught by default.
        "src/gpt_trader/cli/commands/new_command.py",
        "src/gpt_trader/app/containers/new_container.py",
        "scripts/ops/new_launcher.py",
        # RJ-1117: the runtime Mac's filesystem is case-insensitive.
        "Config/profiles/prod.yaml",
        "src/gpt_trader/features/Brokerages/coinbase/client.py",
        "SRC/gpt_trader/CLI/commands/run.py",
        # RJ-1118: the gate itself, so a PR can't narrow the rule it is judged by.
        "src/gpt_trader/agents/trading_trigger.py",
        "src/gpt_trader/agents/cli.py",
        "src/gpt_trader/agents/__init__.py",
        "scripts/agents/pr_readiness.py",
        "scripts/agents/__init__.py",
        "tests/unit/gpt_trader/agents/test_trading_trigger.py",
        # RJ-1118: a new package or module under src/gpt_trader is flagged by default.
        "src/gpt_trader/live/runner.py",
        "src/gpt_trader/live.py",
        "src/gpt_trader/__init__.py",
        "src/gpt_trader/features/new_feature/runner.py",
        # Builds the app container from BotConfig defaults (real broker, not dry run).
        "scripts/ops/controls_smoke.py",
    ],
)
def test_trading_paths_hit(path: str) -> None:
    assert tt.trading_paths([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "src/gpt_trader/features/brokerages/mock/deterministic.py",
        "src/gpt_trader/features/brokerages/coinbase/README.md",
        "src/gpt_trader/features/live_trade/telemetry/metrics.py",
        "src/gpt_trader/features/trade_ideas/store.py",
        "src/gpt_trader/backtesting/simulation/broker.py",
        "scripts/analysis/backtest_runner.py",
        "src/gpt_trader/monitoring/health_checks.py",
        "src/gpt_trader/features/experiment/runner.py",
        "scripts/agents/naming_inventory.py",
        "src/gpt_trader/preflight/checks/risk.py",
        "src/gpt_trader/cli/commands/experiment.py",
        "src/gpt_trader/cli/commands/console.py",
        "config/experiments/round-two-2026-10-20/protocol.json",
        "config/agents/naming_patterns.yaml",
        "deploy/gpt_trader/docker/grafana/dashboards/system_overview.json",
        "scripts/ops/readiness_window.py",
        "tests/unit/gpt_trader/features/brokerages/coinbase/test_auth.py",
        "config/profiles/README.md",
        "src/gpt_trader/cli/commands/strategy_profile.py",
        "deploy/gpt_trader/README.md",
        "docs/COINBASE.md",
        "uv.lock",
        ".github/workflows/ci.yml",
    ],
)
def test_trading_paths_miss(path: str) -> None:
    assert tt.trading_paths([path]) == []


def test_reserved_values_read_raw_lines() -> None:
    body = "intro\n- **Reserved:** none.\n```\nReserved: b - new paid feed\n```"
    assert tt.reserved_values(body) == ["none.", "b - new paid feed"]


LINK = "https://github.com/o/r/pull/1#pullrequestreview-1"


def test_clean_review_must_name_current_head() -> None:
    body = f"Review: {LINK} (Codex) at abcdef1: clean"
    assert tt.clean_review_for_head(body, HEAD)
    assert not tt.clean_review_for_head(body, "1234567890")
    assert not tt.clean_review_for_head(f"Review: {LINK} (Codex) at abcdef1: blocking", HEAD)


@pytest.mark.parametrize(
    "ending",
    [
        "; no blocking findings",
        ", with 2 non-blocking notes",
        " (carried from 1234567: clean rebase)",
    ],
)
def test_clean_review_allowed_endings(ending: str) -> None:
    assert tt.clean_review_for_head(f"Review: {LINK} (Claude) at abcdef1: clean{ending}", HEAD)


@pytest.mark.parametrize(
    "body",
    [
        # A clean and a blocking outcome for the same head contradict each other.
        f"Review: {LINK} (Codex) at abcdef1: clean\nReview: {LINK} (Claude) at abcdef1: blocking",
        # A qualified clean outcome is not clean.
        f"Review: {LINK} (Codex) at abcdef1: clean; blocking findings remain",
        f"Review: {LINK} (Codex) at abcdef1: clean but see notes",
        # A malformed line anywhere fails the set.
        f"Review: {LINK} (Codex) at abcdef1: clean\nReview: pending (Claude)",
        "Review: https://example.com/x (Codex) at abcdef1: clean",
    ],
)
def test_clean_review_rejects_contradictory_or_qualified_lines(body: str) -> None:
    assert not tt.clean_review_for_head(body, HEAD)


def test_clean_review_for_earlier_head_with_blocking_is_fine() -> None:
    body = (
        f"Review: {LINK} (Codex) at 1234567: blocking\n"
        f"Review: {LINK} (Codex) at abcdef1: clean; no blocking findings\nReview: none"
    )
    assert tt.clean_review_for_head(body, HEAD)


def test_clean_review_link_must_be_this_pr_when_known() -> None:
    body = f"Review: {LINK} (Codex) at abcdef1: clean"
    assert tt.clean_review_for_head(body, HEAD, repo="o/r", number=1)
    assert not tt.clean_review_for_head(body, HEAD, repo="o/r", number=2)


def test_pr_files_lists_both_paths_of_a_rename(monkeypatch) -> None:
    pages = [
        [
            {"filename": "docs/STATUS.md"},
            {
                "filename": "src/gpt_trader/utilities/submit.py",
                "previous_filename": "src/gpt_trader/features/live_trade/execution/order_submission.py",
            },
        ]
    ]
    monkeypatch.setattr(tt, "_gh_json", lambda args: pages)
    paths = tt.pr_files("o/r", 1)
    assert tt.trading_paths(paths) == [
        "src/gpt_trader/features/live_trade/execution/order_submission.py"
    ]


def test_routine_pr_without_trading_paths_is_info() -> None:
    result = tt.assess(["docs/STATUS.md"], "Reserved: none", HEAD)
    assert result.severity == "info"


def test_missing_reserved_line_blocks_even_off_trading_paths() -> None:
    # A path the trigger misses must still carry the author's declaration.
    result = tt.assess(["uv.lock"], "bump deps", HEAD)
    assert result.severity == "blocker"
    assert result.paths == ()


TRADING = ["src/gpt_trader/features/live_trade/execution/order_submission.py"]


def test_trading_paths_without_reserved_line_block() -> None:
    result = tt.assess(TRADING, "no lines", HEAD)
    assert result.severity == "blocker"
    assert result.paths == tuple(TRADING)


def test_trading_paths_with_unconfirmed_none_block() -> None:
    assert tt.assess(TRADING, "Reserved: none", HEAD).severity == "blocker"


def test_trading_paths_with_none_and_clean_review_pass() -> None:
    body = f"Reserved: none\nReview: {LINK} (Claude) at abcdef1: clean; no blocking findings"
    assert tt.assess(TRADING, body, HEAD).severity == "info"


@pytest.mark.parametrize("paths", [TRADING, ["docs/STATUS.md"]])
def test_declared_reserved_blocks_until_rj_authorizes_this_head(paths: list[str]) -> None:
    body = "Reserved: e - can cancel open orders"
    assert tt.assess(paths, body, HEAD).severity == "blocker"
    assert tt.assess(paths, body, HEAD, rj_authorized_head="abcdef1").severity == "info"
    assert tt.assess(paths, body, HEAD, rj_authorized_head="1234567").severity == "blocker"
    assert tt.assess(paths, body, HEAD, rj_authorized_head="abc").severity == "blocker"


def test_blank_reserved_line_blocks() -> None:
    assert tt.assess(["docs/STATUS.md"], "Reserved:", HEAD).severity == "blocker"


def test_any_reserved_value_wins_over_none() -> None:
    body = "Reserved: none\nReserved: a - new API key scope"
    assert tt.assess(["docs/STATUS.md"], body, HEAD).severity == "blocker"


REPO = Path(tt.__file__).resolve().parents[3]


def test_every_exception_has_a_reason() -> None:
    for table in (tt.TRADING_EXCEPT, tt.REVIEWED_OUTSIDE):
        assert all(reason.strip() for reason in table.values())


def test_exceptions_and_reviewed_files_are_current() -> None:
    files = tt.tracked_files(REPO)
    stale = [p for p in tt.TRADING_EXCEPT if not any(tt._glob_regex(p).match(f) for f in files)]
    assert stale == []
    for path in tt.REVIEWED_OUTSIDE:
        text = (REPO / path).read_text(encoding="utf-8")
        assert tt.LIVE_CAPABLE.search(text), f"{path} no longer needs review; drop it"
        assert not tt.trading_paths([path]), f"{path} is flagged; drop it"


def test_no_live_capable_file_sits_outside_the_trigger() -> None:
    # A file that reads broker keys, builds a broker or container, or picks
    # mock versus real must be flagged or carry a reviewed reason.
    assert tt.unflagged_live_capable(REPO) == []


@pytest.mark.parametrize(
    "text",
    [
        'key = os.getenv("COINBASE_PROD_CDP_API_KEY")',
        "broker = create_brokerage(config)",
        "container = create_application_container(config)",
        'subprocess.run(["gpt-trader", "run", "--profile", "canary"])',
        "CMD gpt-trader run --profile prod",
        'os.environ["MOCK_BROKER"] = "0"',
    ],
)
def test_backstop_catches_a_new_live_capable_file(tmp_path: Path, text: str) -> None:
    (tmp_path / "scripts" / "analysis").mkdir(parents=True)
    (tmp_path / "scripts/analysis/new_tool.py").write_text(text)
    (tmp_path / "scripts/analysis/notes.md").write_text(text)
    paths = ["scripts/analysis/new_tool.py", "scripts/analysis/notes.md"]
    assert tt.unflagged_live_capable(tmp_path, paths) == ["scripts/analysis/new_tool.py"]


@pytest.mark.parametrize(
    "text",
    [
        "execution:\n  dry_run: false\n  mock_broker: false\n",
        "symbols: [BTC-USD]\nmock_broker: false\n",
        "dry_run: False\n",
        'dry_run: "no"\n',
        '{"mock_broker": false}',
    ],
)
def test_backstop_catches_a_yaml_that_turns_live_switches_off(tmp_path: Path, text: str) -> None:
    # RJ-1117: run --config takes a file from anywhere, so a file outside the
    # flagged directories that turns a live switch off must fail the scan.
    (tmp_path / "strategies").mkdir()
    (tmp_path / "strategies/live.yaml").write_text(text)
    assert tt.unflagged_live_capable(tmp_path, ["strategies/live.yaml"]) == ["strategies/live.yaml"]


def test_config_file_outside_the_trigger_runs_non_live(tmp_path: Path) -> None:
    # RJ-1117 pin: the trigger does not flag this file, so loading it with
    # run --config must not pick live orders on the real broker.
    from argparse import Namespace

    from gpt_trader.cli import services

    (tmp_path / "strategies").mkdir()
    path = tmp_path / "strategies/live.yaml"
    path.write_text("symbols: [BTC-USD]\ninterval: 60\n")
    assert tt.trading_paths(["strategies/live.yaml"]) == []
    assert tt.unflagged_live_capable(tmp_path, ["strategies/live.yaml"]) == []
    config = services.build_config_from_args(
        Namespace(config=str(path), profile=None, dry_run=False)
    )
    assert (config.dry_run, config.mock_broker) == (True, True)


def test_backstop_ignores_safe_switches_and_type_hints(tmp_path: Path) -> None:
    (tmp_path / "safe.yaml").write_text("dry_run: true\nmock_broker: yes\n")
    (tmp_path / "model.py").write_text("dry_run: bool = False\nmock_broker: bool\n")
    assert tt.unflagged_live_capable(tmp_path, ["safe.yaml", "model.py"]) == []


def test_backstop_ignores_unrelated_dry_run_flags(tmp_path: Path) -> None:
    (tmp_path / "tool.py").write_text('parser.add_argument("--dry-run-summary")')
    assert tt.unflagged_live_capable(tmp_path, ["tool.py"]) == []


def test_a_pr_that_adds_its_own_exception_is_flagged() -> None:
    # RJ-1118: excepting a live file and changing it in the same diff must not
    # read as a routine change.
    paths = [
        "src/gpt_trader/agents/trading_trigger.py",
        "src/gpt_trader/features/brokerages/coinbase/client/orders.py",
    ]
    result = tt.assess(paths, "Reserved: none", HEAD)
    assert result.severity == "blocker"
    assert "src/gpt_trader/agents/trading_trigger.py" in result.paths


def test_agent_pr_ready_runs_the_flagged_readiness_check() -> None:
    # The entry point must stay on the flagged files; pyproject.toml is not
    # flagged, so this pin (in a flagged file) is what holds it.
    import tomllib

    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["agent-pr-ready"] == "gpt_trader.agents.cli:pr_ready"
    cli = (REPO / "src/gpt_trader/agents/cli.py").read_text()
    assert '_run_script("pr_readiness.py")' in cli
    readiness = (REPO / "scripts/agents/pr_readiness.py").read_text()
    assert "from gpt_trader.agents import trading_trigger" in readiness
    assert "trading_trigger.assess(" in readiness


EVASIONS = {
    # Each built a real CoinbaseClient in Grok Scout's RJ-1118 check.
    "container": (
        "from gpt_trader.app.config import BotConfig\n"
        "from gpt_trader.app.container import ApplicationContainer as AC\n"
        "def broker_for(config: BotConfig):\n    return AC(config).broker\n"
    ),
    "importlib": (
        "import importlib\n"
        "m = importlib.import_module('gpt_trader.features.' + 'brokerages.factory')\n"
        "build = getattr(m, 'create_' + 'brokerage')\n"
    ),
    "aliased": (
        "from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient as C\n"
        "from gpt_trader.features.brokerages.coinbase.auth import SimpleAuth as A\n"
        "def client(key, pem):\n    return C(auth=A(key, pem))\n"
    ),
    "package_import": (
        "from gpt_trader.features import brokerages\n"
        "client = brokerages.coinbase.client.CoinbaseClient\n"
    ),
    "dunder_import": "m = __import__('gpt_trader.feat' + 'ures.brokerages.factory')\n",
}


@pytest.mark.parametrize("name", sorted(EVASIONS))
@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be", "utf-8-sig", "utf-32"]
)
def test_backstop_catches_broker_building_evasions(
    tmp_path: Path, name: str, encoding: str
) -> None:
    # A wildcard exception's files are scanned, so the path rule and the scan
    # together catch a new broker builder anywhere under src/gpt_trader.
    path = "src/gpt_trader/monitoring/runner.py"
    assert tt.trading_paths([path]) == []
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_bytes(EVASIONS[name].encode(encoding))
    assert tt.unflagged_live_capable(tmp_path, [path]) == [path]


def test_backstop_reads_a_python_coding_cookie(tmp_path: Path) -> None:
    # Python runs UTF-7 source, where "+AGM-" is "c", so the raw bytes hide the name.
    data = b"# -*- coding: utf-7 -*-\nbroker = +AGM-reate_brokerage(config)\n"
    assert "create_brokerage" in data.decode("utf-7")
    (tmp_path / "tool.py").write_bytes(data)
    assert tt.unflagged_live_capable(tmp_path, ["tool.py"]) == ["tool.py"]


def test_backstop_scans_exactly_named_exceptions(tmp_path: Path) -> None:
    # An excepted file that starts building a broker fails until reclassified.
    path = "src/gpt_trader/cli/response.py"
    assert path in tt.TRADING_EXCEPT
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text("client = CoinbaseClient(auth=auth)\n")
    assert tt.unflagged_live_capable(tmp_path, [path]) == [path]


def test_backstop_ignores_resource_loading(tmp_path: Path) -> None:
    (tmp_path / "tool.py").write_text("from importlib import resources\n")
    assert tt.unflagged_live_capable(tmp_path, ["tool.py"]) == []

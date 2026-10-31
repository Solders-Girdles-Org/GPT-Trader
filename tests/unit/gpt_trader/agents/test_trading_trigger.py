from __future__ import annotations

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
        "config/environments/.env.template",
    ],
)
def test_trading_paths_hit(path: str) -> None:
    assert tt.trading_paths([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "src/gpt_trader/features/brokerages/mock/deterministic.py",
        "src/gpt_trader/features/brokerages/paper/hybrid.py",
        "src/gpt_trader/features/brokerages/coinbase/README.md",
        "src/gpt_trader/features/live_trade/telemetry/metrics.py",
        "src/gpt_trader/features/trade_ideas/store.py",
        "src/gpt_trader/backtesting/simulation/broker.py",
        "tests/unit/gpt_trader/features/brokerages/coinbase/test_auth.py",
        "config/profiles/paper.yaml",
        "deploy/gpt_trader/docker/Dockerfile",
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


def test_clean_review_must_name_current_head() -> None:
    body = "Review: https://github.com/o/r/pull/1#pullrequestreview-1 (Codex) at abcdef1: clean"
    assert tt.clean_review_for_head(body, HEAD)
    assert not tt.clean_review_for_head(body, "1234567890")
    blocking = "Review: https://x (Codex) at abcdef1: blocking"
    assert not tt.clean_review_for_head(blocking, HEAD)


def test_routine_pr_without_trading_paths_is_info() -> None:
    result = tt.assess(["docs/STATUS.md"], "Reserved: none", HEAD)
    assert result.severity == "info"


def test_missing_reserved_line_warns_on_routine_pr() -> None:
    result = tt.assess(["uv.lock"], "bump deps", HEAD)
    assert result.severity == "warning"


TRADING = ["src/gpt_trader/features/live_trade/execution/order_submission.py"]


def test_trading_paths_without_reserved_line_block() -> None:
    result = tt.assess(TRADING, "no lines", HEAD)
    assert result.severity == "blocker"
    assert result.paths == tuple(TRADING)


def test_trading_paths_with_unconfirmed_none_block() -> None:
    assert tt.assess(TRADING, "Reserved: none", HEAD).severity == "blocker"


def test_trading_paths_with_none_and_clean_review_pass() -> None:
    body = "Reserved: none\nReview: https://x (Claude) at abcdef1: clean; no blocking findings"
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

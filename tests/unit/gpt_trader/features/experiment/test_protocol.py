"""The committed held-out protocol reads results exactly as the decision record predeclares.

Authority: docs/decisions/held-out-benchmark-before-reasoning-proposer.md,
"Held-out protocol and predeclared reading". The tables below are copied from
that record; if they disagree with the code, the record wins.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product

import pytest

from gpt_trader.features.experiment import protocol
from gpt_trader.features.experiment.engine import run_experiment
from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.protocol import Action, Outcome, Verdict
from gpt_trader.features.experiment.provenance import build_held_out_input

Out, V, A = Outcome, Verdict, Action


def test_window_symbols_rules_and_costs_match_the_record():
    assert protocol.SYMBOLS == tuple(
        f"{coin}-USD" for coin in ("BTC", "ETH", "LTC", "SOL", "XRP", "LINK", "AVAX", "DOT")
    )
    assert protocol.WARMUP_START == datetime(2026, 9, 3, tzinfo=UTC)
    assert protocol.SCORED_FROM == datetime(2026, 9, 6, tzinfo=UTC)
    assert protocol.CUTOFF == datetime(2026, 10, 11, tzinfo=UTC)
    assert (protocol.SCORED_FROM - protocol.WARMUP_START) / timedelta(hours=1) == 72
    assert (protocol.CUTOFF - protocol.SCORED_FROM) / timedelta(hours=1) == 840
    assert protocol.RULES == ("baseline", "mean-reversion")
    assert protocol.MIN_SCORED_SYMBOLS == 7 and protocol.MIN_ROUND_TRIPS == 30
    base, stress = protocol.case_settings("base"), protocol.case_settings("stress")
    assert (base.fee_bps, base.slippage_bps) == (Decimal(10), Decimal(5))
    assert (stress.fee_bps, stress.slippage_bps) == (Decimal(60), Decimal(10))
    for settings in (base, stress):
        assert settings.initial_cash == Decimal(1000)
        assert settings.risk_fraction == Decimal("0.01")
        assert settings.exposure_fraction == Decimal("0.20")
        assert settings.drawdown_fraction == Decimal("0.10")
    exact = protocol.case_settings(fee_bps=Decimal("40"))
    assert (exact.fee_bps, exact.slippage_bps) == (Decimal(40), Decimal(5))
    with pytest.raises(ValueError):
        protocol.case_settings("cheap")


@pytest.mark.parametrize(
    ("scored", "trips", "net", "hold", "expected"),
    [
        (6, 100, 50, 0, Out.INCONCLUSIVE),
        (8, 29, 50, 0, Out.INCONCLUSIVE),
        (7, 30, 50, 10, Out.PASS),
        (8, 30, Decimal("0.01"), 0, Out.PASS),
        (8, 30, 10, 10, Out.POSITIVE_TRAILS_HOLD),
        (8, 30, 10, 20, Out.POSITIVE_TRAILS_HOLD),
        (8, 30, 0, -5, Out.LOSES_LESS_THAN_HOLD),
        (8, 30, -1, -5, Out.LOSES_LESS_THAN_HOLD),
        (8, 30, 0, 0, Out.FAIL),
        (8, 30, -5, -5, Out.FAIL),
        (8, 30, -5, 3, Out.FAIL),
    ],
)
def test_outcome_table(scored, trips, net, hold, expected):
    outcome = protocol.case_outcome(
        scored_symbols=scored, round_trips=trips, net_pnl=Decimal(net), hold_pnl=Decimal(hold)
    )
    assert outcome is expected


def test_verdict_order():
    conclusive = [Out.PASS, Out.POSITIVE_TRAILS_HOLD, Out.LOSES_LESS_THAN_HOLD, Out.FAIL]
    for base, stress in product([Out.INCONCLUSIVE, *conclusive], repeat=2):
        verdict = protocol.rule_verdict(base, stress)
        if Out.INCONCLUSIVE in (base, stress):
            assert verdict is V.INCONCLUSIVE
        elif base is Out.PASS and stress is Out.PASS:
            assert verdict is V.SURVIVES
        elif Out.PASS in (base, stress):
            assert verdict is V.COST_DEPENDENT
        else:
            assert verdict is V.DOES_NOT_SURVIVE


# Mean reversion down the side, baseline across the top (record grid).
GRID = {
    V.SURVIVES: [A.A, A.A, A.A, A.A],
    V.COST_DEPENDENT: [A.A, A.B, A.B, A.B],
    V.DOES_NOT_SURVIVE: [A.A, A.B, A.D, A.C],
    V.INCONCLUSIVE: [A.A, A.B, A.C, A.C],
}
COLUMNS = [V.SURVIVES, V.COST_DEPENDENT, V.DOES_NOT_SURVIVE, V.INCONCLUSIVE]


@pytest.mark.parametrize(("mean_reversion", "baseline"), list(product(COLUMNS, repeat=2)))
def test_combined_reading_grid(mean_reversion, baseline):
    expected = GRID[mean_reversion][COLUMNS.index(baseline)]
    assert protocol.combined_action(mean_reversion, baseline) is expected


def test_exact_fee_run_is_one_case_and_survives_at_rjs_fee_counts_as_survives():
    assert protocol.exact_fee_verdict(Out.PASS) is V.SURVIVES
    for outcome in (Out.POSITIVE_TRAILS_HOLD, Out.LOSES_LESS_THAN_HOLD, Out.FAIL):
        assert protocol.exact_fee_verdict(outcome) is V.DOES_NOT_SURVIVE
    assert protocol.exact_fee_verdict(Out.INCONCLUSIVE) is V.INCONCLUSIVE
    reading = protocol.reading_verdict(V.COST_DEPENDENT, follow_up=V.SURVIVES)
    assert reading.verdict is V.SURVIVES
    assert reading.wording == "survives at RJ's fee"
    # Survives at RJ's fee counts as survives for action A and the forward-test bar.
    assert protocol.combined_action(V.DOES_NOT_SURVIVE, reading.verdict) is A.A
    assert protocol.forward_test_bar(reading.verdict) == (
        "mean-reversion",
        "cash",
        "hold-the-coin",
        "baseline",
    )
    assert protocol.forward_test_bar(V.DOES_NOT_SURVIVE) == (
        "mean-reversion",
        "cash",
        "hold-the-coin",
    )


@pytest.mark.parametrize(
    ("original", "follow_up", "verdict", "wording"),
    [
        (V.SURVIVES, None, V.SURVIVES, "survives costs"),
        (V.DOES_NOT_SURVIVE, None, V.DOES_NOT_SURVIVE, "does not survive"),
        (V.INCONCLUSIVE, None, V.INCONCLUSIVE, "inconclusive"),
        (V.COST_DEPENDENT, None, V.COST_DEPENDENT, "cost-dependent"),
        (V.INCONCLUSIVE, V.SURVIVES, V.SURVIVES, "survives costs"),
        (V.INCONCLUSIVE, V.DOES_NOT_SURVIVE, V.DOES_NOT_SURVIVE, "does not survive"),
        # Still inconclusive after the one follow-up: treated as not surviving,
        # reported as not shown to survive (with coverage), never "does not survive".
        (V.INCONCLUSIVE, V.INCONCLUSIVE, V.DOES_NOT_SURVIVE, "was not shown to survive"),
        (V.INCONCLUSIVE, V.COST_DEPENDENT, V.DOES_NOT_SURVIVE, "does not survive"),
        (V.COST_DEPENDENT, V.DOES_NOT_SURVIVE, V.DOES_NOT_SURVIVE, "does not survive"),
        (V.COST_DEPENDENT, V.INCONCLUSIVE, V.DOES_NOT_SURVIVE, "was not shown to survive"),
        # A follow-up RJ declines counts as that rule's one follow-up.
        (V.INCONCLUSIVE, "declined", V.DOES_NOT_SURVIVE, "was not shown to survive"),
        (V.COST_DEPENDENT, "declined", V.DOES_NOT_SURVIVE, "does not survive"),
    ],
)
def test_follow_up_reading(original, follow_up, verdict, wording):
    reading = protocol.reading_verdict(original, follow_up=follow_up)
    assert (reading.verdict, reading.wording) == (verdict, wording)
    assert reading.follow_up_used is (follow_up is not None)


@pytest.mark.parametrize("original", [V.SURVIVES, V.DOES_NOT_SURVIVE])
def test_conclusive_verdicts_get_no_follow_up(original):
    with pytest.raises(ValueError, match="follow-up"):
        protocol.reading_verdict(original, follow_up=V.SURVIVES)


@pytest.mark.parametrize(
    ("action", "verdict", "used", "expected"),
    [
        (A.A, V.INCONCLUSIVE, False, None),
        (A.A, V.COST_DEPENDENT, False, None),
        (A.B, V.COST_DEPENDENT, False, "exact-fee"),
        (A.B, V.INCONCLUSIVE, False, "extension"),
        (A.B, V.DOES_NOT_SURVIVE, False, None),
        (A.C, V.INCONCLUSIVE, False, "extension"),
        (A.C, V.DOES_NOT_SURVIVE, False, None),
        (A.B, V.COST_DEPENDENT, True, None),
        (A.C, V.INCONCLUSIVE, True, None),
        (A.D, V.DOES_NOT_SURVIVE, False, None),
    ],
)
def test_each_follow_up_is_offered_at_most_once(action, verdict, used, expected):
    assert protocol.follow_up_for(action, verdict, follow_up_used=used) == expected


def _held_out_market(symbol="BTC-USD", bars=None):
    span = (protocol.CUTOFF - protocol.WARMUP_START) / timedelta(hours=1)
    hours = bars if bars is not None else int(span)
    start = protocol.WARMUP_START
    body = {
        "candles": [
            {
                "start": str(int((start + timedelta(hours=hour)).timestamp())),
                "low": "99",
                "high": "101",
                "open": "100",
                "close": "100",
                "volume": "1",
            }
            for hour in range(hours)
        ]
    }
    fetched = protocol.CUTOFF + timedelta(minutes=5)
    return build_held_out_input(
        symbol=symbol,
        responses=[{"request": {}, "fetched_at": fetched.isoformat(), "body": body}],
        window_start=start,
        scored_from=protocol.SCORED_FROM,
        cutoff=start + timedelta(hours=hours),
        fetched_at=fetched,
    )


def test_case_input_binds_rule_and_cost_case_to_shared_market_data():
    market = _held_out_market()
    cases = {
        (rule, cost): parse_input(protocol.case_input(market, rule, cost))
        for rule in protocol.RULES
        for cost in protocol.COST_CASES
    }
    assert len({source.identity for source in cases.values()}) == 4
    source = cases[("mean-reversion", "stress")]
    assert source.benchmark == "mean-reversion"
    assert source.settings == protocol.case_settings("stress")
    assert source.scored_index == 72 and len(source.candles) == 912
    with pytest.raises(ValueError):
        protocol.case_input(market, "regime-switcher", "base")


@pytest.fixture
def short_protocol_window(monkeypatch):
    """Same rules on a 53 + 7 bar window, so scoring runs stay fast.

    The real 72 + 840 window is pinned above; these tests check the scoring
    logic, which reads the window from the module constants.
    """
    monkeypatch.setattr(protocol, "SCORED_FROM", protocol.WARMUP_START + timedelta(hours=53))
    monkeypatch.setattr(protocol, "CUTOFF", protocol.WARMUP_START + timedelta(hours=60))
    monkeypatch.setattr(protocol, "WARMUP_BARS", 53)
    monkeypatch.setattr(protocol, "SCORED_BARS", 7)


def test_scored_requires_the_full_protocol_window_and_every_bar(tmp_path, short_protocol_window):
    source = parse_input(protocol.case_input(_held_out_market(), "baseline", "base"))
    partial = run_experiment(tmp_path / "scored", source, max_bars=59)
    assert not protocol.is_scored(partial)
    complete = run_experiment(tmp_path / "scored")
    assert protocol.is_scored(complete)
    # A run that never trades is still scored.
    assert complete["account"]["fill_count"] == 0


def test_short_window_or_unlisted_symbol_is_not_scored(tmp_path, short_protocol_window):
    short = _held_out_market(bars=59)
    report = run_experiment(
        tmp_path / "short", parse_input(protocol.case_input(short, "baseline", "base"))
    )
    assert report["complete"] and not protocol.is_scored(report)
    other = _held_out_market(symbol="DOGE-USD")
    report = run_experiment(
        tmp_path / "doge", parse_input(protocol.case_input(other, "baseline", "base"))
    )
    assert report["complete"] and not protocol.is_scored(report)


def test_unfingerprinted_input_is_not_scored(tmp_path, short_protocol_window):
    payload = protocol.case_input(_held_out_market(), "baseline", "base")
    del payload["provenance"]
    report = run_experiment(tmp_path / "bare", parse_input(payload))
    assert report["complete"] and not protocol.is_scored(report)

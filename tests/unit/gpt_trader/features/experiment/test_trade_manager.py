"""AI trade manager channel through a scripted fake transport: no model, no network."""

import json
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

import pytest

from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.engine import advance, run_experiment
from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.ledger import (
    DATABASE,
    ExperimentIntegrityError,
    initial_state,
    inspect_run,
)
from gpt_trader.features.experiment.trade_manager import (
    CONTEXT_VERSION,
    RecordedTransport,
    TransportError,
    TransportReply,
    TransportUnavailableError,
)

HOLD = {"action": "hold", "reason": "nothing worth the cost"}
ENTER = {
    "action": "enter",
    "reason": "test entry",
    "size_fraction": "0.1",
    "stop": "95",
    "target": "120",
    "expires_in_hours": 48,
}


class FakeTransport:
    """Replies by decision bar from a script; unscripted bars hold."""

    model = "fake-model-1"

    def __init__(self, payload, script=None, stop_reason="end_turn"):
        self.start = aware_time(payload["candles"][0]["ts"])
        self.script = script or {}
        self.stop_reason = stop_reason
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        last = aware_time(request["context"]["bars"][-1]["ts"])
        bar = int((last - self.start) / timedelta(hours=1))
        reply = self.script.get(bar, HOLD)
        if isinstance(reply, Exception):
            raise reply
        return TransportReply(
            model=self.model,
            text=reply if isinstance(reply, str) else json.dumps(reply),
            stop_reason=self.stop_reason,
            input_tokens=1000,
            output_tokens=200,
            latency_ms=5,
            input_usd_per_million=Decimal("10"),
            output_usd_per_million=Decimal("50"),
        )


@pytest.fixture
def manager_payload(experiment_payload):
    return {**experiment_payload, "benchmark": "ai-trade-manager", "manager": {"cadence_bars": 1}}


def transport_for(payload, script=None, **kwargs):
    return FakeTransport(payload, script, **kwargs)


def replay(source, transport):
    state, events = initial_state(source), []
    for index in range(len(source.candles)):
        event = advance(source, index, state, transport)
        events.append(event)
        state = event["state"]
    return events


def test_channel_is_default_off_and_binds_its_settings(experiment_payload, manager_payload):
    assert parse_input(experiment_payload).manager is None
    assert "manager" not in parse_input(experiment_payload).payload
    with pytest.raises(ValueError, match="only to the ai-trade-manager channel"):
        parse_input({**experiment_payload, "manager": {}})
    source = parse_input(manager_payload)
    assert source.payload["manager"]["cadence_bars"] == 1
    assert source.payload["manager"]["history_bars"] == 48
    rewritten = parse_input({**manager_payload, "manager": {"cadence_bars": 1, "rulebook": "x"}})
    assert rewritten.identity != source.identity
    for bad in ({"cadence_bars": 0}, {"history_bars": "48"}, {"rulebook": ""}, {"other": 1}):
        with pytest.raises(ValueError):
            parse_input({**manager_payload, "manager": bad})


def test_request_is_versioned_and_holds_only_closed_bars(manager_payload):
    source = parse_input(manager_payload)
    transport = transport_for(manager_payload)
    events = replay(source, transport)
    request = transport.requests[55]
    assert request["context_version"] == CONTEXT_VERSION
    bars = request["context"]["bars"]
    assert len(bars) == 48
    assert bars[-1] == manager_payload["candles"][55]
    assert request["context"]["decision_time"] == manager_payload["candles"][56]["ts"]
    assert request["context"]["rulebook"]["version"] == 1
    assert request["context"]["costs"]["round_trip_bps"] == "30"
    assert events[55]["turn"]["request"] == request


def test_enter_fills_next_open_inside_the_rails(manager_payload):
    source = parse_input(manager_payload)
    events = replay(source, transport_for(manager_payload, {2: ENTER}))
    assert events[2]["decision"] == "ai-trade-manager: consider entry at next open"
    assert events[2]["proposal"]["thesis"] == "test entry"
    fill = events[3]["fills"][0]
    price = Decimal(100) * Decimal("1.0005")
    assert fill["side"] == "buy" and Decimal(fill["price"]) == price
    expected = (Decimal(1000) * Decimal("0.1") / price).quantize(
        Decimal("0.00000001"), rounding=ROUND_DOWN
    )
    assert Decimal(fill["quantity"]) == expected
    assert events[3]["state"]["position"]["stop"] == "95"


def test_stay_out_and_keep_make_no_trade(manager_payload):
    source = parse_input(manager_payload)
    events = replay(source, transport_for(manager_payload, {2: ENTER}))
    assert events[1]["decision"] == "ai-trade-manager: stay out"
    assert events[1]["state"]["pending"] is None
    assert events[4]["decision"] == "ai-trade-manager: keep position"
    assert events[4]["state"]["position"] is not None
    assert sum(len(event["fills"]) for event in events[:50]) == 1
    assert "position settled: expiry" in events[50]["actions"]  # 48h expiry, bar 2 + 49h


def test_exit_settles_an_open_position_at_the_next_open(manager_payload):
    source = parse_input(manager_payload)
    exit_reply = {"action": "exit", "reason": "thesis gone"}
    events = replay(source, transport_for(manager_payload, {2: ENTER, 5: exit_reply}))
    assert events[5]["decision"] == "ai-trade-manager: exit at next open"
    fill = events[6]["fills"][0]
    assert fill["side"] == "sell"
    assert Decimal(fill["price"]) == Decimal(100) * Decimal("0.9995")
    assert "position settled: manager exit at next open" in events[6]["actions"]
    assert events[6]["state"]["position"] is None


def test_rulebook_rewrite_is_versioned_journaled_and_sent_back(manager_payload):
    source = parse_input(manager_payload)
    transport = transport_for(
        manager_payload, {3: {**HOLD, "rulebook": "Only buy after a 3% dip."}}
    )
    events = replay(source, transport)
    assert events[3]["turn"]["rulebook"] == "Only buy after a 3% dip."
    assert events[3]["turn"]["rulebook_version"] == 2
    assert events[2]["turn"]["rulebook"] is None
    assert transport.requests[4]["context"]["rulebook"] == {
        "version": 2,
        "text": "Only buy after a 3% dip.",
    }
    assert events[-1]["state"]["manager"]["rulebook_version"] == 2


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        ("not json", "not one JSON object"),
        ({**ENTER, "stop": "101"}, "stop < last close < target"),
        ({**ENTER, "expires_in_hours": 1.5}, "expires_in_hours"),
        ({"action": "exit", "reason": "flat"}, "only while holding"),
        ({**HOLD, "leverage": 5}, "unknown reply fields"),
        ({**HOLD, "rulebook": "y" * 4001}, "rulebook must be"),
        ({k: v for k, v in ENTER.items() if k != "target"}, "enter requires"),
    ],
)
def test_malformed_reply_makes_no_trade_and_records_why(manager_payload, reply, why):
    source = parse_input(manager_payload)
    events = replay(source, transport_for(manager_payload, {2: reply}))
    turn = events[2]["turn"]
    assert turn["outcome"] == "malformed" and why in turn["reason"]
    assert events[2]["decision"] == "ai-trade-manager malformed reply: no trade"
    assert events[2]["state"]["pending"] is None
    assert events[2]["state"]["manager"]["rulebook_version"] == 1
    assert not events[3]["fills"]


def test_enter_while_holding_is_malformed(manager_payload):
    source = parse_input(manager_payload)
    events = replay(source, transport_for(manager_payload, {2: ENTER, 4: ENTER}))
    assert events[4]["turn"]["outcome"] == "malformed"
    assert "only while flat" in events[4]["turn"]["reason"]


def test_failed_and_refused_replies_make_no_trade_and_record_why(manager_payload):
    source = parse_input(manager_payload)
    failed = replay(source, transport_for(manager_payload, {2: TransportError("timeout")}))
    assert failed[2]["turn"]["outcome"] == "failed"
    assert failed[2]["turn"]["reason"] == "transport failed: timeout"
    assert failed[2]["turn"]["model"] == "fake-model-1"
    assert failed[2]["turn"]["response"] is None and failed[2]["turn"]["cost_usd"] == "0"
    refused = replay(source, transport_for(manager_payload, {2: ENTER}, stop_reason="refusal"))
    assert refused[2]["turn"]["outcome"] == "refused"
    assert not any(event["fills"] for event in refused)


def test_entry_size_is_clamped_to_the_exposure_cap(manager_payload):
    source = parse_input(manager_payload)
    reply = {**ENTER, "size_fraction": "0.5", "stop": "97"}
    events = replay(source, transport_for(manager_payload, {2: reply}))
    assert events[2]["proposal"]["size_fraction"] == "0.20"
    assert "entry size clamped to the 0.20 exposure cap" in events[2]["actions"]
    price = Decimal(100) * Decimal("1.0005")
    expected = (Decimal(1000) * Decimal("0.20") / price).quantize(
        Decimal("0.00000001"), rounding=ROUND_DOWN
    )
    assert Decimal(events[3]["fills"][0]["quantity"]) == expected


def test_cadence_decides_every_nth_bar_and_on_fills(experiment_payload):
    payload = {**experiment_payload, "benchmark": "ai-trade-manager", "manager": {}}
    source = parse_input(payload)
    events = replay(source, transport_for(payload, {4: ENTER}))
    turned = [event["sequence"] for event in events if event["turn"]]
    assert turned[:4] == [0, 4, 5, 8]  # bar 5 is the entry fill
    assert events[1]["decision"] == "ai-trade-manager: between decision turns"
    assert events[6]["decision"] == "hold existing position"


def test_evidence_bundle_carries_usage_latency_and_cost(manager_payload):
    source = parse_input(manager_payload)
    turn = replay(source, transport_for(manager_payload))[0]["turn"]
    assert turn["model"] == "fake-model-1"
    assert json.loads(turn["response"]["text"]) == HOLD
    assert turn["usage"] == {"input_tokens": 1000, "output_tokens": 200}
    assert turn["latency_ms"] == 5
    assert Decimal(turn["cost_usd"]) == Decimal("0.02")
    assert turn["rulebook_version"] == 1


def test_pause_resume_equals_uninterrupted_and_never_re_asks(manager_payload, tmp_path):
    source = parse_input(manager_payload)
    script = {2: ENTER, 5: {**HOLD, "rulebook": "new"}, 9: {"action": "exit", "reason": "done"}}
    whole = transport_for(manager_payload, script)
    run_experiment(tmp_path / "whole", source, transport=whole)
    paused = transport_for(manager_payload, script)
    run_experiment(tmp_path / "paused", source, max_bars=7, transport=paused)
    while not run_experiment(tmp_path / "paused", max_bars=7, transport=paused)["complete"]:
        pass
    first, second = inspect_run(tmp_path / "whole"), inspect_run(tmp_path / "paused")
    assert first == second
    assert len(whole.requests) == len(paused.requests) == len(source.candles)
    assert first["manager"]["turns"] == len(source.candles)
    assert first["manager"]["rulebook_version"] == 2
    assert Decimal(first["manager"]["cost_usd"]) == Decimal("0.02") * len(source.candles)
    assert first["account"]["fill_count"] == 2
    run_experiment(tmp_path / "whole", transport=whole)
    assert len(whole.requests) == len(source.candles)


def test_a_due_turn_without_transport_commits_nothing(manager_payload, tmp_path):
    source = parse_input(manager_payload)
    with pytest.raises(TransportUnavailableError, match="no model transport"):
        run_experiment(tmp_path / "run", source)
    assert (tmp_path / "run" / DATABASE).exists()
    assert inspect_run(tmp_path / "run")["processed_bars"] == 0


def test_replay_refuses_a_request_that_differs_from_the_journal(manager_payload):
    source = parse_input(manager_payload)
    turn = replay(source, transport_for(manager_payload))[0]["turn"]
    recorded = RecordedTransport(turn)
    assert recorded.decide(turn["request"]).text == turn["response"]["text"]
    changed = json.loads(json.dumps(turn["request"]))
    changed["context"]["rulebook"]["text"] = "edited"
    with pytest.raises(ExperimentIntegrityError, match="differs from the journal"):
        recorded.decide(changed)
    with pytest.raises(ExperimentIntegrityError, match="lacks"):
        RecordedTransport(None).decide(turn["request"])

"""The Anthropic transport and its spend ceiling, through a stubbed SDK client: no network."""

import json
import re
from decimal import Decimal

import anthropic
import httpx2
import pytest

from gpt_trader.features.experiment.engine import advance, run_experiment
from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.ledger import encode, initial_state, inspect_run
from gpt_trader.features.experiment.model_transport import (
    OVERHEAD_TOKENS,
    REPLY_SCHEMA,
    AnthropicTransport,
    SpendCeilingReached,
    SpendLedger,
    SpendLedgerError,
    TransportHalted,
    estimate_spend,
    from_environment,
    input_token_bound,
    wire_params,
    worst_case_usd,
)
from gpt_trader.features.experiment.trade_manager import TransportError, build_request

HOLD = json.dumps({"action": "hold", "reason": "nothing worth the cost"})
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def message(text=HOLD, stop_reason="end_turn", *, model="claude-fable-5-1", **usage):
    content = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if text is not None:
        content.append({"type": "text", "text": text})
    return anthropic.types.Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 3000, "output_tokens": 1200, **usage},
        }
    )


def status_error(status):
    response = httpx2.Response(status, request=REQUEST)
    return anthropic.APIStatusError(f"status {status}", response=response, body=None)


class StubMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **params):
        self.calls.append(params)
        reply = self.replies.pop(0) if self.replies else message()
        if isinstance(reply, BaseException):
            raise reply
        return reply


class StubClient:
    def __init__(self, *replies):
        self.messages = StubMessages(replies)


@pytest.fixture
def manager_input(experiment_payload):
    return parse_input(
        {**experiment_payload, "benchmark": "ai-trade-manager", "manager": {"cadence_bars": 1}}
    )


@pytest.fixture
def ledger(tmp_path):
    return SpendLedger(tmp_path / "spend.sqlite", Decimal("100"))


def first_request(source):
    return build_request(source, source.scored_index, initial_state(source))


def transport(source, ledger, *replies):
    return AnthropicTransport(source.manager, ledger, StubClient(*replies))


def test_request_is_pinned_structured_and_carries_no_fallback(manager_input, ledger):
    sender = transport(manager_input, ledger)
    request = first_request(manager_input)
    sender.decide(request)
    (params,) = sender.client.messages.calls
    assert params == wire_params(request, manager_input.manager)
    assert params["model"] == "claude-fable-5-1"
    assert params["max_tokens"] == 8000
    assert params["system"] == request["instructions"]
    assert params["messages"] == [{"role": "user", "content": encode(request["context"])}]
    assert params["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": REPLY_SCHEMA},
    }
    assert params["service_tier"] == "standard_only"
    assert params["inference_geo"] == "global"
    assert not {"thinking", "temperature", "fallbacks", "betas", "tools", "speed"} & set(params)


def test_success_reports_model_usage_latency_pricing_and_settles_cost(manager_input, ledger):
    sender = transport(manager_input, ledger, message(model="claude-fable-5-1-served"))
    reply = sender.decide(first_request(manager_input))
    assert reply.model == "claude-fable-5-1-served"
    assert reply.text == HOLD  # the empty thinking block is not part of the reply
    assert reply.stop_reason == "end_turn"
    assert (reply.input_tokens, reply.output_tokens) == (3000, 1200)
    assert type(reply.latency_ms) is int and reply.latency_ms >= 0
    assert (reply.input_usd_per_million, reply.output_usd_per_million) == (10, 50)
    assert reply.cost_usd == Decimal("0.09")  # 3000 * $10/M + 1200 * $50/M
    (call,) = ledger.calls()
    assert call["status"] == "replied" and Decimal(call["cost_usd"]) == Decimal("0.09")
    assert Decimal(call["reserved_usd"]) == worst_case_usd(
        wire_params(first_request(manager_input), manager_input.manager)
    )
    assert ledger.spent_usd() == Decimal("0.09")


def test_cache_tokens_are_counted_and_writes_charged_in_full(manager_input, ledger):
    reply = transport(
        manager_input,
        ledger,
        message(cache_creation_input_tokens=1000, cache_read_input_tokens=500),
    ).decide(first_request(manager_input))
    assert reply.input_tokens == 4500
    # Base cost of 4500 input tokens, plus the 0.25 cache-write surcharge on 1000.
    assert ledger.spent_usd() == reply.cost_usd + Decimal("0.0025")


def test_worst_case_bounds_input_by_bytes_and_output_by_max_tokens(manager_input):
    params = wire_params(first_request(manager_input), manager_input.manager)
    sent = params["system"] + params["messages"][0]["content"] + encode(REPLY_SCHEMA)
    assert input_token_bound(params) > len(sent.encode()) + OVERHEAD_TOKENS - 64
    expected = (input_token_bound(params) * Decimal("12.5") + 8000 * 50) / 1_000_000
    assert worst_case_usd(params) == expected


def test_refusal_is_recorded_as_a_refused_turn_and_still_charged(manager_input, ledger):
    sender = transport(manager_input, ledger, message(text=None, stop_reason="refusal"))
    event = advance(manager_input, 0, initial_state(manager_input), sender)
    assert event["turn"]["outcome"] == "refused"
    assert event["turn"]["response"] == {"text": "", "stop_reason": "refusal"}
    assert event["state"]["pending"] is None
    assert ledger.spent_usd() == Decimal("0.09")


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        (message(text=None), "not one JSON object"),
        (message(text='{"action": "hold", "reas'), "not one JSON object"),
        (message(text='{"action": "hold", "reas', stop_reason="max_tokens"), "not one JSON"),
        (message(text=json.dumps({"action": "sell", "reason": "x"})), "hold, enter or exit"),
    ],
)
def test_malformed_content_makes_a_malformed_no_trade_turn(manager_input, ledger, reply, why):
    event = advance(
        manager_input, 0, initial_state(manager_input), transport(manager_input, ledger, reply)
    )
    assert event["turn"]["outcome"] == "malformed" and why in event["turn"]["reason"]
    assert event["state"]["pending"] is None


@pytest.mark.parametrize(
    ("failure", "reason", "charged"),
    [
        (anthropic.APITimeoutError(request=REQUEST), "timed out after 600s", "reserved"),
        (anthropic.APIConnectionError(request=REQUEST), "connection error", "reserved"),
        (status_error(500), "API error (HTTP 500)", "reserved"),
        (status_error(529), "API error (HTTP 529)", "nothing"),
        (status_error(429), "API error (HTTP 429)", "nothing"),
        (status_error(408), "API error (HTTP 408)", "nothing"),
    ],
)
def test_api_and_network_failures_are_no_trade_turns(
    manager_input, ledger, failure, reason, charged
):
    sender = transport(manager_input, ledger, failure)
    with pytest.raises(TransportError, match=re.escape(reason)):
        sender.decide(first_request(manager_input))
    event = advance(
        manager_input, 0, initial_state(manager_input), transport(manager_input, ledger, failure)
    )
    assert event["turn"]["outcome"] == "failed" and reason in event["turn"]["reason"]
    assert event["turn"]["cost_usd"] == "0"
    held = Decimal(ledger.calls()[0]["reserved_usd"])
    assert ledger.spent_usd() == (2 * held if charged == "reserved" else 0)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
def test_request_or_credential_rejections_stop_the_run(manager_input, ledger, tmp_path, status):
    root = tmp_path / "run"
    sender = transport(manager_input, ledger, message(), status_error(status))
    with pytest.raises(TransportHalted, match=f"HTTP {status}"):
        run_experiment(root, manager_input, transport=sender)
    assert inspect_run(root)["processed_bars"] == 1  # the failing bar was not committed
    assert ledger.calls()[-1]["status"] == f"http {status}"
    assert ledger.spent_usd() == Decimal("0.09")


def test_unknown_model_price_refuses_before_any_call(experiment_payload, ledger):
    source = parse_input(
        {**experiment_payload, "benchmark": "ai-trade-manager", "manager": {"model": "other"}}
    )
    with pytest.raises(TransportHalted, match="No recorded price for model other"):
        AnthropicTransport(source.manager, ledger, StubClient())


def test_spend_ceiling_stops_the_run_loudly_before_the_call(manager_input, tmp_path):
    params = wire_params(first_request(manager_input), manager_input.manager)
    # Room for one worst-case reservation, not two.
    ledger = SpendLedger(tmp_path / "spend.sqlite", worst_case_usd(params) + Decimal("0.05"))
    sender = transport(manager_input, ledger)
    root = tmp_path / "run"
    with pytest.raises(SpendCeilingReached, match="it was not sent"):
        run_experiment(root, manager_input, transport=sender)
    assert len(sender.client.messages.calls) == 1
    assert inspect_run(root)["processed_bars"] == 1
    assert ledger.spent_usd() == Decimal("0.09")


def test_calls_whose_bar_never_committed_still_count(manager_input, ledger, tmp_path):
    class CrashBeforeCommit:
        def __init__(self, inner):
            self.inner = inner
            self.model = inner.model

        def decide(self, request):
            self.inner.decide(request)
            raise RuntimeError("process died before the journal commit")

    root = tmp_path / "run"
    with pytest.raises(RuntimeError):
        run_experiment(
            root,
            manager_input,
            max_bars=1,
            transport=CrashBeforeCommit(transport(manager_input, ledger)),
        )
    assert inspect_run(root)["processed_bars"] == 0
    run_experiment(root, max_bars=1, transport=transport(manager_input, ledger))
    assert inspect_run(root)["processed_bars"] == 1
    assert [call["status"] for call in ledger.calls()] == ["replied", "replied"]
    assert ledger.spent_usd() == Decimal("0.18")


def test_a_call_that_never_settles_keeps_its_full_reservation(manager_input, ledger):
    sender = transport(manager_input, ledger, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        sender.decide(first_request(manager_input))
    (call,) = ledger.calls()
    assert call["status"] == "reserved" and call["cost_usd"] is None
    assert ledger.spent_usd() == Decimal(call["reserved_usd"])


def test_ledger_is_shared_by_path_and_bound_to_its_cap(manager_input, ledger, tmp_path):
    transport(manager_input, ledger).decide(first_request(manager_input))
    again = SpendLedger(tmp_path / "spend.sqlite", Decimal("100"))
    assert again.spent_usd() == Decimal("0.09")
    with pytest.raises(SpendLedgerError, match=r"bound to a \$100 cap"):
        SpendLedger(tmp_path / "spend.sqlite", Decimal("200"))
    for bad in (Decimal(0), Decimal(-1), Decimal("NaN")):
        with pytest.raises(SpendLedgerError):
            SpendLedger(tmp_path / "other.sqlite", bad)


def test_an_unusable_ledger_stops_the_run(manager_input, ledger):
    ledger.path.mkdir()
    with pytest.raises(TransportHalted, match="is unusable"):
        transport(manager_input, ledger).decide(first_request(manager_input))


def test_environment_credential_and_no_sdk_retries(manager_input, ledger):
    with pytest.raises(TransportHalted, match="ANTHROPIC_API_KEY is not set"):
        from_environment(manager_input.manager, ledger, environ={})
    key = {"ANTHROPIC_API_KEY": "k-test"}
    for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS"):
        with pytest.raises(TransportHalted, match=f"{name} is set"):
            from_environment(manager_input.manager, ledger, environ={**key, name: "x"})
    real = from_environment(manager_input.manager, ledger, environ=key)
    assert real.client.api_key == "k-test"
    assert str(real.client.base_url).rstrip("/") == "https://api.anthropic.com"
    assert real.client.max_retries == 0
    assert real.model == "claude-fable-5-1"
    assert ledger.calls() == []


def test_offline_estimate_measures_requests_without_any_call(manager_input, experiment_input):
    estimate = estimate_spend(manager_input)
    assert estimate["turns_without_fills"] == len(manager_input.candles)
    assert estimate["input_token_bound_max"] >= estimate["input_token_bound_mean"] > OVERHEAD_TOKENS
    assert Decimal(estimate["worst_case_usd"]) > len(manager_input.candles) * Decimal("0.4")
    with pytest.raises(ValueError, match="only to the AI trade manager"):
        estimate_spend(experiment_input)


def test_pinned_request_settings_are_bound_and_validated(experiment_payload, manager_input):
    assert manager_input.payload["manager"]["model"] == "claude-fable-5-1"
    assert manager_input.payload["manager"]["effort"] == "high"
    assert manager_input.payload["manager"]["max_tokens"] == 8000
    payload = {**experiment_payload, "benchmark": "ai-trade-manager"}
    lower = parse_input({**payload, "manager": {"effort": "low"}})
    assert lower.identity != parse_input(payload).identity
    for bad in ({"effort": "huge"}, {"max_tokens": 0}, {"max_tokens": 20001}, {"model": ""}):
        with pytest.raises(ValueError):
            parse_input({**payload, "manager": bad})


def test_ledger_file_is_created_only_by_the_first_reservation(manager_input, ledger, tmp_path):
    assert not ledger.path.exists() and ledger.spent_usd() == 0
    tiny = SpendLedger(tmp_path / "tiny.sqlite", Decimal("0.01"))
    with pytest.raises(SpendCeilingReached, match="above the"):
        transport(manager_input, tiny).decide(first_request(manager_input))
    assert not tiny.path.exists()
    transport(manager_input, ledger).decide(first_request(manager_input))
    assert ledger.path.is_file() and len(ledger.calls()) == 1


def test_a_file_that_is_not_a_spend_ledger_is_refused_and_left_unchanged(
    experiment_input, tmp_path
):
    root = tmp_path / "baseline"
    run_experiment(root, experiment_input, max_bars=1)
    journal = next(root.iterdir())
    before = journal.read_bytes()
    with pytest.raises(SpendLedgerError, match="is not a spend ledger"):
        SpendLedger(journal, Decimal("5"))
    text = tmp_path / "notes.txt"
    text.write_text("not a database")
    with pytest.raises(SpendLedgerError, match="is not a spend ledger"):
        SpendLedger(text, Decimal("5"))
    assert journal.read_bytes() == before and text.read_text() == "not a database"


@pytest.mark.parametrize(
    "usage",
    [{"service_tier": "priority"}, {"speed": "fast"}, {"inference_geo": "us"}],
)
def test_usage_outside_the_price_table_stops_the_run(manager_input, ledger, usage):
    with pytest.raises(TransportHalted, match="price table does not cover"):
        transport(manager_input, ledger, message(**usage)).decide(first_request(manager_input))
    assert ledger.calls()[0]["status"] == "replied"
    assert ledger.spent_usd() == Decimal("0.09")


def test_standard_usage_labels_are_accepted(manager_input, ledger):
    reply = message(service_tier="standard", speed="standard", inference_geo="global")
    transport(manager_input, ledger, reply).decide(first_request(manager_input))
    assert ledger.spent_usd() == Decimal("0.09")


def test_a_cost_above_its_reservation_stops_the_run(manager_input, ledger):
    sender = transport(manager_input, ledger, message(output_tokens=20000))
    with pytest.raises(TransportHalted, match="exceeded the call's"):
        sender.decide(first_request(manager_input))
    assert ledger.spent_usd() == Decimal("1.03")  # 3000 * $10/M + 20000 * $50/M


def test_hour_cache_writes_are_charged_at_twice_base_input(manager_input, ledger):
    split = {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 1000}
    reply = message(cache_creation_input_tokens=1000, cache_creation=split)
    transport(manager_input, ledger, reply).decide(first_request(manager_input))
    assert ledger.spent_usd() == Decimal("0.09") + Decimal("0.02")  # 1000 tokens at $20/M

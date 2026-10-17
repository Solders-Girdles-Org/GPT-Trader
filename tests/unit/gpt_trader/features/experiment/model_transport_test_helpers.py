"""A stubbed Anthropic SDK client for transport tests: real SDK types, no network."""

import json

import anthropic
import httpx2

from gpt_trader.features.experiment.ledger import initial_state
from gpt_trader.features.experiment.model_transport import AnthropicTransport
from gpt_trader.features.experiment.trade_manager import build_request

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


def first_request(source):
    return build_request(source, source.scored_index, initial_state(source))


def transport(source, ledger, *replies):
    return AnthropicTransport(source.manager, ledger, StubClient(*replies))

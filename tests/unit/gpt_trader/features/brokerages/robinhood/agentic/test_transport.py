from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gpt_trader.features.brokerages.robinhood.agentic import transport
from gpt_trader.features.brokerages.robinhood.agentic.errors import RobinhoodAgenticViolation


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.pages = [
            SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name=name,
                        inputSchema={"type": "object"},
                        outputSchema={"type": "object"},
                    )
                    for name in (
                        "get_accounts",
                        "get_portfolio",
                        "review_equity_order",
                        "review_option_order",
                    )
                ],
                nextCursor=None,
            )
        ]
        self.payload: dict[str, Any] = {}

    async def list_tools(self, cursor: str | None) -> Any:
        assert cursor is None
        return self.pages[0]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return SimpleNamespace(isError=False, structuredContent=self.payload)


@pytest.mark.asyncio
async def test_gateway_attests_before_four_literal_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    closed = False

    async def close() -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr(
        transport,
        "attest_tool_inventory",
        lambda _tools: (
            {
                name: {"type": "object"}
                for name in (
                    "get_accounts",
                    "get_portfolio",
                    "review_equity_order",
                    "review_option_order",
                )
            },
            "schema-fingerprint",
            "inventory-fingerprint",
        ),
    )
    gateway = transport.McpRobinhoodAgenticGateway(session=session, close_transport=close)
    await gateway._attest()
    await gateway.get_accounts()
    await gateway.get_portfolio("A1")
    await gateway.review_equity_order({"account_number": "A1"})
    await gateway.review_option_order({"account_number": "A1"})
    await gateway.close()
    await gateway.close()

    assert [name for name, _ in session.calls] == [
        "get_accounts",
        "get_portfolio",
        "review_equity_order",
        "review_option_order",
    ]
    assert closed
    assert not hasattr(gateway, "call_tool")
    assert not hasattr(gateway, "session")


@pytest.mark.asyncio
async def test_gateway_rejects_call_before_attestation() -> None:
    session = FakeSession()

    async def close() -> None:
        return None

    gateway = transport.McpRobinhoodAgenticGateway(session=session, close_transport=close)
    with pytest.raises(RobinhoodAgenticViolation, match="not accepted"):
        await gateway.get_accounts()
    assert session.calls == []


@pytest.mark.asyncio
async def test_gateway_rejects_malformed_or_error_result() -> None:
    session = FakeSession()

    async def close() -> None:
        return None

    gateway = transport.McpRobinhoodAgenticGateway(session=session, close_transport=close)
    gateway._input_schemas = {"get_accounts": {"type": "object"}}
    gateway._output_schemas = {"get_accounts": {"type": "object", "required": ["data"]}}
    with pytest.raises(RobinhoodAgenticViolation, match="schema validation"):
        await gateway.get_accounts()
    assert session.calls == [("get_accounts", {})]


@pytest.mark.asyncio
async def test_gateway_rejects_argument_schema_drift_before_dispatch() -> None:
    session = FakeSession()

    async def close() -> None:
        return None

    gateway = transport.McpRobinhoodAgenticGateway(session=session, close_transport=close)
    gateway._input_schemas = {
        "get_portfolio": {
            "type": "object",
            "properties": {"account_number": {"type": "string"}},
            "required": ["account_number"],
            "additionalProperties": False,
        }
    }
    gateway._output_schemas = {"get_portfolio": {"type": "object"}}
    with pytest.raises(RobinhoodAgenticViolation, match="arguments failed"):
        await gateway._invoke("get_portfolio", {"account_number": "A1", "method": "POST"})
    assert session.calls == []

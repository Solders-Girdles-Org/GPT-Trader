from __future__ import annotations

import pytest

from gpt_trader.features.brokerages.robinhood.agentic import schemas
from gpt_trader.features.brokerages.robinhood.agentic.errors import RobinhoodAgenticViolation


def _tool(name: str, marker: str = "stable") -> dict[str, object]:
    return {
        "name": name,
        "inputSchema": {"type": "object", "description": marker},
        "outputSchema": {"type": "object"},
    }


def test_schema_attestation_accepts_exact_four_and_extra_server_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [_tool(name) for name in sorted(schemas.ACCEPTED_TOOL_NAMES)]
    expected = {
        tool["name"]: schemas.schema_fingerprint(
            name=str(tool["name"]),
            input_schema=tool["inputSchema"],
            output_schema=tool["outputSchema"],
        )
        for tool in tools
    }
    monkeypatch.setattr(schemas, "ACCEPTED_TOOL_SCHEMA_FINGERPRINTS", expected)
    tools.append(_tool("place_equity_order"))

    output_schemas, schema_fingerprint, inventory_fingerprint = schemas.attest_tool_inventory(tools)

    assert set(output_schemas) == schemas.ACCEPTED_TOOL_NAMES
    assert len(schema_fingerprint) == 64
    assert len(inventory_fingerprint) == 64


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "schema-drift"])
def test_schema_attestation_fails_closed(mutation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = [_tool(name) for name in sorted(schemas.ACCEPTED_TOOL_NAMES)]
    expected = {
        tool["name"]: schemas.schema_fingerprint(
            name=str(tool["name"]),
            input_schema=tool["inputSchema"],
            output_schema=tool["outputSchema"],
        )
        for tool in tools
    }
    monkeypatch.setattr(schemas, "ACCEPTED_TOOL_SCHEMA_FINGERPRINTS", expected)
    if mutation == "missing":
        tools.pop()
    elif mutation == "duplicate":
        tools.append(dict(tools[0]))
    else:
        tools[0]["inputSchema"] = {"type": "object", "description": "changed"}

    with pytest.raises(RobinhoodAgenticViolation):
        schemas.attest_tool_inventory(tools)


def test_live_captured_fingerprints_cover_only_accepted_tools() -> None:
    assert schemas.ACCEPTED_TOOL_NAMES == {
        "get_accounts",
        "get_portfolio",
        "review_equity_order",
        "review_option_order",
    }
    assert all(len(value) == 64 for value in schemas.ACCEPTED_TOOL_SCHEMA_FINGERPRINTS.values())

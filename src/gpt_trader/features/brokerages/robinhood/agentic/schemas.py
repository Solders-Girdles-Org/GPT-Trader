"""Exact live-captured schema attestations for the accepted Agentic tools."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from gpt_trader.features.brokerages.robinhood.agentic.errors import (
    RobinhoodAgenticViolation,
)

ROBINHOOD_AGENTIC_MCP_URL = "https://agent.robinhood.com/mcp/trading"
ACCEPTED_TOOL_SCHEMA_FINGERPRINTS: Mapping[str, str] = {
    "get_accounts": "1562520a8bddc22bbf7a1911463fc15a4e37747ce3555aee0ad58958a7dbead8",
    "get_portfolio": "a54db6f79498e2f79c18e0dd7c5c88a4ee709b05ac5738828bac5f2d268e8f8d",
    "review_equity_order": "5444b0a3713c5209e35f299cfae4b18cb1523c68f2d8246be351a273865098d1",
    "review_option_order": "d2f16eaddc2118bafcb5fdd81559aec50e70a3bc82e4d4384e519563f7e61b46",
}
ACCEPTED_TOOL_NAMES = frozenset(ACCEPTED_TOOL_SCHEMA_FINGERPRINTS)


def canonical_json(value: Any) -> str:
    """Return deterministic compact JSON for hashing and immutable evidence."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise RobinhoodAgenticViolation("Robinhood Agentic evidence is not valid JSON") from exc


def schema_fingerprint(*, name: str, input_schema: Any, output_schema: Any) -> str:
    payload = canonical_json(
        {"inputSchema": input_schema, "name": name, "outputSchema": output_schema}
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def attest_tool_inventory(
    tools: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], str, str]:
    """Attest four accepted schemas while retaining no route to other tools."""
    seen: dict[str, Mapping[str, Any]] = {}
    for tool in tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise RobinhoodAgenticViolation("Robinhood Agentic tool name is malformed")
        if name in seen:
            raise RobinhoodAgenticViolation("Robinhood Agentic tool inventory contains duplicates")
        seen[name] = tool

    if not ACCEPTED_TOOL_NAMES.issubset(seen):
        missing = sorted(ACCEPTED_TOOL_NAMES.difference(seen))
        raise RobinhoodAgenticViolation(
            f"Robinhood Agentic accepted tool is missing: {', '.join(missing)}"
        )

    output_schemas: dict[str, dict[str, Any]] = {}
    for name, expected in ACCEPTED_TOOL_SCHEMA_FINGERPRINTS.items():
        tool = seen[name]
        input_schema = tool.get("inputSchema")
        output_schema = tool.get("outputSchema")
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            raise RobinhoodAgenticViolation(f"Robinhood Agentic schema is missing for {name}")
        observed = schema_fingerprint(
            name=name,
            input_schema=input_schema,
            output_schema=output_schema,
        )
        if observed != expected:
            raise RobinhoodAgenticViolation(f"Robinhood Agentic schema drift detected for {name}")
        output_schemas[name] = dict(output_schema)

    schema_set = hashlib.sha256(
        canonical_json(dict(ACCEPTED_TOOL_SCHEMA_FINGERPRINTS)).encode()
    ).hexdigest()
    tool_names = hashlib.sha256(canonical_json(sorted(seen)).encode()).hexdigest()
    return output_schemas, schema_set, tool_names


__all__ = [
    "ACCEPTED_TOOL_NAMES",
    "ACCEPTED_TOOL_SCHEMA_FINGERPRINTS",
    "ROBINHOOD_AGENTIC_MCP_URL",
    "attest_tool_inventory",
    "canonical_json",
    "schema_fingerprint",
]

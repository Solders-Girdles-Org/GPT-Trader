from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from gpt_trader.features.brokerages.robinhood.agentic.account_access import (
    RobinhoodAgenticAccountReader,
)
from gpt_trader.features.brokerages.robinhood.agentic.read_review_access import (
    RobinhoodAgenticReadReviewAccess,
)
from gpt_trader.features.brokerages.robinhood.agentic.review_access import (
    RobinhoodAgenticEquityReviewProvider,
    RobinhoodAgenticOptionReviewProvider,
)


@pytest.mark.asyncio
async def test_facade_exposes_no_generic_mcp_escape_and_closes(gateway: Any) -> None:
    def clock() -> datetime:
        return datetime(2026, 7, 13, tzinfo=UTC)

    reader = RobinhoodAgenticAccountReader(
        gateway=gateway,
        expected_account_number="RH-EXPECTED",
        clock=clock,
    )
    access = RobinhoodAgenticReadReviewAccess(
        gateway=gateway,
        reader=reader,
        equity_reviews=RobinhoodAgenticEquityReviewProvider(
            gateway=gateway,
            account_reader=reader,
            expected_account_number="RH-EXPECTED",
            clock=clock,
        ),
        option_reviews=RobinhoodAgenticOptionReviewProvider(
            gateway=gateway,
            account_reader=reader,
            expected_account_number="RH-EXPECTED",
            clock=clock,
        ),
    )

    assert not hasattr(access, "call_tool")
    assert not hasattr(access, "session")
    assert not hasattr(access, "transport")
    async with access:
        observation = await access.read_account()
        assert observation.identity.account_id == "RH-EXPECTED"
    assert gateway.closed

"""Command-scoped Robinhood Agentic read/review facade."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType

from gpt_trader.app.config import BotConfig
from gpt_trader.features.brokerages.accounts import AccountObservation, PreviewRequest
from gpt_trader.features.brokerages.robinhood.agentic.account_access import (
    RobinhoodAgenticAccountReader,
)
from gpt_trader.features.brokerages.robinhood.agentic.models import (
    RobinhoodAgenticEquityReviewEvidence,
    RobinhoodAgenticOptionReviewEvidence,
    RobinhoodAgenticOptionReviewRequest,
)
from gpt_trader.features.brokerages.robinhood.agentic.review_access import (
    RobinhoodAgenticEquityReviewProvider,
    RobinhoodAgenticOptionReviewProvider,
)
from gpt_trader.features.brokerages.robinhood.agentic.transport import (
    McpRobinhoodAgenticGateway,
    RobinhoodAgenticGatewayProtocol,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RobinhoodAgenticReadReviewAccess:
    """Own one attested gateway without exposing a generic MCP call surface."""

    def __init__(
        self,
        *,
        gateway: RobinhoodAgenticGatewayProtocol,
        reader: RobinhoodAgenticAccountReader,
        equity_reviews: RobinhoodAgenticEquityReviewProvider,
        option_reviews: RobinhoodAgenticOptionReviewProvider,
    ) -> None:
        self._gateway = gateway
        self._reader = reader
        self._equity_reviews = equity_reviews
        self._option_reviews = option_reviews

    @classmethod
    async def from_config(
        cls,
        config: BotConfig,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> RobinhoodAgenticReadReviewAccess:
        expected = config.robinhood_agentic_expected_account_number
        if not expected:
            raise RuntimeError("ROBINHOOD_AGENTIC_EXPECTED_ACCOUNT_NUMBER is required")
        gateway = await McpRobinhoodAgenticGateway.connect()
        try:
            reader = RobinhoodAgenticAccountReader(
                gateway=gateway,
                expected_account_number=expected,
                clock=clock,
            )
            return cls(
                gateway=gateway,
                reader=reader,
                equity_reviews=RobinhoodAgenticEquityReviewProvider(
                    gateway=gateway,
                    account_reader=reader,
                    expected_account_number=expected,
                    clock=clock,
                ),
                option_reviews=RobinhoodAgenticOptionReviewProvider(
                    gateway=gateway,
                    account_reader=reader,
                    expected_account_number=expected,
                    clock=clock,
                ),
            )
        except Exception:
            await gateway.close()
            raise

    async def read_account(self) -> AccountObservation:
        return await self._reader.read_account()

    async def review_equity_order(
        self, request: PreviewRequest
    ) -> RobinhoodAgenticEquityReviewEvidence:
        return await self._equity_reviews.review(request)

    async def review_option_order(
        self, request: RobinhoodAgenticOptionReviewRequest
    ) -> RobinhoodAgenticOptionReviewEvidence:
        return await self._option_reviews.review(request)

    async def close(self) -> None:
        await self._gateway.close()

    async def __aenter__(self) -> RobinhoodAgenticReadReviewAccess:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self.close()


__all__ = ["RobinhoodAgenticReadReviewAccess"]

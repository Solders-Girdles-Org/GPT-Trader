"""Basic tests for backtesting simulation components."""

from datetime import UTC, datetime
from decimal import Decimal

from gpt_trader.backtesting.simulation import FeeCalculator, FundingPnLTracker, SimulatedBroker
from gpt_trader.backtesting.types import FeeTier
from gpt_trader.core import MarketType, Product


class TestFeeCalculator:
    """Test fee calculation."""

    def test_tier_2_fees(self):
        """Test TIER_2 fee rates (0.25% maker, 0.40% taker)."""
        calc = FeeCalculator(tier=FeeTier.TIER_2)  # naming: allow

        # Maker fee
        maker_fee = calc.calculate(  # naming: allow
            notional_usd=Decimal("10000"),
            is_maker=True,
        )
        assert maker_fee == Decimal("25")  # 0.25% of 10000

        # Taker fee
        taker_fee = calc.calculate(  # naming: allow
            notional_usd=Decimal("10000"),
            is_maker=False,
        )
        assert taker_fee == Decimal("40")  # 0.40% of 10000

    def test_tier_0_fees(self):
        """Test TIER_0 fee rates (0.60% maker, 0.80% taker)."""
        calc = FeeCalculator(tier=FeeTier.TIER_0)  # naming: allow

        maker_fee = calc.calculate(Decimal("1000"), is_maker=True)  # naming: allow
        assert maker_fee == Decimal("6")  # 0.60% of 1000

        taker_fee = calc.calculate(Decimal("1000"), is_maker=False)  # naming: allow
        assert taker_fee == Decimal("8")  # 0.80% of 1000


class TestFundingPnLTracker:
    """Test funding PnL tracking."""

    def test_funding_accrual(self):
        """Test basic funding accrual."""
        tracker = FundingPnLTracker(
            accrual_interval_hours=1,
            settlement_interval_hours=8,
        )

        symbol = "BTC-PERP-USDC"
        position_size = Decimal("1")  # 1 BTC long
        mark_price = Decimal("50000")
        funding_rate_8h = Decimal("0.0001")  # 0.01% per 8 hours
        current_time = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)

        # First accrual (initializes, returns 0)
        funding = tracker.accrue(symbol, position_size, mark_price, funding_rate_8h, current_time)
        assert funding == Decimal("0")

        # Second accrual after 1 hour
        current_time = datetime(2024, 1, 1, 1, 0, tzinfo=UTC)
        funding = tracker.accrue(symbol, position_size, mark_price, funding_rate_8h, current_time)

        # Expected: (1 BTC * 50000 * 0.0001) / 8 = 0.625
        expected = position_size * mark_price * funding_rate_8h / Decimal("8")
        assert abs(funding - expected) < Decimal("0.01")

    def test_funding_settlement(self):
        """Test funding settlement."""
        tracker = FundingPnLTracker()

        symbol = "ETH-PERP-USDC"
        current_time = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)

        # Accrue some funding
        tracker.accrue(symbol, Decimal("10"), Decimal("3000"), Decimal("0.0001"), current_time)

        current_time = datetime(2024, 1, 1, 1, 0, tzinfo=UTC)
        tracker.accrue(symbol, Decimal("10"), Decimal("3000"), Decimal("0.0001"), current_time)

        accrued = tracker.get_accrued(symbol)
        assert accrued > Decimal("0")

        # Settle
        current_time = datetime(2024, 1, 1, 8, 0, tzinfo=UTC)
        settled = tracker.settle(symbol, current_time)

        assert settled == accrued
        assert tracker.get_accrued(symbol) == Decimal("0")
        assert tracker.get_total_paid(symbol) == settled


class TestSimulatedBroker:
    """Test simulated broker."""

    def test_initialization(self):
        """Test broker initialization."""
        broker = SimulatedBroker(
            initial_equity_usd=Decimal("100000"),
            fee_tier=FeeTier.TIER_2,
        )

        assert broker.get_equity() == Decimal("100000")
        account_info = broker.get_account_info()
        assert account_info["cash"] == Decimal("100000")
        assert len(broker.list_positions()) == 0

    def test_product_registration(self):
        """Test product registration."""
        broker = SimulatedBroker()

        product = Product(
            symbol="BTC-PERP-USDC",
            base_asset="BTC",
            quote_asset="USDC",
            market_type=MarketType.PERPETUAL,
            min_size=Decimal("0.0001"),
            step_size=Decimal("0.0001"),
            min_notional=Decimal("10"),
            price_increment=Decimal("0.01"),
            leverage_max=10,
        )

        broker.register_product(product)

        retrieved = broker.get_product("BTC-PERP-USDC")
        assert retrieved.symbol == "BTC-PERP-USDC"
        assert retrieved.base_asset == "BTC"

    def test_connection_methods(self):
        """Test connection interface methods."""
        broker = SimulatedBroker()

        assert broker.connect() is True
        assert broker.validate_connection() is True
        assert broker.get_account_id() == "SIMULATED_ACCOUNT"

        broker.disconnect()  # Should not raise

    def test_balances(self):
        """Test balance retrieval."""
        broker = SimulatedBroker(initial_equity_usd=Decimal("50000"))

        balances = broker.list_balances()
        assert len(balances) == 1
        assert balances[0].asset == "USDC"
        assert balances[0].total == Decimal("50000")
        assert balances[0].available == Decimal("50000")

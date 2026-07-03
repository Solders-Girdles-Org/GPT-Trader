"""Tests for BotConfig environment aliases and related integrations."""

from __future__ import annotations

from decimal import Decimal

import pytest


class TestBotConfigEnvAliasing:
    """Tests for RISK_* prefixed environment variable support."""

    def test_risk_max_leverage_env_retired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # RISK_MAX_LEVERAGE fed the retired BotRiskConfig.max_leverage alias
        # (#1120 stage 3); leverage caps are runtime RiskConfig enforcement.
        monkeypatch.setenv("RISK_MAX_LEVERAGE", "7")
        from gpt_trader.app.config.bot_config import BotConfig

        config = BotConfig.from_env()
        assert not hasattr(config.risk, "max_leverage")

    def test_risk_position_fraction_from_pct_per_symbol(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RISK_MAX_POSITION_PCT_PER_SYMBOL", "0.15")
        from gpt_trader.app.config.bot_config import BotConfig

        config = BotConfig.from_env()
        assert config.risk.position_fraction == Decimal("0.15")

    def test_risk_daily_loss_limit_pct_env_retired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # RISK_DAILY_LOSS_LIMIT_PCT fed the retired BotRiskConfig alias
        # (#1120 stage 3); the breaker derives from the RiskBudget.
        monkeypatch.setenv("RISK_DAILY_LOSS_LIMIT_PCT", "0.08")
        from gpt_trader.app.config.bot_config import BotConfig

        config = BotConfig.from_env()
        assert not hasattr(config.risk, "daily_loss_limit_pct")

    def test_trading_symbols_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADING_SYMBOLS", "BTC-USD,SOL-USD")
        from gpt_trader.app.config.bot_config import BotConfig

        config = BotConfig.from_env()
        assert config.symbols == ["BTC-USD", "SOL-USD"]

    def test_order_submission_retries_enabled_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORDER_SUBMISSION_RETRIES_ENABLED", "1")
        from gpt_trader.app.config.bot_config import BotConfig

        config = BotConfig.from_env()
        assert config.order_submission_retries_enabled is True

    def test_execution_resilience_defaults_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORDER_SUBMISSION_RETRIES_ENABLED", raising=False)
        monkeypatch.delenv("BROKER_CALLS_USE_DEDICATED_EXECUTOR", raising=False)
        from gpt_trader.app.config.bot_config import BotConfig

        config = BotConfig.from_env()
        assert config.order_submission_retries_enabled is True
        assert config.broker_calls_use_dedicated_executor is True

    def test_health_market_data_staleness_thresholds_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HEALTH_MARKET_DATA_STALENESS_SECONDS_WARN", "12.5")
        monkeypatch.setenv("HEALTH_MARKET_DATA_STALENESS_SECONDS_CRIT", "42.0")
        from gpt_trader.app.config.bot_config import BotConfig

        config = BotConfig.from_env()
        assert config.health_thresholds.market_data_staleness_seconds_warn == 12.5
        assert config.health_thresholds.market_data_staleness_seconds_crit == 42.0


class TestMarkStalenessGuard:
    """Tests for mark staleness guard integration."""

    def test_check_mark_staleness_returns_true_when_no_update(self) -> None:
        from gpt_trader.features.live_trade.risk.config import RiskConfig
        from gpt_trader.features.live_trade.risk.manager import LiveRiskManager

        config = RiskConfig(daily_loss_limit_pct=0.05)
        manager = LiveRiskManager(config=config, state_file=None)

        is_stale = manager.check_mark_staleness("BTC-USD")
        assert is_stale is True

    def test_check_mark_staleness_returns_false_when_fresh(self) -> None:
        import time

        from gpt_trader.features.live_trade.risk.config import RiskConfig
        from gpt_trader.features.live_trade.risk.manager import LiveRiskManager

        config = RiskConfig(daily_loss_limit_pct=0.05)
        manager = LiveRiskManager(config=config, state_file=None)

        manager.last_mark_update["BTC-USD"] = time.time()

        is_stale = manager.check_mark_staleness("BTC-USD")
        assert is_stale is False

    def test_check_mark_staleness_returns_true_when_expired(self) -> None:
        import time

        from gpt_trader.features.live_trade.risk.config import RiskConfig
        from gpt_trader.features.live_trade.risk.manager import LiveRiskManager

        config = RiskConfig(daily_loss_limit_pct=0.05)
        manager = LiveRiskManager(config=config, state_file=None)

        manager.last_mark_update["BTC-USD"] = time.time() - 180

        is_stale = manager.check_mark_staleness("BTC-USD")
        assert is_stale is True


class TestProfileStrategyHydration:
    """Tests for profile loading with strategy config."""

    def test_profile_loader_builds_strategy_config(self) -> None:
        from gpt_trader.app.config.profile_loader import ProfileLoader
        from gpt_trader.config.types import Profile

        loader = ProfileLoader()
        schema = loader.load(Profile.DEV)
        kwargs = loader.to_bot_config_kwargs(schema, Profile.DEV)

        assert "strategy" in kwargs
        from gpt_trader.features.live_trade.strategies.baseline import (
            PerpsStrategyConfig,
        )

        assert isinstance(kwargs["strategy"], PerpsStrategyConfig)
        assert kwargs["strategy"].short_ma_period > 0
        assert kwargs["strategy"].long_ma_period > 0

    def test_profile_loader_builds_risk_config(self) -> None:
        from gpt_trader.app.config.bot_config import BotRiskConfig
        from gpt_trader.app.config.profile_loader import ProfileLoader
        from gpt_trader.config.types import Profile

        loader = ProfileLoader()
        schema = loader.load(Profile.DEV)
        kwargs = loader.to_bot_config_kwargs(schema, Profile.DEV)

        assert "risk" in kwargs
        assert isinstance(kwargs["risk"], BotRiskConfig)
        assert kwargs["risk"].position_fraction > 0

    def test_paper_profile_exists(self) -> None:
        from gpt_trader.config.types import Profile

        assert hasattr(Profile, "PAPER")
        assert Profile.PAPER.value == "paper"

    def test_paper_profile_loads(self) -> None:
        from gpt_trader.app.config.profile_loader import ProfileLoader
        from gpt_trader.config.types import Profile

        loader = ProfileLoader()
        schema = loader.load(Profile.PAPER)
        assert schema.profile_name == "paper"

    def test_profile_schema_has_no_daily_loss_limit(self) -> None:
        # Appetite fields left the profile schema (#1120 stage 3); the
        # runtime breaker derives from the RiskBudget instead.
        from gpt_trader.app.config.profile_loader import ProfileLoader
        from gpt_trader.config.types import Profile

        loader = ProfileLoader()
        schema = loader.load(Profile.PAPER)
        assert not hasattr(schema.risk, "daily_loss_limit_pct")


class TestReduceOnlyEnforcement:
    """Tests for reduce-only mode enforcement in order flow."""

    def test_check_order_blocks_new_position_in_reduce_only(self) -> None:
        from gpt_trader.features.live_trade.risk.config import RiskConfig
        from gpt_trader.features.live_trade.risk.manager import LiveRiskManager

        config = RiskConfig(daily_loss_limit_pct=0.05)
        manager = LiveRiskManager(config=config, state_file=None)

        manager.set_reduce_only_mode(True, reason="test")

        order = {"symbol": "BTC-USD", "side": "BUY", "reduce_only": False}
        assert manager.check_order(order) is False

    def test_check_order_allows_reduce_only_order(self) -> None:
        from gpt_trader.features.live_trade.risk.config import RiskConfig
        from gpt_trader.features.live_trade.risk.manager import LiveRiskManager

        config = RiskConfig(daily_loss_limit_pct=0.05)
        manager = LiveRiskManager(config=config, state_file=None)

        manager.set_reduce_only_mode(True, reason="test")

        order = {"symbol": "BTC-USD", "side": "SELL", "reduce_only": True}
        assert manager.check_order(order) is True

    def test_daily_loss_triggers_reduce_only(self) -> None:
        from gpt_trader.features.live_trade.risk.config import RiskConfig
        from gpt_trader.features.live_trade.risk.manager import LiveRiskManager

        config = RiskConfig(daily_loss_limit_pct=0.05)
        manager = LiveRiskManager(config=config, state_file=None)

        manager.track_daily_pnl(Decimal("10000"), {})

        triggered = manager.track_daily_pnl(Decimal("9400"), {})

        assert triggered is True
        assert manager._reduce_only_mode is True
        assert manager._reduce_only_reason == "daily_loss_limit_breached"

"""Tests for BotConfig feature flag canonicalization and precedence."""

from __future__ import annotations

import os
import warnings

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.config.bot_config import HealthThresholdsConfig, MeanReversionConfig
from gpt_trader.app.runtime.fingerprint import (
    StartupConfigFingerprint,
    compare_startup_config_fingerprints,
    compute_startup_config_fingerprint,
)
from gpt_trader.features.live_trade.strategies.baseline import PerpsStrategyConfig


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Clear all environment variables for isolated tests."""
    for key in list(os.environ.keys()):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


class TestEnableShortsCanonical:
    """enable_shorts derives from the strategy configs (sole source, #1120)."""

    def test_active_enable_shorts_from_baseline_strategy(self) -> None:
        """Baseline strategy type derives enable_shorts from strategy config."""
        config = BotConfig(
            strategy=PerpsStrategyConfig(enable_shorts=True),
            strategy_type="baseline",
        )

        assert config.active_enable_shorts is True

    def test_active_enable_shorts_from_mean_reversion(self) -> None:
        """Mean reversion strategy type derives enable_shorts from mean_reversion config."""
        config = BotConfig(
            mean_reversion=MeanReversionConfig(enable_shorts=False),
            strategy_type="mean_reversion",
        )

        assert config.active_enable_shorts is False

    def test_set_enable_shorts_routes_to_strategy_configs(self) -> None:
        """set_enable_shorts writes the intent onto all strategy configs."""
        config = BotConfig(
            strategy=PerpsStrategyConfig(enable_shorts=False),
            mean_reversion=MeanReversionConfig(enable_shorts=False),
        )

        config.set_enable_shorts(True)

        assert config.strategy.enable_shorts is True
        assert config.mean_reversion.enable_shorts is True
        assert config.active_enable_shorts is True

    def test_active_enable_shorts_does_not_warn(self) -> None:
        """The retired top-level alias no longer produces mismatch warnings."""
        config = BotConfig(
            strategy=PerpsStrategyConfig(enable_shorts=False),
            strategy_type="baseline",
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = config.active_enable_shorts

        assert result is False
        assert len(w) == 0


class TestMockBrokerEnvParsing:
    """Test MOCK_BROKER env parsing."""

    def test_mock_broker_env_true(self, clean_env: pytest.MonkeyPatch) -> None:
        """MOCK_BROKER=1 enables mock broker."""
        clean_env.setenv("MOCK_BROKER", "1")
        result = BotConfig.from_env().mock_broker
        assert result is True

    def test_mock_broker_env_default_false(self, clean_env: pytest.MonkeyPatch) -> None:
        """Defaults to False when MOCK_BROKER is unset."""
        result = BotConfig.from_env().mock_broker
        assert result is False


class TestCoinbaseAccountIdentityEnvParsing:
    def test_expected_identity_fields_load_from_env(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("COINBASE_EXPECTED_PORTFOLIO_UUID", " portfolio-1 ")
        clean_env.setenv(
            "COINBASE_EXPECTED_ACCOUNT_UUIDS",
            "usd-account,btc-account",
        )

        config = BotConfig.from_env()

        assert config.coinbase_expected_portfolio_uuid == "portfolio-1"
        assert config.coinbase_expected_account_uuids == ["usd-account", "btc-account"]

    def test_expected_identity_fields_default_unset(self, clean_env: pytest.MonkeyPatch) -> None:
        config = BotConfig.from_env()

        assert config.coinbase_expected_portfolio_uuid is None
        assert config.coinbase_expected_account_uuids == []


class TestRobinhoodCryptoAccountIdentityEnvParsing:
    def test_expected_account_loads_from_env(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("ROBINHOOD_CRYPTO_EXPECTED_ACCOUNT_NUMBER", " account-123 ")

        config = BotConfig.from_env()

        assert config.robinhood_crypto_expected_account_number == "account-123"

    def test_expected_account_defaults_unset(self, clean_env: pytest.MonkeyPatch) -> None:
        assert BotConfig.from_env().robinhood_crypto_expected_account_number is None


class TestReduceOnlyModeEnvParsing:
    """Test reduce_only_mode env variable parsing."""

    def test_risk_prefixed_enabled(self, clean_env: pytest.MonkeyPatch) -> None:
        """RISK_REDUCE_ONLY_MODE enables reduce-only mode."""
        clean_env.setenv("RISK_REDUCE_ONLY_MODE", "1")
        clean_env.setenv("BROKER", "coinbase")
        config = BotConfig.from_env()
        assert config.reduce_only_mode is True

    def test_default_false(self, clean_env: pytest.MonkeyPatch) -> None:
        """Defaults to False when no env vars set."""
        clean_env.setenv("BROKER", "coinbase")
        config = BotConfig.from_env()
        assert config.reduce_only_mode is False


class TestDerivativesEnvParsing:
    """Test derivatives environment flag parsing."""

    def test_derivatives_enabled_when_cfm_enabled(self, clean_env: pytest.MonkeyPatch) -> None:
        """CFM_ENABLED=1 enables derivatives (CFM US futures)."""
        clean_env.setenv("CFM_ENABLED", "1")
        config = BotConfig.from_env()
        assert config.derivatives_enabled is True

    def test_derivatives_disabled_by_default(self, clean_env: pytest.MonkeyPatch) -> None:
        """Derivatives default to disabled when no flags are set."""
        config = BotConfig.from_env()
        assert config.derivatives_enabled is False

    def test_deprecated_intx_perps_flag_still_enables_with_warning(
        self, clean_env: pytest.MonkeyPatch
    ) -> None:
        """The retired COINBASE_ENABLE_INTX_PERPS alias still enables, but warns."""
        clean_env.setenv("COINBASE_ENABLE_INTX_PERPS", "1")
        with pytest.warns(DeprecationWarning, match="COINBASE_ENABLE_INTX_PERPS"):
            config = BotConfig.from_env()
        assert config.derivatives_enabled is True
        assert config.cfm_enabled is True

    def test_deprecated_intx_perps_flag_zero_does_not_warn(
        self, clean_env: pytest.MonkeyPatch
    ) -> None:
        """COINBASE_ENABLE_INTX_PERPS=0 disables derivatives without warning."""
        clean_env.setenv("COINBASE_ENABLE_INTX_PERPS", "0")
        config = BotConfig.from_env()
        assert config.derivatives_enabled is False


class TestFromDictLegacyProfileMapping:
    """Test BotConfig.from_dict legacy profile schema compatibility."""

    def test_profile_style_emits_deprecation_warning(self) -> None:
        with pytest.warns(DeprecationWarning, match=r"Legacy profile-style YAML mapping"):
            config = BotConfig.from_dict({"profile_name": "minimal"})

        assert config.symbols

    def test_profile_style_maps_strategy_signal_proposals_gate(self) -> None:
        with pytest.warns(DeprecationWarning, match=r"Legacy profile-style YAML mapping"):
            config = BotConfig.from_dict(
                {
                    "profile_name": "proposal-profile",
                    "execution": {"strategy_signal_proposals": True},
                }
            )

        assert config.strategy_signal_proposals_enabled is True

    def test_profile_style_maps_event_driven_paper_lane_gate(self) -> None:
        with pytest.warns(DeprecationWarning, match=r"Legacy profile-style YAML mapping"):
            config = BotConfig.from_dict(
                {
                    "profile_name": "event-lane-profile",
                    "execution": {"event_driven_paper_lane": True},
                }
            )

        assert config.event_driven_paper_lane_enabled is True

    def test_event_driven_paper_lane_gate_defaults_off(self) -> None:
        assert BotConfig().event_driven_paper_lane_enabled is False

    def test_profile_style_maps_risk_budget_runtime_seed_gate(self) -> None:
        with pytest.warns(DeprecationWarning, match=r"Legacy profile-style YAML mapping"):
            config = BotConfig.from_dict(
                {
                    "profile_name": "seed-profile",
                    "execution": {"risk_budget_runtime_seed": True},
                }
            )

        assert config.risk_budget_runtime_seed_enabled is True


class TestHealthThresholdsConfig:
    """Tests for health threshold model conversion."""

    def test_to_health_thresholds_preserves_values(self) -> None:
        configured = HealthThresholdsConfig(
            order_error_rate_warn=0.02,
            order_error_rate_crit=0.12,
            order_retry_rate_warn=0.07,
            order_retry_rate_crit=0.22,
            broker_latency_ms_warn=900.0,
            broker_latency_ms_crit=2800.0,
            ws_staleness_seconds_warn=20.0,
            ws_staleness_seconds_crit=50.0,
            market_data_staleness_seconds_warn=8.0,
            market_data_staleness_seconds_crit=25.0,
            guard_trip_count_warn=4,
            guard_trip_count_crit=11,
            missing_decision_id_count_warn=2,
            missing_decision_id_count_crit=5,
        )

        converted = configured.to_health_thresholds()

        assert converted.order_error_rate_warn == 0.02
        assert converted.order_error_rate_crit == 0.12
        assert converted.order_retry_rate_warn == 0.07
        assert converted.order_retry_rate_crit == 0.22
        assert converted.broker_latency_ms_warn == 900.0
        assert converted.broker_latency_ms_crit == 2800.0
        assert converted.ws_staleness_seconds_warn == 20.0
        assert converted.ws_staleness_seconds_crit == 50.0


class TestStartupConfigFingerprint:
    """Ensure configuration fingerprints are deterministic and comparable."""

    def test_deterministic_fingerprint(self) -> None:
        config = BotConfig()
        first = compute_startup_config_fingerprint(config)
        second = compute_startup_config_fingerprint(config)
        assert first.digest == second.digest
        assert first.payload == second.payload

    def test_compare_detects_mismatch(self) -> None:
        left = StartupConfigFingerprint(digest="abc", payload={"foo": "bar"})
        right = StartupConfigFingerprint(digest="def", payload={"foo": "bar"})

        match, reason = compare_startup_config_fingerprints(left, right)

        assert not match
        assert "expected=abc" in reason
        assert "actual=def" in reason

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
    """Test enable_shorts derivation from strategy config with sync warning."""

    def test_active_enable_shorts_from_baseline_strategy(self) -> None:
        """Baseline strategy type derives enable_shorts from strategy config."""
        config = BotConfig(
            strategy=PerpsStrategyConfig(enable_shorts=True),
            strategy_type="baseline",
        )
        # Reset warning state for clean test
        BotConfig._enable_shorts_sync_warned = False

        with pytest.warns(UserWarning, match="differs from strategy config"):
            assert config.active_enable_shorts is True

    def test_active_enable_shorts_from_mean_reversion(self) -> None:
        """Mean reversion strategy type derives enable_shorts from mean_reversion config."""
        config = BotConfig(
            mean_reversion=MeanReversionConfig(enable_shorts=False),
            strategy_type="mean_reversion",
        )
        BotConfig._enable_shorts_sync_warned = False

        assert config.active_enable_shorts is False

    def test_sync_warning_on_mismatch(self) -> None:
        """Warns when BotConfig.enable_shorts differs from strategy config."""
        config = BotConfig(
            enable_shorts=True,  # Top-level says True
            strategy=PerpsStrategyConfig(enable_shorts=False),  # Strategy says False
            strategy_type="baseline",
        )
        BotConfig._enable_shorts_sync_warned = False

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = config.active_enable_shorts

        assert result is False  # Strategy config is canonical
        assert len(w) == 1
        assert "differs from strategy config" in str(w[0].message)

    def test_no_warning_when_synced(self) -> None:
        """No warning when BotConfig.enable_shorts matches strategy config."""
        config = BotConfig(
            enable_shorts=True,
            strategy=PerpsStrategyConfig(enable_shorts=True),
            strategy_type="baseline",
        )
        BotConfig._enable_shorts_sync_warned = False

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = config.active_enable_shorts

        assert len(w) == 0

    def test_warning_only_once_per_process(self) -> None:
        """Sync warning fires only once per process."""
        config = BotConfig(
            enable_shorts=True,
            strategy=PerpsStrategyConfig(enable_shorts=False),
            strategy_type="baseline",
        )
        BotConfig._enable_shorts_sync_warned = False

        # First access triggers warning
        with warnings.catch_warnings(record=True) as w1:
            warnings.simplefilter("always")
            _ = config.active_enable_shorts
        assert len(w1) == 1

        # Second access does not
        with warnings.catch_warnings(record=True) as w2:
            warnings.simplefilter("always")
            _ = config.active_enable_shorts
        assert len(w2) == 0


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

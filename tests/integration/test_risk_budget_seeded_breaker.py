"""MOCK_BROKER regression for the budget-seeded (loosened) runtime breaker.

The RiskBudget derivation seam (#1120) seeds the runtime breaker from the
active budget version, and the default budget is LOOSER than the legacy
RiskConfig defaults: 10% daily loss / 100% open notional vs 5% / 80%.
Enabling ``risk_budget_runtime_seed_enabled`` is therefore a live-path
behavior change. These tests pin the changed band through the real container
wiring with the deterministic broker: values between the legacy and seeded
limits no longer trip the breaker, values beyond the seeded limits still do,
and the restrict-side permissions (CFM 1x clamp, shorts off) land.
"""

from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.container import (
    ApplicationContainer,
    clear_application_container,
    set_application_container,
)
from gpt_trader.app.risk_budget_seed import RISK_BUDGET_SEED_EVENT_TYPE
from gpt_trader.features.live_trade.risk.manager import ValidationError

pytestmark = pytest.mark.integration

_MARK_PRICE = Decimal("50000")


def _build_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seed_enabled: bool,
) -> ApplicationContainer:
    # Point the seam at an empty ideas root: a missing budget log resolves to
    # DEFAULT_RISK_BUDGET (10% daily loss / 100% open notional, futures
    # leverage and naked shorts forbidden) — the exact budget the default
    # flip would put on the live path.
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(tmp_path / "ideas"))
    config = BotConfig(
        symbols=["BTC-USD"],
        mock_broker=True,
        risk_budget_runtime_seed_enabled=seed_enabled,
        runtime_root=str(tmp_path),
    )
    container = ApplicationContainer(config)
    set_application_container(container)
    return container


@pytest.fixture
def seeded_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[ApplicationContainer]:
    yield _build_container(tmp_path, monkeypatch, seed_enabled=True)
    clear_application_container()


@pytest.fixture
def legacy_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[ApplicationContainer]:
    yield _build_container(tmp_path, monkeypatch, seed_enabled=False)
    clear_application_container()


def _broker_equity(container: ApplicationContainer) -> Decimal:
    balances = container.broker.list_balances()
    return sum((balance.total for balance in balances), Decimal("0"))


class TestSeededBreakerLoosenedBand:
    """Seed enabled: the breaker runs at the budget's 10% / 100% limits."""

    def test_runtime_limits_seeded_from_default_budget(
        self, seeded_container: ApplicationContainer
    ) -> None:
        risk = seeded_container.risk_manager

        assert risk.config is not None
        assert risk.config.daily_loss_limit_pct == pytest.approx(0.10)
        assert risk.config.max_exposure_pct == pytest.approx(1.0)
        # allow_futures_leverage=False clamps the effective CFM cap to 1x.
        assert risk.config.cfm_max_leverage == 1
        # allow_naked_shorts=False forces shorts off end to end.
        assert seeded_container.config.enable_shorts is False
        assert seeded_container.config.active_enable_shorts is False
        # The seeded budget version is attributable in startup telemetry.
        events = seeded_container.event_store.get_recent_by_type(RISK_BUDGET_SEED_EVENT_TYPE)
        assert len(events) == 1

    def test_daily_loss_between_legacy_and_seeded_limits_does_not_trip(
        self, seeded_container: ApplicationContainer
    ) -> None:
        risk = seeded_container.risk_manager
        start_equity = _broker_equity(seeded_container)
        risk.track_daily_pnl(start_equity, {})

        # 7% down: inside the loosened band (would trip the legacy 5% limit).
        tripped = risk.track_daily_pnl(start_equity * Decimal("0.93"), {})

        assert tripped is False
        assert risk.is_reduce_only_mode() is False

    def test_daily_loss_beyond_seeded_limit_still_trips(
        self, seeded_container: ApplicationContainer
    ) -> None:
        risk = seeded_container.risk_manager
        start_equity = _broker_equity(seeded_container)
        risk.track_daily_pnl(start_equity, {})

        # 11% down: beyond the seeded 10% limit.
        tripped = risk.track_daily_pnl(start_equity * Decimal("0.89"), {})

        assert tripped is True
        assert risk.is_reduce_only_mode() is True

    def test_exposure_between_legacy_and_seeded_caps_is_accepted(
        self, seeded_container: ApplicationContainer
    ) -> None:
        risk = seeded_container.risk_manager
        equity = _broker_equity(seeded_container)

        # 90% of equity: inside the loosened band (legacy cap was 80%).
        qty = equity * Decimal("0.90") / _MARK_PRICE
        risk.pre_trade_validate("BTC-USD", "buy", qty, _MARK_PRICE, None, equity, {})

    def test_exposure_beyond_seeded_cap_is_still_rejected(
        self, seeded_container: ApplicationContainer
    ) -> None:
        risk = seeded_container.risk_manager
        equity = _broker_equity(seeded_container)

        qty = equity * Decimal("1.05") / _MARK_PRICE
        with pytest.raises(ValidationError, match="exceed cap"):
            risk.pre_trade_validate("BTC-USD", "buy", qty, _MARK_PRICE, None, equity, {})


class TestLegacyBreakerContrast:
    """Seed disabled: the same inputs trip the legacy 5% / 80% limits.

    This is the other half of the regression pin — if these start passing,
    the loosened band no longer describes a behavior change and the seeded
    tests above lose their meaning.
    """

    def test_daily_loss_trips_at_legacy_limit(self, legacy_container: ApplicationContainer) -> None:
        risk = legacy_container.risk_manager
        start_equity = _broker_equity(legacy_container)
        risk.track_daily_pnl(start_equity, {})

        tripped = risk.track_daily_pnl(start_equity * Decimal("0.93"), {})

        assert tripped is True
        assert risk.is_reduce_only_mode() is True

    def test_exposure_rejected_at_legacy_cap(self, legacy_container: ApplicationContainer) -> None:
        risk = legacy_container.risk_manager
        equity = _broker_equity(legacy_container)

        qty = equity * Decimal("0.90") / _MARK_PRICE
        with pytest.raises(ValidationError, match="exceed cap"):
            risk.pre_trade_validate("BTC-USD", "buy", qty, _MARK_PRICE, None, equity, {})

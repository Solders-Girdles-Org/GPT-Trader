"""Guarded execution shim for backtesting parity."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from gpt_trader.app.config import BotConfig
from gpt_trader.app.protocols import EventStoreProtocol
from gpt_trader.backtesting.validation.decision_logger import DecisionLogger, StrategyDecision
from gpt_trader.core import OrderSide, OrderType, Product
from gpt_trader.core.protocols import ExtendedBrokerProtocol
from gpt_trader.features.live_trade.execution import OrderSubmitter, OrderValidator, StateCollector
from gpt_trader.features.live_trade.execution.submission_result import (
    OrderSubmissionResult,
    OrderSubmissionStatus,
)
from gpt_trader.features.live_trade.execution.validation import (
    ValidationFailureTracker,
)
from gpt_trader.features.live_trade.risk import ValidationError
from gpt_trader.features.live_trade.risk.protocols import RiskManagerProtocol
from gpt_trader.persistence.event_store import EventStore
from gpt_trader.persistence.orders_store import OrdersStore


@dataclass(frozen=True)
class BacktestDecisionContext:
    """Optional context for decision logging."""

    cycle_id: str | None = None
    strategy_name: str = ""
    strategy_params: dict[str, Any] = field(default_factory=dict)
    recent_marks: list[Decimal] = field(default_factory=list)
    mark_price: Decimal | None = None


@dataclass(frozen=True)
class BacktestExecutionContext:
    """Minimal execution context for backtesting guard parity."""

    config: BotConfig
    broker: ExtendedBrokerProtocol
    risk_manager: RiskManagerProtocol
    event_store: EventStoreProtocol | None = None
    orders_store: OrdersStore | None = None
    bot_id: str = "backtest"
    decision_logger: DecisionLogger | None = None
    integration_mode: bool = False
    failure_tracker: ValidationFailureTracker | None = None


class BacktestGuardedExecutor:
    """Execute orders through live guard stack against a simulated broker."""

    def __init__(self, context: BacktestExecutionContext) -> None:
        event_store = context.event_store or EventStore()
        self._context = context
        self._event_store = event_store
        self._decision_logger = context.decision_logger

        self._open_orders: list[str] = []
        self._state_collector = StateCollector(
            broker=context.broker,
            config=context.config,
            integration_mode=context.integration_mode,
        )
        self._order_submitter = OrderSubmitter(
            broker=context.broker,
            event_store=event_store,
            bot_id=context.bot_id,
            open_orders=self._open_orders,
            orders_store=context.orders_store,
            integration_mode=context.integration_mode,
        )
        failure_tracker = context.failure_tracker or ValidationFailureTracker()
        self._order_validator = OrderValidator(
            broker=context.broker,
            risk_manager=context.risk_manager,
            enable_order_preview=context.config.enable_order_preview,
            record_preview_callback=self._order_submitter.record_preview,
            record_rejection_callback=self._order_submitter.record_rejection,
            failure_tracker=failure_tracker,
        )

    @property
    def open_orders(self) -> list[str]:
        """Return the tracked open order IDs."""

        return list(self._open_orders)

    def submit_order(
        self,
        *,
        symbol: str,
        side: OrderSide,
        order_type: OrderType = OrderType.MARKET,
        quantity: Decimal,
        price: Decimal | None = None,
        effective_price: Decimal | None = None,
        reduce_only: bool = False,
        leverage: int | None = None,
        product: Product | None = None,
        decision_context: BacktestDecisionContext | None = None,
        reason: str = "backtest_submission",
        client_order_id: str | None = None,
    ) -> OrderSubmissionResult:
        """Submit an order through StateCollector + OrderValidator + OrderSubmitter."""

        _, equity, _, _, positions = self._state_collector.collect_account_state()
        positions_dict = self._state_collector.build_positions_dict(positions)

        resolved_product = self._state_collector.require_product(symbol, product)
        resolved_price = price if price is None else Decimal(str(price))
        effective = effective_price or self._state_collector.resolve_effective_price(
            symbol,
            side.value.lower(),
            resolved_price,
            resolved_product,
        )
        order_quantity = Decimal(str(quantity))

        decision = self._build_decision(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=order_quantity,
            price=resolved_price,
            equity=equity,
            positions_dict=positions_dict,
            effective_price=effective,
            decision_context=decision_context,
            reason=reason,
        )
        decision_id = client_order_id
        if decision_id is None:
            if decision is not None:
                decision_id = decision.decision_id
            else:
                decision_id = self._order_submitter.generate_client_order_id(None)

        try:
            order_quantity, adjusted_price = self._order_validator.validate_exchange_rules(
                symbol=symbol,
                side=side,
                order_type=order_type,
                order_quantity=order_quantity,
                price=resolved_price,
                effective_price=effective,
                product=resolved_product,
            )
            if adjusted_price is not None:
                resolved_price = adjusted_price
        except ValidationError as exc:
            return self._handle_validation_failure(
                decision=decision,
                symbol=symbol,
                side=side,
                quantity=order_quantity,
                price=resolved_price,
                reason_code="exchange_rules",
                error=str(exc),
            )
        except Exception as exc:
            return self._handle_guard_error(
                decision=decision,
                symbol=symbol,
                side=side,
                quantity=order_quantity,
                price=resolved_price,
                error=str(exc),
            )

        try:
            self._order_validator.run_pre_trade_validation(
                symbol=symbol,
                side=side,
                order_quantity=order_quantity,
                effective_price=effective,
                product=resolved_product,
                equity=equity,
                current_positions=positions_dict,
            )
        except ValidationError as exc:
            return self._handle_validation_failure(
                decision=decision,
                symbol=symbol,
                side=side,
                quantity=order_quantity,
                price=resolved_price,
                reason_code="pre_trade_validation",
                error=str(exc),
            )
        except Exception as exc:
            return self._handle_guard_error(
                decision=decision,
                symbol=symbol,
                side=side,
                quantity=order_quantity,
                price=resolved_price,
                error=str(exc),
            )

        try:
            self._order_validator.enforce_slippage_guard(symbol, side, order_quantity, effective)
        except ValidationError as exc:
            return self._handle_validation_failure(
                decision=decision,
                symbol=symbol,
                side=side,
                quantity=order_quantity,
                price=resolved_price,
                reason_code="slippage_guard",
                error=str(exc),
            )
        except Exception as exc:
            return self._handle_guard_error(
                decision=decision,
                symbol=symbol,
                side=side,
                quantity=order_quantity,
                price=resolved_price,
                error=str(exc),
            )

        try:
            self._order_validator.maybe_preview_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                order_quantity=order_quantity,
                effective_price=effective,
                stop_price=None,
                tif=self._context.config.time_in_force,
                reduce_only=reduce_only,
                leverage=leverage,
            )
        except ValidationError as exc:
            return self._handle_validation_failure(
                decision=decision,
                symbol=symbol,
                side=side,
                quantity=order_quantity,
                price=resolved_price,
                reason_code="order_preview",
                error=str(exc),
            )
        except Exception as exc:
            return self._handle_guard_error(
                decision=decision,
                symbol=symbol,
                side=side,
                quantity=order_quantity,
                price=resolved_price,
                error=str(exc),
            )

        reduce_only_final = self._order_validator.finalize_reduce_only_flag(reduce_only, symbol)

        submission_outcome = self._order_submitter.submit_order_with_result(
            symbol=symbol,
            side=side,
            order_type=order_type,
            order_quantity=order_quantity,
            price=resolved_price,
            effective_price=effective,
            stop_price=None,
            tif=self._context.config.time_in_force,
            reduce_only=reduce_only_final,
            leverage=leverage,
            client_order_id=decision_id,
        )

        if not submission_outcome.success:
            reason = submission_outcome.reason or "broker_rejected"
            self._record_decision(
                decision=decision,
                passed=False,
                failures=[reason],
            )
            return OrderSubmissionResult(
                status=OrderSubmissionStatus.FAILED,
                reason=reason,
                error=submission_outcome.error_message,
            )

        order_id = submission_outcome.order_id
        self._record_decision(
            decision=decision,
            passed=True,
            failures=[],
            order_id=order_id,
            fill_price=effective,
            fill_quantity=order_quantity,
        )
        return OrderSubmissionResult(
            status=OrderSubmissionStatus.SUCCESS,
            order_id=order_id,
        )

    def _build_decision(
        self,
        *,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None,
        equity: Decimal,
        positions_dict: dict[str, dict[str, Any]],
        effective_price: Decimal,
        decision_context: BacktestDecisionContext | None,
        reason: str,
    ) -> StrategyDecision | None:
        if self._decision_logger is None:
            return None

        cycle_id = (
            decision_context.cycle_id
            if decision_context and decision_context.cycle_id
            else self._decision_logger.start_cycle()
        )
        position = positions_dict.get(symbol, {})
        position_quantity = Decimal(str(position.get("quantity", "0")))
        position_side = position.get("side")
        mark_price = (
            decision_context.mark_price
            if decision_context and decision_context.mark_price is not None
            else effective_price
        )
        recent_marks = (
            decision_context.recent_marks
            if decision_context and decision_context.recent_marks
            else [mark_price]
        )

        decision = StrategyDecision.create(
            cycle_id=cycle_id,
            symbol=symbol,
            equity=equity,
            position_quantity=position_quantity,
            position_side=position_side,
            mark_price=mark_price,
            recent_marks=recent_marks,
        )
        if decision_context:
            decision.with_strategy(decision_context.strategy_name, decision_context.strategy_params)
        decision.with_action(
            action=side.value,
            quantity=quantity,
            price=price,
            order_type=order_type.value,
            reason=reason,
        )
        return decision

    def _record_decision(
        self,
        *,
        decision: StrategyDecision | None,
        passed: bool,
        failures: list[str],
        order_id: str | None = None,
        fill_price: Decimal | None = None,
        fill_quantity: Decimal | None = None,
    ) -> None:
        if decision is None or self._decision_logger is None:
            return

        decision.with_risk_result(passed, failures)
        if passed and order_id and fill_price is not None and fill_quantity is not None:
            decision.with_execution(
                order_id=order_id,
                fill_price=fill_price,
                fill_quantity=fill_quantity,
            )
        self._decision_logger.log_decision(decision)

    def _handle_validation_failure(
        self,
        *,
        decision: StrategyDecision | None,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal | None,
        reason_code: str,
        error: str,
    ) -> OrderSubmissionResult:
        self._order_submitter.record_rejection(
            symbol=symbol,
            side=side.value,
            quantity=quantity,
            price=price,
            reason=reason_code,
        )
        self._record_decision(
            decision=decision,
            passed=False,
            failures=[reason_code],
        )
        return OrderSubmissionResult(
            status=OrderSubmissionStatus.BLOCKED,
            reason=error,
        )

    def _handle_guard_error(
        self,
        *,
        decision: StrategyDecision | None,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal | None,
        error: str,
    ) -> OrderSubmissionResult:
        self._order_submitter.record_rejection(
            symbol=symbol,
            side=side.value,
            quantity=quantity,
            price=price,
            reason="guard_error",
        )
        self._record_decision(
            decision=decision,
            passed=False,
            failures=["guard_error"],
        )
        return OrderSubmissionResult(
            status=OrderSubmissionStatus.FAILED,
            error=error,
        )

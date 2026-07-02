"""
Backtest runner that produces a readiness evidence artifact.

This script runs a readiness-gated strategy over historical candles and writes:
- JSON: runtime_data/<profile>/reports/backtest_<run_id>.json
- Text: runtime_data/<profile>/reports/backtest_<run_id>.txt
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from gpt_trader.app.config import StrategyType
from gpt_trader.app.config.bot_config import BotConfig
from gpt_trader.app.config.profile_loader import ProfileLoader
from gpt_trader.backtesting.data.manager import create_coinbase_data_provider
from gpt_trader.backtesting.metrics.risk import calculate_risk_metrics
from gpt_trader.backtesting.metrics.statistics import calculate_trade_statistics
from gpt_trader.backtesting.simulation.broker import SimulatedBroker
from gpt_trader.backtesting.types import FeeTier, SimulationConfig
from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.brokerages.coinbase.client.client import CoinbaseClient
from gpt_trader.features.live_trade.factory import create_strategy
from gpt_trader.features.live_trade.strategies.baseline import Action
from gpt_trader.features.live_trade.strategies.baseline.strategy import SpotStrategy
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="backtest_runner")


def _iso_utc(dt: datetime) -> str:
    dt_utc = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return dt_utc.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime:
    raw = value.strip()
    if len(raw) == 10:  # YYYY-MM-DD
        dt = datetime.fromisoformat(raw).replace(tzinfo=UTC)
        return dt
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _anchor_end(args_end: str | None) -> datetime:
    if args_end:
        return _parse_dt(args_end)
    now_utc = datetime.now(tz=UTC)
    return datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=UTC)


def _window_ranges(
    *,
    anchor_end: datetime,
    window_days: int,
    step_days: int,
    windows: int,
) -> list[tuple[datetime, datetime]]:
    if step_days <= 0 or window_days <= 0:
        raise ValueError("window_days and step_days must be positive")
    if step_days > window_days:
        raise ValueError("step_days must be <= window_days")
    ranges: list[tuple[datetime, datetime]] = []
    for idx in range(windows):
        window_end = anchor_end - timedelta(days=idx * step_days)
        window_start = window_end - timedelta(days=window_days)
        ranges.append((window_start, window_end))
    return ranges


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso_utc(value)
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        # Enum
        return getattr(value, "value")
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _serialize(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {
                "webhook_url",
                "coinbase_intx_portfolio_uuid",
                "metadata",
            } or any(
                token in key_text for token in ("secret", "token", "pass" + "word", "webhook")
            ):
                redacted[key] = "REDACTED"
            else:
                redacted[key] = _redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


def _snapshot_config(config: BotConfig) -> dict[str, Any]:
    return _redact_sensitive(_serialize(config))


def _hash_payload(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _hash_candles(candles: Sequence[Any]) -> str:
    hasher = hashlib.sha256()
    for candle in candles:
        ts = _iso_utc(getattr(candle, "ts", datetime.now(tz=UTC)))
        hasher.update(ts.encode("utf-8"))
        hasher.update(b"|")
        hasher.update(str(getattr(candle, "open", "")).encode("utf-8"))
        hasher.update(b"|")
        hasher.update(str(getattr(candle, "high", "")).encode("utf-8"))
        hasher.update(b"|")
        hasher.update(str(getattr(candle, "low", "")).encode("utf-8"))
        hasher.update(b"|")
        hasher.update(str(getattr(candle, "close", "")).encode("utf-8"))
        hasher.update(b"|")
        hasher.update(str(getattr(candle, "volume", "")).encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _candles_coverage(candles: Sequence[Any]) -> dict[str, Any]:
    if not candles:
        return {"count": 0, "first_ts": None, "last_ts": None}
    first = candles[0]
    last = candles[-1]
    return {
        "count": len(candles),
        "first_ts": _iso_utc(getattr(first, "ts", datetime.now(tz=UTC))),
        "last_ts": _iso_utc(getattr(last, "ts", datetime.now(tz=UTC))),
    }


def _quality_pass(quality_report: Any | None) -> tuple[bool | None, bool | None]:
    if quality_report is None:
        return None, None
    is_acceptable = bool(getattr(quality_report, "is_acceptable", False))
    issues = getattr(quality_report, "all_issues", [])
    has_error = any(getattr(issue, "severity", "") == "error" for issue in issues)
    return is_acceptable and not has_error, is_acceptable


def _run_guard_parity(
    *,
    profile: str,
    symbol: str,
    output_dir: Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    script_path = Path(__file__).resolve().parent / "analysis" / "guard_parity_regression.py"
    command = [
        sys.executable,
        str(script_path),
        "--profile",
        profile,
        "--symbol",
        symbol,
        "--output-dir",
        str(output_dir),
    ]
    if run_id:
        command.extend(["--run-id", run_id])

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
    stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]

    json_report = None
    for line in stdout_lines:
        if line.startswith("json_report:"):
            json_report = line.partition(":")[2].strip()

    return {
        "status": "pass" if result.returncode == 0 else "fail",
        "exit_code": result.returncode,
        "json_report": json_report,
        "stdout_tail": stdout_lines[-10:],
        "stderr_tail": stderr_lines[-10:],
        "command": command,
    }


def _load_profile_config(profile_name: str) -> BotConfig:
    from gpt_trader.config.types import Profile

    profile_enum = Profile(profile_name)
    loader = ProfileLoader()
    schema = loader.load(profile_enum)
    kwargs = loader.to_bot_config_kwargs(schema, profile_enum)
    return BotConfig(**kwargs)


def _build_position_state(position: Any) -> dict[str, Any] | None:
    if position is None:
        return None
    return {
        "quantity": position.quantity,
        "entry_price": position.entry_price,
        "unrealized_pnl": position.unrealized_pnl,
        "side": position.side,
        "leverage": position.leverage,
    }


def _select_strategy(config: BotConfig, *, ensemble_profile: str | None = None) -> Any:
    if config.strategy_type == "ensemble" and ensemble_profile:
        from gpt_trader.features.live_trade.strategies.ensemble import EnsembleStrategy

        profile_path = Path(ensemble_profile)
        if profile_path.exists():
            return EnsembleStrategy.from_yaml(profile_path)
        return EnsembleStrategy.from_profile_name(ensemble_profile)

    strategy = create_strategy(config)

    # Backtesting defaults to spot-safe behavior when shorts are disabled.
    if config.strategy_type == "baseline" and not config.enable_shorts:
        return SpotStrategy(config=config.strategy)

    if config.strategy_type == "regime_switcher":
        config.regime_switcher_trend_mode = cast(
            Literal["delegate", "regime_follow"], config.regime_switcher_trend_mode
        )

    return strategy


def _lookback_bars(strategy: Any) -> int:
    hint = getattr(strategy, "required_lookback_bars", None)
    if hint is not None:
        try:
            value = hint() if callable(hint) else hint
            if isinstance(value, (int, float, str)):
                return int(value)
            return int(str(value))
        except (TypeError, ValueError):
            pass

    config = getattr(strategy, "config", None)
    if config is None:
        return 64

    required = 64

    long_ma = getattr(config, "long_ma_period", None)
    if long_ma is None:
        long_ma = getattr(config, "long_ma", None)
    rsi_period = getattr(config, "rsi_period", None)
    if long_ma is not None or rsi_period is not None:
        long_ma_value = int(long_ma) if long_ma is not None else 20
        rsi_value = int(rsi_period) if rsi_period is not None else 14
        required = max(required, max(long_ma_value, rsi_value + 1) + 10)

    # Mean reversion strategies depend on rolling windows that should be fully
    # available in backtests, otherwise larger window configs silently clamp.
    lookback_window = getattr(config, "lookback_window", None)
    if lookback_window is not None:
        required = max(required, int(lookback_window))
    if getattr(config, "trend_filter_enabled", False):
        trend_window = getattr(config, "trend_window", None)
        if trend_window is not None:
            required = max(required, int(trend_window))

    return required


def _evaluate_pillar_2_gates(
    *,
    risk_metrics: Any,
    trade_statistics: Any,
    broker_stats: dict[str, Any],
    initial_equity: Decimal,
    quality_report: Any | None,
    candles_loaded: int,
) -> dict[str, Any]:
    max_drawdown = Decimal(str(getattr(risk_metrics, "max_drawdown_pct", 0)))
    sharpe = getattr(risk_metrics, "sharpe_ratio", None)
    sharpe_value = Decimal(str(sharpe)) if sharpe is not None else None
    profit_factor = Decimal(str(getattr(trade_statistics, "profit_factor", 0)))
    total_trades = int(getattr(trade_statistics, "total_trades", 0))
    total_fees = Decimal(str(broker_stats.get("total_fees_paid", 0)))
    max_fees_allowed = initial_equity * Decimal("0.05")
    trade_rate_threshold = Decimal("100") / Decimal("72")
    trade_threshold = max(1, (candles_loaded + 71) // 72)
    trades_per_100_bars = (
        Decimal(total_trades) / Decimal(candles_loaded) * Decimal("100")
        if candles_loaded > 0
        else Decimal("0")
    )
    trades_per_100_bars_display = trades_per_100_bars.quantize(Decimal("0.01"))
    trade_rate_threshold_display = trade_rate_threshold.quantize(Decimal("0.01"))

    data_quality_ok, data_quality_acceptable = _quality_pass(quality_report)

    results: dict[str, Any] = {
        "max_drawdown_pct": {
            "value": str(max_drawdown),
            "threshold": "10",
            "pass": max_drawdown <= Decimal("10"),
        },
        "profit_factor": {
            "value": str(profit_factor),
            "threshold": "1.2",
            "pass": profit_factor >= Decimal("1.2"),
        },
        "sharpe_ratio": {
            "value": str(sharpe_value) if sharpe_value is not None else None,
            "threshold": "1.0",
            "pass": sharpe_value is not None and sharpe_value >= Decimal("1.0"),
        },
        "total_fees_paid": {
            "value": str(total_fees),
            "threshold": str(max_fees_allowed),
            "pass": total_fees <= max_fees_allowed,
        },
        "total_trades": {
            "value": total_trades,
            "threshold": trade_threshold,
            "pass": total_trades >= trade_threshold,
        },
        "trades_per_100_bars": {
            "value": str(trades_per_100_bars_display),
            "threshold": str(trade_rate_threshold_display),
            "pass": trades_per_100_bars >= trade_rate_threshold,
        },
    }
    if data_quality_ok is not None:
        results["data_quality"] = {
            "pass": data_quality_ok,
            "acceptable": bool(data_quality_acceptable),
        }

    results["net_profit_factor"] = {
        "value": str(getattr(trade_statistics, "net_profit_factor", 0)),
        "threshold": "1.0",
        "pass": getattr(trade_statistics, "net_profit_factor", Decimal("0")) >= Decimal("1.0"),
    }
    results["fee_drag_per_trade"] = {
        "value": str(getattr(trade_statistics, "fee_drag_per_trade", 0)),
        "threshold": "50",
        "pass": getattr(trade_statistics, "fee_drag_per_trade", Decimal("0")) <= Decimal("50"),
    }

    gate_flags = [entry.get("pass", False) for entry in results.values() if isinstance(entry, dict)]
    results["overall_pass"] = all(gate_flags)
    return results


def _execute_decision(
    *,
    broker: SimulatedBroker,
    config: BotConfig,
    symbol: str,
    action: Action,
    equity: Decimal,
    price: Decimal,
    leverage: int,
    position_fraction: Decimal,
) -> None:
    position = broker.get_position(symbol)

    if action == Action.CLOSE:
        if position is None:
            return
        side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        order_type = OrderType.MARKET
        limit_price = None
        if config.use_limit_orders:
            order_type = OrderType.LIMIT
            limit_price = price

        broker.place_order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=abs(position.quantity),
            price=limit_price,
            reduce_only=True,
            leverage=leverage,
            tif=config.time_in_force,
        )

        return

    if action not in (Action.BUY, Action.SELL):
        return

    if price <= 0:
        return

    target_notional = equity * position_fraction * Decimal(leverage)
    quantity = target_notional / price
    if quantity <= 0:
        return

    side = OrderSide.BUY if action == Action.BUY else OrderSide.SELL
    order_type = OrderType.MARKET
    limit_price = None
    if config.use_limit_orders:
        order_type = OrderType.LIMIT
        limit_price = price

    broker.place_order(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=limit_price,
        leverage=leverage,
        tif=config.time_in_force,
    )


async def run_backtest(
    *,
    profile: str,
    symbol: str,
    granularity: str,
    start: datetime,
    end: datetime,
    initial_equity_usd: Decimal,
    fee_tier: FeeTier,
    position_fraction: Decimal,
    max_leverage: int,
    cache_dir: Path,
    strategy_type: str | None = None,
    ensemble_profile: str | None = None,
    enable_shorts: bool | None = None,
    regime_trend_mode: str = "delegate",
    risk_free_rate: Decimal = Decimal("0"),
    validate_quality: bool = True,
    spike_threshold_pct: float = 15.0,
    volume_anomaly_std: float = 6.0,
    mean_reversion_entry: float | None = None,
    mean_reversion_exit: float | None = None,
    mean_reversion_window: int | None = None,
    mean_reversion_cooldown: int | None = None,
    mean_reversion_trend_filter: bool = False,
    mean_reversion_trend_window: int | None = None,
    mean_reversion_trend_threshold: float | None = None,
    mean_reversion_trend_override_z: float | None = None,
    candles_override: Sequence[Any] | None = None,
    quality_report_override: Any | None = None,
) -> tuple[dict[str, Any], str]:
    config = _load_profile_config(profile)
    config.symbols = [symbol]
    if strategy_type is not None:
        config.strategy_type = cast(StrategyType, strategy_type)
    if enable_shorts is not None:
        config.enable_shorts = enable_shorts
        config.mean_reversion.enable_shorts = enable_shorts
    config.regime_switcher_trend_mode = cast(
        Literal["delegate", "regime_follow"], regime_trend_mode
    )

    if mean_reversion_entry is not None:
        config.mean_reversion.z_score_entry_threshold = mean_reversion_entry
    if mean_reversion_exit is not None:
        config.mean_reversion.z_score_exit_threshold = mean_reversion_exit
    if mean_reversion_window is not None:
        config.mean_reversion.lookback_window = mean_reversion_window
    if mean_reversion_cooldown is not None:
        config.mean_reversion.cooldown_bars = mean_reversion_cooldown
    if mean_reversion_trend_filter:
        config.mean_reversion.trend_filter_enabled = True
    if mean_reversion_trend_window is not None:
        config.mean_reversion.trend_window = mean_reversion_trend_window
    if mean_reversion_trend_threshold is not None:
        config.mean_reversion.trend_threshold_pct = mean_reversion_trend_threshold
    if mean_reversion_trend_override_z is not None:
        config.mean_reversion.trend_override_z_score = mean_reversion_trend_override_z

    config_snapshot = _snapshot_config(config)
    config_hash = _hash_payload(config_snapshot)

    # Use public market endpoints by default (no auth required).
    client = CoinbaseClient(api_mode=config.coinbase_api_mode)
    data_provider = create_coinbase_data_provider(
        client=client,
        cache_dir=cache_dir,
        validate_quality=validate_quality,
        spike_threshold_pct=spike_threshold_pct,
        volume_anomaly_std=volume_anomaly_std,
    )

    if candles_override is None:
        candles = await data_provider.get_candles(
            symbol=symbol,
            granularity=granularity,
            start=start,
            end=end,
        )
    else:
        candles = list(candles_override)
    candles = sorted(candles, key=lambda c: c.ts)
    if not candles:
        raise RuntimeError(f"No candles returned for {symbol} {granularity} {start}..{end}")
    candles_hash = _hash_candles(candles)
    candles_coverage = _candles_coverage(candles)

    sim_config = SimulationConfig(
        start_date=start,
        end_date=end,
        granularity=granularity,
        initial_equity_usd=initial_equity_usd,
        fee_tier=fee_tier,
    )
    broker = SimulatedBroker(
        initial_equity_usd=initial_equity_usd,
        fee_tier=fee_tier,
        config=sim_config,
    )
    broker.connect()

    strategy = _select_strategy(config, ensemble_profile=ensemble_profile)
    leverage = max(1, min(max_leverage, int(getattr(config.risk, "target_leverage", 1))))
    lookback_bars = _lookback_bars(strategy)
    mark_history: deque[Decimal] = deque(maxlen=lookback_bars)
    candle_history: deque[Any] = deque(maxlen=lookback_bars)

    for idx, candle in enumerate(candles, start=1):
        broker.update_bar(symbol, candle)
        broker.update_equity_curve()

        mark_history.append(candle.close)
        candle_history.append(candle)
        recent_marks = list(mark_history)
        current_mark = candle.close
        equity = broker.equity
        decision = strategy.decide(
            symbol=symbol,
            current_mark=current_mark,
            position_state=_build_position_state(broker.get_position(symbol)),
            recent_marks=recent_marks,
            equity=equity,
            product=None,
            candles=list(candle_history),
        )

        _execute_decision(
            broker=broker,
            config=config,
            symbol=symbol,
            action=decision.action,
            equity=equity,
            price=current_mark,
            leverage=leverage,
            position_fraction=position_fraction,
        )

        if idx % 5000 == 0:
            logger.info(
                "Backtest progress",
                processed=idx,
                total=len(candles),
                progress_pct=f"{(idx / len(candles)) * 100:.1f}",
            )

    # Force-close open position to realize PnL for reporting.
    if broker.get_position(symbol) is not None:
        _execute_decision(
            broker=broker,
            config=config,
            symbol=symbol,
            action=Action.CLOSE,
            equity=broker.equity,
            price=candles[-1].close,
            leverage=leverage,
            position_fraction=position_fraction,
        )
        broker.update_equity_curve()

    risk = calculate_risk_metrics(broker, risk_free_rate=risk_free_rate)
    stats = calculate_trade_statistics(broker)
    broker_stats = broker.get_statistics()

    quality_report = (
        quality_report_override
        if quality_report_override is not None
        else data_provider.get_quality_report(symbol, granularity)
    )

    gate_results = _evaluate_pillar_2_gates(
        risk_metrics=risk,
        trade_statistics=stats,
        broker_stats=broker_stats,
        initial_equity=initial_equity_usd,
        quality_report=quality_report,
        candles_loaded=len(candles),
    )

    payload: dict[str, Any] = {
        "run_id": f"backtest_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}",
        "generated_at": _iso_utc(datetime.now(tz=UTC)),
        "profile": profile,
        "symbol": symbol,
        "granularity": granularity,
        "start_date": _iso_utc(start),
        "end_date": _iso_utc(end),
        "candles_loaded": len(candles),
        "risk_free_rate": str(risk_free_rate),
        "spike_threshold_pct": spike_threshold_pct,
        "volume_anomaly_std": volume_anomaly_std,
        "strategy_type": config.strategy_type,
        "strategy_class": strategy.__class__.__name__,
        "ensemble_profile": ensemble_profile,
        "initial_equity_usd": str(initial_equity_usd),
        "fee_tier": fee_tier.value,
        "leverage": leverage,
        "position_fraction": str(position_fraction),
        "broker_stats": _serialize(broker_stats),
        "risk_metrics": _serialize(risk),
        "trade_statistics": _serialize(stats),
        "data_quality": _serialize(quality_report) if quality_report is not None else None,
        "pillar_2_gates": _serialize(gate_results),
        "config_snapshot": config_snapshot,
        "config_hash": config_hash,
        "candles_hash": candles_hash,
        "candles_coverage": candles_coverage,
    }

    summary_lines = [
        "BACKTEST SUMMARY",
        f"Profile: {profile}",
        f"Symbol: {symbol}",
        f"Granularity: {granularity}",
        f"Range: {_iso_utc(start)} .. {_iso_utc(end)}",
        f"Candles: {len(candles)}",
        f"Candles Hash: {candles_hash}",
        f"Config Hash: {config_hash}",
        "",
        "PERFORMANCE",
        f"Final Equity: {broker_stats['final_equity']}",
        f"Total Return %: {broker_stats['total_return_pct']}",
        "",
        "RISK",
        f"Risk-Free Rate: {risk_free_rate}",
        f"Max Drawdown %: {risk.max_drawdown_pct}",
        f"Sharpe: {risk.sharpe_ratio}",
        f"Sortino: {risk.sortino_ratio}",
        "",
        "TRADES",
        f"Total Trades: {stats.total_trades}",
        f"Win Rate %: {stats.win_rate}",
        f"Profit Factor: {stats.profit_factor}",
        f"Net Profit Factor: {stats.net_profit_factor}",
        f"Fee Drag/Trade: {stats.fee_drag_per_trade}",
    ]
    if ensemble_profile:
        summary_lines.insert(2, f"Ensemble Profile: {ensemble_profile}")
    summary = "\n".join(summary_lines) + "\n"
    return payload, summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a strategy backtest and save evidence artifacts."
    )
    parser.add_argument("--profile", default="canary", help="Profile name (default: canary)")
    parser.add_argument(
        "--symbol", default=None, help="Symbol to backtest (default: profile first)"
    )
    parser.add_argument("--granularity", default="FIVE_MINUTE", help="Candle granularity")
    parser.add_argument(
        "--start", type=str, default=None, help="Start datetime (ISO) or YYYY-MM-DD"
    )
    parser.add_argument("--end", type=str, default=None, help="End datetime (ISO) or YYYY-MM-DD")
    parser.add_argument(
        "--days", type=int, default=30, help="Lookback days when --start/--end omitted"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use quick defaults for faster iteration (caps lookback and skips quality checks)",
    )
    parser.add_argument(
        "--initial-equity-usd",
        type=str,
        default="100000",
        help="Initial equity in USD (default: 100000)",
    )
    parser.add_argument(
        "--fee-tier",
        type=str,
        default=FeeTier.TIER_2.name,
        choices=[t.name for t in FeeTier],
        help="Fee tier (default: TIER_2)",
    )
    parser.add_argument(
        "--risk-free-rate",
        type=float,
        default=0.0,
        help="Annual risk-free rate for Sharpe/Sortino (default: 0.0)",
    )
    parser.add_argument(
        "--spike-threshold-pct",
        type=float,
        default=15.0,
        help="Price spike threshold percent for data quality checks (default: 15.0)",
    )
    parser.add_argument(
        "--volume-anomaly-std",
        type=float,
        default=6.0,
        help="Volume anomaly std-dev threshold for data quality checks (default: 6.0)",
    )
    parser.add_argument(
        "--skip-quality",
        action="store_true",
        help="Skip data quality checks and reports (faster, less strict)",
    )
    parser.add_argument(
        "--guard-parity",
        action="store_true",
        help="Run guard parity regression and attach the report",
    )
    parser.add_argument(
        "--guard-parity-strict",
        action="store_true",
        help="Exit non-zero if guard parity fails",
    )
    parser.add_argument(
        "--position-fraction",
        type=str,
        default="0.10",
        help="Position fraction per trade (default: 0.10)",
    )
    parser.add_argument(
        "--max-leverage",
        type=int,
        default=1,
        help="Max leverage to use in simulation (default: 1)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Candle cache dir (default: runtime_data/<profile>/cache/candles)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output dir (default: runtime_data/<profile>/reports)",
    )
    parser.add_argument(
        "--ensemble-profile",
        type=str,
        default=None,
        help=("Ensemble profile name or path (use with strategy_type=ensemble)"),
    )
    parser.add_argument(
        "--strategy-type",
        type=str,
        default=None,
        choices=["baseline", "mean_reversion", "ensemble", "regime_switcher"],
        help="Override strategy type for the run",
    )
    parser.add_argument(
        "--regime-trend-mode",
        type=str,
        default="delegate",
        choices=["delegate", "regime_follow"],
        help="Trend behavior for regime_switcher (default: delegate)",
    )
    parser.add_argument(
        "--enable-shorts",
        action="store_true",
        help="Enable shorts for the backtest run",
    )
    parser.add_argument(
        "--disable-shorts",
        action="store_true",
        help="Disable shorts for the backtest run",
    )
    parser.add_argument(
        "--mean-reversion-entry",
        type=float,
        default=None,
        help="Mean reversion entry Z-score threshold override",
    )
    parser.add_argument(
        "--mean-reversion-exit",
        type=float,
        default=None,
        help="Mean reversion exit Z-score threshold override",
    )
    parser.add_argument(
        "--mean-reversion-window",
        type=int,
        default=None,
        help="Mean reversion lookback window override",
    )
    parser.add_argument(
        "--mean-reversion-cooldown",
        type=int,
        default=None,
        help="Mean reversion cooldown bars override",
    )
    parser.add_argument(
        "--mean-reversion-trend-filter",
        action="store_true",
        help="Enable mean reversion trend filter",
    )
    parser.add_argument(
        "--mean-reversion-trend-window",
        type=int,
        default=None,
        help="Mean reversion trend window override",
    )
    parser.add_argument(
        "--mean-reversion-trend-threshold",
        type=float,
        default=None,
        help="Mean reversion trend threshold percent (e.g. 0.01 = 1%%)",
    )
    parser.add_argument(
        "--mean-reversion-trend-override-z",
        type=float,
        default=None,
        help="Allow counter-trend entries when abs(z-score) >= this value",
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run walk-forward evaluation with rolling windows",
    )
    parser.add_argument(
        "--wf-windows",
        type=int,
        default=6,
        help="Number of walk-forward windows (default: 6)",
    )
    parser.add_argument(
        "--wf-window-days",
        type=int,
        default=90,
        help="Window size in days (default: 90)",
    )
    parser.add_argument(
        "--wf-step-days",
        type=int,
        default=30,
        help="Step size in days (default: 30)",
    )
    parser.add_argument(
        "--wf-require-all-pass",
        action="store_true",
        help="Exit non-zero if any walk-forward window fails",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if args.quick:
        if args.walk_forward:
            args.wf_windows = min(args.wf_windows, 3)
            args.wf_window_days = min(args.wf_window_days, 30)
            args.wf_step_days = min(args.wf_step_days, 15)
        elif args.start is None and args.end is None:
            args.days = min(args.days, 7)
        args.skip_quality = True

    require_quality = not args.quick and not args.skip_quality

    profile = args.profile
    config = _load_profile_config(profile)

    symbol = args.symbol or (config.symbols[0] if config.symbols else "BTC-USD")
    granularity = args.granularity

    anchor_end = _anchor_end(args.end)
    end = _parse_dt(args.end) if args.end else datetime.now(tz=UTC)
    start = _parse_dt(args.start) if args.start else end - timedelta(days=int(args.days))

    cache_dir = args.cache_dir or Path("runtime_data") / profile / "cache" / "candles"
    output_dir = args.output_dir or Path("runtime_data") / profile / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    initial_equity = Decimal(args.initial_equity_usd)
    fee_tier = FeeTier[args.fee_tier]
    position_fraction = Decimal(args.position_fraction)

    enable_shorts: bool | None = None
    if args.enable_shorts and args.disable_shorts:
        raise SystemExit("--enable-shorts and --disable-shorts are mutually exclusive")
    if args.enable_shorts:
        enable_shorts = True
    elif args.disable_shorts:
        enable_shorts = False

    if args.walk_forward:
        windows = _window_ranges(
            anchor_end=anchor_end,
            window_days=args.wf_window_days,
            step_days=args.wf_step_days,
            windows=args.wf_windows,
        )
        full_start = min(window_start for window_start, _ in windows)
        full_end = max(window_end for _, window_end in windows)

        cache_dir.mkdir(parents=True, exist_ok=True)
        run_stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        wf_dir = output_dir / f"walk_forward_{run_stamp}"
        wf_dir.mkdir(parents=True, exist_ok=True)

        config.symbols = [symbol]
        if args.strategy_type is not None:
            config.strategy_type = cast(StrategyType, args.strategy_type)
        if enable_shorts is not None:
            config.enable_shorts = enable_shorts
            config.mean_reversion.enable_shorts = enable_shorts
        config.regime_switcher_trend_mode = cast(
            Literal["delegate", "regime_follow"], args.regime_trend_mode
        )

        if args.mean_reversion_entry is not None:
            config.mean_reversion.z_score_entry_threshold = args.mean_reversion_entry
        if args.mean_reversion_exit is not None:
            config.mean_reversion.z_score_exit_threshold = args.mean_reversion_exit
        if args.mean_reversion_window is not None:
            config.mean_reversion.lookback_window = args.mean_reversion_window
        if args.mean_reversion_cooldown is not None:
            config.mean_reversion.cooldown_bars = args.mean_reversion_cooldown
        if args.mean_reversion_trend_filter:
            config.mean_reversion.trend_filter_enabled = True
        if args.mean_reversion_trend_window is not None:
            config.mean_reversion.trend_window = args.mean_reversion_trend_window
        if args.mean_reversion_trend_threshold is not None:
            config.mean_reversion.trend_threshold_pct = args.mean_reversion_trend_threshold
        if args.mean_reversion_trend_override_z is not None:
            config.mean_reversion.trend_override_z_score = args.mean_reversion_trend_override_z

        client = CoinbaseClient(api_mode=config.coinbase_api_mode)
        data_provider = create_coinbase_data_provider(
            client=client,
            cache_dir=cache_dir,
            validate_quality=not args.skip_quality,
            spike_threshold_pct=float(args.spike_threshold_pct),
            volume_anomaly_std=float(args.volume_anomaly_std),
        )

        candles = asyncio.run(
            data_provider.get_candles(
                symbol=symbol,
                granularity=granularity,
                start=full_start,
                end=full_end,
            )
        )
        candles = sorted(candles, key=lambda c: c.ts)
        if not candles:
            raise SystemExit("No candles returned for walk-forward range")

        quality_report = (
            None if args.skip_quality else data_provider.get_quality_report(symbol, granularity)
        )
        data_quality_ok, _ = _quality_pass(quality_report)
        if require_quality and data_quality_ok is False:
            print("Data quality check failed; aborting walk-forward run.")
            return 2

        summary_rows: list[dict[str, Any]] = []
        pass_count = 0

        config_overrides = {
            "strategy_type": args.strategy_type,
            "ensemble_profile": args.ensemble_profile,
            "enable_shorts": enable_shorts,
            "regime_trend_mode": args.regime_trend_mode,
            "risk_free_rate": Decimal(str(args.risk_free_rate)),
            "validate_quality": not args.skip_quality,
            "spike_threshold_pct": float(args.spike_threshold_pct),
            "volume_anomaly_std": float(args.volume_anomaly_std),
            "mean_reversion_entry": args.mean_reversion_entry,
            "mean_reversion_exit": args.mean_reversion_exit,
            "mean_reversion_window": args.mean_reversion_window,
            "mean_reversion_cooldown": args.mean_reversion_cooldown,
            "mean_reversion_trend_filter": args.mean_reversion_trend_filter,
            "mean_reversion_trend_window": args.mean_reversion_trend_window,
            "mean_reversion_trend_threshold": args.mean_reversion_trend_threshold,
            "mean_reversion_trend_override_z": args.mean_reversion_trend_override_z,
            "quality_report_override": quality_report,
        }

        summary_payload = {
            "generated_at": _iso_utc(datetime.now(tz=UTC)),
            "profile": profile,
            "symbol": symbol,
            "granularity": granularity,
            "anchor_end": _iso_utc(anchor_end),
            "window_days": args.wf_window_days,
            "step_days": args.wf_step_days,
            "windows": args.wf_windows,
            "pass_count": 0,
            "total_windows": 0,
            "pass_rate": 0.0,
            "rows": [],
            "strategy": {
                "strategy_type": args.strategy_type,
                "ensemble_profile": args.ensemble_profile,
                "regime_trend_mode": args.regime_trend_mode,
                "mean_reversion_entry": args.mean_reversion_entry,
                "mean_reversion_exit": args.mean_reversion_exit,
                "mean_reversion_window": args.mean_reversion_window,
                "mean_reversion_cooldown": args.mean_reversion_cooldown,
                "mean_reversion_trend_filter": args.mean_reversion_trend_filter,
                "mean_reversion_trend_window": args.mean_reversion_trend_window,
                "mean_reversion_trend_threshold": args.mean_reversion_trend_threshold,
                "mean_reversion_trend_override_z": args.mean_reversion_trend_override_z,
            },
        }

        for idx, (window_start, window_end) in enumerate(windows):
            window_candles = [
                candle for candle in candles if window_start <= candle.ts < window_end
            ]
            if not window_candles:
                raise SystemExit(f"No candles for window {idx}")

            payload, summary = asyncio.run(
                run_backtest(
                    profile=profile,
                    symbol=symbol,
                    granularity=granularity,
                    start=window_start,
                    end=window_end,
                    initial_equity_usd=initial_equity,
                    fee_tier=fee_tier,
                    position_fraction=position_fraction,
                    max_leverage=int(args.max_leverage),
                    cache_dir=cache_dir,
                    candles_override=window_candles,
                    **config_overrides,
                )
            )

            run_id = payload["run_id"]
            window_dir = wf_dir / (f"window_{idx:02d}_{window_start:%Y%m%d}_{window_end:%Y%m%d}")
            window_dir.mkdir(parents=True, exist_ok=True)

            json_path = window_dir / f"{run_id}.json"
            txt_path = window_dir / f"{run_id}.txt"
            json_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
            txt_path.write_text(summary)

            gates = payload.get("pillar_2_gates", {})
            failed_gates = [
                gate_id
                for gate_id, gate_value in gates.items()
                if isinstance(gate_value, dict) and not gate_value.get("pass", False)
            ]

            row = {
                "window_start": _iso_utc(window_start),
                "window_end": _iso_utc(window_end),
                "candles_loaded": payload.get("candles_loaded", 0),
                "total_trades": payload.get("trade_statistics", {}).get("total_trades"),
                "trades_per_100_bars": gates.get("trades_per_100_bars", {}).get("value"),
                "profit_factor": payload.get("trade_statistics", {}).get("profit_factor"),
                "net_profit_factor": payload.get("trade_statistics", {}).get("net_profit_factor"),
                "fee_drag_per_trade": payload.get("trade_statistics", {}).get("fee_drag_per_trade"),
                "sharpe_ratio": payload.get("risk_metrics", {}).get("sharpe_ratio"),
                "max_drawdown_pct": payload.get("risk_metrics", {}).get("max_drawdown_pct"),
                "overall_pass": gates.get("overall_pass", False),
                "failed_gates": failed_gates,
                "run_id": run_id,
                "artifact_json": str(json_path.relative_to(wf_dir)),
                "artifact_txt": str(txt_path.relative_to(wf_dir)),
            }
            summary_rows.append(row)
            if gates.get("overall_pass", False):
                pass_count += 1

        summary_payload.update(
            {
                "pass_count": pass_count,
                "total_windows": len(summary_rows),
                "pass_rate": round(pass_count / len(summary_rows), 3) if summary_rows else 0.0,
                "rows": summary_rows,
            }
        )

        guard_parity_result = None
        if args.guard_parity:
            guard_parity_result = _run_guard_parity(
                profile=profile,
                symbol=symbol,
                output_dir=output_dir,
            )
            summary_payload["guard_parity"] = guard_parity_result

        summary_json_path = wf_dir / "summary.json"
        summary_md_path = wf_dir / "summary.md"
        summary_json_path.write_text(json.dumps(summary_payload, indent=2, sort_keys=True))

        header = (
            "| Window Start | Window End | Candles | Trades | Trades/100 | Net PF | PF | "
            "Fee Drag | Sharpe | Max DD | Pass | Failed Gates |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        )
        rows_md = []
        for row in summary_rows:
            rows_md.append(
                "| {window_start} | {window_end} | {candles_loaded} | {total_trades} | "
                "{trades_per_100_bars} | {net_profit_factor} | {profit_factor} | "
                "{fee_drag_per_trade} | {sharpe_ratio} | {max_drawdown_pct} | {overall_pass} | "
                "{failed_gates} |".format(
                    window_start=row["window_start"],
                    window_end=row["window_end"],
                    candles_loaded=row["candles_loaded"],
                    total_trades=row["total_trades"],
                    trades_per_100_bars=row["trades_per_100_bars"],
                    net_profit_factor=row["net_profit_factor"],
                    profit_factor=row["profit_factor"],
                    fee_drag_per_trade=row["fee_drag_per_trade"],
                    sharpe_ratio=row["sharpe_ratio"],
                    max_drawdown_pct=row["max_drawdown_pct"],
                    overall_pass="yes" if row["overall_pass"] else "no",
                    failed_gates=", ".join(row["failed_gates"]) if row["failed_gates"] else "-",
                )
            )

        summary_lines = [
            f"Walk-forward pass count: {pass_count}/{len(summary_rows)}",
            f"Pass rate: {summary_payload['pass_rate']}",
        ]
        if guard_parity_result is not None:
            summary_lines.append(
                f"Guard parity: {guard_parity_result['status']} ({guard_parity_result['json_report']})"
            )
        summary_lines.extend(["", header, *rows_md])
        summary_md_path.write_text("\n".join(summary_lines))

        print(f"Walk-forward summary saved: {summary_json_path}")
        print(f"Walk-forward summary saved: {summary_md_path}")

        if args.guard_parity_strict and guard_parity_result is not None:
            if guard_parity_result.get("status") != "pass":
                return 3
        if args.wf_require_all_pass and pass_count != len(summary_rows):
            return 1
        return 0

    payload, summary = asyncio.run(
        run_backtest(
            profile=profile,
            symbol=symbol,
            granularity=granularity,
            start=start,
            end=end,
            initial_equity_usd=initial_equity,
            fee_tier=fee_tier,
            position_fraction=position_fraction,
            max_leverage=int(args.max_leverage),
            cache_dir=cache_dir,
            validate_quality=not args.skip_quality,
            strategy_type=args.strategy_type,
            ensemble_profile=args.ensemble_profile,
            enable_shorts=enable_shorts,
            regime_trend_mode=args.regime_trend_mode,
            risk_free_rate=Decimal(str(args.risk_free_rate)),
            spike_threshold_pct=float(args.spike_threshold_pct),
            volume_anomaly_std=float(args.volume_anomaly_std),
            mean_reversion_entry=args.mean_reversion_entry,
            mean_reversion_exit=args.mean_reversion_exit,
            mean_reversion_window=args.mean_reversion_window,
            mean_reversion_cooldown=args.mean_reversion_cooldown,
            mean_reversion_trend_filter=args.mean_reversion_trend_filter,
            mean_reversion_trend_window=args.mean_reversion_trend_window,
            mean_reversion_trend_threshold=args.mean_reversion_trend_threshold,
            mean_reversion_trend_override_z=args.mean_reversion_trend_override_z,
        )
    )

    guard_parity_result = None
    if args.guard_parity:
        guard_parity_result = _run_guard_parity(
            profile=profile,
            symbol=symbol,
            output_dir=output_dir,
        )
        payload["guard_parity"] = guard_parity_result

    run_id = payload["run_id"]
    json_path = output_dir / f"{run_id}.json"
    txt_path = output_dir / f"{run_id}.txt"

    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    txt_path.write_text(summary)

    logger.info(
        "Backtest complete", run_id=run_id, json_path=str(json_path), txt_path=str(txt_path)
    )
    print(summary)
    print(f"Saved: {json_path}")
    print(f"Saved: {txt_path}")
    quality_gate = payload.get("pillar_2_gates", {}).get("data_quality", {})
    if require_quality and quality_gate and not quality_gate.get("pass", True):
        print("Data quality check failed; exiting non-zero.")
        return 2
    if args.guard_parity_strict and guard_parity_result is not None:
        if guard_parity_result.get("status") != "pass":
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

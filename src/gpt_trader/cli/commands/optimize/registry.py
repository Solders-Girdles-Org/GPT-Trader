"""Declarative registry for optimize param groups and objectives."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from gpt_trader.features.optimize.objectives.factories import (
    create_execution_quality_objective,
    create_perpetuals_objective,
    create_risk_averse_objective,
    create_streak_resilient_objective,
    create_tail_risk_aware_objective,
    create_time_efficient_objective,
)
from gpt_trader.features.optimize.objectives.single import (
    CalmarRatioObjective,
    MaxDrawdownObjective,
    ProfitFactorObjective,
    SharpeRatioObjective,
    SortinoRatioObjective,
    TotalReturnObjective,
    WinRateObjective,
)
from gpt_trader.features.optimize.parameter_space.builder import ParameterSpaceBuilder
from gpt_trader.features.optimize.parameter_space.definitions import (
    risk_parameter_space,
    simulation_parameter_space,
    strategy_parameter_space,
)

ParameterGroupBuilder = Callable[[ParameterSpaceBuilder], ParameterSpaceBuilder]


def _strategy_builder(builder: ParameterSpaceBuilder) -> ParameterSpaceBuilder:
    return builder.with_strategy_defaults()


def _risk_builder(builder: ParameterSpaceBuilder) -> ParameterSpaceBuilder:
    return builder.with_risk_defaults()


def _simulation_builder(builder: ParameterSpaceBuilder) -> ParameterSpaceBuilder:
    return builder.with_simulation_defaults()


def _wrap_class_factory(factory: type) -> Callable[..., Any]:
    """Wrap simple objective classes so they accept kwargs like factories."""

    def factory_wrapper(**kwargs: Any) -> Any:  # pragma: no cover - simple wrapper
        min_trades = kwargs.get("min_trades", 10)
        return factory(min_trades=min_trades)

    return factory_wrapper


@dataclass(frozen=True)
class ParameterGroupSpec:
    """Specification for a reusable parameter group."""

    name: str
    builder: ParameterGroupBuilder
    parameter_names: frozenset[str]
    description: str = ""

    def apply(self, builder: ParameterSpaceBuilder) -> ParameterSpaceBuilder:
        """Apply the builder hook to the provided ParameterSpaceBuilder."""
        return self.builder(builder)


@dataclass(frozen=True)
class ObjectiveSpec:
    """Specification for an optimization objective."""

    name: str
    factory: Callable[..., Any]
    direction: str
    description: str = ""


DEFAULT_PARAMETER_GROUPS = ("strategy", "risk")
LEGACY_RISK_FALLBACK_PARAMETER_NAMES = frozenset(
    {
        "dry_run_equity_usd",
        "max_drawdown_pct",
        "max_position_size",
        "reduce_only_threshold",
        "trailing_stop_pct",
    }
)
# daily_loss_limit_pct: the BotRiskConfig alias was retired (#1120 stage 3);
# the runtime breaker now derives from the RiskBudget, so stored results
# carrying it are dropped instead of re-applied.
REMOVED_PARAMETER_NAMES = frozenset({"daily_loss_limit", "daily_loss_limit_pct"})


STRATEGY_GROUP = ParameterGroupSpec(
    name="strategy",
    builder=_strategy_builder,
    parameter_names=frozenset(param.name for param in strategy_parameter_space()),
    description="Strategy signal and execution parameters.",
)

RISK_GROUP = ParameterGroupSpec(
    name="risk",
    builder=_risk_builder,
    parameter_names=frozenset(param.name for param in risk_parameter_space()),
    description="Risk and leverage parameters.",
)

SIMULATION_GROUP = ParameterGroupSpec(
    name="simulation",
    builder=_simulation_builder,
    parameter_names=frozenset(param.name for param in simulation_parameter_space()),
    description="Simulation tuning parameters (fees/slippage).",
)


def _ensure_unique_spec_names(specs: Iterable[ParameterGroupSpec]) -> dict[str, ParameterGroupSpec]:
    registry: dict[str, ParameterGroupSpec] = {}
    duplicates: list[str] = []
    for spec in specs:
        if spec.name in registry:
            duplicates.append(spec.name)
        registry[spec.name] = spec
    if duplicates:
        raise ValueError(f"Duplicate parameter group keys in registry: {', '.join(duplicates)}")
    return registry


PARAMETER_GROUP_REGISTRY = _ensure_unique_spec_names([STRATEGY_GROUP, RISK_GROUP, SIMULATION_GROUP])


def list_parameter_group_names() -> tuple[str, ...]:
    """Return all registered parameter group names in deterministic order."""
    return tuple(PARAMETER_GROUP_REGISTRY.keys())


_parameter_to_group: dict[str, ParameterGroupSpec] = {}
for spec in PARAMETER_GROUP_REGISTRY.values():
    for name in spec.parameter_names:
        if name in _parameter_to_group:
            raise ValueError(f"Parameter '{name}' is registered in multiple groups")
        _parameter_to_group[name] = spec


OBJECTIVE_SPEC_ENTRIES = [
    ObjectiveSpec(
        name="sharpe", factory=_wrap_class_factory(SharpeRatioObjective), direction="maximize"
    ),
    ObjectiveSpec(
        name="sortino", factory=_wrap_class_factory(SortinoRatioObjective), direction="maximize"
    ),
    ObjectiveSpec(
        name="calmar", factory=_wrap_class_factory(CalmarRatioObjective), direction="maximize"
    ),
    ObjectiveSpec(
        name="total_return", factory=_wrap_class_factory(TotalReturnObjective), direction="maximize"
    ),
    ObjectiveSpec(
        name="win_rate", factory=_wrap_class_factory(WinRateObjective), direction="maximize"
    ),
    ObjectiveSpec(
        name="profit_factor",
        factory=_wrap_class_factory(ProfitFactorObjective),
        direction="maximize",
    ),
    ObjectiveSpec(
        name="max_drawdown", factory=_wrap_class_factory(MaxDrawdownObjective), direction="minimize"
    ),
    ObjectiveSpec(name="risk_averse", factory=create_risk_averse_objective, direction="maximize"),
    ObjectiveSpec(
        name="execution_quality", factory=create_execution_quality_objective, direction="maximize"
    ),
    ObjectiveSpec(
        name="time_efficient", factory=create_time_efficient_objective, direction="maximize"
    ),
    ObjectiveSpec(
        name="streak_resilient", factory=create_streak_resilient_objective, direction="maximize"
    ),
    ObjectiveSpec(name="perpetuals", factory=create_perpetuals_objective, direction="maximize"),
    ObjectiveSpec(
        name="tail_risk_aware", factory=create_tail_risk_aware_objective, direction="maximize"
    ),
]


def _ensure_unique_objectives(specs: Iterable[ObjectiveSpec]) -> dict[str, ObjectiveSpec]:
    registry: dict[str, ObjectiveSpec] = {}
    duplicates: list[str] = []
    for spec in specs:
        if spec.name in registry:
            duplicates.append(spec.name)
        registry[spec.name] = spec
    if duplicates:
        raise ValueError(f"Duplicate objective keys in registry: {', '.join(duplicates)}")
    return registry


OBJECTIVE_REGISTRY = _ensure_unique_objectives(OBJECTIVE_SPEC_ENTRIES)


def list_objective_names() -> tuple[str, ...]:
    """Return all registered objective names in deterministic order."""
    return tuple(OBJECTIVE_REGISTRY.keys())


def add_parameter_groups(
    builder: ParameterSpaceBuilder, groups: Iterable[str]
) -> ParameterSpaceBuilder:
    """Apply the requested parameter groups to the builder."""
    for group_name in groups:
        group = PARAMETER_GROUP_REGISTRY[group_name]
        builder = group.apply(builder)
    return builder


def categorize_parameters_by_group(
    parameters: dict[str, Any], *, fallback_group: str | None = None
) -> dict[str, dict[str, Any]]:
    """Categorize parameters by their registered group."""
    if fallback_group is None:
        fallback_group = DEFAULT_PARAMETER_GROUPS[0]
    grouped: dict[str, dict[str, Any]] = {name: {} for name in PARAMETER_GROUP_REGISTRY}

    for param_name, value in parameters.items():
        if param_name in REMOVED_PARAMETER_NAMES:
            continue
        group = _parameter_to_group.get(param_name)
        if group:
            target = group.name
        elif param_name in LEGACY_RISK_FALLBACK_PARAMETER_NAMES:
            target = RISK_GROUP.name
        else:
            target = fallback_group
        grouped[target][param_name] = value

    return grouped


def get_parameter_group(name: str) -> ParameterGroupSpec:
    """Return the spec for a parameter group."""
    return PARAMETER_GROUP_REGISTRY[name]


def has_parameter_group(name: str) -> bool:
    """Check if a group exists in the registry."""
    return name in PARAMETER_GROUP_REGISTRY


def get_objective_spec(name: str) -> ObjectiveSpec:
    """Return the spec for an objective."""
    return OBJECTIVE_REGISTRY[name]


def has_objective(name: str) -> bool:
    """Check if an objective exists in the registry."""
    return name in OBJECTIVE_REGISTRY

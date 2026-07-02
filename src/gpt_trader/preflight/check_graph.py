from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class PreflightCheckNode:
    name: str
    dependencies: tuple[str, ...] = ()


class PreflightCheckGraphError(ValueError):
    """Raised when the preflight check graph cannot be assembled."""


CORE_PREFLIGHT_CHECKS: tuple[PreflightCheckNode, ...] = (
    PreflightCheckNode("check_python_version"),
    PreflightCheckNode("check_dependencies", dependencies=("check_python_version",)),
    PreflightCheckNode("check_environment_variables", dependencies=("check_dependencies",)),
    PreflightCheckNode("check_api_connectivity", dependencies=("check_environment_variables",)),
    PreflightCheckNode("check_key_permissions", dependencies=("check_api_connectivity",)),
    PreflightCheckNode("check_risk_configuration", dependencies=("check_environment_variables",)),
    PreflightCheckNode(
        "check_pretrade_diagnostics",
        dependencies=(
            "check_api_connectivity",
            "check_key_permissions",
            "check_risk_configuration",
        ),
    ),
    PreflightCheckNode("check_test_suite", dependencies=("check_dependencies",)),
    PreflightCheckNode(
        "check_profile_configuration", dependencies=("check_environment_variables",)
    ),
    PreflightCheckNode("check_system_time", dependencies=("check_python_version",)),
    PreflightCheckNode("check_disk_space", dependencies=("check_system_time",)),
    PreflightCheckNode(
        "simulate_dry_run",
        dependencies=("check_profile_configuration", "check_risk_configuration"),
    ),
    PreflightCheckNode(
        "check_event_store_redaction", dependencies=("check_profile_configuration",)
    ),
    PreflightCheckNode(
        "check_trade_ideas_readiness", dependencies=("check_environment_variables",)
    ),
    PreflightCheckNode(
        "check_readiness_report",
        dependencies=("check_event_store_redaction", "check_trade_ideas_readiness"),
    ),
)


def assemble_preflight_check_graph(
    nodes: Sequence[PreflightCheckNode],
) -> tuple[PreflightCheckNode, ...]:
    if not nodes:
        return ()

    name_to_node: dict[str, PreflightCheckNode] = {}
    duplicate_names: list[str] = []
    for node in nodes:
        if node.name in name_to_node:
            duplicate_names.append(node.name)
            continue
        name_to_node[node.name] = node

    if duplicate_names:
        unique = ", ".join(sorted(set(duplicate_names)))
        raise PreflightCheckGraphError(f"Duplicate preflight check names: {unique}")

    missing_dependencies: list[tuple[str, str]] = []
    for node in nodes:
        for dependency in node.dependencies:
            if dependency not in name_to_node:
                missing_dependencies.append((node.name, dependency))

    if missing_dependencies:
        missing_dependencies.sort()
        details = ", ".join(
            f"{check_name} -> {dependency}" for check_name, dependency in missing_dependencies
        )
        raise PreflightCheckGraphError(f"Missing preflight check dependencies: {details}")

    order_index = {node.name: index for index, node in enumerate(nodes)}
    in_degree = {node.name: len(node.dependencies) for node in nodes}
    dependents: dict[str, list[str]] = {node.name: [] for node in nodes}
    for node in nodes:
        for dependency in node.dependencies:
            dependents[dependency].append(node.name)

    ready: list[tuple[int, str]] = [
        (order_index[name], name) for name, degree in in_degree.items() if degree == 0
    ]
    ready.sort()

    ordered: list[PreflightCheckNode] = []
    while ready:
        _, name = ready.pop(0)
        ordered.append(name_to_node[name])
        for dependent in dependents[name]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append((order_index[dependent], dependent))
                ready.sort()

    if len(ordered) != len(nodes):
        remaining = sorted(
            [name for name, degree in in_degree.items() if degree > 0],
            key=order_index.__getitem__,
        )
        raise PreflightCheckGraphError(
            "Preflight check graph has cyclic dependencies: " + ", ".join(remaining)
        )

    return tuple(ordered)


__all__ = [
    "CORE_PREFLIGHT_CHECKS",
    "PreflightCheckGraphError",
    "PreflightCheckNode",
    "assemble_preflight_check_graph",
]

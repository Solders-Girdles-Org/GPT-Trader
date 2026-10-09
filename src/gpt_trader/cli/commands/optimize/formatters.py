"""Output formatters for optimization CLI commands."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

COMPARISON_METRICS: tuple[tuple[str, str], ...] = (
    ("best_objective_value", "Best Objective Value"),
    ("total_trials", "Total Trials"),
    ("feasible_trials", "Feasible Trials"),
)


def format_run_summary_text(run: dict[str, Any]) -> str:
    """
    Format an optimization run summary for human-readable output.

    Args:
        run: Dictionary containing run data

    Returns:
        Formatted text string
    """
    lines = [
        "=" * 80,
        f"{'OPTIMIZATION STUDY: ' + run['study_name']:^80}",
        "=" * 80,
        "",
        f"Run ID: {run['run_id']}",
        f"Started: {_format_datetime(run['started_at'])}",
    ]

    if run.get("completed_at"):
        lines.append(f"Completed: {_format_datetime(run['completed_at'])}")

    lines.extend(
        [
            f"Total Trials: {run['total_trials']}",
            f"Feasible Trials: {run['feasible_trials']}",
            "",
        ]
    )

    if run.get("best_objective_value") is not None:
        lines.extend(
            [
                "-" * 40,
                "BEST TRIAL",
                "-" * 40,
                f"Objective Value: {run['best_objective_value']:.4f}",
                "",
                "Parameters:",
            ]
        )

        if run.get("best_parameters"):
            for key, value in sorted(run["best_parameters"].items()):
                lines.append(f"  {key}: {_format_value(value)}")

    lines.append("=" * 80)
    return "\n".join(lines)


def format_run_summary_json(run: dict[str, Any], compact: bool = False) -> str:
    """
    Format an optimization run summary as JSON.

    Args:
        run: Dictionary containing run data
        compact: If True, use compact JSON format

    Returns:
        JSON string
    """
    output = {
        "status": "completed" if run.get("completed_at") else "in_progress",
        "run_id": run["run_id"],
        "study_name": run["study_name"],
        "started_at": run["started_at"],
        "completed_at": run.get("completed_at"),
        "total_trials": run["total_trials"],
        "feasible_trials": run["feasible_trials"],
        "best_trial": None,
    }

    if run.get("best_objective_value") is not None:
        output["best_trial"] = {
            "objective_value": run["best_objective_value"],
            "parameters": run.get("best_parameters"),
        }

    if compact:
        return json.dumps(output, separators=(",", ":"))
    return json.dumps(output, indent=2)


def format_trials_text(
    trials: list[dict[str, Any]],
    limit: int = 10,
    show_all_params: bool = False,
) -> str:
    """
    Format a list of trials for human-readable output.

    Args:
        trials: List of trial dictionaries
        limit: Maximum number of trials to show
        show_all_params: If True, show all parameters

    Returns:
        Formatted text string
    """
    if not trials:
        return "No trials found."

    # Sort by objective value (descending for maximize)
    sorted_trials = sorted(
        trials,
        key=lambda t: t.get("objective_value", float("-inf")),
        reverse=True,
    )[:limit]

    lines = [
        f"Top {len(sorted_trials)} Trials:",
        "-" * 60,
        f"{'#':>4} {'Objective':>12} {'Feasible':>8} {'Duration':>10}",
        "-" * 60,
    ]

    for trial in sorted_trials:
        feasible = "Yes" if trial.get("is_feasible", False) else "No"
        duration = f"{trial.get('duration_seconds', 0):.1f}s"
        obj_val = trial.get("objective_value")
        obj_str = f"{obj_val:.4f}" if obj_val is not None else "N/A"

        lines.append(
            f"{trial.get('trial_number', '?'):>4} {obj_str:>12} {feasible:>8} {duration:>10}"
        )

        if show_all_params and trial.get("parameters"):
            for key, value in sorted(trial["parameters"].items()):
                lines.append(f"      {key}: {_format_value(value)}")

    return "\n".join(lines)


def format_trials_csv(trials: list[dict[str, Any]]) -> str:
    """
    Format trials as CSV.

    Args:
        trials: List of trial dictionaries

    Returns:
        CSV string
    """
    if not trials:
        return ""

    # Collect all parameter keys
    param_keys: set[str] = set()
    for trial in trials:
        if trial.get("parameters"):
            param_keys.update(trial["parameters"].keys())

    param_keys_sorted = sorted(param_keys)

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    header = ["trial_number", "objective_value", "is_feasible", "duration_seconds"]
    header.extend(param_keys_sorted)
    writer.writerow(header)

    # Rows
    for trial in trials:
        row = [
            trial.get("trial_number", ""),
            trial.get("objective_value", ""),
            trial.get("is_feasible", ""),
            trial.get("duration_seconds", ""),
        ]
        params = trial.get("parameters", {})
        for key in param_keys_sorted:
            row.append(params.get(key, ""))
        writer.writerow(row)

    return output.getvalue()


def format_run_list_text(runs: list[dict[str, Any]]) -> str:
    """
    Format a list of optimization runs.

    Args:
        runs: List of run summaries

    Returns:
        Formatted text string
    """
    if not runs:
        return "No optimization runs found."

    lines = [
        "Optimization Runs:",
        "-" * 70,
        f"{'Run ID':<24} {'Study Name':<20} {'Best Value':>12} {'Date':>12}",
        "-" * 70,
    ]

    for run in runs:
        best_val = run.get("best_value")
        best_str = f"{best_val:.4f}" if best_val is not None else "N/A"
        date_str = _format_date_short(run.get("started_at", ""))
        run_id = run.get("run_id", "")[:22]
        study_name = run.get("study_name", "")[:18]

        lines.append(f"{run_id:<24} {study_name:<20} {best_str:>12} {date_str:>12}")

    return "\n".join(lines)


def format_run_list_json(runs: list[dict[str, Any]], compact: bool = False) -> str:
    """
    Format a list of optimization runs as JSON.

    Args:
        runs: List of run summaries
        compact: If True, use compact JSON format

    Returns:
        JSON string
    """
    if compact:
        return json.dumps({"runs": runs}, separators=(",", ":"))
    return json.dumps({"runs": runs}, indent=2)


def format_comparison_text(comparison: dict[str, Any]) -> str:
    """
    Format a comparison payload for human-readable output.

    Args:
        comparison: Dictionary containing matrix and run metadata

    Returns:
        Formatted comparison text
    """
    runs = comparison.get("runs", [])
    matrix = comparison.get("matrix", [])
    baseline = comparison.get("baseline_run")
    best_run = comparison.get("best_run")

    if not runs:
        return "No runs to compare."

    if len(runs) == 1:
        return format_run_summary_text(runs[0])

    lines = [
        "=" * 80,
        f"{'OPTIMIZATION COMPARISON':^80}",
        "=" * 80,
        "",
    ]

    if baseline:
        baseline_label = f"{baseline['study_name']} ({baseline['run_id']})"
        lines.append(f"Baseline: {baseline_label}")
        if baseline.get("started_at"):
            lines.append(f"Started: {baseline['started_at']}")
        lines.append("")

    if best_run and best_run.get("objective_value") is not None:
        lines.append(
            f"Best objective: {best_run['objective_value']:.4f} (run {best_run['run_id']})"
        )
        lines.append("")

    lines.append("Metric matrix (values ± deltas vs baseline):")
    lines.append("")
    lines.extend(
        _format_comparison_matrix(matrix, runs, baseline.get("run_id") if baseline else None)
    )

    lines.append("")
    lines.append("Runs:")
    for run in runs:
        marker = " (baseline)" if baseline and run["run_id"] == baseline["run_id"] else ""
        lines.append(f"  - {run['study_name']} ({run['run_id']}){marker}")

    lines.append("")
    lines.append("=" * 80)
    return "\n".join(lines)


def format_comparison_json(comparison: dict[str, Any], compact: bool = False) -> str:
    """
    Format a comparison payload as JSON.

    Args:
        comparison: Comparison payload dictionary
        compact: If True, use compact JSON format

    Returns:
        JSON string
    """
    payload = {"comparison": comparison}
    if compact:
        return json.dumps(payload, separators=(",", ":"))
    return json.dumps(payload, indent=2)


def _format_comparison_matrix(
    matrix: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    baseline_run_id: str | None,
) -> list[str]:
    """Render the comparison matrix as an aligned table."""
    if not matrix or not runs:
        return ["No comparison metrics available."]

    header = ["Metric"]
    header.extend(_format_header_label(run, baseline_run_id) for run in runs)

    rows: list[list[str]] = []
    for matrix_row in matrix:
        entries: list[str] = [str(matrix_row.get("label", ""))]
        raw_values = matrix_row.get("values", [])
        entry_map: dict[str, dict[str, Any]] = {}
        if isinstance(raw_values, list):
            for raw_entry in raw_values:
                if not isinstance(raw_entry, dict):
                    continue
                run_key = str(raw_entry.get("run_id", ""))
                if run_key:
                    entry_map[run_key] = raw_entry
        for run in runs:
            run_id = str(run.get("run_id", ""))
            entry = entry_map.get(run_id, {"value": None, "delta": None})
            entry_value = _format_matrix_value(entry.get("value"))
            delta_str = _format_delta(entry.get("delta"))
            entries.append(f"{entry_value} (Δ {delta_str})")
        rows.append(entries)

    all_rows: list[list[str]] = [header] + rows
    col_widths: list[int] = []
    for col_idx in range(len(header)):
        max_width = max(
            len(row_cells[col_idx]) for row_cells in all_rows if len(row_cells) > col_idx
        )
        col_widths.append(max_width)

    formatted: list[str] = []
    header_line = " | ".join(
        header[col_idx].ljust(col_widths[col_idx]) for col_idx in range(len(header))
    )
    separator = "-+-".join("-" * width for width in col_widths)
    formatted.append(header_line)
    formatted.append(separator)

    for row_cells in rows:
        row_line = " | ".join(
            row_cells[col_idx].ljust(col_widths[col_idx]) for col_idx in range(len(header))
        )
        formatted.append(row_line)

    return formatted


def _format_header_label(run: dict[str, Any], baseline_run_id: str | None) -> str:
    """Create a column header label for a run."""
    run_id = str(run.get("run_id", ""))
    label = run_id
    if baseline_run_id and run_id == baseline_run_id:
        label += " (baseline)"
    return label


def _format_matrix_value(value: Any) -> str:
    """Format a matrix cell value."""
    if value is None:
        return "N/A"
    return _format_value(value)


def _format_delta(delta: Any | None) -> str:
    """Format a delta value with sign."""
    if delta is None:
        return "N/A"
    if isinstance(delta, int):
        return f"{delta:+d}"
    return f"{float(delta):+.4f}"


def _format_datetime(dt_str: str | None) -> str:
    """Format ISO datetime string for display."""
    if not dt_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(dt_str)


def _format_date_short(dt_str: str | None) -> str:
    """Format ISO datetime string as short date."""
    if not dt_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return str(dt_str)[:10]


def _format_value(value: Any) -> str:
    """Format a parameter value for display."""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)

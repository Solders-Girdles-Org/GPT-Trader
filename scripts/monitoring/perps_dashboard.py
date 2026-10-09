#!/usr/bin/env python3
"""
Coinbase Trader Metrics Dashboard

Surfaces Coinbase Trader metrics emitted via EventStore and health.json.
Uses 'rich' library for a professional TUI.

Usage:
  python scripts/monitoring/perps_dashboard.py --profile dev --refresh 1 --window-min 5
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.append(str(SRC_PATH))


def load_events(
    jsonl_path: Path,
    events_db: Path | None = None,
    max_lines: int = 5000,
) -> deque[dict]:
    events: deque[dict] = deque(maxlen=max_lines)
    if events_db is not None and events_db.exists():
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(str(events_db))
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                """
                SELECT timestamp, event_type, payload
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (max_lines,),
            )
            rows = list(cursor)
            for row in reversed(rows):
                try:
                    payload = json.loads(row["payload"])
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                event_type = str(row["event_type"] or "")
                event = dict(payload)
                event["type"] = event_type
                event["timestamp"] = row["timestamp"]
                events.append(event)
            if events:
                return events
        except Exception:
            events.clear()
        finally:
            if connection is not None:
                connection.close()
    if not jsonl_path.exists():
        return events
    try:
        with jsonl_path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    continue
                events.append(evt)
    except Exception:
        pass
    return events


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone()


def summarize(events: deque[dict], window: timedelta) -> dict:
    now = datetime.now().astimezone()
    cutoff = now - window
    success = 0
    failed = 0
    drift = 0
    pos_drift = 0

    for evt in events:
        ts = parse_time(evt.get("timestamp") or evt.get("time"))
        if ts is None or ts < cutoff:
            continue

        etype = str(evt.get("type") or evt.get("event_type") or "").lower()
        if etype == "order_success":
            success += 1
        elif etype == "order_failed":
            failed += 1
        elif etype == "order_drift":
            drift += 1
        elif etype == "position_drift":
            pos_drift += 1

    total = success + failed
    acceptance = (success / total * 100.0) if total > 0 else 0.0

    return {
        "success": success,
        "failed": failed,
        "total": total,
        "acceptance_rate": acceptance,
        "drift_events": drift,
        "position_drift_events": pos_drift,
        "recent_events": list(events)[
            -15:
        ],  # Get absolute last 15 events regardless of window for log
    }


def load_health(health_path: Path) -> dict:
    if not health_path.exists():
        return {"ok": False, "message": "health.json not found"}
    try:
        with health_path.open("r") as f:
            return json.load(f)
    except Exception as e:
        return {"ok": False, "message": f"error reading health: {e}"}


def load_metrics(metrics_path: Path, events_db: Path | None = None) -> dict:
    if metrics_path.exists():
        try:
            with metrics_path.open("r") as f:
                return json.load(f)
        except Exception:
            return {}
    if events_db is None or not events_db.exists():
        return {}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(events_db))
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(
            """
            SELECT payload FROM events
            WHERE event_type = ?
            ORDER BY id DESC
            LIMIT 250
            """,
            ("metric",),
        )
        fallback: dict | None = None
        for row in cursor:
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            metrics = payload.get("metrics")
            if not isinstance(metrics, dict):
                continue
            if fallback is None:
                fallback = metrics
            if metrics.get("event_type") in (None, "cycle_metrics"):
                return metrics
        return fallback or {}
    except Exception:
        return {}
    finally:
        if connection is not None:
            connection.close()


def make_header(profile: str, window_min: int) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    grid.add_row(
        "[b]🚀 Coinbase Trader Dashboard[/b]",
        f"[dim]Profile:[/dim] [cyan]{profile}[/cyan] | [dim]Window:[/dim] [cyan]{window_min}m[/cyan] | [dim]{datetime.now().strftime('%H:%M:%S')}[/dim]",
    )
    return Panel(grid, style="white on blue")


def make_health_panel(health: dict, metrics: dict) -> Panel:
    status_color = "green" if health.get("ok") else "red"
    status_icon = "✅" if health.get("ok") else "❌"

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()

    table.add_row(
        "Status",
        f"[{status_color}]{status_icon} {health.get('message', 'Unknown')}[/{status_color}]",
    )
    if health.get("error"):
        table.add_row("Error", f"[red]{health.get('error')}[/red]")
    table.add_row("Last Update", f"[dim]{health.get('timestamp', '')}[/dim]")

    # System Metrics
    sys_metrics = metrics.get("system", {})
    if sys_metrics:
        table.add_row("", "")
        table.add_row("[u]System Resources[/u]", "")

        cpu = sys_metrics.get("cpu_percent", 0)
        mem_pct = sys_metrics.get("memory_percent", 0)
        mem_mb = sys_metrics.get("memory_used_mb", 0)

        cpu_color = "green" if cpu < 50 else "yellow" if cpu < 80 else "red"
        mem_color = "green" if mem_pct < 50 else "yellow" if mem_pct < 80 else "red"

        table.add_row("CPU", f"[{cpu_color}]{cpu:.1f}%[/{cpu_color}]")
        table.add_row("Memory", f"[{mem_color}]{mem_pct:.1f}% ({mem_mb:.0f} MB)[/{mem_color}]")

    return Panel(table, title="System Health", border_style=status_color)


def make_orders_panel(summary: dict) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column(justify="right")

    total = summary["total"]
    success = summary["success"]
    failed = summary["failed"]
    rate = summary["acceptance_rate"]

    table.add_row("Total Orders", str(total))
    table.add_row("Successful", f"[green]{success}[/green]")
    table.add_row("Failed", f"[red]{failed}[/red]")

    rate_color = "green" if rate > 90 else "yellow" if rate > 50 else "red"
    table.add_row("Acceptance Rate", f"[{rate_color}]{rate:.1f}%[/{rate_color}]")

    table.add_row("", "")
    table.add_row("[u]Reconciliation[/u]", "")

    drift = summary["drift_events"]
    pos_drift = summary["position_drift_events"]

    drift_style = "green" if drift == 0 else "red"
    pos_drift_style = "green" if pos_drift == 0 else "red"

    table.add_row("Order Drift", f"[{drift_style}]{drift}[/{drift_style}]")
    table.add_row("Position Drift", f"[{pos_drift_style}]{pos_drift}[/{pos_drift_style}]")

    return Panel(table, title="Trading Metrics", border_style="cyan")


def make_events_panel(events: list[dict]) -> Panel:
    table = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    table.add_column("Time", style="dim", width=12)
    table.add_column("Type", style="bold", width=15)
    table.add_column("Details")

    for e in reversed(events):  # Show newest first
        t = parse_time(e.get("time") or e.get("timestamp"))
        t_str = t.strftime("%H:%M:%S") if t else ""

        etype = e.get("type", "")
        details = ""
        style = "white"

        sym = e.get("symbol") or e.get("product_id") or ""

        if etype == "order_success":
            style = "green"
            details = f"{e.get('side', '').upper()} {sym} {e.get('quantity', '')} @ {e.get('price', 'MKT')}"
        elif etype == "order_failed":
            style = "red"
            details = f"{sym} {e.get('reason', '')}"
        elif "drift" in etype:
            style = "yellow"
            details = f"local={e.get('local_count')} exch={e.get('exchange_count')}"
        elif etype == "alert":
            style = "red bold"
            details = e.get("message", "")
        else:
            details = str(e)

        table.add_row(t_str, Text(etype, style=style), details)

    return Panel(table, title="Recent Events", border_style="blue")


def main() -> None:
    parser = argparse.ArgumentParser(description="Coinbase Trader Metrics Dashboard")
    parser.add_argument("--profile", choices=["dev", "demo", "prod", "canary"], default="dev")
    parser.add_argument("--refresh", type=int, default=1, help="Refresh interval seconds")
    parser.add_argument("--window-min", type=int, default=5, help="Sliding window in minutes")
    args = parser.parse_args()

    # Load config and container to resolve paths consistently
    from gpt_trader.app.config import BotConfig
    from gpt_trader.app.container import create_application_container

    config = BotConfig(profile=args.profile)
    container = create_application_container(config)

    # Use container's runtime paths
    base_dir = container.runtime_paths.event_store_root
    events_db = base_dir / "events.db"
    events_path = base_dir / "events.jsonl"
    health_path = base_dir / "health.json"
    metrics_path = base_dir / "metrics.json"

    window = timedelta(minutes=args.window_min)

    layout = Layout()
    layout.split(Layout(name="header", size=3), Layout(name="body"), Layout(name="footer", size=15))
    layout["body"].split_row(Layout(name="left"), Layout(name="right"))

    with Live(layout, refresh_per_second=1):
        try:
            while True:
                events = load_events(events_path, events_db)
                summary = summarize(events, window)
                health = load_health(health_path)
                metrics = load_metrics(metrics_path, events_db)

                layout["header"].update(make_header(args.profile, args.window_min))
                layout["left"].update(make_health_panel(health, metrics))
                layout["right"].update(make_orders_panel(summary))
                layout["footer"].update(make_events_panel(summary["recent_events"]))

                time.sleep(args.refresh)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    sys.exit(main())

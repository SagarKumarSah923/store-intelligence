"""
live_dashboard.py - Terminal dashboard using Rich.
Shows real-time metrics updating as events flow in.
Run: python dashboard/live_dashboard.py --store STORE_PURPLLE_001 --api http://localhost:8000
"""

import argparse
import time
import sys
import httpx
from datetime import datetime

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich.columns import Columns
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console()


def fetch(api: str, path: str) -> dict:
    try:
        r = httpx.get(f"{api}{path}", timeout=5.0)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def make_metrics_panel(data: dict) -> Panel:
    if not data:
        return Panel("[red]No data[/red]", title="Metrics")
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Metric", style="cyan")
    t.add_column("Value",  style="bold white")
    t.add_row("👥 Unique Visitors",   str(data.get("unique_visitors", 0)))
    t.add_row("💳 Billing Visitors",  str(data.get("billing_visitors", 0)))
    t.add_row("📈 Conversion Rate",   f"{float(data.get('conversion_rate', 0))*100:.1f}%")
    t.add_row("🛒 Queue Depth",       str(data.get("queue_depth", 0)))
    t.add_row("🚶 Abandonment Rate",  f"{float(data.get('abandonment_rate', 0))*100:.1f}%")
    last = data.get("last_event_at", "-")
    t.add_row("🕐 Last Event",        str(last)[:19] if last else "-")
    return Panel(t, title="[bold green]📊 Live Store Metrics[/bold green]", border_style="green")


def make_funnel_panel(data: dict) -> Panel:
    if not data or "funnel" not in data:
        return Panel("[red]No funnel data[/red]", title="Funnel")
    t = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    t.add_column("Stage",     style="cyan")
    t.add_column("Visitors",  style="bold white", justify="right")
    t.add_column("Drop-off",  style="yellow",     justify="right")
    stage_icons = {"ENTRY": "🚪", "ZONE_VISIT": "🛍️", "BILLING_QUEUE": "🧾", "PURCHASE": "✅"}
    for stage in data["funnel"]:
        icon = stage_icons.get(stage["stage"], "·")
        drop = stage["drop_off_pct"]
        drop_str = f"{drop:.1f}%" if drop > 0 else "-"
        t.add_row(f"{icon} {stage['label']}", str(stage["visitors"]), drop_str)
    overall = data.get("overall_conversion_pct", 0)
    return Panel(t, title=f"[bold blue]🔽 Conversion Funnel  ({overall:.1f}% overall)[/bold blue]",
                 border_style="blue")


def make_heatmap_panel(data: dict) -> Panel:
    if not data or "zones" not in data:
        return Panel("[red]No heatmap data[/red]", title="Heatmap")
    t = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    t.add_column("Zone",       style="cyan")
    t.add_column("Visits",     justify="right")
    t.add_column("Avg Dwell",  justify="right")
    t.add_column("Heat",       justify="right")
    BARS = "▁▂▃▄▅▆▇█"
    for zone in data["zones"][:6]:
        score = int(zone.get("heat_score", 0))
        bar_idx = min(7, score // 13)
        bar = BARS[bar_idx] * 4
        color = "red" if score > 70 else "yellow" if score > 40 else "white"
        dwell_s = zone.get("avg_dwell_ms", 0) // 1000
        t.add_row(
            zone["zone_id"],
            str(zone.get("visit_count", 0)),
            f"{dwell_s}s",
            f"[{color}]{bar} {score}[/{color}]"
        )
    conf = data.get("data_confidence", "?")
    conf_color = "green" if conf == "HIGH" else "yellow"
    return Panel(t,
        title=f"[bold magenta]🗺️  Zone Heatmap  [confidence: [{conf_color}]{conf}[/{conf_color}]][/bold magenta]",
        border_style="magenta")


def make_anomalies_panel(data: dict) -> Panel:
    if not data or "anomalies" not in data:
        return Panel("[green]✅ No anomalies[/green]", title="Anomalies")
    anomalies = data["anomalies"]
    if not anomalies:
        return Panel("[green]✅ All systems normal[/green]",
                     title="[bold green]🔔 Anomalies[/bold green]", border_style="green")
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("", style="bold")
    t.add_column("")
    sev_colors = {"CRITICAL": "red", "WARN": "yellow", "INFO": "blue"}
    sev_icons  = {"CRITICAL": "🚨", "WARN": "⚠️",  "INFO": "ℹ️"}
    for a in anomalies[:5]:
        sev = a.get("severity", "INFO")
        col = sev_colors.get(sev, "white")
        icon = sev_icons.get(sev, "·")
        t.add_row(
            f"[{col}]{icon} {a['anomaly_type']}[/{col}]",
            a.get("description", "")[:55]
        )
    border = "red" if any(a["severity"] == "CRITICAL" for a in anomalies) else "yellow"
    return Panel(t,
        title=f"[bold red]🔔 Anomalies ({len(anomalies)})[/bold red]",
        border_style=border)


def make_header(store_id: str, refresh: int) -> Panel:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return Panel(
        f"[bold white]🏪 Purplle Store Intelligence  |  Store: [cyan]{store_id}[/cyan]"
        f"  |  🕐 {now}  |  🔄 refresh every {refresh}s[/bold white]",
        border_style="white"
    )


def run_dashboard(store_id: str, api: str, refresh: int):
    if not RICH_AVAILABLE:
        print("Rich not installed. Run: pip install rich")
        _simple_dashboard(store_id, api, refresh)
        return

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while True:
            metrics   = fetch(api, f"/stores/{store_id}/metrics")
            funnel    = fetch(api, f"/stores/{store_id}/funnel")
            heatmap   = fetch(api, f"/stores/{store_id}/heatmap")
            anomalies = fetch(api, f"/stores/{store_id}/anomalies")

            layout = Layout()
            layout.split_column(
                Layout(make_header(store_id, refresh), size=3),
                Layout(name="mid"),
                Layout(make_anomalies_panel(anomalies), size=10),
            )
            layout["mid"].split_row(
                Layout(make_metrics_panel(metrics)),
                Layout(make_funnel_panel(funnel)),
                Layout(make_heatmap_panel(heatmap)),
            )
            live.update(layout)
            time.sleep(refresh)


def _simple_dashboard(store_id: str, api: str, refresh: int):
    while True:
        metrics = fetch(api, f"/stores/{store_id}/metrics")
        print(f"\n=== {store_id} @ {datetime.now().strftime('%H:%M:%S')} ===")
        print(f"  Visitors:    {metrics.get('unique_visitors', 0)}")
        print(f"  Conversion:  {float(metrics.get('conversion_rate', 0))*100:.1f}%")
        print(f"  Queue:       {metrics.get('queue_depth', 0)}")
        time.sleep(refresh)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purplle Store Intelligence Live Dashboard")
    parser.add_argument("--store",   default="STORE_PURPLLE_001")
    parser.add_argument("--api",     default="http://localhost:8000")
    parser.add_argument("--refresh", type=int, default=5)
    args = parser.parse_args()
    run_dashboard(args.store, args.api, args.refresh)

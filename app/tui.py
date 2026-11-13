"""Live terminal dashboard for provisioning status."""

from __future__ import annotations

import time

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.state import AppState


def _render(state: AppState) -> Group:
    snapshot = state.snapshot()
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold cyan", no_wrap=True)
    summary.add_column()
    summary.add_row("Status", snapshot["status"])
    summary.add_row("Details", snapshot["message"])
    summary.add_row("Attempts", str(snapshot["attempts"]))
    summary.add_row("Availability domains", ", ".join(snapshot["availability_domains"]) or "Discovering")
    summary.add_row("Current domain", snapshot["current_ad"] or "—")
    summary.add_row("Last attempt", snapshot["last_attempt_at"] or "—")
    summary.add_row("Next retry", snapshot["next_retry_at"] or "—")
    summary.add_row("Instance OCID", snapshot["instance_id"] or "—")
    summary.add_row("Public IPv4", snapshot["public_ip"] or "—")

    logs = Table(box=box.SIMPLE, expand=True, show_header=True, header_style="bold")
    logs.add_column("Time", width=27, no_wrap=True)
    logs.add_column("Level", width=9, no_wrap=True)
    logs.add_column("Event", overflow="fold")
    for item in snapshot["logs"][-12:]:
        style = "red" if item["level"] == "ERROR" else "yellow" if item["level"] == "WARNING" else ""
        logs.add_row(item["timestamp"], Text(item["level"], style=style), item["message"])
    return Group(
        Panel(summary, title="OCI A1 Capacity Hunter", border_style="cyan"),
        Panel(logs, title="Recent activity", border_style="blue"),
        Text("Press Ctrl+C to stop. The web dashboard can pause or resume polling.", style="dim"),
    )


def run_tui(state: AppState) -> None:
    console = Console()
    with Live(_render(state), console=console, refresh_per_second=2, screen=True) as live:
        try:
            while not state.is_shutdown_requested():
                live.update(_render(state), refresh=True)
                if state.snapshot()["status"] in {"Provisioned", "Stopped", "Error"}:
                    time.sleep(0.5)
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            state.request_shutdown()

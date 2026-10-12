"""Diagnostics export for troubleshooting."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .controller import BridgeController


def export_diagnostics(controller: BridgeController) -> str:
    """Build a human-readable diagnostics report."""
    state = controller.state
    config = controller.config
    clients = controller.snapshot_clients()

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("Serial Bridge Manager — Diagnostics Report")
    lines.append("=" * 60)
    lines.append(f"Generated : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Bridge    : {'RUNNING' if state.running else 'STOPPED'}")
    lines.append(f"COM Port  : {config.com_port} @ {config.baud_rate}")
    lines.append(f"TCP       : {config.tcp_host}:{config.tcp_port}")
    lines.append(f"Status    : {state.status_text}")
    if state.last_error:
        lines.append(f"Last Error: {state.last_error}")
    lines.append(f"Clients   : {state.client_count}")
    for c in clients:
        lines.append(
            f"  - {c.name} ({c.kind}) from {c.addr}  [{c.color_name}]"
        )
    if state.started_at:
        elapsed = time.time() - state.started_at
        lines.append(f"Uptime    : {elapsed:.0f} s")
    lines.append("=" * 60)
    return "\n".join(lines)


def export_diagnostics_json(controller: BridgeController) -> str:
    """Build a JSON diagnostics payload."""
    state = controller.state
    config = controller.config
    clients = controller.snapshot_clients()

    return json.dumps(
        {
            "generated_at": time.time(),
            "bridge_running": state.running,
            "com_port": config.com_port,
            "baud_rate": config.baud_rate,
            "tcp_host": config.tcp_host,
            "tcp_port": config.tcp_port,
            "status_text": state.status_text,
            "last_error": state.last_error,
            "client_count": state.client_count,
            "clients": [
                {
                    "name": c.name,
                    "addr": c.addr,
                    "color": c.color_name,
                    "kind": c.kind,
                }
                for c in clients
            ],
            "uptime_seconds": (
                time.time() - state.started_at if state.started_at else 0
            ),
        },
        indent=2,
    )

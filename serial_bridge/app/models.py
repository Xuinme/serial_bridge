"""Domain models for the Serial Bridge Manager.

All state is represented as plain dataclasses so the UI layer
never touches the bridge internals directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BridgeConfig:
    """User-facing bridge configuration (persisted to settings.json)."""

    com_port: str = ""
    baud_rate: int = 115200
    tcp_host: str = "0.0.0.0"
    tcp_port: int = 9999
    newline_mode: str = "crlf"
    auto_reconnect: bool = False
    close_action: str = "ask"  # "ask" | "minimize_to_tray" | "exit"


@dataclass
class BridgeRuntimeState:
    """Live snapshot of the bridge process (updated by Controller)."""

    running: bool = False
    starting: bool = False
    status_text: str = "Stopped"
    client_count: int = 0
    started_at: float | None = None
    last_error: str | None = None


@dataclass
class ClientSnapshot:
    """Information about one connected TCP client."""

    client_id: int
    name: str
    addr: str
    color_name: str
    connected_at: float
    kind: str = "external"  # "external" | "builtin-telnet"


@dataclass
class BridgeEvent:
    """A single event pushed from the bridge thread to the UI event queue."""

    kind: str
    timestamp: float = field(default_factory=time.time)
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


# ── Well-known event kinds ──────────────────────────────────────────
# These are the structured events the UI can subscribe to:
#
#   bridge_starting
#   bridge_started
#   bridge_start_failed
#   bridge_runtime_failed payload: operation (read | write)
#   bridge_stopped
#   client_connected       payload: client_id, name, addr, color_name
#   client_disconnected    payload: client_id, name
#   client_count_changed   payload: count
#   log_message            message: log line
#   port_list_updated      payload: ports (list of PortInfo)
#   telnet_connected
#   telnet_disconnected
#   telnet_data            payload: data (bytes)
#   telnet_error           message: error text


@dataclass
class SessionConfig:
    """Configuration of a single multi-session entry (TASK-007)."""

    session_id: str
    name: str
    com_port: str
    baud_rate: int = 115200
    tcp_port: int = 9999
    newline_mode: str = "crlf"
    auto_reconnect: bool = False


#: Allowed workbench theme modes (TASK-010).  Only these exact lowercase
#: values are ever accepted from settings; anything else falls back to dark.
VALID_THEME_MODES = ("dark", "light")

#: Allowed workbench accent colour names (TASK-010).  Only these exact
#: lowercase values are accepted; the actual colours live in workbench_theme.
VALID_ACCENT_COLORS = ("blue", "teal", "green", "amber", "rose")

#: Defaults and accepted ranges for the four user-adjustable font groups.
FONT_SIZE_DEFAULTS = {
    "title_font_size": 16,
    "ui_font_size": 10,
    "helper_font_size": 9,
    "terminal_font_size": 10,
}
FONT_SIZE_RANGES = {
    "title_font_size": (14, 22),
    "ui_font_size": (9, 15),
    "helper_font_size": (8, 13),
    "terminal_font_size": (9, 18),
}


@dataclass
class AppSettings:
    """Version 2 persisted settings for multiple sessions.

    schema_version is fixed at 2.  sessions carries every session the user
    has configured, preserving its insertion order.  Appearance preferences
    are optional extensions of the same schema (no v3).
    """

    schema_version: int = 2
    close_action: str = "ask"
    active_session_id: str | None = None
    sessions: list[SessionConfig] = field(default_factory=list)
    theme_mode: str = "dark"
    accent_color: str = "blue"
    title_font_size: int = FONT_SIZE_DEFAULTS["title_font_size"]
    ui_font_size: int = FONT_SIZE_DEFAULTS["ui_font_size"]
    helper_font_size: int = FONT_SIZE_DEFAULTS["helper_font_size"]
    terminal_font_size: int = FONT_SIZE_DEFAULTS["terminal_font_size"]
    traffic_log_enabled: bool = False
    traffic_log_directory: str = ""


@dataclass
class SessionSnapshot:
    """A point-in-time view of one session (safe for the UI to render)."""

    session_id: str
    name: str
    com_port: str
    baud_rate: int
    tcp_port: int
    newline_mode: str
    auto_reconnect: bool
    running: bool
    starting: bool
    status_text: str
    client_count: int
    last_error: str | None


@dataclass
class SessionEvent:
    """A single session-scoped event surfaced to the UI layer."""

    session_id: str
    kind: str
    timestamp: float
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

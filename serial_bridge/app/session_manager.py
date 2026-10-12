"""Multi-session manager (TASK-007).

SessionManager owns one BridgeController per session and exposes a stable,
session_id-scoped API for the UI.  A controller_factory may be injected so
tests can drive the manager without opening real COM ports or binding real
TCP listeners.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Callable

from .controller import BridgeController
from .models import (
    AppSettings,
    BridgeConfig,
    SessionConfig,
    SessionEvent,
    SessionSnapshot,
)
from .tcp_ports import TcpPortUnavailableError, is_tcp_port_available

#: Signature for a factory that turns a SessionConfig into a controller.
#: Tests inject a fake to avoid real serial/TCP resources.  This is the only
#: extension point the manager exposes; it never touches the bridge.
ControllerFactory = Callable[[SessionConfig], BridgeController]


class SessionManager:
    """Owns one controller per session and exposes session-scoped APIs.

    All public methods route through session_id; unknown ids raise KeyError,
    invalid or duplicated config raises ValueError, and repeated
    start/stop/shutdown are idempotent.
    """

    def __init__(self, controller_factory: ControllerFactory | None = None, *,
                 traffic_log_enabled: bool = False,
                 traffic_log_directory: str = "",
                 tcp_port_available: Callable[[int], bool] | None = None):
        self._factory = controller_factory or self._default_factory
        self._traffic_log_enabled = bool(traffic_log_enabled)
        self._traffic_log_directory = str(traffic_log_directory or "")
        self._tcp_port_available = tcp_port_available or is_tcp_port_available
        #: Stable-ordered per-session controller registry (insertion order).
        self._controllers: dict[str, BridgeController] = {}
        #: Authoritative per-session config; the controller config is derived
        #: from this whenever a controller is created or replaced.
        self._configs: dict[str, SessionConfig] = {}

    # -- default factory --------------------------------------------------

    def _default_factory(self, config: SessionConfig) -> BridgeController:
        """Build a real BridgeController from a session config.

        tcp_host is intentionally hard-coded to 0.0.0.0, matching the current
        SerialBridge behaviour, so this task does not create a configurable
        listener-address illusion.
        """
        bridge_cfg = BridgeConfig(
            com_port=config.com_port,
            baud_rate=config.baud_rate,
            tcp_host="0.0.0.0",
            tcp_port=config.tcp_port,
            newline_mode=config.newline_mode,
            auto_reconnect=config.auto_reconnect,
        )
        return BridgeController(
            bridge_cfg,
            session_id=config.session_id,
            session_name=config.name,
            traffic_log_enabled=self._traffic_log_enabled,
            traffic_log_directory=self._traffic_log_directory,
        )

    # -- lookup helpers ---------------------------------------------------

    def _require(self, session_id: str) -> BridgeController:
        try:
            return self._controllers[session_id]
        except KeyError:
            raise KeyError(f"Unknown session: {session_id!r}") from None

    def _validate_config(self, config: SessionConfig, exclude_id: str | None = None) -> None:
        """Reject invalid fields and in-app COM/TCP port duplicates.

        exclude_id skips the session being updated so it may keep its own
        COM/TCP port while changing other fields.
        """
        if not config.com_port or not str(config.com_port).strip():
            raise ValueError("com_port must not be empty")
        if isinstance(config.baud_rate, bool) or not isinstance(config.baud_rate, int) or config.baud_rate <= 0:
            raise ValueError("baud_rate must be a positive integer")
        if isinstance(config.tcp_port, bool) or not isinstance(config.tcp_port, int) or not (1 <= config.tcp_port <= 65535):
            raise ValueError("tcp_port must be in range 1-65535")

        for sid, other in self._configs.items():
            if sid == exclude_id:
                continue
            if other.com_port.casefold() == config.com_port.casefold():
                raise ValueError(
                    f"COM port {config.com_port!r} is already used by session {sid!r}"
                )
            if other.tcp_port == config.tcp_port:
                raise ValueError(
                    f"TCP port {config.tcp_port} is already used by session {sid!r}"
                )

    # -- public API -------------------------------------------------------

    def list_sessions(self) -> list[SessionSnapshot]:
        """Return a snapshot for every session, in creation order."""
        return [self.get_snapshot(sid) for sid in self._controllers]

    def get_snapshot(self, session_id: str) -> SessionSnapshot:
        """Return a snapshot for a session; raises KeyError if unknown."""
        controller = self._require(session_id)
        cfg = self._configs[session_id]
        state = controller.state
        return SessionSnapshot(
            session_id=session_id,
            name=cfg.name,
            com_port=cfg.com_port,
            baud_rate=cfg.baud_rate,
            tcp_port=cfg.tcp_port,
            newline_mode=cfg.newline_mode,
            auto_reconnect=cfg.auto_reconnect,
            running=state.running,
            starting=state.starting,
            status_text=state.status_text,
            client_count=state.client_count,
            last_error=state.last_error,
        )

    def get_controller(self, session_id: str) -> BridgeController:
        """Return the live controller for a session."""
        return self._require(session_id)

    def create_session(self, config: SessionConfig) -> str:
        """Create a session; empty session_id is auto-assigned a UUID.

        Returns the chosen session id.  Raises ValueError on invalid fields
        or a duplicate id/COM/port.
        """
        session_id = config.session_id
        if session_id is None or not str(session_id).strip():
            session_id = uuid.uuid4().hex
        else:
            session_id = str(session_id).strip()
        if session_id in self._controllers:
            raise ValueError(f"Session id {session_id!r} already exists")

        com_port = "" if config.com_port is None else str(config.com_port).strip()
        config = dataclasses.replace(
            config,
            session_id=session_id,
            com_port=com_port,
        )
        self._validate_config(config, exclude_id=None)
        controller = self._factory(config)
        configure_logging = getattr(controller, "configure_traffic_logging", None)
        if configure_logging is not None:
            configure_logging(
                self._traffic_log_enabled, self._traffic_log_directory)
        self._controllers[session_id] = controller
        self._configs[session_id] = config
        return session_id

    def update_session(self, session_id: str, config: SessionConfig) -> None:
        """Replace the config of a stopped session.

        Raises KeyError for unknown ids and ValueError for invalid fields,
        duplicate COM/port (excluding this session), or a running session.  A
        running session cannot be modified, so the session is recreated with
        the new config so its controller reflects it.
        """
        controller = self._require(session_id)
        if controller.state.running or controller.state.starting:
            raise ValueError(f"Cannot modify running session {session_id!r}")

        com_port = "" if config.com_port is None else str(config.com_port).strip()
        config = dataclasses.replace(
            config,
            session_id=session_id,
            com_port=com_port,
        )
        self._validate_config(config, exclude_id=session_id)
        new_controller = self._factory(config)
        configure_logging = getattr(new_controller, "configure_traffic_logging", None)
        if configure_logging is not None:
            configure_logging(
                self._traffic_log_enabled, self._traffic_log_directory)
        self._controllers[session_id] = new_controller
        self._configs[session_id] = config

    def next_tcp_port(self, start: int = 9999,
                      exclude_id: str | None = None) -> int:
        """Return the first app- and OS-available TCP port at/after start."""
        if isinstance(start, bool) or not isinstance(start, int):
            start = 9999
        used = {
            cfg.tcp_port for sid, cfg in self._configs.items()
            if sid != exclude_id
        }
        port = max(1, start)
        while port <= 65535:
            if port not in used and self._tcp_port_available(port):
                return port
            port += 1
        raise ValueError("No free TCP port available in range 1-65535")

    def ensure_tcp_port_available(self, port: int,
                                  exclude_id: str | None = None) -> None:
        """Reject an OS-owned listener port and include a usable suggestion."""
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
            raise ValueError("tcp_port must be in range 1-65535")
        if self._tcp_port_available(port):
            return
        try:
            suggestion = self.next_tcp_port(9999, exclude_id=exclude_id)
        except ValueError:
            suggestion = None
        raise TcpPortUnavailableError(port, suggestion)

    def start_session(self, session_id: str) -> None:
        """Start a session; idempotent (no-op if already starting/running)."""
        self._require(session_id).start_bridge()

    def configure_traffic_logging(self, enabled: bool, directory: str) -> None:
        """Apply global traffic logging settings to current and future sessions."""
        self._traffic_log_enabled = bool(enabled)
        self._traffic_log_directory = str(directory or "")
        for controller in self._controllers.values():
            configure = getattr(controller, "configure_traffic_logging", None)
            if configure is not None:
                configure(self._traffic_log_enabled, self._traffic_log_directory)

    def stop_session(self, session_id: str) -> None:
        """Stop a session; idempotent."""
        controller = self._require(session_id)
        if not (controller.state.running or controller.state.starting):
            return
        if controller.state.status_text.casefold() == "stopping":
            return
        controller.stop_bridge()

    def delete_session(self, session_id: str) -> None:
        """Delete a session; a running session is shut down first."""
        controller = self._require(session_id)
        if controller.state.running or controller.state.starting:
            controller.shutdown()
        self._controllers.pop(session_id, None)
        self._configs.pop(session_id, None)

    def drain_events(self) -> list[SessionEvent]:
        """Drain each controller event bus into session-scoped events.

        Each controller events keep their relative order; ordering across
        different sessions is not guaranteed.
        """
        events: list[SessionEvent] = []
        for session_id, controller in self._controllers.items():
            for bridge_event in controller.event_bus.drain():
                events.append(
                    SessionEvent(
                        session_id=session_id,
                        kind=bridge_event.kind,
                        timestamp=bridge_event.timestamp,
                        message=bridge_event.message,
                        payload=dict(bridge_event.payload),
                    )
                )
        return events

    def export_settings(self, close_action: str, active_session_id: str | None) -> AppSettings:
        """Build a version 2 AppSettings from the current sessions."""
        return AppSettings(
            schema_version=2,
            close_action=close_action,
            active_session_id=active_session_id,
            sessions=[dataclasses.replace(c) for c in self._configs.values()],
        )

    def shutdown_all(self) -> None:
        """Shut down every session, continuing past any single failure."""
        errors: list[str] = []
        for session_id in list(self._controllers):
            try:
                self._controllers[session_id].shutdown()
            except Exception as exc:
                errors.append(f"{session_id}: {exc}")
        if errors:
            raise RuntimeError(
                "shutdown_all failed for: " + "; ".join(errors)
            )

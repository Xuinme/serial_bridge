"""Bridge Controller — the single source of truth for bridge lifecycle.

Owns the background thread, the asyncio event loop, and the
``SerialBridge`` instance.  Pushes structured ``BridgeEvent``
instances onto the ``EventBus`` so the UI never touches the bridge
internals directly.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

from bridge import SerialBridge

from .events import EventBus
from .models import BridgeConfig, BridgeEvent, BridgeRuntimeState, ClientSnapshot
from .traffic_log import TrafficLogWriter


class BridgeController:
    """Manages the bridge lifecycle on a dedicated background thread.

    Usage from the UI thread::

        ctrl = BridgeController(BridgeConfig(com_port="COM9", baud_rate=115200))
        ctrl.start_bridge()
        # ... later, from root.after():
        for event in ctrl.event_bus.drain():
            handle(event)
        ctrl.stop_bridge()
        ctrl.shutdown()
    """

    def __init__(self, config: BridgeConfig | None = None, *,
                 session_id: str = "", session_name: str = "",
                 traffic_log_enabled: bool = False,
                 traffic_log_directory: str = ""):
        self.config = config or BridgeConfig()
        self.state = BridgeRuntimeState()
        self.event_bus = EventBus()

        # Internal handles
        self._bridge: SerialBridge | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: dict[int, ClientSnapshot] = {}
        self._lock = threading.Lock()
        self._session_id = session_id or "session"
        self._session_name = session_name or self._session_id
        self._traffic_log = TrafficLogWriter(
            enabled=traffic_log_enabled,
            directory=traffic_log_directory,
            on_error=self._on_traffic_log_error,
        )

    # ── Public API (call from UI thread) ─────────────────────────

    def start_bridge(self) -> None:
        """Launch the bridge on a background thread.

        Idempotent: if already running or starting this is a no-op.
        """
        if self.state.running or self.state.starting:
            return

        self.state.starting = True
        self.state.status_text = "Starting"
        self.state.last_error = None
        self._traffic_log.start_run(
            session_id=self._session_id,
            session_name=self._session_name,
            com_port=self.config.com_port,
            baud_rate=self.config.baud_rate,
            tcp_port=self.config.tcp_port,
        )
        self.event_bus.push(BridgeEvent(kind="bridge_starting"))

        self._thread = threading.Thread(
            target=self._run_bridge, daemon=True, name="bridge-thread"
        )
        self._thread.start()

    def stop_bridge(self) -> None:
        """Signal the bridge to stop (non-blocking).

        Safe to call from the UI thread.  The actual teardown happens
        on the bridge thread; a ``bridge_stopped`` event will be
        pushed when it completes.
        """
        self.state.starting = False
        if self.state.running:
            self.state.status_text = "Stopping"
        bridge = self._bridge
        if bridge is not None:
            bridge.running = False
        self.event_bus.push(BridgeEvent(kind="bridge_stopping"))

    def shutdown(self) -> None:
        """Stop bridge and wait for background thread to exit.

        Call this when the application window is closing.
        """
        self.stop_bridge()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=5)
        self.state.running = False
        self.state.starting = False
        self._thread = None
        self._loop = None
        self._bridge = None
        self._traffic_log.stop()

    def configure_traffic_logging(self, enabled: bool, directory: str) -> None:
        """Apply logging settings immediately without restarting the bridge."""
        self._traffic_log.configure(enabled, directory)
        if enabled and (self.state.running or self.state.starting):
            self._traffic_log.start_run(
                session_id=self._session_id,
                session_name=self._session_name,
                com_port=self.config.com_port,
                baud_rate=self.config.baud_rate,
                tcp_port=self.config.tcp_port,
            )

    def snapshot_clients(self) -> list[ClientSnapshot]:
        """Return a copy of the current client list (thread-safe)."""
        with self._lock:
            return list(self._clients.values())

    # ── Bridge event callbacks (called from asyncio thread) ──────

    def _on_bridge_status(self, status: str) -> None:
        self.event_bus.push(BridgeEvent(kind="log_message", message=status))

    def _on_bridge_log(self, message: str) -> None:
        self.event_bus.push(BridgeEvent(kind="log_message", message=message))

    def _on_bridge_traffic(self, direction: str, data: bytes, source: str) -> None:
        self._traffic_log.record(direction, data, source)

    def _on_traffic_log_error(self, message: str) -> None:
        self.event_bus.push(BridgeEvent(kind="log_message", message=message))

    def _on_bridge_event(self, event: dict) -> None:
        """Handle structured events from SerialBridge."""
        kind = event.get("kind", "")
        ts = event.get("timestamp", time.time())

        if kind == "client_connected":
            cid = event["client_id"]
            name = event.get("name", f"Client-{cid + 1}")
            addr = event.get("addr", "?")
            color_name = event.get("color_name", "")
            snap = ClientSnapshot(
                client_id=cid,
                name=name,
                addr=addr,
                color_name=color_name,
                connected_at=ts,
                kind="external",
            )
            with self._lock:
                self._clients[cid] = snap
                count = len(self._clients)
            self.state.client_count = count
            self.event_bus.push(
                BridgeEvent(
                    kind="client_connected",
                    message=f"{name} connected from {addr}",
                    payload={
                        "client_id": cid,
                        "name": name,
                        "addr": addr,
                        "color_name": color_name,
                    },
                )
            )
            self.event_bus.push(
                BridgeEvent(
                    kind="client_count_changed",
                    payload={"count": count},
                )
            )

        elif kind == "client_disconnected":
            cid = event["client_id"]
            name = event.get("name", f"Client-{cid + 1}")
            with self._lock:
                self._clients.pop(cid, None)
                count = len(self._clients)
            self.state.client_count = count
            self.event_bus.push(
                BridgeEvent(
                    kind="client_disconnected",
                    message=f"{name} disconnected",
                    payload={"client_id": cid, "name": name},
                )
            )
            self.event_bus.push(
                BridgeEvent(
                    kind="client_count_changed",
                    payload={"count": count},
                )
            )

        elif kind == "bridge_started":
            self.state.running = True
            self.state.starting = False
            self.state.started_at = ts
            self.state.status_text = "Running"
            self.event_bus.push(
                BridgeEvent(
                    kind="bridge_started",
                    message=event.get("message", "Bridge started"),
                )
            )

        elif kind == "bridge_start_failed":
            self.state.running = False
            self.state.starting = False
            self.state.last_error = event.get("message", "Unknown error")
            self.state.status_text = "Start failed"
            self.event_bus.push(
                BridgeEvent(
                    kind="bridge_start_failed",
                    message=event.get("message", "Bridge start failed"),
                )
            )

        elif kind == "bridge_runtime_failed":
            message = event.get("message", "Bridge stopped after runtime failure")
            self.state.running = False
            self.state.starting = False
            self.state.last_error = message
            self.state.status_text = "Stopped"
            self.state.started_at = None
            with self._lock:
                self._clients.clear()
            self.state.client_count = 0
            self.event_bus.push(
                BridgeEvent(
                    kind="bridge_runtime_failed",
                    message=message,
                    payload={"operation": event.get("operation", "")},
                )
            )

        elif kind == "bridge_stopped":
            self.state.running = False
            self.state.starting = False
            self.state.status_text = "Stopped"
            self.state.started_at = None
            with self._lock:
                self._clients.clear()
            self.state.client_count = 0
            self.event_bus.push(
                BridgeEvent(
                    kind="bridge_stopped",
                    message=event.get("message", "Bridge stopped"),
                )
            )

    # ── Background thread ────────────────────────────────────────

    def _run_bridge(self) -> None:
        """Entry point for the background thread."""
        bridge = SerialBridge(
            com_port=self.config.com_port,
            baud_rate=self.config.baud_rate,
            tcp_port=self.config.tcp_port,
            status_callback=self._on_bridge_status,
            log_callback=self._on_bridge_log,
            event_callback=self._on_bridge_event,
            traffic_callback=self._on_bridge_traffic,
        )
        self._bridge = bridge

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        try:
            ok = loop.run_until_complete(bridge.start())
            if not ok:
                return
            # Keep alive until stopped
            while bridge.running:
                loop.run_until_complete(asyncio.sleep(0.2))
        except Exception as exc:
            self._on_bridge_event({
                "kind": "bridge_start_failed",
                "message": str(exc),
            })
        finally:
            if bridge.running or bridge.server or bridge.serial_port:
                try:
                    loop.run_until_complete(bridge.stop())
                except Exception:
                    pass
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._bridge = None
            self._traffic_log.stop()

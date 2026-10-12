"""Fake SerialBridge used by BridgeController tests.

``BridgeController`` imports ``SerialBridge`` from :mod:`bridge` and
constructs it inside its background thread.  To test the controller
without opening a real COM port (or relying on a nonexistent port name
to fail), tests replace ``app.controller.SerialBridge`` with this fake
and drive two deterministic modes:

* ``mode == "success"`` — ``start()`` flips ``running`` on and emits
  ``bridge_started``; ``stop()`` emits ``bridge_stopped``.
* ``mode == "fail"`` — ``start()`` emits ``bridge_start_failed`` and
  returns False; no server/serial handles are held.

The controller passes ``status_callback`` / ``log_callback`` /
``event_callback`` into the constructor; the fake records them so it can
push structured events back onto the controller's event bus.
"""

from __future__ import annotations

import asyncio
import time


class FakeBridge:
    """In-memory stand-in for :class:`bridge.SerialBridge`."""

    #: Default behaviour for instances constructed by the controller.
    mode: str = "success"

    def __init__(self, com_port: str = "", baud_rate: int = 115200,
                 tcp_port: int = 9999, status_callback=None,
                 log_callback=None, event_callback=None, traffic_callback=None):
        self.com_port = com_port
        self.baud_rate = baud_rate
        self.tcp_port = tcp_port
        self._on_status = status_callback or (lambda m: None)
        self._on_log = log_callback or (lambda m: None)
        self._on_event = event_callback or (lambda e: None)
        self._on_traffic = traffic_callback or (lambda _direction, _data, _source: None)

        self.mode = FakeBridge.mode
        self.running = False
        self.server = None
        self.serial_port = None

        self.start_calls = 0
        self.stop_calls = 0

    def _emit(self, kind: str, message: str, **extra) -> None:
        payload = {"kind": kind, "message": message, "timestamp": time.time()}
        payload.update(extra)
        self._on_event(payload)

    async def start(self) -> bool:
        """Emulate a successful or failed bridge start."""
        self.start_calls += 1
        if self.mode == "fail":
            self._emit("bridge_start_failed", "mock start failure")
            self.running = False
            return False
        self.server = object()
        self.serial_port = object()
        self.running = True
        self._emit("bridge_started", "mock bridge started")
        if self.mode == "runtime_fail":
            asyncio.get_running_loop().call_later(0.15, self._fail_runtime)
        return True

    def _fail_runtime(self) -> None:
        if not self.running:
            return
        self.running = False
        self._emit("bridge_runtime_failed", "mock serial disconnected")

    async def stop(self) -> None:
        """Emulate a clean bridge stop."""
        self.stop_calls += 1
        self.running = False
        self.server = None
        self.serial_port = None
        self._emit("bridge_stopped", "mock bridge stopped")

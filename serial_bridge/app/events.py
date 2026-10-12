"""Thread-safe event bus for bridge → UI communication.

Background threads push ``BridgeEvent`` instances into a
``queue.Queue``; the UI thread drains them periodically via
``root.after()``.
"""

from __future__ import annotations

import queue
from typing import Callable

from .models import BridgeEvent

# Event handlers receive a BridgeEvent and return None.
EventHandler = Callable[[BridgeEvent], None]


class EventBus:
    """Simple single-consumer event queue.

    Usage::

        bus = EventBus()
        bus.push(BridgeEvent(kind="bridge_started", message="..."))
        bus.drain(on_event)  # call periodically from the UI thread
    """

    def __init__(self, maxsize: int = 512):
        self._q: queue.Queue[BridgeEvent] = queue.Queue(maxsize=maxsize)
        self._handlers: dict[str, list[EventHandler]] = {}

    def push(self, event: BridgeEvent) -> None:
        """Enqueue an event (thread-safe)."""
        try:
            self._q.put_nowait(event)
        except queue.Full:
            pass  # drop oldest? For now just drop new events silently

    def drain(self) -> list[BridgeEvent]:
        """Pull ALL pending events out of the queue.

        Returns them as a list so the UI can batch-process.
        """
        events: list[BridgeEvent] = []
        while True:
            try:
                events.append(self._q.get_nowait())
            except queue.Empty:
                break
        return events

    def clear(self) -> None:
        """Discard all queued events."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

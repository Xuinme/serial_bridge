"""Built-in Telnet Client — lightweight TCP client for the terminal window.

Connects to the bridge's TCP server (``127.0.0.1:<port>``) and
behaves like any other bridge client.  IAC filtering is done here
so the terminal display never sees negotiation bytes.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from bridge import IAC, WILL, WONT, DO, DONT, SB, SE, ECHO, SGA, TelnetFilter

# Callback signatures:
#   on_data(bytes)       — payload received from bridge (IAC-filtered)
#   on_connected()        — socket connected
#   on_disconnected(str)  — socket closed, with reason
#   on_error(str)         — non-fatal error


class TelnetClient:
    """A minimal raw-TCP client that strips telnet IAC and exposes
    callbacks for the terminal UI.

    Runs a recv loop on its own daemon thread so the UI stays
    responsive.

    Usage::

        client = TelnetClient(host="127.0.0.1", port=9999)
        client.on_data = lambda data: show_in_terminal(data)
        client.connect()
        client.send(b"help\\r\\n")
        client.disconnect()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9999):
        self.host = host
        self.port = port

        # Callbacks (set by UI layer)
        self.on_data: Callable[[bytes], None] = lambda _: None
        self.on_connected: Callable[[], None] = lambda: None
        self.on_disconnected: Callable[[str], None] = lambda _: None
        self.on_error: Callable[[str], None] = lambda _: None

        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._closed_by_user = False

    # ── Public API ───────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._sock is not None and self._running

    def connect(self) -> bool:
        """Open socket and start recv loop. Returns True on success."""
        if self._running:
            return True
        self._closed_by_user = False
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5)
            self._sock.connect((self.host, self.port))
            self._sock.settimeout(0.3)  # short timeout for recv loop
        except OSError as exc:
            self._sock = None
            self.on_error(f"Failed to connect: {exc}")
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._recv_loop, daemon=True, name="telnet-client"
        )
        self._thread.start()
        self.on_connected()
        return True

    def disconnect(self) -> None:
        """Close socket and stop recv loop."""
        was_running = self._running
        self._closed_by_user = True
        self._running = False
        sock = self._sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
            self._sock = None
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2)
        self._thread = None
        if was_running:
            self.on_disconnected("Closed by user")

    def send(self, data: bytes) -> None:
        """Send raw bytes to the bridge (thread-safe)."""
        sock = self._sock
        if sock is None:
            return
        try:
            sock.sendall(data)
        except OSError as exc:
            self.on_error(f"Send error: {exc}")
            self.disconnect()

    # ── Recv loop ────────────────────────────────────────────────

    def _recv_loop(self) -> None:
        """Background thread: read from socket, strip IAC, push to UI."""
        tfilter = TelnetFilter(on_negotiation=self._on_telnet)
        buf = bytearray()

        while self._running:
            sock = self._sock
            if sock is None:
                break
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                if self._running:
                    self.on_error(f"Recv error: {exc}")
                break

            if not chunk:
                # EOF — server closed connection
                break

            # Feed through telnet filter
            clean = tfilter.feed(chunk)
            if clean:
                self.on_data(clean)

        # Signal disconnect if we exited the loop unexpectedly
        if self._running:
            self._running = False
            self.on_disconnected("Connection lost")

    @staticmethod
    def _on_telnet(cmd: int, option: int) -> None:
        """Minimal telnet negotiation: accept server's WILL ECHO/SGA."""
        # The TelnetClient is a passive receiver — the bridge's
        # TelnetFilter already handles most negotiation. Here we
        # just don't crash on incoming negotiations.
        pass

"""
Serial Port Bridge - Core Engine
=================================
Shares a physical COM port over TCP with multi-client support.
Client input is written only to the serial port; every client receives
the serial device's real output, broadcast to all clients.  Printable
device echo that matches a client's recent input is colored by that
client's source color using ANSI escape codes; all other device output
is shown in the terminal's default color.  The bridge never fabricates a
local input echo.

Architecture:
  COM port <---> [SerialBridge] <--TCP:9999---> Client-1 (telnet)
                                              Client-2 (telnet)
                                              AI Agent  (raw TCP)
"""

import asyncio
import logging
import time
from collections import OrderedDict, deque

import serial

from app.tcp_ports import create_listener_socket

logger = logging.getLogger(__name__)

# ── ANSI Color Palette ──────────────────────────────────────────────
# Each connecting client gets a unique color so you can tell who sent what.
CLIENT_COLORS = [
    "\033[0;33m",  # Yellow
    "\033[0;36m",  # Cyan
    "\033[0;35m",  # Magenta
    "\033[0;34m",  # Blue
    "\033[0;37m",  # White
    "\033[0;91m",  # Bright Red
    "\033[0;92m",  # Bright Green
    "\033[0;94m",  # Bright Blue
]
COM_COLOR = "\033[0;32m"  # Green — reserved for COM port data
RESET = "\033[0m"

# ── Telnet Protocol Constants ───────────────────────────────────────
IAC = 255  # Interpret As Command
WILL = 251
WONT = 252
DO = 253
DONT = 254
SB = 250  # Subnegotiation Begin
SE = 240  # Subnegotiation End
EC = 247  # Erase Character
ECHO = 1
SGA = 3  # Suppress Go Ahead
BACKSPACE_BYTES = bytes((8, 127))  # BS, DEL

# ── Patch 9: Device Echo Attribution ─────────────────────────────────
ATTRIBUTION_TIMEOUT = 1.5  # seconds before pending attribution expires
_PRINTABLE_MIN = 0x20
_PRINTABLE_MAX = 0x7e
_ATTRIBUTION_ESCAPE = 1
_ATTRIBUTION_CSI = 2

# Telnet commands without an option byte (2-byte sequences)
_TWO_BYTE_COMMANDS = set(range(241, 250))  # NOP, DM, BRK, IP, AO, AYT, EC, EL, GA


class TelnetFilter:
    """Strips telnet IAC commands from a byte stream and handles negotiation.

    Telnet clients (including Windows ``telnet.exe``) inject IAC sequences
    for option negotiation.  This filter removes them so the serial device
    never sees them, and so the broadcast data is clean.

    Parameters
    ----------
    on_negotiation : callable(cmd, option)
        Called for each WILL/WONT/DO/DONT so the server can reply.
    map_ec_to_backspace : bool
        If true, Telnet IAC EC is converted to DEL for serial consoles.
    """

    def __init__(self, on_negotiation=None, map_ec_to_backspace=False):
        self._buf = bytearray()
        self._on_negotiation = on_negotiation
        self._map_ec_to_backspace = map_ec_to_backspace

    def feed(self, data: bytes) -> bytes:
        """Feed raw bytes; return clean payload with telnet commands removed."""
        self._buf.extend(data)
        clean = bytearray()
        i = 0
        buf = self._buf

        while i < len(buf):
            b = buf[i]
            if b != IAC:
                clean.append(b)
                i += 1
                continue

            # --- IAC sequence ---
            if i + 1 >= len(buf):
                break  # incomplete — wait for more data
            cmd = buf[i + 1]

            if cmd == IAC:
                # IAC IAC → literal 0xFF byte
                clean.append(IAC)
                i += 2
            elif cmd in (WILL, WONT, DO, DONT):
                if i + 2 >= len(buf):
                    break  # incomplete option byte
                option = buf[i + 2]
                if self._on_negotiation:
                    self._on_negotiation(cmd, option)
                i += 3
            elif cmd == SB:
                # Subnegotiation: skip until IAC SE
                end = buf.find(bytes([IAC, SE]), i)
                if end == -1:
                    break  # incomplete subnegotiation
                i = end + 2
            elif cmd == EC and self._map_ec_to_backspace:
                clean.append(127)
                i += 2
            elif cmd in _TWO_BYTE_COMMANDS:
                i += 2
            else:
                i += 2  # unknown — skip

        self._buf = buf[i:]  # keep unprocessed tail
        return bytes(clean)


class TelnetNvtNormalizer:
    """Restore Telnet NVT character encoding for serial devices.

    Telnet NVT encodes a literal carriage return as CR NUL. Physical serial
    consoles use a bare CR for one Enter, so the second byte of both CR NUL
    and CRLF must not be forwarded.  The state also handles a TCP read split
    between CR and its trailing NUL/LF.
    """

    CR = 13
    LF = 10
    NUL = 0

    def __init__(self):
        self._last_was_cr = False

    def feed(self, data: bytes) -> bytes:
        """Feed Telnet NVT payload; return bytes suitable for the serial port."""
        if not data:
            return b""

        clean = bytearray()
        i = 0

        if self._last_was_cr:
            self._last_was_cr = False
            if data[0] in (self.NUL, self.LF):
                i = 1

        while i < len(data):
            b = data[i]
            if b == self.CR:
                if i + 1 < len(data) and data[i + 1] in (self.NUL, self.LF):
                    clean.append(self.CR)
                    i += 2
                    self._last_was_cr = False
                else:
                    clean.append(self.CR)
                    self._last_was_cr = i == len(data) - 1
                    i += 1
            else:
                clean.append(b)
                self._last_was_cr = False
                i += 1

        return bytes(clean)


# ── Bridge ────────────────────────────────────────────────────────────


class SerialBridge:
    """Async bridge between a physical COM port and a TCP server.

    Typical usage from the GUI thread::

        bridge = SerialBridge("COM3", 115200, 9999,
                              status_callback=..., log_callback=...)
        # Run bridge.start() inside asyncio event loop (separate thread).

    The bridge is **not** thread-safe for configuration changes;
    start/stop should be serialised externally.
    """

    def __init__(self, com_port, baud_rate=115200, tcp_port=9999,
                 status_callback=None, log_callback=None,
                 event_callback=None, traffic_callback=None):
        self.com_port = com_port
        self.baud_rate = baud_rate
        self.tcp_port = tcp_port
        self._on_status = status_callback or (lambda m: None)
        self._on_log = log_callback or (lambda m: None)
        self._on_event = event_callback or (lambda e: None)
        self._on_traffic = traffic_callback or (lambda _direction, _data, _source: None)

        # Mutable state (all accessed from the asyncio thread)
        self.serial_port: serial.Serial | None = None
        self.server: asyncio.AbstractServer | None = None
        self.running = False
        self._clients: OrderedDict[int, dict] = OrderedDict()
        self._next_cid = 0
        self._serial_write_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._serial_failure_reported = False
        # Patch 9: device echo attribution queue replaces _pending_echo
        # Each entry: {"byte": int, "color": str, "client_id": int, "timestamp": float}
        self._pending_attribution: deque[dict] = deque()
        self._attribution_parse_state: dict[int, int] = {}
        self._pending_echo: bytes = b""  # deprecated — kept for test compatibility

    # ── Public API ─────────────────────────────────────────────────

    async def start(self) -> bool:
        """Open serial port and start TCP server.  Returns True on success."""
        self._serial_failure_reported = False
        self.running = True
        self._loop = asyncio.get_running_loop()

        # 1. Open serial
        try:
            self.serial_port = serial.Serial(
                port=self.com_port,
                baudrate=self.baud_rate,
                timeout=0,  # non-blocking reads
            )
        except Exception as exc:
            self._log(f"Failed to open {self.com_port}: {exc}")
            self.running = False
            self._on_event({
                "kind": "bridge_start_failed",
                "timestamp": time.time(),
                "message": f"Failed to open {self.com_port}: {exc}",
            })
            return False
        self._log(f"Opened {self.com_port} @ {self.baud_rate} baud")

        # 2. Start TCP server
        self._log(f"Starting TCP server on 0.0.0.0:{self.tcp_port} ...")
        listener = None
        try:
            listener = create_listener_socket("0.0.0.0", self.tcp_port)
            self.server = await asyncio.start_server(
                self._handle_client, sock=listener
            )
        except Exception as exc:
            if listener is not None:
                listener.close()
            self._log(f"Failed to start TCP server: {exc}")
            if self.serial_port:
                self.serial_port.close()
                self.serial_port = None
            self.running = False
            self._on_event({
                "kind": "bridge_start_failed",
                "timestamp": time.time(),
                "message": f"Failed to start TCP server: {exc}",
            })
            return False
        self._log(f"TCP server listening on 0.0.0.0:{self.tcp_port}")
        self._on_status(
            f"Running — {self.com_port} @ {self.baud_rate}  →  :{self.tcp_port}"
        )

        # 3. Background serial reader
        self._serial_task = asyncio.create_task(self._serial_read_loop())

        # Notify structured event consumers
        self._on_event({
            "kind": "bridge_started",
            "timestamp": time.time(),
            "message": f"{self.com_port} @ {self.baud_rate} → :{self.tcp_port}",
        })
        return True

    async def stop(self):
        """Gracefully tear down the bridge."""
        self.running = False

        # Cancel serial reader
        if hasattr(self, "_serial_task"):
            self._serial_task.cancel()
            try:
                await self._serial_task
            except (asyncio.CancelledError, Exception):
                pass

        # Disconnect all clients
        for cinfo in list(self._clients.values()):
            try:
                cinfo["writer"].close()
            except Exception:
                pass
        self._clients.clear()
        self._attribution_parse_state.clear()

        # Stop server
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

        # Close serial
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None

        self._log("Bridge stopped")
        self._on_status("Stopped")
        self._on_event({
            "kind": "bridge_stopped",
            "timestamp": time.time(),
            "message": "Bridge stopped",
        })

    # ── Serial I/O ─────────────────────────────────────────────────

    def _handle_serial_failure(self, operation: str, exc: Exception) -> bool:
        """Stop the current run and report its first serial I/O failure."""
        if not self.running or self._serial_failure_reported:
            return False
        self._serial_failure_reported = True
        self.running = False
        phase = "读取" if operation == "read" else "写入"
        message = f"串口 {self.com_port} {phase}失败，桥接已停止：{exc}"
        self._log(message)
        self._on_event({
            "kind": "bridge_runtime_failed",
            "timestamp": time.time(),
            "message": message,
            "operation": operation,
        })
        return True

    async def _serial_read_loop(self):
        """Poll serial port and broadcast incoming data to all clients.

        Patch 9: Instead of suppressing device echo with _strip_echo,
        COM data is formatted with client-color attribution for
        printable characters that match the pending attribution queue.
        All terminal output is real COMx data — bridge never fabricates
        local echo.
        """
        while self.running:
            try:
                sp = self.serial_port
                if not sp or not sp.is_open:
                    raise OSError("串口句柄已关闭")
                n = sp.in_waiting
                if n > 0:
                    data = await self._loop.run_in_executor(None, sp.read, n)
                    if data:
                        self._emit_traffic("SERIAL_RX", data, self.com_port)
                        formatted = self._format_com_data_with_attribution(data)
                        if formatted:
                            await self._broadcast(formatted, color="", raw=True)
                await asyncio.sleep(0.01)  # 10 ms poll
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._handle_serial_failure("read", exc)
                break

    async def _write_serial(self, data: bytes, source: str = "") -> bool:
        """Thread-safe serial write (prevents interleaving from multiple clients)."""
        if not self.running:
            return False
        sp = self.serial_port
        try:
            if not (sp and sp.is_open):
                raise OSError("串口句柄已关闭")
            async with self._serial_write_lock:
                written = await self._loop.run_in_executor(None, sp.write, data)
            written = len(data) if written is None else max(0, int(written))
            if written:
                self._emit_traffic("SERIAL_TX", data[:written], source)
            if written != len(data):
                raise OSError(f"串口只写入 {written}/{len(data)} 字节")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._handle_serial_failure("write", exc)
            return False
        return True

    # ── Patch 9: Device Echo Attribution ───────────────────────────

    def _extract_printable_for_attribution(self, data: bytes, color: str,
                                            client_id: int):
        """Register printable characters for device-echo color attribution.

        Only bytes in the printable ASCII range (0x20–0x7e) are queued.
        Control characters (arrows, backspace, enter, ESC, etc.) are
        written to COMx but never attributed — the device decides how to
        respond to them.

        ESC/CSI state is kept per client across calls so direction-key
        bytes like ``[`` and ``A`` inside a split ``ESC[A`` are never queued.
        """
        now = time.time()
        state = self._attribution_parse_state.get(client_id, 0)

        for b in data:
            if b == 0x1b:
                state = _ATTRIBUTION_ESCAPE
                continue

            if state == _ATTRIBUTION_ESCAPE:
                if b == 0x5b:  # '[' — CSI introducer
                    state = _ATTRIBUTION_CSI
                    continue
                if 0x40 <= b <= 0x7e:  # Simple ESC sequence (e.g. ESC c)
                    state = 0
                    continue
                state = 0  # Invalid escape continuation; process normally.
            elif state == _ATTRIBUTION_CSI:
                # Parameter bytes (0x30-0x3f), intermediate bytes
                # (0x20-0x2f), and the final byte are all control syntax.
                if 0x30 <= b <= 0x3f or 0x20 <= b <= 0x2f:
                    continue
                if 0x40 <= b <= 0x7e:
                    state = 0
                    continue
                state = 0  # Invalid CSI continuation; process normally.

            if _PRINTABLE_MIN <= b <= _PRINTABLE_MAX:
                self._pending_attribution.append({
                    "byte": b,
                    "color": color,
                    "client_id": client_id,
                    "timestamp": now,
                })

        if state:
            self._attribution_parse_state[client_id] = state
        else:
            self._attribution_parse_state.pop(client_id, None)

    def _clean_expired_attribution(self):
        """Remove pending attribution entries older than ATTRIBUTION_TIMEOUT."""
        cutoff = time.time() - ATTRIBUTION_TIMEOUT
        while (self._pending_attribution and
               self._pending_attribution[0]["timestamp"] < cutoff):
            self._pending_attribution.popleft()

    def _format_com_data_with_attribution(self, data: bytes) -> bytes:
        """Format COMx data with client-color attribution.

        Each byte is compared against the head of the pending attribution
        queue (FIFO).  On match the byte is wrapped in the corresponding
        client color; on mismatch it passes through uncolored.

        Expired entries are cleaned before matching so stale pending
        input cannot accidentally colour unrelated device output.
        """
        self._clean_expired_attribution()

        result = bytearray()
        pending = self._pending_attribution

        for b in data:
            if pending and b == pending[0]["byte"]:
                entry = pending.popleft()
                result.extend(entry["color"].encode("utf-8"))
                result.append(b)
                result.extend(RESET.encode("utf-8"))
            else:
                result.append(b)

        return bytes(result)

    # ── Deprecated (Patch 8 compat, not called in main path) ──────

    def _strip_echo(self, data: bytes) -> bytes:
        """Strip device echo from *data* by matching against the last client input.

        .. deprecated:: Patch 9
            Replaced by ``_format_com_data_with_attribution``.
            Kept for test compatibility only.
        """
        pending = self._pending_echo
        if not pending:
            return data

        if data == pending:
            self._pending_echo = b""
            return b""
        elif len(data) < len(pending) and pending.startswith(data):
            self._pending_echo = pending[len(data):]
            return b""
        elif len(data) > len(pending) and data.startswith(pending):
            self._pending_echo = b""
            return data[len(pending):]
        else:
            self._pending_echo = b""
            return data

    @staticmethod
    def _bridge_echo_payload(data: bytes) -> bytes:
        """Return the part of client input that the bridge should echo locally.

        .. deprecated:: Patch 9
            Bridge no longer fabricates local input echo; terminal output
            comes exclusively from COMx device.  Kept for test compatibility.
        """
        if not data:
            return b""
        return bytes(b for b in data if b not in BACKSPACE_BYTES)

    # ── TCP Client Handling ────────────────────────────────────────

    async def _handle_client(self, reader, writer):
        """Handle a single TCP client connection (one coroutine per client)."""
        cid = self._next_cid
        self._next_cid += 1

        color = CLIENT_COLORS[cid % len(CLIENT_COLORS)]
        name = f"Client-{cid + 1}"
        addr = writer.get_extra_info("peername")
        addr_str = f"{addr[0]}:{addr[1]}" if addr else "?"

        cinfo = {"writer": writer, "name": name, "color": color, "addr": addr_str}
        self._clients[cid] = cinfo
        self._log(f"{name} ({color}■{RESET}) connected from {addr_str}")
        self._on_status(f"{len(self._clients)} client(s) connected")
        self._on_event({
            "kind": "client_connected",
            "timestamp": time.time(),
            "client_id": cid,
            "name": name,
            "addr": addr_str,
            "color_name": self._color_name(cid),
        })

        # Send welcome banner
        self._write_line(
            writer,
            f"{color}╔══════════════════════════════════════╗{RESET}",
        )
        self._write_line(
            writer,
            f"{color}║  Connected as {name:22s}  ║{RESET}",
        )
        self._write_line(
            writer,
            f"{color}║  COM: {self.com_port} @ {self.baud_rate:7d} baud       ║{RESET}",
        )
        self._write_line(
            writer,
            f"{color}║  Device echo (default)  │  Attributed (colored) ║{RESET}",
        )
        self._write_line(
            writer,
            f"{color}╚══════════════════════════════════════╝{RESET}",
        )

        # Set up telnet handling (reactive only — respond when client negotiates)
        def on_telnet(cmd, option):
            self._reply_telnet(writer, cmd, option)

        tfilter = TelnetFilter(on_negotiation=on_telnet, map_ec_to_backspace=True)
        nvt_normalizer = TelnetNvtNormalizer()

        # Drain welcome banner + proactive telnet negotiation
        # Tell telnet clients: "I will echo, turn off your local echo"
        self._send_telnet(writer, WILL, ECHO)
        self._send_telnet(writer, WILL, SGA)
        try:
            await writer.drain()
        except Exception:
            pass

        try:
            while self.running:
                data = await reader.read(4096)
                if not data:
                    break  # EOF — client disconnected

                clean = tfilter.feed(data)
                if clean:
                    clean = nvt_normalizer.feed(clean)
                if clean:
                    # Patch 9: write ALL bytes to COMx (printable + control).
                    # Bridge NO LONGER fabricates local input echo — terminal
                    # output comes exclusively from COMx device real feedback.
                    source = f"{name}@{addr_str}"
                    written = await self._write_serial(clean, source=source)
                    if not written:
                        break

                    # Register only printable chars for device-echo color
                    # attribution.  Control chars (arrows, backspace, enter,
                    # ESC, etc.) are never attributed — the device decides
                    # how to respond to them and its output speaks for itself.
                    self._extract_printable_for_attribution(clean, color, cid)

        except (ConnectionResetError, BrokenPipeError, OSError):
            pass  # normal disconnect
        except Exception:
            logger.exception("Unexpected error in client handler")
        finally:
            self._clients.pop(cid, None)
            self._attribution_parse_state.pop(cid, None)
            try:
                writer.close()
            except Exception:
                pass
            self._log(f"{name} disconnected")
            if self.running:
                self._on_status(
                    f"{len(self._clients)} client(s) connected"
                    if self._clients
                    else "Running — waiting for clients"
                )
            self._on_event({
                "kind": "client_disconnected",
                "timestamp": time.time(),
                "client_id": cid,
                "name": name,
            })

    # ── Broadcast ──────────────────────────────────────────────────

    async def _broadcast(self, data: bytes, *, color: str, exclude_writer=None, raw: bool = False):
        """Send *data* to all clients except *exclude_writer*.

        If *raw* is True, data is sent as-is without ANSI color wrapping
        (used for COM port output which should appear in the terminal's default color).
        """
        if not self._clients:
            return

        if raw:
            message = data
        else:
            message = f"{color}".encode("utf-8") + data + f"{RESET}".encode("utf-8")

        dead = []
        for cid, cinfo in self._clients.items():
            if cinfo["writer"] is exclude_writer:
                continue
            try:
                cinfo["writer"].write(message)
            except Exception:
                dead.append(cid)

        # Drain all writes
        for cinfo in self._clients.values():
            try:
                await cinfo["writer"].drain()
            except Exception:
                pass

        # Clean up dead clients
        for cid in dead:
            try:
                self._clients[cid]["writer"].close()
            except Exception:
                pass
            self._clients.pop(cid, None)

    # ── Telnet helpers ─────────────────────────────────────────────

    @staticmethod
    def _send_telnet(writer, cmd, option):
        """Send a single IAC command byte sequence."""
        try:
            writer.write(bytes([IAC, cmd, option]))
        except Exception:
            pass

    @staticmethod
    def _reply_telnet(writer, cmd, option):
        """Respond to incoming telnet negotiation."""
        try:
            if cmd == WILL:
                if option == ECHO:
                    writer.write(bytes([IAC, DONT, ECHO]))
                elif option == SGA:
                    writer.write(bytes([IAC, DO, SGA]))
                else:
                    writer.write(bytes([IAC, DONT, option]))
            elif cmd == DO:
                if option in (ECHO, SGA):
                    writer.write(bytes([IAC, WILL, option]))
                else:
                    writer.write(bytes([IAC, WONT, option]))
        except Exception:
            pass

    @staticmethod
    def _write_line(writer, text):
        """Send a CRLF-terminated line to *writer* (best-effort)."""
        try:
            writer.write((text + "\r\n").encode("utf-8"))
        except Exception:
            pass

    # ── Logging ────────────────────────────────────────────────────

    def _emit_traffic(self, direction: str, data: bytes, source: str) -> None:
        """Best-effort raw traffic hook; diagnostics must never break I/O."""
        try:
            self._on_traffic(direction, bytes(data), source)
        except Exception:
            logger.exception("Unexpected error in traffic callback")

    def _color_name(self, cid: int) -> str:
        """Return a human-readable color name for client *cid*."""
        names = ["yellow", "cyan", "magenta", "blue",
                 "white", "bright_red", "bright_green", "bright_blue"]
        return names[cid % len(names)]

    def _log(self, message: str):
        ts = time.strftime("%H:%M:%S")
        self._on_log(f"[{ts}] {message}")

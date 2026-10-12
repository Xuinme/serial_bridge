"""
Full data-path test with a mock serial device (no physical COM port).

Exercises the complete Patch 9 pipeline: raw/telnet clients → IAC
filtering → NVT normalization → serial write, plus device echo →
attributed broadcast.  Verifies that the bridge never fabricates a
local input echo, that control characters are never attributed, and
that telnet control mappings work.
"""
import asyncio
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from bridge import CLIENT_COLORS, IAC, WILL, DO, DONT, ECHO, SGA, SerialBridge
from harness import (
    start_mock_bridge, stop_mock_bridge, close_writer, drain, assert_clean,
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def strip_ansi(data: bytes) -> bytes:
    """Strip ANSI SGR escape sequences so text can be matched contiguously."""
    return re.sub(rb"\x1b\[[0-9;]*m", b"", data)


class FaultingSerial:
    """Serial stub that fails at a chosen I/O boundary."""

    def __init__(self, *, fail_read=False, fail_write=False):
        self.is_open = True
        self.fail_read = fail_read
        self.fail_write = fail_write

    @property
    def in_waiting(self):
        if self.fail_read:
            raise OSError("device disconnected during read")
        return 0

    def write(self, _data):
        if self.fail_write:
            raise OSError("device disconnected during write")
        return 0

    def close(self):
        self.is_open = False


async def exercise_serial_disconnect_paths():
    """A runtime read/write failure stops once instead of polling forever."""
    read_logs = []
    read_events = []
    read_bridge = SerialBridge(
        "MOCK-READ",
        log_callback=read_logs.append,
        event_callback=read_events.append,
    )
    read_serial = FaultingSerial(fail_read=True)
    read_bridge.running = True
    read_bridge._loop = asyncio.get_running_loop()
    read_bridge.serial_port = read_serial
    read_task = asyncio.create_task(read_bridge._serial_read_loop())
    await asyncio.sleep(0.65)
    check("Serial read disconnect stops bridge", not read_bridge.running)
    check("Serial read loop exits after disconnect", read_task.done())
    if not read_task.done():
        read_task.cancel()
    try:
        await read_task
    except asyncio.CancelledError:
        pass
    read_failures = [
        e for e in read_events if e.get("kind") == "bridge_runtime_failed"
    ]
    check(
        "Serial read disconnect emits one failure event",
        len(read_failures) == 1,
        f"(events {len(read_failures)})",
    )
    check(
        "Serial read disconnect logs once",
        len(read_logs) == 1,
        f"(logs {len(read_logs)})",
    )
    read_serial.close()

    write_logs = []
    write_events = []
    write_traffic = []
    write_bridge = SerialBridge(
        "MOCK-WRITE",
        log_callback=write_logs.append,
        event_callback=write_events.append,
        traffic_callback=lambda direction, data, source: write_traffic.append(
            (direction, bytes(data), source)),
    )
    write_serial = FaultingSerial(fail_write=True)
    write_bridge.running = True
    write_bridge._loop = asyncio.get_running_loop()
    write_bridge.serial_port = write_serial
    for payload in (b"x", b"y"):
        try:
            await write_bridge._write_serial(payload)
        except OSError:
            pass
    write_failures = [
        e for e in write_events if e.get("kind") == "bridge_runtime_failed"
    ]
    check("Serial write disconnect stops bridge", not write_bridge.running)
    check(
        "Serial write disconnect emits one failure event",
        len(write_failures) == 1,
        f"(events {len(write_failures)})",
    )
    check(
        "Serial write disconnect logs once",
        len(write_logs) == 1,
        f"(logs {len(write_logs)})",
    )
    check("Failed serial writes do not create false TX records", not write_traffic)
    write_serial.close()


async def main():
    bridge, mock, port = await start_mock_bridge()
    traffic = []
    bridge._on_traffic = lambda direction, data, source: traffic.append(
        (direction, bytes(data), source))
    raw_w = None
    tel_w = None
    try:
        # ── Test A: raw TCP client (like netcat) ────────────────
        raw_r, raw_w = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        raw_data = await drain(raw_r, timeout=1.0)
        raw_iac = raw_data.count(b"\xff")
        check("Raw client: proactive IAC from server (WILL ECHO + WILL SGA)",
              raw_iac == 2, f"({raw_iac} IAC bytes)")
        check("Raw client: welcome present",
              b"Connected as" in strip_ansi(raw_data))
        check("Raw client: ANSI colors in banner",
              b"\033[" in raw_data)

        # Raw client sends → serial, but no bridge-local echo
        raw_w.write(b"hello\r\n")
        await raw_w.drain()
        await asyncio.sleep(0.2)
        check("Raw client input written to serial",
              b"hello" in mock.written,
              f"(written {bytes(mock.written)!r})")
        check("Traffic TX records normalized bytes after successful serial write",
              any(direction == "SERIAL_TX" and data == b"hello\r" and
                  source.startswith("Client-1@")
                  for direction, data, source in traffic))

        raw_echo = await drain(raw_r, timeout=0.4)
        check("Raw client gets no bridge-local echo",
              b"hello" not in strip_ansi(raw_echo),
              f"(received {raw_echo!r})")

        # Device echo → attributed broadcast, zero IAC
        mock.device_echo(b"hello\r\n")
        await asyncio.sleep(0.3)
        bcast = await drain(raw_r, timeout=1.0)
        check("Raw client gets device echo broadcast (zero IAC)",
              bcast.count(b"\xff") == 0, f"({len(bcast)} bytes)")
        check("Device echo attributed with client color",
              CLIENT_COLORS[0].encode() in bcast)
        check("Device echo carries content",
              b"hello" in strip_ansi(bcast))
        check("Traffic RX records raw device bytes before formatting",
              ("SERIAL_RX", b"hello\r\n", "MOCK") in traffic)

        # ── Test B: telnet-negotiating client (like MobaXterm) ──
        tel_r, tel_w = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)

        # 1) Drain the welcome banner and the server's proactive IAC before
        #    asserting anything about client negotiation.
        welcome_and_iac = await drain(tel_r, timeout=1.0)
        check("Telnet client: banner visible after strip",
              b"Connected as" in strip_ansi(welcome_and_iac))

        # 2) Assert the exact reply for each client negotiation.
        async def expect_reply(cmd, option, expected, label):
            tel_w.write(bytes([IAC, cmd, option]))
            await tel_w.drain()
            await asyncio.sleep(0.15)
            resp = await drain(tel_r, timeout=1.0)
            check(label, expected in resp,
                  f"expected {bytes(expected)!r}, got {resp!r}")

        await expect_reply(WILL, ECHO, bytes([IAC, DONT, ECHO]), "WILL ECHO -> DONT ECHO")
        await expect_reply(WILL, SGA, bytes([IAC, DO, SGA]), "WILL SGA -> DO SGA")
        await expect_reply(DO, ECHO, bytes([IAC, WILL, ECHO]), "DO ECHO -> WILL ECHO")
        await expect_reply(DO, SGA, bytes([IAC, WILL, SGA]), "DO SGA -> WILL SGA")

        # Telnet NVT CR NUL → bare CR to serial, NUL stripped
        mock.written.clear()
        tel_w.write(b"\r\x00")
        await tel_w.drain()
        await asyncio.sleep(0.2)
        check("Telnet NVT: CR NUL normalized to bare CR",
              b"\r" in mock.written and b"\x00" not in mock.written,
              f"(written {bytes(mock.written)!r})")
        check("Telnet NVT: CR is not attributed",
              not any(e["byte"] == 0x0d for e in bridge._pending_attribution))

        # IAC EC → DEL to serial, not attributed
        mock.written.clear()
        tel_w.write(b"a" + bytes([IAC, 247]) + b"b")
        await tel_w.drain()
        await asyncio.sleep(0.2)
        check("Telnet IAC EC mapped to DEL on serial",
              b"\x7f" in mock.written,
              f"(written {bytes(mock.written)!r})")
        queue_bytes = [e["byte"] for e in bridge._pending_attribution]
        check("IAC EC not attributed; only a/b queued",
              queue_bytes == [ord("a"), ord("b")],
              f"(queue {queue_bytes})")

        # Control chars (arrow key) never queued
        bridge._pending_attribution.clear()
        mock.written.clear()
        tel_w.write(b"\x1b[A")
        await tel_w.drain()
        await asyncio.sleep(0.2)
        check("Arrow key bytes are never attributed",
              len(bridge._pending_attribution) == 0,
              f"(queue {[e['byte'] for e in bridge._pending_attribution]})")
        check("Arrow key sequence still reaches serial",
              b"\x1b[A" in mock.written)
        check("Traffic TX distinguishes the second client source",
              any(direction == "SERIAL_TX" and source.startswith("Client-2@")
                  for direction, _data, source in traffic))
    finally:
        await close_writer(raw_w)
        await close_writer(tel_w)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)

    await exercise_serial_disconnect_paths()

    print("\n" + "=" * 55)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"RESULTS: {passed}/{len(results)} passed")
    for name, ok, detail in results:
        print(f"  [{'OK' if ok else 'XX'}] {name} {detail}")
    return passed == len(results)


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)

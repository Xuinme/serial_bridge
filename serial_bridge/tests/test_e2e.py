"""
Comprehensive workflow test for SerialBridge (Patch 9 data flow).

Simulates a user observation window and an AI agent interacting over
TCP, backed by a mock serial device so no physical COM port is
required.  Verifies Patch 9 semantics:

  * client input is written to the serial port but is never echoed
    back by the bridge (no bridge-local echo);
  * the device's own echo is broadcast to every client with source
    color attribution;
  * clients use distinct colors and the bridge stays up across a
    client disconnect.
"""
import asyncio
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from bridge import CLIENT_COLORS
from harness import (
    start_mock_bridge, stop_mock_bridge, close_writer, drain, assert_clean,
)

results = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, name, detail))
    print(f"  [{status}] {name} {detail}")


def strip_ansi(data: bytes) -> bytes:
    """Strip ANSI SGR escape sequences so text can be matched contiguously."""
    return re.sub(rb"\x1b\[[0-9;]*m", b"", data)


async def connect_client(port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await asyncio.sleep(0.2)
    return reader, writer


async def main():
    bridge, mock, port = await start_mock_bridge()
    reader_a = None
    writer_a = None
    reader_b = None
    writer_b = None
    try:
        # ── Phase 1: Start bridge on an OS-assigned port ────────
        check("TCP server listening on OS-assigned port",
              bridge.server is not None and port > 0, f"(port {port})")

        # ── Phase 2: Two clients connect ────────────────────────
        reader_a, writer_a = await connect_client(port)
        welcome_a = await drain(reader_a)
        check("User window receives welcome banner",
              b"Connected as Client-1" in welcome_a or b"Client-1" in welcome_a,
              f"({len(welcome_a)} bytes)")

        reader_b, writer_b = await connect_client(port)
        welcome_b = await drain(reader_b)
        check("AI agent receives welcome banner",
              b"Connected as Client-2" in welcome_b or b"Client-2" in welcome_b,
              f"({len(welcome_b)} bytes)")

        # ── Phase 3: Client A (user) sends data ─────────────────
        writer_a.write(b"AT+VERSION\r\n")
        await writer_a.drain()
        await asyncio.sleep(0.2)

        check("Client A input written to serial",
              b"AT+VERSION" in mock.written,
              f"(written {bytes(mock.written)!r})")

        # No bridge-local echo: observer B must NOT see A's raw input
        data_b = await drain(reader_b, timeout=0.4)
        check("Bridge does not fabricate local echo",
              b"AT+VERSION" not in data_b,
              f"(observer received {data_b!r})")

        # ── Phase 4: Device echoes A's input → attributed ───────
        mock.device_echo(b"AT+VERSION\r\n")
        await asyncio.sleep(0.3)

        data_b = await drain(reader_b, timeout=1.0)
        check("AI sees device echo of Client-1 in yellow",
              b"AT+VERSION" in strip_ansi(data_b) and
              CLIENT_COLORS[0].encode() in data_b,
              f"({len(data_b)} bytes)")

        data_a = await drain(reader_a, timeout=1.0)
        check("User window also sees the device echo",
              b"AT+VERSION" in strip_ansi(data_a),
              f"({len(data_a)} bytes)")

        # ── Phase 5: Client B (AI) sends → attributed cyan ──────
        writer_b.write(b"AT+STATUS\r\n")
        await writer_b.drain()
        await asyncio.sleep(0.2)
        check("Client B input written to serial",
              b"AT+STATUS" in mock.written)

        mock.device_echo(b"AT+STATUS\r\n")
        await asyncio.sleep(0.3)

        data_a = await drain(reader_a, timeout=1.0)
        check("User sees device echo of Client-2 in cyan",
              b"AT+STATUS" in strip_ansi(data_a) and
              CLIENT_COLORS[1].encode() in data_a,
              f"({len(data_a)} bytes)")

        # ── Phase 6: Distinct source colors ─────────────────────
        check("Client-1 and Client-2 use distinct colors",
              CLIENT_COLORS[0] != CLIENT_COLORS[1])

        # ── Phase 7: Client B disconnects, bridge keeps running ──
        await close_writer(writer_b)
        writer_b = None
        await asyncio.sleep(0.3)
        check("Bridge still running after client disconnect",
              bridge.running)

    finally:
        await close_writer(writer_b)
        await close_writer(writer_a)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)

    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r[0] == "PASS")
    failed = sum(1 for r in results if r[0] == "FAIL")
    print(f"RESULTS: {passed} passed, {failed} failed, {len(results)} total")
    for status, name, detail in results:
        icon = "[OK]" if status == "PASS" else "[XX]"
        print(f"  {icon} {name}  {detail}")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

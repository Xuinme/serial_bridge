"""
Telnet protocol handling test (mock serial, no physical COM port).

Validates:
  1. Raw TCP clients see a clean banner and zero IAC in device-echo broadcasts.
  2. Telnet clients negotiate (the server responds) and display a clean
     IAC-free view after the client-side IAC strip.
  3. The bridge never fabricates a local input echo (Patch 9).
"""
import asyncio
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from bridge import CLIENT_COLORS, IAC, WILL, WONT, DO, DONT, ECHO, SGA
from harness import (
    start_mock_bridge, stop_mock_bridge, close_writer, drain, assert_clean,
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def strip_ansi(data: bytes) -> bytes:
    return re.sub(rb"\x1b\[[0-9;]*m", b"", data)


def strip_iac(data: bytes) -> bytes:
    """Strip negotiation IAC from a telnet client's display (client-side)."""
    clean = bytearray()
    i = 0
    while i < len(data):
        if data[i] == IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (WILL, WONT, DO, DONT):
                i += 3
            elif cmd == 250:  # SB
                end = data.find(bytes([IAC, 240]), i)
                i = end + 2 if end != -1 else i + 2
            elif 241 <= cmd <= 249:
                i += 2
            else:
                i += 2
        else:
            clean.append(data[i])
            i += 1
    return bytes(clean)


async def main():
    bridge, mock, port = await start_mock_bridge()
    raw_w = None
    tel_w = None
    try:
        # ── Test A: raw TCP client (like netcat) ────────────────
        raw_r, raw_w = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        raw_data = await drain(raw_r, timeout=1.0)
        raw_iac = raw_data.count(b"\xff")
        check("Raw client: only proactive IAC (WILL ECHO + WILL SGA)",
              raw_iac == 2, f"({raw_iac} IAC bytes)")
        check("Raw client: banner visible after ANSI strip",
              b"Connected as" in strip_ansi(raw_data))

        # Raw client sends → no bridge-local echo
        raw_w.write(b"ping\r\n")
        await raw_w.drain()
        await asyncio.sleep(0.2)
        check("Raw client: no bridge-local echo",
              b"ping" not in strip_ansi(await drain(raw_r, timeout=0.4)))

        # Device echo → attributed broadcast, IAC-free content
        mock.device_echo(b"ping\r\n")
        await asyncio.sleep(0.3)
        bcast = await drain(raw_r, timeout=1.0)
        check("Raw client: broadcast has zero IAC",
              bcast.count(b"\xff") == 0, f"({len(bcast)} bytes)")
        check("Raw client: broadcast shows the device echo",
              b"ping" in strip_ansi(bcast))
        check("Raw client: source color present",
              CLIENT_COLORS[0].encode() in bcast)

        # ── Test B: telnet client (like MobaXterm) ──────────────
        tel_r, tel_w = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)

        # 1) Drain the welcome banner and the server's PROACTIVE IAC (WILL ECHO,
        #    WILL SGA) before asserting anything about client negotiation.
        welcome_and_iac = await drain(tel_r, timeout=1.0)
        check("Telnet client: banner visible after IAC + ANSI strip",
              b"Connected as" in strip_iac(strip_ansi(welcome_and_iac)))

        # 2) Assert the exact reply for each client negotiation.  The server's
        #    _reply_telnet emits: WILL ECHO -> DONT ECHO, WILL SGA -> DO SGA,
        #    DO ECHO/SGA -> WILL ECHO/SGA.
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

        # Telnet client writes; device echoes; display stays IAC-free
        tel_w.write(b"AT+TEST\r\n")
        await tel_w.drain()
        await asyncio.sleep(0.2)
        mock.device_echo(b"AT+TEST\r\n")
        await asyncio.sleep(0.3)
        tel_echo = await drain(tel_r, timeout=1.0)
        check("Telnet client: broadcast has zero IAC",
              tel_echo.count(b"\xff") == 0, f"({len(tel_echo)} bytes)")
        check("Telnet client: echo data visible after strip",
              b"AT+TEST" in strip_ansi(tel_echo))
        check("Telnet client: source color present",
              b"\033[0;3" in tel_echo)
    finally:
        await close_writer(raw_w)
        await close_writer(tel_w)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)

    print("\n" + "=" * 50)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"RESULTS: {passed}/{len(results)} passed")
    for name, ok, detail in results:
        print(f"  [{'OK' if ok else 'XX'}] {name} {detail}")
    return passed == len(results)


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)

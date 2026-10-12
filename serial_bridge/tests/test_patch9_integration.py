"""
Patch 9 integration test: full data-flow with mock serial port.

Tests the complete path without requiring a physical COM port:
  TCP client → TelnetFilter → NvtNormalizer → _write_serial
  → mock_serial device_echo → _format_com_data_with_attribution
  → _broadcast → TCP client receives attributed data.

Each test starts its own loopback listener on an OS-assigned port and
closes every client socket, the listener, the serial read task and the
mock serial in ``finally`` (via the shared harness), then asserts the
post-conditions, so no test probes, reuses or leaks a service/resource.
"""
import asyncio
import sys
import time
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from bridge import (
    CLIENT_COLORS, IAC, WILL, ECHO, ATTRIBUTION_TIMEOUT,
)
import harness as harness_mod
from harness import (
    start_mock_bridge, stop_mock_bridge, close_writer, drain, assert_clean,
)


async def test_full_flow_printable_attribution():
    """Client types 'abc', device echoes 'abc' → client receives colored 'abc'."""
    bridge, mock, port = await start_mock_bridge()
    writer = None
    writer2 = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        welcome = await drain(reader, timeout=2.0)
        assert b"Connected as" in welcome, f"No welcome: {welcome[:100]!r}"

        # Client sends raw TCP "abc" (no telnet negotiation)
        writer.write(b"abc")
        await writer.drain()
        await asyncio.sleep(0.1)

        # Verify written to serial
        assert b"abc" in mock.written, f"Expected 'abc' written to serial, got {mock.written!r}"

        # Verify pending attribution has 3 entries
        assert len(bridge._pending_attribution) == 3, \
            f"Expected 3 pending entries, got {len(bridge._pending_attribution)}"

        # Collect broadcast output by connecting a second client as observer
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader2)

        # Device echoes "abc"
        mock.device_echo(b"abc")
        await asyncio.sleep(0.2)

        # Second client should receive attributed output
        raw = await drain(reader2, timeout=2.0)

        # Check for color codes in the broadcast
        yellow = CLIENT_COLORS[0].encode()
        assert yellow in raw, \
            f"Expected yellow color for attributed chars, got {raw!r}"
        assert b"a" in raw and b"b" in raw and b"c" in raw

        # Attribution queue should be drained
        assert len(bridge._pending_attribution) == 0, \
            f"Queue should be empty after echo, got {len(bridge._pending_attribution)}"
    finally:
        await close_writer(writer2)
        await close_writer(writer)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)


async def test_full_flow_control_chars_not_attributed():
    """Arrow keys and Enter go to serial but are never attributed."""
    bridge, mock, port = await start_mock_bridge()
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader)

        # Client sends direction key + Enter + printable
        writer.write(b"\x1b[A\r\nhello")
        await writer.drain()
        await asyncio.sleep(0.1)

        # All bytes go to serial
        assert b"\x1b[A" in mock.written, f"Arrow key not written: {mock.written!r}"

        # But only printable chars are queued
        bytes_in_queue = [e["byte"] for e in bridge._pending_attribution]
        for b_data in bytes_in_queue:
            assert 0x20 <= b_data <= 0x7e, \
                f"Control char {b_data:#x} in attribution queue!"

        # ESC-related bytes ('['=0x5b, 'A'=0x41) should NOT be in queue
        assert 0x5b not in bytes_in_queue, f"ESC '[' leaked into queue: {bytes_in_queue}"
        assert 0x41 not in bytes_in_queue, f"ESC 'A' leaked into queue: {bytes_in_queue}"
    finally:
        await close_writer(writer)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)


async def test_split_escape_state_is_per_client_and_cleaned():
    """Split CSI input is excluded per client and state dies on disconnect."""
    bridge, mock, port = await start_mock_bridge()
    writer1 = None
    writer2 = None
    try:
        reader1, writer1 = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader1)
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader2)

        client1, client2 = list(bridge._clients)
        bridge._pending_attribution.clear()

        # Split after ESC while the other client sends ordinary input.
        writer1.write(b"\x1b")
        await writer1.drain()
        await asyncio.sleep(0.1)
        assert bytes(mock.written).endswith(b"\x1b")
        assert client1 in bridge._attribution_parse_state

        writer2.write(b"x")
        await writer2.drain()
        await asyncio.sleep(0.1)
        entries = [(entry["byte"], entry["client_id"])
                   for entry in bridge._pending_attribution]
        assert entries == [(ord("x"), client2)]

        writer1.write(b"[1;5A")
        await writer1.drain()
        await asyncio.sleep(0.1)
        assert bytes(mock.written).endswith(b"[1;5A")
        assert client1 not in bridge._attribution_parse_state
        assert entries == [(entry["byte"], entry["client_id"])
                           for entry in bridge._pending_attribution]

        # Split after the CSI introducer as a second boundary case.
        bridge._pending_attribution.clear()
        writer1.write(b"\x1b[")
        await writer1.drain()
        await asyncio.sleep(0.1)
        assert bytes(mock.written).endswith(b"\x1b[")
        assert client1 in bridge._attribution_parse_state

        writer2.write(b"y")
        await writer2.drain()
        await asyncio.sleep(0.1)
        writer1.write(b"A")
        await writer1.drain()
        await asyncio.sleep(0.1)
        assert bytes(mock.written).endswith(b"A")
        entries = [(entry["byte"], entry["client_id"])
                   for entry in bridge._pending_attribution]
        assert entries == [(ord("y"), client2)]
        assert client1 not in bridge._attribution_parse_state

        # An unfinished sequence is removed when its client disconnects.
        writer1.write(b"\x1b")
        await writer1.drain()
        await asyncio.sleep(0.1)
        assert bytes(mock.written).endswith(b"\x1b")
        assert client1 in bridge._attribution_parse_state
        await close_writer(writer1)
        writer1 = None
        for _ in range(20):
            if client1 not in bridge._clients:
                break
            await asyncio.sleep(0.05)
        assert client1 not in bridge._clients
        assert client1 not in bridge._attribution_parse_state
    finally:
        await close_writer(writer2)
        await close_writer(writer1)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)


async def test_full_flow_telnet_nvt_enter():
    """Telnet client sends CR NUL for Enter → bridge normalizes to bare CR."""
    bridge, mock, port = await start_mock_bridge()
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader, timeout=2.0)

        # Simulate telnet negotiation so NVT normalizer activates
        writer.write(bytes([IAC, WILL, ECHO]))
        await writer.drain()
        await asyncio.sleep(0.1)
        await drain(reader)

        # Send telnet NVT CR NUL (Enter in NVT mode)
        writer.write(b"\r\x00")
        await writer.drain()
        await asyncio.sleep(0.1)

        # Should be normalized to bare CR in serial output
        assert b"\r" in mock.written, f"CR should reach serial: {mock.written!r}"
        assert b"\x00" not in mock.written, \
            f"NUL should be stripped: {mock.written!r}"

        # Enter (CR) is a control char — NOT in attribution queue
        cr_in_queue = any(e["byte"] == 0x0d for e in bridge._pending_attribution)
        assert not cr_in_queue, "CR should not be in attribution queue"
    finally:
        await close_writer(writer)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)


async def test_crlf_enter_is_single_cr_without_or_with_negotiation():
    """Built-in and Windows Telnet Enter both write exactly one serial CR."""
    bridge, mock, port = await start_mock_bridge()
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader, timeout=2.0)

        # Built-in client path: it sends CRLF without initiating negotiation.
        mock.written.clear()
        writer.write(b"\r\n")
        await writer.drain()
        await asyncio.sleep(0.1)
        assert bytes(mock.written) == b"\r", \
            f"unnegotiated Enter wrote {bytes(mock.written)!r}"

        # Windows telnet path: negotiation happens before the same CRLF Enter.
        writer.write(bytes([IAC, WILL, ECHO]))
        await writer.drain()
        await asyncio.sleep(0.1)
        await drain(reader)
        mock.written.clear()
        writer.write(b"\r\n")
        await writer.drain()
        await asyncio.sleep(0.1)
        assert bytes(mock.written) == b"\r", \
            f"negotiated Enter wrote {bytes(mock.written)!r}"
    finally:
        await close_writer(writer)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)


async def test_full_flow_telnet_backspace_to_serial():
    """Telnet IAC EC is mapped to DEL and written to serial; not attributed."""
    bridge, mock, port = await start_mock_bridge()
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader, timeout=2.0)

        # Trigger telnet negotiation
        writer.write(bytes([IAC, WILL, ECHO]))
        await writer.drain()
        await asyncio.sleep(0.1)
        await drain(reader)

        # Send IAC EC (telnet backspace) + printable
        writer.write(b"a" + bytes([IAC, 247]) + b"b")
        await writer.drain()
        await asyncio.sleep(0.1)

        # DEL (0x7f) should appear in serial output
        assert b"\x7f" in mock.written, \
            f"IAC EC should become DEL in serial: {mock.written!r}"

        # Only 'a' and 'b' should be in attribution
        queue_bytes = [e["byte"] for e in bridge._pending_attribution]
        assert queue_bytes == [ord("a"), ord("b")], \
            f"Expected [a, b] in queue, got {queue_bytes}"
    finally:
        await close_writer(writer)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)


async def test_full_flow_no_local_echo():
    """Patch 9: client input is NOT broadcast back to clients."""
    bridge, mock, port = await start_mock_bridge()
    writer = None
    writer2 = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader)

        # Second client observes
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader2)

        # Client-1 types — should NOT appear immediately (no local echo)
        writer.write(b"xyz")
        await writer.drain()
        await asyncio.sleep(0.2)

        # Observer should NOT receive the input (Patch 9: no local echo)
        data = await drain(reader2, timeout=1.0)

        # Input "xyz" should NOT appear as broadcast (no bridge echo)
        assert b"xyz" not in data, \
            f"Local echo detected — Patch 9 should not broadcast client input: {data!r}"

        # Now device echoes "xyz" → should be broadcast with attribution
        mock.device_echo(b"xyz")
        await asyncio.sleep(0.2)

        data2 = await drain(reader2, timeout=2.0)

        assert b"x" in data2 and b"y" in data2 and b"z" in data2, \
            f"Device echo should be broadcast: {data2!r}"
        yellow = CLIENT_COLORS[0].encode()
        assert yellow in data2, f"Attributed chars should be colored: {data2!r}"
    finally:
        await close_writer(writer2)
        await close_writer(writer)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)


async def test_full_flow_attribution_timeout():
    """Attribution entries expire after ATTRIBUTION_TIMEOUT seconds."""
    bridge, mock, port = await start_mock_bridge()
    writer = None
    writer2 = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader)

        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await drain(reader2)

        # Client types "test"
        writer.write(b"test")
        await writer.drain()
        await asyncio.sleep(0.1)

        assert len(bridge._pending_attribution) == 4

        # Wait for expiration
        await asyncio.sleep(ATTRIBUTION_TIMEOUT + 0.3)

        # Device echoes "test" — queue should be cleaned, no attribution
        mock.device_echo(b"test")
        await asyncio.sleep(0.2)

        data = await drain(reader2, timeout=2.0)

        # Should get raw data without color codes
        assert b"test" in data, f"Device echo should still be broadcast: {data!r}"
        assert b"\033[" not in data or CLIENT_COLORS[0].encode() not in data, \
            f"Expired attribution should not color output: {data!r}"
    finally:
        await close_writer(writer2)
        await close_writer(writer)
        await stop_mock_bridge(bridge)
        await assert_clean(bridge, mock)


async def test_harness_rolls_back_listener_start_failure():
    """A failed listener start closes the mock serial and leaves no read task."""
    created = []
    real_factory = harness_mod.MockSerial

    def recording_factory():
        serial = real_factory()
        created.append(serial)
        return serial

    async def fail_start_server(*args, **kwargs):
        raise OSError("injected listener failure")

    original_start_server = harness_mod.asyncio.start_server
    original_serial_factory = harness_mod.MockSerial
    harness_mod.asyncio.start_server = fail_start_server
    harness_mod.MockSerial = recording_factory
    try:
        try:
            await harness_mod.start_mock_bridge()
        except OSError as exc:
            assert str(exc) == "injected listener failure"
        else:
            raise AssertionError("listener startup failure should propagate")
    finally:
        harness_mod.asyncio.start_server = original_start_server
        harness_mod.MockSerial = original_serial_factory

    assert len(created) == 1
    assert created[0].is_open is False
    current = asyncio.current_task()
    serial_tasks = [
        task for task in asyncio.all_tasks()
        if task is not current and not task.done() and
        task.get_coro().__qualname__ == "SerialBridge._serial_read_loop"
    ]
    assert not serial_tasks, f"serial reader leaked after startup failure: {serial_tasks}"


async def test_close_writer_propagates_cleanup_failure():
    """Client cleanup failures must fail the test instead of being swallowed."""
    class FailingWriter:
        def close(self):
            raise RuntimeError("injected close failure")

        async def wait_closed(self):
            raise AssertionError("wait_closed should not run after close fails")

        def is_closing(self):
            return False

    try:
        await close_writer(FailingWriter())
    except RuntimeError as exc:
        assert str(exc) == "injected close failure"
    else:
        raise AssertionError("close_writer swallowed a cleanup failure")


# ── Runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    async def run_all():
        tests = [
            test_full_flow_printable_attribution,
            test_full_flow_control_chars_not_attributed,
            test_split_escape_state_is_per_client_and_cleaned,
            test_full_flow_telnet_nvt_enter,
            test_crlf_enter_is_single_cr_without_or_with_negotiation,
            test_full_flow_telnet_backspace_to_serial,
            test_full_flow_no_local_echo,
            test_full_flow_attribution_timeout,
            test_harness_rolls_back_listener_start_failure,
            test_close_writer_propagates_cleanup_failure,
        ]
        passed = 0
        failed = 0
        for test in tests:
            try:
                await test()
                print(f"  [PASS] {test.__name__}")
                passed += 1
            except Exception:
                print(f"  [FAIL] {test.__name__}")
                traceback.print_exc()
                failed += 1
        print(f"\nRESULTS: {passed} passed, {failed} failed, {len(tests)} total")
        return failed == 0

    success = asyncio.run(run_all())
    sys.exit(0 if success else 1)

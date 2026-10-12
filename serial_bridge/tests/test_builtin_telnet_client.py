"""
Tests for the Built-in Telnet Client and IAC filtering.

Tests the TelnetClient class, TelnetFilter behavior, and
basic send/recv patterns without requiring a real COM port.
"""
import sys
import threading
import time
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from bridge import (
    TelnetFilter,
    TelnetNvtNormalizer,
    SerialBridge,
    IAC,
    WILL,
    WONT,
    DO,
    DONT,
    EC,
    ECHO,
    SGA,
)
from app.telnet_client import TelnetClient


# ── TelnetFilter tests ────────────────────────────────────────────────


def test_filter_strips_iac_will_echo():
    """TelnetFilter strips IAC WILL ECHO."""
    tf = TelnetFilter()
    data = tf.feed(bytes([IAC, WILL, ECHO]))
    assert data == b"", f"Expected empty, got {data!r}"


def test_filter_strips_iac_do_sga():
    """TelnetFilter strips IAC DO SGA."""
    tf = TelnetFilter()
    data = tf.feed(bytes([IAC, DO, SGA]))
    assert data == b"", f"Expected empty, got {data!r}"


def test_filter_passes_normal_data():
    """TelnetFilter passes normal data through."""
    tf = TelnetFilter()
    data = tf.feed(b"hello world\r\n")
    assert data == b"hello world\r\n"


def test_filter_iac_iac_escape():
    """IAC IAC → literal 0xFF."""
    tf = TelnetFilter()
    data = tf.feed(bytes([IAC, IAC]))
    assert data == bytes([IAC]), f"Expected 0xFF, got {data!r}"


def test_filter_mixed_content():
    """Filter handles telnet commands mixed with data."""
    tf = TelnetFilter()
    # Simulate: IAC WILL ECHO + data + IAC DO SGA + more data
    raw = bytes([IAC, WILL, ECHO]) + b"hello" + bytes([IAC, DO, SGA]) + b"world"
    data = tf.feed(raw)
    assert data == b"helloworld", f"Got {data!r}"


def test_filter_incomplete_sequence():
    """Incomplete IAC at end of buffer is preserved for next feed."""
    tf = TelnetFilter()
    # Only IAC byte, no command
    data1 = tf.feed(bytes([IAC]))
    assert data1 == b"", "Should return empty for incomplete IAC"

    # Feed the rest
    data2 = tf.feed(bytes([WILL, ECHO]))
    assert data2 == b"", "Should strip completed IAC WILL ECHO"


def test_filter_subnegotiation():
    """IAC SB ... IAC SE subnegotiation is stripped."""
    tf = TelnetFilter()
    raw = b"before" + bytes([IAC, 250]) + b"subdata" + bytes([IAC, 240]) + b"after"
    data = tf.feed(raw)
    assert data == b"beforeafter", f"Got {data!r}"


def test_filter_two_byte_commands():
    """Two-byte telnet commands (NOP, DM, etc.) are stripped."""
    tf = TelnetFilter()
    raw = b"x" + bytes([IAC, 241]) + b"y"  # IAC NOP
    data = tf.feed(raw)
    assert data == b"xy", f"Got {data!r}"


def test_filter_strips_iac_ec_by_default():
    """IAC EC is stripped by display-side filters unless explicitly mapped."""
    tf = TelnetFilter()
    data = tf.feed(bytes([IAC, EC]))
    assert data == b"", f"Got {data!r}"


def test_filter_maps_iac_ec_to_del_for_bridge_input():
    """Bridge input can map Telnet EC to DEL for serial consoles."""
    tf = TelnetFilter(map_ec_to_backspace=True)
    data = tf.feed(b"a" + bytes([IAC, EC]) + b"b")
    assert data == b"a\x7fb", f"Got {data!r}"


def test_bridge_echo_drops_backspace_controls():
    """Backspace goes to serial only; local bridge echo omits it."""
    data = SerialBridge._bridge_echo_payload(b"a\x08b\x7fc")
    assert data == b"abc", f"Got {data!r}"


# ── Telnet NVT normalization tests ────────────────────────────────────


def test_nvt_cr_nul_to_cr():
    """Telnet NVT CR NUL is restored to a bare CR."""
    normalizer = TelnetNvtNormalizer()
    data = normalizer.feed(b"boot\r\x00boot")
    assert data == b"boot\rboot", f"Got {data!r}"


def test_nvt_cr_nul_split_across_feeds():
    """CR NUL is restored even when TCP splits it across reads."""
    normalizer = TelnetNvtNormalizer()
    data1 = normalizer.feed(b"\r")
    data2 = normalizer.feed(b"\x00")
    assert data1 == b"\r", f"Got {data1!r}"
    assert data2 == b"", f"Got {data2!r}"


def test_nvt_crlf_to_single_cr_and_preserves_literal_nul():
    """CRLF is one serial-console Enter; unrelated NUL stays unchanged."""
    normalizer = TelnetNvtNormalizer()
    data = normalizer.feed(b"line\r\nraw\x00")
    assert data == b"line\rraw\x00", f"Got {data!r}"


def test_nvt_crlf_split_across_feeds_to_single_cr():
    """A TCP split between CR and LF must not create a second Enter."""
    normalizer = TelnetNvtNormalizer()
    data1 = normalizer.feed(b"command\r")
    data2 = normalizer.feed(b"\nnext")
    assert data1 == b"command\r", f"Got {data1!r}"
    assert data2 == b"next", f"Got {data2!r}"


# ── TelnetClient tests ────────────────────────────────────────────────


def test_client_initial_state():
    """New TelnetClient is not connected."""
    client = TelnetClient()
    assert client.connected is False


def test_client_connect_invalid_port():
    """Connecting to a deterministically failing endpoint is handled."""

    # Use a mock socket whose connect() fails, so no real socket is ever
    # created and the test asserts the failure/error-callback deterministically
    # without leaking a socket (telnet_client.connect() sets _sock=None on a
    # failed connect, which is out of this test's write scope).
    from unittest import mock

    import app.telnet_client as telnet_mod

    fake = mock.MagicMock()
    fake.connect.side_effect = OSError("connection refused")
    with mock.patch.object(telnet_mod.socket, "socket", return_value=fake):
        client = TelnetClient(host="127.0.0.1", port=0)
        errors = []
        client.on_error = lambda msg: errors.append(msg)
        ok = client.connect()
        assert ok is False, "connect() to a failed endpoint must return False"
        assert client.connected is False, "client must not be marked connected"
        assert errors, "on_error callback must fire after a failed connect"
        client.disconnect()


def test_client_disconnect_when_not_connected():
    """disconnect() when not connected doesn't crash."""
    client = TelnetClient()
    client.disconnect()
    # Should not raise


def test_client_send_when_not_connected():
    """send() when not connected doesn't crash."""
    client = TelnetClient()
    client.send(b"test")
    # Should not raise


def test_client_callbacks_assignable():
    """All callbacks can be assigned."""
    client = TelnetClient()
    calls = []

    client.on_data = lambda d: calls.append(("data", d))
    client.on_connected = lambda: calls.append(("connected",))
    client.on_disconnected = lambda r: calls.append(("disconnected", r))
    client.on_error = lambda m: calls.append(("error", m))

    # Trigger callbacks manually (they should be callable)
    client.on_data(b"test")
    client.on_connected()
    client.on_disconnected("test")
    client.on_error("test error")

    assert len(calls) == 4, f"Expected 4 callbacks, got {len(calls)}"


# ── TelnetFilter negotiation callback ─────────────────────────────────


def test_filter_negotiation_callback():
    """TelnetFilter calls on_negotiation for WILL/WONT/DO/DONT."""
    negotiations = []

    def on_neg(cmd, option):
        negotiations.append((cmd, option))

    tf = TelnetFilter(on_negotiation=on_neg)
    tf.feed(bytes([IAC, WILL, ECHO]))
    assert (WILL, ECHO) in negotiations

    tf.feed(bytes([IAC, DO, SGA]))
    assert (DO, SGA) in negotiations


# ── Patch 9: Device Echo Attribution tests ─────────────────────────────


def test_attribution_printable_chars_colored():
    """Client inputs 'abc', COM echoes 'abc' → output has client color."""
    bridge = SerialBridge("MOCK", tcp_port=0)
    bridge.running = True
    bridge._extract_printable_for_attribution(b"abc", "\033[0;33m", 0)
    result = bridge._format_com_data_with_attribution(b"abc")
    assert b"\033[0;33m" in result, f"Expected color codes in {result!r}"
    assert b"a" in result and b"b" in result and b"c" in result
    assert result.count(b"\033[0;33m") == 3, f"Expected 3 color starts, got {result!r}"


def test_attribution_control_chars_not_queued():
    """Arrow keys, enter, backspace are written to COM but not attributed."""
    bridge = SerialBridge("MOCK", tcp_port=0)
    bridge.running = True
    # Direction key ESC[A + Enter CR + printable 'x'
    bridge._extract_printable_for_attribution(b"\x1b[A\r\x00x", "\033[0;33m", 0)
    # Queue should only have 'x' (0x78)
    assert len(bridge._pending_attribution) == 1, \
        f"Expected 1 pending entry, got {len(bridge._pending_attribution)}"
    assert bridge._pending_attribution[0]["byte"] == ord("x")


def test_attribution_split_escape_sequences_not_queued():
    """ESC/CSI syntax stays excluded when split across input chunks."""
    cases = [
        (b"\x1b", b"[A"),
        (b"\x1b[", b"A"),
        (b"\x1b[1;", b"5A"),
    ]
    for first, second in cases:
        bridge = SerialBridge("MOCK", tcp_port=0)
        bridge._extract_printable_for_attribution(first, "\033[0;33m", 0)
        assert 0 in bridge._attribution_parse_state
        bridge._extract_printable_for_attribution(second, "\033[0;33m", 0)
        assert not bridge._pending_attribution, \
            f"Split escape leaked into attribution: {first!r} | {second!r}"
        assert 0 not in bridge._attribution_parse_state


def test_attribution_escape_state_isolated_by_client():
    """An incomplete escape from one client does not suppress another."""
    bridge = SerialBridge("MOCK", tcp_port=0)
    bridge._extract_printable_for_attribution(b"\x1b", "\033[0;33m", 0)
    bridge._extract_printable_for_attribution(b"x", "\033[0;36m", 1)

    entries = [(entry["byte"], entry["client_id"])
               for entry in bridge._pending_attribution]
    assert entries == [(ord("x"), 1)]
    assert 0 in bridge._attribution_parse_state
    assert 1 not in bridge._attribution_parse_state

    bridge._extract_printable_for_attribution(b"[A", "\033[0;33m", 0)
    assert entries == [(entry["byte"], entry["client_id"])
                       for entry in bridge._pending_attribution]
    assert 0 not in bridge._attribution_parse_state


def test_attribution_backspace_not_in_queue():
    """Client inputs 'a\x08b\x7fc', only a/b/c in pending."""
    bridge = SerialBridge("MOCK", tcp_port=0)
    bridge.running = True
    bridge._extract_printable_for_attribution(b"a\x08b\x7fc", "\033[0;33m", 0)
    assert len(bridge._pending_attribution) == 3, \
        f"Expected 3 entries, got {len(bridge._pending_attribution)}"
    bytes_in_queue = [e["byte"] for e in bridge._pending_attribution]
    assert bytes_in_queue == [ord("a"), ord("b"), ord("c")], \
        f"Expected [a,b,c], got {bytes_in_queue}"


def test_attribution_expired_entries_not_matched():
    """Pending entries older than timeout are not matched."""
    bridge = SerialBridge("MOCK", tcp_port=0)
    bridge.running = True
    # Force an expired entry by setting timestamp far in the past
    bridge._pending_attribution.append({
        "byte": ord("z"),
        "color": "\033[0;33m",
        "client_id": 0,
        "timestamp": 0,  # epoch — definitely expired
    })
    result = bridge._format_com_data_with_attribution(b"z")
    # Should NOT have color — entry was cleaned before matching
    assert b"\033[0;33m" not in result, \
        f"Expired entry should not color output, got {result!r}"


def test_attribution_multi_client_order():
    """Client-1 'a', Client-2 'b', COM echoes 'ab' → correct colors."""
    bridge = SerialBridge("MOCK", tcp_port=0)
    bridge.running = True
    bridge._extract_printable_for_attribution(b"a", "\033[0;33m", 0)  # Yellow
    bridge._extract_printable_for_attribution(b"b", "\033[0;36m", 1)  # Cyan
    result = bridge._format_com_data_with_attribution(b"ab")
    # 'a' should be yellow, 'b' should be cyan
    yellow = "\033[0;33m"
    cyan = "\033[0;36m"
    assert yellow.encode() in result, f"Missing yellow for client-1 'a': {result!r}"
    assert cyan.encode() in result, f"Missing cyan for client-2 'b': {result!r}"


def test_attribution_unmatched_chars_pass_through():
    """COM output that doesn't match any pending entry passes uncolored."""
    bridge = SerialBridge("MOCK", tcp_port=0)
    bridge.running = True
    bridge._extract_printable_for_attribution(b"abc", "\033[0;33m", 0)
    result = bridge._format_com_data_with_attribution(b"xyz> ")
    # "xyz> " has no overlap with "abc" — should be raw, no color codes
    assert b"\033" not in result, \
        f"Unmatched output should not have color codes: {result!r}"
    assert result == b"xyz> ", f"Expected raw pass-through: {result!r}"


def test_attribution_partial_match_fifo():
    """Only the head of the queue is matched; partial match = FIFO."""
    bridge = SerialBridge("MOCK", tcp_port=0)
    bridge.running = True
    bridge._extract_printable_for_attribution(b"xyz", "\033[0;33m", 0)
    # COM echoes "xqz" — 'x' matches head, 'q' doesn't match 'y' (2nd)
    result = bridge._format_com_data_with_attribution(b"xqz")
    # 'x' should be yellow, 'q' and 'z' should be raw
    yellow = "\033[0;33m"
    yellow_b = yellow.encode()
    # Count: only 'x' gets colored
    assert result.count(yellow_b) == 1, \
        f"Expected 1 colored char ('x'), got: {result!r}"
    # 'q' and 'z' are still in the result as raw bytes
    assert b"q" in result and b"z" in result


def test_attribution_control_chars_not_colored_in_output():
    """Control chars in COM output are never color-wrapped."""
    bridge = SerialBridge("MOCK", tcp_port=0)
    bridge.running = True
    # COM outputs \r\nfmsh>
    result = bridge._format_com_data_with_attribution(b"\r\nfmsh> ")
    assert b"\033" not in result, \
        f"Control chars should not be colored: {result!r}"
    assert result == b"\r\nfmsh> ", f"Expected raw: {result!r}"


# ── Integration: TelnetClient with bridge (if available) ──────────────


def test_telnet_client_with_bridge():
    """End-to-end: TelnetClient connects to a bridge this test owns.

    The bridge runs over a mock serial device on an OS-assigned loopback
    port, so the test never probes, depends on, or reuses a pre-existing
    localhost service.  The teardown is strict: the client is disconnected
    in ``finally``, the loop-cleanup coroutine is awaited to completion
    before the loop stops, leftover tasks are cancelled/awaited, and the
    bridge thread is joined and verified dead so ``-X dev`` sees no leaked
    task, socket or event loop.
    """
    import asyncio

    from harness import start_mock_bridge, stop_mock_bridge

    received = []
    connected = []
    errors = []
    holder = {}

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        holder["loop"] = loop
        try:
            bridge, mock, port = loop.run_until_complete(start_mock_bridge())
            holder["bridge"] = bridge
            holder["port"] = port
            loop.run_forever()
        except Exception as exc:
            errors.append(exc)
        finally:
            loop.close()

    bridge_thread = threading.Thread(target=_run, daemon=True)
    bridge_thread.start()

    wait_deadline = time.time() + 5
    while "port" not in holder and time.time() < wait_deadline:
        time.sleep(0.05)
    assert "port" in holder, f"Own bridge did not start: {errors!r}"

    loop = holder["loop"]
    bridge = holder["bridge"]
    client = None

    async def _cleanup_loop():
        # Cancel/await leftover tasks (client handlers, serial reader) first so
        # the loop drains and wait_closed() has no active connections to block on.
        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks(loop) if t is not current]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # Then fully release the mock bridge (raises RuntimeError on failure).
        await stop_mock_bridge(bridge)

    try:
        client = TelnetClient(host="127.0.0.1", port=holder["port"])
        client.on_data = lambda d: received.append(d)
        client.on_connected = lambda: connected.append(True)

        ok = client.connect()
        assert ok, "TelnetClient failed to connect"
        time.sleep(0.5)

        # Should have received the welcome banner
        assert len(connected) == 1, "on_connected not called"
        assert len(received) > 0, "No data received from bridge"
        all_data = b"".join(received)
        assert b"Connected as" in all_data or b"Client-" in all_data, \
            f"Expected welcome banner, got: {all_data[:200]!r}"

        # Send a test message
        client.send(b"test_message\r\n")
        time.sleep(0.3)
        assert client.connected is True, "client should still be connected"

        client.disconnect()
        assert client.connected is False, "client must be disconnected"
    finally:
        # 1. Always disconnect the client so its socket is released.
        if client is not None:
            client.disconnect()

        # 2. Schedule cleanup on the loop and WAIT for the returned Future to
        #    complete before stopping the loop.
        fut = asyncio.run_coroutine_threadsafe(_cleanup_loop(), loop)
        try:
            fut.result(timeout=5)
        except Exception as exc:
            errors.append(exc)

        # 3. Stop the loop, then verify the bridge thread actually exited.
        loop.call_soon_threadsafe(loop.stop)
        bridge_thread.join(timeout=5)
        assert bridge_thread.is_alive() is False, "bridge thread did not exit"
        assert not errors, f"Bridge thread/cleanup errors: {errors}"


# ── Runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    tests = [
        test_filter_strips_iac_will_echo,
        test_filter_strips_iac_do_sga,
        test_filter_passes_normal_data,
        test_filter_iac_iac_escape,
        test_filter_mixed_content,
        test_filter_incomplete_sequence,
        test_filter_subnegotiation,
        test_filter_two_byte_commands,
        test_filter_strips_iac_ec_by_default,
        test_filter_maps_iac_ec_to_del_for_bridge_input,
        test_bridge_echo_drops_backspace_controls,
        test_nvt_cr_nul_to_cr,
        test_nvt_cr_nul_split_across_feeds,
        test_nvt_crlf_to_single_cr_and_preserves_literal_nul,
        test_nvt_crlf_split_across_feeds_to_single_cr,
        test_client_initial_state,
        test_client_connect_invalid_port,
        test_client_disconnect_when_not_connected,
        test_client_send_when_not_connected,
        test_client_callbacks_assignable,
        test_filter_negotiation_callback,
        test_telnet_client_with_bridge,
        # Patch 9: device echo attribution
        test_attribution_printable_chars_colored,
        test_attribution_control_chars_not_queued,
        test_attribution_split_escape_sequences_not_queued,
        test_attribution_escape_state_isolated_by_client,
        test_attribution_backspace_not_in_queue,
        test_attribution_expired_entries_not_matched,
        test_attribution_multi_client_order,
        test_attribution_unmatched_chars_pass_through,
        test_attribution_partial_match_fifo,
        test_attribution_control_chars_not_colored_in_output,
    ]

    passed = 0
    failed = 0
    skipped = 0
    for test in tests:
        try:
            result = test()
            if result is False:
                skipped += 1
            else:
                print(f"  [PASS] {test.__name__}")
                passed += 1
        except Exception:
            print(f"  [FAIL] {test.__name__}")
            traceback.print_exc()
            failed += 1

    print(f"\nRESULTS: {passed} passed, {failed} failed, {skipped} skipped, {len(tests)} total")
    sys.exit(0 if failed == 0 else 1)

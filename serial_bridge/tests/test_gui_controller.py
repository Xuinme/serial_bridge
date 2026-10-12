"""
Unit tests for BridgeController lifecycle and event system.

``app.controller.SerialBridge`` is patched with a deterministic
:class:`FakeBridge`, so tests exercise both a successful start/stop and a
graceful start failure without opening a real COM port or relying on a
nonexistent port name.
"""
import sys
import tempfile
import time
from pathlib import Path as _Path
from unittest import mock

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import app.controller as controller_mod
from app.controller import BridgeController
from app.models import BridgeConfig, BridgeEvent
from fake_bridge import FakeBridge


def _wait_for(ctrl, *kinds, timeout=3.0):
    """Poll until every requested event kind is seen, preserving each batch."""
    expected = set(kinds)
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        for e in ctrl.event_bus.drain():
            seen.add(e.kind)
        if expected <= seen:
            return True
        time.sleep(0.05)
    return expected <= seen


def test_initial_state():
    """Controller starts with correct initial state."""
    ctrl = BridgeController(BridgeConfig(com_port="MOCK", baud_rate=9600))
    assert ctrl.state.running is False
    assert ctrl.state.starting is False
    assert ctrl.state.client_count == 0
    assert ctrl.snapshot_clients() == []


def test_start_bridge_sets_starting():
    """start_bridge() sets the starting flag immediately."""
    ctrl = BridgeController(BridgeConfig(com_port="MOCK"))
    with mock.patch.object(controller_mod.threading.Thread, "start") as start:
        try:
            ctrl.start_bridge()
            assert ctrl.state.starting is True
            assert _wait_for(ctrl, "bridge_starting")
            start.assert_called_once()
        finally:
            ctrl.shutdown()


def test_start_succeeds_and_stops_in_order():
    """Successful start produces bridge_started then bridge_stopped."""
    FakeBridge.mode = "success"
    ctrl = BridgeController(BridgeConfig(com_port="MOCK", tcp_port=0))
    thread = None
    loop = None
    bridge = None
    with mock.patch.object(controller_mod, "SerialBridge", FakeBridge):
        try:
            ctrl.start_bridge()
            thread = ctrl._thread
            assert thread is not None

            # Controller owns/holds the bridge and its event loop.
            assert _wait_for(ctrl, "bridge_started")
            assert ctrl.state.running is True
            assert isinstance(ctrl._bridge, FakeBridge)
            bridge = ctrl._bridge
            loop = ctrl._loop
            assert loop is not None

            # Normal stop emits bridge_stopped and flips state.
            ctrl.stop_bridge()
            assert _wait_for(ctrl, "bridge_stopped")
            assert ctrl.state.running is False
        finally:
            ctrl.shutdown()

    assert thread is not None and thread.is_alive() is False
    assert loop is not None and loop.is_closed()
    assert bridge is not None and bridge.stop_calls == 1
    assert bridge.server is None and bridge.serial_port is None
    assert ctrl._thread is None
    assert ctrl._bridge is None


def test_start_fails_gracefully():
    """Deterministic start failure is handled gracefully."""
    FakeBridge.mode = "fail"
    ctrl = BridgeController(BridgeConfig(com_port="MOCK"))
    thread = None
    with mock.patch.object(controller_mod, "SerialBridge", FakeBridge):
        try:
            ctrl.start_bridge()
            thread = ctrl._thread
            assert _wait_for(ctrl, "bridge_start_failed")
            assert ctrl.state.running is False
            assert ctrl.state.last_error == "mock start failure"
        finally:
            ctrl.shutdown()
    assert thread is not None and thread.is_alive() is False


def test_runtime_serial_failure_stops_and_can_restart():
    """A serial disconnect becomes one stopped state and permits restart."""
    FakeBridge.mode = "runtime_fail"
    ctrl = BridgeController(BridgeConfig(com_port="MOCK"))
    failed_thread = None
    failed_bridge = None
    with mock.patch.object(controller_mod, "SerialBridge", FakeBridge):
        try:
            ctrl.start_bridge()
            failed_thread = ctrl._thread
            assert _wait_for(ctrl, "bridge_started")
            failed_bridge = ctrl._bridge
            assert failed_bridge is not None
            assert _wait_for(ctrl, "bridge_runtime_failed", "bridge_stopped")

            assert ctrl.state.running is False
            assert ctrl.state.starting is False
            assert ctrl.state.status_text == "Stopped"
            assert ctrl.state.last_error == "mock serial disconnected"

            if failed_thread is not None:
                failed_thread.join(timeout=2)
            assert failed_thread is not None and failed_thread.is_alive() is False

            FakeBridge.mode = "success"
            ctrl.start_bridge()
            assert ctrl.state.last_error is None
            assert _wait_for(ctrl, "bridge_started")
            assert ctrl.state.running is True
        finally:
            ctrl.shutdown()

    assert failed_bridge is not None and failed_bridge.stop_calls == 1
    assert failed_bridge.server is None and failed_bridge.serial_port is None


def test_double_start_creates_no_second_thread():
    """Calling start_bridge() twice does not spawn a second thread."""
    FakeBridge.mode = "success"
    ctrl = BridgeController(BridgeConfig(com_port="MOCK"))
    first_thread = None
    with mock.patch.object(controller_mod, "SerialBridge", FakeBridge):
        try:
            ctrl.start_bridge()
            first_thread = ctrl._thread
            # Second call while starting/running is a genuine no-op: same thread.
            ctrl.start_bridge()
            second_thread = ctrl._thread
            assert second_thread is first_thread, \
                "start_bridge() must not create a new thread"
            assert _wait_for(ctrl, "bridge_started")
        finally:
            ctrl.shutdown()
    assert first_thread is not None and first_thread.is_alive() is False


def test_stop_when_not_running():
    """stop_bridge() when not running doesn't crash."""
    ctrl = BridgeController()
    ctrl.stop_bridge()
    ctrl.shutdown()
    # Should not raise


def test_event_bus_drain_clears_queue():
    """After draining, the event bus should be empty."""
    ctrl = BridgeController()
    ctrl.event_bus.push(BridgeEvent(kind="test1"))
    ctrl.event_bus.push(BridgeEvent(kind="test2"))
    events = ctrl.event_bus.drain()
    assert len(events) == 2
    events = ctrl.event_bus.drain()
    assert len(events) == 0


def test_snapshot_clients_empty():
    """snapshot_clients returns empty list initially."""
    ctrl = BridgeController()
    assert ctrl.snapshot_clients() == []


def test_config_preserved():
    """Controller preserves the config it's given."""
    cfg = BridgeConfig(com_port="COM5", baud_rate=38400, tcp_port=8888)
    ctrl = BridgeController(cfg)
    assert ctrl.config.com_port == "COM5"
    assert ctrl.config.baud_rate == 38400
    assert ctrl.config.tcp_port == 8888


def test_shutdown_cleans_up():
    """shutdown() joins the worker thread and clears handles."""
    FakeBridge.mode = "success"
    ctrl = BridgeController(BridgeConfig(com_port="MOCK"))
    thread = None
    loop = None
    bridge = None
    with mock.patch.object(controller_mod, "SerialBridge", FakeBridge):
        try:
            ctrl.start_bridge()
            assert _wait_for(ctrl, "bridge_started")
            thread = ctrl._thread
            loop = ctrl._loop
            bridge = ctrl._bridge
        finally:
            ctrl.shutdown()
    assert thread is not None and thread.is_alive() is False
    assert loop is not None and loop.is_closed()
    assert bridge is not None and bridge.stop_calls == 1
    assert bridge.server is None and bridge.serial_port is None
    assert ctrl._thread is None
    assert ctrl._bridge is None
    assert ctrl.state.running is False


def test_event_bridge_starting():
    """A bridge_starting event is always emitted on start."""
    FakeBridge.mode = "success"
    ctrl = BridgeController(BridgeConfig(com_port="MOCK"))
    thread = None
    with mock.patch.object(controller_mod, "SerialBridge", FakeBridge):
        try:
            ctrl.start_bridge()
            thread = ctrl._thread
            assert _wait_for(ctrl, "bridge_starting", "bridge_started")
        finally:
            ctrl.shutdown()
    assert thread is not None and thread.is_alive() is False


def test_live_traffic_logging_toggle_keeps_bridge_running_and_cleans_thread():
    """Live logging settings apply without restarting an active bridge."""
    FakeBridge.mode = "success"
    baseline = len([
        thread for thread in controller_mod.threading.enumerate()
        if thread.name.startswith("traffic-log-")
    ])
    with tempfile.TemporaryDirectory() as td:
        ctrl = BridgeController(
            BridgeConfig(com_port="MOCK", baud_rate=115200, tcp_port=9999),
            session_id="live-log",
            session_name="Live log",
        )
        bridge = None
        with mock.patch.object(controller_mod, "SerialBridge", FakeBridge):
            try:
                ctrl.start_bridge()
                assert _wait_for(ctrl, "bridge_started")
                bridge = ctrl._bridge
                assert bridge is not None
                assert ctrl._traffic_log.active is False

                ctrl.configure_traffic_logging(True, td)
                assert ctrl._traffic_log.active is True
                path = ctrl._traffic_log.current_path
                ctrl._on_bridge_traffic("SERIAL_TX", b"whoami\r", "Client-1")

                ctrl.configure_traffic_logging(False, td)
                assert ctrl._traffic_log.active is False
                assert ctrl.state.running is True
                assert ctrl._bridge is bridge
                assert path is not None and path.exists()
                assert "77 68 6F 61 6D 69 0D" in path.read_text(encoding="utf-8")
            finally:
                ctrl.shutdown()

    assert len([
        thread for thread in controller_mod.threading.enumerate()
        if thread.name.startswith("traffic-log-")
    ]) == baseline


# ── Runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    tests = [
        test_initial_state,
        test_start_bridge_sets_starting,
        test_start_succeeds_and_stops_in_order,
        test_start_fails_gracefully,
        test_runtime_serial_failure_stops_and_can_restart,
        test_double_start_creates_no_second_thread,
        test_stop_when_not_running,
        test_event_bus_drain_clears_queue,
        test_snapshot_clients_empty,
        test_config_preserved,
        test_shutdown_cleans_up,
        test_event_bridge_starting,
        test_live_traffic_logging_toggle_keeps_bridge_running_and_cleans_thread,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  [PASS] {test.__name__}")
            passed += 1
        except Exception:
            print(f"  [FAIL] {test.__name__}")
            traceback.print_exc()
            failed += 1

    print(f"\nRESULTS: {passed} passed, {failed} failed, {len(tests)} total")
    sys.exit(0 if failed == 0 else 1)

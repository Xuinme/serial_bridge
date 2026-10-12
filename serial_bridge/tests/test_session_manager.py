"""
Automated tests for SessionManager and the multi-session domain models
(TASK-007).

API-level tests drive an in-memory FakeController (no threads, no COM).  A
final resource test drives real BridgeController instances with the FakeBridge
fake so it proves two sessions share no bridge thread and that shutdown_all
releases every thread.
"""
import sys
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.controller as controller_mod
from app.events import EventBus
from app.models import (
    AppSettings,
    BridgeConfig,
    BridgeEvent,
    BridgeRuntimeState,
    SessionConfig,
    SessionEvent,
    SessionSnapshot,
)
from app.session_manager import SessionManager
from app.tcp_ports import TcpPortUnavailableError
from fake_bridge import FakeBridge

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


# -- Fakes -------------------------------------------------------------

class FakeState:
    def __init__(self):
        self.running = False
        self.starting = False
        self.status_text = "Stopped"
        self.client_count = 0
        self.last_error = None


class FakeController:
    def __init__(self, config, fail_on_start=False, shutdown_error=None):
        self.config = config
        self.state = FakeState()
        self.event_bus = EventBus()
        self._fail_on_start = fail_on_start
        self._shutdown_error = shutdown_error
        self.start_calls = 0
        self.stop_calls = 0
        self.shutdown_calls = 0

    def start_bridge(self):
        if self.state.running or self.state.starting:
            return
        self.start_calls += 1
        self.state.starting = True
        self.state.status_text = "Starting"
        self.event_bus.push(BridgeEvent(kind="bridge_starting"))
        if self._fail_on_start:
            self.state.starting = False
            self.state.running = False
            self.state.status_text = "Start failed"
            self.state.last_error = "mock start failure"
            self.event_bus.push(BridgeEvent(kind="bridge_start_failed", message="mock start failure"))
        else:
            self.state.starting = False
            self.state.running = True
            self.state.status_text = "Running"
            self.event_bus.push(BridgeEvent(kind="bridge_started", message="mock started"))

    def stop_bridge(self):
        if not (self.state.running or self.state.starting):
            return
        self.stop_calls += 1
        self.state.running = False
        self.state.status_text = "Stopped"
        self.event_bus.push(BridgeEvent(kind="bridge_stopped"))

    def shutdown(self):
        self.shutdown_calls += 1
        if self._shutdown_error:
            raise self._shutdown_error
        self.stop_bridge()


def make_manager(port_available=lambda _port: True):
    return SessionManager(
        controller_factory=lambda config: FakeController(config),
        tcp_port_available=port_available,
    )


def _wait_until(cond, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


# -- Models ------------------------------------------------------------

def test_models_defaults():
    cfg = SessionConfig(session_id="s1", name="A", com_port="COM1")
    check("SessionConfig defaults",
          cfg.baud_rate == 115200 and cfg.tcp_port == 9999 and
          cfg.newline_mode == "crlf" and cfg.auto_reconnect is False)
    app = AppSettings()
    check("AppSettings defaults",
          app.schema_version == 2 and app.close_action == "ask" and
          app.active_session_id is None and app.sessions == [])
    snap = SessionSnapshot(
        session_id="s", name="n", com_port="c", baud_rate=9600, tcp_port=1,
        newline_mode="lf", auto_reconnect=True,
        running=False, starting=False, status_text="Stopped", client_count=0,
        last_error=None,
    )
    check("SessionSnapshot fields",
          snap.session_id == "s" and snap.client_count == 0 and
          snap.newline_mode == "lf" and snap.auto_reconnect is True)
    ev = SessionEvent(session_id="s", kind="k", timestamp=1.0)
    check("SessionEvent payload default", ev.message == "" and ev.payload == {})


# -- Create / query ----------------------------------------------------

def test_create_session_auto_id():
    mgr = make_manager()
    s1 = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    s2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM2", tcp_port=10000))
    check("auto ids non-empty & unique", bool(s1) and bool(s2) and s1 != s2)
    check("auto ids are not list indices", s1 != "0" and s2 != "1")
    check("auto ids are uuid hex", len(s1) == 32 and len(s2) == 32)


def test_create_session_explicit_id():
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="myid", name="A", com_port="COM1"))
    check("explicit id preserved", sid == "myid")


def test_list_and_snapshot():
    mgr = make_manager()
    s1 = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", baud_rate=9600, tcp_port=9999))
    s2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM2", baud_rate=115200, tcp_port=10000))
    snaps = mgr.list_sessions()
    check("list preserves insertion order", [s.session_id for s in snaps] == [s1, s2])
    snap = mgr.get_snapshot(s1)
    check("snapshot fields",
          snap.name == "A" and snap.com_port == "COM1" and snap.baud_rate == 9600 and
          snap.tcp_port == 9999 and snap.running is False and snap.status_text == "Stopped")


def test_get_controller_and_keyerror():
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1"))
    ctrl = mgr.get_controller(sid)
    check("get_controller returns controller",
          hasattr(ctrl, "start_bridge") and ctrl.config.session_id == sid)
    for op in (mgr.get_snapshot, mgr.get_controller, mgr.start_session,
               mgr.stop_session, mgr.delete_session):
        try:
            op("nope")
            ok = False
        except KeyError:
            ok = True
        check(f"unknown id -> KeyError ({op.__name__})", ok)


# -- Validation --------------------------------------------------------

def test_validation_rejects_invalid():
    mgr = make_manager()
    cases = [
        (SessionConfig(session_id="", name="", com_port="", tcp_port=9999), "empty COM rejected"),
        (SessionConfig(session_id="", name="", com_port="COM1", baud_rate=0), "zero baud rejected"),
        (SessionConfig(session_id="", name="", com_port="COM1", baud_rate=-1), "neg baud rejected"),
        (SessionConfig(session_id="", name="", com_port="COM1", tcp_port=0), "tcp 0 rejected"),
        (SessionConfig(session_id="", name="", com_port="COM1", tcp_port=70000), "tcp>65535 rejected"),
    ]
    for config, label in cases:
        try:
            mgr.create_session(config)
            check(label, False)
        except ValueError:
            check(label, True)


def test_duplicate_rejected():
    mgr = make_manager()
    mgr.create_session(SessionConfig(session_id="dup", name="A", com_port="COM1", tcp_port=9999))
    cases = [
        (SessionConfig(session_id="dup", name="B", com_port="COM2", tcp_port=10000), "duplicate id rejected"),
        (SessionConfig(session_id="", name="B", com_port="COM1", tcp_port=10000), "duplicate COM rejected"),
        (SessionConfig(session_id="", name="B", com_port="COM2", tcp_port=9999), "duplicate TCP rejected"),
    ]
    for config, label in cases:
        try:
            mgr.create_session(config)
            check(label, False)
        except ValueError:
            check(label, True)


def test_com_normalized_before_duplicate_check():
    mgr = make_manager()
    sid = mgr.create_session(
        SessionConfig(session_id="a", name="A", com_port="  COM17  ", tcp_port=9999)
    )
    check("COM whitespace trimmed", mgr.get_snapshot(sid).com_port == "COM17")
    try:
        mgr.create_session(
            SessionConfig(session_id="b", name="B", com_port="com17", tcp_port=10000)
        )
        check("COM duplicate is case-insensitive", False)
    except ValueError:
        check("COM duplicate is case-insensitive", True)


def test_next_tcp_port():
    mgr = make_manager()
    check("empty manager next port", mgr.next_tcp_port() == 9999)
    mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    check("skips 9999", mgr.next_tcp_port() == 10000)
    mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM2", tcp_port=10000))
    check("skips 9999 and 10000", mgr.next_tcp_port() == 10001)
    check("custom start respected", mgr.next_tcp_port(9998) == 9998)


def test_next_tcp_port_skips_os_owned_port():
    mgr = make_manager(port_available=lambda port: port != 10000)
    mgr.create_session(SessionConfig(
        session_id="", name="A", com_port="COM1", tcp_port=9999))
    check("skips configured and OS-owned ports", mgr.next_tcp_port() == 10001)
    try:
        mgr.ensure_tcp_port_available(10000)
        check("OS-owned port rejected", False)
    except TcpPortUnavailableError as exc:
        check("OS-owned port rejected with suggestion",
              exc.port == 10000 and exc.suggested_port == 10001)


# -- Lifecycle ---------------------------------------------------------

def test_start_stop_idempotent():
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    mgr.start_session(sid)
    mgr.start_session(sid)
    check("started running", mgr.get_snapshot(sid).running is True)
    ctrl = mgr.get_controller(sid)
    check("start idempotent single start", ctrl.start_calls == 1)
    mgr.stop_session(sid)
    mgr.stop_session(sid)
    check("stopped", mgr.get_snapshot(sid).running is False)
    check("stop idempotent single stop", ctrl.stop_calls == 1)


def test_stop_stopped_session_has_no_event():
    mgr = SessionManager()
    sid = mgr.create_session(
        SessionConfig(session_id="s", name="A", com_port="COM1", tcp_port=9999)
    )
    mgr.stop_session(sid)
    mgr.stop_session(sid)
    check("stopped session emits no stopping events", mgr.drain_events() == [])


def test_drain_events_attribution():
    mgr = make_manager()
    s1 = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    s2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM2", tcp_port=10000))
    mgr.start_session(s1)
    mgr.start_session(s2)
    events = mgr.drain_events()
    check("events all carry valid session ids", all(e.session_id in (s1, s2) for e in events))
    kinds1 = [e.kind for e in events if e.session_id == s1]
    kinds2 = [e.kind for e in events if e.session_id == s2]
    check("s1 event order", kinds1[:2] == ["bridge_starting", "bridge_started"])
    check("s2 event order", kinds2[:2] == ["bridge_starting", "bridge_started"])


def test_exception_isolation():
    def factory(config):
        return FakeController(config, fail_on_start=str(config.com_port).startswith("FAIL"))
    mgr = SessionManager(controller_factory=factory)
    s1 = mgr.create_session(SessionConfig(session_id="", name="A", com_port="FAILCOM", tcp_port=9999))
    s2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM10", tcp_port=10000))
    mgr.start_session(s1)
    mgr.start_session(s2)
    snap1 = mgr.get_snapshot(s1)
    snap2 = mgr.get_snapshot(s2)
    check("failed session reflects error", snap1.running is False and snap1.last_error == "mock start failure")
    check("other session unaffected", snap2.running is True)


# -- Update / delete / export / shutdown -------------------------------

def test_update_session():
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", baud_rate=9600, tcp_port=9999))
    mgr.update_session(sid, SessionConfig(session_id=sid, name="A2", com_port="COM1", baud_rate=115200, tcp_port=9999))
    snap = mgr.get_snapshot(sid)
    check("update replaces config", snap.name == "A2" and snap.baud_rate == 115200)
    check("update keeps id", snap.session_id == sid)


def test_update_running_rejected():
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    mgr.start_session(sid)
    try:
        mgr.update_session(sid, SessionConfig(session_id=sid, name="A", com_port="COM1", tcp_port=9999))
        check("update running rejected", False)
    except ValueError:
        check("update running rejected", True)


def test_update_duplicate_rejected():
    mgr = make_manager()
    mgr.create_session(SessionConfig(session_id="a", name="A", com_port="COM1", tcp_port=9999))
    sid2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM2", tcp_port=10000))
    try:
        mgr.update_session(sid2, SessionConfig(session_id=sid2, name="B", com_port="COM1", tcp_port=10000))
        check("update duplicate COM rejected", False)
    except ValueError:
        check("update duplicate COM rejected", True)
    mgr.update_session(sid2, SessionConfig(session_id=sid2, name="B", com_port="COM2", tcp_port=10000))
    check("update keeps own com/port", mgr.get_snapshot(sid2).com_port == "COM2")


def test_delete_session():
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    mgr.start_session(sid)
    mgr.delete_session(sid)
    check("delete removes session", mgr.list_sessions() == [])
    try:
        mgr.get_snapshot(sid)
        check("deleted id becomes unknown", False)
    except KeyError:
        check("deleted id becomes unknown", True)
    mgr2 = make_manager()
    sid2 = mgr2.create_session(SessionConfig(session_id="", name="B", com_port="COM2", tcp_port=10000))
    mgr2.start_session(sid2)
    ctrl = mgr2.get_controller(sid2)
    mgr2.delete_session(sid2)
    check("delete running shuts down first", ctrl.shutdown_calls >= 1)


def test_export_settings():
    mgr = make_manager()
    s1 = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    s2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM2", baud_rate=9600, tcp_port=10000))
    app = mgr.export_settings("minimize_to_tray", s1)
    check("export schema_version", app.schema_version == 2)
    check("export close_action", app.close_action == "minimize_to_tray")
    check("export active_session_id", app.active_session_id == s1)
    check("export sessions",
          [x.session_id for x in app.sessions] == [s1, s2] and app.sessions[1].baud_rate == 9600)


def test_shutdown_all_continues():
    def factory(config):
        if str(config.com_port).startswith("RAISE"):
            return FakeController(config, shutdown_error=RuntimeError("boom"))
        return FakeController(config)
    mgr = SessionManager(controller_factory=factory)
    mgr.create_session(SessionConfig(session_id="", name="A", com_port="RAISE1", tcp_port=9999))
    s2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM10", tcp_port=10000))
    mgr.start_session(s2)
    try:
        mgr.shutdown_all()
        check("shutdown_all raises on failure", False)
    except RuntimeError as exc:
        check("shutdown_all aggregates error", "boom" in str(exc))
    check("other session still closed", mgr.get_snapshot(s2).running is False)
    check("other session shutdown called", mgr.get_controller(s2).shutdown_calls >= 1)


def test_shutdown_all_idempotent():
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    mgr.start_session(sid)
    mgr.shutdown_all()
    mgr.shutdown_all()
    check("shutdown_all idempotent (no error)", True)
    check("session stopped after shutdown_all", mgr.get_snapshot(sid).running is False)


def test_restart_after_stop():
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    mgr.start_session(sid)
    mgr.stop_session(sid)
    mgr.start_session(sid)
    check("session restarts after stop", mgr.get_snapshot(sid).running is True)


# -- Resource isolation with real controllers --------------------------

class SelectiveFakeBridge(FakeBridge):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._select = "fail" if str(self.com_port).startswith("FAIL") else "success"

    async def start(self):
        self.start_calls += 1
        if self._select == "fail":
            self._emit("bridge_start_failed", "mock start failure")
            self.running = False
            return False
        self.server = object()
        self.serial_port = object()
        self.running = True
        self._emit("bridge_started", "mock bridge started")
        return True


def test_real_controller_resource_isolation():
    baseline = [t for t in threading.enumerate() if t.name == "bridge-thread"]
    with mock.patch.object(controller_mod, "SerialBridge", SelectiveFakeBridge):
        mgr = SessionManager()
        s_ok = mgr.create_session(SessionConfig(session_id="", name="OK", com_port="COM1", tcp_port=9999))
        s_fail = mgr.create_session(SessionConfig(session_id="", name="FAIL", com_port="FAILCOM", tcp_port=10000))
        try:
            mgr.start_session(s_ok)
            mgr.start_session(s_fail)
            ok_started = _wait_until(lambda: mgr.get_snapshot(s_ok).running is True)
            fail_errored = _wait_until(lambda: mgr.get_snapshot(s_fail).last_error is not None)
            check("ok session running", ok_started)
            check("fail session errored", fail_errored)
        finally:
            mgr.shutdown_all()
    after = [t for t in threading.enumerate() if t.name == "bridge-thread"]
    check("no residual bridge-thread after shutdown_all", after == baseline)


# -- Runner ------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    tests = [
        test_models_defaults,
        test_create_session_auto_id,
        test_create_session_explicit_id,
        test_list_and_snapshot,
        test_get_controller_and_keyerror,
        test_validation_rejects_invalid,
        test_duplicate_rejected,
        test_com_normalized_before_duplicate_check,
        test_next_tcp_port,
        test_next_tcp_port_skips_os_owned_port,
        test_start_stop_idempotent,
        test_stop_stopped_session_has_no_event,
        test_drain_events_attribution,
        test_exception_isolation,
        test_update_session,
        test_update_running_rejected,
        test_update_duplicate_rejected,
        test_delete_session,
        test_export_settings,
        test_shutdown_all_continues,
        test_shutdown_all_idempotent,
        test_restart_after_stop,
        test_real_controller_resource_isolation,
    ]

    for test in tests:
        try:
            test()
            print(f"  [PASS] {test.__name__}")
        except Exception:
            print(f"  [FAIL] {test.__name__}")
            traceback.print_exc()
            results.append((f"EXCEPTION in {test.__name__}", False, ""))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\nRESULTS: {passed} passed, {failed} failed, {len(results)} total checks")
    sys.exit(0 if failed == 0 else 1)

"""Multi-session close/lifecycle integration tests (TASK-009).

Drives the TASK-005 close/tray lifecycle against the TASK-007 SessionManager,
adapted through the _SessionControllerFacade (main.py).  Uses fake serial
controllers, fake terminals, a fake tray and a fake root, so no real COM, TCP,
thread, or Tk are touched.  Any failure returns a non-zero process exit code.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.events import EventBus
from app.models import AppSettings, BridgeEvent, SessionConfig
from app.session_manager import SessionManager
from app.tcp_ports import TcpPortUnavailableError
from main import (_CloseLifecycle, _SessionControllerFacade,
                  _TerminalCollection, _AutoSaveService)
import ui.main_window as ui_main_window

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


# -- Fakes --------------------------------------------------------------


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


class FakeTerminal:
    def __init__(self, is_open=True):
        self.is_open = is_open
        self.hide_calls = 0
        self.close_calls = 0

    def hide(self):
        self.hide_calls += 1

    def close(self):
        self.close_calls += 1
        self.is_open = False


class FakeTray:
    def __init__(self, start_ok=True):
        self.start_calls = 0
        self.stop_calls = 0
        self._start_ok = start_ok
        self._running = False

    @property
    def is_running(self):
        return self._running

    def start(self):
        self.start_calls += 1
        if not self._start_ok:
            raise RuntimeError("mock tray start failure")
        self._running = True

    def stop(self):
        self.stop_calls += 1
        self._running = False


class FakeRoot:
    def __init__(self):
        self.withdraw_calls = 0
        self.deiconify_calls = 0
        self.lift_calls = 0
        self.destroy_calls = 0
        self.state_calls = []
        self.after_calls = []

    def withdraw(self):
        self.withdraw_calls += 1

    def deiconify(self):
        self.deiconify_calls += 1

    def lift(self):
        self.lift_calls += 1

    def destroy(self):
        self.destroy_calls += 1

    def state(self, value=None):
        self.state_calls.append(value)
        return "normal"

    def after(self, ms, cb):
        self.after_calls.append(cb)
        return len(self.after_calls)


def make_manager(n_sessions=2):
    def factory(config):
        return FakeController(config)
    mgr = SessionManager(
        controller_factory=factory,
        tcp_port_available=lambda _port: True,
    )
    for i in range(n_sessions):
        mgr.create_session(SessionConfig(
            session_id="s%d" % i, name="S%d" % i, com_port="COM%d" % (i + 1),
            tcp_port=9999 + i,
        ))
    return mgr


def build_lifecycle(*, close_action="ask", running=True, tray_start_ok=True):
    mgr = make_manager()
    settings = AppSettings(schema_version=2, close_action=close_action,
                           active_session_id="s0", sessions=[])
    facade = _SessionControllerFacade(mgr, settings)
    sids = [s.session_id for s in mgr.list_sessions()]
    terminated = {sid: FakeTerminal() for sid in sids}
    terminal_collection = _TerminalCollection(terminated)
    root = FakeRoot()
    tray = FakeTray(start_ok=tray_start_ok)
    saved = []

    def _save(config):
        saved.append(config.close_action)

    lifecycle = _CloseLifecycle(
        root, facade, _save, lambda: terminal_collection, tray,
        lambda *a, **k: None,
    )
    lifecycle._saved = saved
    lifecycle._terminals = terminated
    lifecycle._mgr = mgr
    lifecycle._facade = facade
    lifecycle._collection = terminal_collection
    if running:
        for sid in sids:
            mgr.start_session(sid)
    return lifecycle, root, mgr, terminated, saved, tray


def _dialog(result):
    return mock.patch.object(ui_main_window, "show_close_dialog", return_value=result)


# -- Tests --------------------------------------------------------------


def test_facade_reports_any_running_and_shutdown_all():
    mgr = make_manager()
    settings = AppSettings(close_action="ask")
    facade = _SessionControllerFacade(mgr, settings)
    check("facade: not running initially", facade.state.running is False)
    mgr.start_session("s0")
    check("facade: any running when one session runs", facade.state.running is True)
    facade.shutdown()
    check("facade: shutdown_all stops every session",
          all(not s.running for s in mgr.list_sessions()))


def test_ask_cancel_in_multisession():
    lifecycle, root, mgr, term, saved, tray = build_lifecycle(close_action="ask")
    with _dialog(("cancel", False)):
        lifecycle.request_close()
    check("cancel: no tray start", tray.start_calls == 0)
    check("cancel: no session shutdown",
          all(not mgr.get_controller(s).shutdown_calls for s in ["s0", "s1"]))
    check("cancel: sessions still running",
          all(s.running for s in mgr.list_sessions()))
    check("cancel: pref still ask", lifecycle._facade.config.close_action == "ask")


def test_ask_minimize_not_remember():
    lifecycle, root, mgr, term, saved, tray = build_lifecycle(close_action="ask")
    with _dialog(("minimize", False)):
        lifecycle.request_close()
    check("minimize: tray started once", tray.start_calls == 1)
    check("minimize: sessions keep running",
          all(s.running for s in mgr.list_sessions()))
    check("minimize: all terminals hidden",
          all(t.hide_calls == 1 for t in term.values()))
    check("minimize: pref stays ask", lifecycle._facade.config.close_action == "ask")
    check("minimize: root withdrawn", root.withdraw_calls == 1)


def test_ask_minimize_remember_persists():
    lifecycle, root, mgr, term, saved, tray = build_lifecycle(close_action="ask")
    with _dialog(("minimize", True)):
        lifecycle.request_close()
    check("minimize-remember: pref saved to minimize_to_tray",
          lifecycle._facade.config.close_action == "minimize_to_tray")
    check("minimize-remember: saved once", saved == ["minimize_to_tray"])
    check("minimize-remember: sessions keep running",
          all(s.running for s in mgr.list_sessions()))


def test_ask_exit_closes_all_and_releases():
    lifecycle, root, mgr, term, saved, tray = build_lifecycle(close_action="ask")
    with _dialog(("exit", False)):
        lifecycle.request_close()
    check("exit: save called", len(saved) >= 1)
    check("exit: all terminals closed", all(t.close_calls == 1 for t in term.values()))
    check("exit: all sessions shutdown", all(not s.running for s in mgr.list_sessions()))
    check("exit: tray stopped", tray.stop_calls == 1)
    check("exit: root destroyed", root.destroy_calls == 1)


def test_configured_minimize_and_exit():
    minimum = build_lifecycle(close_action="minimize_to_tray")
    with _dialog(("cancel", False)):
        minimum[0].request_close()
    check("configured-minimize: starts tray without dialog", minimum[5].start_calls == 1)
    check("configured-minimize: sessions keep running",
          all(s.running for s in minimum[2].list_sessions()))

    lifesc = build_lifecycle(close_action="exit")
    with _dialog(("cancel", False)):
        lifesc[0].request_close()
    check("configured-exit: sessions shutdown", all(not s.running for s in lifesc[2].list_sessions()))
    check("configured-exit: root destroyed", lifesc[1].destroy_calls == 1)
    check("configured-exit: tray stopped", lifesc[5].stop_calls == 1)


def test_safe_exit_idempotent():
    lifecycle, root, mgr, term, saved, tray = build_lifecycle(close_action="ask")
    lifecycle.safe_exit()
    lifecycle.safe_exit()
    check("idempotent: root destroyed once", root.destroy_calls == 1)
    check("idempotent: tray stopped once", tray.stop_calls == 1)
    check("idempotent: terminal closed once", all(t.close_calls == 1 for t in term.values()))
    check("idempotent: no session running", all(not s.running for s in mgr.list_sessions()))


def test_safe_exit_uses_optional_window_teardown_once():
    lifecycle, root, mgr, term, saved, tray = build_lifecycle(close_action="exit")
    teardown_calls = []
    lifecycle._window_teardown = lambda: teardown_calls.append(True)
    lifecycle.safe_exit()
    lifecycle.safe_exit()
    check("window-teardown: optional callback called once", teardown_calls == [True])
    check("window-teardown: lifecycle does not also destroy root",
          root.destroy_calls == 0)
    check("window-teardown: other resources still released",
          all(not s.running for s in mgr.list_sessions()) and
          all(t.close_calls == 1 for t in term.values()) and
          tray.stop_calls == 1)


def test_safe_exit_continues_after_save_failure():
    mgr = make_manager()
    settings = AppSettings(close_action="ask")
    facade = _SessionControllerFacade(mgr, settings)
    term = {sid: FakeTerminal() for sid in ["s0", "s1"]}
    collection = _TerminalCollection(term)
    root = FakeRoot()
    tray = FakeTray()
    mgr.start_session("s0")

    def broken_save(config):
        raise OSError("settings write failed")

    lifecycle = _CloseLifecycle(root, facade, broken_save, lambda: collection, tray,
                                lambda *a, **k: None)
    with mock.patch.object(sys, "stderr"):
        lifecycle.safe_exit()
    check("savefail: terminals still closed", all(t.close_calls == 1 for t in term.values()))
    check("savefail: sessions still shutdown", all(not s.running for s in mgr.list_sessions()))
    check("savefail: tray still stopped", tray.stop_calls == 1)
    check("savefail: root still destroyed", root.destroy_calls == 1)


def test_autosave_service_persists_on_mutations():
    saved = []
    callbacks = []

    def persist():
        saved.append(True)

    mgr = make_manager(n_sessions=0)

    def before_update(session_id):
        callbacks.append(("update", session_id, mgr.get_controller(session_id)))

    def before_delete(session_id):
        callbacks.append(("delete", session_id, mgr.get_controller(session_id)))

    svc = _AutoSaveService(
        mgr, persist,
        before_delete=before_delete,
        before_update=before_update,
    )
    sid = svc.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    original = mgr.get_controller(sid)
    svc.update_session(sid, SessionConfig(session_id=sid, name="A2", com_port="COM1", tcp_port=9999))
    replacement = mgr.get_controller(sid)
    svc.delete_session(sid)
    check("autosave: create waits for final UI selection", len(saved) == 1)
    check("autosave: update closes old binding before replacement",
          callbacks[0] == ("update", sid, original) and replacement is not original)
    check("autosave: delete callback sees live controller",
          callbacks[1] == ("delete", sid, replacement))
    check("autosave: delete waits for fallback UI selection", len(saved) == 1)
    check("autosave: delegates list_sessions", svc.list_sessions() == [])


def test_autosave_service_rejects_os_owned_tcp_port():
    mgr = SessionManager(
        controller_factory=lambda config: FakeController(config),
        tcp_port_available=lambda port: port != 10000,
    )
    mgr.create_session(SessionConfig(
        session_id="s0", name="A", com_port="COM1", tcp_port=9999))
    svc = _AutoSaveService(mgr, lambda: None)
    try:
        svc.create_session(SessionConfig(
            session_id="", name="B", com_port="COM2", tcp_port=10000))
        check("autosave: OS-owned TCP port rejected before save", False)
    except TcpPortUnavailableError as exc:
        check("autosave: OS-owned TCP port rejected before save",
              exc.port == 10000 and exc.suggested_port == 10001)
    check("autosave: rejected connection was not created",
          [snap.session_id for snap in mgr.list_sessions()] == ["s0"])


# -- Runner -------------------------------------------------------------

if __name__ == "__main__":
    import traceback
    tests = [
        test_facade_reports_any_running_and_shutdown_all,
        test_ask_cancel_in_multisession,
        test_ask_minimize_not_remember,
        test_ask_minimize_remember_persists,
        test_ask_exit_closes_all_and_releases,
        test_configured_minimize_and_exit,
        test_safe_exit_idempotent,
        test_safe_exit_uses_optional_window_teardown_once,
        test_safe_exit_continues_after_save_failure,
        test_autosave_service_persists_on_mutations,
        test_autosave_service_rejects_os_owned_tcp_port,
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

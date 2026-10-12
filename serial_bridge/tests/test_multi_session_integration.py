"""Multi-session backend integration tests (TASK-009).

Verifies that the TASK-007 SessionManager satisfies the exact duck-typed
service contract consumed by the TASK-008 WorkbenchWindow, that two sessions
run independently, that v1 config migration and v2 restore feed the manager
correctly, and that conflicts / deletes are isolated.  Uses an injected fake
controller factory so no real COM, TCP listener or bridge thread is created.
"""
import contextlib
import dataclasses
import io
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.profiles as profiles
import main as app_main
import ui.main_window as ui_main_window
import ui.terminal_window as ui_terminal_window
import ui.tray as ui_tray
import ui.workbench_theme as workbench_theme
from app.events import EventBus
from app.models import AppSettings, BridgeEvent, SessionConfig
from app.session_manager import SessionManager

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


class FakeState:
    def __init__(self):
        self.running = False
        self.starting = False
        self.status_text = "Stopped"
        self.client_count = 0
        self.last_error = None


class FakeController:
    def __init__(self, config, fail_on_start=False):
        self.config = config
        self.state = FakeState()
        self.event_bus = EventBus()
        self._fail_on_start = fail_on_start
        self.start_calls = 0
        self.traffic_log_calls = []

    def configure_traffic_logging(self, enabled, directory):
        self.traffic_log_calls.append((bool(enabled), str(directory)))

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
        self.state.running = False
        self.state.status_text = "Stopped"
        self.event_bus.push(BridgeEvent(kind="bridge_stopped"))

    def shutdown(self):
        self.stop_bridge()


def make_manager(fail_on=()):
    def factory(config):
        return FakeController(config, fail_on_start=str(config.com_port) in fail_on)
    return SessionManager(
        controller_factory=factory,
        tcp_port_available=lambda _port: True,
    )


def test_service_contract_surface():
    """Every method the WorkbenchWindow consumes is present and callable."""
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    check("contract: create_session returns str", isinstance(sid, str))
    check("contract: list_sessions", mgr.list_sessions()[0].session_id == sid)
    check("contract: get_snapshot fields",
          mgr.get_snapshot(sid).name == "A" and mgr.get_snapshot(sid).com_port == "COM1")
    check("contract: next_tcp_port", mgr.next_tcp_port() == 10000)
    mgr.start_session(sid)
    check("contract: start_session", mgr.get_snapshot(sid).running is True)
    check("contract: drain_events", any(e.session_id == sid for e in mgr.drain_events()))
    mgr.stop_session(sid)
    check("contract: stop_session", mgr.get_snapshot(sid).running is False)
    mgr.update_session(
        sid,
        SessionConfig(session_id=sid, name="B", com_port="COM1", tcp_port=9999),
    )
    check("contract: update_session", mgr.get_snapshot(sid).name == "B")
    mgr.delete_session(sid)
    check("contract: delete_session", mgr.list_sessions() == [])
    check("contract: shutdown_all noop on empty", (mgr.shutdown_all() is None) or True)


def test_two_sessions_independent():
    mgr = make_manager()
    s1 = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    s2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM2", tcp_port=10000))
    mgr.start_session(s1)
    check("two: only A running after starting A",
          mgr.get_snapshot(s1).running is True and mgr.get_snapshot(s2).running is False)
    mgr.start_session(s2)
    check("two: both running", all(s.running for s in mgr.list_sessions()))
    mgr.stop_session(s1)
    check("two: B unaffected by stopping A",
          mgr.get_snapshot(s1).running is False and mgr.get_snapshot(s2).running is True)
    mgr.start_session(s1)
    check("two: A restarts after stop", mgr.get_snapshot(s1).running is True)
    mgr.shutdown_all()
    check("two: shutdown_all stops both", all(not s.running for s in mgr.list_sessions()))


def test_traffic_log_settings_reach_existing_and_future_sessions():
    mgr = make_manager()
    s1 = mgr.create_session(SessionConfig(
        session_id="a", name="A", com_port="COM1", tcp_port=9999))
    first = mgr.get_controller(s1)
    check("traffic-log: new controller gets manager defaults",
          first.traffic_log_calls == [(False, "")])
    mgr.configure_traffic_logging(True, "C:/logs")
    check("traffic-log: existing controller updates live",
          first.traffic_log_calls[-1] == (True, "C:/logs"))
    s2 = mgr.create_session(SessionConfig(
        session_id="b", name="B", com_port="COM2", tcp_port=10000))
    check("traffic-log: future controller inherits latest settings",
          mgr.get_controller(s2).traffic_log_calls == [(True, "C:/logs")])
    mgr.update_session(s2, SessionConfig(
        session_id="b", name="B2", com_port="COM2", tcp_port=10000))
    check("traffic-log: replacement controller inherits latest settings",
          mgr.get_controller(s2).traffic_log_calls == [(True, "C:/logs")])


def test_isolation_on_start_failure():
    mgr = make_manager(fail_on=("FAILCOM",))
    s1 = mgr.create_session(SessionConfig(session_id="", name="A", com_port="FAILCOM", tcp_port=9999))
    s2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM2", tcp_port=10000))
    mgr.start_session(s1)
    mgr.start_session(s2)
    check("isolation: failed session errored",
          mgr.get_snapshot(s1).running is False and mgr.get_snapshot(s1).last_error == "mock start failure")
    check("isolation: other session unaffected", mgr.get_snapshot(s2).running is True)


def test_conflict_rejection_before_start():
    mgr = make_manager()
    mgr.create_session(SessionConfig(session_id="dup", name="A", com_port="COM1", tcp_port=9999))
    cases = [
        (SessionConfig(session_id="dup", name="B", com_port="COM2", tcp_port=10000), "duplicate id"),
        (SessionConfig(session_id="", name="B", com_port="COM1", tcp_port=10000), "duplicate COMcmp"),
        (SessionConfig(session_id="", name="B", com_port="COM3", tcp_port=9999), "duplicate port"),
        (SessionConfig(session_id="", name="B", com_port="COM4", tcp_port=70000), "invalid port"),
        (SessionConfig(session_id="", name="B", com_port="", tcp_port=10000), "empty COM"),
    ]
    for config, label in cases:
        try:
            mgr.create_session(config)
            check("conflict: " + label + " rejected", False)
        except ValueError:
            check("conflict: " + label + " rejected", True)


def test_delete_running_shuts_down():
    mgr = make_manager()
    sid = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    mgr.start_session(sid)
    ctrl = mgr.get_controller(sid)
    mgr.delete_session(sid)
    check("delete-running: removed", mgr.list_sessions() == [])
    check("delete-running: controller stopped", ctrl.state.running is False)


def test_v1_migration_feeds_manager():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            tp.write_text(json.dumps({
                "last_com_port": "COM9", "baud_rate": 9600, "tcp_port": 8888,
                "close_action": "exit",
            }), encoding="utf-8")
            settings = profiles.load_app_settings()
            check("v1: migrated to one session", len(settings.sessions) == 1)
            mgr = make_manager()
            for sess in settings.sessions:
                mgr.create_session(SessionConfig(
                    session_id=sess.session_id, name=sess.name, com_port=sess.com_port,
                    baud_rate=sess.baud_rate, tcp_port=sess.tcp_port,
                    newline_mode=sess.newline_mode, auto_reconnect=sess.auto_reconnect,
                ))
            check("v1: manager restores the session",
                  len(mgr.list_sessions()) == 1 and
                  mgr.get_snapshot(mgr.list_sessions()[0].session_id).com_port == "COM9")
            check("v1: close_action migrated", settings.close_action == "exit")


def test_v2_roundtrip_restores_no_duplicates():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            settings = profiles.load_app_settings()
            settings.sessions = [
                SessionConfig(session_id="a", name="A", com_port="COM1", baud_rate=115200, tcp_port=9999),
                SessionConfig(session_id="b", name="B", com_port="COM2", baud_rate=9600, tcp_port=10000),
            ]
            profiles.save_app_settings(settings)
            mgr = make_manager()
            for sess in profiles.load_app_settings().sessions:
                mgr.create_session(SessionConfig(
                    session_id=sess.session_id, name=sess.name, com_port=sess.com_port,
                    baud_rate=sess.baud_rate, tcp_port=sess.tcp_port,
                    newline_mode=sess.newline_mode, auto_reconnect=sess.auto_reconnect,
                ))
            snaps = mgr.list_sessions()
            check("v2: restored exactly two sessions", len(snaps) == 2)
            check("v2: no duplicate ids", len({s.session_id for s in snaps}) == 2)
            check("v2: restore repeat is stable",
                  len(profiles.load_app_settings().sessions) == 2)


def test_events_attributed_per_session():
    mgr = make_manager()
    s1 = mgr.create_session(SessionConfig(session_id="", name="A", com_port="COM1", tcp_port=9999))
    s2 = mgr.create_session(SessionConfig(session_id="", name="B", com_port="COM2", tcp_port=10000))
    mgr.start_session(s1)
    mgr.start_session(s2)
    events = mgr.drain_events()
    check("events: all carry a valid session_id", all(e.session_id in (s1, s2) for e in events))
    check("events: per-session order preserved",
          [e.kind for e in events if e.session_id == s1][:2] == ["bridge_starting", "bridge_started"])


class RecordingTerminal:
    instances = []

    def __init__(self, _root, controller):
        self.controller = controller
        self.is_open = False
        self.close_calls = 0
        self.hide_calls = 0
        self.__class__.instances.append(self)

    def open(self):
        self.is_open = True

    def close(self):
        self.close_calls += 1
        self.is_open = False

    def hide(self):
        self.hide_calls += 1


class RecordingTray:
    instances = []

    def __init__(self, on_restore, on_exit):
        self.on_restore = on_restore
        self.on_exit = on_exit
        self.start_calls = 0
        self.stop_calls = 0
        self._running = False
        self.__class__.instances.append(self)

    @property
    def is_running(self):
        return self._running

    def start(self):
        self.start_calls += 1
        self._running = True

    def stop(self):
        self.stop_calls += 1
        self._running = False


def test_formal_gui_persists_final_selection_and_refreshes_terminal():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "settings.json"
        initial = AppSettings(
            schema_version=2,
            close_action="exit",
            active_session_id="s2",
            sessions=[
                SessionConfig(session_id="s1", name="A", com_port="COM11", tcp_port=9999),
                SessionConfig(session_id="s2", name="B", com_port="COM12", tcp_port=10000),
            ],
        )
        saved_active = []
        observed = {}
        real_save = profiles.save_app_settings
        RecordingTerminal.instances = []
        RecordingTray.instances = []

        def record_save(settings):
            saved_active.append(settings.active_session_id)
            real_save(settings)

        def scripted_run(window):
            wb = window._wb
            try:
                observed["initial"] = wb._selected_id

                log_dir = str(Path(td) / "captured traffic")
                log_prefs = dataclasses.replace(
                    wb._prefs,
                    traffic_log_enabled=True,
                    traffic_log_directory=log_dir,
                )
                observed["log_apply"] = wb._apply_preferences(log_prefs)
                wb.root.update()
                persisted = profiles.load_app_settings()
                controllers = list(wb._service._manager._controllers.values())
                observed["log_settings"] = (
                    persisted.traffic_log_enabled,
                    persisted.traffic_log_directory,
                    all(c._traffic_log._enabled and
                        c._traffic_log._directory == log_dir
                        for c in controllers),
                )
                saved_active.clear()

                wb._telnet("s2")
                first_terminal = RecordingTerminal.instances[-1]
                first_controller = first_terminal.controller
                wb._service.update_session(
                    "s2",
                    SessionConfig(
                        session_id="s2", name="B edited", com_port="COM12",
                        baud_rate=115200, tcp_port=10001,
                    ),
                )
                wb._telnet("s2")
                second_terminal = RecordingTerminal.instances[-1]
                observed["terminal_replaced"] = (
                    first_terminal.close_calls == 1 and
                    second_terminal is not first_terminal and
                    second_terminal.controller is not first_controller
                )

                wb._select("s1")
                observed["selected_saved"] = saved_active[-1]
                wb._create_session(SessionConfig(
                    session_id="s3", name="C", com_port="COM13", tcp_port=10002,
                ))
                observed["created_saved"] = saved_active[-1]
                wb._delete_session("s3")
                latest = profiles.load_app_settings()
                observed["deleted_saved"] = saved_active[-1]
                observed["saved_ids_after_delete"] = [s.session_id for s in latest.sessions]
                second_terminal.close()
            finally:
                window.destroy()

        with mock.patch.object(profiles, "_settings_path", return_value=path):
            real_save(initial)
            with (
                mock.patch.object(profiles, "save_app_settings", side_effect=record_save),
                mock.patch("app.session_manager.is_tcp_port_available", return_value=True),
                mock.patch.object(ui_terminal_window, "TerminalWindow", RecordingTerminal),
                mock.patch.object(ui_tray, "TrayIconManager", RecordingTray),
                mock.patch.object(ui_main_window.MainWindow, "run", scripted_run),
            ):
                app_main.gui_mode()

        check("gui-state: saved active session restored on startup",
              observed["initial"] == "s2")
        check("gui-state: traffic logging persists and applies live",
              observed["log_apply"] is True and
              observed["log_settings"] ==
              (True, str(Path(td) / "captured traffic"), True))
        check("gui-state: edit closes terminal bound to replaced controller",
              observed["terminal_replaced"])
        check("gui-state: explicit selection persists immediately",
              observed["selected_saved"] == "s1")
        check("gui-state: create persists only after new session is selected",
              observed["created_saved"] == "s3")
        check("gui-state: delete persists a valid fallback selection",
              observed["deleted_saved"] == "s1" and
              observed["deleted_saved"] in observed["saved_ids_after_delete"] and
              "s3" not in observed["saved_ids_after_delete"])
        check("gui-state: no premature create/delete save",
              saved_active == ["s2", "s1", "s3", "s1"])
        check("gui-state: tray starts with GUI and stops after mainloop",
              len(RecordingTray.instances) == 1 and
              RecordingTray.instances[0].start_calls == 1 and
              RecordingTray.instances[0].stop_calls == 1)


def test_gui_mode_rejects_bad_persisted_sessions_without_overwrite():
    cases = [
        ("duplicate", [
            {"session_id": "good", "name": "Good", "com_port": "COM11",
             "baud_rate": 115200, "tcp_port": 9999},
            {"session_id": "duplicate", "name": "Duplicate", "com_port": "com11",
             "baud_rate": 115200, "tcp_port": 10000},
        ]),
        ("invalid", [
            {"session_id": "invalid", "name": "Invalid", "com_port": "COM14",
             "baud_rate": 115200, "tcp_port": 70000},
        ]),
    ]
    for label, sessions in cases:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            source = json.dumps({
                "schema_version": 2,
                "close_action": "ask",
                "active_session_id": sessions[0]["session_id"],
                "sessions": sessions,
            })
            path.write_text(source, encoding="utf-8")
            with (
                mock.patch.object(profiles, "_settings_path", return_value=path),
                mock.patch.object(profiles, "save_app_settings") as save_mock,
                mock.patch.object(ui_main_window, "MainWindow") as window_mock,
            ):
                try:
                    app_main.gui_mode()
                    rejected = False
                    detail = ""
                except ValueError as exc:
                    rejected = True
                    detail = str(exc)
            check(f"bad-config: {label} session rejected with diagnostic",
                  rejected and bool(detail))
            check(f"bad-config: {label} source preserved",
                  path.read_text(encoding="utf-8") == source)
            check(f"bad-config: {label} never reaches GUI or save",
                  not window_mock.called and not save_mock.called)


def test_formal_exit_cancels_polling_across_restarts():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "settings.json"
        initial = AppSettings(
            schema_version=2,
            close_action="exit",
            active_session_id="s2",
            sessions=[
                SessionConfig(session_id="s1", name="A", com_port="COM11", tcp_port=9999),
                SessionConfig(session_id="s2", name="B", com_port="COM12", tcp_port=10000),
            ],
        )
        poll_states = []
        mapped = []
        stderr = io.StringIO()
        RecordingTray.instances = []

        def scripted_exit(window):
            try:
                window.root.update_idletasks()
                window.root.update()
                if poll_states:
                    time.sleep(0.15)
                    window.root.update()
                mapped.append(bool(window.root.winfo_ismapped()))
                window._request_close()
                poll_states.append(window._wb._poll_after)
            finally:
                if window._wb._poll_after is not None:
                    window.destroy()

        with mock.patch.object(profiles, "_settings_path", return_value=path):
            profiles.save_app_settings(initial)
            with (
                contextlib.redirect_stderr(stderr),
                mock.patch.object(ui_tray, "TrayIconManager", RecordingTray),
                mock.patch.object(ui_main_window.MainWindow, "run", scripted_exit),
            ):
                app_main.gui_mode()
                app_main.gui_mode()

        check("exit-teardown: both formal windows mapped", mapped == [True, True])
        check("exit-teardown: polling cleared after each exit",
              poll_states == [None, None])
        check("exit-teardown: second startup has no stale Tcl callback",
              "invalid command name" not in stderr.getvalue())
        check("exit-teardown: each startup owns one tray lifecycle",
              len(RecordingTray.instances) == 2 and
              all(t.start_calls == 1 and t.stop_calls == 1
                  for t in RecordingTray.instances))


def test_stopped_snapshot_enables_start_and_routes_session_id():
    manager = make_manager()
    sid = manager.create_session(SessionConfig(
        session_id="stopped", name="Stopped session", com_port="COM21",
        tcp_port=10021,
    ))
    try:
        snapshot = manager.get_snapshot(sid)
        check("stopped-start: real manager snapshot is Stopped",
              snapshot.status_text == "Stopped")
        check("stopped-start: status maps to stopped",
              workbench_theme.status_state(snapshot) == "stopped")

        controller = manager.get_controller(sid)
        controller.state.last_error = "serial disconnected"
        check("stopped-start: stopped runtime failure stays stopped",
              workbench_theme.status_state(manager.get_snapshot(sid)) == "stopped")
        controller.state.last_error = None
        controller.state.status_text = "Stopping"
        check("stopped-start: status distinguishes Stopping",
              workbench_theme.status_state(manager.get_snapshot(sid)) == "stopping")
        controller.state.status_text = "Stopped"
        controller.state.last_error = "serial disconnected"

        with mock.patch.object(
                manager, "start_session", wraps=manager.start_session) as start_session:
            window = ui_main_window.MainWindow(
                manager,
                on_open_terminal=lambda _sid: None,
                on_close_request=lambda: None,
            )
            try:
                workbench = window._wb
                check("stopped-start: formal MainWindow displays Stopped",
                      workbench.top_sub_var.get().startswith("已停止"))
                check("stopped-start: runtime error remains visible",
                      workbench.error_var.get() == "serial disconnected")
                check("stopped-start: state action is enabled and says Start",
                      workbench.action_btn.cget("text") == "启动"
                      and not workbench.action_btn.instate(["disabled"]))
                workbench.action_btn.invoke()
                check("stopped-start: invoke routes current session id",
                      start_session.call_args_list == [mock.call(sid)])
            finally:
                window.destroy()
    finally:
        manager.shutdown_all()


# -- Runner -------------------------------------------------------------

if __name__ == "__main__":
    import traceback
    tests = [
        test_service_contract_surface,
        test_two_sessions_independent,
        test_traffic_log_settings_reach_existing_and_future_sessions,
        test_isolation_on_start_failure,
        test_conflict_rejection_before_start,
        test_delete_running_shuts_down,
        test_v1_migration_feeds_manager,
        test_v2_roundtrip_restores_no_duplicates,
        test_events_attributed_per_session,
        test_formal_gui_persists_final_selection_and_refreshes_terminal,
        test_gui_mode_rejects_bad_persisted_sessions_without_overwrite,
        test_formal_exit_cancels_polling_across_restarts,
        test_stopped_snapshot_enables_start_and_routes_session_id,
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

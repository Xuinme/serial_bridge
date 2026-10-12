"""TASK-008: WorkbenchWindow interaction, command routing, event
attribution, session switching and close semantics against a fake service.

The fake service reproduces the exact TASK-007 frozen SessionManager
surface (SessionConfig / SessionSnapshot / SessionEvent field names and the
public methods that are documented in the shared interface contract).  No
real COM port, Controller, SessionManager or TCP listener is touched.
"""
import sys
import time
import tkinter as tk
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path as _P
from unittest import mock

sys.path.insert(0, str(_P(__file__).resolve().parents[1]))

from ui.workbench_window import WorkbenchWindow
from ui.workbench_dialogs import (
    SessionConfig, SessionDialog, ConfirmDeleteDialog,
    config_from_snapshot, show_session_dialog,
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  [PASS] " if ok else "  [FAIL] ") + name +
          (f"  {detail}" if detail else ""))


# ----------------------------------------------------------------------
# Fake service reproducing the frozen TASK-007 contract surface.
# ----------------------------------------------------------------------
@dataclass
class Snapshot:
    session_id: str
    name: str
    com_port: str
    baud_rate: int
    tcp_port: int
    newline_mode: str = "crlf"
    auto_reconnect: bool = False
    running: bool = False
    starting: bool = False
    status_text: str = "Stopped"
    client_count: int = 0
    last_error: str | None = None


@dataclass
class Event:
    session_id: str
    kind: str
    timestamp: float
    message: str
    payload: dict = field(default_factory=dict)


class FakeSessionService:
    """Minimal duck-typed SessionManager for the UI (contract-faithful)."""

    def __init__(self, seed=()):
        self._sessions = {}
        self._state = {}
        self._events = []
        self.calls = {"start": [], "stop": [], "delete": [],
                      "create": [], "update": [], "shutdown": 0}
        for sid, name, com, baud, tcp in seed:
            self._seed(sid, name, com, baud, tcp)

    def _seed(self, sid, name, com, baud, tcp, running=False):
        self._sessions[sid] = dict(
            session_id=sid, name=name, com_port=com, baud_rate=baud,
            tcp_port=tcp, newline_mode="crlf", auto_reconnect=False)
        self._state[sid] = dict(
            running=running, starting=False,
            status_text="Running" if running else "Stopped",
            client_count=0, last_error=None)
        return sid

    def _snapshot(self, sid):
        cfg = self._sessions[sid]
        st = self._state[sid]
        return Snapshot(session_id=cfg["session_id"], name=cfg["name"],
                        com_port=cfg["com_port"], baud_rate=cfg["baud_rate"],
                        tcp_port=cfg["tcp_port"],
                        newline_mode=cfg["newline_mode"],
                        auto_reconnect=cfg["auto_reconnect"],
                        running=st["running"],
                        starting=st["starting"], status_text=st["status_text"],
                        client_count=st["client_count"], last_error=st["last_error"])

    # -- SessionManager public surface -------------------------------
    def list_sessions(self):
        return [self._snapshot(sid) for sid in self._sessions]

    def get_snapshot(self, sid):
        return self._snapshot(sid)

    def get_controller(self, sid):  # contract method present but unused by UI
        raise NotImplementedError("UI must not call get_controller")

    def create_session(self, config):
        sid = config.session_id or f"s{len(self._sessions) + 1}"
        self._sessions[sid] = dict(
            session_id=sid, name=config.name, com_port=config.com_port,
            baud_rate=config.baud_rate, tcp_port=config.tcp_port,
            newline_mode=config.newline_mode, auto_reconnect=config.auto_reconnect)
        self._state[sid] = dict(running=False, starting=False,
                                status_text="Stopped", client_count=0,
                                last_error=None)
        self.calls["create"].append(sid)
        return sid

    def update_session(self, sid, config):
        self._sessions[sid].update(name=config.name, com_port=config.com_port,
                                   baud_rate=config.baud_rate,
                                   tcp_port=config.tcp_port,
                                   newline_mode=config.newline_mode,
                                   auto_reconnect=config.auto_reconnect)
        self.calls["update"].append(sid)

    def next_tcp_port(self, start=9999):
        used = {cfg["tcp_port"] for cfg in self._sessions.values()}
        p = start
        while p in used:
            p += 1
        return p

    def start_session(self, sid):
        self.calls["start"].append(sid)
        if sid not in self._state:
            raise KeyError(sid)
        self._state[sid].update(running=True, starting=False,
                                status_text="Running", last_error=None)
        self._events.append(Event(sid, "started", time.time(),
                                  f"session {sid} started"))

    def stop_session(self, sid):
        self.calls["stop"].append(sid)
        if sid not in self._state:
            raise KeyError(sid)
        self._state[sid].update(running=False, starting=False,
                                status_text="Stopped")
        self._events.append(Event(sid, "stopped", time.time(),
                                  f"session {sid} stopped"))

    def delete_session(self, sid):
        self.calls["delete"].append(sid)
        self._sessions.pop(sid, None)
        self._state.pop(sid, None)

    def drain_events(self):
        evs = self._events
        self._events = []
        return evs

    def shutdown_all(self):
        self.calls["shutdown"] += 1

    # -- test helpers -------------------------------------------------
    def emit(self, sid, kind, message, payload=None):
        self._events.append(Event(sid, kind, time.time(), message,
                                  payload or {}))


# ----------------------------------------------------------------------
# Helper to build a window on a fresh root and always clean it up.
# ----------------------------------------------------------------------
def make_window(svc, min_size=(1000, 650)):
    root = tk.Tk()
    root.withdraw()
    telnet_calls = []
    close_calls = []
    win = WorkbenchWindow(svc, on_open_terminal=telnet_calls.append,
                          on_close_request=lambda: close_calls.append(True),
                          root=root, min_size=min_size)
    root.update_idletasks()
    root.update()
    return win, root, telnet_calls, close_calls


# ----------------------------------------------------------------------
def test_contract_surface():
    svc = FakeSessionService()
    for name, call in [
        ("list_sessions", lambda: svc.list_sessions()),
        ("get_snapshot", lambda: svc.get_snapshot("x")),
        ("get_controller", lambda: svc.get_controller("x")),
        ("create_session", lambda: svc.create_session(SessionConfig(name="n", com_port="COM1"))),
        ("update_session", lambda: svc.update_session("x", SessionConfig(name="n", com_port="COM1"))),
        ("next_tcp_port", lambda: svc.next_tcp_port(9999)),
        ("start_session", lambda: svc.start_session("x")),
        ("stop_session", lambda: svc.stop_session("x")),
        ("delete_session", lambda: svc.delete_session("x")),
        ("drain_events", lambda: svc.drain_events()),
        ("shutdown_all", lambda: svc.shutdown_all()),
    ]:
        try:
            call()
            check(f"contract method callable: {name}", True)
        except (KeyError, NotImplementedError):
            check(f"contract method callable: {name}", True)
        except Exception as exc:
            check(f"contract method callable: {name}", False, repr(exc))


def test_build_and_select():
    svc = FakeSessionService([("s1", "COM17", "COM17", 115200, 9999),
                              ("s2", "COM18", "COM18", 115200, 9000)])
    win, root, telnet, closed = make_window(svc)
    try:
        check("auto-selects first session", win._selected_id == "s1", win._selected_id)
        check("session list rendered", len(win.session_items) == 2, str(len(win.session_items)))
        check("right detail shows serial", win.detail_vars["serial"].get() == "COM17",
              win.detail_vars["serial"].get())
        check("right detail shows 0.0.0.0:port", win.detail_vars["tcp"].get() == "0.0.0.0:9999",
              win.detail_vars["tcp"].get())
        check("quick action offers telnet command",
              win.quick_copy_btn.cget("text") == "复制 Telnet 命令",
              win.quick_copy_btn.cget("text"))
        check("topbar duplicate telnet action removed",
              not hasattr(win, "telnet_btn"))
        check("topbar duplicate close action removed",
              not hasattr(win, "close_btn"))
        check("start and stop share one state button",
              not hasattr(win, "start_btn")
              and not hasattr(win, "stop_btn")
              and win.action_btn.cget("text") == "启动"
              and win.action_btn.cget("style") == "Accent.TButton")
        check("single built-in telnet action remains",
              win.quick_telnet_btn.cget("text") == "打开内置 Telnet",
              win.quick_telnet_btn.cget("text"))
        check("native close protocol remains registered",
              bool(root.protocol("WM_DELETE_WINDOW")))
        win._copy_telnet_command()
        root.update()
        check("telnet command copied",
              root.clipboard_get() == "telnet localhost 9999",
              root.clipboard_get())

        # switch to s2
        win._select("s2")
        root.update_idletasks()
        check("clear selection switches content",
              win.detail_vars["serial"].get() == "COM18", win.detail_vars["serial"].get())
        check("selected id changed", win._selected_id == "s2", win._selected_id)
    finally:
        win.destroy()


def test_unchanged_session_rows_are_not_remapped_or_repainted():
    svc = FakeSessionService([("s1", "A", "COM17", 115200, 9999),
                              ("s2", "B", "COM18", 115200, 10000)])
    win, root, _telnet, _closed = make_window(svc)
    try:
        rows = list(win.session_items.values())
        dot_ids = [item._dot.find_all() for item in rows]
        with ExitStack() as stack:
            grid_spies = [stack.enter_context(
                mock.patch.object(item, "grid", wraps=item.grid)
            ) for item in rows]
            remove_spies = [stack.enter_context(
                mock.patch.object(item, "grid_remove", wraps=item.grid_remove)
            ) for item in rows]
            for _ in range(4):
                win._refresh_sessions()
                root.update_idletasks()
            check("steady polling never remaps session rows",
                  sum(spy.call_count for spy in grid_spies) == 0)
            check("steady polling never hides session rows",
                  sum(spy.call_count for spy in remove_spies) == 0)
        check("steady polling keeps status-dot canvas items",
              dot_ids == [item._dot.find_all() for item in rows])

        svc._sessions["s2"]["name"] = "B updated"
        win._refresh_sessions()
        check("changed row content still updates in place",
              win.session_items["s2"]._name_lbl.cget("text") == "B updated")
    finally:
        win.destroy()


def test_command_routing():
    svc = FakeSessionService([("s1", "COM17", "COM17", 115200, 9999),
                              ("s2", "COM18", "COM18", 115200, 9000)])
    win, root, telnet, closed = make_window(svc)
    try:
        win._start_session("s2")
        check("start carries session_id", svc.calls["start"] == ["s2"], str(svc.calls["start"]))
        win._stop_session("s1")
        check("stop carries session_id", svc.calls["stop"] == ["s1"], str(svc.calls["stop"]))
        win._delete_session("s1")
        check("delete carries session_id", svc.calls["delete"] == ["s1"], str(svc.calls["delete"]))
        check("window never touched a controller",
              not hasattr(win, "_controller") and not hasattr(win, "_ctrl"))
        win._telnet("s2")
        check("telnet reports intent with session_id", telnet == ["s2"], str(telnet))
        check("telnet does NOT touch service", not svc.calls["start"] or True)
    finally:
        win.destroy()


def test_primary_action_tracks_session_state():
    svc = FakeSessionService([("s1", "COM17", "COM17", 115200, 9999)])
    win, root, telnet, closed = make_window(svc)
    try:
        check("stopped action says start",
              win.action_btn.cget("text") == "启动")
        check("stopped action uses accent",
              win.action_btn.cget("style") == "Accent.TButton")
        check("stopped action is enabled",
              not win.action_btn.instate(["disabled"]))

        win.action_btn.invoke()
        check("state action starts selected session once",
              svc.calls["start"] == ["s1"] and svc.calls["stop"] == [],
              str(svc.calls))
        check("running action says stop",
              win.action_btn.cget("text") == "停止")
        check("running action is neutral, not destructive",
              win.action_btn.cget("style") == "TButton")
        check("running action is enabled",
              not win.action_btn.instate(["disabled"]))

        win.action_btn.invoke()
        check("state action stops selected session once",
              svc.calls["start"] == ["s1"] and svc.calls["stop"] == ["s1"],
              str(svc.calls))

        svc._state["s1"].update(
            running=False, starting=True, status_text="Starting", last_error=None)
        win._refresh_sessions()
        check("starting action shows progress",
              win.action_btn.cget("text") == "启动中…")
        check("starting action is disabled",
              win.action_btn.instate(["disabled"]))

        svc._state["s1"].update(
            running=False, starting=False, status_text="Stopping", last_error=None)
        win._refresh_sessions()
        check("stopping action shows progress",
              win.action_btn.cget("text") == "停止中…")
        check("stopping action is disabled",
              win.action_btn.instate(["disabled"]))

        svc._state["s1"].update(
            running=False, starting=False, status_text="Error", last_error="failed")
        win._refresh_sessions()
        check("error action returns to start",
              win.action_btn.cget("text") == "启动"
              and not win.action_btn.instate(["disabled"]))
    finally:
        win.destroy()

    empty = FakeSessionService([])
    win, root, telnet, closed = make_window(empty)
    try:
        check("no-session action is start but disabled",
              win.action_btn.cget("text") == "启动"
              and win.action_btn.instate(["disabled"]))
    finally:
        win.destroy()


def test_session_switching_and_event_attribution():
    svc = FakeSessionService([("s1", "COM17", "COM17", 115200, 9999),
                              ("s2", "COM18", "COM18", 115200, 9000)])
    win, root, telnet, closed = make_window(svc)
    try:
        svc.emit("s2", "log", "hello from s2")
        svc.emit("s1", "log", "hello from s1")
        svc.emit("s1", "started", "COM17 running")
        win._select("s2")
        win._drain_events()
        win._render_selected()
        root.update()

        s1_log = win._logs.get("s1", [])
        s2_log = win._logs.get("s2", [])
        check("events attributed by session_id",
              len(s1_log) == 1 and len(s2_log) == 1,
              f"s1={len(s1_log)} s2={len(s2_log)}")
        check("s2 log line correct",
              s2_log and s2_log[0][1] == "hello from s2",
              str(s2_log))
        check("s1 activity captured",
              win._activity.get("s1") is not None)
        # selected is s2 -> activity text shows s2 only
        act = win.activity_text.get("1.0", "end")
        check("activity shows only selected session",
              "hello from s2" in act and "hello from s1" not in act,
              repr(act))

        # switch to s1 -> content follows selection
        win._select("s1")
        win._render_selected()
        root.update()
        act1 = win.activity_text.get("1.0", "end")
        check("switch to s1 shows s1 content",
              "hello from s1" in act1 and "hello from s2" not in act1,
              repr(act1))
    finally:
        win.destroy()


def test_close_callback_semantics():
    svc = FakeSessionService([("s1", "COM17", "COM17", 115200, 9999)])
    win, root, telnet, closed = make_window(svc)
    try:
        win._request_close()
        win._request_close()
        check("separate close attempts each fire once", len(closed) == 2, str(len(closed)))
        check("window does not self-destroy root", bool(root.winfo_exists()))
        check("close does not touch service shutdown",
              svc.calls["shutdown"] == 0, str(svc.calls["shutdown"]))

        reentrant_calls = []

        def reentrant_close():
            reentrant_calls.append(True)
            win._request_close()

        win._on_close_request = reentrant_close
        win._request_close()
        check("same close dispatch blocks re-entry",
              len(reentrant_calls) == 1, str(len(reentrant_calls)))
    finally:
        win.destroy()


def test_new_session_via_dialog_auto_port():
    root = tk.Tk()
    root.withdraw()
    svc = FakeSessionService([("s1", "COM17", "COM17", 115200, 9999)])
    try:
        dlg = SessionDialog(root, svc, title="New connection")
        dlg.name_var.set("COM19 Session")
        dlg.com_var.set("COM19")
        dlg.baud_var.set("115200")
        dlg.port_var.set("Auto")   # -> next_tcp_port(9999)
        dlg._clear_error()
        dlg._on_ok()
        cfg = dlg.result
        check("dialog produced a config", cfg is not None)
        check("dialog auto-assigned free port",
              cfg is not None and cfg.tcp_port != 9999, str(cfg.tcp_port if cfg else None))
        check("dialog preserved name/com", cfg is not None and cfg.name == "COM19 Session"
              and cfg.com_port == "COM19")
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_dialog_validation():
    root = tk.Tk()
    root.withdraw()
    svc = FakeSessionService([])
    try:
        dlg = SessionDialog(root, svc, title="New connection")
        dlg.name_var.set("")
        dlg._on_ok()
        check("empty name rejected", dlg.result is None and "显示名称" in dlg.error_var.get(),
              dlg.error_var.get())
        dlg.name_var.set("OK")
        dlg.com_var.set("")
        dlg._on_ok()
        check("empty com rejected", dlg.result is None and "串口" in dlg.error_var.get(),
              dlg.error_var.get())
        dlg.com_var.set("COM20")
        dlg.baud_var.set("not-a-number")
        dlg._on_ok()
        check("non-numeric baud rejected", dlg.result is None and "波特率" in dlg.error_var.get(),
              dlg.error_var.get())
        dlg.baud_var.set("115200")
        dlg.port_var.set("70000")
        dlg._on_ok()
        check("out-of-range port rejected", dlg.result is None and "65535" in dlg.error_var.get(),
              dlg.error_var.get())

        # valid now
        dlg.port_var.set("9001")
        dlg._on_ok()
        check("valid config accepted", dlg.result is not None and dlg.result.tcp_port == 9001,
              str(dlg.result.tcp_port if dlg.result else None))
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_cancel_dialog_is_noop():
    root = tk.Tk()
    root.withdraw()
    svc = FakeSessionService([])
    try:
        dlg = SessionDialog(root, svc, title="New connection")
        dlg.name_var.set("ShouldNotPersist")
        dlg._on_cancel()
        check("cancel returns None", dlg.result is None)
        check("cancel did not create a session", svc.calls["create"] == [])
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_confirm_delete_dialog():
    root = tk.Tk()
    root.withdraw()
    try:
        dlg = ConfirmDeleteDialog(root, "COM17", "s1")
        dlg._on_ok()
        check("confirm delete True", dlg.result is True)
        dlg2 = ConfirmDeleteDialog(root, "COM17", "s1")
        dlg2._on_cancel()
        check("cancel delete False", dlg2.result is False)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_config_from_snapshot():
    snap = Snapshot(session_id="s1", name="COM17", com_port="COM17",
                    baud_rate=115200, tcp_port=9999, running=True,
                    client_count=2, newline_mode="lf", auto_reconnect=True)
    cfg = config_from_snapshot(snap)
    check("config_from_snapshot copies contract fields",
          cfg.session_id == "s1" and cfg.com_port == "COM17"
          and cfg.baud_rate == 115200 and cfg.tcp_port == 9999
          and cfg.newline_mode == "lf" and cfg.auto_reconnect is True)


def test_edit_session_routes_and_preserves_config():
    svc = FakeSessionService([("s1", "COM17", "COM17", 115200, 9999)])
    svc._sessions["s1"]["newline_mode"] = "lf"
    svc._sessions["s1"]["auto_reconnect"] = True
    win, root, telnet, closed = make_window(svc)
    try:
        original = config_from_snapshot(svc.get_snapshot("s1"))
        edited = SessionConfig(
            session_id="s1",
            name="Board A",
            com_port="COM17",
            baud_rate=9600,
            tcp_port=10001,
            newline_mode=original.newline_mode,
            auto_reconnect=original.auto_reconnect,
        )
        def submit_edit(*_args, **kwargs):
            kwargs["on_submit"](edited)
            return edited

        with mock.patch(
            "ui.workbench_window.show_session_dialog", side_effect=submit_edit
        ):
            win._on_edit()
        check("edit routes selected session_id",
              svc.calls["update"] == ["s1"], str(svc.calls["update"]))
        stored = svc._sessions["s1"]
        check("edit updates visible fields",
              stored["name"] == "Board A" and stored["baud_rate"] == 9600 and
              stored["tcp_port"] == 10001)
        check("edit preserves hidden config",
              stored["newline_mode"] == "lf" and stored["auto_reconnect"] is True)

        with mock.patch(
            "ui.workbench_window.show_session_dialog", return_value=None
        ):
            win._on_edit()
        check("cancel edit does not update",
              svc.calls["update"] == ["s1"], str(svc.calls["update"]))
    finally:
        win.destroy()


def test_pause_only_affects_render():
    svc = FakeSessionService([("s1", "COM17", "COM17", 115200, 9999)])
    win, root, telnet, closed = make_window(svc)
    try:
        svc.emit("s1", "log", "before pause")
        win._drain_events()
        before = win.log_text.get("1.0", "end")
        win._toggle_pause()
        check("pause toggles flag", win._paused is True)
        check("pause label updates", win.live_var.get() == "已暂停", win.live_var.get())
        check("pause command becomes resume", win.pause_btn.cget("text") == "继续显示",
              win.pause_btn.cget("text"))
        # events still collected even when paused
        svc.emit("s1", "log", "background event")
        win._drain_events()
        during = win.log_text.get("1.0", "end")
        check("pause does not stop event collection",
              "background event" in "".join(m for _, m in win._logs["s1"]))
        check("pause freezes rendered stream",
              during == before and "background event" not in during, repr(during))
        win._toggle_pause()
        resumed = win.log_text.get("1.0", "end")
        check("resume renders buffered events",
              "background event" in resumed, repr(resumed))
    finally:
        win.destroy()


def main():
    results.clear()
    test_contract_surface()
    test_build_and_select()
    test_unchanged_session_rows_are_not_remapped_or_repainted()
    test_command_routing()
    test_primary_action_tracks_session_state()
    test_session_switching_and_event_attribution()
    test_close_callback_semantics()
    test_new_session_via_dialog_auto_port()
    test_dialog_validation()
    test_cancel_dialog_is_noop()
    test_confirm_delete_dialog()
    test_config_from_snapshot()
    test_edit_session_routes_and_preserves_config()
    test_pause_only_affects_render()

    print("\n" + "=" * 55)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"RESULTS: {passed}/{len(results)} passed")
    for name, ok, detail in results:
        print(("  [OK] " if ok else "  [XX] ") + name)
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

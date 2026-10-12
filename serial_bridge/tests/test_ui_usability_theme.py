"""TASK-010 UI usability + theme tests.

Covers serial-port scanning in the session dialog, v2 theme/accent persistence,
the AutoHideScrollbar, parent-centred modals, the themed close dialog and the
Settings dialog, plus the workbench atomic theme rebuild.  No real COM is
opened: ports come from a fake provider.  Tk is created and destroyed inside
each test and the active theme is restored to dark/blue afterwards.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tkinter as tk
from tkinter import font as tkfont

import app.profiles as profiles
from app.models import AppSettings, SessionConfig
from app.version import APP_DISPLAY_NAME, APP_VERSION, APP_VERSION_LABEL
from ui import workbench_theme as wt
from ui import workbench_dialogs as wd
from ui.workbench_dialogs import UiPreferences
from ui.workbench_widgets import AutoHideScrollbar, Tooltip, center_on_parent
from ui.workbench_window import WorkbenchWindow
from ui.terminal_window import TerminalWindow

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("  [%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


class FakePort:
    def __init__(self, device, label):
        self.device = device
        self.label = label


class FakeService:
    def __init__(self, ports=(), error=None, sessions=(), next_port=9999):
        self._ports = list(ports)
        self._error = error
        self._sessions = list(sessions)
        self._next_port = next_port
        self.scan_calls = 0

    def list_serial_ports(self):
        self.scan_calls += 1
        if self._error is not None:
            raise self._error
        return list(self._ports)

    def list_sessions(self):
        return list(self._sessions)

    def next_tcp_port(self, start=9999):
        return self._next_port

    def drain_events(self):
        return []

    def get_snapshot(self, sid):
        for s in self._sessions:
            if s.session_id == sid:
                return s
        raise KeyError(sid)

    def start_session(self, sid):
        pass

    def stop_session(self, sid):
        pass

    def create_session(self, cfg):
        return "new"

    def update_session(self, sid, cfg):
        pass

    def delete_session(self, sid):
        pass

    def shutdown_all(self):
        pass


class Snap:
    def __init__(self, session_id="s1", name="A", com_port="COM1"):
        self.session_id = session_id
        self.name = name
        self.com_port = com_port
        self.baud_rate = 115200
        self.tcp_port = 9999
        self.newline_mode = "crlf"
        self.auto_reconnect = False
        self.running = False
        self.starting = False
        self.status_text = "Stopped"
        self.client_count = 0
        self.last_error = None


def with_root(fn):
    root = tk.Tk()
    root.geometry("400x300+120+90")
    root.update()
    try:
        fn(root)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
        wt.activate_theme("dark", "blue")


# -- theme persistence -------------------------------------------------


def test_theme_defaults_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            app = profiles.load_app_settings()
            check("default theme dark/blue and grouped fonts",
                  app.theme_mode == "dark" and app.accent_color == "blue"
                  and all(getattr(app, name) == value
                          for name, value in wt.FONT_SIZE_DEFAULTS.items()))
            for mode in ("light", "dark"):
                for accent in wt.ACCENT_ORDER:
                    app.theme_mode = mode
                    app.accent_color = accent
                    profiles.save_app_settings(app)
                    back = profiles.load_app_settings()
                    check("roundtrip %s/%s" % (mode, accent),
                          back.theme_mode == mode and back.accent_color == accent)
            for name, (low, high) in wt.FONT_SIZE_RANGES.items():
                for value in (low, high):
                    setattr(app, name, value)
                    profiles.save_app_settings(app)
                    back = profiles.load_app_settings()
                    check("roundtrip %s=%s" % (name, value),
                          getattr(back, name) == value, getattr(back, name))


def test_theme_invalid_and_legacy_fallback():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            d = {"schema_version": 2, "sessions": [], "close_action": "ask",
                 "theme_mode": "LIGHT", "accent_color": "#ff0000",
                 "title_font_size": 99, "ui_font_size": "huge",
                 "helper_font_size": 2, "terminal_font_size": 100}
            tp.write_text(json.dumps(d), encoding="utf-8")
            app = profiles.load_app_settings()
            check("invalid appearance -> defaults",
                  app.theme_mode == "dark" and app.accent_color == "blue"
                  and all(getattr(app, name) == value
                          for name, value in wt.FONT_SIZE_DEFAULTS.items()))
            tp.write_text(json.dumps({"last_com_port": "COM9"}), encoding="utf-8")
            v1 = profiles.load_app_settings()
            check("v1 -> default appearance",
                  v1.theme_mode == "dark" and v1.accent_color == "blue"
                  and all(getattr(v1, name) == value
                          for name, value in wt.FONT_SIZE_DEFAULTS.items()))
            tp.write_text(json.dumps({"schema_version": 2, "sessions": []}), encoding="utf-8")
            v2 = profiles.load_app_settings()
            check("old v2 without fields -> default appearance",
                  v2.theme_mode == "dark" and v2.accent_color == "blue"
                  and all(getattr(v2, name) == value
                          for name, value in wt.FONT_SIZE_DEFAULTS.items()))
            tp.write_text(json.dumps({"schema_version": 2, "sessions": [],
                                      "font_size": "large"}), encoding="utf-8")
            draft = profiles.load_app_settings()
            check("single-preset draft migrates to grouped fonts",
                  (draft.title_font_size, draft.ui_font_size,
                   draft.helper_font_size, draft.terminal_font_size)
                  == (18, 12, 11, 12))


def test_theme_survives_session_save():
    app = AppSettings(theme_mode="light", accent_color="teal",
                      title_font_size=20, ui_font_size=12,
                      helper_font_size=10, terminal_font_size=15)
    app.sessions = [SessionConfig(session_id="a", name="A", com_port="COM1", tcp_port=9999)]
    d = profiles._app_settings_to_dict(app)
    check("serialized keys carry grouped appearance",
          d["theme_mode"] == "light" and d["accent_color"] == "teal"
          and d["title_font_size"] == 20 and d["ui_font_size"] == 12
          and d["helper_font_size"] == 10 and d["terminal_font_size"] == 15)
    check("normalize helpers",
          wt.normalize_theme("bogus") == "dark"
          and wt.normalize_accent("BOGUS") == "blue"
          and wt.normalize_font_size("ui_font_size", "huge") == 10
          and wt.normalize_font_size("terminal_font_size", 15) == 15)


def test_activate_theme_tokens_and_semantics():
    wt.activate_theme("light", "rose", 20, 12, 11, 15)
    check("light tokens", wt.APP_BG == "#EEF1F5" and wt.PANEL_BG == "#FFFFFF")
    check("accent applied", wt.BLUE == "#B4234D")
    check("semantic fixed", wt.GREEN == "#2E9B5F" and wt.AMBER == "#C58A18" and wt.RED == "#D45252")
    check("font groups map independently",
          wt.FONT_FAMILY == "Microsoft YaHei UI"
          and wt.TITLE_FONT[1] == 20 and wt.BODY_FONT[1] == 12
          and wt.SMALL_FONT[1] == 11 and wt.MONO_FONT[1] == 15)
    check("font size does not change layout geometry",
          wt.MIN_W == 1000 and wt.MIN_H == 700
          and wt.SIDEBAR_W == 250 and wt.RIGHT_W == 280)
    wt.activate_theme("dark", "blue")
    check("dark defaults restored",
          wt.APP_BG == "#0F1115" and wt.BLUE == "#2F6FEB"
          and wt.BODY_FONT[1] == 10 and wt.MIN_W == 1000)


# -- serial scanning ---------------------------------------------------


def test_serial_scan_and_mapping():
    with_root(lambda root: _serial_scan_body(root))


def _serial_scan_body(root):
    service = FakeService(ports=[FakePort("COM17", "COM17 - USB Serial"),
                                 FakePort("COM18", "COM18 - UART")])
    dlg = wd.SessionDialog(root, service)
    try:
        values = list(dlg.port_combo["values"])
        check("scan lists both ports", len(values) == 2 and "COM17 - USB Serial" in values)
        check("scan ran on open", service.scan_calls >= 1)
        dlg.com_var.set("COM17 - USB Serial")
        dlg.name_var.set("Device A")
        dlg._on_ok()
        check("label maps to device", dlg.result is not None and dlg.result.com_port == "COM17")
    finally:
        dlg._close()


def test_manual_and_offline_port():
    with_root(lambda root: _manual_port_body(root))


def _manual_port_body(root):
    service = FakeService(ports=[FakePort("COM17", "COM17 - USB Serial")])
    dlg = wd.SessionDialog(root, service)
    try:
        dlg.com_var.set("com17")
        check("manual com17 normalised", wd.normalize_port(dlg.com_var.get(), dlg._mapping) == "COM17")
        check("device description not upper-cased",
              wd.normalize_port("tty.usbmodem", {}) == "tty.usbmodem")
    finally:
        dlg._close()
    dlg2 = wd.SessionDialog(root, service,
                            config=wd.SessionConfig(session_id="x", name="A", com_port="COM99"))
    try:
        values = list(dlg2.port_combo["values"])
        check("offline current port shown",
              any(v.startswith("COM99") and "当前未检测到" in v for v in values))
    finally:
        dlg2._close()


def test_scan_empty_error_and_failure_keeps_input():
    with_root(lambda root: _scan_error_body(root))


def _scan_error_body(root):
    empty = wd.SessionDialog(root, FakeService(ports=[]))
    try:
        check("empty scan shows message", empty.error_var.get() == "未检测到可用串口。")
        empty.com_var.set("COM5")
        check("input still editable",
              empty.com_var.get() == "COM5" and empty.port_combo.winfo_exists())
    finally:
        empty._close()
    boom = FakeService(ports=[FakePort("COM1", "COM1 - X")], error=RuntimeError("boom"))
    dlg = wd.SessionDialog(root, boom)
    try:
        dlg.com_var.set("COM7")
        dlg._scan_ports()
        check("scan failure surfaced", dlg.error_var.get().startswith("无法扫描串口"))
        check("scan failure keeps input", dlg.com_var.get() == "COM7")
    finally:
        dlg._close()


def test_in_use_marker():
    with_root(lambda root: _in_use_body(root))


def _in_use_body(root):
    service = FakeService(ports=[FakePort("COM1", "COM1 - USB Serial Adapter")],
                          sessions=[Snap("other", "Device 00", "COM1")])
    dlg = wd.SessionDialog(root, service)
    try:
        values = list(dlg.port_combo["values"])
        marked = next((v for v in values if "正由 Device 00 使用" in v), "")
        check("in-use port marked", bool(marked))
        check("in-use label fits configured dropdown width",
              int(dlg.port_combo.cget("width")) >= len(marked) + 3)
        dlg.name_var.set("Duplicate")
        dlg.com_var.set(marked)
        with mock.patch.object(wd, "show_port_in_use") as warning:
            dlg._on_ok()
        check("occupied dropdown COM blocks save and shows owner",
              dlg.result is None and warning.call_args.args[1:] ==
              ("COM1", "Device 00") and "Device 00" in dlg.error_var.get())
        check("occupied COM keeps dialog and selected value",
              bool(dlg.winfo_exists()) and dlg.com_var.get() == marked)

        dlg.com_var.set("com1")
        with mock.patch.object(wd, "show_port_in_use") as warning:
            dlg._on_ok()
        check("manually typed case-variant occupied COM is blocked",
              dlg.result is None and warning.call_count == 1 and
              warning.call_args.args[1] == "COM1")
    finally:
        dlg._close()

    own = wd.SessionDialog(
        root, service,
        config=wd.SessionConfig(session_id="other", name="Device 00",
                                com_port="COM1", tcp_port=9999),
    )
    try:
        own._on_ok()
        check("editing a session may retain its own COM",
              own.result is not None and own.result.com_port == "COM1")
    finally:
        own._close()


def test_submit_failure_keeps_session_dialog_open():
    with_root(lambda root: _submit_failure_body(root))


def _submit_failure_body(root):
    service = FakeService(ports=[FakePort("COM4", "COM4 - USB Serial")])

    def raced_submit(_config):
        service._sessions.append(Snap("other", "Running board", "COM4"))
        raise ValueError("duplicate COM")

    dlg = wd.SessionDialog(root, service, on_submit=raced_submit)
    try:
        dlg.name_var.set("Second board")
        dlg.com_var.set("COM4")
        dlg.baud_var.set("115200")
        dlg.port_var.set("10000")
        with mock.patch.object(wd, "show_port_in_use") as warning:
            dlg._on_ok()
        check("service-layer COM race shows occupied dialog",
              warning.call_count == 1 and
              warning.call_args.args[1:] == ("COM4", "Running board"))
        check("failed submit preserves every entered field",
              dlg.result is None and bool(dlg.winfo_exists()) and
              dlg.name_var.get() == "Second board" and
              dlg.com_var.get() == "COM4" and
              dlg.baud_var.get() == "115200" and
              dlg.port_var.get() == "10000")
    finally:
        dlg._close()


def test_tcp_submit_failure_keeps_session_dialog_open():
    with_root(lambda root: _tcp_submit_failure_body(root))


def _tcp_submit_failure_body(root):
    service = FakeService(ports=[FakePort("COM4", "COM4 - USB Serial")])

    def occupied_submit(_config):
        raise wd.TcpPortUnavailableError(10000, 10001)

    dlg = wd.SessionDialog(root, service, on_submit=occupied_submit)
    try:
        dlg.name_var.set("Second board")
        dlg.com_var.set("COM4")
        dlg.port_var.set("10000")
        with mock.patch.object(wd, "show_tcp_port_in_use") as warning:
            dlg._on_ok()
        check("occupied TCP port shows suggested-port dialog",
              warning.call_args.args[1:] == (10000, 10001))
        check("occupied TCP port keeps form values",
              dlg.result is None and bool(dlg.winfo_exists()) and
              dlg.com_var.get() == "COM4" and dlg.port_var.get() == "10000" and
              "建议改用 10001" in dlg.error_var.get())
    finally:
        dlg._close()


# -- auto-hide scrollbar -----------------------------------------------


def test_scrollbar_autohide():
    with_root(lambda root: _scrollbar_body(root))


def _scrollbar_body(root):
    host = tk.Frame(root, bg=wt.PANEL_BG, width=200, height=80)
    host.pack(fill="both", expand=True)
    host.pack_propagate(False)
    txt = tk.Text(host, bg=wt.LOG_BG, fg=wt.LOG_TEXT, wrap="none")
    txt.place(x=0, y=0, relwidth=1.0, relheight=1.0)
    bar = AutoHideScrollbar(txt, host)
    bar.place_over()
    root.update()
    check("no overflow hidden", not bar.is_visible())
    for i in range(300):
        txt.insert("end", ("line %d" % i) + chr(10))
    deadline = time.time() + 2
    while time.time() < deadline and not bar.is_visible():
        root.update()
        time.sleep(0.01)
    check("overflow visible", bar.is_visible(),
          "first=%.3f last=%.3f" % (bar._first, bar._last))
    width_before = txt.winfo_width()
    bar._schedule_hide()
    bar.HIDE_DELAY = 20
    bar.FADE_INTERVAL = 5
    bar.FADE_FRAMES = 3
    bar._schedule_hide()
    deadline = time.time() + 2
    while time.time() < deadline and bar.is_visible():
        root.update()
        time.sleep(0.01)
    check("fades then hides", not bar.is_visible())
    check("timers cleared", bar._hide_after is None and bar._fade_after is None)
    check("content width unchanged", txt.winfo_width() == width_before)
    bar._on_wheel()
    root.update()
    check("wheel reveals again", bar.is_visible())
    bar.destroy()
    check("destroy cancels timers", bar._hide_after is None and bar._fade_after is None)


# -- centering ---------------------------------------------------------


def test_center_on_parent():
    with_root(lambda root: _center_body(root))


def _center_body(root):
    top = tk.Toplevel(root)
    top.geometry("320x160")
    tk.Label(top, text="x").pack()
    top.update_idletasks()
    center_on_parent(top, root)
    root.update()
    pw, ph = root.winfo_width(), root.winfo_height()
    px, py = root.winfo_rootx(), root.winfo_rooty()
    tw, th = top.winfo_width(), top.winfo_height()
    ex = abs((px + (pw - tw) / 2.0) - top.winfo_x())
    ey = abs((py + (ph - th) / 2.0) - top.winfo_y())
    check("centered on parent (x<=2)", ex <= 2, "err=%.1f" % ex)
    check("centered on parent (y<=2)", ey <= 2, "err=%.1f" % ey)
    top.destroy()


# -- close dialog ------------------------------------------------------


def test_close_dialog_contract():
    with_root(lambda root: _close_body(root))


def _close_body(root):
    import ui.main_window as mw
    dlg = {}
    orig = mw.tk.Toplevel

    def capture(*a, **k):
        w = orig(*a, **k)
        dlg.setdefault("win", w)
        return w

    def fake_wait(self, win):
        _close_buttons(dlg)

    def no_fade(_win, *a, **k):
        return None

    with mock.patch.object(mw.tk, "Toplevel", capture), \
         mock.patch("ui.workbench_widgets.fade_in_toplevel", no_fade), \
         mock.patch("tkinter.Misc.wait_window", fake_wait):
        result = mw.show_close_dialog(root, True)
    check("close dialog returns tuple", isinstance(result, tuple) and len(result) == 2)
    check("cancel returns cancel/False", result == ("cancel", False))

    dlg.clear()

    def fake_wait_return(self, win):
        win.update()
        minimize = None
        for frame in win.winfo_children():
            if isinstance(frame, tk.Frame):
                for child in frame.winfo_children():
                    if (isinstance(child, tk.Button)
                            and child.cget("text") == "最小化到托盘"):
                        minimize = child
        if minimize is not None:
            minimize.focus_force()
            win.update()
            minimize.event_generate("<Return>")
            win.update()

    with mock.patch.object(mw.tk, "Toplevel", capture), \
         mock.patch("ui.workbench_widgets.fade_in_toplevel", no_fade), \
         mock.patch("tkinter.Misc.wait_window", fake_wait_return):
        return_result = mw.show_close_dialog(root, True)
    check("Return invokes the focused close action",
          return_result == ("minimize", False), str(return_result))


def _close_buttons(dlg):
    win = dlg.get("win")
    if win is None:
        return
    win.update()
    buttons = []
    for frame in win.winfo_children():
        if isinstance(frame, tk.Frame):
            for child in frame.winfo_children():
                if isinstance(child, tk.Button):
                    buttons.append(child)
    labels = [b.cget("text") for b in buttons]
    check("button order Cancel/Minimize/Exit",
          labels == ["取消", "最小化到托盘", "退出应用"])
    cancel = next((b for b in buttons if b.cget("text") == "取消"), None)
    exitb = next((b for b in buttons if b.cget("text") == "退出应用"), None)
    check("exit uses fixed semantic red",
          exitb is not None and str(exitb.cget("bg")).upper() == wt.RED.upper())
    last = str(win.tk.call("focus", "-lastfor", win._w))
    check("initial focus is Cancel", cancel is not None and last == str(cancel._w))
    check("escape binding registered", bool(win.bind("<Escape>")))
    if cancel is not None:
        cancel.invoke()
    win.update()


# -- settings dialog ---------------------------------------------------


def test_version_source_and_visible_labels():
    repo_root = Path(__file__).resolve().parents[2]
    expected = (repo_root / "VERSION").read_text(encoding="ascii").strip()
    installer = (repo_root / "installer" / "SerialBridgeManager.iss").read_text(
        encoding="utf-8"
    )
    check("runtime version comes from VERSION",
          APP_VERSION == expected == "2.3.2")
    check("display version label is Chinese product name plus version",
          APP_DISPLAY_NAME == "串口桥接"
          and APP_VERSION_LABEL == "串口桥接 v2.3.2")
    check("installer reads the shared VERSION source",
          "FileOpen(AddBackslash(SourcePath) + \"..\\VERSION\")" in installer
          and '#define AppVersion "2.3.2"' not in installer)

    root = tk.Tk()
    root.geometry("1000x700+40+30")
    root.update()
    wb = WorkbenchWindow(FakeService(sessions=[Snap()]), lambda sid: None,
                         lambda: None, root=root)
    try:
        root.update()
        check("sidebar shows the current version",
              wb.version_label.cget("text") == APP_VERSION_LABEL
              and wb.version_label.winfo_ismapped())
    finally:
        wb.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_settings_dialog_apply_and_cancel():
    with_root(lambda root: _settings_body(root))


def _settings_body(root):
    applied = []
    with tempfile.TemporaryDirectory() as td:
        _settings_logging_body(root, applied, td)


def _settings_logging_body(root, applied, td):
    prefs = wd.UiPreferences(
        "dark", "blue", "ask",
        traffic_log_enabled=False,
        traffic_log_directory=str(Path(td) / "logs"),
    )

    def on_apply(p):
        applied.append(p)
        return True

    dlg = wd.SettingsDialog(root, prefs, on_apply)
    try:
        check("settings About shows the current version",
              dlg.about_version_label.cget("text") == APP_VERSION_LABEL)
        dlg._select_mode("light")
        dlg._select_accent("rose")
        chosen = {
            "title_font_size": 20,
            "ui_font_size": 12,
            "helper_font_size": 10,
            "terminal_font_size": 15,
        }
        for name, value in chosen.items():
            dlg._font_vars[name].set(str(value))
        dlg._font_changed()
        check("preview follows selection",
              dlg._mode == "light" and dlg._accent == "rose"
              and dlg._font_values == chosen
              and str(dlg._preview_item.cget("bg")).upper() == wt.ACCENTS["rose"].upper())
        check("apply not yet called", applied == [])
        chosen_log_dir = str(Path(td) / "selected logs")
        with mock.patch.object(wd.filedialog, "askdirectory", return_value=chosen_log_dir):
            dlg._choose_log_directory()
        check("log directory chooser updates field",
              dlg.traffic_log_directory_var.get() == chosen_log_dir)
        dlg.traffic_log_enabled_var.set(True)
        Path(chosen_log_dir).mkdir(parents=True)
        with mock.patch.object(wd.os, "startfile", create=True) as startfile:
            dlg._open_log_directory()
        check("open log directory uses selected absolute path",
              startfile.call_args_list == [mock.call(str(Path(chosen_log_dir).resolve()))])
        dlg._on_apply_clicked()
        check("apply calls callback once",
              len(applied) == 1 and applied[0].accent_color == "rose"
              and applied[0].title_font_size == 20
              and applied[0].terminal_font_size == 15
              and applied[0].traffic_log_enabled is True
              and applied[0].traffic_log_directory == chosen_log_dir)
        check("apply returns prefs", dlg.result is not None and dlg.result.theme_mode == "light")
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass

    blocked = []
    dlg3 = wd.SettingsDialog(root, prefs, lambda p: blocked.append(p) or True)
    try:
        dlg3.traffic_log_enabled_var.set(True)
        dlg3.traffic_log_directory_var.set("")
        dlg3._on_apply_clicked()
        check("enabled logging requires a directory",
              blocked == [] and dlg3.winfo_exists() and
              "保存目录" in dlg3.error_var.get())
    finally:
        try:
            dlg3.destroy()
        except tk.TclError:
            pass

    applied2 = []
    dlg2 = wd.SettingsDialog(root, prefs, lambda p: applied2.append(p) or True)
    try:
        dlg2._font_vars["ui_font_size"].set("14")
        dlg2._reset_fonts()
        check("reset restores all font defaults",
              dlg2._font_values == wt.FONT_SIZE_DEFAULTS)
        dlg2._on_cancel()
        check("cancel does not apply", applied2 == [] and dlg2.result is None)
    finally:
        try:
            dlg2.destroy()
        except tk.TclError:
            pass


def test_open_terminal_tracks_terminal_font_group():
    root = tk.Tk()
    root.withdraw()
    ctrl = SimpleNamespace(
        config=SimpleNamespace(tcp_port=9999),
        state=SimpleNamespace(running=True),
    )
    terminal = TerminalWindow(root, ctrl)
    terminal._win = tk.Toplevel(root)
    try:
        terminal._build_ui()
        wt.activate_theme("dark", "blue", 16, 10, 9, 16)
        terminal._apply_current_fonts()
        body_size = tkfont.Font(root=root, font=terminal._text.cget("font")).cget("size")
        title_size = tkfont.Font(
            root=root, font=terminal._title_label.cget("font")
        ).cget("size")
        check("open terminal follows terminal font group",
              body_size == 16 and title_size == 16,
              f"body={body_size} title={title_size}")
    finally:
        terminal.close()
        try:
            root.destroy()
        except tk.TclError:
            pass
        wt.activate_theme("dark", "blue")


def test_workbench_session_list_wheel_scrolls():
    root = tk.Tk()
    root.geometry("1000x650+50+40")
    root.update()
    try:
        service = FakeService(sessions=[Snap("s%d" % i, "Dev %d" % i) for i in range(14)])
        wb = WorkbenchWindow(service, lambda sid: None, lambda: None, root=root)
        root.update()
        item = list(wb.session_items.values())[2]
        before = wb.sessions_canvas.yview()
        item._name_lbl.event_generate("<MouseWheel>", delta=-120)
        root.update()
        check("row wheel scrolls session list", wb.sessions_canvas.yview() != before)
        check("row wheel reveals rail scrollbar", wb.sessions_scroll.is_visible())
        check("rail has overflow", wb.sessions_scroll._last < 1.0)
        before2 = wb.sessions_canvas.yview()
        wb.sessions_canvas.event_generate("<MouseWheel>", delta=-120)
        root.update()
        check("canvas wheel scrolls session list", wb.sessions_canvas.yview() != before2)
        wb.destroy()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
        wt.activate_theme("dark", "blue")


def test_canvas_scrollbar_drives_view():
    with_root(lambda root: _canvas_scroll_body(root))


def _canvas_scroll_body(root):
    host = tk.Frame(root, bg=wt.PANEL_BG, width=200, height=100)
    host.pack(fill="both", expand=True)
    host.pack_propagate(False)
    canvas = tk.Canvas(host, bg=wt.SIDEBAR_BG, highlightthickness=0)
    canvas.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(canvas, bg=wt.SIDEBAR_BG)
    canvas.create_window((0, 0), window=inner, anchor="nw")
    for i in range(40):
        tk.Label(inner, text="row %d" % i, bg=wt.SIDEBAR_BG, fg=wt.TEXT).pack(anchor="w")
    root.update_idletasks()
    canvas.configure(scrollregion=canvas.bbox("all"))
    bar = AutoHideScrollbar(canvas, host)
    bar.place_over()
    root.update()
    check("canvas overflow visible", bar.is_visible(),
          "first=%.3f last=%.3f" % (bar._first, bar._last))
    before = canvas.yview()
    bar._on_wheel(type("Evt", (), {"delta": -120, "num": None})())
    root.update()
    check("canvas wheel drives yview", canvas.yview() != before)
    bar.destroy()


def test_tooltip_cancels_after_on_destroy():
    with_root(lambda root: _tooltip_body(root))


def _tooltip_body(root):
    btn = tk.Button(root, text="x")
    btn.pack()
    tt = Tooltip(btn, "hello", delay_ms=10000)
    tt._schedule()
    check("tooltip schedules after", tt._after is not None)
    btn.destroy()
    root.update()
    check("tooltip after cancelled on widget destroy", tt._after is None)


def test_repeated_tk_lifecycle_clean():
    pending = []
    for _ in range(3):
        root = tk.Tk()
        root.geometry("1000x650+40+30")
        root.update()
        service = FakeService(sessions=[Snap("a", "A"), Snap("b", "B")])
        wb = WorkbenchWindow(service, lambda sid: None, lambda: None, root=root)
        root.update()
        wb._apply_preferences(UiPreferences("light", "teal", "ask"))
        root.update()
        wb._apply_preferences(UiPreferences("dark", "blue", "ask"))
        root.update()
        wb.destroy()
        try:
            root.update()
        except tk.TclError:
            pass
        pending.append((wb.sessions_scroll._hide_after, wb.sessions_scroll._fade_after,
                        wb.activity_scroll._hide_after, wb.activity_scroll._fade_after,
                        wb.log_scroll._hide_after, wb.log_scroll._fade_after,
                        wb._poll_after))
    check("no pending after across 3 rebuild cycles",
          all(all(x is None for x in row) for row in pending))
    wt.activate_theme("dark", "blue")


def test_theme_switch_immediate_destroy_has_no_tcl_diagnostic():
    source_root = str(Path(__file__).resolve().parents[1])
    script = "\n".join([
        "import sys",
        "sys.path.insert(0, %r)" % source_root,
        "import tkinter as tk",
        "from ui import workbench_theme as wt",
        "root = tk.Tk()",
        "wt.apply_workbench_theme(root)",
        "root.destroy()",
        "next_root = tk.Tk()",
        "next_root.update()",
        "next_root.destroy()",
    ])
    completed = subprocess.run(
        [sys.executable, "-u", "-B", "-X", "dev", "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    diagnostic = completed.stderr.strip()
    ok = completed.returncode == 0 and not diagnostic
    detail = "" if ok else "exit=%d stderr=%s" % (
        completed.returncode, diagnostic or "<empty>")
    check("theme switch immediate destroy leaves no Tcl diagnostic", ok, detail)


# -- runner ------------------------------------------------------------


if __name__ == "__main__":
    import traceback
    tests = [
        test_theme_defaults_roundtrip,
        test_theme_invalid_and_legacy_fallback,
        test_theme_survives_session_save,
        test_activate_theme_tokens_and_semantics,
        test_serial_scan_and_mapping,
        test_manual_and_offline_port,
        test_scan_empty_error_and_failure_keeps_input,
        test_in_use_marker,
        test_submit_failure_keeps_session_dialog_open,
        test_tcp_submit_failure_keeps_session_dialog_open,
        test_scrollbar_autohide,
        test_center_on_parent,
        test_close_dialog_contract,
        test_version_source_and_visible_labels,
        test_settings_dialog_apply_and_cancel,
        test_open_terminal_tracks_terminal_font_group,
        test_workbench_session_list_wheel_scrolls,
        test_canvas_scrollbar_drives_view,
        test_tooltip_cancels_after_on_destroy,
        test_repeated_tk_lifecycle_clean,
        test_theme_switch_immediate_destroy_has_no_tcl_diagnostic,
    ]
    for test in tests:
        try:
            test()
            print("  [PASS] %s" % test.__name__)
        except Exception:
            print("  [FAIL] %s" % test.__name__)
            traceback.print_exc()
            results.append(("EXCEPTION in %s" % test.__name__, False, ""))
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print("RESULTS: %d passed, %d failed, %d total checks" % (passed, failed, len(results)))
    sys.exit(0 if failed == 0 else 1)

"""
Automated tests for the close-action / tray behaviour (TASK-005).

Drives ``_CloseLifecycle`` (main.py) and ``TrayIconManager`` (ui/tray.py) with
fakes for root, controller, terminal, settings persistence and pystray, so no
real COM, no real localhost service and no real display is required.  Any
failure returns a non-zero process exit code.
"""
import subprocess
import sys
import tempfile
import threading
import ctypes
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.profiles as profiles
from app.models import BridgeConfig
from app.profiles import VALID_CLOSE_ACTIONS
from ui import tray as tray_mod
import ui.main_window as ui_main_window
from ui.main_window import (
    CLOSE_ACTION_DISPLAY, CLOSE_ACTION_VALUES, show_close_dialog,
)
from ui.tray import TrayIconManager, TrayUnavailableError
from ui.terminal_window import TerminalWindow
import main as main_mod
from main import _CloseLifecycle

PKG_DIR = str(Path(__file__).resolve().parents[1])

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


# ── Fakes ───────────────────────────────────────────────────────────

class FakeRoot:
    def __init__(self):
        self.after_calls = []
        self.withdraw_calls = 0
        self.deiconify_calls = 0
        self.lift_calls = 0
        self.destroy_calls = 0
        self.state_calls = []

    def after(self, ms, callback):
        self.after_calls.append(callback)
        return len(self.after_calls)

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


class FakeController:
    def __init__(self, close_action="ask", running=False):
        self.config = BridgeConfig(close_action=close_action)
        self.shutdown_calls = 0
        self.state = type("_S", (), {"running": running})()

    def shutdown(self):
        self.shutdown_calls += 1


class FakeTerminal:
    def __init__(self, is_open=True):
        self.is_open = is_open
        self.hide_calls = 0
        self.close_calls = 0

    def hide(self):
        self.hide_calls += 1

    def close(self):
        self.close_calls += 1


class FakeTray:
    def __init__(self, start_ok=True, running=False):
        self.start_calls = 0
        self.stop_calls = 0
        self._start_ok = start_ok
        self._running = running

    @property
    def is_running(self):
        return self._running

    def start(self):
        self.start_calls += 1
        if not self._start_ok:
            raise TrayUnavailableError("mock tray start failure")
        self._running = True

    def stop(self):
        self.stop_calls += 1
        self._running = False


def make_lifecycle(*, close_action="ask", running=False, tray=None,
                   terminal=None):
    root = FakeRoot()
    controller = FakeController(close_action=close_action, running=running)
    t = terminal if terminal is not None else FakeTerminal()
    written = []

    def _save(cfg):
        written.append(cfg.close_action)

    lifecycle = _CloseLifecycle(
        root, controller, _save, lambda: t, tray, lambda *a, **k: None,
    )
    lifecycle._written = written
    lifecycle._terminal = t
    return lifecycle, root, controller, t, written


def dialog(result):
    """Context manager: pretend show_close_dialog returned *result*."""
    return mock.patch.object(ui_main_window, "show_close_dialog", return_value=result)


# ── Test: config close_action whitelist (matrix #1) ─────────────────

def test_config_close_action_whitelist():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            tp.write_text(json_dumps({"last_com_port": "COM1"}), encoding="utf-8")
            check("config: missing -> ask", profiles.load_settings().close_action == "ask")
            for v in VALID_CLOSE_ACTIONS:
                cfg = BridgeConfig(close_action=v)
                profiles.save_settings(cfg)
                check(f"config: roundtrip {v}", profiles.load_settings().close_action == v)
            tp.write_text(json_dumps({"close_action": "bogus"}), encoding="utf-8")
            check("config: unknown -> ask", profiles.load_settings().close_action == "ask")
            tp.write_text(json_dumps({"close_action": True}), encoding="utf-8")
            check("config: bool -> ask", profiles.load_settings().close_action == "ask")
            tp.write_text(json_dumps({"close_action": "ASK"}), encoding="utf-8")
            check("config: case-variant -> ask", profiles.load_settings().close_action == "ask")


def json_dumps(obj):
    import json
    return json.dumps(obj)


# ── Test: close-action display mapping (matrix contract) ────────────

def test_close_action_display_mapping():
    labels = [label for _, label in CLOSE_ACTION_DISPLAY]
    check("display: order Ask/Minimize/Exit",
          labels == ["每次询问", "最小化到系统托盘", "退出应用"])
    check("display: internal values whitelisted",
          all(v in VALID_CLOSE_ACTIONS for v in CLOSE_ACTION_VALUES.values()))
    check("display: label->value maps correctly",
          CLOSE_ACTION_VALUES["最小化到系统托盘"] == "minimize_to_tray")


# ── Test: CLI import adds no tkinter/pystray (matrix #12) ───────────

def test_cli_import_no_tk_or_tray():
    # Verify merely importing main pulls in neither tkinter nor pystray, in a
    # fresh subprocess so this test's own imports don't affect the result.
    code = (
        f"import sys; sys.path.insert(0, {PKG_DIR!r}); import main; "
        "print(('ui.tray' in sys.modules), ('pystray' in sys.modules), "
        "('tkinter' in sys.modules))"
    )
    out = subprocess.check_output([sys.executable, "-B", "-c", code], text=True).strip()
    vals = out.split()
    check("cli: no ui.tray import", vals[0] == "False")
    check("cli: no pystray import", vals[1] == "False")
    check("cli: no tkinter import", vals[2] == "False")


def test_windows_app_identity_is_set_before_gui():
    setter = mock.Mock(return_value=0)
    fake_windll = type("_Windll", (), {
        "shell32": type("_Shell32", (), {
            "SetCurrentProcessExplicitAppUserModelID": setter,
        })(),
    })()
    with (
        mock.patch.object(main_mod.sys, "platform", "win32"),
        mock.patch.object(ctypes, "windll", fake_windll),
    ):
        result = main_mod._set_windows_app_user_model_id()
    check("Windows AppUserModelID applied",
          result is True and setter.call_args.args ==
          (main_mod.WINDOWS_APP_USER_MODEL_ID,))

    with mock.patch.object(main_mod.sys, "platform", "linux"):
        check("non-Windows app identity is a no-op",
              main_mod._set_windows_app_user_model_id() is False)


# ── Lifecycle: ask + Cancel (matrix #2) ─────────────────────────────

def test_ask_cancel():
    tray = FakeTray()
    lifecycle, root, controller, t, _ = make_lifecycle(close_action="ask", tray=tray)
    with dialog(("cancel", False)):
        lifecycle.request_close()
    check("cancel: no hide", root.withdraw_calls == 0)
    check("cancel: no shutdown", controller.shutdown_calls == 0)
    check("cancel: no tray start", tray.start_calls == 0)
    check("cancel: pref still ask", controller.config.close_action == "ask")


def test_main_window_close_only_signals():
    window = object.__new__(ui_main_window.MainWindow)
    calls = []
    window._on_close_request = lambda: calls.append("request")
    window._on_close()
    check("main-window close: only signals lifecycle", calls == ["request"])


def test_hidden_terminal_restores_without_reconnect():
    class FakeTerminalToplevel:
        def __init__(self):
            self.withdraw_calls = 0
            self.deiconify_calls = 0
            self.lift_calls = 0

        def winfo_exists(self):
            return True

        def withdraw(self):
            self.withdraw_calls += 1

        def deiconify(self):
            self.deiconify_calls += 1

        def lift(self):
            self.lift_calls += 1

    class FakeText:
        def __init__(self):
            self.focus_calls = 0

        def focus_set(self):
            self.focus_calls += 1

    terminal = object.__new__(TerminalWindow)
    terminal._win = FakeTerminalToplevel()
    terminal._text = FakeText()
    terminal.hide()
    with mock.patch.object(TerminalWindow, "_connect_client") as connect:
        terminal.open()
    check("terminal-restore: hidden window shown",
          terminal._win.withdraw_calls == 1 and terminal._win.deiconify_calls == 1)
    check("terminal-restore: lifted and focused",
          terminal._win.lift_calls == 1 and terminal._text.focus_calls == 1)
    check("terminal-restore: no second connection", connect.call_count == 0)


# ── Lifecycle: ask + Minimize + not remember (matrix #3) ────────────

def test_ask_minimize_not_remember():
    tray = FakeTray()
    lifecycle, root, controller, t, _ = make_lifecycle(close_action="ask", tray=tray)
    with dialog(("minimize", False)):
        lifecycle.request_close()
    check("minimize: tray started once", tray.start_calls == 1)
    check("minimize: window hidden", root.withdraw_calls == 1)
    check("minimize: bridge not shutdown", controller.shutdown_calls == 0)
    check("minimize: pref still ask", controller.config.close_action == "ask")
    check("minimize: terminal hidden", t.hide_calls == 1)


# ── Lifecycle: ask + Minimize + remember (matrix #4) ────────────────

def test_ask_minimize_remember():
    tray = FakeTray()
    lifecycle, root, controller, t, written = make_lifecycle(close_action="ask", tray=tray)
    with dialog(("minimize", True)):
        lifecycle.request_close()
    check("minimize-remember: pref saved", controller.config.close_action == "minimize_to_tray")
    check("minimize-remember: save written", written == ["minimize_to_tray"])
    check("minimize-remember: hidden", root.withdraw_calls == 1)
    with dialog(None):
        # Second X now bypasses the dialog (config no longer 'ask').
        lifecycle.request_close()
    check("minimize-remember: second click no shutdown", controller.shutdown_calls == 0)
    check("minimize-remember: tray still one", tray.start_calls == 1)


# ── Lifecycle: ask + Exit (matrix #5) ───────────────────────────────

def test_ask_exit_not_remember():
    tray = FakeTray()
    lifecycle, root, controller, t, _ = make_lifecycle(close_action="ask", tray=tray)
    with dialog(("exit", False)):
        lifecycle.request_close()
    check("exit-notremember: safe exited once", controller.shutdown_calls == 1)
    check("exit-notremember: root destroyed", root.destroy_calls == 1)
    check("exit-notremember: pref still ask", controller.config.close_action == "ask")
    check("exit-notremember: terminal closed", t.close_calls == 1)
    check("exit-notremember: tray stopped", tray.stop_calls == 1)


def test_ask_exit_remember():
    tray = FakeTray()
    lifecycle, root, controller, t, written = make_lifecycle(close_action="ask", tray=tray)
    with dialog(("exit", True)):
        lifecycle.request_close()
    check("exit-remember: pref saved", controller.config.close_action == "exit")
    check("exit-remember: save written", written == ["exit"])
    check("exit-remember: exactly one shutdown", controller.shutdown_calls == 1)


# ── Lifecycle: configured minimize / exit (matrix #6) ───────────────

def test_configured_minimize_and_exit():
    tray = FakeTray()
    lifecycle, root, controller, t, written = make_lifecycle(
        close_action="minimize_to_tray", tray=tray,
    )
    lifecycle.request_close()
    check("configured-minimize: tray started", tray.start_calls == 1)
    check("configured-minimize: hid", root.withdraw_calls == 1)
    check("configured-minimize: no shutdown", controller.shutdown_calls == 0)
    check("configured-minimize: preference saved", written == ["minimize_to_tray"])

    tray2 = FakeTray()
    lifecycle2, root2, controller2, t2, _ = make_lifecycle(close_action="exit", tray=tray2)
    lifecycle2.request_close()
    check("configured-exit: safe exited", controller2.shutdown_calls == 1)
    check("configured-exit: root destroyed", root2.destroy_calls == 1)


# ── Lifecycle: tray failure keeps window + bridge (matrix #7) ───────

def test_tray_failure_keeps_window():
    tray = FakeTray(start_ok=False)
    lifecycle, root, controller, t, written = make_lifecycle(close_action="ask", tray=tray)
    with dialog(("minimize", True)):
        lifecycle.request_close()
    check("trayfail: not hidden", root.withdraw_calls == 0)
    check("trayfail: bridge not shutdown", controller.shutdown_calls == 0)
    check("trayfail: pref reverted to ask", controller.config.close_action == "ask")
    check("trayfail: save written ask", written == ["ask"])

    configured_tray = FakeTray(start_ok=False)
    configured, configured_root, configured_controller, _, configured_written = make_lifecycle(
        close_action="minimize_to_tray", tray=configured_tray,
    )
    configured.request_close()
    check("trayfail-configured: not hidden", configured_root.withdraw_calls == 0)
    check("trayfail-configured: pref reverted", configured_controller.config.close_action == "ask")
    check("trayfail-configured: fallback saved", configured_written == ["ask"])


# ── Lifecycle: tray callbacks dispatch via root.after (matrix #8) ───

def test_tray_callbacks_use_root_after():
    tray = FakeTray()
    lifecycle, root, controller, t, _ = make_lifecycle(close_action="ask", tray=tray)
    lifecycle.tray_restore()
    check("restore: dispatched via after", len(root.after_calls) == 1)
    cb = root.after_calls.pop()
    cb()
    check("restore: deiconify+lift+normal",
          root.deiconify_calls == 1 and root.lift_calls == 1 and "normal" in root.state_calls)

    lifecycle.tray_exit()
    check("exit: dispatched via after", len(root.after_calls) == 1)
    cb2 = root.after_calls.pop()
    cb2()
    check("exit cb: safe exit ran", controller.shutdown_calls == 1 and root.destroy_calls == 1)


# ── Lifecycle: repeated minimize uses one tray (matrix #9) ──────────

def test_repeated_minimize_one_tray():
    tray = FakeTray()
    lifecycle, root, controller, t, _ = make_lifecycle(close_action="ask", tray=tray)
    with dialog(("minimize", False)):
        lifecycle.request_close()
        lifecycle.request_close()
    check("repeat-minimize: single tray start", tray.start_calls == 1)
    check("repeat-minimize: no duplicate shutdown", controller.shutdown_calls == 0)


# ── Lifecycle: safe exit idempotent (matrix #10, #11) ───────────────

def test_safe_exit_idempotent():
    tray = FakeTray()
    lifecycle, root, controller, t, _ = make_lifecycle(close_action="ask", tray=tray)
    lifecycle.safe_exit()
    lifecycle.safe_exit()
    check("idempotent: shutdown once", controller.shutdown_calls == 1)
    check("idempotent: destroy once", root.destroy_calls == 1)
    check("idempotent: terminal close once", t.close_calls == 1)
    check("idempotent: tray stop once", tray.stop_calls == 1)


def test_safe_exit_continues_after_save_failure():
    root = FakeRoot()
    controller = FakeController()
    terminal = FakeTerminal()
    tray = FakeTray()

    def broken_save(_config):
        raise OSError("settings write failed")

    lifecycle = _CloseLifecycle(
        root, controller, broken_save, lambda: terminal, tray,
    )
    with mock.patch.object(main_mod.sys, "stderr"):
        lifecycle.safe_exit()
    check("savefail-exit: terminal still closed", terminal.close_calls == 1)
    check("savefail-exit: controller still shutdown", controller.shutdown_calls == 1)
    check("savefail-exit: tray still stopped", tray.stop_calls == 1)
    check("savefail-exit: root still destroyed", root.destroy_calls == 1)


# ── TrayIconManager: success/failure (matrix #7 / #12) ──────────────

class FakeIcon:
    def __init__(self, *a, **k):
        self._stop = threading.Event()
        self.stopped = False
        self.visible = False
        self.name = a[0] if a else k.get("name")
        self.title = a[2] if len(a) > 2 else k.get("title")
        self.menu = a[3] if len(a) > 3 else k.get("menu")

    def run(self, setup=None):
        if setup:
            setup(self)
        self._stop.wait()

    def stop(self):
        self.stopped = True
        self._stop.set()


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items, **k):
        self.items = items


class FakeMenuItem:
    def __init__(self, *a, **k):
        self.args = a
        self.kwargs = k

    def __call__(self, icon):
        return self.args[1](icon, self)


class FakePystray:
    Icon = FakeIcon
    Menu = FakeMenu
    MenuItem = FakeMenuItem


class FakeImage:
    def __init__(self, path):
        self.path = path
        self.closed = False

    @classmethod
    def open(cls, path):
        return cls(path)

    def close(self):
        self.closed = True


def _install_pystray_fakes():
    import types
    pil = types.ModuleType("PIL")
    pilimg = types.ModuleType("PIL.Image")
    pilimg.open = FakeImage.open
    pil.Image = pilimg
    return mock.patch.dict(
        sys.modules,
        {"pystray": FakePystray, "PIL": pil, "PIL.Image": pilimg},
        clear=False,
    )


def test_tray_start_stop_success():
    # Default icon path points at the real generated asset (exists).
    callbacks = []
    tray = TrayIconManager(
        lambda: callbacks.append("restore"),
        lambda: callbacks.append("exit"),
    )
    with _install_pystray_fakes():
        tray.start()
        check("tray: started", tray.is_running and isinstance(tray._icon, FakeIcon))
        check("tray: setup made icon visible", tray._icon.visible is True)
        check("tray: short Chinese display name",
              tray._icon.title == tray_mod.APP_DISPLAY_NAME == "串口桥接")
        tray._icon.menu.items[0](tray._icon)
        tray._icon.menu.items[2](tray._icon)
        check("tray: menu callbacks accept pystray arguments", callbacks == ["restore", "exit"])
        image = tray._image
        tray.stop()
        check("tray: stopped", not tray.is_running and tray._thread is None)
        check("tray: image closed", image.closed)


def test_tray_start_reports_missing_dependency():
    tray = TrayIconManager(lambda: None, lambda: None, icon_path="x.ico")
    builtins = sys.modules["builtins"]
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "pystray":
            raise ImportError("pystray missing")
        return real_import(name, *a, **k)

    with mock.patch.object(builtins, "__import__", side_effect=fake_import):
        try:
            tray.start()
            ok = False
        except TrayUnavailableError:
            ok = True
    check("tray: missing pystray -> TrayUnavailableError", ok)


def test_tray_start_ready_timeout():
    class NoReadyIcon(FakeIcon):
        def run(self, setup=None):
            self._stop.wait()

    tray = TrayIconManager(lambda: None, lambda: None)
    with _install_pystray_fakes():
        with mock.patch.object(FakePystray, "Icon", NoReadyIcon):
            try:
                tray.start()
                ok = False
            except TrayUnavailableError:
                ok = True
            check("tray: ready timeout -> TrayUnavailableError", ok)
            check("tray: ready timeout leaves no thread",
                  tray._thread is None and not any(
                      thread.name == "tray-icon" for thread in threading.enumerate()
                  ))


def test_tray_start_thread_failure():
    class FailingIcon(FakeIcon):
        def run(self, setup=None):
            raise RuntimeError("mock tray loop failure")

    tray = TrayIconManager(lambda: None, lambda: None)
    with _install_pystray_fakes():
        with mock.patch.object(FakePystray, "Icon", FailingIcon):
            try:
                tray.start()
                ok = False
            except TrayUnavailableError as exc:
                ok = "mock tray loop failure" in str(exc)
            check("tray: thread failure -> TrayUnavailableError", ok)
            check("tray: thread failure cleaned references",
                  tray._icon is None and tray._thread is None)


def test_tray_start_rejects_live_partial_thread():
    release = threading.Event()
    partial = threading.Thread(
        target=release.wait, name="tray-icon", daemon=True,
    )
    partial.start()
    tray = TrayIconManager(lambda: None, lambda: None)
    tray._thread = partial
    try:
        try:
            tray.start()
            ok = False
        except TrayUnavailableError as exc:
            ok = "still running" in str(exc)
        check("tray: live partial thread blocks restart", ok)
        check("tray: live partial thread handle retained", tray._thread is partial)
    finally:
        release.set()
        partial.join(timeout=1)
        tray._cleanup_refs()


# ── Runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    tests = [
        test_config_close_action_whitelist,
        test_close_action_display_mapping,
        test_cli_import_no_tk_or_tray,
        test_windows_app_identity_is_set_before_gui,
        test_ask_cancel,
        test_main_window_close_only_signals,
        test_hidden_terminal_restores_without_reconnect,
        test_ask_minimize_not_remember,
        test_ask_minimize_remember,
        test_ask_exit_not_remember,
        test_ask_exit_remember,
        test_configured_minimize_and_exit,
        test_tray_failure_keeps_window,
        test_tray_callbacks_use_root_after,
        test_repeated_minimize_one_tray,
        test_safe_exit_idempotent,
        test_safe_exit_continues_after_save_failure,
        test_tray_start_stop_success,
        test_tray_start_reports_missing_dependency,
        test_tray_start_ready_timeout,
        test_tray_start_thread_failure,
        test_tray_start_rejects_live_partial_thread,
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

"""
Serial Bridge Manager — Entry Point
====================================
GUI mode (default):
    python main.py

CLI mode (headless):
    python main.py --cli COM9 115200 9999

Requirements:
    pip install pyserial
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time

WINDOWS_APP_USER_MODEL_ID = "xinme324.SerialBridgeManager"


def _set_windows_app_user_model_id() -> bool:
    """Give Windows one stable identity for shortcuts and taskbar grouping."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
        return result == 0
    except (AttributeError, OSError):
        return False


def _ensure_pyserial():
    """Auto-install pyserial if missing (best-effort)."""
    try:
        import serial  # noqa: F401
    except ImportError:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "pyserial"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


# ── CLI mode (unchanged) ────────────────────────────────────────────


def cli_mode(com_port, baud_rate, tcp_port):
    """Run bridge headless — logs to stdout, runs until Ctrl+C."""
    from bridge import SerialBridge

    bridge = SerialBridge(
        com_port, baud_rate, tcp_port,
        status_callback=lambda m: print(f"[STATUS] {m}", flush=True),
        log_callback=lambda m: print(f"[LOG] {m}", flush=True),
    )

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ok = loop.run_until_complete(bridge.start())
        if not ok:
            print("[FATAL] Bridge failed to start", flush=True)
            loop.close()
            return
        print(f"[READY] Bridge running — telnet localhost {tcp_port}", flush=True)
        while bridge.running:
            loop.run_until_complete(asyncio.sleep(0.2))
        loop.run_until_complete(bridge.stop())
        loop.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    try:
        while t.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[STOP] Shutting down...", flush=True)
        bridge.running = False
        t.join(timeout=5)
        print("[STOP] Done", flush=True)


# ── GUI mode (new architecture) ─────────────────────────────────────


class _CloseLifecycle:
    """Single owner of the GUI close / tray / exit flow.

    ``gui_mode()`` composes this with real handles; tests inject fakes for the
    root, controller, terminal getter and tray manager.  It never closes a
    resource more than once (see ``_closing``) and it never lets the tray
    thread touch tkinter directly — tray callbacks dispatch via ``root.after``.
    """

    def __init__(self, root, controller, save_settings, terminal_getter, tray,
                 show_error=None, window_teardown=None):
        self._root = root
        self._controller = controller
        self._save = save_settings
        self._terminal_getter = terminal_getter  # () -> TerminalWindow | None
        self._tray = tray                        # TrayIconManager | None
        self._show_error = show_error or (lambda title, message: None)
        self._window_teardown = window_teardown
        self._closing = False

    # ── Entry: the user clicked the window X ───────────────────────

    def request_close(self):
        action = self._controller.config.close_action
        if action == "ask":
            from ui.main_window import show_close_dialog
            chosen, remember = show_close_dialog(
                self._root, self._controller.state.running,
            )
            if chosen == "cancel":
                return
            if chosen == "minimize":
                self._minimize(remember)
                return
            if chosen == "exit":
                self._exit(remember)
                return
            return
        if action == "minimize_to_tray":
            self._minimize(False)
            return
        if action == "exit":
            self._exit(False)
            return

    # ── Minimize to tray ───────────────────────────────────────────

    def _minimize(self, remember):
        try:
            if not self._tray.is_running:
                self._tray.start()
        except Exception as exc:  # noqa: BLE001 - any tray failure keeps window up
            self._controller.config.close_action = "ask"
            save_error = None
            try:
                self._save(self._controller.config)
            except Exception as save_exc:  # noqa: BLE001
                save_error = save_exc
            detail = str(exc)
            if save_error is not None:
                detail += f"\n无法保存回退设置：{save_error}"
            self._show_error(
                "系统托盘不可用",
                f"{detail}\n\n应用仍在运行。",
            )
            return
        if remember:
            self._controller.config.close_action = "minimize_to_tray"
        try:
            self._save(self._controller.config)
        except Exception as exc:  # noqa: BLE001
            self._show_error(
                "设置未保存",
                f"{exc}\n\n本次运行仍会将窗口最小化。",
            )
        self._hide_terminal()
        try:
            self._root.withdraw()
        except Exception:  # noqa: BLE001
            pass

    def _hide_terminal(self):
        terminal = self._terminal_getter()
        if terminal is not None and terminal.is_open:
            try:
                terminal.hide()
            except Exception:  # noqa: BLE001
                pass

    # ── Restore from tray ──────────────────────────────────────────

    def restore(self):
        try:
            self._root.deiconify()
            self._root.state("normal")
            self._root.lift()
        except Exception:  # noqa: BLE001
            pass

    def tray_restore(self):
        # Runs on the tray thread; dispatch to the Tk main thread.
        try:
            self._root.after(0, self.restore)
        except Exception:  # noqa: BLE001 - root may be destroyed
            pass

    # ── Exit ───────────────────────────────────────────────────────

    def _exit(self, remember):
        if remember:
            # The new preference is persisted by safe_exit() below, once.
            self._controller.config.close_action = "exit"
        self.safe_exit()

    def tray_exit(self):
        # Runs on the tray thread; dispatch to the Tk main thread.
        try:
            self._root.after(0, self.safe_exit)
        except Exception:  # noqa: BLE001
            pass

    def safe_exit(self):
        """Idempotent, single safe-exit path that releases every resource."""
        if self._closing:
            return
        self._closing = True
        errors = []
        try:
            self._save(self._controller.config)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"save settings: {exc}")
        terminal = self._terminal_getter()
        if terminal is not None and terminal.is_open:
            try:
                terminal.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"close terminal: {exc}")
        try:
            self._controller.shutdown()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"shutdown controller: {exc}")
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception as exc:  # noqa: BLE001 - destroy Tk even on failure
                errors.append(f"stop tray: {exc}")
        if self._window_teardown is not None:
            try:
                self._window_teardown()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"teardown window: {exc}")
                try:
                    self._root.destroy()
                except Exception as destroy_exc:  # noqa: BLE001
                    errors.append(f"destroy root: {destroy_exc}")
        else:
            try:
                self._root.destroy()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"destroy root: {exc}")
        if errors:
            print("Shutdown completed with errors: " + "; ".join(errors), file=sys.stderr)


class _ConfigProxy:
    """Expose AppSettings.close_action as a BridgeConfig-like property."""

    def __init__(self, settings):
        self._settings = settings

    @property
    def close_action(self) -> str:
        return self._settings.close_action

    @close_action.setter
    def close_action(self, value: str) -> None:
        self._settings.close_action = value


class _SessionStateProxy:
    """Expose "any session running" as a controller-like state.running."""

    def __init__(self, manager):
        self._manager = manager

    @property
    def running(self) -> bool:
        try:
            return any(s.running for s in self._manager.list_sessions())
        except Exception:  # noqa: BLE001 - a transient error must not break close
            return False


class _SessionControllerFacade:
    """Present the multi-session manager as a single controller.

    The close/tray lifecycle was written against a single BridgeController;
    this facade adapts the SessionManager + AppSettings to that same surface so
    the lifecycle can operate across every session while the legacy
    single-controller path (exercised by the frozen regression tests) is
    untouched.
    """

    def __init__(self, manager, settings):
        self._manager = manager
        self.config = _ConfigProxy(settings)
        self.state = _SessionStateProxy(manager)

    def shutdown(self) -> None:
        self._manager.shutdown_all()


class _TerminalCollection:
    """Aggregate the per-session terminal windows for the lifecycle."""

    def __init__(self, terminals):
        self._terminals = terminals

    @property
    def is_open(self) -> bool:
        return any(t.is_open for t in self._terminals.values())

    def hide(self) -> None:
        for t in self._terminals.values():
            try:
                t.hide()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        for t in self._terminals.values():
            try:
                t.close()
            except Exception:  # noqa: BLE001
                pass


class _AutoSaveService:
    """Delegate SessionManager calls while coordinating external resources.

    Selection-changing create/delete operations are persisted by the
    WorkbenchWindow selection callback, after its final active id is known.
    Edits do not change selection, so they are persisted here after replacing
    the controller; any terminal bound to the old controller is closed first.
    """

    def __init__(self, manager, persist, before_delete=None, before_update=None,
                 port_provider=None):
        self._manager = manager
        self._persist = persist
        self._before_delete = before_delete
        self._before_update = before_update
        self._port_provider = port_provider

    def __getattr__(self, name):
        return getattr(self._manager, name)

    def list_serial_ports(self):
        """Enumerate serial ports through the injected provider (never opens)."""
        if self._port_provider is None:
            return []
        return list(self._port_provider())

    def create_session(self, config):
        self._manager.ensure_tcp_port_available(config.tcp_port)
        return self._manager.create_session(config)

    def update_session(self, session_id, config):
        self._manager.ensure_tcp_port_available(
            config.tcp_port, exclude_id=session_id,
        )
        if self._before_update is not None:
            self._before_update(session_id)
        self._manager.update_session(session_id, config)
        self._persist()

    def start_session(self, session_id):
        snapshot = self._manager.get_snapshot(session_id)
        if not (snapshot.running or snapshot.starting):
            self._manager.ensure_tcp_port_available(
                snapshot.tcp_port, exclude_id=session_id,
            )
        self._manager.start_session(session_id)

    def delete_session(self, session_id):
        # A session cannot be deleted while its terminal is still open.
        if self._before_delete is not None:
            self._before_delete(session_id)
        self._manager.delete_session(session_id)


def gui_mode():
    """Launch the multi-session Serial Bridge Manager GUI (TASK-009).

    Wires the TASK-007 SessionManager into the TASK-008 WorkbenchWindow,
    adapts TASK-005 close/tray lifecycle to all sessions, enables v1->v2
    settings migration and per-session terminal windows.  The CLI path is
    unchanged.
    """
    _set_windows_app_user_model_id()

    from app.models import SessionConfig
    from app.ports import list_ports
    from app.profiles import default_logs_dir, load_app_settings, save_app_settings
    from app.session_manager import SessionManager
    from app.traffic_log import prepare_log_directory
    from tkinter import messagebox
    from ui import workbench_theme as wt
    from ui.main_window import MainWindow
    from ui.terminal_window import TerminalWindow
    from ui.tray import TrayIconManager, TrayUnavailableError

    # Load v2 settings (migrates a v1 config to a single session on the fly).
    settings = load_app_settings()
    if not settings.traffic_log_directory.strip():
        settings.traffic_log_directory = str(default_logs_dir())

    # Real session manager (one BridgeController per session, no real COM).
    manager = SessionManager(
        traffic_log_enabled=settings.traffic_log_enabled,
        traffic_log_directory=settings.traffic_log_directory,
    )
    for sess in settings.sessions:
        if not sess.com_port or not sess.com_port.strip():
            # The sole built-in default is an intentional editable placeholder.
            if len(settings.sessions) == 1 and sess.session_id == "default":
                continue
            raise ValueError(
                f"Cannot load saved session {sess.session_id!r}: "
                "com_port must not be empty"
            )
        try:
            manager.create_session(SessionConfig(
                session_id=sess.session_id, name=sess.name, com_port=sess.com_port,
                baud_rate=sess.baud_rate, tcp_port=sess.tcp_port,
                newline_mode=sess.newline_mode, auto_reconnect=sess.auto_reconnect,
            ))
        except ValueError as exc:
            raise ValueError(
                f"Cannot load saved session {sess.session_id!r}: {exc}"
            ) from exc

    # Per-session terminal windows.  Each session owns at most one window.
    terminals: dict[str, TerminalWindow] = {}

    def open_terminal(sid):
        if not sid:
            return
        ctrl = manager.get_controller(sid)
        win = terminals.get(sid)
        if win is None or not win.is_open:
            win = TerminalWindow(main.root, ctrl)
            terminals[sid] = win
        win.open()

    def close_terminal(sid):
        win = terminals.get(sid)
        if win is None:
            return
        if win.is_open:
            win.close()
        terminals.pop(sid, None)

    def show_error(title, message):
        try:
            messagebox.showerror(title, message)
        except Exception:  # noqa: BLE001
            pass

    active_session = {"id": settings.active_session_id}

    # Persist the current selection + close action + theme as v2 settings.
    def write_settings(config=None):
        app = manager.export_settings(
            (config.close_action if config is not None else settings.close_action),
            active_session["id"],
        )
        app.theme_mode = settings.theme_mode
        app.accent_color = settings.accent_color
        app.traffic_log_enabled = settings.traffic_log_enabled
        app.traffic_log_directory = settings.traffic_log_directory
        for name in wt.FONT_SIZE_DEFAULTS:
            setattr(app, name, getattr(settings, name))
        save_app_settings(app)

    def persist_settings(config=None):
        try:
            write_settings(config)
        except Exception as exc:  # noqa: BLE001 - surface, never stop sessions
            show_error("设置未保存", str(exc))

    def apply_preferences(prefs):
        """Validate + persist a UiPreferences tuple; roll back on failure."""
        old = (
            settings.theme_mode,
            settings.accent_color,
            *(getattr(settings, name) for name in wt.FONT_SIZE_DEFAULTS),
            settings.close_action,
            settings.traffic_log_enabled,
            settings.traffic_log_directory,
        )
        settings.theme_mode = wt.normalize_theme(prefs.theme_mode)
        settings.accent_color = wt.normalize_accent(prefs.accent_color)
        for name, default in wt.FONT_SIZE_DEFAULTS.items():
            setattr(
                settings,
                name,
                wt.normalize_font_size(name, getattr(prefs, name, default)),
            )
        settings.close_action = (
            prefs.close_action if prefs.close_action in
            ("ask", "minimize_to_tray", "exit") else "ask"
        )
        settings.traffic_log_enabled = bool(prefs.traffic_log_enabled)
        settings.traffic_log_directory = str(prefs.traffic_log_directory or "").strip()
        if settings.traffic_log_enabled:
            try:
                settings.traffic_log_directory = str(
                    prepare_log_directory(settings.traffic_log_directory)
                )
            except (OSError, ValueError) as exc:
                (
                    settings.theme_mode,
                    settings.accent_color,
                    settings.title_font_size,
                    settings.ui_font_size,
                    settings.helper_font_size,
                    settings.terminal_font_size,
                    settings.close_action,
                    settings.traffic_log_enabled,
                    settings.traffic_log_directory,
                ) = old
                show_error("设置未保存", str(exc))
                return False
        try:
            write_settings()
            manager.configure_traffic_logging(
                settings.traffic_log_enabled,
                settings.traffic_log_directory,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - restore and keep old theme
            (
                settings.theme_mode,
                settings.accent_color,
                settings.title_font_size,
                settings.ui_font_size,
                settings.helper_font_size,
                settings.terminal_font_size,
                settings.close_action,
                settings.traffic_log_enabled,
                settings.traffic_log_directory,
            ) = old
            show_error("设置未保存", str(exc))
            return False

    # MainWindow is a thin shim over WorkbenchWindow; it registers the close
    # intent once (via on_close_request) and never owns lifecycle decisions.
    request_holder: dict[str, object] = {"fn": None}

    def on_close_request():
        fn = request_holder["fn"]
        if fn is not None:
            fn()

    def on_open_terminal(sid):
        open_terminal(sid)

    def on_selection_change(sid):
        active_session["id"] = sid
        persist_settings()

    # Auto-persist settings when sessions are created/edited/deleted, and
    # close the per-session terminal before a session is deleted.
    service = _AutoSaveService(
        manager,
        persist_settings,
        before_delete=close_terminal,
        before_update=close_terminal,
        port_provider=list_ports,
    )

    main = MainWindow(
        service,
        on_open_terminal=on_open_terminal,
        on_close_request=on_close_request,
        initial_session_id=settings.active_session_id,
        on_selection_change=on_selection_change,
        initial_theme_mode=settings.theme_mode,
        initial_accent_color=settings.accent_color,
        initial_title_font_size=settings.title_font_size,
        initial_ui_font_size=settings.ui_font_size,
        initial_helper_font_size=settings.helper_font_size,
        initial_terminal_font_size=settings.terminal_font_size,
        initial_close_action=settings.close_action,
        initial_traffic_log_enabled=settings.traffic_log_enabled,
        initial_traffic_log_directory=settings.traffic_log_directory,
        on_preferences_change=apply_preferences,
    )
    active_session["id"] = getattr(main._wb, "_selected_id", None)

    # Adapt the multi-session manager to the lifecycle facade.
    facade = _SessionControllerFacade(manager, settings)
    terminal_collection = _TerminalCollection(terminals)

    # Build the close/tray lifecycle and wire it as the close handler.
    lifecycle = _CloseLifecycle(
        main.root, facade, persist_settings, lambda: terminal_collection,
        None, show_error, window_teardown=main.destroy,
    )
    request_holder["fn"] = lifecycle.request_close

    # Tray callbacks run on the tray thread; dispatch via root.after.
    tray = TrayIconManager(lifecycle.tray_restore, lifecycle.tray_exit)
    lifecycle._tray = tray

    try:
        tray.start()
    except TrayUnavailableError as exc:
        show_error("系统托盘不可用", str(exc))

    try:
        main.run()
    finally:
        if tray.is_running:
            try:
                tray.stop()
            except TrayUnavailableError:
                pass


# ── Entry point ──────────────────────────────────────────────────────


def main():
    _ensure_pyserial()

    # CLI mode: python main.py --cli COM9 115200 9999
    if "--cli" in sys.argv:
        try:
            idx = sys.argv.index("--cli")
            com = sys.argv[idx + 1]
            baud = int(sys.argv[idx + 2]) if len(sys.argv) > idx + 2 else 115200
            port = int(sys.argv[idx + 3]) if len(sys.argv) > idx + 3 else 9999
        except (IndexError, ValueError):
            print("Usage: python main.py --cli <COM_PORT> [BAUD] [TCP_PORT]")
            print("Example: python main.py --cli COM9 115200 9999")
            sys.exit(1)
        cli_mode(com, baud, port)
        return

    # GUI mode (default)
    gui_mode()


if __name__ == "__main__":
    main()

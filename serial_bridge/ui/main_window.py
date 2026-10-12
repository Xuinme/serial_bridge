"""Main window entry for Serial Bridge Manager.

TASK-009: the formal GUI is now the multi-session WorkbenchWindow.  The
"MainWindow" name is kept as a thin compat shim so existing entry points and
the frozen close/lifecycle tests keep working, but all rendering and session
routing is delegated to WorkbenchWindow.  There is exactly one formal GUI.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import theme

# -- Window close-action contract --------------------------------------
# The dropdown shows these labels; the configuration persists the internal
# value (left column).  Only these internal values are legal.
CLOSE_ACTION_DISPLAY = [
    ("ask", "每次询问"),
    ("minimize_to_tray", "最小化到系统托盘"),
    ("exit", "退出应用"),
]
CLOSE_ACTION_LABELS = [label for _, label in CLOSE_ACTION_DISPLAY]
CLOSE_ACTION_VALUES = {label: value for value, label in CLOSE_ACTION_DISPLAY}


def show_close_dialog(parent, bridge_running: bool) -> tuple[str, bool]:
    """Show a modal three-button close dialog.

    Returns ``(action, remember_choice)`` where ``action`` is one of
    ``"minimize"`` / ``"exit"`` / ``"cancel"``.

    Must be called from the Tk main thread.  Uses a custom ``Toplevel`` so the
    buttons are explicit (not a Yes/No/Cancel messagebox).
    """
    from . import workbench_theme as wt
    from .workbench_widgets import center_on_parent, fade_in_toplevel

    result = {"action": "cancel", "remember": False}
    remember = tk.BooleanVar(value=False)

    dlg = tk.Toplevel(parent)
    dlg.title(f"关闭 {wt.APP_DISPLAY_NAME}")
    dlg.configure(bg=wt.PANEL_BG)
    dlg.resizable(False, False)
    wt.apply_workbench_theme(dlg)
    wt.apply_window_icon(dlg)

    if bridge_running:
        body = "串口桥接正在运行。退出将断开所有客户端并释放串口。"
    else:
        body = "关闭窗口时要执行什么操作？"

    tk.Label(
        dlg,
        text=body,
        bg=wt.PANEL_BG,
        fg=wt.TEXT,
        font=wt.BODY_FONT,
        justify="left",
        wraplength=360,
    ).pack(anchor="w", padx=22, pady=(18, 6))

    tk.Checkbutton(
        dlg,
        text="记住我的选择",
        variable=remember,
        bg=wt.PANEL_BG,
        fg=wt.TEXT,
        font=wt.SMALL_FONT,
        anchor="w",
        selectcolor=wt.CARD_BG,
        activebackground=wt.PANEL_BG,
        activeforeground=wt.TEXT,
        highlightthickness=0,
    ).pack(anchor="w", padx=22, pady=(0, 12))

    def choose(action: str):
        result["action"] = action
        result["remember"] = remember.get()
        try:
            dlg.grab_release()
        except tk.TclError:
            pass
        try:
            dlg.destroy()
        except tk.TclError:
            pass

    buttons = tk.Frame(dlg, bg=wt.PANEL_BG)
    buttons.pack(fill="x", padx=22, pady=(4, 18))
    buttons.grid_columnconfigure(0, weight=1)
    buttons.grid_columnconfigure(1, weight=1)
    buttons.grid_columnconfigure(2, weight=1)

    cancel_btn = tk.Button(
        buttons, text="取消", command=lambda: choose("cancel"),
        bg=wt.CARD_BG, fg=wt.TEXT, relief="flat",
        activebackground=wt.SIDEBAR_HOVER, activeforeground=wt.TEXT,
    )
    cancel_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
    minimize_btn = tk.Button(
        buttons, text="最小化到托盘", command=lambda: choose("minimize"),
        bg=wt.BLUE, fg="#ffffff", relief="flat",
        activebackground=wt.BLUE, activeforeground="#ffffff",
    )
    minimize_btn.grid(row=0, column=1, sticky="ew", padx=(0, 8))
    exit_btn = tk.Button(
        buttons, text="退出应用", command=lambda: choose("exit"),
        bg=wt.RED, fg="#ffffff", relief="flat",
        activebackground=wt.RED, activeforeground="#ffffff",
    )
    exit_btn.grid(row=0, column=2, sticky="ew")

    def invoke_focused_button(event):
        button = event.widget
        if button in (cancel_btn, minimize_btn, exit_btn):
            button.invoke()
        return "break"

    for button in (cancel_btn, minimize_btn, exit_btn):
        button.bind("<Return>", invoke_focused_button)

    dlg.update_idletasks()
    center_on_parent(dlg, parent)
    dlg.transient(parent)
    try:
        dlg.grab_set()
    except tk.TclError:
        pass
    dlg.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))
    dlg.bind("<Escape>", lambda _: choose("cancel"))
    cancel_btn.focus_set()
    fade_in_toplevel(dlg)

    parent.wait_window(dlg)
    return (result["action"], result["remember"])


class MainWindow:
    """Thin compat shim pointing to the multi-session WorkbenchWindow.

    Keeps the ``MainWindow`` name and its close-signalling surface so existing
    entry points and the frozen close/lifecycle tests keep working, but all
    rendering and session routing is delegated to ``WorkbenchWindow``.  This is
    the single formal GUI; there is no second old-styled window.
    """

    def __init__(self, service,
                 on_open_terminal: Callable[[str], None] | None = None,
                 on_close_request: Callable[[], None] | None = None,
                 initial_session_id: str | None = None,
                 on_selection_change: Callable[[str | None], None] | None = None,
                 initial_theme_mode: str = "dark",
                 initial_accent_color: str = "blue",
                 initial_title_font_size: int = 16,
                 initial_ui_font_size: int = 10,
                 initial_helper_font_size: int = 9,
                 initial_terminal_font_size: int = 10,
                 initial_close_action: str = "ask",
                 initial_traffic_log_enabled: bool = False,
                 initial_traffic_log_directory: str = "",
                 on_preferences_change=None):
        from .workbench_window import WorkbenchWindow
        self._service = service
        self._on_open_terminal = on_open_terminal
        self._on_close_request = on_close_request
        self._wb = WorkbenchWindow(
            service,
            on_open_terminal if on_open_terminal else (lambda _sid: None),
            self._request_close,
            initial_session_id=initial_session_id,
            on_selection_change=on_selection_change,
            initial_theme_mode=initial_theme_mode,
            initial_accent_color=initial_accent_color,
            initial_title_font_size=initial_title_font_size,
            initial_ui_font_size=initial_ui_font_size,
            initial_helper_font_size=initial_helper_font_size,
            initial_terminal_font_size=initial_terminal_font_size,
            initial_close_action=initial_close_action,
            initial_traffic_log_enabled=initial_traffic_log_enabled,
            initial_traffic_log_directory=initial_traffic_log_directory,
            on_preferences_change=on_preferences_change,
        )
        self.root = self._wb.root

    def _request_close(self):
        if self._on_close_request is not None:
            self._on_close_request()

    def _on_close(self):
        """MainWindow only signals the lifecycle owner; never exits itself."""
        if self._on_close_request is not None:
            self._on_close_request()

    def run(self):
        self._wb.root.mainloop()

    def destroy(self):
        """Convenience teardown for the workbench (kept for tests/integration)."""
        self._wb.destroy()

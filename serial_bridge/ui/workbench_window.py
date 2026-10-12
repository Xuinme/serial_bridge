"""Codex-style multi-session workstation UI (standalone shell).

WorkbenchWindow is a fully self-contained four-region workbench:

    +--------------------------------------------------------------+
    |  top bar: current session + Start/Stop toggle / Delete         |
    +--------+--------------------------------+--------------------+
    |  left  |   centre: activity + log       |  right details     |
    |  rail  |                                |                    |
    +--------+--------------------------------+--------------------+
    |                    status bar                                  |
    +--------------------------------------------------------------+

It talks to the backend ONLY through the TASK-007 frozen SessionManager
public methods (duck-typed service) and two callbacks:
on_open_terminal(session_id) and on_close_request().  It never owns a
BridgeController, a SerialBridge, a serial port or any lifecycle decision;
every Start/Stop/Delete/Telnet carries the correct session_id.

The window is exercised in this task exclusively against a fake service.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from app.tcp_ports import TcpPortUnavailableError

from . import workbench_theme as wt
from .workbench_dialogs import (
    SessionConfig,
    UiPreferences,
    config_from_snapshot,
    show_confirm_delete,
    show_session_dialog,
    show_settings_dialog,
    show_tcp_port_in_use,
)
from .workbench_widgets import AutoHideScrollbar


class SessionItem(tk.Frame):
    """A single clickable row in the left session rail."""

    def __init__(self, parent, on_select):
        super().__init__(parent, bg=wt.SIDEBAR_BG, cursor="hand2")
        self._on_select = on_select
        self._sid = None
        self._selected = False
        self._render_state = None
        self.grid_columnconfigure(1, weight=1)
        self._dot = tk.Canvas(self, width=12, height=12, bg=wt.SIDEBAR_BG,
                              highlightthickness=0)
        self._dot.grid(row=0, column=0, rowspan=2, sticky="n",
                       padx=(12, 10), pady=(16, 0))
        self._name_lbl = tk.Label(self, text="", bg=wt.SIDEBAR_BG, fg=wt.TEXT,
                                  font=wt.BODY_FONT, anchor="w")
        self._name_lbl.grid(row=0, column=1, sticky="ew", pady=(12, 0))
        self._sub_lbl = tk.Label(self, text="", bg=wt.SIDEBAR_BG,
                                 fg=wt.TEXT_MUTED, font=wt.TINY_FONT,
                                 anchor="w", justify="left")
        self._sub_lbl.grid(row=1, column=1, sticky="ew", pady=(1, 12))
        for w in (self, self._dot, self._name_lbl, self._sub_lbl):
            w.bind("<Button-1>", lambda _e, s=self._sid: self._click())
            w.bind("<Enter>", lambda _e: self._enter(True))
            w.bind("<Leave>", lambda _e: self._enter(False))

    def _click(self):
        if self._on_select:
            self._on_select(self._sid)

    def _enter(self, on):
        if not self._selected:
            self._paint(wt.SIDEBAR_HOVER if on else wt.SIDEBAR_BG)

    def _paint(self, bg):
        self.configure(bg=bg)
        for w in (self._dot, self._name_lbl, self._sub_lbl):
            w.configure(bg=bg)

    def bind_sid(self, sid):
        self._sid = sid

    def bind_wheel(self, handler):
        """Route a mouse-wheel over the whole row to the session list."""
        for w in (self, self._dot, self._name_lbl, self._sub_lbl):
            w.bind("<MouseWheel>", handler, add="+")
            w.bind("<Button-4>", handler, add="+")
            w.bind("<Button-5>", handler, add="+")

    def update(self, snapshot, selected):
        render_state = (
            snapshot.session_id,
            snapshot.name,
            snapshot.com_port,
            snapshot.baud_rate,
            snapshot.tcp_port,
            bool(snapshot.running),
            bool(snapshot.starting),
            bool(snapshot.last_error),
            bool(selected),
        )
        if render_state == self._render_state:
            return False

        self._render_state = render_state
        self._selected = selected
        self._sid = snapshot.session_id
        bg = wt.SIDEBAR_ACTIVE if selected else wt.SIDEBAR_BG
        self._paint(bg)
        accent = wt.GREEN if snapshot.running else (
            wt.AMBER if snapshot.starting else (
                wt.RED if snapshot.last_error else wt.TEXT_MUTED))
        self._dot.delete("all")
        self._dot.create_oval(2, 2, 10, 10, fill=accent, outline="")
        self._name_lbl.configure(text=snapshot.name or snapshot.session_id,
                                 fg=wt.BLUE if selected else wt.TEXT)
        self._sub_lbl.configure(
            text=(f"{snapshot.com_port} @ {snapshot.baud_rate}\n"
                  f"TCP {snapshot.tcp_port}"))
        return True


class WorkbenchWindow:
    """Four-region multi-session workbench driven by a duck-typed service."""

    def __init__(self, service, on_open_terminal: Callable[[str], None],
                 on_close_request: Callable[[], None], root: tk.Tk | None = None,
                 min_size: tuple[int, int] | None = None,
                 collapse_threshold: int | None = None,
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
        self._service = service
        self._on_open_terminal = on_open_terminal
        self._on_close_request = on_close_request
        self._on_selection_change = on_selection_change
        self._on_preferences_change = on_preferences_change
        self._prefs = UiPreferences(
            theme_mode=wt.normalize_theme(initial_theme_mode),
            accent_color=wt.normalize_accent(initial_accent_color),
            close_action=(initial_close_action if initial_close_action in
                          ("ask", "minimize_to_tray", "exit") else "ask"),
            title_font_size=wt.normalize_font_size(
                "title_font_size", initial_title_font_size),
            ui_font_size=wt.normalize_font_size(
                "ui_font_size", initial_ui_font_size),
            helper_font_size=wt.normalize_font_size(
                "helper_font_size", initial_helper_font_size),
            terminal_font_size=wt.normalize_font_size(
                "terminal_font_size", initial_terminal_font_size),
            traffic_log_enabled=bool(initial_traffic_log_enabled),
            traffic_log_directory=str(initial_traffic_log_directory or ""),
        )
        wt.activate_theme(
            self._prefs.theme_mode,
            self._prefs.accent_color,
            self._prefs.title_font_size,
            self._prefs.ui_font_size,
            self._prefs.helper_font_size,
            self._prefs.terminal_font_size,
        )
        self.min_size = min_size or (wt.MIN_W, wt.MIN_H)
        self.collapse_threshold = collapse_threshold or wt.COLLAPSE_THRESHOLD

        self._snapshots: dict[str, object] = {}
        self._selected_id: str | None = initial_session_id
        self._selection_notifications_enabled = False
        self._logs: dict[str, list[tuple[float, str]]] = {}
        self._activity: dict[str, list[tuple[float, str, str, str]]] = {}
        self._paused = False
        self._close_dispatching = False
        self._right_collapsed = False
        self._poll_after: str | None = None

        self._created_root = root is None
        self.root = root if root is not None else tk.Tk()
        if self._created_root:
            self.root.withdraw()
        self.root.title(wt.APP_DISPLAY_NAME)
        wt.apply_workbench_theme(self.root)
        wt.apply_window_icon(self.root)
        self.root.minsize(*self.min_size)
        if self._created_root:
            self.root.geometry(f"{wt.DEFAULT_W}x{wt.DEFAULT_H}")
        self.root.protocol("WM_DELETE_WINDOW", self._request_close)

        self._build()
        self.root.bind("<Configure>", self._on_configure)
        self._refresh_all()
        self._selection_notifications_enabled = True
        self._start_polling()
        if self._created_root:
            self.root.update_idletasks()
            wt.sync_windows_taskbar_icon(self.root)
            self.root.deiconify()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build(self):
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(2, weight=0)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=0, minsize=wt.SIDEBAR_W)
        self.root.grid_columnconfigure(2, weight=0, minsize=wt.RIGHT_W)

        self.topbar = tk.Frame(self.root, bg=wt.APP_BG_2, height=wt.TOPBAR_H)
        self.topbar.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.topbar.grid_propagate(False)
        self._build_topbar(self.topbar)

        self.sidebar = tk.Frame(self.root, bg=wt.SIDEBAR_BG, width=wt.SIDEBAR_W)
        self.sidebar.grid(row=1, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self._build_sidebar(self.sidebar)

        self.center = tk.Frame(self.root, bg=wt.PANEL_BG)
        self.center.grid(row=1, column=1, sticky="nsew")
        self._build_center(self.center)

        self.right = tk.Frame(self.root, bg=wt.PANEL_BG, width=wt.RIGHT_W)
        self.right.grid(row=1, column=2, sticky="nsew")
        self.right.grid_propagate(False)
        self._build_right(self.right)

        self.statusbar = tk.Frame(self.root, bg=wt.APP_BG_2, height=wt.STATUSBAR_H)
        self.statusbar.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.statusbar.grid_propagate(False)
        self._build_statusbar(self.statusbar)

    def _build_topbar(self, parent):
        left = tk.Frame(parent, bg=wt.APP_BG_2)
        left.pack(side="left", padx=18, fill="y")
        self.top_title_var = tk.StringVar(value="未选择连接")
        self.top_sub_var = tk.StringVar(value="")
        tk.Label(left, textvariable=self.top_title_var, bg=wt.APP_BG_2,
                 fg=wt.TEXT, font=wt.HEADING_FONT).pack(anchor="w", pady=(6, 0))
        tk.Label(left, textvariable=self.top_sub_var, bg=wt.APP_BG_2,
                 fg=wt.TEXT_MUTED, font=wt.SMALL_FONT).pack(anchor="w")

        right = tk.Frame(parent, bg=wt.APP_BG_2)
        right.pack(side="right", padx=14, fill="y", pady=10)
        self.action_btn = ttk.Button(
            right, text="启动", style="Accent.TButton",
            command=self._on_primary_action,
        )
        self.action_btn.pack(side="left", padx=(0, 8))
        self.delete_btn = ttk.Button(right, text="删除", style="Danger.TButton",
                                     command=self._on_delete)
        self.delete_btn.pack(side="left")

    def _build_sidebar(self, parent):
        tk.Label(parent, text=wt.APP_DISPLAY_NAME, bg=wt.SIDEBAR_BG, fg=wt.TEXT,
                 font=wt.HEADING_FONT).pack(anchor="w", padx=18,
                                             pady=(16, 1))
        tk.Label(parent, text="工作区", bg=wt.SIDEBAR_BG, fg=wt.TEXT_MUTED,
                 font=wt.TINY_FONT).pack(anchor="w", padx=18)
        self.new_btn = ttk.Button(parent, text="+  新建连接",
                                  style="Accent.TButton",
                                  command=self._on_new_connection)
        self.new_btn.pack(fill="x", padx=14, pady=(14, 12))

        tk.Label(parent, text="连接列表", bg=wt.SIDEBAR_BG, fg=wt.TEXT_MUTED,
                 font=wt.TINY_FONT).pack(anchor="w", padx=18, pady=(4, 2))

        list_host = tk.Frame(parent, bg=wt.SIDEBAR_BG)
        list_host.pack(fill="both", expand=True, padx=10, pady=(2, 6))
        self.sessions_canvas = tk.Canvas(list_host, bg=wt.SIDEBAR_BG,
                                         highlightthickness=0, borderwidth=0,
                                         width=50, height=50)
        self.sessions_canvas.pack(side="left", fill="both", expand=True)
        self.sessions_scroll = AutoHideScrollbar(self.sessions_canvas, list_host)
        self.sessions_scroll.place_over()
        self.sessions_inner = tk.Frame(self.sessions_canvas, bg=wt.SIDEBAR_BG)
        self.sessions_inner.grid_columnconfigure(0, weight=1)
        self.sessions_window = self.sessions_canvas.create_window(
            (0, 0), window=self.sessions_inner, anchor="nw")
        self.sessions_inner.bind("<Configure>", self._on_sessions_inner)
        self.sessions_canvas.bind("<Configure>", self._on_sessions_canvas)
        for w in (self.sessions_canvas, self.sessions_inner, list_host):
            w.bind("<MouseWheel>", self._on_sessions_wheel, add="+")
            w.bind("<Button-4>", self._on_sessions_wheel, add="+")
            w.bind("<Button-5>", self._on_sessions_wheel, add="+")
        self.session_items: dict[str, SessionItem] = {}
        self._session_order: list[str] = []

        bottom = tk.Frame(parent, bg=wt.SIDEBAR_BG)
        bottom.pack(side="bottom", fill="x", padx=12, pady=(4, 10))
        self.settings_row = tk.Frame(bottom, bg=wt.SIDEBAR_BG, height=34,
                                     cursor="hand2")
        self.settings_row.pack(fill="x")
        self.settings_row.pack_propagate(False)
        gear = tk.Label(self.settings_row, text=chr(0x2699), bg=wt.SIDEBAR_BG,
                        fg=wt.TEXT_SECONDARY, font=wt.BODY_FONT)
        gear.pack(side="left", padx=(6, 8))
        gear_text = tk.Label(self.settings_row, text="设置", bg=wt.SIDEBAR_BG,
                             fg=wt.TEXT_SECONDARY, font=wt.SMALL_FONT)
        gear_text.pack(side="left")
        self._settings_widgets = (self.settings_row, gear, gear_text)
        for w in self._settings_widgets:
            w.bind("<Button-1>", lambda _e: self._on_settings())
            w.bind("<Enter>", lambda _e: self._settings_hover(True))
            w.bind("<Leave>", lambda _e: self._settings_hover(False))
        self.version_label = tk.Label(
            bottom, text=wt.APP_VERSION_LABEL, bg=wt.SIDEBAR_BG,
            fg=wt.TEXT_MUTED, font=wt.TINY_FONT, anchor="w",
        )
        self.version_label.pack(fill="x", padx=6, pady=(3, 0))

    def _build_center(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_rowconfigure(3, weight=1)

        header = tk.Frame(parent, bg=wt.PANEL_BG)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 6))
        title_row = tk.Frame(header, bg=wt.PANEL_BG)
        title_row.pack(fill="x")
        tk.Label(title_row, text="桥接活动", bg=wt.PANEL_BG, fg=wt.TEXT,
                 font=wt.HEADING_FONT).pack(side="left")
        self.live_var = tk.StringVar(value="实时")
        self.live_lbl = tk.Label(title_row, textvariable=self.live_var,
                                 bg=wt.GREEN_SOFT, fg=wt.GREEN, font=wt.TINY_FONT,
                                 padx=8, pady=2)
        self.live_lbl.pack(side="left", padx=(10, 0))
        actions = tk.Frame(header, bg=wt.PANEL_BG)
        actions.pack(fill="x", pady=(5, 0))
        for column in range(3):
            actions.grid_columnconfigure(column, weight=1, uniform="stream")
        self.stream_buttons = []
        for column, (txt, cmd) in enumerate((
                ("暂停显示", self._toggle_pause),
                ("清空", self._clear_log),
                ("复制", self._copy_log))):
            b = ttk.Button(actions, text=txt, command=cmd)
            b.grid(row=0, column=column, sticky="ew",
                   padx=((0 if column == 0 else 3),
                         (0 if column == 2 else 3)))
            self.stream_buttons.append(b)
            if cmd == self._toggle_pause:
                self.pause_btn = b

        act_host = tk.Frame(parent, bg=wt.PANEL_BG)
        act_host.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        act_host.grid_columnconfigure(0, weight=1)
        act_host.grid_rowconfigure(0, weight=1)
        self.activity_text = tk.Text(act_host, bg=wt.CARD_BG, fg=wt.TEXT,
                                     font=wt.MONO_SMALL, wrap="none",
                                     state="disabled", borderwidth=0,
                                     highlightthickness=1,
                                     highlightbackground=wt.CARD_BORDER)
        self.activity_text.grid(row=0, column=0, sticky="nsew")
        self.activity_scroll = AutoHideScrollbar(self.activity_text, act_host)
        self.activity_scroll.place_over()
        self._configure_text_tags(self.activity_text)

        log_header = tk.Frame(parent, bg=wt.PANEL_BG)
        log_header.grid(row=2, column=0, sticky="ew", padx=18, pady=(2, 6))
        tk.Label(log_header, text="串口输出 / 日志", bg=wt.PANEL_BG,
                 fg=wt.TEXT_MUTED, font=wt.TINY_FONT).pack(side="left")

        log_host = tk.Frame(parent, bg=wt.PANEL_BG)
        log_host.grid(row=3, column=0, sticky="nsew", padx=18, pady=(0, 14))
        log_host.grid_columnconfigure(0, weight=1)
        log_host.grid_rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_host, bg=wt.LOG_BG, fg=wt.LOG_TEXT,
                                font=wt.MONO_FONT, wrap="none", state="disabled",
                                borderwidth=0, highlightthickness=1,
                                highlightbackground=wt.LOG_BORDER, insertbackground=wt.LOG_TEXT)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_scroll = AutoHideScrollbar(self.log_text, log_host)
        self.log_scroll.place_over()

    def _build_right(self, parent):
        self.right_canvas = tk.Canvas(
            parent, bg=wt.PANEL_BG, highlightthickness=0, borderwidth=0,
            width=wt.RIGHT_W,
        )
        self.right_canvas.pack(fill="both", expand=True)
        self.right_scroll = AutoHideScrollbar(self.right_canvas, parent)
        self.right_scroll.place_over()
        self.right_inner = tk.Frame(self.right_canvas, bg=wt.PANEL_BG)
        self.right_window = self.right_canvas.create_window(
            (0, 0), window=self.right_inner, anchor="nw"
        )
        self.right_inner.bind("<Configure>", self._on_right_inner)
        self.right_canvas.bind("<Configure>", self._on_right_canvas)
        content = self.right_inner

        tk.Label(content, text="连接信息", bg=wt.PANEL_BG, fg=wt.TEXT,
                 font=wt.HEADING_FONT).pack(anchor="w", padx=18, pady=(16, 2))
        tk.Label(content, text="当前连接详情", bg=wt.PANEL_BG,
                 fg=wt.TEXT_MUTED, font=wt.SMALL_FONT).pack(anchor="w", padx=18)

        card = tk.Frame(content, bg=wt.CARD_BG, padx=14, pady=12)
        card.pack(fill="x", padx=14, pady=(12, 10))
        self.detail_vars = {}
        for i, key in enumerate(("serial", "baud", "tcp", "clients")):
            self._detail_row(card, i, key)

        tk.Label(content, text="最近错误", bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY,
                 font=wt.SMALL_FONT).pack(anchor="w", padx=18, pady=(8, 2))
        self.error_var = tk.StringVar(value="")
        self.error_lbl = tk.Label(content, textvariable=self.error_var,
                                  bg=wt.CARD_BG, fg=wt.RED, font=wt.SMALL_FONT,
                                  anchor="w", justify="left", wraplength=230)
        self.error_lbl.pack(fill="x", padx=18, pady=(0, 8))

        tk.Label(content, text="快捷操作", bg=wt.PANEL_BG, fg=wt.TEXT,
                 font=wt.SECTION_FONT).pack(anchor="w", padx=18, pady=(10, 4))
        self.quick_edit_btn = ttk.Button(content, text="编辑连接",
                                         command=self._on_edit)
        self.quick_edit_btn.pack(fill="x", padx=14, pady=3)
        self.quick_telnet_btn = ttk.Button(content, text="打开内置 Telnet",
                                           command=self._on_telnet)
        self.quick_telnet_btn.pack(fill="x", padx=14, pady=3)
        self.quick_copy_btn = ttk.Button(content, text="复制 Telnet 命令",
                                         command=self._copy_telnet_command)
        self.quick_copy_btn.pack(fill="x", padx=14, pady=3)
        self.quick_refresh_btn = ttk.Button(content, text="刷新",
                                            command=self._refresh_all)
        self.quick_refresh_btn.pack(fill="x", padx=14, pady=3)
        self._bind_right_wheel(content)

    def _detail_row(self, parent, row, key):
        labels = {"serial": "串口", "baud": "波特率",
                  "tcp": "TCP 监听地址", "clients": "客户端"}
        tk.Label(parent, text=labels[key], bg=wt.CARD_BG, fg=wt.TEXT_MUTED,
                 font=wt.TINY_FONT).grid(row=row * 2, column=0, sticky="w",
                                         pady=(4, 2))
        var = tk.StringVar(value="—")
        self.detail_vars[key] = var
        tk.Label(parent, textvariable=var, bg=wt.CARD_BG, fg=wt.TEXT,
                 font=wt.MONO_SMALL, anchor="w").grid(row=row * 2 + 1, column=0,
                                                      sticky="ew", pady=(0, 6))

    def _build_statusbar(self, parent):
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(parent, textvariable=self.status_var, bg=wt.APP_BG_2,
                 fg=wt.TEXT_MUTED, font=wt.TINY_FONT).pack(side="left",
                                                           padx=16, pady=6)
        self.status_summary_var = tk.StringVar(value="")
        tk.Label(parent, textvariable=self.status_summary_var, bg=wt.APP_BG_2,
                 fg=wt.TEXT_MUTED, font=wt.TINY_FONT).pack(side="right",
                                                           padx=16, pady=6)

    # ------------------------------------------------------------------
    # text helpers
    # ------------------------------------------------------------------
    def _configure_text_tags(self, widget):
        widget.tag_configure("run", foreground=wt.GREEN)
        widget.tag_configure("warn", foreground=wt.AMBER)
        widget.tag_configure("error", foreground=wt.RED)
        widget.tag_configure("info", foreground=wt.BLUE)
        widget.tag_configure("muted", foreground=wt.TEXT_MUTED)

    def _event_color(self, kind: str) -> str:
        k = (kind or "").lower()
        if "error" in k or k in ("start_failed", "failed", "errored"):
            return "error"
        if "warn" in k or "starting" in k or "stopping" in k or "stop" in k:
            return "warn"
        if k in ("started", "running", "client_connected", "ready"):
            return "run"
        return "info"

    # ------------------------------------------------------------------
    # polling / refresh
    # ------------------------------------------------------------------
    def _start_polling(self):
        if self._poll_after is None:
            self._poll_after = self.root.after(wt.POLL_MS, self._poll)

    def _cancel_polling(self):
        if self._poll_after is not None:
            try:
                self.root.after_cancel(self._poll_after)
            except tk.TclError:
                pass
            self._poll_after = None
        for attr in ("_activity_after", "_log_after"):
            a = getattr(self, attr, None)
            if a is not None:
                try:
                    self.root.after_cancel(a)
                except tk.TclError:
                    pass
                setattr(self, attr, None)

    def _poll(self):
        self._poll_after = None
        self._drain_events()
        self._refresh_sessions()
        if self.root.winfo_exists():
            self._start_polling()

    def _drain_events(self):
        try:
            events = self._service.drain_events()
        except Exception as exc:
            self._service_error(exc)
            return
        for ev in events or []:
            self._record_event(ev)
        if events:
            self._render_selected()

    def _record_event(self, ev):
        sid = getattr(ev, "session_id", None)
        if not sid:
            return
        ts = getattr(ev, "timestamp", None) or time.time()
        kind = getattr(ev, "kind", "event") or "event"
        msg = getattr(ev, "message", "") or ""
        color = self._event_color(kind)

        act = self._activity.setdefault(sid, [])
        act.append((float(ts), kind, msg, color))
        if len(act) > wt.MAX_ACTIVITY_ROWS:
            del act[:len(act) - wt.MAX_ACTIVITY_ROWS]

        if kind in ("log", "log_message", "serial_output"):
            log = self._logs.setdefault(sid, [])
            log.append((float(ts), msg))
            if len(log) > wt.MAX_LOG_LINES:
                del log[:len(log) - wt.MAX_LOG_LINES]

    def _refresh_sessions(self):
        try:
            snaps = self._service.list_sessions()
        except Exception as exc:
            self._service_error(exc)
            return
        order = [s.session_id for s in snaps]
        self._snapshots = {s.session_id: s for s in snaps}
        previous = self._selected_id
        if self._selected_id is not None and self._selected_id not in self._snapshots:
            self._selected_id = None
        if self._selected_id is None and order:
            self._selected_id = order[0]
        self._render_session_list(order)
        self._render_selected()
        if self._selected_id != previous:
            self._notify_selection_change()

    def _render_session_list(self, order):
        layout_changed = order != self._session_order
        for sid in list(self.session_items):
            if sid not in order:
                self.session_items.pop(sid).destroy()
                layout_changed = True
        for row, sid in enumerate(order):
            item = self.session_items.get(sid)
            if item is None:
                item = SessionItem(self.sessions_inner, self._select)
                item.bind_sid(sid)
                item.bind_wheel(self._on_sessions_wheel)
                self.session_items[sid] = item
                layout_changed = True
            snap = self._snapshots[sid]
            item.update(snap, sid == self._selected_id)
        if layout_changed:
            for row, sid in enumerate(order):
                self.session_items[sid].grid(row=row, column=0, sticky="ew")
            self._session_order = list(order)

    def _render_selected(self, render_streams=None):
        if render_streams is None:
            render_streams = not self._paused
        self._update_top(self._selected_id)
        self._update_right(self._selected_id)
        self._update_controls(self._selected_id)
        if render_streams:
            self._render_activity(self._selected_id)
            self._render_log(self._selected_id)
        self._update_statusbar()

    def _update_top(self, sid):
        if not sid or sid not in self._snapshots:
            self.top_title_var.set("未选择连接")
            self.top_sub_var.set("新建或选择一个连接以开始使用")
            return
        snap = self._snapshots[sid]
        state = wt.status_state(snap)
        self.top_title_var.set(snap.name or sid)
        label, accent, _bg = wt.STATE_COLORS.get(state, wt.STATE_COLORS["stopped"])
        self.top_sub_var.set(f"{label}  ·  客户端 {snap.client_count}")

    def _update_right(self, sid):
        if not sid or sid not in self._snapshots:
            for v in self.detail_vars.values():
                v.set("—")
            self.error_var.set("")
            return
        snap = self._snapshots[sid]
        self.detail_vars["serial"].set(snap.com_port or "—")
        self.detail_vars["baud"].set(str(snap.baud_rate))
        self.detail_vars["tcp"].set(f"0.0.0.0:{snap.tcp_port}")
        self.detail_vars["clients"].set(str(snap.client_count))
        self.error_var.set(snap.last_error or "")

    def _update_controls(self, sid):
        has = sid is not None and sid in self._snapshots
        telnet_ok = has and bool(self._snapshots[sid].running)
        state = wt.status_state(self._snapshots[sid]) if has else "stopped"
        if not has:
            self.action_btn.configure(text="启动", style="Accent.TButton")
            self.action_btn.state(["disabled"])
            self.delete_btn.state(["disabled"])
            self.quick_edit_btn.state(["disabled"])
            self.quick_telnet_btn.state(["disabled"])
            return
        if state == "running":
            self.action_btn.configure(text="停止", style="TButton")
            self.action_btn.state(["!disabled"])
        elif state == "starting":
            self.action_btn.configure(text="启动中…", style="Accent.TButton")
            self.action_btn.state(["disabled"])
        elif state == "stopping":
            self.action_btn.configure(text="停止中…", style="TButton")
            self.action_btn.state(["disabled"])
        else:
            self.action_btn.configure(text="启动", style="Accent.TButton")
            self.action_btn.state(["!disabled"])
        self.delete_btn.state(["!disabled"])
        edit_disabled = state in ("starting", "running", "stopping")
        self.quick_edit_btn.state(["disabled"] if edit_disabled else ["!disabled"])
        self.quick_telnet_btn.state(["disabled"] if not telnet_ok else ["!disabled"])

    def _render_activity(self, sid):
        self.activity_text.configure(state="normal")
        self.activity_text.delete("1.0", "end")
        if not sid or sid not in self._activity:
            self.activity_text.configure(state="disabled")
            return
        for ts, kind, msg, color in self._activity[sid]:
            line = f"{time.strftime('%H:%M:%S', time.localtime(ts))}  {msg}"
            self.activity_text.insert("end", line + "\n", color)
        self.activity_text.see("end")
        self.activity_text.configure(state="disabled")

    def _render_log(self, sid):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        if not sid or sid not in self._logs:
            self.log_text.configure(state="disabled")
            return
        for ts, msg in self._logs[sid]:
            self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _update_statusbar(self):
        sid = self._selected_id
        if not sid or sid not in self._snapshots:
            self.status_var.set("就绪")
            self.status_summary_var.set("未选择连接")
            return
        snap = self._snapshots[sid]
        self.status_summary_var.set(
            f"{snap.com_port}  •  {snap.baud_rate}  •  TCP {snap.tcp_port}")

    # ------------------------------------------------------------------
    # user actions (service routing; every call carries session_id)
    # ------------------------------------------------------------------
    def _select(self, sid):
        if sid != self._selected_id:
            self._selected_id = sid
            self._render_selected(render_streams=True)
            self._notify_selection_change()

    def _notify_selection_change(self):
        if (self._selection_notifications_enabled and
                self._on_selection_change is not None):
            self._on_selection_change(self._selected_id)

    def _on_new_connection(self):
        created = {"sid": None}

        def submit(config):
            created["sid"] = self._service.create_session(config)

        cfg = show_session_dialog(
            self.root, self._service, on_submit=submit,
        )
        if cfg is None:
            return
        sid = created["sid"]
        if sid is None:
            return
        previous = self._selected_id
        self._selected_id = sid
        self._refresh_sessions()
        if self._selected_id != previous:
            self._notify_selection_change()

    def _create_session(self, config) -> str | None:
        try:
            sid = self._service.create_session(config)
        except Exception as exc:
            self._service_error(exc)
            return None
        previous = self._selected_id
        self._selected_id = sid
        self._refresh_sessions()
        if self._selected_id != previous:
            self._notify_selection_change()
        return sid

    def _on_primary_action(self):
        sid = self._selected_id
        if not sid or sid not in self._snapshots:
            return
        state = wt.status_state(self._snapshots[sid])
        if state == "running":
            self._stop_session(sid)
        elif state in ("stopped", "error"):
            self._start_session(sid)

    def _start_session(self, sid):
        if not sid:
            return
        try:
            self._service.start_session(sid)
        except TcpPortUnavailableError as exc:
            self._service_error(exc)
            show_tcp_port_in_use(self.root, exc.port, exc.suggested_port)
        except Exception as exc:
            self._service_error(exc)
        self._refresh_sessions()

    def _stop_session(self, sid):
        if not sid:
            return
        try:
            self._service.stop_session(sid)
        except Exception as exc:
            self._service_error(exc)
        self._refresh_sessions()

    def _on_delete(self):
        sid = self._selected_id
        if not sid:
            return
        snap = self._snapshots.get(sid)
        name = snap.name if snap else sid
        if show_confirm_delete(self.root, name, sid):
            self._delete_session(sid)

    def _on_edit(self):
        sid = self._selected_id
        if not sid or sid not in self._snapshots:
            return
        config = config_from_snapshot(self._snapshots[sid])

        def submit(edited_config):
            self._service.update_session(sid, edited_config)

        edited = show_session_dialog(
            self.root,
            self._service,
            config=config,
            title="编辑连接",
            on_submit=submit,
        )
        if edited is None:
            return
        self._refresh_sessions()

    def _delete_session(self, sid):
        if not sid:
            return
        try:
            self._service.delete_session(sid)
        except Exception as exc:
            self._service_error(exc)
        self._refresh_sessions()

    def _on_telnet(self):
        self._telnet(self._selected_id)

    def _telnet(self, sid):
        if not sid:
            return
        # only report intent; the terminal instance is owned by TASK-009.
        if self._on_open_terminal:
            self._on_open_terminal(sid)

    def _request_close(self):
        if self._close_dispatching:
            return
        self._close_dispatching = True
        try:
            # The UI only reports the intent; the lifecycle owner decides
            # whether this attempt exits, minimizes, or is cancelled.
            if self._on_close_request:
                self._on_close_request()
        finally:
            self._close_dispatching = False

    def _toggle_pause(self):
        self._paused = not self._paused
        self.live_var.set("已暂停" if self._paused else "实时")
        self.pause_btn.configure(text="继续显示" if self._paused else "暂停显示")
        self.live_lbl.configure(bg=wt.AMBER_SOFT if self._paused else wt.GREEN_SOFT,
                                fg=wt.AMBER if self._paused else wt.GREEN)
        if not self._paused:
            self._render_selected(render_streams=True)

    def _clear_log(self):
        sid = self._selected_id
        if sid and sid in self._logs:
            self._logs[sid] = []
        self._render_log(sid)

    def _copy_log(self):
        sid = self._selected_id
        text = ""
        if sid and sid in self._logs:
            text = "".join(m + "\n" for _, m in self._logs[sid])
        elif sid and sid in self._activity:
            text = "\n".join(m for _, _, m, _ in self._activity[sid])
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass

    def _copy_telnet_command(self):
        sid = self._selected_id
        if not sid or sid not in self._snapshots:
            return
        snap = self._snapshots[sid]
        command = f"telnet localhost {snap.tcp_port}"
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(command)
            self.status_var.set(f"已复制：{command}")
        except tk.TclError:
            pass

    def _service_error(self, exc):
        self.status_var.set(f"服务错误：{exc}")
        self.error_var.set(str(exc))
        self._render_log_error(str(exc))

    def _render_log_error(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[服务错误] {message}\n", "error")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_all(self):
        self._drain_events()
        self._refresh_sessions()

    # ------------------------------------------------------------------
    # scroll helpers + collapse
    # ------------------------------------------------------------------
    def _on_sessions_wheel(self, event=None):
        """Wheel over the rail (including session rows) scrolls the list."""
        delta = getattr(event, "delta", 0) or 0
        num = getattr(event, "num", None)
        if num == 4:
            steps = -1
        elif num == 5:
            steps = 1
        else:
            steps = -int(delta / 120)
            if steps == 0 and delta:
                steps = -1 if delta > 0 else 1
        try:
            self.sessions_canvas.yview_scroll(steps, "units")
        except tk.TclError:
            return "break"
        bar = getattr(self, "sessions_scroll", None)
        if bar is not None:
            try:
                bar._reveal()
            except Exception:  # noqa: BLE001
                pass
        return "break"

    def _on_sessions_inner(self, _e=None):
        self.sessions_canvas.configure(
            scrollregion=self.sessions_canvas.bbox("all") or (0, 0, 0, 0))

    def _on_sessions_canvas(self, event):
        self.sessions_canvas.itemconfigure(self.sessions_window,
                                           width=max(1, event.width))

    def _bind_right_wheel(self, widget):
        """Route wheel events from every detail child to the detail canvas."""
        for event_name in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(event_name, self._on_right_wheel, add="+")
        for child in widget.winfo_children():
            self._bind_right_wheel(child)

    def _on_right_wheel(self, event=None):
        delta = getattr(event, "delta", 0) or 0
        num = getattr(event, "num", None)
        if num == 4:
            steps = -1
        elif num == 5:
            steps = 1
        else:
            steps = -int(delta / 120)
            if steps == 0 and delta:
                steps = -1 if delta > 0 else 1
        try:
            self.right_canvas.yview_scroll(steps, "units")
        except tk.TclError:
            return "break"
        try:
            self.right_scroll._reveal()
        except (AttributeError, tk.TclError):
            pass
        return "break"

    def _on_right_inner(self, _event=None):
        self.right_canvas.configure(
            scrollregion=self.right_canvas.bbox("all") or (0, 0, 0, 0)
        )

    def _on_right_canvas(self, event):
        self.right_canvas.itemconfigure(self.right_window,
                                        width=max(1, event.width))

    def _on_configure(self, event):
        if event.widget is not self.root:
            return
        self._sync_collapse()

    def _sync_collapse(self):
        """Decide and apply right-panel collapse from the current width."""
        w = self.root.winfo_width()
        collapsed = w < self.collapse_threshold
        if collapsed != self._right_collapsed:
            self._right_collapsed = collapsed
            self._apply_right_collapse(collapsed)

    def _apply_right_collapse(self, collapsed):
        if collapsed:
            self.right.grid_remove()
            self.root.grid_columnconfigure(2, weight=0, minsize=0)
        else:
            self.right.grid()
            self.root.grid_columnconfigure(2, weight=0, minsize=wt.RIGHT_W)

    # ------------------------------------------------------------------
    # Settings / atomic theme rebuild
    # ------------------------------------------------------------------
    def _settings_hover(self, on):
        bg = wt.SIDEBAR_HOVER if on else wt.SIDEBAR_BG
        for w in getattr(self, "_settings_widgets", ()):
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass

    def _on_settings(self):
        try:
            result = show_settings_dialog(self.root, self._prefs,
                                          self._apply_preferences)
        except Exception as exc:  # noqa: BLE001
            self._service_error(exc)
            return
        if result is not None:
            self._prefs = result

    def _apply_preferences(self, prefs) -> bool:
        """Validate, persist via the host callback, then rebuild atomically."""
        clean = UiPreferences(
            theme_mode=wt.normalize_theme(prefs.theme_mode),
            accent_color=wt.normalize_accent(prefs.accent_color),
            close_action=(prefs.close_action if prefs.close_action in
                          ("ask", "minimize_to_tray", "exit") else "ask"),
            traffic_log_enabled=bool(prefs.traffic_log_enabled),
            traffic_log_directory=str(prefs.traffic_log_directory or "").strip(),
            **{
                name: wt.normalize_font_size(
                    name, getattr(prefs, name, default)
                )
                for name, default in wt.FONT_SIZE_DEFAULTS.items()
            },
        )
        if self._on_preferences_change is not None:
            try:
                ok = bool(self._on_preferences_change(clean))
            except Exception:  # noqa: BLE001 - keep the old theme on failure
                ok = False
            if not ok:
                return False
        self._prefs = clean
        wt.activate_theme(
            clean.theme_mode,
            clean.accent_color,
            clean.title_font_size,
            clean.ui_font_size,
            clean.helper_font_size,
            clean.terminal_font_size,
        )
        self._rebuild()
        return True

    def _cancel_scrollbars(self):
        for attr in ("sessions_scroll", "activity_scroll", "log_scroll",
                     "right_scroll"):
            bar = getattr(self, attr, None)
            if bar is not None:
                try:
                    bar._cancel_timers()
                except Exception:  # noqa: BLE001
                    pass

    def _rebuild(self):
        """Recreate the workbench frames with the active palette (no new root)."""
        selected = self._selected_id
        logs = self._logs
        activity = self._activity
        snapshots = self._snapshots
        paused = self._paused
        self._cancel_polling()
        self._cancel_scrollbars()
        for attr in ("topbar", "sidebar", "center", "right", "statusbar"):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
        self.session_items = {}
        self._right_collapsed = False
        for column in (0, 1, 2):
            self.root.grid_columnconfigure(column, weight=0, minsize=0)
        wt.apply_workbench_theme(self.root)
        self._build()
        self._logs = logs
        self._activity = activity
        self._snapshots = snapshots
        self._selected_id = selected
        self._paused = paused
        self.live_var.set("已暂停" if paused else "实时")
        self.pause_btn.configure(text="继续显示" if paused else "暂停显示")
        self.live_lbl.configure(
            bg=wt.AMBER_SOFT if paused else wt.GREEN_SOFT,
            fg=wt.AMBER if paused else wt.GREEN)
        self._refresh_sessions()
        self._render_selected(render_streams=True)
        self._sync_collapse()
        self._start_polling()

    # ------------------------------------------------------------------
    # teardown
    # ------------------------------------------------------------------
    def destroy(self):
        self._cancel_polling()
        self._cancel_scrollbars()
        wt.release_windows_taskbar_icon(self.root)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

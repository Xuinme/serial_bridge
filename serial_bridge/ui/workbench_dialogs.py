"""Workbench dialogs: session create/edit, Settings and confirm-delete.

SessionDialog validates values and may invoke a caller-owned submit callback;
it closes only after that callback succeeds.  WorkbenchWindow still owns the
actual create/update routing so every operation carries the correct session_id.
TASK-010 adds serial-port scanning, a themed Settings dialog (Light/Dark +
fixed accents) and shared parent-centred placement / fade-in for every modal.
"""

from __future__ import annotations

import os
import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, font as tkfont, ttk

from app.tcp_ports import TcpPortUnavailableError

from . import workbench_theme as wt
from .workbench_widgets import center_on_parent, fade_in_toplevel, Tooltip

BAUD_OPTIONS = ["1200", "2400", "4800", "9600", "19200", "38400", "57600",
                "115200", "230400", "460800", "921600"]
VALID_BAUD_RANGE = (1, 10000000)
NEWLINE_OPTIONS = ["crlf", "lf", "cr"]
PORT_OPTIONS = ["自动", "9999", "9000", "8080", "2000", "2323", "23"]
LISTEN_ADDRESS = "0.0.0.0"

THEME_MODES = ("light", "dark")
THEME_LABELS = {"light": "浅色", "dark": "深色"}

FONT_GROUPS = (
    ("title_font_size", "标题"),
    ("ui_font_size", "界面"),
    ("helper_font_size", "辅助文字"),
    ("terminal_font_size", "日志与终端"),
)

CLOSE_ACTION_OPTIONS = [
    ("ask", "每次询问"),
    ("minimize_to_tray", "最小化到系统托盘"),
    ("exit", "退出应用"),
]
CLOSE_ACTION_BY_LABEL = {label: value for value, label in CLOSE_ACTION_OPTIONS}
CLOSE_ACTION_LABEL = {value: label for value, label in CLOSE_ACTION_OPTIONS}


@dataclass
class SessionConfig:
    """Mirror of the frozen SessionConfig contract (TASK-007)."""

    session_id: str = ""
    name: str = ""
    com_port: str = ""
    baud_rate: int = 115200
    tcp_port: int = 9999
    newline_mode: str = "crlf"
    auto_reconnect: bool = False


@dataclass(frozen=True)
class UiPreferences:
    """Immutable workbench preference tuple exchanged with the app layer."""

    theme_mode: str = "dark"
    accent_color: str = "blue"
    close_action: str = "ask"
    title_font_size: int = wt.FONT_SIZE_DEFAULTS["title_font_size"]
    ui_font_size: int = wt.FONT_SIZE_DEFAULTS["ui_font_size"]
    helper_font_size: int = wt.FONT_SIZE_DEFAULTS["helper_font_size"]
    terminal_font_size: int = wt.FONT_SIZE_DEFAULTS["terminal_font_size"]
    traffic_log_enabled: bool = False
    traffic_log_directory: str = ""


def config_from_snapshot(snap) -> SessionConfig:
    """Build an editable SessionConfig from a session snapshot."""
    return SessionConfig(
        session_id=getattr(snap, "session_id", ""),
        name=getattr(snap, "name", ""),
        com_port=getattr(snap, "com_port", ""),
        baud_rate=getattr(snap, "baud_rate", 115200),
        tcp_port=getattr(snap, "tcp_port", 9999),
        newline_mode=getattr(snap, "newline_mode", "crlf"),
        auto_reconnect=bool(getattr(snap, "auto_reconnect", False)),
    )


def normalize_port(raw: str, mapping: dict | None = None) -> str:
    """Map a dropdown label to its device; normalise a typed COM name.

    Never upper-cases a whole device description: only a bare COMnn/LPTnn
    token is normalised, everything else is only trimmed.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if mapping and value in mapping:
        return str(mapping[value])
    if re.fullmatch(r"(?i)(com|lpt)[0-9]+", value):
        return value.upper()
    return value


class _Modal(tk.Toplevel):
    """Shared modal behaviour: theme, centering, fade-in, Escape handling."""

    def __init__(self, parent, title: str):
        super().__init__(parent)
        self._parent = parent
        self.title(title)
        self.configure(bg=wt.PANEL_BG)
        self.resizable(False, False)
        wt.apply_workbench_theme(self)
        wt.apply_window_icon(self)

    def _finish_modal(self) -> None:
        self.update_idletasks()
        center_on_parent(self, self._parent)
        self.transient(self._parent)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        fade_in_toplevel(self)


class SessionDialog(_Modal):
    """Create or edit a session; result holds a SessionConfig on OK."""

    def __init__(self, parent, service, config=None, title="新建连接",
                 on_submit=None):
        super().__init__(parent, title)
        self._service = service
        self._on_submit = on_submit
        self._editing = config is not None
        self.result = None
        self._existing_id = config.session_id if config else ""
        self._ports = []
        self._mapping = {}
        self._build(config)
        self._scan_ports()
        self._finish_modal()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _build(self, config):
        body = tk.Frame(self, bg=wt.PANEL_BG, padx=20, pady=18)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)

        tk.Label(body, text=("编辑连接" if config else "新建连接"),
                 bg=wt.PANEL_BG, fg=wt.TEXT, font=wt.HEADING_FONT).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

        self._field_label(body, 1, "显示名称")
        self.name_var = tk.StringVar(value=config.name if config else "")
        ttk.Entry(body, textvariable=self.name_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=(0, 10), ipady=2)

        self._field_label(body, 2, "串口")
        self.com_var = tk.StringVar(value=config.com_port if config else "")
        self.port_combo = ttk.Combobox(body, textvariable=self.com_var,
                                       state="normal", values=[], width=34)
        self.port_combo.grid(row=2, column=1, sticky="ew", pady=(0, 10), ipady=2)
        self.port_combo.configure(postcommand=self._scan_ports)
        refresh = tk.Label(body, text=chr(0x21BB), bg=wt.CARD_BG, fg=wt.TEXT,
                           font=(wt.FONT_FAMILY, wt.BODY_FONT[1] + 1), width=2, height=1,
                           cursor="hand2")
        refresh.grid(row=2, column=2, sticky="w", padx=(8, 0))
        refresh.configure(width=2, height=1)
        Tooltip(refresh, "刷新串口列表")
        refresh.bind("<Button-1>", lambda _e: self._scan_ports())
        self._refresh_btn = refresh

        self._field_label(body, 3, "波特率")
        self.baud_var = tk.StringVar(value=str(config.baud_rate) if config else "115200")
        ttk.Combobox(body, textvariable=self.baud_var, values=BAUD_OPTIONS,
                     state="normal").grid(row=3, column=1, columnspan=2,
                                         sticky="ew", pady=(0, 10), ipady=2)

        self._field_label(body, 4, "TCP 端口")
        self.port_var = tk.StringVar(value=str(config.tcp_port) if config else "自动")
        ttk.Combobox(body, textvariable=self.port_var, values=PORT_OPTIONS,
                     state="normal").grid(row=4, column=1, columnspan=2,
                                         sticky="ew", pady=(0, 10), ipady=2)

        self._field_label(body, 5, "监听地址")
        self.host_var = tk.StringVar(value=LISTEN_ADDRESS)
        ttk.Entry(body, textvariable=self.host_var, state="readonly").grid(
            row=5, column=1, columnspan=2, sticky="ew", pady=(0, 10), ipady=2)

        self._field_label(body, 6, "换行模式")
        self.newline_var = tk.StringVar(
            value=(config.newline_mode if config else "crlf").upper())
        ttk.Combobox(body, textvariable=self.newline_var,
                     values=[v.upper() for v in NEWLINE_OPTIONS],
                     state="readonly").grid(row=6, column=1, columnspan=2,
                                           sticky="ew", pady=(0, 10), ipady=2)

        self.auto_var = tk.BooleanVar(
            value=bool(config.auto_reconnect) if config else False)
        tk.Checkbutton(body, text="自动重连", variable=self.auto_var,
                       bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY, font=wt.SMALL_FONT,
                       selectcolor=wt.CARD_BG, activebackground=wt.PANEL_BG,
                       activeforeground=wt.TEXT, anchor="w",
                       highlightthickness=0).grid(row=7, column=1, columnspan=2,
                                                  sticky="w", pady=(0, 6))

        self.error_var = tk.StringVar(value="")
        self.error_label = tk.Label(body, textvariable=self.error_var,
                                    bg=wt.PANEL_BG, fg=wt.RED, font=wt.SMALL_FONT,
                                    anchor="w", justify="left", wraplength=320)
        self.error_label.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        self.error_label.grid_remove()

        buttons = tk.Frame(body, bg=wt.PANEL_BG)
        buttons.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=1)
        ttk.Button(buttons, text="取消", command=self._on_cancel).grid(
            row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(buttons, text=("保存" if config else "创建"),
                   style="Accent.TButton", command=self._on_ok).grid(
            row=0, column=1, sticky="ew")

    def _field_label(self, parent, row, text):
        tk.Label(parent, text=text, bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY,
                 font=wt.SMALL_FONT, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(0, 14), pady=(4, 10))

    def _used_ports(self) -> dict:
        used = {}
        try:
            for snap in self._service.list_sessions():
                if snap.session_id == self._existing_id:
                    continue
                if snap.com_port:
                    used[str(snap.com_port).casefold()] = snap.name or snap.session_id
        except Exception:
            pass
        return used

    def _scan_ports(self) -> None:
        try:
            ports = list(self._service.list_serial_ports() or [])
        except Exception as exc:
            self._show_error("无法扫描串口：%s" % exc)
            return
        self._ports = ports
        used = self._used_ports()
        mapping = {}
        display = []
        devices = set()
        for p in ports:
            device = str(getattr(p, "device", "") or "")
            label = str(getattr(p, "label", "") or device)
            devices.add(device.casefold())
            owner = used.get(device.casefold())
            shown = ("%s  （正由 %s 使用）" % (label, owner)) if owner else label
            display.append(shown)
            mapping[shown] = device
            mapping[label] = device
        current = (self.com_var.get() or "").strip()
        if current and current not in mapping and current.casefold() not in devices:
            synthetic = "%s - 当前未检测到" % current
            display.insert(0, synthetic)
            mapping[synthetic] = current
        self._mapping = mapping
        self.port_combo["values"] = display
        self._fit_port_combo(display)
        if not ports:
            self._show_error("未检测到可用串口。")
        else:
            self._clear_scan_error()

    def _fit_port_combo(self, values) -> None:
        """Show the longest scan label while keeping the modal on-screen."""
        longest = max((len(str(value)) for value in values), default=0)
        try:
            default_font = tkfont.nametofont("TkDefaultFont", root=self)
            char_width = max(default_font.measure("0"), 1)
            screen_width = max(self.winfo_vrootwidth(), self.winfo_screenwidth())
            max_chars = max(34, (screen_width - 220) // char_width)
        except tk.TclError:
            max_chars = 80
        target = min(max(34, longest + 3), max_chars)
        current = int(self.port_combo.cget("width") or 0)
        if target <= current:
            return
        self.port_combo.configure(width=target)
        if self.winfo_ismapped():
            self.update_idletasks()
            center_on_parent(self, self._parent)

    def _show_error(self, msg):
        self.error_var.set(msg)
        self.error_label.grid()
        try:
            center_on_parent(self, self._parent)
        except tk.TclError:
            pass

    def _clear_scan_error(self):
        current = self.error_var.get()
        if current.startswith("未检测到可用串口") or current.startswith("无法扫描串口"):
            self._clear_error()

    def _clear_error(self):
        self.error_var.set("")
        self.error_label.grid_remove()

    def _on_ok(self):
        self._clear_error()
        name = self.name_var.get().strip()
        if not name:
            self._show_error("请输入显示名称。")
            return
        device = normalize_port(self.com_var.get(), self._mapping)
        if not device:
            self._show_error("请选择或输入串口。")
            return
        owner = self._used_ports().get(device.casefold())
        if owner:
            self._show_port_conflict(device, owner)
            return
        try:
            baud = int(self.baud_var.get().strip())
        except ValueError:
            self._show_error("波特率必须是整数。")
            return
        if not (VALID_BAUD_RANGE[0] <= baud <= VALID_BAUD_RANGE[1]):
            self._show_error("波特率超出允许范围。")
            return
        port_raw = self.port_var.get().strip()
        if port_raw.lower() in ("auto", "自动", ""):
            try:
                tcp_port = self._service.next_tcp_port(9999)
            except Exception as exc:
                self._show_error("无法选择可用 TCP 端口：%s" % exc)
                return
        else:
            try:
                tcp_port = int(port_raw)
            except ValueError:
                self._show_error("TCP 端口必须是整数。")
                return
            if not (1 <= tcp_port <= 65535):
                self._show_error("TCP 端口必须在 1-65535 之间。")
                return
        newline = (self.newline_var.get() or "CRLF").strip().lower()
        if newline not in NEWLINE_OPTIONS:
            self._show_error("换行模式无效。")
            return
        config = SessionConfig(
            session_id=self._existing_id, name=name, com_port=device,
            baud_rate=baud, tcp_port=tcp_port, newline_mode=newline,
            auto_reconnect=bool(self.auto_var.get()))
        if self._on_submit is not None:
            try:
                self._on_submit(config)
            except TcpPortUnavailableError as exc:
                self._show_error("保存失败：%s" % exc)
                show_tcp_port_in_use(self, exc.port, exc.suggested_port)
                try:
                    self.grab_set()
                except tk.TclError:
                    pass
                return
            except Exception as exc:  # noqa: BLE001 - keep user input intact
                owner = self._used_ports().get(device.casefold())
                if owner:
                    self._show_port_conflict(device, owner)
                else:
                    self._show_error("保存失败：%s" % exc)
                return
        self.result = config
        self._close()

    def _show_port_conflict(self, device, owner):
        message = "串口 %s 正由连接“%s”使用，无法保存。" % (device, owner)
        self._show_error(message)
        show_port_in_use(self, device, owner)
        try:
            self.grab_set()
            self.port_combo.focus_set()
        except tk.TclError:
            pass

    def _on_cancel(self):
        self.result = None
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class PortInUseDialog(_Modal):
    """Centered warning shown when another configured session owns a COM."""

    def __init__(self, parent, device, owner):
        super().__init__(parent, "无法保存连接")
        body = tk.Frame(self, bg=wt.PANEL_BG, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(
            body, text="串口正在使用", bg=wt.PANEL_BG, fg=wt.TEXT,
            font=wt.HEADING_FONT,
        ).pack(anchor="w", pady=(0, 8))
        tk.Label(
            body,
            text="串口 %s 正由连接“%s”使用。\n请修改或删除原连接，或选择其他串口。" %
                 (device, owner),
            bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY, font=wt.SMALL_FONT,
            justify="left", wraplength=360,
        ).pack(anchor="w", pady=(0, 16))
        ttk.Button(body, text="知道了", style="Accent.TButton",
                   command=self._close).pack(fill="x")
        self._finish_modal()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())

    def _close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class TcpPortInUseDialog(_Modal):
    """Centered warning for a TCP listener conflict."""

    def __init__(self, parent, port, suggested_port=None):
        super().__init__(parent, "TCP 端口不可用")
        body = tk.Frame(self, bg=wt.PANEL_BG, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(
            body, text="TCP 端口已被占用", bg=wt.PANEL_BG, fg=wt.TEXT,
            font=wt.HEADING_FONT,
        ).pack(anchor="w", pady=(0, 8))
        detail = "TCP 端口 %s 已被其他程序占用，当前连接无法使用该端口。" % port
        if suggested_port is not None:
            detail += "\n建议改用 TCP 端口 %s。" % suggested_port
        tk.Label(
            body, text=detail, bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY,
            font=wt.SMALL_FONT, justify="left", wraplength=380,
        ).pack(anchor="w", pady=(0, 16))
        ttk.Button(body, text="知道了", style="Accent.TButton",
                   command=self._close).pack(fill="x")
        self._finish_modal()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())

    def _close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class SettingsDialog(_Modal):
    """Appearance (theme + accent) and window behaviour, applied atomically."""

    SWATCH_W = 28
    SWATCH_H = 18
    SWATCH_GAP = 8

    def __init__(self, parent, current, on_apply):
        super().__init__(parent, "设置")
        self._current = current
        self._on_apply = on_apply
        self.result = None
        self._mode = wt.normalize_theme(current.theme_mode)
        self._accent = wt.normalize_accent(current.accent_color)
        self._font_values = {
            name: wt.normalize_font_size(
                name, getattr(current, name, wt.FONT_SIZE_DEFAULTS[name])
            )
            for name, _label in FONT_GROUPS
        }
        self._font_vars = {}
        self._build()
        self._finish_modal()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _build(self):
        body = tk.Frame(self, bg=wt.PANEL_BG, padx=20, pady=18)
        body.pack(fill="both", expand=True)

        content = tk.Frame(body, bg=wt.PANEL_BG)
        content.pack(fill="both", expand=True)
        appearance = tk.Frame(content, bg=wt.PANEL_BG)
        appearance.pack(side="left", fill="both", expand=True, padx=(0, 24))
        behavior = tk.Frame(content, bg=wt.PANEL_BG)
        behavior.pack(side="left", fill="both", expand=True)

        tk.Label(appearance, text="外观", bg=wt.PANEL_BG, fg=wt.TEXT,
                 font=wt.HEADING_FONT).pack(anchor="w", pady=(0, 10))

        theme_row = tk.Frame(appearance, bg=wt.PANEL_BG)
        theme_row.pack(fill="x", pady=(0, 10))
        tk.Label(theme_row, text="主题", bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY,
                 font=wt.SMALL_FONT, width=10, anchor="w").pack(side="left")
        self._theme_labels = {}
        for mode in THEME_MODES:
            lbl = tk.Label(theme_row, text=THEME_LABELS[mode], bg=wt.PANEL_BG,
                           fg=wt.TEXT_MUTED, font=wt.BODY_FONT, padx=12, pady=3,
                           cursor="hand2", takefocus=1, highlightthickness=1,
                           highlightbackground=wt.PANEL_BG,
                           highlightcolor=wt.BLUE)
            lbl.pack(side="left", padx=(0, 6))
            lbl.bind("<Button-1>", lambda _e, m=mode: self._select_mode(m))
            lbl.bind("<Return>", lambda _e, m=mode: self._select_mode(m))
            lbl.bind("<space>", lambda _e, m=mode: self._select_mode(m))
            self._theme_labels[mode] = lbl

        accent_row = tk.Frame(appearance, bg=wt.PANEL_BG)
        accent_row.pack(fill="x", pady=(0, 14))
        tk.Label(accent_row, text="强调色", bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY,
                 font=wt.SMALL_FONT, width=10, anchor="w").pack(side="left")
        self._swatches = {}
        for name in wt.ACCENT_ORDER:
            sw = tk.Canvas(accent_row, width=self.SWATCH_W, height=self.SWATCH_H,
                           highlightthickness=1, highlightbackground=wt.PANEL_BG,
                           bg=wt.PANEL_BG, takefocus=1, cursor="hand2")
            sw.pack(side="left", padx=(0, self.SWATCH_GAP))
            sw.bind("<Button-1>", lambda _e, n=name: self._select_accent(n))
            sw.bind("<Return>", lambda _e, n=name: self._select_accent(n))
            sw.bind("<space>", lambda _e, n=name: self._select_accent(n))
            self._swatches[name] = sw

        font_header = tk.Frame(appearance, bg=wt.PANEL_BG)
        font_header.pack(fill="x", pady=(0, 6))
        tk.Label(font_header, text="字体大小", bg=wt.PANEL_BG, fg=wt.TEXT,
                 font=wt.SECTION_FONT).pack(side="left")
        ttk.Button(font_header, text="恢复默认", command=self._reset_fonts).pack(side="right")

        font_grid = tk.Frame(appearance, bg=wt.PANEL_BG)
        font_grid.pack(fill="x", pady=(0, 12))
        font_grid.grid_columnconfigure(1, weight=1)
        for row, (name, label) in enumerate(FONT_GROUPS):
            tk.Label(font_grid, text=label, bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY,
                     font=wt.SMALL_FONT, width=12, anchor="w").grid(
                         row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=str(self._font_values[name]))
            self._font_vars[name] = var
            low, high = wt.FONT_SIZE_RANGES[name]
            spin = ttk.Spinbox(font_grid, from_=low, to=high, increment=1,
                               textvariable=var, width=6, justify="center",
                               command=self._font_changed)
            spin.grid(row=row, column=1, sticky="w", padx=(6, 6), pady=2)
            spin.bind("<Return>", self._font_changed)
            spin.bind("<FocusOut>", self._font_changed)
            var.trace_add("write", lambda *_args: self._font_changed())
            tk.Label(font_grid, text="pt", bg=wt.PANEL_BG, fg=wt.TEXT_MUTED,
                     font=wt.TINY_FONT).grid(row=row, column=2, sticky="w")

        tk.Label(appearance, text="预览", bg=wt.PANEL_BG, fg=wt.TEXT_MUTED,
                 font=wt.TINY_FONT).pack(anchor="w")
        self._preview = tk.Frame(appearance, bg=wt.PANEL_BG, pady=6)
        self._preview.pack(fill="x", pady=(2, 14))
        self._preview_title = tk.Label(self._preview, text="标题预览",
                                       anchor="w", padx=10, pady=3)
        self._preview_title.pack(fill="x")
        preview_actions = tk.Frame(self._preview, bg=wt.PANEL_BG)
        preview_actions.pack(fill="x")
        self._preview_item = tk.Label(preview_actions, text="  界面文字  ",
                                      font=wt.BODY_FONT, anchor="w", padx=10, pady=4)
        self._preview_item.pack(side="left", fill="x", expand=True)
        self._preview_btn = tk.Label(preview_actions, text="主要按钮",
                                     font=(wt.FONT_FAMILY, wt.BODY_FONT[1], "bold"),
                                     padx=14, pady=5)
        self._preview_btn.pack(side="right")
        self._preview_helper = tk.Label(self._preview, text="辅助说明文字",
                                        anchor="w", padx=10, pady=2)
        self._preview_helper.pack(fill="x")
        self._preview_terminal = tk.Label(
            self._preview, text="telnet localhost 9999", anchor="w", padx=10, pady=2
        )
        self._preview_terminal.pack(fill="x")

        tk.Label(behavior, text="窗口行为", bg=wt.PANEL_BG, fg=wt.TEXT,
                 font=wt.HEADING_FONT).pack(anchor="w", pady=(0, 8))
        close_row = tk.Frame(behavior, bg=wt.PANEL_BG)
        close_row.pack(fill="x", pady=(0, 4))
        tk.Label(close_row, text="关闭窗口时", bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY,
                 font=wt.SMALL_FONT, width=10, anchor="w").pack(side="left")
        close_value = getattr(self._current, "close_action", "ask")
        self.close_var = tk.StringVar(value=CLOSE_ACTION_LABEL.get(close_value, "每次询问"))
        ttk.Combobox(close_row, textvariable=self.close_var,
                     values=[label for _, label in CLOSE_ACTION_OPTIONS],
                     state="readonly", width=26).pack(side="left")

        tk.Label(behavior, text="通信日志", bg=wt.PANEL_BG, fg=wt.TEXT,
                 font=wt.HEADING_FONT).pack(anchor="w", pady=(14, 8))
        self.traffic_log_enabled_var = tk.BooleanVar(
            value=bool(getattr(self._current, "traffic_log_enabled", False)))
        ttk.Checkbutton(
            behavior,
            text="启用串口原始通信日志",
            variable=self.traffic_log_enabled_var,
        ).pack(anchor="w", pady=(0, 8))

        directory_row = tk.Frame(behavior, bg=wt.PANEL_BG)
        directory_row.pack(fill="x")
        self.traffic_log_directory_var = tk.StringVar(
            value=str(getattr(self._current, "traffic_log_directory", "") or ""))
        self.traffic_log_directory_entry = ttk.Entry(
            directory_row, textvariable=self.traffic_log_directory_var, width=28)
        self.traffic_log_directory_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(directory_row, text="选择", width=7,
                   command=self._choose_log_directory).pack(side="left", padx=(6, 0))
        ttk.Button(directory_row, text="打开", width=7,
                   command=self._open_log_directory).pack(side="left", padx=(6, 0))
        tk.Label(
            behavior,
            text="日志包含原始命令、设备输出和可能的敏感信息，请妥善保管。",
            bg=wt.PANEL_BG,
            fg=wt.AMBER,
            font=wt.TINY_FONT,
            anchor="w",
            justify="left",
            wraplength=400,
        ).pack(fill="x", pady=(6, 0))

        tk.Label(behavior, text="关于", bg=wt.PANEL_BG, fg=wt.TEXT,
                 font=wt.HEADING_FONT).pack(anchor="w", pady=(18, 6))
        self.about_version_label = tk.Label(
            behavior, text=wt.APP_VERSION_LABEL, bg=wt.PANEL_BG,
            fg=wt.TEXT_SECONDARY, font=wt.SMALL_FONT, anchor="w",
        )
        self.about_version_label.pack(fill="x")

        self.error_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.error_var, bg=wt.PANEL_BG, fg=wt.RED,
                 font=wt.SMALL_FONT, anchor="w", justify="left",
                 wraplength=320).pack(fill="x", pady=(8, 0))

        buttons = tk.Frame(body, bg=wt.PANEL_BG)
        buttons.pack(fill="x", pady=(14, 0))
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=1)
        ttk.Button(buttons, text="取消", command=self._on_cancel).grid(
            row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(buttons, text="应用", style="Accent.TButton",
                   command=self._on_apply_clicked).grid(row=0, column=1, sticky="ew")
        self._render_selection()

    def _select_mode(self, mode):
        self._mode = wt.normalize_theme(mode)
        self._render_selection()

    def _select_accent(self, name):
        self._accent = wt.normalize_accent(name)
        self._render_selection()

    def _font_changed(self, _event=None):
        for name, _label in FONT_GROUPS:
            self._font_values[name] = wt.normalize_font_size(
                name, self._font_vars[name].get()
            )
        self._render_selection()

    def _reset_fonts(self):
        for name, _label in FONT_GROUPS:
            value = wt.FONT_SIZE_DEFAULTS[name]
            self._font_values[name] = value
            self._font_vars[name].set(str(value))
        self._render_selection()

    def _choose_log_directory(self):
        initial = self.traffic_log_directory_var.get().strip()
        chosen = filedialog.askdirectory(
            parent=self,
            initialdir=initial if initial and Path(initial).is_dir() else None,
            title="选择通信日志保存目录",
        )
        if chosen:
            self.traffic_log_directory_var.set(chosen)
            self.error_var.set("")

    def _open_log_directory(self):
        raw = self.traffic_log_directory_var.get().strip()
        if not raw:
            self.error_var.set("请先选择通信日志保存目录。")
            return
        try:
            path = Path(raw).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            startfile = getattr(os, "startfile", None)
            if startfile is None:
                raise OSError("当前系统不支持打开目录")
            startfile(str(path.resolve()))
            self.error_var.set("")
        except OSError as exc:
            self.error_var.set(f"无法打开日志目录：{exc}")

    def _render_selection(self):
        accent = wt.ACCENTS[self._accent]
        palette = wt.PALETTES[wt.normalize_theme(self._mode)]
        panel = palette["PANEL_BG"]
        text = palette["TEXT"]
        preview_fonts = wt.fonts_for(*self._font_values.values())
        for mode, lbl in self._theme_labels.items():
            selected = (mode == self._mode)
            lbl.configure(fg=accent if selected else text,
                          highlightbackground=accent if selected else panel,
                          font=(wt.FONT_FAMILY, preview_fonts["body"][1],
                                "bold" if selected else "normal"))
        for name, sw in self._swatches.items():
            selected = (name == self._accent)
            color = wt.ACCENTS[name]
            sw.configure(bg=panel,
                         highlightbackground=accent if selected else panel,
                         highlightthickness=2 if selected else 1)
            sw.delete("all")
            sw.create_rectangle(1, 1, self.SWATCH_W - 1, self.SWATCH_H - 1,
                                fill=color, outline="")
            if selected:
                sw.create_text(self.SWATCH_W / 2, self.SWATCH_H / 2,
                               text=chr(0x2713), fill="#FFFFFF",
                               font=(wt.FONT_FAMILY, wt.TINY_FONT[1], "bold"))
        self._preview.configure(bg=panel)
        self._preview_title.configure(bg=panel, fg=text,
                                      font=preview_fonts["title"])
        self._preview_item.configure(bg=accent, fg="#FFFFFF",
                                     font=preview_fonts["body"])
        self._preview_btn.configure(bg=accent, fg="#FFFFFF",
                                    font=(wt.FONT_FAMILY, preview_fonts["body"][1], "bold"))
        self._preview_helper.configure(bg=panel, fg=palette["TEXT_MUTED"],
                                       font=preview_fonts["small"])
        self._preview_terminal.configure(bg=palette["LOG_BG"],
                                         fg=palette["LOG_TEXT"],
                                         font=preview_fonts["mono"])

    def _on_apply_clicked(self):
        log_directory = self.traffic_log_directory_var.get().strip()
        if self.traffic_log_enabled_var.get() and not log_directory:
            self.error_var.set("启用通信日志前，请选择保存目录。")
            self.traffic_log_directory_entry.focus_set()
            return
        prefs = UiPreferences(
            theme_mode=self._mode,
            accent_color=self._accent,
            close_action=CLOSE_ACTION_BY_LABEL.get(self.close_var.get(), "ask"),
            traffic_log_enabled=bool(self.traffic_log_enabled_var.get()),
            traffic_log_directory=log_directory,
            **self._font_values,
        )
        try:
            ok = bool(self._on_apply(prefs)) if self._on_apply else True
        except Exception as exc:
            self.error_var.set(str(exc))
            return
        if ok:
            self.result = prefs
            self._close()
        else:
            self.error_var.set("设置保存失败。")

    def _on_cancel(self):
        self.result = None
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class ConfirmDeleteDialog(_Modal):
    """Confirm a session delete.  result is True only on explicit confirm."""

    def __init__(self, parent, name, session_id):
        super().__init__(parent, "删除连接")
        self.result = False
        body = tk.Frame(self, bg=wt.PANEL_BG, padx=20, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="确定删除这个连接吗？", bg=wt.PANEL_BG, fg=wt.TEXT,
                 font=wt.HEADING_FONT).pack(anchor="w", pady=(0, 8))
        tk.Label(body, text="%s  （会话 %s）" % (name, session_id),
                 bg=wt.PANEL_BG, fg=wt.TEXT_SECONDARY, font=wt.SMALL_FONT,
                 anchor="w").pack(anchor="w", pady=(0, 6))
        tk.Label(body, text="删除将停止该连接，并释放对应的串口和 TCP 资源。",
                 bg=wt.PANEL_BG, fg=wt.TEXT_MUTED, font=wt.SMALL_FONT, anchor="w",
                 wraplength=340, justify="left").pack(anchor="w", pady=(0, 14))
        buttons = tk.Frame(body, bg=wt.PANEL_BG)
        buttons.pack(fill="x")
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=1)
        ttk.Button(buttons, text="取消", command=self._on_cancel).grid(
            row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(buttons, text="删除", style="Danger.TButton",
                   command=self._on_ok).grid(row=0, column=1, sticky="ew")
        self._finish_modal()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _on_ok(self):
        self.result = True
        self._close()

    def _on_cancel(self):
        self.result = False
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


def show_session_dialog(parent, service, config=None, title="新建连接",
                        on_submit=None):
    """Blocking helper that returns the chosen SessionConfig or None."""
    dlg = SessionDialog(
        parent, service, config=config, title=title, on_submit=on_submit,
    )
    parent.wait_window(dlg)
    return dlg.result


def show_port_in_use(parent, device, owner):
    """Show the parent-centred occupied-COM warning."""
    dlg = PortInUseDialog(parent, device, owner)
    parent.wait_window(dlg)


def show_tcp_port_in_use(parent, port, suggested_port=None):
    """Show the parent-centred occupied-TCP warning."""
    dlg = TcpPortInUseDialog(parent, port, suggested_port)
    parent.wait_window(dlg)


def show_confirm_delete(parent, name, session_id):
    """Blocking helper that returns True only on confirm."""
    dlg = ConfirmDeleteDialog(parent, name, session_id)
    parent.wait_window(dlg)
    return dlg.result


def show_settings_dialog(parent, current, on_apply):
    """Blocking helper returning the applied UiPreferences or None."""
    dlg = SettingsDialog(parent, current, on_apply)
    parent.wait_window(dlg)
    return dlg.result

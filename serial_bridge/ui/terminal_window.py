"""Built-in telnet terminal popup.

There is no input field and no Send button.  The focused terminal text
area captures keyboard input and sends it to the bridge as a normal TCP
client.  Displayed text always comes from the bridge echo / COM output.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox

from app.telnet_client import TelnetClient

from . import workbench_theme as wt
from .ansi import AnsiParser
from .theme import (
    TERM_BG,
    TERM_BORDER,
    TERM_GREEN,
    TERM_MUTED,
    TERM_PANEL,
    TERM_TEXT,
    configure_terminal_tags,
)

MAX_TERMINAL_LINES = 8000


class TerminalWindow:
    """Pure terminal popup for the built-in TCP client."""

    def __init__(self, parent: tk.Tk, controller):
        self._parent = parent
        self._ctrl = controller
        self._client: TelnetClient | None = None
        self._win: tk.Toplevel | None = None
        self._text: tk.Text | None = None
        self._ansi = AnsiParser()
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._line_count = 0
        self._select_anchor: str | None = None
        self._title_label: tk.Label | None = None
        self._hint_label: tk.Label | None = None
        self._font_signature: tuple[int, ...] | None = None

    @property
    def is_open(self) -> bool:
        return self._win is not None and self._win.winfo_exists()

    def open(self):
        if self.is_open:
            # Existing window (possibly hidden/minimized to tray): restore it
            # without creating a second client connection or a new window.
            try:
                self._win.deiconify()
            except tk.TclError:
                pass
            self._win.lift()
            if self._text:
                self._text.focus_set()
            return
        if not self._ctrl.state.running:
            messagebox.showwarning("桥接未运行", "请先启动桥接。")
            return

        win = tk.Toplevel(self._parent)
        # TASK-010: the shell/title follows the active workbench theme; the
        # terminal body keeps its own near-black readable palette.
        wt.apply_workbench_theme(win)
        wt.apply_window_icon(win)
        win.title(f"内置 Telnet - localhost:{self._ctrl.config.tcp_port}")
        win.geometry("760x380")
        win.minsize(560, 300)
        win.configure(bg=wt.PANEL_BG)
        self._win = win

        self._build_ui()
        self._connect_client()
        self._drain_queue()
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.after(120, lambda: self._text.focus_set() if self._text else None)

    def hide(self):
        """Hide the terminal window (used when the app is minimized to tray).

        Only withdraws the Toplevel; does not disconnect the client or clear
        the terminal content.
        """
        if self._win and self._win.winfo_exists():
            try:
                self._win.withdraw()
            except tk.TclError:
                pass

    def close(self):
        client = self._client
        self._client = None
        if client:
            client.disconnect()
        self._ansi.reset()
        if self._win:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
        self._win = None
        self._text = None
        self._title_label = None
        self._hint_label = None
        self._font_signature = None

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        win = self._win
        outer = tk.Frame(win, bg=wt.PANEL_BG)
        outer.pack(fill="both", expand=True)

        title = tk.Frame(outer, bg=wt.PANEL_BG, height=50)
        title.pack(fill="x")
        title.pack_propagate(False)
        for i, col in enumerate(("#ff5f57", "#fbbf24", "#22c55e")):
            dot = tk.Canvas(title, width=16, height=16, bg=wt.PANEL_BG, highlightthickness=0)
            dot.place(x=22 + i * 24, y=17)
            dot.create_oval(1, 1, 15, 15, fill=col, outline="")
        self._title_label = tk.Label(
            title,
            text=f"内置 Telnet - localhost:{self._ctrl.config.tcp_port}",
            bg=wt.PANEL_BG,
            fg=wt.TEXT,
            font=wt.MONO_FONT,
        )
        self._title_label.place(x=96, y=16)

        self._text = tk.Text(
            outer,
            bg=TERM_BG,
            fg=TERM_TEXT,
            insertbackground=TERM_GREEN,
            font=wt.MONO_FONT,
            wrap="char",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=TERM_BORDER,
            highlightcolor=TERM_BORDER,
            padx=22,
            pady=20,
            undo=False,
            takefocus=True,
        )
        self._text.pack(fill="both", expand=True, padx=28, pady=(20, 12))
        configure_terminal_tags(self._text)
        self._text.tag_configure(
            "bold", font=(wt.FONT_MONO, wt.MONO_FONT[1], "bold")
        )
        self._text.tag_configure(
            "italic", font=(wt.FONT_MONO, wt.MONO_FONT[1], "italic")
        )
        self._text.bind("<KeyPress>", self._on_keypress)
        self._text.bind("<Control-c>", self._on_ctrl_c)
        self._text.bind("<Control-v>", self._on_paste)
        self._text.bind("<Button-1>", self._on_mouse_press)
        self._text.bind("<B1-Motion>", self._on_mouse_drag)
        self._text.bind("<ButtonRelease-1>", self._on_mouse_release)
        self._text.bind("<Double-Button-1>", self._on_mouse_press)
        self._text.bind("<Triple-Button-1>", self._on_mouse_press)
        self._text.bind("<Button-2>", self._on_mouse_focus)
        self._text.bind("<Button-3>", self._show_menu)

        self._menu = tk.Menu(outer, tearoff=False)
        self._menu.add_command(label="复制", command=self._copy)
        self._menu.add_command(label="粘贴", command=self._paste_clipboard)
        self._menu.add_command(label="清空", command=self._clear)
        self._menu.add_separator()
        self._menu.add_command(label="关闭", command=self.close)

        self._hint_label = tk.Label(
            outer,
            text="无需发送按钮，键盘输入会直接发送到 Telnet 连接。",
            bg=wt.PANEL_BG,
            fg=wt.TEXT_MUTED,
            font=wt.MONO_TINY,
        )
        self._hint_label.pack(anchor="w", padx=28, pady=(0, 14))
        self._apply_current_fonts()

    def _apply_current_fonts(self):
        sizes = wt.current_font_sizes()
        signature = tuple(sizes.values())
        if signature == self._font_signature:
            return
        self._font_signature = signature
        if self._title_label is not None:
            self._title_label.configure(font=wt.MONO_FONT)
        if self._hint_label is not None:
            self._hint_label.configure(font=wt.MONO_TINY)
        if self._text is not None:
            self._text.configure(font=wt.MONO_FONT)
            self._text.tag_configure(
                "bold", font=(wt.FONT_MONO, wt.MONO_FONT[1], "bold")
            )
            self._text.tag_configure(
                "italic", font=(wt.FONT_MONO, wt.MONO_FONT[1], "italic")
            )

    def _connect_client(self):
        port = self._ctrl.config.tcp_port
        self._append_plain(f"正在连接 localhost:{port}...\n", ("ansi_bright_black",))
        client = TelnetClient("127.0.0.1", port)
        client.on_connected = lambda: self._queue.put(("connected", None))
        client.on_disconnected = lambda reason: self._queue.put(("disconnected", reason))
        client.on_error = lambda msg: self._queue.put(("error", msg))
        client.on_data = lambda data: self._queue.put(("data", data))
        self._client = client
        if not client.connect():
            self._append_plain("连接失败。\n", ("ansi_red",))

    # ── Keyboard input ──────────────────────────────────────────────

    def _on_keypress(self, event):
        client = self._client
        if not client or not client.connected:
            return "break"

        keysym = event.keysym
        if keysym in ("Return", "KP_Enter"):
            client.send(b"\r\n")
            return "break"
        if keysym == "BackSpace":
            client.send(b"\x08")
            return "break"
        if keysym == "Delete":
            client.send(b"\x1b[3~")
            return "break"
        if keysym == "Tab":
            client.send(b"\t")
            return "break"
        if keysym == "Escape":
            client.send(b"\x1b")
            return "break"
        if keysym in ("Up", "Down", "Right", "Left"):
            seq = {"Up": b"\x1b[A", "Down": b"\x1b[B", "Right": b"\x1b[C", "Left": b"\x1b[D"}
            client.send(seq[keysym])
            return "break"

        char = event.char
        if char and len(char) == 1 and ord(char) >= 32:
            client.send(char.encode("utf-8", errors="replace"))
        return "break"

    def _on_ctrl_c(self, _):
        if self._client and self._client.connected:
            self._client.send(b"\x03")
        return "break"

    def _on_paste(self, _):
        self._paste_clipboard()
        return "break"

    def _on_mouse_focus(self, _):
        if self._text:
            self._text.focus_set()
            self._keep_cursor_at_end()
        return "break"

    def _on_mouse_press(self, event):
        t = self._text
        if not t:
            return "break"
        t.focus_set()
        self._select_anchor = t.index(f"@{event.x},{event.y}")
        t.tag_remove("sel", "1.0", "end")
        self._keep_cursor_at_end()
        return "break"

    def _on_mouse_drag(self, event):
        t = self._text
        if not t or not self._select_anchor:
            return "break"
        idx = t.index(f"@{event.x},{event.y}")
        if t.compare(idx, "<", self._select_anchor):
            start, end = idx, self._select_anchor
        else:
            start, end = self._select_anchor, idx
        t.tag_remove("sel", "1.0", "end")
        if t.compare(start, "!=", end):
            t.tag_add("sel", start, end)
        self._keep_cursor_at_end()
        return "break"

    def _on_mouse_release(self, _):
        self._select_anchor = None
        self._keep_cursor_at_end()
        return "break"

    def _keep_cursor_at_end(self):
        if not self._text:
            return
        try:
            self._text.mark_set("insert", "end-1c")
        except tk.TclError:
            pass

    # ── Event/display handling ─────────────────────────────────────

    def _drain_queue(self):
        if not self.is_open:
            return
        self._apply_current_fonts()
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "connected":
                self._append_plain(
                    "已连接到 localhost:%d  （桥接客户端）\n"
                    "可直接在终端中输入，按 Enter 发送。\n\n"
                    % self._ctrl.config.tcp_port,
                    ("ansi_bright_black",),
                )
            elif kind == "disconnected":
                self._append_plain(f"\n--- 已断开：{payload} ---\n", ("ansi_bright_black",))
            elif kind == "error":
                self._append_plain(f"\n[错误] {payload}\n", ("ansi_red",))
            elif kind == "data":
                for text, tags in self._ansi.feed(payload):
                    self._append_plain(text, tags)
        self._win.after(30, self._drain_queue)

    def _append_plain(self, text: str, tags: tuple[str, ...] = ()):
        if not self._text or not text:
            return
        t = self._text
        try:
            self._keep_cursor_at_end()
            remaining = text
            while remaining:
                bs = remaining.find("\x08")
                cr = remaining.find("\r")
                positions = [p for p in (bs, cr) if p >= 0]
                if not positions:
                    t.insert("end", remaining, tags)
                    self._line_count += remaining.count("\n")
                    break
                pos = min(positions)
                if pos:
                    chunk = remaining[:pos]
                    t.insert("end", chunk, tags)
                    self._line_count += chunk.count("\n")
                ch = remaining[pos]
                if ch == "\x08":
                    idx = t.index("end-1c")
                    if idx != "1.0":
                        t.delete(f"{idx} - 1 char", idx)
                    remaining = remaining[pos + 1:]
                elif ch == "\r":
                    if pos + 1 < len(remaining) and remaining[pos + 1] == "\n":
                        t.insert("end", "\n", tags)
                        self._line_count += 1
                        remaining = remaining[pos + 2:]
                    else:
                        line = t.index("end-1c").split(".")[0]
                        t.mark_set("insert", f"{line}.0")
                        remaining = remaining[pos + 1:]
            self._keep_cursor_at_end()
            t.see("end")
            if self._line_count > MAX_TERMINAL_LINES:
                trim = MAX_TERMINAL_LINES // 4
                t.delete("1.0", f"{trim}.0")
                self._line_count -= trim
                self._keep_cursor_at_end()
        except tk.TclError:
            pass

    # ── Context menu ────────────────────────────────────────────────

    def _show_menu(self, event):
        self._keep_cursor_at_end()
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _copy(self):
        if not self._text:
            return
        try:
            sel = self._text.get("sel.first", "sel.last")
        except tk.TclError:
            return
        self._win.clipboard_clear()
        self._win.clipboard_append(sel)

    def _paste_clipboard(self):
        if not self._client or not self._client.connected:
            return
        try:
            data = self._win.clipboard_get()
        except tk.TclError:
            return
        if data:
            self._client.send(data.encode("utf-8", errors="replace"))

    def _clear(self):
        if self._text:
            self._text.delete("1.0", "end")
            self._line_count = 0

"""Custom lightweight widgets used by the manager UI."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import theme


def color_for_client(name: str) -> str:
    return theme.CLIENT_COLOR_HEX.get(name, theme.TEXT_SECONDARY)


class RoundedCard(tk.Canvas):
    """Canvas-backed rounded panel with an interior frame."""

    def __init__(
        self,
        parent,
        radius: int = 18,
        fill: str = theme.CARD_BG,
        outline: str = theme.CARD_BORDER,
        padding: tuple[int, int] = (28, 26),
        **kw,
    ):
        super().__init__(
            parent,
            bg=theme.WINDOW_BG,
            highlightthickness=0,
            borderwidth=0,
            **kw,
        )
        self.radius = radius
        self.fill = fill
        self.outline = outline
        self.pad_x, self.pad_y = padding
        self.inner = tk.Frame(self, bg=fill, highlightthickness=0)
        self._window_id = self.create_window(
            self.pad_x, self.pad_y, anchor="nw", window=self.inner
        )
        self.bind("<Configure>", self._draw)

    def _draw(self, event=None):
        self.delete("card")
        w = max(2, self.winfo_width())
        h = max(2, self.winfo_height())
        self.create_round_rect(
            1,
            1,
            w - 1,
            h - 1,
            self.radius,
            fill=self.fill,
            outline=self.outline,
            width=1,
            tags="card",
        )
        self.tag_lower("card")
        self.coords(self._window_id, self.pad_x, self.pad_y)
        self.itemconfigure(
            self._window_id,
            width=max(1, w - self.pad_x * 2),
            height=max(1, h - self.pad_y * 2),
        )

    def create_round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=20, **kw)


class StatusPill(tk.Canvas):
    """Small rounded status badge."""

    def __init__(self, parent, text="STOPPED", fill=theme.FIELD_BG, fg=theme.TEXT_MUTED):
        super().__init__(
            parent,
            width=114,
            height=34,
            bg=theme.WINDOW_BG,
            highlightthickness=0,
        )
        self.text = text
        self.fill = fill
        self.fg = fg
        self.bind("<Configure>", lambda _: self._draw())
        self._draw()

    def set(self, text: str, fill: str, fg: str):
        self.text = text
        self.fill = fill
        self.fg = fg
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or 114
        h = self.winfo_height() or 34
        self.create_round_rect(1, 1, w - 1, h - 1, h // 2, fill=self.fill, outline="")
        self.create_text(w / 2, h / 2, text=self.text, fill=self.fg, font=theme.SMALL_FONT)

    def create_round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=18, **kw)


class PillButton(tk.Canvas):
    """Canvas pill button with command callback."""

    def __init__(
        self,
        parent,
        text: str,
        command: Callable[[], None] | None = None,
        fill: str = theme.FIELD_BG,
        fg: str = theme.TEXT,
        active_fill: str | None = None,
        width: int = 150,
        height: int = 38,
        **kw,
    ):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=theme.CARD_BG,
            highlightthickness=0,
            cursor="hand2",
            **kw,
        )
        self.text = text
        self.command = command
        self.fill = fill
        self.fg = fg
        self.active_fill = active_fill or fill
        self.disabled = False
        self._active = False
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self._draw()

    def set_text(self, text: str):
        self.text = text
        self._draw()

    def set_disabled(self, disabled: bool):
        self.disabled = disabled
        self.configure(cursor="" if disabled else "hand2")
        self._draw()

    def _click(self, _):
        if not self.disabled and self.command:
            self.command()

    def _on_configure(self, _):
        self._draw()

    def _enter(self, _):
        self._active = True
        self._draw()

    def _leave(self, _):
        self._active = False
        self._draw()

    def _draw(self):
        self.delete("all")
        actual_w = self.winfo_width()
        actual_h = self.winfo_height()
        w = actual_w if actual_w > 1 else int(self["width"])
        h = actual_h if actual_h > 1 else int(self["height"])
        fill = self.active_fill if self._active and not self.disabled else self.fill
        fg = self.fg if not self.disabled else theme.TEXT_MUTED
        if self.disabled:
            fill = theme.FIELD_BG
        self.create_round_rect(1, 1, w - 1, h - 1, 10, fill=fill, outline=theme.CARD_BORDER)
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=theme.BODY_FONT)

    def create_round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=18, **kw)


class FlowPanel(tk.Canvas):
    """Three-node flow panel matching the reference image."""

    def __init__(self, parent):
        super().__init__(parent, height=144, bg=theme.WINDOW_BG, highlightthickness=0)
        self.com_text = "COM9  CP210x  115200"
        self.server_text = "TCP server  :9999"
        self.clients_text = "MobaXterm / PuTTY / AI / Built-in"
        self.running = False
        self._phase = 0
        self._after_id: str | None = None
        self.bind("<Configure>", self._draw)

    def update_labels(self, com: str, baud: int, tcp_port: int, running: bool = True):
        self.running = running
        if running:
            self.com_text = f"{com or 'COM?'}  CP210x  {baud}"
            self.server_text = f"TCP server  :{tcp_port}"
            self.clients_text = "MobaXterm / PuTTY / AI / Built-in"
            self._start_animation()
        else:
            self.com_text = "No active serial session"
            self.server_text = "TCP server stopped"
            self.clients_text = "No clients"
            self.stop_animation()
        self._draw()

    def stop_animation(self):
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def destroy(self):
        self.stop_animation()
        super().destroy()

    def _start_animation(self):
        if not self._after_id and self.winfo_exists():
            self._after_id = self.after(140, self._animate)

    def _animate(self):
        self._after_id = None
        if not self.running or not self.winfo_exists():
            return
        self._phase = (self._phase + 1) % 18
        self._draw()
        self._after_id = self.after(140, self._animate)

    def _draw(self, event=None):
        self.delete("all")
        w = max(260, self.winfo_width())
        h = max(120, self.winfo_height())

        if w < 620:
            self._draw_vertical_flow(w, h)
            return

        y = h / 2
        xs = [86, w / 2, w - 86]
        data = [
            ("Physical UART", self.com_text, theme.BLUE),
            ("Bridge Core", self.server_text, theme.GREEN),
            ("Clients", self.clients_text, theme.PURPLE),
        ]
        self.create_line(xs[0], y, xs[2], y, fill="#b9c5d4", width=2)
        for i in range(2):
            self.create_polygon(xs[i + 1] - 10, y, xs[i + 1] - 22, y - 7, xs[i + 1] - 22, y + 7, fill="#91a0b4", outline="")
        if self.running:
            self._draw_activity_dots(xs, y)
        for x, (title, sub, color) in zip(xs, data):
            self.create_oval(x - 12, y - 12, x + 12, y + 12, fill=theme.WINDOW_BG, outline=color, width=3)
            self.create_oval(x - 5, y - 5, x + 5, y + 5, fill=color, outline="")
            self.create_text(x, y - 34, text=title, anchor="center", fill=theme.TEXT, font=(theme.FONT_FAMILY, 11))
            self.create_text(x, y + 34, text=sub, anchor="center", fill=theme.TEXT_MUTED, font=theme.SMALL_FONT, width=180)

    def _draw_activity_dots(self, xs, y):
        colors = (theme.BLUE, theme.GREEN)
        for seg in range(2):
            x1 = xs[seg] + 34
            x2 = xs[seg + 1] - 34
            span = max(1, x2 - x1)
            for offset in (0, 6, 12):
                t = ((self._phase + offset) % 18) / 17
                x = x1 + span * t
                radius = 3 if offset else 4
                self.create_oval(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    fill=colors[seg],
                    outline="",
                )

    def _arrow(self, x1, y1, x2, y2):
        self.create_line(x1, y1, x2, y2, fill="#91a0b4", width=3)
        self.create_polygon(x2, y2, x2 - 14, y2 - 8, x2 - 14, y2 + 8, fill="#91a0b4", outline="")

    def _draw_vertical_flow(self, w, h):
        x = 34
        text_x = 62
        ys = [28, 86, 144]
        data = [
            ("Physical UART", self.com_text, theme.BLUE),
            ("Bridge Core", self.server_text, theme.GREEN),
            ("Clients", self.clients_text, theme.PURPLE),
        ]
        self.create_line(x, ys[0] + 15, x, ys[2] - 15, fill="#b9c5d4", width=2)
        if self.running:
            y1 = ys[0] + 22
            y2 = ys[2] - 22
            span = max(1, y2 - y1)
            for offset, color in ((0, theme.BLUE), (6, theme.GREEN), (12, theme.PURPLE)):
                t = ((self._phase + offset) % 18) / 17
                cy = y1 + span * t
                self.create_oval(x - 4, cy - 4, x + 4, cy + 4, fill=color, outline="")
        for y, (title, sub, color) in zip(ys, data):
            self.create_oval(x - 11, y - 11, x + 11, y + 11, fill=theme.WINDOW_BG, outline=color, width=3)
            self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline="")
            self.create_text(text_x, y - 12, text=title, anchor="w", fill=theme.TEXT, font=(theme.FONT_FAMILY, 10))
            self.create_text(text_x, y + 8, text=sub, anchor="w", fill=theme.TEXT_MUTED, font=theme.TINY_FONT, width=max(160, w - text_x - 16))

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=20, **kw)


class ClientRow(tk.Canvas):
    """Connected-client row."""

    def __init__(self, parent, name: str, role: str, addr: str, color_name: str):
        super().__init__(parent, height=64, bg=theme.CARD_BG, highlightthickness=0)
        self.name = name
        self.role = role
        self.addr = addr
        self.color = color_for_client(color_name)
        self.bind("<Configure>", self._draw)

    def _draw(self, event=None):
        self.delete("all")
        w = self.winfo_width() or 320
        self._round_rect(1, 1, w - 1, 62, 12, fill=theme.CARD_ALT, outline="#e3eaf2")
        self.create_oval(18, 24, 32, 38, fill=self.color, outline="")
        self.create_text(48, 22, text=self.name, anchor="w", fill=theme.TEXT, font=(theme.FONT_FAMILY, 10))
        self.create_text(48, 44, text=f"{self.role}   {self.addr}", anchor="w", fill=theme.TEXT_MUTED, font=theme.SMALL_FONT)

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=18, **kw)


class ActionRow(tk.Canvas):
    """Quick action row with optional blue OPEN pill."""

    def __init__(
        self,
        parent,
        title: str,
        subtitle: str,
        command: Callable[[], None] | None = None,
        primary: bool = False,
    ):
        super().__init__(parent, height=54, bg=theme.CARD_BG, highlightthickness=0, cursor="hand2")
        self.title = title
        self.subtitle = subtitle
        self.command = command
        self.primary = primary
        self.disabled = False
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._click)

    def set_disabled(self, disabled: bool):
        self.disabled = disabled
        self.configure(cursor="" if disabled else "hand2")
        self._draw()

    def _click(self, _):
        if self.command and not self.disabled:
            self.command()

    def _draw(self, event=None):
        self.delete("all")
        w = self.winfo_width() or 360
        fill = theme.BLUE_SOFT if self.primary and not self.disabled else theme.CARD_ALT
        outline = "#bcd5ff" if self.primary and not self.disabled else "#e3eaf2"
        if self.disabled:
            fill = theme.FIELD_BG
        self._round_rect(1, 1, w - 1, 52, 12, fill=fill, outline=outline)
        dot = theme.BLUE if self.primary else theme.TEXT_MUTED
        if self.disabled:
            dot = theme.TEXT_MUTED
        self.create_oval(22, 20, 36, 34, fill=dot, outline="")
        text_width = max(90, w - (188 if self.primary and w >= 260 else 76))
        self.create_text(56, 19, text=self.title, anchor="w", width=text_width, fill=theme.BLUE if self.primary and not self.disabled else theme.TEXT, font=theme.BODY_FONT)
        self.create_text(56, 40, text=self.subtitle, anchor="w", fill=theme.TEXT_MUTED, font=theme.TINY_FONT)
        if self.primary and w >= 260:
            self._round_rect(w - 132, 16, w - 30, 40, 13, fill=theme.BLUE if not self.disabled else "#cbd5e1", outline="")
            self.create_text(w - 81, 28, text="OPEN", fill="#ffffff", font=theme.TINY_FONT)

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=18, **kw)


class EventStrip(tk.Canvas):
    """Dark event strip showing latest key events."""

    def __init__(self, parent):
        super().__init__(parent, height=78, bg=theme.WINDOW_BG, highlightthickness=0)
        self.events: list[tuple[str, str]] = []
        self.bind("<Configure>", self._draw)

    def push(self, text: str):
        self.events.append((time.strftime("%H:%M:%S"), text))
        self.events = self.events[-4:]
        self._draw()

    def _draw(self, event=None):
        self.delete("all")
        w = self.winfo_width() or 1000
        self._round_rect(1, 1, w - 1, 76, 14, fill=theme.EVENT_BG, outline="")
        if not self.events:
            self.create_text(28, 39, text="Ready", anchor="w", fill=theme.EVENT_MUTED, font=theme.MONO_SMALL)
            return
        slot = max(240, (w - 56) // max(1, len(self.events)))
        x = 28
        for ts, msg in self.events:
            self.create_text(x, 38, text=f"{ts} {msg}", anchor="w", fill=theme.EVENT_TEXT, font=theme.MONO_SMALL)
            x += slot

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=18, **kw)


# Backward-compatible names for tests/imports that may still reference them.
StatusBadge = StatusPill
FlowDiagram = FlowPanel
ClientCard = ClientRow
EventBar = EventStrip

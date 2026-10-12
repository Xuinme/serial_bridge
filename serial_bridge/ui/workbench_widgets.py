"""Reusable workbench widgets: centering, fade-in, tooltip, auto-hide scrollbar.

TASK-010.  Tk/ttk has no per-widget alpha, so "transparency" here means
placing an 8px overlay on top of the content and interpolating the thumb
colour toward the track colour before hiding it - never a real alpha track.
"""

from __future__ import annotations

import tkinter as tk

from . import workbench_theme as wt


def center_on_parent(toplevel, parent) -> None:
    """Center a modal on *parent*, clamped to the visible screen bounds."""
    toplevel.update_idletasks()
    try:
        pw = parent.winfo_width() or 1
        ph = parent.winfo_height() or 1
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
    except tk.TclError:
        pw = ph = 1
        px = py = 0
    w = toplevel.winfo_reqwidth()
    h = toplevel.winfo_reqheight()
    try:
        vx = toplevel.winfo_vrootx()
        vy = toplevel.winfo_vrooty()
        vw = toplevel.winfo_vrootwidth()
        vh = toplevel.winfo_vrootheight()
    except tk.TclError:
        vx = vy = 0
        vw = toplevel.winfo_screenwidth()
        vh = toplevel.winfo_screenheight()
    x = px + (pw - w) // 2
    y = py + (ph - h) // 2
    x = max(vx, min(x, vx + max(0, vw - w)))
    y = max(vy, min(y, vy + max(0, vh - h)))
    # Set the size explicitly too, so the mapped window really is w x h.
    toplevel.geometry("%dx%d+%d+%d" % (int(w), int(h), int(x), int(y)))
    # The window manager may enforce a larger minimum (Windows tracking size);
    # re-centre against whatever size the window actually got.
    try:
        toplevel.update_idletasks()
        aw = toplevel.winfo_width()
        ah = toplevel.winfo_height()
    except tk.TclError:
        return
    if aw != w or ah != h:
        x = px + (pw - aw) // 2
        y = py + (ph - ah) // 2
        x = max(vx, min(x, vx + max(0, vw - aw)))
        y = max(vy, min(y, vy + max(0, vh - ah)))
        toplevel.geometry("+%d+%d" % (int(x), int(y)))


def fade_in_toplevel(toplevel, frames: int = 4, total_ms: int = 120,
                     start_alpha: float = 0.92) -> None:
    """Fade a modal from 0.92 to 1.0 in 4 frames; no-op if unsupported."""
    try:
        toplevel.attributes("-alpha", start_alpha)
    except tk.TclError:
        return
    steps = max(1, int(frames))
    delay = max(1, int(total_ms) // steps)
    state = {"after": None}

    def _cancel(_event=None) -> None:
        pending = state.get("after")
        state["after"] = None
        if pending is not None:
            try:
                toplevel.after_cancel(pending)
            except (tk.TclError, ValueError):
                pass

    try:
        toplevel.bind("<Destroy>", _cancel, add="+")
    except tk.TclError:
        pass

    def _step(i: int) -> None:
        state["after"] = None
        try:
            toplevel.attributes("-alpha", min(1.0, start_alpha + (1.0 - start_alpha) * i / steps))
        except tk.TclError:
            return
        if i < steps:
            try:
                state["after"] = toplevel.after(delay, lambda: _step(i + 1))
            except tk.TclError:
                pass

    _step(1)


class Tooltip:
    """Small themed hover tooltip (no third-party dependency)."""

    DELAY_MS = 420

    def __init__(self, widget, text: str, delay_ms: int | None = None):
        self._widget = widget
        self._text = text
        self._delay = self.DELAY_MS if delay_ms is None else int(delay_ms)
        self._win = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        # A pending after() would fire against a destroyed widget command and
        # leave an "invalid command name" diagnostic; cancel it on destroy.
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not self._widget:
            return
        self.hide()

    def _schedule(self, _event=None) -> None:
        self._cancel()
        try:
            self._after = self._widget.after(self._delay, self._show)
        except tk.TclError:
            pass

    def _show(self) -> None:
        self._after = None
        if self._win is not None or not self._text:
            return
        try:
            x = self._widget.winfo_rootx() + 12
            y = self._widget.winfo_rooty() + self._widget.winfo_height() + 6
        except tk.TclError:
            return
        win = tk.Toplevel(self._widget)
        win.wm_overrideredirect(True)
        win.configure(bg=wt.CARD_BORDER)
        tk.Label(win, text=self._text, bg=wt.CARD_BG, fg=wt.TEXT,
                 font=wt.TINY_FONT, padx=8, pady=4,
                 highlightthickness=0).pack(padx=1, pady=1)
        win.wm_geometry("+%d+%d" % (x, y))
        self._win = win

    def hide(self, _event=None) -> None:
        self._cancel()
        win, self._win = self._win, None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self._widget.after_cancel(self._after)
            except (tk.TclError, ValueError):
                pass
            self._after = None

    def destroy(self) -> None:
        self.hide()


class AutoHideScrollbar(tk.Canvas):
    """8px overlay scrollbar: thumb only, auto-hide, simulated fade-out.

    ``set(first, last)`` is wired to the target yscrollcommand.  When the
    content fits (first <= 0 and last >= 1) the bar hides immediately;
    otherwise interaction reveals it and 900ms of idle starts a 5-frame
    fade toward the track colour, after which it is place_forget()-ed.
    """

    WIDTH = 8
    THUMB_NORMAL = 4
    THUMB_ACTIVE = 6
    HIDE_DELAY = 900
    FADE_FRAMES = 5
    FADE_INTERVAL = 30

    def __init__(self, target, container=None, **kw):
        parent = container if container is not None else target.master
        super().__init__(parent, width=self.WIDTH, highlightthickness=0,
                         borderwidth=0, bd=0, bg=wt.PANEL_BG, **kw)
        self._target = target
        self._first = 0.0
        self._last = 1.0
        self._placed = False
        self._dragging = False
        self._hover = False
        self._hide_after = None
        self._fade_after = None
        self._fade_step = None
        self._thumb_w = self.THUMB_NORMAL
        self._target.configure(yscrollcommand=self.set)
        self._bind_target()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    # -- wiring ----------------------------------------------------------

    def _bind_target(self) -> None:
        self._target.bind("<Enter>", self._on_enter, add="+")
        self._target.bind("<Leave>", self._on_leave, add="+")
        self._target.bind("<MouseWheel>", self._on_wheel, add="+")
        self._target.bind("<Button-4>", self._on_wheel, add="+")
        self._target.bind("<Button-5>", self._on_wheel, add="+")

    def refresh_theme(self) -> None:
        """Re-read the active palette colours (after a theme switch)."""
        try:
            self.configure(bg=self._track_color())
        except tk.TclError:
            return
        if self._placed:
            self._draw()

    def _track_color(self) -> str:
        """The colour underneath the overlay = the target's own background."""
        try:
            return str(self._target.cget("bg"))
        except tk.TclError:
            return wt.PANEL_BG

    def _thumb_color(self) -> str:
        return wt.SCROLL_THUMB_HOVER if (self._hover or self._dragging) else wt.SCROLL_THUMB

    # -- geometry --------------------------------------------------------

    def place_over(self) -> None:
        """Overlay the right edge of the container; adds no grid column."""
        self.place(relx=1.0, rely=0.0, anchor="ne", relheight=1.0,
                   width=self.WIDTH)
        self._placed = True
        self._draw()

    def is_visible(self) -> bool:
        return bool(self.winfo_manager())

    def set(self, first, last) -> None:
        """yscrollcommand sink (also called whenever yview changes)."""
        try:
            first = float(first)
            last = float(last)
        except (TypeError, ValueError):
            return
        changed = (first, last) != (self._first, self._last)
        self._first, self._last = first, last
        if first <= 0.0 and last >= 1.0:
            self._cancel_timers()
            self._fade_step = None
            self._forget()
            return
        if changed or self._fade_step is not None:
            self._reveal()
        elif not self.is_visible():
            return
        self._draw()

    def _forget(self) -> None:
        if self.is_visible():
            try:
                self.place_forget()
            except tk.TclError:
                pass

    # -- interaction -----------------------------------------------------

    def _on_wheel(self, event=None):
        """Reveal, and drive the view for targets lacking their own binding.

        tk.Text already scrolls itself through its class binding, so this must
        not consume the event for it.  tk.Canvas has no default wheel
        behaviour, so the overlay drives yview_scroll itself.
        """
        self._reveal()
        try:
            is_canvas = self._target.winfo_class() == "Canvas"
        except tk.TclError:
            return None
        if not is_canvas:
            return None
        delta = getattr(event, "delta", 0) or 0
        num = getattr(event, "num", None)
        try:
            if num == 4:
                self._target.yview_scroll(-1, "units")
            elif num == 5:
                self._target.yview_scroll(1, "units")
            elif delta:
                steps = -int(delta / 120)
                self._target.yview_scroll(steps if steps else (-1 if delta > 0 else 1), "units")
        except tk.TclError:
            pass
        return "break"

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._reveal()
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        if not self._dragging:
            self._schedule_hide()
        self._draw()

    def _on_press(self, event) -> None:
        self._reveal()
        if self._thumb_bounds() and self._thumb_bounds()[0] <= event.y <= self._thumb_bounds()[1]:
            self._dragging = True
            self._drag_offset = event.y - self._thumb_bounds()[0]
        else:
            self._jump_to(event.y)
        self._draw()

    def _on_drag(self, event) -> None:
        if not self._dragging:
            return
        self._reveal()
        self._jump_to(event.y - getattr(self, "_drag_offset", 0), center=False)

    def _on_release(self, _event=None) -> None:
        self._dragging = False
        self._schedule_hide()
        self._draw()

    def _thumb_bounds(self):
        height = max(1, self.winfo_height())
        top = int(self._first * height)
        size = max(24, int((self._last - self._first) * height))
        bottom = min(height, top + size)
        return (top, bottom)

    def _jump_to(self, y, center: bool = True) -> None:
        height = max(1, self.winfo_height())
        top, bottom = self._thumb_bounds()
        size = max(1, bottom - top)
        if center:
            y = y - size // 2
        fraction = max(0.0, min(1.0, y / float(height)))
        try:
            self._target.yview_moveto(fraction)
        except tk.TclError:
            return

    # -- reveal / fade ---------------------------------------------------

    def _reveal(self) -> None:
        self._cancel_timers()
        self._fade_step = None
        if not self.is_visible():
            self.place_over()
        self._thumb_w = self.THUMB_ACTIVE if (self._hover or self._dragging) else self.THUMB_NORMAL
        self._draw()
        if not self._dragging:
            self._schedule_hide()

    def _schedule_hide(self) -> None:
        self._cancel_timers()
        if self._first <= 0.0 and self._last >= 1.0:
            self._forget()
            return
        try:
            self._hide_after = self.after(self.HIDE_DELAY, self._begin_fade)
        except tk.TclError:
            self._hide_after = None

    def _begin_fade(self) -> None:
        self._hide_after = None
        self._fade_step = 0
        self._fade()

    def _fade(self) -> None:
        if self._fade_step is None:
            return
        step = self._fade_step
        if step >= self.FADE_FRAMES:
            self._fade_step = None
            self._fade_after = None
            self._forget()
            return
        t = step / float(self.FADE_FRAMES)
        color = wt._blend(self._thumb_color(), self._track_color(), t)
        self._draw(color=color)
        self._fade_step = step + 1
        try:
            self._fade_after = self.after(self.FADE_INTERVAL, self._fade)
        except tk.TclError:
            self._fade_after = None
            self._fade_step = None

    def _cancel_timers(self) -> None:
        for attr in ("_hide_after", "_fade_after"):
            pending = getattr(self, attr, None)
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except (tk.TclError, ValueError):
                    pass
                setattr(self, attr, None)

    # -- drawing ---------------------------------------------------------

    def _draw(self, color=None) -> None:
        try:
            self.delete("all")
            height = max(1, self.winfo_height())
            top, bottom = self._thumb_bounds()
            top = max(0, min(top, height - 8))
            bottom = max(top + 8, min(bottom, height))
            width = self._thumb_w if not self._dragging else self.THUMB_ACTIVE
            x0 = (int(self.winfo_width() or self.WIDTH) - width) / 2.0
            self.create_rectangle(x0, top, x0 + width, bottom,
                                  fill=color or self._thumb_color(), outline="")
        except tk.TclError:
            pass

    def destroy(self) -> None:
        self._cancel_timers()
        self._fade_step = None
        try:
            super().destroy()
        except tk.TclError:
            pass

"""TASK-008: WorkbenchWindow layout robustness.

Verifies the four-region workbench holds together at the target minimum
1000x650 and at common desktop sizes without overlap or clipping, that the
right detail panel folds below the collapse threshold, and that dynamic
content does not change the toolbar size.  No real COM or long-lived GUI
process is used; every window is created and destroyed inside the test.
"""
import sys
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1]))

from ui.workbench_window import WorkbenchWindow
from ui.workbench_dialogs import UiPreferences
from ui import workbench_theme as wt

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  [PASS] " if ok else "  [FAIL] ") + name +
          (f"  {detail}" if detail else ""))


@dataclass
class Snapshot:
    session_id: str
    name: str
    com_port: str
    baud_rate: int
    tcp_port: int
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


class MiniService:
    """Minimal duck-typed service for layout tests (no lifecycle here)."""

    def __init__(self, seed=()):
        self._snap = []
        self._events = []
        for sid, name, com, baud, tcp, running in seed:
            self._snap.append(Snapshot(session_id=sid, name=name,
                                       com_port=com, baud_rate=baud,
                                       tcp_port=tcp, running=running,
                                       client_count=1))
        self._next = 10001

    def list_sessions(self):
        return list(self._snap)

    def drain_events(self):
        evs = self._events
        self._events = []
        return evs

    def next_tcp_port(self, start=9999):
        p = start
        while any(s.tcp_port == p for s in self._snap):
            p += 1
        return p

    def create_session(self, config):
        return config.session_id or "new"

    def update_session(self, sid, config):
        pass

    def start_session(self, sid):
        self._events.append(Event(sid, "started", 0.0, "started"))

    def stop_session(self, sid):
        pass

    def delete_session(self, sid):
        self._snap = [s for s in self._snap if s.session_id != sid]


SEED = [("s1", "COM17", "COM17", 115200, 9999, True),
        ("s2", "COM18", "COM18", 115200, 9000, False)]


def build(svc, min_size=(1000, 650), collapse_threshold=1000, geometry="1000x650"):
    root = tk.Tk()
    win = WorkbenchWindow(svc, on_open_terminal=lambda sid: None,
                          on_close_request=lambda: None,
                          root=root, min_size=min_size,
                          collapse_threshold=collapse_threshold)
    root.geometry(geometry)
    root.update_idletasks()
    root.update()
    win._sync_collapse()
    root.update_idletasks()
    return win, root


def geom(w):
    w.update_idletasks()
    return (w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height())


def no_overlap(a, b):
    """Return True if the two ("x","y","w","h") rects do not overlap."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay


def four_regions_ok(win, label):
    sx, sy, sw, sh = geom(win.sidebar)
    cx, cy, cw, ch = geom(win.center)
    rx, ry, rw, rh = geom(win.right)
    tx, ty, tw, th = geom(win.topbar)
    rxw, _, rxww, _ = None, None, None, None
    winw = win.root.winfo_width()
    winh = win.root.winfo_height()

    ok = True
    ok &= (sw >= 240 and sw <= 290)   # 250-ish, task allows 250-280
    ok &= (rw >= 260 and rw <= 320)
    ok &= (cw > 0 and ch > 0)
    ok &= (rw > 0 and rh > 0 and sw > 0 and sh > 0)
    # horizontal order: sidebar | center | right
    ok &= (sx + sw <= cx + 2)
    ok &= (cx + cw <= rx + 2)
    # regions stay inside the root
    ok &= (sx >= 0 and rx + rw <= winw + 2)
    ok &= (sy >= 0 and sy + sh <= winh + 2)
    # no overlap among the three main zones
    ok &= no_overlap((sx, sy, sw, sh), (cx, cy, cw, ch))
    ok &= no_overlap((cx, cy, cw, ch), (rx, ry, rw, rh))
    check(f"four regions hold at {label}",
          ok, f"sw={sw} rw={rw} cw={cw} ch={ch} win={winw}x{winh}")
    return ok


def test_min_size_1000x650():
    svc = MiniService(SEED)
    win, root = build(svc, min_size=(1000, 650), collapse_threshold=1000,
                      geometry="1000x650")
    try:
        check("window reports 1000x650 (or larger)",
              win.root.winfo_width() >= 1000 and win.root.winfo_height() >= 650,
              f"{win.root.winfo_width()}x{win.root.winfo_height()}")
        four_regions_ok(win, "1000x650")
        check("right panel present at 1000x650",
              not win._right_collapsed and win.right.winfo_ismapped())
    finally:
        win.destroy()


def test_common_desktop_sizes():
    for w, h in ((1280, 800), (1366, 768), (1920, 1080)):
        svc = MiniService(SEED)
        win, root = build(svc, min_size=(1000, 650), collapse_threshold=1000,
                          geometry=f"{w}x{h}")
        try:
            four_regions_ok(win, f"{w}x{h}")
        finally:
            win.destroy()


def test_right_collapse_threshold():
    svc = MiniService(SEED)
    win, root = build(svc, min_size=(700, 500), collapse_threshold=1000,
                      geometry="820x600")
    try:
        check("narrow window collapses right panel",
              win._right_collapsed and not win.right.winfo_ismapped(),
              f"collapsed={win._right_collapsed}")
        cx, cy, cw, ch = geom(win.center)
        check("centre expands when right folded", cw > 300, f"cw={cw}")

        # widen back -> panel returns
        root.geometry("1280x800")
        root.update_idletasks()
        root.update()
        win._sync_collapse()
        root.update_idletasks()
        check("wide window restores right panel",
              not win._right_collapsed and win.right.winfo_ismapped(),
              f"collapsed={win._right_collapsed}")
        four_regions_ok(win, "1280x800 restored")
    finally:
        win.destroy()


def test_toolbar_height_stable():
    svc = MiniService(SEED)
    win, root = build(svc, min_size=(1000, 650), collapse_threshold=1000,
                      geometry="1240x760")
    try:
        h0 = geom(win.topbar)[3]
        # inject lots of log content for s1 and refresh
        for i in range(200):
            win._record_event(Event("s1", "log", float(i), f"line {i}"))
        win._render_selected()
        root.update_idletasks()
        h1 = geom(win.topbar)[3]
        check("toolbar height unchanged by content", h0 == h1, f"{h0} vs {h1}")
    finally:
        win.destroy()


def test_controls_reachable_and_not_clipped():
    svc = MiniService(SEED)
    win, root = build(svc, min_size=(1000, 650), collapse_threshold=1000,
                      geometry="1000x650")
    try:
        winw = win.root.winfo_width()
        # Only session lifecycle commands belong in the top bar.  Built-in
        # Telnet remains in Quick actions and closing remains on the native X.
        top_buttons = (win.action_btn, win.delete_btn)
        check("topbar contains one state action and delete",
              [btn.cget("text") for btn in top_buttons]
              == ["停止", "删除"]
              and not hasattr(win, "start_btn")
              and not hasattr(win, "stop_btn")
              and not hasattr(win, "telnet_btn")
              and not hasattr(win, "close_btn"))
        check("quick actions keep the single built-in Telnet entry",
              win.quick_telnet_btn.cget("text") == "打开内置 Telnet")
        check("native window close protocol remains registered",
              bool(root.protocol("WM_DELETE_WINDOW")))

        # Top-bar buttons must be inside the window and to the right side.
        for btn in top_buttons:
            bx, by, bw, bh = geom(btn)
            check(f"topbar button '{btn.cget('text')}' reachable",
                  bx >= 0 and bx + bw <= winw + 2 and bw > 0 and bh > 0,
                  f"x={bx} w={bw} win={winw}")
    finally:
        win.destroy()


def test_grouped_fonts_keep_geometry_and_actions_accessible():
    root = tk.Tk()
    svc = MiniService(SEED)
    win = WorkbenchWindow(svc, on_open_terminal=lambda sid: None,
                          on_close_request=lambda: None, root=root)
    try:
        root.geometry("1240x760")
        root.update()
        before_size = (root.winfo_width(), root.winfo_height())
        check("Chinese UI font selected", wt.FONT_FAMILY == "Microsoft YaHei UI")
        check("window icon asset applies", wt.apply_window_icon(root))
        check("window uses short Chinese display name",
              root.title() == wt.APP_DISPLAY_NAME == "串口桥接")
        check("grouped font preference applies",
              win._apply_preferences(UiPreferences(
                  title_font_size=22,
                  ui_font_size=15,
                  helper_font_size=13,
                  terminal_font_size=18,
              )))
        root.update_idletasks()
        root.update()
        after_size = (root.winfo_width(), root.winfo_height())
        check("font changes do not resize the window",
              after_size == before_size, f"before={before_size} after={after_size}")
        check("font changes keep the fixed minimum",
              tuple(root.minsize()) == (1000, 700), str(root.minsize()))
        check("font changes keep fixed panel widths",
              win.sidebar.winfo_width() == 250 and win.right.winfo_width() == 280,
              f"sidebar={win.sidebar.winfo_width()} right={win.right.winfo_width()}")
        check("details use a scrollable fixed-width viewport",
              win.right_canvas.winfo_ismapped()
              and win.right_inner.winfo_reqwidth() <= win.right.winfo_width(),
              f"inner={win.right_inner.winfo_reqwidth()} right={win.right.winfo_width()}")
        check("final quick action remains in scrollable content",
              win.quick_refresh_btn.winfo_ismapped()
              and win.right_canvas.bbox("all") is not None,
              str(win.right_canvas.bbox("all")))
        check("larger fonts keep topbar actions visible",
              all(btn.winfo_ismapped() and btn.winfo_width() > 0
                  for btn in (win.action_btn, win.delete_btn)))
        check("theme rebuild keeps session rows full width",
              all(item.winfo_width() == win.sessions_canvas.winfo_width()
                  for item in win.session_items.values()),
              f"canvas={win.sessions_canvas.winfo_width()} "
              f"rows={[item.winfo_width() for item in win.session_items.values()]}")
        check("larger fonts keep stream commands uncut",
              all(btn.winfo_width() >= btn.winfo_reqwidth()
                  for btn in win.stream_buttons),
              str([(btn.cget("text"), btn.winfo_width(), btn.winfo_reqwidth())
                   for btn in win.stream_buttons]))
        root.geometry("1000x700")
        root.update()
        check("maximum fonts keep stream commands readable at minimum size",
              all(btn.winfo_ismapped() and btn.winfo_width() >= 100
                  for btn in win.stream_buttons),
              str([(btn.cget("text"), btn.winfo_width())
                   for btn in win.stream_buttons]))
    finally:
        win.destroy()
        wt.activate_theme("dark", "blue")


def test_first_map_sets_native_windows_taskbar_icons():
    if sys.platform != "win32":
        return

    import ctypes

    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
    ]
    user32.SendMessageW.restype = ctypes.c_void_p

    root = tk.Tk()
    root.withdraw()
    win = WorkbenchWindow(
        MiniService(SEED),
        on_open_terminal=lambda sid: None,
        on_close_request=lambda: None,
        root=root,
    )
    try:
        root.geometry("1000x700")
        root.update_idletasks()
        root.deiconify()
        root.update()
        hwnd = int(user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id())

        def native_icons():
            return tuple(int(user32.SendMessageW(hwnd, 0x007F, kind, 0) or 0)
                         for kind in (1, 0, 2))

        first = native_icons()
        check("first map sets native big/small taskbar icons",
              all(first), repr(first))
        state = root._serial_bridge_native_icons
        handles = (state["large"], state["small"])
        check("native icons come from owned large/small handles",
              first[0] == handles[0] and first[1] == handles[1]
              and first[2] == handles[1], repr((first, handles)))
        check("repeated native icon sync is idempotent",
              wt.sync_windows_taskbar_icon(root)
              and (state["large"], state["small"]) == handles)
    finally:
        win.destroy()
    check("destroy releases native icon state",
          not hasattr(root, "_serial_bridge_native_icons"))


def test_session_rows_fill_and_follow_available_width():
    svc = MiniService(SEED)
    win, root = build(svc, min_size=(1000, 650), collapse_threshold=1000,
                      geometry="1000x650")
    try:
        def widths_match():
            width = win.sessions_canvas.winfo_width()
            return width > 1 and all(
                item.winfo_width() == width
                for item in win.session_items.values()
            )

        check("session rows fill the list at minimum size", widths_match(),
              f"canvas={win.sessions_canvas.winfo_width()} "
              f"rows={[item.winfo_width() for item in win.session_items.values()]}")

        initial_width = win.sessions_canvas.winfo_width()
        root.geometry("1366x768")
        root.grid_columnconfigure(0, minsize=320)
        root.update_idletasks()
        root.update()
        check("session list receives the resized sidebar width",
              win.sessions_canvas.winfo_width() > initial_width,
              f"before={initial_width} after={win.sessions_canvas.winfo_width()}")
        check("session rows follow the list after layout resize", widths_match(),
              f"canvas={win.sessions_canvas.winfo_width()} "
              f"rows={[item.winfo_width() for item in win.session_items.values()]}")
    finally:
        win.destroy()


def main():
    results.clear()
    test_min_size_1000x650()
    test_common_desktop_sizes()
    test_right_collapse_threshold()
    test_toolbar_height_stable()
    test_controls_reachable_and_not_clipped()
    test_grouped_fonts_keep_geometry_and_actions_accessible()
    test_first_map_sets_native_windows_taskbar_icons()
    test_session_rows_fill_and_follow_available_width()

    print("\n" + "=" * 55)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"RESULTS: {passed}/{len(results)} passed")
    for name, ok, detail in results:
        print(("  [OK] " if ok else "  [XX] ") + name)
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

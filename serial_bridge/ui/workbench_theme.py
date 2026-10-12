"""Two-mode (Light/Dark) palette, accents and ttk styles for the workbench.

TASK-010: the workbench ships fixed Dark and Light palettes plus five fixed
accent colours.  `activate_theme(mode, accent)` rebinds the module-level
tokens in place so existing widgets that read `wt.TOKEN` at runtime pick up
the new values; a full visual change is applied by rebuilding the workbench
frames (see WorkbenchWindow._rebuild).

Colour contract (TASK-010):
* semantic status colours are fixed and never follow the accent:
  Running #2E9B5F, Starting/Stopping #C58A18, Error/Delete #D45252
* the accent only affects selection / focus / primary buttons.
* no gradients, glow, decorative fills, glassmorphism or large radii.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
import sys
from tkinter import ttk

from app.models import FONT_SIZE_DEFAULTS, FONT_SIZE_RANGES
from app.version import APP_DISPLAY_NAME, APP_VERSION, APP_VERSION_LABEL

# -- Fixed accent colours (TASK-010) -----------------------------------

ACCENTS = {
    "blue": "#2F6FEB",
    "teal": "#0F766E",
    "green": "#2E7D32",
    "amber": "#A15C00",
    "rose": "#B4234D",
}
ACCENT_ORDER = ("blue", "teal", "green", "amber", "rose")

# -- Fixed semantic colours (never follow the accent) ------------------

SEMANTIC_RUNNING = "#2E9B5F"
SEMANTIC_WARNING = "#C58A18"
SEMANTIC_ERROR = "#D45252"

# -- Fixed palettes ----------------------------------------------------

PALETTES = {
    "dark": {
        "APP_BG": "#0F1115",
        "APP_BG_2": "#0B0D10",
        "SIDEBAR_BG": "#0B0D10",
        "SIDEBAR_HOVER": "#171A20",
        "SIDEBAR_ACTIVE": "#20242C",
        "SIDEBAR_LINE": "#2D333D",
        "PANEL_BG": "#181B21",
        "CARD_BG": "#20242C",
        "CARD_BG_ALT": "#242A33",
        "CARD_BORDER": "#2D333D",
        "TITLEBAR_BG": "#0B0D10",
        "LOG_BG": "#0B0D10",
        "LOG_BORDER": "#2D333D",
        "TEXT": "#F2F4F7",
        "TEXT_SECONDARY": "#B5BDC9",
        "TEXT_MUTED": "#7F8998",
        "TEXT_FAINT": "#5A6272",
        "LOG_TEXT": "#D7DDE5",
        "LOG_MUTED": "#6B7480",
        "LOG_ASSERT": "#C9D1DA",
        "GRAY": "#6B7280",
        "GRAY_SOFT": "#232830",
        "ACCENT_TEXT": "#FFFFFF",
        "SOFT_MIX": 0.20,
        "SELECT_MIX": 0.26,
    },
    "light": {
        "APP_BG": "#EEF1F5",
        "APP_BG_2": "#F7F8FA",
        "SIDEBAR_BG": "#F7F8FA",
        "SIDEBAR_HOVER": "#EDF0F4",
        "SIDEBAR_ACTIVE": "#E6EBF2",
        "SIDEBAR_LINE": "#D7DDE5",
        "PANEL_BG": "#FFFFFF",
        "CARD_BG": "#F4F6F8",
        "CARD_BG_ALT": "#EEF1F5",
        "CARD_BORDER": "#D7DDE5",
        "TITLEBAR_BG": "#F7F8FA",
        "LOG_BG": "#F4F6F8",
        "LOG_BORDER": "#D7DDE5",
        "TEXT": "#1D232D",
        "TEXT_SECONDARY": "#596474",
        "TEXT_MUTED": "#7D8897",
        "TEXT_FAINT": "#98A2B0",
        "LOG_TEXT": "#1D232D",
        "LOG_MUTED": "#7D8897",
        "LOG_ASSERT": "#596474",
        "GRAY": "#6B7280",
        "GRAY_SOFT": "#E8ECF1",
        "ACCENT_TEXT": "#FFFFFF",
        "SOFT_MIX": 0.14,
        "SELECT_MIX": 0.20,
    },
}


def normalize_theme(mode) -> str:
    """Return a legal theme mode; anything else falls back to dark."""
    return mode if isinstance(mode, str) and mode in PALETTES else "dark"


def normalize_accent(name) -> str:
    """Return a legal accent name; anything else falls back to blue."""
    return name if isinstance(name, str) and name in ACCENTS else "blue"


def _rgb(value: str):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _blend(color_a: str, color_b: str, t: float) -> str:
    """Blend two #rrggbb colours; t=0 -> a, t=1 -> b."""
    ra, ga, ba = _rgb(color_a)
    rb, gb, bb = _rgb(color_b)
    mix = lambda x, y: int(round(x + (y - x) * t))
    return "#%02X%02X%02X" % (mix(ra, rb), mix(ga, gb), mix(ba, bb))


# Active state (module tokens below are rebound by _apply_tokens).
_ACTIVE_MODE = "dark"
_ACTIVE_ACCENT = "blue"
_ACTIVE_FONT_SIZES = dict(FONT_SIZE_DEFAULTS)


def current_theme_mode() -> str:
    return _ACTIVE_MODE


def current_accent() -> str:
    return _ACTIVE_ACCENT


def current_font_sizes() -> dict[str, int]:
    return dict(_ACTIVE_FONT_SIZES)


def _apply_tokens(mode: str, accent: str) -> None:
    """Rebind every module-level colour token for the given theme."""
    global _ACTIVE_MODE, _ACTIVE_ACCENT, STATE_COLORS
    mode = normalize_theme(mode)
    accent = normalize_accent(accent)
    _ACTIVE_MODE = mode
    _ACTIVE_ACCENT = accent
    p = PALETTES[mode]
    g = globals()
    for key, value in p.items():
        if isinstance(value, str):
            g[key] = value
    accent_hex = ACCENTS[accent]
    g["TEXT_ON_ACCENT"] = p["ACCENT_TEXT"]
    g["BLUE"] = accent_hex
    g["BLUE_SOFT"] = _blend(p["PANEL_BG"], accent_hex, p["SOFT_MIX"])
    g["ACCENT_SELECT"] = _blend(p["PANEL_BG"], accent_hex, p["SELECT_MIX"])
    g["GREEN"] = SEMANTIC_RUNNING
    g["AMBER"] = SEMANTIC_WARNING
    g["RED"] = SEMANTIC_ERROR
    g["GREEN_SOFT"] = _blend(p["PANEL_BG"], SEMANTIC_RUNNING, p["SOFT_MIX"])
    g["AMBER_SOFT"] = _blend(p["PANEL_BG"], SEMANTIC_WARNING, p["SOFT_MIX"])
    g["RED_SOFT"] = _blend(p["PANEL_BG"], SEMANTIC_ERROR, p["SOFT_MIX"])
    g["SCROLL_THUMB"] = _blend(p["PANEL_BG"], p["TEXT_MUTED"], 0.55)
    g["SCROLL_THUMB_HOVER"] = _blend(p["PANEL_BG"], p["TEXT"], 0.75)
    STATE_COLORS = {
        "stopped": ("已停止", TEXT_MUTED, GRAY_SOFT),
        "starting": ("正在启动", AMBER, AMBER_SOFT),
        "running": ("运行中", GREEN, GREEN_SOFT),
        "stopping": ("正在停止", AMBER, AMBER_SOFT),
        "error": ("错误", RED, RED_SOFT),
    }


def activate_theme(
    mode,
    accent,
    title_font_size=FONT_SIZE_DEFAULTS["title_font_size"],
    ui_font_size=FONT_SIZE_DEFAULTS["ui_font_size"],
    helper_font_size=FONT_SIZE_DEFAULTS["helper_font_size"],
    terminal_font_size=FONT_SIZE_DEFAULTS["terminal_font_size"],
) -> None:
    """Activate the palette, accent and four font/layout groups."""
    _apply_tokens(mode, accent)
    _apply_font_tokens(
        title_font_size, ui_font_size, helper_font_size, terminal_font_size
    )


# -- Fonts -------------------------------------------------------------

FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

def normalize_font_size(name: str, value) -> int:
    default = FONT_SIZE_DEFAULTS[name]
    low, high = FONT_SIZE_RANGES[name]
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if low <= parsed <= high else default


def normalize_font_sizes(title, ui, helper, terminal) -> dict[str, int]:
    values = (title, ui, helper, terminal)
    return {
        name: normalize_font_size(name, value)
        for name, value in zip(FONT_SIZE_DEFAULTS, values)
    }


def fonts_for(title, ui, helper, terminal):
    """Return all font tuples for four values without changing active state."""
    sizes = normalize_font_sizes(title, ui, helper, terminal)
    title_size = sizes["title_font_size"]
    ui_size = sizes["ui_font_size"]
    helper_size = sizes["helper_font_size"]
    terminal_size = sizes["terminal_font_size"]
    return {
        "title": (FONT_FAMILY, title_size, "bold"),
        "heading": (FONT_FAMILY, max(10, title_size - 3), "bold"),
        "section": (FONT_FAMILY, max(9, title_size - 4), "bold"),
        "body": (FONT_FAMILY, ui_size),
        "small": (FONT_FAMILY, helper_size),
        "tiny": (FONT_FAMILY, max(7, helper_size - 1)),
        "mono": (FONT_MONO, terminal_size),
        "mono_small": (FONT_MONO, max(8, terminal_size - 1)),
        "mono_tiny": (FONT_MONO, max(7, terminal_size - 2)),
    }


def _apply_font_tokens(title, ui, helper, terminal) -> None:
    global _ACTIVE_FONT_SIZES
    global TITLE_FONT, HEADING_FONT, SECTION_FONT, BODY_FONT, SMALL_FONT, TINY_FONT
    global MONO_FONT, MONO_SMALL, MONO_TINY
    global SIDEBAR_W, RIGHT_W, TOPBAR_H, STATUSBAR_H
    global MIN_W, MIN_H, DEFAULT_W, DEFAULT_H, COLLAPSE_THRESHOLD
    global ENTRY_PAD, BUTTON_PAD, PAD, SECTION_GAP

    sizes = normalize_font_sizes(title, ui, helper, terminal)
    _ACTIVE_FONT_SIZES = sizes
    fonts = fonts_for(*sizes.values())
    TITLE_FONT = fonts["title"]
    HEADING_FONT = fonts["heading"]
    SECTION_FONT = fonts["section"]
    BODY_FONT = fonts["body"]
    SMALL_FONT = fonts["small"]
    TINY_FONT = fonts["tiny"]
    MONO_FONT = fonts["mono"]
    MONO_SMALL = fonts["mono_small"]
    MONO_TINY = fonts["mono_tiny"]

    # Font preferences change text only.  Window and panel geometry are
    # deliberate design dimensions and must remain stable at every font size.
    SIDEBAR_W = 250
    RIGHT_W = 280
    TOPBAR_H = 56
    STATUSBAR_H = 30
    MIN_W = 1000
    MIN_H = 700
    DEFAULT_W = 1240
    DEFAULT_H = 760
    COLLAPSE_THRESHOLD = MIN_W

    ENTRY_PAD = (8, 6)
    BUTTON_PAD = (14, 7)
    PAD = 12
    SECTION_GAP = 18

# -- Fixed geometry (design-time sizes, used by layout tests) ---------

# Values are populated by _apply_font_tokens() below.
SIDEBAR_W = RIGHT_W = TOPBAR_H = STATUSBAR_H = 0
MIN_W = MIN_H = DEFAULT_W = DEFAULT_H = COLLAPSE_THRESHOLD = 0
POLL_MS = 400

MAX_LOG_LINES = 800
MAX_ACTIVITY_ROWS = 120

ENTRY_PAD = BUTTON_PAD = (0, 0)
PAD = SECTION_GAP = 0

APP_ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "serial_bridge.ico"


def find_app_icon_path() -> Path | None:
    """Locate the icon in source runs and Nuitka/Inno installations."""
    candidates = (
        APP_ICON_PATH,
        Path(sys.executable).resolve().parent / "assets" / "serial_bridge.ico",
        Path(sys.argv[0]).resolve().parent / "assets" / "serial_bridge.ico",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _windows_toplevel_handle(window) -> int:
    """Return the native top-level HWND behind a Tk window."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        child = int(window.winfo_id())
        return int(user32.GetAncestor(child, 2) or child)
    except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
        return 0


def sync_windows_taskbar_icon(window) -> bool:
    """Set per-window big/small icons before Windows builds the taskbar item."""
    if sys.platform != "win32":
        return False
    path = find_app_icon_path()
    hwnd = _windows_toplevel_handle(window)
    if path is None or not hwnd:
        return False

    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
        ]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        user32.DestroyIcon.restype = ctypes.c_int

        state = getattr(window, "_serial_bridge_native_icons", None)
        if state is None:
            image_icon = 1
            load_from_file = 0x0010
            large = int(user32.LoadImageW(
                None, str(path), image_icon,
                user32.GetSystemMetrics(11), user32.GetSystemMetrics(12),
                load_from_file,
            ) or 0)
            small = int(user32.LoadImageW(
                None, str(path), image_icon,
                user32.GetSystemMetrics(49), user32.GetSystemMetrics(50),
                load_from_file,
            ) or 0)
            if not large or not small:
                for handle in {large, small} - {0}:
                    user32.DestroyIcon(handle)
                return False
            state = {
                "path": str(path),
                "large": large,
                "small": small,
                "hwnd": 0,
                "previous_large": 0,
                "previous_small": 0,
            }
            window._serial_bridge_native_icons = state

        if state["hwnd"] != hwnd:
            state["hwnd"] = hwnd
            state["previous_large"] = int(
                user32.SendMessageW(hwnd, 0x0080, 1, state["large"]) or 0
            )
            state["previous_small"] = int(
                user32.SendMessageW(hwnd, 0x0080, 0, state["small"]) or 0
            )
        else:
            user32.SendMessageW(hwnd, 0x0080, 1, state["large"])
            user32.SendMessageW(hwnd, 0x0080, 0, state["small"])
        return True
    except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
        return False


def release_windows_taskbar_icon(window) -> None:
    """Restore prior window icons and free HICONs loaded by this module."""
    state = getattr(window, "_serial_bridge_native_icons", None)
    if not state:
        return
    try:
        del window._serial_bridge_native_icons
    except AttributeError:
        pass
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.IsWindow.argtypes = [ctypes.c_void_p]
        user32.IsWindow.restype = ctypes.c_int
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
        ]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        user32.DestroyIcon.restype = ctypes.c_int
        hwnd = int(state.get("hwnd", 0))
        if hwnd and user32.IsWindow(hwnd):
            user32.SendMessageW(
                hwnd, 0x0080, 1, int(state.get("previous_large", 0)))
            user32.SendMessageW(
                hwnd, 0x0080, 0, int(state.get("previous_small", 0)))
        for handle in {int(state["large"]), int(state["small"])} - {0}:
            user32.DestroyIcon(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def apply_window_icon(window) -> bool:
    """Apply the bundled icon to both Tk and Windows taskbar surfaces."""
    path = find_app_icon_path()
    if path is None:
        return False

    applied = False
    try:
        window.iconbitmap(default=str(path))
        applied = True
    except (AttributeError, OSError, tk.TclError):
        pass

    try:
        from PIL import Image, ImageTk

        with Image.open(path) as source:
            photo = ImageTk.PhotoImage(source.convert("RGBA"), master=window)
        window.iconphoto(True, photo)
        window._serial_bridge_icon_photo = photo
        applied = True
    except (AttributeError, ImportError, OSError, tk.TclError):
        pass

    if sys.platform == "win32":
        try:
            if not getattr(window, "_serial_bridge_icon_events_bound", False):
                def sync_on_map(event, target=window):
                    if event.widget is target:
                        sync_windows_taskbar_icon(target)

                def release_on_destroy(event, target=window):
                    if event.widget is target:
                        release_windows_taskbar_icon(target)

                window.bind("<Map>", sync_on_map, add="+")
                window.bind("<Destroy>", release_on_destroy, add="+")
                window._serial_bridge_icon_events_bound = True
            if window.winfo_ismapped():
                applied = sync_windows_taskbar_icon(window) or applied
        except (AttributeError, tk.TclError):
            pass
    return applied

# Populated by _apply_tokens() below.
STATE_COLORS = {}


def apply_workbench_theme(root):
    """Apply the CURRENT palette to a root or toplevel and return the style."""
    style = ttk.Style(root)
    theme_changed = False
    for preferred in ("clam", "alt", "default"):
        if preferred in style.theme_names():
            if style.theme_use() != preferred:
                style.theme_use(preferred)
                theme_changed = True
            break

    if isinstance(root, tk.Tk):
        root.configure(bg=APP_BG)
    else:
        root.configure(bg=PANEL_BG)

    style.configure(".", font=BODY_FONT, background=PANEL_BG, foreground=TEXT)
    style.configure("Workbench.TFrame", background=APP_BG)
    style.configure("Panel.TFrame", background=PANEL_BG)
    style.configure("Sidebar.TFrame", background=SIDEBAR_BG)
    style.configure("Card.TFrame", background=CARD_BG)
    style.configure("Topbar.TFrame", background=APP_BG_2)

    style.configure("TLabel", background=PANEL_BG, foreground=TEXT)
    style.configure("Title.TLabel", background=PANEL_BG, foreground=TEXT, font=TITLE_FONT)
    style.configure("Heading.TLabel", background=PANEL_BG, foreground=TEXT, font=HEADING_FONT)
    style.configure("Section.TLabel", background=CARD_BG, foreground=TEXT, font=SECTION_FONT)
    style.configure("Muted.TLabel", background=PANEL_BG, foreground=TEXT_MUTED, font=SMALL_FONT)
    style.configure("Faint.TLabel", background=PANEL_BG, foreground=TEXT_FAINT, font=TINY_FONT)
    style.configure("Mono.TLabel", background=PANEL_BG, foreground=TEXT, font=MONO_SMALL)
    style.configure("Sidebar.TLabel", background=SIDEBAR_BG, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD_BG, foreground=TEXT)
    style.configure("CardMuted.TLabel", background=CARD_BG, foreground=TEXT_MUTED, font=SMALL_FONT)

    style.configure("TEntry", fieldbackground=CARD_BG, foreground=TEXT,
                    insertcolor=TEXT, padding=ENTRY_PAD)
    style.configure("TCombobox", fieldbackground=CARD_BG, foreground=TEXT,
                    padding=5, arrowcolor=TEXT)
    style.map("TCombobox",
              fieldbackground=[("readonly", CARD_BG), ("!disabled", CARD_BG)],
              foreground=[("readonly", TEXT), ("!disabled", TEXT)])

    style.configure("TButton", padding=BUTTON_PAD, font=BODY_FONT,
                    background=CARD_BG, foreground=TEXT)
    style.map(
        "TButton",
        background=[("disabled", PANEL_BG), ("active", SIDEBAR_HOVER), ("!disabled", CARD_BG)],
        foreground=[("disabled", TEXT_MUTED), ("!disabled", TEXT)],
    )
    style.configure(
        "Accent.TButton",
        padding=BUTTON_PAD,
        font=(FONT_FAMILY, BODY_FONT[1], "bold"),
        background=BLUE,
        foreground=TEXT_ON_ACCENT,
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", GRAY_SOFT), ("active", _blend(BLUE, "#000000", 0.15)),
                    ("!disabled", BLUE)],
        foreground=[("disabled", TEXT_MUTED), ("!disabled", TEXT_ON_ACCENT)],
    )
    style.configure("Danger.TButton", padding=BUTTON_PAD, font=BODY_FONT,
                    background=RED, foreground=TEXT_ON_ACCENT)
    style.map(
        "Danger.TButton",
        background=[("disabled", GRAY_SOFT), ("active", _blend(RED, "#000000", 0.15)),
                    ("!disabled", RED)],
        foreground=[("disabled", TEXT_MUTED), ("!disabled", TEXT_ON_ACCENT)],
    )
    style.configure("TScrollbar", background=SIDEBAR_LINE, troughcolor=PANEL_BG,
                    arrowcolor=TEXT_MUTED)

    # theme_use() queues ttk::ThemeChanged as idle work. Process it while
    # this Tk application still exists so immediate teardown cannot leak the
    # callback into the next root created in the same process.
    if theme_changed:
        try:
            root.update_idletasks()
        except tk.TclError:
            pass
    return style


def status_state(snapshot):
    """Map a session snapshot to a canonical state key."""
    if getattr(snapshot, "running", False):
        return "running"
    if getattr(snapshot, "starting", False):
        return "starting"
    text = (getattr(snapshot, "status_text", "") or "").casefold()
    if text == "stopped":
        return "stopped"
    if getattr(snapshot, "last_error", None):
        return "error"
    if "error" in text or "failed" in text:
        return "error"
    if "stopping" in text:
        return "stopping"
    if "starting" in text:
        return "starting"
    return "stopped"


_apply_tokens("dark", "blue")
_apply_font_tokens(*FONT_SIZE_DEFAULTS.values())

"""Visual constants and ttk styles for Serial Bridge Manager.

The application intentionally uses one polished light theme that matches
the confirmed mockup.  Keeping the visual system small helps this utility
stay lightweight and predictable.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ── Palette ──────────────────────────────────────────────────────────

APP_BG = "#e9eef3"
GRID = "#dfe7ee"
WINDOW_BG = "#ffffff"
WINDOW_BORDER = "#cfd8e3"
CARD_BG = "#ffffff"
CARD_ALT = "#f7faff"
CARD_BORDER = "#d6e0ea"
FIELD_BG = "#f8fafc"

TEXT = "#162033"
TEXT_SECONDARY = "#536173"
TEXT_MUTED = "#718197"
BLUE = "#2f6be6"
BLUE_SOFT = "#eaf2ff"
GREEN = "#16864a"
GREEN_SOFT = "#d8f8e4"
CYAN = "#17a7e8"
PURPLE = "#7c3aed"
ORANGE = "#f59e0b"
RED = "#ef4444"
YELLOW = "#fbbf24"

EVENT_BG = "#111827"
EVENT_TEXT = "#e5edf5"
EVENT_MUTED = "#a9b5c4"

TERM_BG = "#070b10"
TERM_PANEL = "#0f1720"
TERM_BORDER = "#283445"
TERM_TEXT = "#d7e0ea"
TERM_MUTED = "#93a1b2"
TERM_GREEN = "#67d391"
TERM_BLUE = "#38bdf8"

# ANSI terminal colors
ANSI = {
    "ansi_black": "#45475a",
    "ansi_red": "#f38ba8",
    "ansi_green": "#67d391",
    "ansi_yellow": "#facc15",
    "ansi_blue": "#60a5fa",
    "ansi_magenta": "#c084fc",
    "ansi_cyan": "#38bdf8",
    "ansi_white": "#d7e0ea",
    "ansi_bright_black": "#93a1b2",
    "ansi_bright_red": "#fb7185",
    "ansi_bright_green": "#86efac",
    "ansi_bright_yellow": "#fde68a",
    "ansi_bright_blue": "#93c5fd",
    "ansi_bright_magenta": "#d8b4fe",
    "ansi_bright_cyan": "#67e8f9",
    "ansi_bright_white": "#f8fafc",
}

CLIENT_COLOR_HEX = {
    "yellow": ORANGE,
    "cyan": CYAN,
    "magenta": PURPLE,
    "blue": BLUE,
    "white": TEXT_SECONDARY,
    "bright_red": RED,
    "bright_green": GREEN,
    "bright_blue": BLUE,
    "green": GREEN,
}

# ── Fonts ────────────────────────────────────────────────────────────

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"

TITLE_FONT = (FONT_FAMILY, 20, "bold")
SECTION_FONT = (FONT_FAMILY, 13)
BODY_FONT = (FONT_FAMILY, 10)
SMALL_FONT = (FONT_FAMILY, 8)
TINY_FONT = (FONT_FAMILY, 7)
MONO_FONT = (FONT_MONO, 9)
MONO_SMALL = (FONT_MONO, 8)

# Compatibility names for older code/tests.
BG_PRIMARY = WINDOW_BG
BG_SECONDARY = FIELD_BG
FG_PRIMARY = TEXT
FG_SECONDARY = TEXT_SECONDARY
FG_MUTED = TEXT_MUTED
FG_ACCENT = BLUE
STATUS_RUNNING = GREEN
STATUS_STOPPED = TEXT_MUTED
STATUS_ERROR = RED
HEADING_FONT = TITLE_FONT

ANSI_COLOR_MAP: dict[str, str] = {
    "30": "ansi_black",
    "31": "ansi_red",
    "32": "ansi_green",
    "33": "ansi_yellow",
    "34": "ansi_blue",
    "35": "ansi_magenta",
    "36": "ansi_cyan",
    "37": "ansi_white",
    "90": "ansi_bright_black",
    "91": "ansi_bright_red",
    "92": "ansi_bright_green",
    "93": "ansi_bright_yellow",
    "94": "ansi_bright_blue",
    "95": "ansi_bright_magenta",
    "96": "ansi_bright_cyan",
    "97": "ansi_bright_white",
}


def apply_theme(root: tk.Misc) -> ttk.Style:
    """Apply the fixed app theme to a root or toplevel."""
    style = ttk.Style(root)
    for preferred in ("clam", "alt", "default"):
        if preferred in style.theme_names():
            style.theme_use(preferred)
            break

    root.configure(bg=APP_BG if isinstance(root, tk.Tk) else TERM_PANEL)

    style.configure(".", font=BODY_FONT, background=WINDOW_BG, foreground=TEXT)
    style.configure("App.TFrame", background=APP_BG)
    style.configure("Window.TFrame", background=WINDOW_BG)
    style.configure("Card.TFrame", background=CARD_BG)
    style.configure("AltCard.TFrame", background=CARD_ALT)
    style.configure("Terminal.TFrame", background=TERM_PANEL)

    style.configure("TLabel", background=WINDOW_BG, foreground=TEXT)
    style.configure("Title.TLabel", background=WINDOW_BG, foreground=TEXT, font=TITLE_FONT)
    style.configure(
        "Subtitle.TLabel",
        background=WINDOW_BG,
        foreground=TEXT_SECONDARY,
        font=BODY_FONT,
    )
    style.configure("Section.TLabel", background=CARD_BG, foreground=TEXT, font=SECTION_FONT)
    style.configure("Muted.TLabel", background=CARD_BG, foreground=TEXT_MUTED, font=SMALL_FONT)
    style.configure("Terminal.TLabel", background=TERM_PANEL, foreground=TERM_TEXT)

    style.configure("TEntry", fieldbackground=FIELD_BG, foreground=TEXT, padding=6)
    style.configure("TCombobox", fieldbackground=FIELD_BG, foreground=TEXT, padding=5)

    style.configure("TButton", padding=(14, 6), font=BODY_FONT)
    style.configure("Primary.TButton", padding=(18, 8), font=(FONT_FAMILY, 10), foreground="#ffffff")
    style.map(
        "Primary.TButton",
        background=[("active", "#14743f"), ("!disabled", GREEN)],
        foreground=[("!disabled", "#ffffff")],
    )
    style.configure("Ghost.TButton", padding=(14, 7), font=BODY_FONT)
    style.configure("Small.TButton", padding=(8, 4), font=SMALL_FONT)

    style.configure("TSeparator", background=CARD_BORDER)
    return style


def current_theme() -> str:
    return "light"


def toggle_theme(root: tk.Misc) -> str:
    """Compatibility shim: keep the fixed light theme."""
    apply_theme(root)
    return "light"


def configure_terminal_tags(text_widget: tk.Text) -> None:
    """Register ANSI color tags on a terminal text widget."""
    for tag_name, hex_color in ANSI.items():
        text_widget.tag_configure(tag_name, foreground=hex_color)
    text_widget.tag_configure("bold", font=(FONT_MONO, 9, "bold"))
    text_widget.tag_configure("italic", font=(FONT_MONO, 9, "italic"))
    text_widget.tag_configure("underline", underline=True)
    text_widget.tag_configure("dim", foreground=TERM_MUTED)

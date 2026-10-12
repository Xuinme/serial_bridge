"""ANSI SGR (Select Graphic Rendition) parser for tkinter Text widgets.

Converts CSI-prefixed escape sequences into ``(text, tags)`` tokens
that can be inserted into a ``tk.Text`` widget with tag-based coloring.

Supports the subset used by the serial bridge: basic 3-bit and
bright foreground colors, plus reset.

Usage::

    parser = AnsiParser()
    for text, tags in parser.feed(b"\\033[0;33mhello\\033[0m"):
        text_widget.insert(tk.END, text, tags)
"""

from __future__ import annotations

import re
from typing import Iterator

from .theme import ANSI_COLOR_MAP

# Regex to match CSI SGR sequences: ESC [ ... m
_SGR_RE = re.compile(rb"\x1b\[([\d;]*)m")


class AnsiParser:
    """Stateful parser that converts ANSI-escaped bytes into tagged text.

    Call ``feed(data)`` with raw bytes; it returns a list of
    ``(text: str, tags: tuple[str, ...])`` ready for ``Text.insert()``.
    """

    def __init__(self):
        self._buf = bytearray()
        self._current_tags: tuple[str, ...] = ()
        # Track whether we have a partial escape at the end
        self._partial = False

    def feed(self, data: bytes) -> list[tuple[str, tuple[str, ...]]]:
        """Feed raw bytes, return tagged-text tokens."""
        self._buf.extend(data)
        raw = bytes(self._buf)
        tokens: list[tuple[str, tuple[str, ...]]] = []

        pos = 0
        while pos < len(raw):
            # Find next escape
            m = _SGR_RE.search(raw, pos)
            if m is None:
                # No complete SGR sequence found.
                # Check for a partial escape at the end (ESC with no 'm').
                esc_pos = raw.find(b"\x1b", pos)
                if esc_pos >= 0:
                    # Emit everything before the partial escape
                    if esc_pos > pos:
                        text = raw[pos:esc_pos].decode("utf-8", errors="replace")
                        if text:
                            tokens.append((text, self._current_tags))
                    # Keep the partial escape in the buffer for next feed
                    self._buf = bytearray(raw[esc_pos:])
                    return tokens
                else:
                    # No escape at all — emit everything
                    text = raw[pos:].decode("utf-8", errors="replace")
                    if text:
                        tokens.append((text, self._current_tags))
                    self._buf.clear()
                    return tokens

            start = m.start()
            end = m.end()

            # Emit text before the escape
            if start > pos:
                text = raw[pos:start].decode("utf-8", errors="replace")
                if text:
                    tokens.append((text, self._current_tags))

            # Parse SGR parameters
            params_str = m.group(1).decode("ascii", errors="replace")
            self._current_tags = _parse_sgr(params_str, self._current_tags)

            pos = end

        self._buf.clear()
        return tokens

    def reset(self) -> None:
        """Reset parser state (call on disconnect)."""
        self._buf.clear()
        self._current_tags = ()


def _parse_sgr(
    params_str: str, current: tuple[str, ...]
) -> tuple[str, ...]:
    """Parse a single SGR parameter string and return updated tags."""
    if not params_str:
        params_str = "0"

    tags: set[str] = set(current)
    parts = params_str.split(";")

    i = 0
    while i < len(parts):
        try:
            p = int(parts[i])
        except ValueError:
            i += 1
            continue

        if p == 0:
            # Reset all attributes
            tags.clear()
        elif p == 1:
            tags.add("bold")
        elif p == 3:
            tags.add("italic")
        elif p == 4:
            tags.add("underline")
        elif p == 22:
            tags.discard("bold")
        elif p == 23:
            tags.discard("italic")
        elif p == 24:
            tags.discard("underline")
        elif 30 <= p <= 37 or 90 <= p <= 97:
            # Foreground color — remove previous color tags first
            tags = {t for t in tags if not t.startswith("ansi_")}
            tag = ANSI_COLOR_MAP.get(str(p))
            if tag:
                tags.add(tag)
        elif p == 39:
            # Default foreground
            tags = {t for t in tags if not t.startswith("ansi_")}
        elif p == 38 and i + 2 < len(parts) and parts[i + 1] == "5":
            # 256-color: 38;5;N (not fully supported — just keep existing)
            i += 2
        elif p == 38 and i + 3 < len(parts) and parts[i + 1] == "2":
            # 24-bit color: 38;2;R;G;B (not supported)
            i += 3

        i += 1

    return tuple(sorted(tags))

"""
Unit tests for the ANSI SGR parser used by the Built-in Telnet terminal.
"""
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

# ``ui.ansi`` imports ``ANSI_COLOR_MAP`` from ``ui.theme``, which pulls in
# tkinter at module level even though the parser itself never uses tkinter.
# On a headless/CI host without tkinter, provide minimal inert stub modules
# so this pure-logic test can run.  On Windows (tkinter present) these are a
# no-op.
try:
    import tkinter  # noqa: F401
    import tkinter.ttk  # noqa: F401
except Exception:
    import types as _types

    _tk = _types.ModuleType("tkinter")
    _ttk = _types.ModuleType("tkinter.ttk")
    _ttk.Style = object
    _tk.ttk = _ttk
    _tk.Misc = object
    _tk.Tk = object
    _tk.Text = object
    _tk.END = "end"
    sys.modules["tkinter"] = _tk
    sys.modules["tkinter.ttk"] = _ttk

from ui.ansi import AnsiParser, _parse_sgr


def test_no_escape():
    """Plain text passes through unchanged."""
    parser = AnsiParser()
    tokens = parser.feed(b"hello world")
    assert len(tokens) == 1
    assert tokens[0][0] == "hello world"
    assert tokens[0][1] == ()


def test_yellow():
    """ANSI yellow (33) → ansi_yellow tag."""
    parser = AnsiParser()
    tokens = parser.feed(b"\033[0;33mhello\033[0m")
    assert len(tokens) >= 1
    assert any(t == "hello" and "ansi_yellow" in tags for t, tags in tokens)


def test_cyan():
    """ANSI cyan (36) → ansi_cyan tag."""
    parser = AnsiParser()
    tokens = parser.feed(b"\033[0;36mcyan text\033[0m")
    assert any(t == "cyan text" and "ansi_cyan" in tags for t, tags in tokens)


def test_magenta():
    """ANSI magenta (35) → ansi_magenta tag."""
    parser = AnsiParser()
    tokens = parser.feed(b"\033[0;35mmagenta\033[0m")
    assert any(t == "magenta" and "ansi_magenta" in tags for t, tags in tokens)


def test_reset():
    """ANSI reset (0) clears tags."""
    parser = AnsiParser()
    tokens = parser.feed(b"\033[0;33myellow\033[0mplain")
    # Last token should be "plain" with no color tags
    last = tokens[-1]
    assert last[0] == "plain"
    assert not any(t.startswith("ansi_") for t in last[1])


def test_mixed_colors():
    """Multiple color sequences in one feed."""
    parser = AnsiParser()
    tokens = parser.feed(
        b"\033[0;32mgreen\033[0m \033[0;31mred\033[0m \033[0;34mblue\033[0m"
    )
    colors_seen = set()
    for text, tags in tokens:
        for t in tags:
            if t.startswith("ansi_"):
                colors_seen.add(t)
    assert "ansi_green" in colors_seen
    assert "ansi_red" in colors_seen
    assert "ansi_blue" in colors_seen


def test_plain_text_no_tags():
    """Text without ANSI sequences should have empty tags."""
    parser = AnsiParser()
    tokens = parser.feed(b"COM9: ready\r\n")
    for text, tags in tokens:
        # No ANSI color tags
        assert not any(t.startswith("ansi_") for t in tags)


def test_incomplete_sequence():
    """Partial ANSI at end should not crash."""
    parser = AnsiParser()
    # Feed partial escape
    tokens = parser.feed(b"text \033[0;3")
    # Should produce text up to the partial escape
    assert len(tokens) >= 1
    assert "text " in tokens[0][0]

    # Feed the rest
    tokens = parser.feed(b"3myellow")
    assert any("ansi_yellow" in tags for _, tags in tokens)


def test_bright_colors():
    """Bright colors (90-97) map correctly."""
    parser = AnsiParser()
    tokens = parser.feed(b"\033[0;91mbright red\033[0m")
    assert any(
        t == "bright red" and "ansi_bright_red" in tags
        for t, tags in tokens
    )


def test_bold_attribute():
    """SGR 1 should add bold tag."""
    parser = AnsiParser()
    tokens = parser.feed(b"\033[1mbold text\033[0m")
    assert any("bold" in tags for _, tags in tokens)


def test_reset_clears_state():
    """parser.reset() should clear all state."""
    parser = AnsiParser()
    parser.feed(b"\033[0;33mhello")
    parser.reset()
    tokens = parser.feed(b"world")
    assert tokens[0][0] == "world"
    assert tokens[0][1] == ()


def test_bridge_color_output():
    """Simulate actual bridge output format."""
    parser = AnsiParser()
    # Bridge output: [Client-1] \033[0;33mdata\033[0m\r\n
    data = b"[Client-1] \033[0;33mAT+VERSION\033[0m\r\n"
    tokens = parser.feed(data)
    texts = [t for t, _ in tokens]
    combined = "".join(texts)
    assert "AT+VERSION" in combined
    assert "Client-1" in combined


def test_empty_feed():
    """Empty feed should return empty list."""
    parser = AnsiParser()
    tokens = parser.feed(b"")
    assert tokens == []


def test_sgr_parse_reset():
    """_parse_sgr with 0 resets all tags."""
    result = _parse_sgr("0", ("ansi_yellow", "bold"))
    assert result == ()


def test_sgr_parse_color_switch():
    """_parse_sgr switches from one color to another."""
    result = _parse_sgr("31", ("ansi_green",))
    assert "ansi_red" in result
    assert "ansi_green" not in result


# ── Runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    tests = [
        test_no_escape,
        test_yellow,
        test_cyan,
        test_magenta,
        test_reset,
        test_mixed_colors,
        test_plain_text_no_tags,
        test_incomplete_sequence,
        test_bright_colors,
        test_bold_attribute,
        test_reset_clears_state,
        test_bridge_color_output,
        test_empty_feed,
        test_sgr_parse_reset,
        test_sgr_parse_color_switch,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  [PASS] {test.__name__}")
            passed += 1
        except Exception:
            print(f"  [FAIL] {test.__name__}")
            traceback.print_exc()
            failed += 1

    print(f"\nRESULTS: {passed} passed, {failed} failed, {len(tests)} total")
    sys.exit(0 if failed == 0 else 1)

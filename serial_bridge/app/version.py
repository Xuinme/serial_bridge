"""Application identity loaded from the repository's VERSION file."""

from __future__ import annotations

import re
import sys
from pathlib import Path


APP_DISPLAY_NAME = "串口桥接"
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def _version_file_candidates() -> tuple[Path, ...]:
    """Return source-tree and packaged locations without depending on cwd."""
    return (
        Path(__file__).resolve().parents[2] / "VERSION",
        Path(sys.executable).resolve().parent / "VERSION",
    )


def _load_app_version() -> str:
    for path in _version_file_candidates():
        try:
            value = path.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if _VERSION_PATTERN.fullmatch(value):
            return value
    return "未知"


APP_VERSION = _load_app_version()
APP_VERSION_LABEL = f"{APP_DISPLAY_NAME} v{APP_VERSION}"

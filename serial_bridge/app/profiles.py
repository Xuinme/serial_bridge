"""Settings persistence via a local JSON file.

Writes to ``%LOCALAPPDATA%\\SerialBridge\\settings.json`` by default,
with a fallback to ``~/.serial_bridge/settings.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import (
    VALID_ACCENT_COLORS,
    VALID_THEME_MODES,
    FONT_SIZE_DEFAULTS,
    FONT_SIZE_RANGES,
    AppSettings,
    BridgeConfig,
    SessionConfig,
)

#: Only these internal values may be persisted for ``close_action``.
VALID_CLOSE_ACTIONS = ("ask", "minimize_to_tray", "exit")


def _settings_dir() -> Path:
    """Return the settings directory, creating it if necessary."""
    candidates: list[Path] = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "SerialBridge")
    candidates.append(Path.home() / ".serial_bridge")
    candidates.append(Path.cwd() / ".serial_bridge")

    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            try:
                probe.unlink()
            except OSError:
                pass
            return base
        except OSError:
            continue

    # Last resort: return cwd-relative path.  Callers still catch OSError.
    return Path.cwd() / ".serial_bridge"


def _settings_path() -> Path:
    return _settings_dir() / "settings.json"


def load_settings() -> BridgeConfig:
    """Load saved configuration or return defaults."""
    path = _settings_path()
    cfg = BridgeConfig()
    try:
        if not path.exists():
            return cfg
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return cfg

    cfg.com_port = data.get("last_com_port", cfg.com_port)
    cfg.baud_rate = data.get("baud_rate", cfg.baud_rate)
    cfg.tcp_host = data.get("tcp_host", cfg.tcp_host)
    cfg.tcp_port = data.get("tcp_port", cfg.tcp_port)
    cfg.newline_mode = data.get("newline_mode", cfg.newline_mode)

    # close_action: strictly whitelisted; invalid/absent values fall back to "ask".
    raw_close = data.get("close_action", cfg.close_action)
    if isinstance(raw_close, str) and raw_close in VALID_CLOSE_ACTIONS:
        cfg.close_action = raw_close
    else:
        cfg.close_action = "ask"
    return cfg


def save_settings(config: BridgeConfig) -> None:
    """Persist current configuration."""
    data = {
        "last_com_port": config.com_port,
        "baud_rate": config.baud_rate,
        "tcp_host": config.tcp_host,
        "tcp_port": config.tcp_port,
        "newline_mode": config.newline_mode,
        "close_action": config.close_action if config.close_action in VALID_CLOSE_ACTIONS else "ask",
    }
    try:
        _settings_path().write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # non-critical


def default_logs_dir() -> Path:
    """Return the default communication-log path without creating it."""
    return _settings_path().parent / "logs"


def logs_dir() -> Path:
    p = default_logs_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p

# ---- Multi-session (v2) settings --------------------------------------
# TASK-007: keep the legacy single-config API (load_settings/save_settings)
# intact while adding an idempotent, atomic v2 persistence layer.

#: Stable id used for the single session produced when migrating a legacy
#: single-port config, or when no config file exists (an editable default).
DEFAULT_SESSION_ID = "default"


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_choice(value, allowed, default):
    """Accept only exact lowercase members of *allowed*; else *default*."""
    if isinstance(value, str) and value in allowed:
        return value
    return default


def _normalize_font_size(name, value):
    default = FONT_SIZE_DEFAULTS[name]
    low, high = FONT_SIZE_RANGES[name]
    parsed = _as_int(value, default)
    return parsed if low <= parsed <= high else default


def _font_values(data):
    """Read four font groups, including the short-lived preset draft."""
    legacy = {
        "small": (14, 9, 8, 9),
        "medium": (16, 10, 9, 10),
        "large": (18, 12, 11, 12),
    }.get(data.get("font_size"))
    defaults = legacy or tuple(FONT_SIZE_DEFAULTS.values())
    names = tuple(FONT_SIZE_DEFAULTS)
    return {
        name: _normalize_font_size(name, data.get(name, defaults[index]))
        for index, name in enumerate(names)
    }


def _default_session() -> SessionConfig:
    """Return the single editable default session (no COM yet)."""
    return SessionConfig(
        session_id=DEFAULT_SESSION_ID,
        name="Connection 1",
        com_port="",
        baud_rate=115200,
        tcp_port=9999,
        newline_mode="crlf",
        auto_reconnect=False,
    )


def _default_app_settings() -> AppSettings:
    """Return v2 settings holding exactly one editable default session."""
    return AppSettings(
        schema_version=2,
        close_action="ask",
        active_session_id=None,
        sessions=[_default_session()],
        theme_mode="dark",
        accent_color="blue",
        traffic_log_enabled=False,
        traffic_log_directory="",
        **FONT_SIZE_DEFAULTS,
    )


def _from_v1(data) -> AppSettings:
    """Migrate a legacy single-port config dict into v2 settings."""
    if not isinstance(data, dict):
        return _default_app_settings()
    close_action = data.get("close_action", "ask")
    if close_action not in VALID_CLOSE_ACTIONS:
        close_action = "ask"
    session = SessionConfig(
        session_id=DEFAULT_SESSION_ID,
        name="Connection 1",
        com_port=str(data.get("last_com_port", "")),
        baud_rate=_as_int(data.get("baud_rate"), 115200),
        tcp_port=_as_int(data.get("tcp_port"), 9999),
        newline_mode=str(data.get("newline_mode", "crlf")),
        auto_reconnect=False,
    )
    fonts = _font_values(data)
    return AppSettings(
        schema_version=2,
        close_action=close_action,
        active_session_id=None,
        sessions=[session],
        theme_mode=_normalize_choice(data.get("theme_mode"), VALID_THEME_MODES, "dark"),
        accent_color=_normalize_choice(data.get("accent_color"), VALID_ACCENT_COLORS, "blue"),
        traffic_log_enabled=False,
        traffic_log_directory="",
        **fonts,
    )


def _from_v2(data) -> AppSettings:
    """Parse an already-versioned v2 settings dict."""
    close_action = data.get("close_action", "ask")
    if close_action not in VALID_CLOSE_ACTIONS:
        close_action = "ask"
    active_session_id = data.get("active_session_id")
    sessions = []
    for i, item in enumerate(data.get("sessions", [])):
        if not isinstance(item, dict):
            continue
        sessions.append(SessionConfig(
            session_id=str(item.get("session_id") or f"session-{i}"),
            name=str(item.get("name", "Connection")),
            com_port=str(item.get("com_port", "")),
            baud_rate=_as_int(item.get("baud_rate"), 115200),
            tcp_port=_as_int(item.get("tcp_port"), 9999),
            newline_mode=str(item.get("newline_mode", "crlf")),
            auto_reconnect=bool(item.get("auto_reconnect", False)),
        ))
    if not sessions:
        sessions = [_default_session()]
    fonts = _font_values(data)
    return AppSettings(
        schema_version=2,
        close_action=close_action,
        active_session_id=active_session_id,
        sessions=sessions,
        theme_mode=_normalize_choice(data.get("theme_mode"), VALID_THEME_MODES, "dark"),
        accent_color=_normalize_choice(data.get("accent_color"), VALID_ACCENT_COLORS, "blue"),
        traffic_log_enabled=(data.get("traffic_log_enabled") is True),
        traffic_log_directory=(
            str(data.get("traffic_log_directory", "")).strip()
            if data.get("traffic_log_directory") is not None else ""
        ),
        **fonts,
    )


def _app_settings_to_dict(settings: AppSettings) -> dict:
    """Serialize v2 settings to the on-disk dict shape."""
    return {
        "schema_version": 2,
        "close_action": (
            settings.close_action
            if settings.close_action in VALID_CLOSE_ACTIONS
            else "ask"
        ),
        "active_session_id": settings.active_session_id,
        "theme_mode": _normalize_choice(
            settings.theme_mode, VALID_THEME_MODES, "dark"
        ),
        "accent_color": _normalize_choice(
            settings.accent_color, VALID_ACCENT_COLORS, "blue"
        ),
        "traffic_log_enabled": bool(settings.traffic_log_enabled),
        "traffic_log_directory": str(settings.traffic_log_directory or "").strip(),
        **{
            name: _normalize_font_size(name, getattr(settings, name, default))
            for name, default in FONT_SIZE_DEFAULTS.items()
        },
        "sessions": [
            {
                "session_id": s.session_id,
                "name": s.name,
                "com_port": s.com_port,
                "baud_rate": s.baud_rate,
                "tcp_port": s.tcp_port,
                "newline_mode": s.newline_mode,
                "auto_reconnect": bool(s.auto_reconnect),
            }
            for s in settings.sessions
        ],
    }


def _atomic_write(path: Path, value: dict) -> None:
    """Write JSON atomically: temp file in the same dir, then os.replace."""
    payload = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="settings", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _maybe_backup(path: Path, text: str) -> None:
    """Atomically create a one-time backup before overwriting a legacy file.

    Backup failure is fatal: callers must not replace the source settings
    unless a complete recovery copy exists.
    """
    backup = path.with_name(path.name + ".bak")
    if backup.exists():
        return
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=backup.name, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, backup)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_app_settings() -> AppSettings:
    """Load version 2 multi-session settings or return v2 defaults.

    A missing file returns a default AppSettings holding one editable session.
    A legacy (v1) dict is migrated to a single session on the fly.  Invalid
    JSON leaves the original file untouched and returns defaults.  Repeated
    calls on the same file never generate additional sessions.
    """
    path = _settings_path()
    if not path.exists():
        return _default_app_settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_app_settings()
    if isinstance(data, dict) and "schema_version" not in data:
        return _from_v1(data)
    if isinstance(data, dict) and data.get("schema_version") == 2:
        return _from_v2(data)
    raise ValueError("Unsupported settings schema_version")


def save_app_settings(settings: AppSettings) -> None:
    """Persist v2 settings atomically, without clobbering an invalid file.

    Before the first overwrite of a valid legacy file, a one-time recoverable
    backup is created.  If the existing file is not valid JSON, a ValueError is
    raised and the original file is left unchanged (never overwrite a source
    we cannot recover).
    """
    path = _settings_path()
    if path.exists():
        try:
            current_text = path.read_text(encoding="utf-8")
            current_data = json.loads(current_text)
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError("Refusing to overwrite an invalid settings file") from exc
        if not isinstance(current_data, dict):
            raise ValueError("Refusing to overwrite a non-object settings file")
        if "schema_version" in current_data and current_data.get("schema_version") != 2:
            raise ValueError("Refusing to overwrite an unsupported settings version")
        if "schema_version" not in current_data:
            _maybe_backup(path, current_text)
    _atomic_write(path, _app_settings_to_dict(settings))

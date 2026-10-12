"""Asynchronous raw serial traffic logging.

The bridge thread only copies bytes into a bounded queue.  A dedicated writer
thread owns the file handle, formatting and rotation so slow storage never
blocks serial I/O.
"""

from __future__ import annotations

import json
import os
import queue
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 4
DEFAULT_QUEUE_SIZE = 8192
_STOP = object()


def prepare_log_directory(value: str | os.PathLike[str]) -> Path:
    """Create and verify a writable absolute log directory."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("请选择通信日志保存目录。")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    fd, probe = tempfile.mkstemp(dir=str(path), prefix=".serial-bridge-", suffix=".tmp")
    os.close(fd)
    try:
        Path(probe).unlink()
    except OSError:
        pass
    return path


def _safe_component(value: object, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    text = text.strip("._-")[:48]
    return text or fallback


@dataclass(frozen=True)
class _TrafficRecord:
    timestamp: datetime
    direction: str
    data: bytes
    source: str


class TrafficLogWriter:
    """One controller's queue-backed, restartable communication log."""

    def __init__(self, *, enabled: bool = False, directory: str = "",
                 on_error: Callable[[str], None] | None = None,
                 max_bytes: int = DEFAULT_MAX_BYTES,
                 backup_count: int = DEFAULT_BACKUP_COUNT,
                 queue_size: int = DEFAULT_QUEUE_SIZE):
        self._enabled = bool(enabled)
        self._directory = str(directory or "").strip()
        self._on_error = on_error or (lambda _message: None)
        self._max_bytes = max(256, int(max_bytes))
        self._backup_count = max(0, int(backup_count))
        self._queue_size = max(1, int(queue_size))
        self._lock = threading.RLock()
        self._queue: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        self._active = False
        self._dropped = 0
        self._error_reported = False
        self._current_path: Path | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def current_path(self) -> Path | None:
        with self._lock:
            return self._current_path

    def configure(self, enabled: bool, directory: str) -> None:
        """Update future-run settings; stop an incompatible active writer."""
        clean_directory = str(directory or "").strip()
        with self._lock:
            changed = (
                bool(enabled) != self._enabled or
                clean_directory != self._directory
            )
            self._enabled = bool(enabled)
            self._directory = clean_directory
        if changed and self.active:
            self.stop()

    def start_run(self, *, session_id: str, session_name: str, com_port: str,
                  baud_rate: int, tcp_port: int) -> Path | None:
        """Start a fresh per-run log when enabled; idempotent while active."""
        with self._lock:
            if self._active:
                return self._current_path
            if not self._enabled:
                return None
            directory = self._directory
            self._error_reported = False

        try:
            base = prepare_log_directory(directory)
        except (OSError, ValueError) as exc:
            self._report_error(f"无法启用通信日志：{exc}")
            return None

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        filename = "{}_{}_{}_{}.log".format(
            stamp,
            _safe_component(session_name, "connection"),
            _safe_component(com_port, "serial"),
            _safe_component(session_id, "session"),
        )
        path = base / filename
        metadata = {
            "started": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "session_id": str(session_id),
            "session_name": str(session_name),
            "com_port": str(com_port),
            "baud_rate": int(baud_rate),
            "tcp_port": int(tcp_port),
        }
        records: queue.Queue = queue.Queue(maxsize=self._queue_size)
        thread = threading.Thread(
            target=self._run,
            args=(records, path, metadata),
            daemon=True,
            name="traffic-log-{}".format(_safe_component(session_id, "session")),
        )
        with self._lock:
            self._queue = records
            self._thread = thread
            self._active = True
            self._dropped = 0
            self._current_path = path
        try:
            thread.start()
        except Exception as exc:  # noqa: BLE001 - logging must not stop the bridge
            with self._lock:
                if self._thread is thread:
                    self._queue = None
                    self._thread = None
                    self._active = False
                    self._current_path = None
            self._report_error(f"通信日志线程启动失败：{exc}")
            return None
        return path

    def record(self, direction: str, data: bytes, source: str = "") -> bool:
        """Queue an immutable byte snapshot without waiting for disk I/O."""
        if not data:
            return True
        with self._lock:
            if not self._active or self._queue is None:
                return False
            records = self._queue
        item = _TrafficRecord(
            timestamp=datetime.now().astimezone(),
            direction=str(direction),
            data=bytes(data),
            source=str(source or ""),
        )
        try:
            records.put_nowait(item)
            return True
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False

    def stop(self) -> None:
        """Drain queued records, close the file and join the writer thread."""
        with self._lock:
            records = self._queue
            thread = self._thread
            if records is None or thread is None:
                self._active = False
                return
            self._active = False
        if thread is threading.current_thread():
            return
        try:
            records.put(_STOP, timeout=2)
        except queue.Full:
            self._report_error("通信日志队列无法完成关闭。")
            try:
                records.get_nowait()
                records.put_nowait(_STOP)
            except (queue.Empty, queue.Full):
                pass
        thread.join(timeout=5)
        if thread.is_alive():
            self._report_error("通信日志线程未能在 5 秒内退出。")
        with self._lock:
            if self._thread is thread:
                self._queue = None
                self._thread = None

    def _run(self, records: queue.Queue, path: Path, metadata: dict) -> None:
        handle = None
        size = 0
        try:
            handle = path.open("a", encoding="utf-8", newline="\n")
            header = (
                "# Serial Bridge raw communication log\n"
                f"# metadata={json.dumps(metadata, ensure_ascii=False)}\n"
                "# warning=This file may contain commands, credentials and device data.\n"
            )
            handle.write(header)
            handle.flush()
            size = len(header.encode("utf-8"))
            last_flush = time.monotonic()

            while True:
                item = records.get()
                if item is _STOP:
                    break
                dropped = self._take_dropped()
                if dropped:
                    line = f"# dropped_records={dropped}\n"
                    handle, size = self._write_rotating(handle, path, size, line)
                line = self._format_record(item)
                handle, size = self._write_rotating(handle, path, size, line)
                now = time.monotonic()
                if records.empty() or now - last_flush >= 0.5:
                    handle.flush()
                    last_flush = now

            dropped = self._take_dropped()
            if dropped:
                handle, size = self._write_rotating(
                    handle, path, size, f"# dropped_records={dropped}\n")
            footer = "# stopped={}\n".format(
                datetime.now().astimezone().isoformat(timespec="milliseconds"))
            handle, _size = self._write_rotating(handle, path, size, footer)
            handle.flush()
        except Exception as exc:  # noqa: BLE001 - logging must not stop the bridge
            self._report_error(f"通信日志写入失败：{exc}")
            self._drain_after_failure(records)
        finally:
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            with self._lock:
                if self._queue is records:
                    self._active = False

    def _write_rotating(self, handle, path: Path, size: int, text: str):
        encoded_size = len(text.encode("utf-8"))
        if size > 0 and size + encoded_size > self._max_bytes:
            handle.close()
            self._rotate(path)
            handle = path.open("w", encoding="utf-8", newline="\n")
            continuation = "# continued={}\n".format(
                datetime.now().astimezone().isoformat(timespec="milliseconds"))
            handle.write(continuation)
            size = len(continuation.encode("utf-8"))
        handle.write(text)
        return handle, size + encoded_size

    def _rotate(self, path: Path) -> None:
        if self._backup_count <= 0:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        oldest = path.with_name(f"{path.name}.{self._backup_count}")
        try:
            oldest.unlink()
        except FileNotFoundError:
            pass
        for index in range(self._backup_count - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            target = path.with_name(f"{path.name}.{index + 1}")
            if source.exists():
                source.replace(target)
        if path.exists():
            path.replace(path.with_name(f"{path.name}.1"))

    @staticmethod
    def _format_record(item: _TrafficRecord) -> str:
        timestamp = item.timestamp.isoformat(timespec="milliseconds")
        direction = (
            item.direction if item.direction in ("SERIAL_TX", "SERIAL_RX")
            else "SERIAL_UNKNOWN"
        )
        source = json.dumps(item.source, ensure_ascii=False)
        text = json.dumps(
            item.data.decode("utf-8", errors="backslashreplace"),
            ensure_ascii=False,
        )
        return (
            f"[{timestamp}] [{direction}] source={source} len={len(item.data)} "
            f"hex={item.data.hex(' ').upper()} text={text}\n"
        )

    def _take_dropped(self) -> int:
        with self._lock:
            dropped = self._dropped
            self._dropped = 0
            return dropped

    @staticmethod
    def _drain_after_failure(records: queue.Queue) -> None:
        while True:
            try:
                if records.get_nowait() is _STOP:
                    return
            except queue.Empty:
                return

    def _report_error(self, message: str) -> None:
        with self._lock:
            if self._error_reported:
                return
            self._error_reported = True
        try:
            self._on_error(message)
        except Exception:
            pass

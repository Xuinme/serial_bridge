"""System tray icon manager for Serial Bridge Manager (Windows only).

Only the GUI path should import this module.  ``pystray`` and ``Pillow`` are
imported lazily inside :meth:`TrayIconManager.start`, so a headless CLI
invocation (or a GUI that never minimizes to tray) never requires them.

The tray callbacks (``on_restore`` / ``on_exit``) run on the tray thread and
must never touch tkinter directly; the caller is responsible for dispatching
them onto the Tk main thread (e.g. via ``root.after``).
"""

from __future__ import annotations

import threading
from pathlib import Path

from .workbench_theme import APP_DISPLAY_NAME

# Resolve relative to this file, never the CWD, and never an absolute repo path.
_DEFAULT_ICON = Path(__file__).resolve().parent.parent / "assets" / "serial_bridge.ico"


class TrayUnavailableError(RuntimeError):
    """Raised when the system tray icon cannot be created or started."""


class TrayIconManager:
    """Managed, idempotent pystray icon running on a named background thread.

    Public interface::

        tray = TrayIconManager(on_restore, on_exit, icon_path=None)
        tray.start()   # idempotent; raises TrayUnavailableError on failure
        tray.stop()    # idempotent; raises TrayUnavailableError if thread hangs
    """

    def __init__(self, on_restore, on_exit, icon_path=None):
        self._on_restore = on_restore
        self._on_exit = on_exit
        self._icon_path = Path(icon_path) if icon_path else _DEFAULT_ICON
        self._icon = None
        self._thread: threading.Thread | None = None
        self._image = None
        self._ready = threading.Event()
        self._failed: list[Exception] = []
        self._started = False

    @property
    def is_running(self) -> bool:
        return (
            self._started
            and self._thread is not None
            and self._thread.is_alive()
        )

    def start(self) -> None:
        """Create and start the tray icon.  Idempotent.

        Raises ``TrayUnavailableError`` if pystray/Pillow is missing, the icon
        file is absent, the tray thread errors, or the icon is not ready within
        3 seconds.  On failure any half-initialised state is cleaned up.
        """
        if self._started and self.is_running:
            return
        if self._thread is not None and self._thread.is_alive():
            raise TrayUnavailableError(
                "A previous tray icon thread is still running"
            )
        if self._started and not self.is_running:
            # A prior attempt started but its thread later died: reset.
            self._cleanup_refs()

        try:
            import pystray
            from PIL import Image
        except ImportError as exc:
            raise TrayUnavailableError(
                f"System tray is unavailable (pystray/Pillow missing): {exc}"
            ) from exc

        if not self._icon_path.exists():
            raise TrayUnavailableError(
                f"Tray icon not found: {self._icon_path}"
            )

        self._ready.clear()
        self._failed.clear()
        image = None
        try:
            image = Image.open(self._icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("打开窗口", self._restore_from_menu, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出应用", self._exit_from_menu),
            )
            icon = pystray.Icon("serial-bridge", image, APP_DISPLAY_NAME, menu)
        except Exception as exc:  # noqa: BLE001
            if image is not None:
                try:
                    image.close()
                except Exception:  # noqa: BLE001
                    pass
            raise TrayUnavailableError(f"Failed to create tray icon: {exc}") from exc

        def setup(ready_icon):
            # A custom pystray setup callback must make the icon visible.
            ready_icon.visible = True
            self._ready.set()

        thread = threading.Thread(
            target=self._run_icon,
            args=(icon, setup),
            name="tray-icon",
            daemon=True,
        )
        self._icon = icon
        self._image = image
        self._thread = thread
        thread.start()

        if not self._ready.wait(timeout=3.0):
            cleanup_error = self._stop_current()
            if cleanup_error is not None:
                raise cleanup_error
            raise TrayUnavailableError("Tray icon did not become ready within 3s")
        if self._failed:
            failure = self._failed[0]
            self._cleanup_refs()
            raise TrayUnavailableError(str(failure)) from failure
        if not thread.is_alive():
            self._cleanup_refs()
            raise TrayUnavailableError("Tray icon thread exited before startup completed")

        self._started = True

    def _run_icon(self, icon, setup) -> None:
        try:
            icon.run(setup=setup)
        except Exception as exc:  # noqa: BLE001 - surfaced to start()/stop()
            self._failed.append(exc)
        finally:
            self._ready.set()

    def _restore_from_menu(self, _icon, _item) -> None:
        self._on_restore()

    def _exit_from_menu(self, _icon, _item) -> None:
        self._on_exit()

    def stop(self) -> None:
        """Stop the icon and wait for the tray thread to exit.  Idempotent.

        Raises ``TrayUnavailableError`` if the thread does not exit within 3s,
        so a stuck thread is never silently accepted.
        """
        error = self._stop_current()
        if error is not None:
            raise error

    def _stop_current(self) -> TrayUnavailableError | None:
        """Stop the current icon, preserving handles if its thread is stuck."""
        icon = self._icon
        thread = self._thread
        stop_error = None
        if icon is not None:
            try:
                icon.stop()
            except Exception as exc:  # noqa: BLE001
                stop_error = exc
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
            if thread.is_alive():
                return TrayUnavailableError("Tray icon thread did not stop within 3s")
        self._cleanup_refs()
        if stop_error is not None:
            return TrayUnavailableError(f"Failed to stop tray icon: {stop_error}")
        return None

    def _cleanup_refs(self) -> None:
        image = self._image
        self._icon = None
        self._thread = None
        self._image = None
        self._started = False
        self._ready = threading.Event()
        self._failed = []
        if image is not None:
            try:
                image.close()
            except Exception:  # noqa: BLE001
                pass

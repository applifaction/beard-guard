"""Reusable black monitor overlays, without screenshots or display-mode changes."""

from __future__ import annotations

import gc
import sys
import threading
import time
from typing import Any

from desktop_monitors import get_monitor_geometries


class ScreenBlackoutController:
    """One Tk worker; refresh set_active(True) on each unsafe camera frame.

    Missing heartbeats fail open. Escape dismisses the blackout until a safe
    frame arrives, so a stuck detection cannot immediately cover the screens
    again. All Tk objects are created, used and finalized on the worker thread.
    """

    def __init__(self, *, enabled: bool = True, release_timeout: float = 1.0):
        if release_timeout <= 0:
            raise ValueError("release_timeout must be positive")
        self.enabled = enabled
        self.release_timeout = release_timeout
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._visible = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False
        self._dismissed = False
        self._deadline = 0.0
        self._failure_count = 0

    def set_active(self, active: bool) -> bool:
        """Update the desired state without blocking detection or queuing frames."""
        with self._lock:
            self._active = bool(active)
            if not active:
                self._dismissed = False
                return True
            self._deadline = time.monotonic() + self.release_timeout
            if not self.enabled or self._stop.is_set() or self._failure_count >= 2:
                return False
            if self._dismissed:
                return False
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._worker_main, name="beardguard-screen-blackout", daemon=True
                )
                try:
                    self._thread.start()
                except Exception as error:
                    self._thread = None
                    self._record_failure(error)
                    return False
            return True

    def dismiss(self) -> None:
        """Emergency release; do not rearm until set_active(False)."""
        with self._lock:
            self._dismissed = True

    @property
    def visible(self) -> bool:
        return self._visible.is_set()

    @property
    def worker_alive(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def shutdown(self, timeout: float = 2.0) -> bool:
        """Remove all overlays and stop the worker; safe to call repeatedly."""
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def _should_show(self) -> bool:
        with self._lock:
            return (
                self._active
                and not self._dismissed
                and time.monotonic() < self._deadline
                and not self._stop.is_set()
            )

    def _worker_main(self) -> None:
        root = None
        windows: list[Any] = []
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            errors: list[str] = []

            def on_error(_kind, error, _traceback):
                # Never retain a traceback: its Tk callback frames can keep the
                # interpreter alive until GC on a different thread (Tcl abort).
                errors.append(str(error))
                root.quit()

            root.report_callback_exception = on_error

            def on_escape(_event):
                self.dismiss()
                return "break"

            root.bind_all("<Escape>", on_escape)

            def poll():
                if self._stop.is_set():
                    root.quit()
                    return
                show = self._should_show()
                if show and not self.visible:
                    monitors = get_monitor_geometries(root)
                    while len(windows) < len(monitors):
                        window = tk.Toplevel(root)
                        windows.append(window)
                        window.withdraw()
                        window.title("Beard Guard Blackout")
                        window.overrideredirect(True)
                        window.attributes("-topmost", True)
                        window.configure(background="black", cursor="none", borderwidth=0)
                    while len(windows) > len(monitors):
                        windows.pop().destroy()
                    # Monitor discovery can take time. Recheck the latest frame
                    # before showing anything, not a stale queued trigger.
                    if self._should_show():
                        for window, (x, y, width, height) in zip(windows, monitors):
                            window.geometry(f"{width}x{height}{x:+d}{y:+d}")
                            window.deiconify()
                            window.lift()
                        if windows:
                            windows[-1].focus_force()
                        root.update_idletasks()
                        self._visible.set()
                elif not show and self.visible:
                    for window in windows:
                        window.withdraw()
                    root.update_idletasks()
                    self._visible.clear()
                root.after(20, poll)

            root.after(0, poll)
            root.mainloop()
            if errors:
                raise RuntimeError(errors[0])
        except Exception as error:
            self._record_failure(error)
        finally:
            for window in windows:
                try:
                    window.destroy()
                except Exception:
                    pass
            windows.clear()
            window = None  # release the final Toplevel on its owning thread
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass
                root = None
                gc.collect()
            self._visible.clear()
            with self._lock:
                self._thread = None

    def _record_failure(self, error: Exception) -> None:
        self._failure_count += 1
        print(f"Beard Guard blackout failed: {error}", file=sys.stderr)
        if self._failure_count >= 2:
            print("Beard Guard blackout disabled after two worker failures.", file=sys.stderr)

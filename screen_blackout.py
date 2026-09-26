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

    Missing heartbeats fail open. Escape dismisses the blackout until safe
    frames arrive continuously for rearm_duration, tolerating tracking jitter.
    All Tk objects are created, used and finalized on the worker thread.
    """

    def __init__(self, *, enabled: bool = True, release_timeout: float = 1.0, rearm_duration: float = 1.0):
        if release_timeout <= 0 or rearm_duration <= 0:
            raise ValueError("release_timeout and rearm_duration must be positive")
        self.enabled = enabled
        self.release_timeout = release_timeout
        self.rearm_duration = rearm_duration
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._visible = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False
        self._dismissed = False
        self._safe_since: float | None = None
        self._last_update = 0.0
        self._deadline = 0.0
        self._failure_count = 0

    def set_active(self, active: bool) -> bool:
        """Update the desired state without blocking detection or queuing frames."""
        with self._lock:
            now = time.monotonic()
            gap = now - self._last_update
            self._last_update = now
            self._active = bool(active)
            if not active:
                if self._dismissed:
                    if self._safe_since is None or gap > self.release_timeout:
                        self._safe_since = now
                    elif now - self._safe_since >= self.rearm_duration:
                        self._dismissed = False
                        self._safe_since = None
                return True
            self._safe_since = None
            self._deadline = now + self.release_timeout
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
        """Emergency release; only sustained safe camera frames can rearm it."""
        with self._lock:
            self._dismissed = True
            self._safe_since = None

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
        canvases: list[Any] = []
        photos: dict[int, Any] = {}
        branding_failed = False
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

            def draw_branding(canvas, width, height):
                nonlocal branding_failed
                size = min(300, max(96, min(width, height) // 3))
                if size not in photos and not branding_failed:
                    try:
                        from PIL import ImageTk
                        from blackout_branding import render_logo_badge
                        photos[size] = ImageTk.PhotoImage(render_logo_badge(size), master=root)
                    except (ImportError, OSError) as error:
                        # A missing optional image must not disable the warning.
                        branding_failed = True
                        print(f"Beard Guard logo unavailable: {error}", file=sys.stderr)
                canvas.configure(width=width, height=height)
                canvas.delete("all")
                if size in photos:
                    canvas.create_image(width // 2, height // 2, image=photos[size])
                canvas.create_text(
                    width // 2, height // 2 + size // 2 + 30,
                    text="Beardguard", fill="#edf0f3",
                    font=("Arial", max(16, size // 12), "bold"),
                )
                canvas.create_text(
                    width // 2, height // 2 + size // 2 + 64,
                    text="Hand vom Bart nehmen  ·  Esc zum Freigeben",
                    fill="#abb2bc", font=("Arial", max(10, size // 22)),
                )

            retry_at = 0.0

            def poll():
                nonlocal retry_at
                if self._stop.is_set():
                    root.quit()
                    return
                show = self._should_show()
                if show and not self.visible and time.monotonic() >= retry_at:
                    monitors = get_monitor_geometries(root)
                    while len(windows) < len(monitors):
                        window = tk.Toplevel(root)
                        windows.append(window)
                        window.withdraw()
                        window.title("Beard Guard Blackout")
                        window.overrideredirect(True)
                        window.attributes("-topmost", True)
                        window.configure(background="black", cursor="none", borderwidth=0)
                        canvas = tk.Canvas(window, background="black", highlightthickness=0, cursor="none")
                        canvas.pack(fill="both", expand=True)
                        canvases.append(canvas)
                    while len(windows) > len(monitors):
                        canvases.pop().destroy()
                        windows.pop().destroy()
                    # Monitor discovery can take time. Recheck the latest frame
                    # before showing anything, not a stale queued trigger.
                    if self._should_show():
                        for window, canvas, (x, y, width, height) in zip(windows, canvases, monitors):
                            window.geometry(f"{width}x{height}{x:+d}{y:+d}")
                            draw_branding(canvas, width, height)
                            window.deiconify()
                            window.lift()
                        root.update_idletasks()
                        if windows:
                            try:
                                # Route Escape to the overlay even if another app
                                # takes focus. Released on every hide/cleanup path.
                                windows[-1].grab_set_global()
                                windows[-1].focus_force()
                            except tk.TclError:
                                # Menus/modal dialogs can own a temporary grab.
                                # Fail open and retry, without burning the worker
                                # failure budget or leaving inaccessible overlays.
                                for window in windows:
                                    window.grab_release()
                                    window.withdraw()
                                retry_at = time.monotonic() + 0.25
                                root.after(20, poll)
                                return
                        self._visible.set()
                elif not show and self.visible:
                    for window in windows:
                        window.grab_release()
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
                    window.grab_release()
                    window.destroy()
                except Exception:
                    pass
            windows.clear()
            canvases.clear()
            photos.clear()  # PhotoImage must also be finalized on the Tk thread
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

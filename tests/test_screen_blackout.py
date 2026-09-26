"""Real overlay checks; run under xvfb-run, never on the user's desktop."""

import contextlib
import io
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import types
import unittest
from unittest import mock

from screen_blackout import ScreenBlackoutController


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@unittest.skipUnless(os.environ.get("DISPLAY"), "requires an isolated X11 display")
class ScreenBlackoutTests(unittest.TestCase):
    def test_blackout_stays_while_refreshed_and_hides_when_hand_leaves(self):
        controller = ScreenBlackoutController(enabled=True, release_timeout=0.2)
        try:
            self.assertTrue(controller.set_active(True))
            self.assertTrue(wait_for(lambda: controller.visible))
            # More than one timeout passes, but camera frames keep it alive.
            for _ in range(8):
                controller.set_active(True)
                time.sleep(0.05)
                self.assertTrue(controller.visible)
            controller.set_active(False)
            self.assertTrue(wait_for(lambda: not controller.visible))
            self.assertTrue(controller.worker_alive)
        finally:
            self.assertTrue(controller.shutdown())
        self.assertFalse(controller.worker_alive)
        self.assertTrue(controller.shutdown())

    def test_stalled_camera_releases_blackout_automatically(self):
        controller = ScreenBlackoutController(release_timeout=0.2)
        try:
            controller.set_active(True)
            self.assertTrue(wait_for(lambda: controller.visible))
            self.assertTrue(wait_for(lambda: not controller.visible, timeout=0.6))
        finally:
            controller.shutdown()

    def test_escape_releases_and_does_not_rearm_until_hand_leaves(self):
        controller = ScreenBlackoutController(rearm_duration=0.1)
        try:
            controller.set_active(True)
            self.assertTrue(wait_for(lambda: controller.visible))
            subprocess.run(["xdotool", "key", "Escape"], check=True, timeout=2)
            self.assertTrue(wait_for(lambda: not controller.visible))
            self.assertFalse(controller.set_active(True))
            time.sleep(0.05)
            self.assertFalse(controller.visible)
            deadline = time.monotonic() + 0.15
            while time.monotonic() < deadline:
                controller.set_active(False)
                time.sleep(0.02)
            self.assertTrue(controller.set_active(True))
            self.assertTrue(wait_for(lambda: controller.visible))
        finally:
            self.assertTrue(controller.shutdown())
        self.assertFalse(controller.visible)

    def test_escape_releases_even_when_another_app_owns_keyboard_focus(self):
        other = subprocess.Popen(
            [sys.executable, "-c", "import tkinter as tk; r=tk.Tk(); r.title('Beardguard Escape Test Receiver'); r.bind('<Escape>', lambda e: print('ESC', flush=True)); r.update(); print('READY', flush=True); r.mainloop()"],
            stdout=subprocess.PIPE, text=True,
        )
        controller = ScreenBlackoutController()
        try:
            # Focus the WM wrapper, not winfo_id()'s inner Tk window. The
            # latter never establishes Tk's internal focus, even without us.
            self.assertEqual(other.stdout.readline().strip(), "READY")
            other_id = subprocess.check_output(
                ["xdotool", "search", "--name", "^Beardguard Escape Test Receiver$"], text=True, timeout=2
            ).splitlines()[0]
            controller.set_active(True)
            self.assertTrue(wait_for(lambda: controller.visible))
            subprocess.run(["xdotool", "windowfocus", "--sync", other_id], check=True, timeout=2)
            subprocess.run(["xdotool", "key", "Escape"], check=True, timeout=2)
            # Keep camera heartbeats flowing so the stall timeout cannot mask
            # a missed Escape event.
            def released():
                controller.set_active(True)
                return not controller.visible
            self.assertTrue(wait_for(released, timeout=0.35), "Escape was lost to the focused application")
            # Bare Xvfb has no window manager to restore focus after withdrawal.
            subprocess.run(["xdotool", "windowfocus", "--sync", other_id], check=True, timeout=2)
            subprocess.run(["xdotool", "key", "Escape"], check=True, timeout=2)
            ready, _, _ = select.select([other.stdout], [], [], 1)
            self.assertTrue(ready, "Overlay did not release the keyboard grab")
            self.assertEqual(other.stdout.readline().strip(), "ESC")
        finally:
            controller.shutdown()
            other.terminate()
            other.wait(timeout=2)
            other.stdout.close()

    def test_foreign_input_grab_fails_open_then_recovers_without_disabling(self):
        other = subprocess.Popen(
            [sys.executable, "-c", "import tkinter as tk; r=tk.Tk(); r.update(); r.grab_set_global(); print('READY', flush=True); r.mainloop()"],
            stdout=subprocess.PIPE, text=True,
        )
        controller = ScreenBlackoutController()
        try:
            self.assertEqual(other.stdout.readline().strip(), "READY")
            deadline = time.monotonic() + 0.7
            while time.monotonic() < deadline:
                self.assertTrue(controller.set_active(True))
                time.sleep(0.03)
                self.assertFalse(controller.visible)
            self.assertTrue(controller.worker_alive)
            other.terminate()
            other.wait(timeout=2)
            def recovered():
                controller.set_active(True)
                return controller.visible
            self.assertTrue(wait_for(recovered), "Temporary grab conflict permanently disabled warnings")
        finally:
            controller.shutdown()
            if other.poll() is None:
                other.terminate()
                other.wait(timeout=2)
            other.stdout.close()

    def test_dismissal_survives_a_single_missing_detection_frame(self):
        controller = ScreenBlackoutController()
        try:
            controller.set_active(True)
            self.assertTrue(wait_for(lambda: controller.visible))
            controller.dismiss()
            self.assertTrue(wait_for(lambda: not controller.visible))
            controller.set_active(False)  # transient missed hand/face landmark
            self.assertFalse(controller.set_active(True), "One missing frame must not cancel Escape")
            self.assertFalse(controller.visible)
        finally:
            controller.shutdown()

    def test_stalled_camera_cannot_rearm_an_escape_dismissal(self):
        controller = ScreenBlackoutController(release_timeout=0.1, rearm_duration=0.2)
        try:
            controller.set_active(True)
            self.assertTrue(wait_for(lambda: controller.visible))
            controller.dismiss()
            self.assertTrue(wait_for(lambda: not controller.visible))
            controller.set_active(False)
            time.sleep(0.25)
            controller.set_active(False)
            self.assertFalse(controller.set_active(True))
        finally:
            controller.shutdown()

    def test_both_monitors_show_centered_logo_on_dark_background_without_capture(self):
        from PIL import ImageGrab

        # Emulate two adjacent displays within the isolated 1280x1024 Xvfb screen.
        controller = ScreenBlackoutController()
        monitors = [(0, 0, 640, 1024), (640, 0, 640, 1024)]
        # A MagicMock would retain the worker-owned Tk root in call_args.
        with mock.patch("screen_blackout.get_monitor_geometries", new=lambda root: monitors):
            try:
                with mock.patch.object(ImageGrab, "grab", side_effect=AssertionError("must not capture desktop")):
                    controller.set_active(True)
                    self.assertTrue(wait_for(lambda: controller.visible))
                subprocess.run(["xsetroot", "-solid", "#ff00ff"], check=True, timeout=2)
                image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
                for point in [(1, 1), (639, 1023), (640, 1), (1279, 1023)]:
                    self.assertEqual(image.getpixel(point), (0, 0, 0), point)
                for center_x in [320, 960]:
                    self.assertNotEqual(image.getpixel((center_x, 512)), (0, 0, 0))
                    self.assertEqual(image.getpixel((center_x, 416)), (37, 40, 46))  # graphite frame
                    self.assertEqual(image.getpixel((center_x - 103, 409)), (0, 0, 0))  # rounded corner
                    logo = image.crop((center_x - 70, 442, center_x + 70, 582))
                    self.assertGreater(len(logo.getcolors(20000) or []), 100)
                controller.set_active(False)
                self.assertTrue(wait_for(lambda: not controller.visible))
                revealed = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
                self.assertEqual(revealed.getpixel((1, 1)), (255, 0, 255))
                self.assertEqual(revealed.getpixel((1279, 1023)), (255, 0, 255))
            finally:
                controller.shutdown()

    def test_100_cycles_reuse_worker_and_keep_socket_count_bounded(self):
        def sockets():
            return sum(
                os.readlink(fd).startswith("socket:")
                for fd in Path("/proc/self/fd").iterdir() if fd.exists()
            )

        controller = ScreenBlackoutController()
        try:
            controller.set_active(True)
            self.assertTrue(wait_for(lambda: controller.visible))
            baseline = sockets()
            for _ in range(100):
                controller.set_active(False)
                self.assertTrue(wait_for(lambda: not controller.visible))
                controller.set_active(True)
                self.assertTrue(wait_for(lambda: controller.visible))
                self.assertTrue(controller.worker_alive)
            self.assertLessEqual(sockets(), baseline)
        finally:
            controller.shutdown()

    def test_callback_error_removes_windows_and_stops_worker(self):
        controller = ScreenBlackoutController()
        errors = io.StringIO()
        try:
            controller.set_active(True)
            self.assertTrue(wait_for(lambda: controller.visible))
            controller.set_active(False)
            self.assertTrue(wait_for(lambda: not controller.visible))
            def fail_discovery(root):
                raise RuntimeError("display failed")

            with mock.patch("screen_blackout.get_monitor_geometries", new=fail_discovery), \
                 contextlib.redirect_stderr(errors):
                controller.set_active(True)
                self.assertTrue(wait_for(lambda: not controller.worker_alive))
            self.assertFalse(controller.visible)
            self.assertIn("display failed", errors.getvalue())
        finally:
            self.assertTrue(controller.shutdown())


class ScreenBlackoutFailureTests(unittest.TestCase):
    def test_initialization_retries_once_then_disables_visuals(self):
        broken_tk = types.SimpleNamespace(Tk=mock.Mock(side_effect=RuntimeError("no display")))
        controller = ScreenBlackoutController()
        errors = io.StringIO()
        with mock.patch.dict(sys.modules, {"tkinter": broken_tk}), contextlib.redirect_stderr(errors):
            for _ in range(2):
                self.assertTrue(controller.set_active(True))
                self.assertTrue(wait_for(lambda: not controller.worker_alive))
            self.assertFalse(controller.set_active(True))
        self.assertEqual(broken_tk.Tk.call_count, 2)
        self.assertIn("disabled after two worker failures", errors.getvalue())
        self.assertTrue(controller.shutdown())

    def test_disabled_controller_does_not_start_worker(self):
        controller = ScreenBlackoutController(enabled=False)
        self.assertFalse(controller.set_active(True))
        self.assertFalse(controller.worker_alive)
        self.assertFalse(controller.visible)
        self.assertTrue(controller.shutdown())


if __name__ == "__main__":
    unittest.main()

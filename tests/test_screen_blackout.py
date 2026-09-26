"""Real overlay checks; run under xvfb-run, never on the user's desktop."""

import contextlib
import io
import os
from pathlib import Path
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
        controller = ScreenBlackoutController()
        try:
            controller.set_active(True)
            self.assertTrue(wait_for(lambda: controller.visible))
            subprocess.run(["xdotool", "key", "Escape"], check=True, timeout=2)
            self.assertTrue(wait_for(lambda: not controller.visible))
            self.assertFalse(controller.set_active(True))
            time.sleep(0.05)
            self.assertFalse(controller.visible)
            controller.set_active(False)
            self.assertTrue(controller.set_active(True))
            self.assertTrue(wait_for(lambda: controller.visible))
        finally:
            self.assertTrue(controller.shutdown())
        self.assertFalse(controller.visible)

    def test_both_monitors_are_solid_black_without_desktop_capture(self):
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

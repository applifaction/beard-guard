"""Discover every active display without a slow hardware reprobe."""

import subprocess
import unittest
from unittest import mock

from desktop_monitors import get_monitor_geometries


CURRENT_LAYOUT = """Screen 0: minimum 8 x 8, current 1920 x 2280, maximum 32767 x 32767
HDMI-0 connected 1920x1080+0+1200 (normal left inverted right x axis y axis) 370mm x 140mm
   1920x1080     60.00*+
DP-0 disconnected (normal left inverted right x axis y axis)
DP-1 connected (normal left inverted right x axis y axis)
eDP-1-1 connected primary 1920x1200+0+0 (normal left inverted right x axis y axis) 345mm x 215mm
"""


class DesktopMonitorTests(unittest.TestCase):
    def test_returns_both_active_displays_from_current_server_configuration(self):
        with mock.patch("desktop_monitors.sys.platform", "linux"), \
             mock.patch.dict("os.environ", {"DISPLAY": ":99"}), \
             mock.patch("desktop_monitors.shutil.which", return_value="/usr/bin/xrandr"), \
             mock.patch("desktop_monitors.subprocess.check_output", return_value=CURRENT_LAYOUT) as query:
            self.assertEqual(
                get_monitor_geometries(None),
                [(0, 1200, 1920, 1080), (0, 0, 1920, 1200)],
            )
        self.assertIn("--current", query.call_args.args[0])
        self.assertIn("--query", query.call_args.args[0])
        self.assertNotIn("--listmonitors", query.call_args.args[0])

    def test_query_failure_keeps_virtual_desktop_fallback(self):
        root = mock.Mock()
        root.winfo_vrootx.return_value = 0
        root.winfo_vrooty.return_value = 0
        root.winfo_vrootwidth.return_value = 1920
        root.winfo_vrootheight.return_value = 2280
        with mock.patch("desktop_monitors.sys.platform", "linux"), \
             mock.patch.dict("os.environ", {"DISPLAY": ":99"}), \
             mock.patch("desktop_monitors.shutil.which", return_value="/usr/bin/xrandr"), \
             mock.patch("desktop_monitors.subprocess.check_output", side_effect=subprocess.TimeoutExpired("xrandr", 0.5)):
            self.assertEqual(get_monitor_geometries(root), [(0, 0, 1920, 2280)])


if __name__ == "__main__":
    unittest.main()

"""Monitor discovery shared by desktop warning overlays."""

import os
import re
import shutil
import subprocess
import sys
from typing import Any


def get_monitor_geometries(root: Any) -> list[tuple[int, int, int, int]]:
    """Return monitor rectangles as (x, y, width, height)."""
    if sys.platform.startswith("linux") and os.environ.get("DISPLAY") and shutil.which("xrandr"):
        try:
            # Read the server's current layout without probing monitor hardware.
            # --listmonitors can take seconds on NVIDIA/PRIME and hit our timeout.
            output = subprocess.check_output(
                ["xrandr", "--query", "--current"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=0.5,
            )
            geometries = []
            for line in output.splitlines():
                match = re.match(
                    r"^\S+ connected(?: primary)? (\d+)x(\d+)([+-]\d+)([+-]\d+)(?:\s|$)",
                    line,
                )
                if match:
                    width, height, x, y = map(int, match.groups())
                    geometries.append((x, y, width, height))
            if geometries:
                return geometries
        except (subprocess.SubprocessError, OSError):
            pass

    try:
        x, y = root.winfo_vrootx(), root.winfo_vrooty()
        width, height = root.winfo_vrootwidth(), root.winfo_vrootheight()
        if width > 0 and height > 0:
            return [(x, y, width, height)]
    except Exception:
        pass
    return [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]

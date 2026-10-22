import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_DIR / "beardguard.sh"


class BeardGuardLauncherTests(unittest.TestCase):
    def test_second_launch_does_not_start_another_camera_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            copied_launcher = root / "beardguard.sh"
            shutil.copy2(LAUNCHER, copied_launcher)
            (root / "beard_guard.py").touch()
            python_bin = root / "venv" / "bin" / "python"
            python_bin.parent.mkdir(parents=True)
            python_bin.write_text(
                "#!/usr/bin/env bash\n"
                'printf "started\\n" >> "$BEARDGUARD_TEST_LOG"\n'
                "sleep 2\n",
                encoding="utf-8",
            )
            python_bin.chmod(0o755)
            launch_log = root / "launch.log"
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root),
                    "XDG_RUNTIME_DIR": str(runtime_dir),
                    "BEARDGUARD_DESKTOP_LAUNCH": "1",
                    "BEARDGUARD_TEST_LOG": str(launch_log),
                }
            )

            first = subprocess.Popen([str(copied_launcher)], env=env)
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not launch_log.exists():
                    time.sleep(0.02)
                self.assertTrue(launch_log.exists(), "first launch did not start")

                second = subprocess.run(
                    [str(copied_launcher)],
                    env=env,
                    timeout=1,
                    check=False,
                )
                self.assertEqual(second.returncode, 0)
                self.assertEqual(launch_log.read_text(encoding="utf-8").splitlines(), ["started"])
            finally:
                first.terminate()
                try:
                    first.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    first.kill()


if __name__ == "__main__":
    unittest.main()

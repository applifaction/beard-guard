import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_DIR / "beardguard.sh"
INSTALLER = PROJECT_DIR / "scripts" / "install-ubuntu-desktop-entry.sh"


class BeardGuardInstallerTests(unittest.TestCase):
    def test_installer_enables_restartable_user_service_instead_of_desktop_autostart(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bin_dir = home / "bin"
            bin_dir.mkdir()
            systemctl_log = home / "systemctl.log"
            systemctl = bin_dir / "systemctl"
            systemctl.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$*" >> "$BEARDGUARD_TEST_SYSTEMCTL_LOG"\n'
            )
            systemctl.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "BEARDGUARD_TEST_SYSTEMCTL_LOG": str(systemctl_log),
                }
            )

            subprocess.run([str(INSTALLER)], env=env, check=True, timeout=30)

            service = home / ".config/systemd/user/beardguard-watchdog.service"
            self.assertTrue(service.exists())
            service_text = service.read_text()
            self.assertIn(f"ExecStart={PROJECT_DIR}/scripts/beardguard-camera-watchdog.sh", service_text)
            self.assertIn("Restart=always", service_text)
            self.assertIn("WantedBy=graphical-session.target", service_text)
            self.assertFalse(
                (home / ".config/autostart/beardguard-watchdog.desktop").exists()
            )
            systemctl_calls = systemctl_log.read_text().splitlines()
            self.assertIn("--user daemon-reload", systemctl_calls)
            self.assertIn(
                "--user enable --now beardguard-watchdog.service",
                systemctl_calls,
            )


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

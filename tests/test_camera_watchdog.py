import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
WATCHDOG = PROJECT_DIR / "scripts" / "beardguard-camera-watchdog.sh"


def wait_for(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class CameraWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.camera = self.root / "video-test"
        self.camera.touch()
        self.fake_app = self.root / "beard_guard.py"
        self.fake_app.touch()
        self.fake_launcher = self.root / "beardguard.sh"
        self.fake_launcher.write_text("#!/usr/bin/env bash\nexit 0\n")
        self.fake_launcher.chmod(0o755)
        self.log = self.root / "watchdog.log"
        self.marker = self.root / "beardguard-launched"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        launcher = bin_dir / "gtk-launch"
        launcher.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s\\n" "$*" >> "$BEARDGUARD_TEST_LAUNCH_MARKER"\n'
        )
        launcher.chmod(0o755)
        journalctl = bin_dir / "journalctl"
        journalctl.write_text(
            "#!/usr/bin/env bash\n"
            '[[ -f "$BEARDGUARD_TEST_XHCI_LOG" ]] && /bin/cat "$BEARDGUARD_TEST_XHCI_LOG"\n'
        )
        journalctl.chmod(0o755)
        self.xhci_log = self.root / "xhci-watchdog.log"
        self.fault_latch = self.root / "camera-fault-latched"
        self.recovery_retry_marker = self.root / "xhci-recovery-retry"
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{bin_dir}:{self.env['PATH']}",
                "BEARDGUARD_CAMERA_DEVICES": str(self.camera),
                "BEARDGUARD_PROJECT_DIR": str(self.root),
                "BEARDGUARD_WATCHDOG_POLL_SECONDS": "0.05",
                "BEARDGUARD_RESUME_DELAY_SECONDS": "0",
                "BEARDGUARD_STARTUP_GRACE_SECONDS": "30",
                "BEARDGUARD_STARTUP_RETRY_SECONDS": "1",
                "BEARDGUARD_STARTUP_FAILURE_WINDOW_SECONDS": "120",
                "BEARDGUARD_CLAIM_GUARD_SECONDS": "0",
                "BEARDGUARD_NO_CALL_RESTART_SECONDS": "30",
                "BEARDGUARD_TEST_LAUNCH_MARKER": str(self.marker),
                "BEARDGUARD_WATCHDOG_LOG": str(self.log),
                "BEARDGUARD_XHCI_FAULT_LATCH": str(self.fault_latch),
                "BEARDGUARD_XHCI_RECOVERY_RETRY_MARKER": str(self.recovery_retry_marker),
                "BEARDGUARD_XHCI_RECOVERY_RETRY_WINDOW_SECONDS": "600",
                "BEARDGUARD_TEST_XHCI_LOG": str(self.xhci_log),
            }
        )
        self.processes = []

    def tearDown(self):
        for process in reversed(self.processes):
            process.terminate()
        for process in reversed(self.processes):
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        subprocess.run(
            ["fuser", "-k", str(self.camera)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.tmp.cleanup()

    def start_holder(self):
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,time; f=open(sys.argv[1], 'rb'); time.sleep(30)",
                str(self.camera),
            ]
        )
        self.processes.append(holder)
        return holder

    def start_watchdog(self):
        watchdog = subprocess.Popen(
            [str(WATCHDOG)],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.processes.append(watchdog)
        return watchdog

    def start_beardguard_with_camera_child(self):
        code = (
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable, '-c', "
            "'import sys,time; f=open(sys.argv[1], \\\"rb\\\"); time.sleep(30)', "
            "sys.argv[1]]); time.sleep(30)"
        )
        beardguard = subprocess.Popen(
            [sys.executable, "-c", code, str(self.camera), str(self.fake_app)]
        )
        self.processes.append(beardguard)
        return beardguard

    def log_contains(self, text):
        return self.log.exists() and text in self.log.read_text()

    def test_early_autostart_failure_retries_without_five_minute_delay(self):
        self.env["BEARDGUARD_STARTUP_GRACE_SECONDS"] = "0"
        self.start_watchdog()

        def launch_count():
            if not self.marker.exists():
                return 0
            return len(self.marker.read_text().splitlines())

        self.assertTrue(wait_for(lambda: launch_count() >= 1), self.log.read_text())
        self.assertTrue(
            wait_for(lambda: launch_count() >= 2, timeout=6.0),
            self.log.read_text(),
        )
        self.assertTrue(self.log_contains("Early startup exit"), self.log.read_text())

    def test_camera_disconnect_during_call_does_not_launch_beardguard(self):
        holder = self.start_holder()
        self.assertTrue(wait_for(lambda: holder.poll() is None))
        self.start_watchdog()
        self.assertTrue(
            wait_for(lambda: self.log_contains("Camera already in use by another process")),
            self.log.read_text() if self.log.exists() else "watchdog produced no log",
        )

        self.camera.unlink()
        self.assertTrue(wait_for(lambda: self.log_contains("entering fault quarantine")))
        self.camera.touch()
        self.assertTrue(wait_for(lambda: self.log_contains("withholding automatic Beard Guard start")))
        time.sleep(0.3)

        self.assertFalse(self.marker.exists(), self.log.read_text())

    def test_clean_camera_release_resumes_beardguard(self):
        holder = self.start_holder()
        self.start_watchdog()
        self.assertTrue(wait_for(lambda: self.log_contains("Camera already in use by another process")))

        holder.terminate()
        holder.wait(timeout=2)
        self.assertTrue(wait_for(self.marker.exists), self.log.read_text())
        self.assertTrue(self.log_contains("Camera released cleanly"))

    def test_command_text_reference_does_not_clear_fault_latch(self):
        self.fault_latch.write_text("latched\n")
        decoy = subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"import time; time.sleep(30) # {self.fake_app} ",
            ],
            cwd=self.root,
        )
        self.processes.append(decoy)
        self.start_watchdog()
        time.sleep(0.5)

        self.assertTrue(self.fault_latch.exists(), self.log.read_text())
        self.assertNotIn("clearing fault latch", self.log.read_text())

    def test_recovered_controller_clears_fault_latch_and_restarts(self):
        self.fault_latch.write_text("latched\n")
        self.env["BEARDGUARD_STARTUP_GRACE_SECONDS"] = "0"
        self.start_watchdog()
        time.sleep(0.2)
        self.assertFalse(self.marker.exists(), self.log.read_text())

        self.xhci_log.write_text(
            "xHCI controller 0000:80:14.0 recovered successfully\n"
        )

        self.assertTrue(
            wait_for(lambda: not self.fault_latch.exists()),
            self.log.read_text(),
        )
        self.assertTrue(wait_for(self.marker.exists), self.log.read_text())
        self.assertIn("clearing stale camera-fault latch", self.log.read_text())

    def test_repeated_xhci_fault_on_same_kernel_stays_quarantined(self):
        self.recovery_retry_marker.write_text(f"{os.uname().release}\n")
        self.fault_latch.write_text("latched\n")
        self.xhci_log.write_text(
            "xHCI controller 0000:80:14.0 recovered successfully\n"
        )
        self.env["BEARDGUARD_STARTUP_GRACE_SECONDS"] = "0"

        self.start_watchdog()
        time.sleep(0.5)

        self.assertTrue(self.fault_latch.exists(), self.log.read_text())
        self.assertFalse(self.marker.exists(), self.log.read_text())
        self.assertIn("Repeated xHCI fault on the same kernel", self.log.read_text())

    def test_persistent_fault_latch_requires_manual_beardguard_start(self):
        self.fault_latch.write_text("latched\n")
        self.start_watchdog()
        time.sleep(0.4)
        self.assertFalse(self.marker.exists(), self.log.read_text())

        beardguard = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", str(self.fake_app)],
            cwd=self.root,
        )
        self.processes.append(beardguard)
        self.assertTrue(
            wait_for(lambda: not self.fault_latch.exists()),
            self.log.read_text(),
        )
        self.assertIsNone(beardguard.poll(), self.log.read_text())
        self.assertIn("clearing fault latch", self.log.read_text())

    def test_xhci_camera_storm_stops_beardguard_and_latches_restart(self):
        beardguard = self.start_beardguard_with_camera_child()
        self.assertTrue(
            wait_for(
                lambda: subprocess.run(
                    ["fuser", str(self.camera)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
        )
        self.start_watchdog()
        self.assertTrue(wait_for(lambda: self.log_contains("Beard Guard is running"), timeout=10.0))

        self.xhci_log.write_text(
            "Recovery deferred: camera is active (pids=4242) and known USB input is still present\n"
        )

        self.assertTrue(wait_for(lambda: beardguard.poll() is not None), self.log.read_text())
        self.assertTrue(
            wait_for(
                lambda: subprocess.run(
                    ["fuser", str(self.camera)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                != 0
            ),
            "camera-owning child survived Beardguard shutdown",
        )
        self.assertTrue(self.fault_latch.exists(), self.log.read_text())
        self.assertTrue(self.log_contains("xHCI camera error storm detected"), self.log.read_text())
        time.sleep(0.3)
        self.assertFalse(self.marker.exists(), self.log.read_text())

    def test_camera_child_process_is_treated_as_part_of_beardguard(self):
        beardguard = self.start_beardguard_with_camera_child()
        self.assertTrue(
            wait_for(
                lambda: subprocess.run(
                    ["fuser", str(self.camera)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
        )
        self.start_watchdog()
        self.assertTrue(wait_for(lambda: self.log_contains("Beard Guard is running"), timeout=10.0))
        time.sleep(8.0)

        self.assertIsNone(beardguard.poll(), self.log.read_text())
        self.assertFalse(self.log_contains("Detected camera use by another process"), self.log.read_text())


if __name__ == "__main__":
    unittest.main()

import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from camera_capture import (
    CameraMode,
    FFmpegCameraCapture,
    FallbackCameraCapture,
    build_ffmpeg_command,
    open_first_working_camera,
    open_linux_camera,
    prioritize_camera_modes,
)


class FakeProcess:
    def __init__(self, stdout_bytes):
        self.stdout = io.BytesIO(stdout_bytes)
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class CameraCaptureTests(unittest.TestCase):
    def test_ffmpeg_command_defaults_to_mjpeg_raw_pipe(self):
        command = build_ffmpeg_command(
            device="/dev/video0",
            width=320,
            height=180,
            fps=30,
            ffmpeg_path="/usr/bin/ffmpeg",
        )

        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertIn("mjpeg", command)
        self.assertIn("320x180", command)
        self.assertIn("bgr24", command)
        self.assertEqual(command[-2:], ["rawvideo", "pipe:1"])

    def test_ir_camera_can_be_prioritized_ahead_of_unstable_color_camera(self):
        color = CameraMode(Path("/dev/video0"), "mjpeg", 320, 180, 30)
        infrared = CameraMode(Path("/dev/video2"), "gray", 640, 360, 15)

        self.assertEqual(
            prioritize_camera_modes(color, [infrared], prefer_ir=True),
            [infrared, color],
        )

    def test_ir_only_mode_excludes_unstable_color_fallback(self):
        color = CameraMode(Path("/dev/video0"), "mjpeg", 320, 180, 30)
        infrared = CameraMode(Path("/dev/video2"), "gray", 640, 360, 15)

        self.assertEqual(
            prioritize_camera_modes(
                color,
                [infrared],
                prefer_ir=True,
                allow_primary_fallback=False,
            ),
            [infrared],
        )

    def test_ffmpeg_command_accepts_ir_grayscale_input(self):
        command = build_ffmpeg_command(
            device="/dev/video2",
            width=640,
            height=360,
            fps=15,
            input_format="gray",
        )

        self.assertEqual(command[command.index("-input_format") + 1], "gray")
        self.assertIn("640x360", command)
        self.assertIn("bgr24", command)

    @patch("camera_capture.Path.exists", return_value=True)
    @patch("camera_capture.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("camera_capture._ir_camera_modes")
    @patch("camera_capture.FallbackCameraCapture")
    def test_linux_camera_can_use_color_yuyv_without_ir_fallback(
        self,
        fallback_capture,
        ir_camera_modes,
        _which,
        _exists,
    ):
        ir_camera_modes.return_value = [
            CameraMode(Path("/dev/video2"), "gray", 640, 360, 15)
        ]

        open_linux_camera(
            camera_index=0,
            width=320,
            height=180,
            fps=30,
            primary_input_format="yuyv422",
            allow_ir_fallback=False,
        )

        self.assertEqual(
            fallback_capture.call_args.args[0],
            [CameraMode(Path("/dev/video0"), "yuyv422", 320, 180, 30)],
        )

    def test_first_camera_without_frames_is_released_before_fallback(self):
        captures = []
        outcomes = iter([False, True])

        class ProbeCapture:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.released = False
                self.outcome = next(outcomes)
                captures.append(self)

            def read(self):
                return self.outcome, object() if self.outcome else None

            def release(self):
                self.released = True

        result = open_first_working_camera(
            [
                CameraMode(Path("/dev/video0"), "mjpeg", 320, 180, 30),
                CameraMode(Path("/dev/video2"), "gray", 640, 360, 15),
            ],
            ffmpeg_path="/usr/bin/ffmpeg",
            read_timeout=0.1,
            capture_factory=ProbeCapture,
        )

        self.assertIs(result, captures[1])
        self.assertTrue(captures[0].released)
        self.assertFalse(captures[1].released)
        self.assertEqual(captures[1].kwargs["device"], Path("/dev/video2"))
        self.assertEqual(captures[1].kwargs["input_format"], "gray")

    def test_runtime_stream_failure_switches_to_remaining_camera(self):
        captures = []
        sequences = iter(
            [
                iter([(True, "color-frame"), (False, None)]),
                iter([(True, "ir-frame"), (True, "next-ir-frame")]),
            ]
        )

        class StreamCapture:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.results = next(sequences)
                self.released = False
                captures.append(self)

            def isOpened(self):
                return not self.released

            def read(self):
                return next(self.results)

            def release(self):
                self.released = True

        capture = FallbackCameraCapture(
            [
                CameraMode(Path("/dev/video0"), "mjpeg", 320, 180, 30),
                CameraMode(Path("/dev/video2"), "gray", 640, 360, 15),
            ],
            ffmpeg_path="/usr/bin/ffmpeg",
            read_timeout=0.1,
            capture_factory=StreamCapture,
        )
        try:
            self.assertEqual(capture.read(), (True, "color-frame"))
            self.assertEqual(capture.read(), (True, "ir-frame"))
            self.assertEqual(capture.read(), (True, "next-ir-frame"))
        finally:
            capture.release()

        self.assertTrue(captures[0].released)
        self.assertTrue(captures[1].released)
        self.assertEqual(captures[1].kwargs["input_format"], "gray")

    def test_complete_raw_frame_is_returned_with_expected_shape(self):
        width, height = 4, 2
        raw_frame = np.arange(width * height * 3, dtype=np.uint8).tobytes()
        process = FakeProcess(raw_frame)
        calls = []

        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return process

        capture = FFmpegCameraCapture(
            device="/dev/video-test",
            width=width,
            height=height,
            fps=30,
            read_timeout=0.5,
            popen_factory=popen,
        )
        try:
            success, frame = capture.read()
            self.assertTrue(success)
            self.assertEqual(frame.shape, (height, width, 3))
            self.assertEqual(frame.tobytes(), raw_frame)
            self.assertEqual(calls[0][1]["stdout"], subprocess.PIPE)
            self.assertEqual(calls[0][1]["stderr"], subprocess.DEVNULL)
        finally:
            capture.release()

        self.assertTrue(process.terminated)

    def test_partial_frame_returns_failure_instead_of_stale_image(self):
        process = FakeProcess(b"too short")
        capture = FFmpegCameraCapture(
            device="/dev/video-test",
            width=4,
            height=2,
            fps=30,
            read_timeout=0.2,
            popen_factory=lambda *args, **kwargs: process,
        )
        try:
            success, frame = capture.read()
        finally:
            capture.release()

        self.assertFalse(success)
        self.assertIsNone(frame)


if __name__ == "__main__":
    unittest.main()

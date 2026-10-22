from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import threading
import time

import numpy as np


@dataclass(frozen=True)
class CameraMode:
    device: Path
    input_format: str
    width: int
    height: int
    fps: int


def build_ffmpeg_command(
    device,
    width,
    height,
    fps,
    ffmpeg_path="ffmpeg",
    input_format="mjpeg",
):
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "fatal",
        "-f",
        "v4l2",
        "-input_format",
        input_format,
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        str(device),
        "-an",
        "-sn",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


class FFmpegCameraCapture:
    """Continuously drain FFmpeg frames while exposing only the newest V4L2 image."""

    def __init__(
        self,
        device,
        width,
        height,
        fps,
        read_timeout=2.0,
        ffmpeg_path="ffmpeg",
        input_format="mjpeg",
        popen_factory=subprocess.Popen,
    ):
        self.device = str(device)
        self.width = width
        self.height = height
        self.fps = fps
        self.read_timeout = read_timeout
        self._frame_size = width * height * 3
        self._condition = threading.Condition()
        self._frame = None
        self._sequence = 0
        self._delivered_sequence = 0
        self._closed = False
        self._stop = threading.Event()

        command = build_ffmpeg_command(
            device=self.device,
            width=width,
            height=height,
            fps=fps,
            ffmpeg_path=ffmpeg_path,
            input_format=input_format,
        )
        self._process = popen_factory(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self._frame_size * 2,
        )
        if self._process.stdout is None:
            self._process.terminate()
            raise RuntimeError("FFmpeg camera process did not provide a stdout pipe")

        self._reader = threading.Thread(
            target=self._read_frames,
            name="beardguard-ffmpeg-camera",
            daemon=True,
        )
        self._reader.start()

    def _read_exactly(self):
        chunks = bytearray()
        while len(chunks) < self._frame_size and not self._stop.is_set():
            chunk = self._process.stdout.read(self._frame_size - len(chunks))
            if not chunk:
                return None
            chunks.extend(chunk)
        return bytes(chunks) if len(chunks) == self._frame_size else None

    def _read_frames(self):
        try:
            while not self._stop.is_set():
                raw_frame = self._read_exactly()
                if raw_frame is None:
                    break
                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape(
                    self.height,
                    self.width,
                    3,
                )
                with self._condition:
                    self._frame = frame
                    self._sequence += 1
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()

    def isOpened(self):
        return not self._closed and self._process.poll() is None

    def read(self):
        deadline = time.monotonic() + self.read_timeout
        with self._condition:
            while self._sequence <= self._delivered_sequence and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, None
                self._condition.wait(remaining)

            if self._sequence <= self._delivered_sequence or self._frame is None:
                return False, None

            self._delivered_sequence = self._sequence
            return True, self._frame

    def release(self):
        if self._stop.is_set():
            return

        self._stop.set()
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

        if self._process.stdout is not None:
            self._process.stdout.close()
        self._reader.join(timeout=1)
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class FallbackCameraCapture:
    """Keep reading while advancing through camera modes after stream failure."""

    def __init__(
        self,
        candidates,
        *,
        ffmpeg_path,
        read_timeout,
        capture_factory=FFmpegCameraCapture,
    ):
        self._candidates = iter(candidates)
        self._ffmpeg_path = ffmpeg_path
        self._read_timeout = read_timeout
        self._capture_factory = capture_factory
        self._capture = None
        self._closed = False
        self._open_next()

    def _open_next(self):
        if self._capture is not None:
            self._capture.release()
            self._capture = None

        for mode in self._candidates:
            try:
                capture = self._capture_factory(
                    device=mode.device,
                    width=mode.width,
                    height=mode.height,
                    fps=mode.fps,
                    input_format=mode.input_format,
                    read_timeout=self._read_timeout,
                    ffmpeg_path=self._ffmpeg_path,
                )
            except (OSError, RuntimeError):
                continue
            if capture.isOpened():
                self._capture = capture
                return True
            capture.release()
        return False

    def isOpened(self):
        return not self._closed and self._capture is not None

    def read(self):
        while not self._closed and self._capture is not None:
            try:
                success, frame = self._capture.read()
            except (OSError, RuntimeError):
                success, frame = False, None
            if success:
                return True, frame
            if not self._open_next():
                break
        return False, None

    def release(self):
        if self._closed:
            return
        self._closed = True
        if self._capture is not None:
            self._capture.release()
            self._capture = None


def open_first_working_camera(
    candidates,
    *,
    ffmpeg_path,
    read_timeout,
    capture_factory=FFmpegCameraCapture,
):
    """Return the first camera that produces a complete frame."""
    failures = []
    for mode in candidates:
        capture = None
        try:
            capture = capture_factory(
                device=mode.device,
                width=mode.width,
                height=mode.height,
                fps=mode.fps,
                input_format=mode.input_format,
                read_timeout=read_timeout,
                ffmpeg_path=ffmpeg_path,
            )
            success, _frame = capture.read()
            if success:
                return capture
            failures.append(f"{mode.device}: no frame received")
        except (OSError, RuntimeError) as error:
            failures.append(f"{mode.device}: {error}")
        if capture is not None:
            capture.release()

    detail = "; ".join(failures) or "no camera candidates found"
    raise RuntimeError(f"Could not read from a Linux camera: {detail}")


def _ir_camera_modes():
    modes = []
    for sysfs_device in sorted(Path("/sys/class/video4linux").glob("video*")):
        try:
            name = (sysfs_device / "name").read_text().strip()
        except OSError:
            continue
        device = Path("/dev") / sysfs_device.name
        try:
            is_capture_node = (sysfs_device / "index").read_text().strip() == "0"
        except OSError:
            is_capture_node = False
        if "IR Camera" in name and is_capture_node and device.exists():
            modes.append(CameraMode(device, "gray", 640, 360, 15))
    return modes


def prioritize_camera_modes(
    primary,
    alternatives,
    *,
    prefer_ir,
    allow_primary_fallback=True,
):
    alternatives = [mode for mode in alternatives if primary is None or mode.device != primary.device]
    if primary is None:
        return alternatives
    if prefer_ir and alternatives:
        if allow_primary_fallback:
            return [*alternatives, primary]
        return alternatives
    return [primary, *alternatives]


def open_linux_camera(
    camera_index,
    width,
    height,
    fps,
    read_timeout=2.0,
    prefer_ir=False,
    allow_primary_fallback=True,
    primary_input_format="mjpeg",
    allow_ir_fallback=True,
):
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("FFmpeg is required for stable Linux camera capture")

    primary_device = Path(f"/dev/video{camera_index}")
    primary_mode = (
        CameraMode(primary_device, primary_input_format, width, height, fps)
        if primary_device.exists()
        else None
    )
    ir_modes = _ir_camera_modes() if prefer_ir or allow_ir_fallback else []
    candidates = prioritize_camera_modes(
        primary_mode,
        ir_modes,
        prefer_ir=prefer_ir,
        allow_primary_fallback=allow_primary_fallback,
    )

    return FallbackCameraCapture(
        candidates,
        ffmpeg_path=ffmpeg_path,
        read_timeout=read_timeout,
    )

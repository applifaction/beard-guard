#!/usr/bin/env python3
from pathlib import Path
import cv2
import mediapipe as mp
import time
import subprocess
import random
import sys
import shutil
import os
import re
from threading import Thread

from camera_capture import open_linux_camera
from screen_blackout import ScreenBlackoutController
from warning_state import WarningDebouncer

# Import winsound only on Windows
use_winsound = sys.platform.startswith("win")
if use_winsound:
    import winsound

# Base directory of the script
BASE_DIR = Path(__file__).resolve().parent
# Directory containing alarm sound files
ALARMS_DIR = BASE_DIR / "alarms"

APP_NAME = "Beard Guard"
APP_ID = "beardguard"

# Global reference to the current alarm process or flag
alarm_proc = None
window_identity_configured = False

# Determine audio player for Linux and macOS
if sys.platform.startswith("linux"):
    audio_player = "paplay" if shutil.which("paplay") else "aplay"
elif sys.platform == "darwin":
    audio_player = "afplay"
else:
    audio_player = None

def configure_linux_window_identity(force=False):
    """Set the OpenCV X11 window class so Ubuntu/GNOME can map the app icon."""
    global window_identity_configured
    if window_identity_configured and not force:
        return
    if not sys.platform.startswith("linux") or not os.environ.get("DISPLAY") or not shutil.which("xprop"):
        return

    try:
        client_list = subprocess.check_output(
            ["xprop", "-root", "_NET_CLIENT_LIST"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
        )
        window_ids = re.findall(r"0x[0-9a-fA-F]+", client_list)
        current_pid = str(os.getpid())

        for window_id in window_ids:
            props = subprocess.check_output(
                ["xprop", "-id", window_id, "_NET_WM_PID", "_NET_WM_NAME", "WM_NAME"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=0.5,
            )
            if f"_NET_WM_PID(CARDINAL) = {current_pid}" in props and APP_NAME in props:
                subprocess.run(
                    ["xprop", "-id", window_id, "-f", "WM_CLASS", "8s", "-set", "WM_CLASS", APP_ID],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=0.5,
                )
                window_identity_configured = True
                return
    except (subprocess.SubprocessError, OSError):
        return


# Function to play a random alarm sound, stoppable and with auto-timeout
def play_alarm():
    global alarm_proc
    # Skip if already playing
    if use_winsound and alarm_proc:
        return
    if not use_winsound and alarm_proc and alarm_proc.poll() is None:
        return

    # Select random sound file; require at least one file
    sounds = list(ALARMS_DIR.glob("*.wav"))
    if not sounds:
        return  # no alarm files available
    alarm_path = random.choice(sounds)

    # Play depending on platform
    if use_winsound:
        winsound.PlaySound(str(alarm_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        alarm_proc = True
        # Auto-stop after 2 seconds
        def _stop_win():
            time.sleep(alarm_cooldown)
            winsound.PlaySound(None, winsound.SND_PURGE)
            global alarm_proc
            alarm_proc = None
        Thread(target=_stop_win, daemon=True).start()
    else:
        proc = subprocess.Popen([audio_player, str(alarm_path)])
        alarm_proc = proc
        # Auto-stop after 2 seconds
        def _stop_unix(p):
            time.sleep(alarm_cooldown)
            if p.poll() is None:
                p.terminate()
            global alarm_proc
            if alarm_proc is p:
                alarm_proc = None
        Thread(target=_stop_unix, args=(proc,), daemon=True).start()

# Function to stop the alarm when hand moves away
def stop_alarm():
    global alarm_proc
    if not alarm_proc:
        return
    if use_winsound:
        winsound.PlaySound(None, winsound.SND_PURGE)
    else:
        if alarm_proc.poll() is None:
            alarm_proc.terminate()
    alarm_proc = None

# Initialize MediaPipe face mesh and hands solutions
mp_face = mp.solutions.face_mesh
mp_hands = mp.solutions.hands
face_mesh = mp_face.FaceMesh(min_detection_confidence=0.5, min_tracking_confidence=0.5)
hands = mp_hands.Hands(min_detection_confidence=0.8, min_tracking_confidence=0.5)

# This Bison camera's MJPEG endpoint desynchronizes the xHCI transfer ring.
# Its raw YUYV endpoint provides stable color frames, while the IR node emits
# alternating black frames. Capture at the tested 352x288 bandwidth, upscale
# for landmark processing, and fail closed instead of using either faulty stream.
CAMERA_INDEX = 0
CAMERA_WIDTH = 352
CAMERA_HEIGHT = 288
CAMERA_FPS = 30
CAMERA_FOURCC = "MJPG"
PROCESSING_WIDTH = 640
PROCESSING_HEIGHT = 480

if sys.platform.startswith("linux"):
    cap = open_linux_camera(
        camera_index=CAMERA_INDEX,
        width=CAMERA_WIDTH,
        height=CAMERA_HEIGHT,
        fps=CAMERA_FPS,
        prefer_ir=False,
        primary_input_format="yuyv422",
        allow_ir_fallback=False,
    )
else:
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_ANY)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*CAMERA_FOURCC))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    raise RuntimeError(f"Could not open camera {CAMERA_INDEX}")

# Timestamp of last alarm and cooldown in seconds
TRIGGER_HOLD_DURATION = 0.25  # reject isolated landmark jitter before covering the desktop
last_alarm = 0
alarm_cooldown = 2 # seconds

# Black overlays stay visible while the hand is too close, independently of audio.
# Brief missed landmarks are normal; keep the warning stable until tracking has
# remained safe for this long instead of flashing the desktop between frames.
SCREEN_BLACKOUT_ENABLED = True
SCREEN_BLACKOUT_CLEAR_GRACE_DURATION = 0.5
SCREEN_BLACKOUT_RELEASE_TIMEOUT = 1.0  # fail open if camera frames stop arriving

screen_blackout_controller = ScreenBlackoutController(
    enabled=SCREEN_BLACKOUT_ENABLED,
    release_timeout=SCREEN_BLACKOUT_RELEASE_TIMEOUT,
)
warning_debouncer = WarningDebouncer(
    activation_duration=TRIGGER_HOLD_DURATION,
    clear_duration=SCREEN_BLACKOUT_CLEAR_GRACE_DURATION,
)

# Threshold factor for distance relative to face width
DISTANCE_THRESHOLD_FACTOR = 1.00  # 100%

# Upper boundary for the fingertip: include the upper beard near the ears.
HAND_Y_THRESHOLD_FACTOR = 1.0  # Exact eye level; larger values exclude more of the upper beard.

# Lower beard boundary below the chin, relative to the nose-to-chin distance.
# A full distance includes the lower beard without extending into the lap.
CHIN_LINE_OFFSET_FACTOR = 1.0

try:
    while True:
        success, frame = cap.read()
        if not success:
            print("Camera frame read failed; stopping Beard Guard.", file=sys.stderr, flush=True)
            break

        if (frame.shape[1], frame.shape[0]) != (PROCESSING_WIDTH, PROCESSING_HEIGHT):
            frame = cv2.resize(frame, (PROCESSING_WIDTH, PROCESSING_HEIGHT), interpolation=cv2.INTER_LINEAR)

        # Flip image horizontally and convert to RGB
        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Detect face and hand landmarks
        face_results = face_mesh.process(rgb)
        hand_results = hands.process(rgb)

        # Default marker color and hand position validity
        hand_marker_color = (0, 0, 255)  # red
        hand_valid = False
        hand_too_close = False

        if face_results.multi_face_landmarks and hand_results.multi_hand_landmarks:
            face_landmarks = face_results.multi_face_landmarks[0]
            hand_landmarks = hand_results.multi_hand_landmarks[0]
            h, w, _ = frame.shape

            # Chin point: landmark 152
            chin = face_landmarks.landmark[152]
            chin_x, chin_y = int(chin.x * w), int(chin.y * h)

            # Nose tip: landmark 1
            nose = face_landmarks.landmark[1]
            nose_y = int(nose.y * h)

            # Distance between chin and nose tip
            chin_nose_dist = abs(chin_y - nose_y)

            # Position of red safety line below the chin
            line_y = chin_y + int(chin_nose_dist * CHIN_LINE_OFFSET_FACTOR)

            # Index fingertip: landmark 8
            idx_tip = hand_landmarks.landmark[8]
            hand_x, hand_y = int(idx_tip.x * w), int(idx_tip.y * h)

            # Eye-level reference point: landmark 168
            eye = face_landmarks.landmark[168]
            eye_y = int(eye.y * h)

            # Update hand marker color if hand is outside valid region
            if hand_y < eye_y * HAND_Y_THRESHOLD_FACTOR or hand_y > line_y:
                hand_marker_color = (0, 255, 0)  # green
            else:
                hand_valid = True

            # Draw visual markers even if skipping alarm check
            cv2.circle(frame, (chin_x, chin_y), 5, (0, 255, 0), -1)
            cv2.circle(frame, (hand_x, hand_y), 5, hand_marker_color, -1)
            cv2.line(frame, (chin_x, chin_y), (hand_x, hand_y), (255, 0, 0), 2)
            cv2.line(frame, (0, line_y), (w, line_y), (0, 0, 255), 2)

            if hand_valid:
                # Estimate face width using landmarks 234 (left) and 454 (right)
                left = face_landmarks.landmark[234]
                right = face_landmarks.landmark[454]
                face_width = abs(int((right.x - left.x) * w))

                # Calculate distance between chin and fingertip
                dist = ((chin_x - hand_x)**2 + (chin_y - hand_y)**2) ** 0.5
                threshold = face_width * DISTANCE_THRESHOLD_FACTOR

                hand_too_close = dist < threshold

        now = time.monotonic()
        blackout_active = warning_debouncer.update(hand_too_close, now=now)
        if not hand_too_close:
            stop_alarm()

        # Refresh every frame, not just when a sound starts. Audio cooldowns must
        # never uncover the desktop while the hand is still near the beard.
        screen_blackout_controller.set_active(blackout_active)
        if blackout_active and now - last_alarm > alarm_cooldown:
            play_alarm()
            last_alarm = now

        # Apply visual overlay during alarm
        if alarm_proc:
            overlay = frame.copy()
            cv2.rectangle(overlay, (0, 0), (frame.shape[1], frame.shape[0]), (0, 0, 0), -1)
            alpha = 0.6
            cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
            cv2.putText(frame, "STOP!", (frame.shape[1] // 4, frame.shape[0] // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 255), 10)

        # Display the annotated frame
        cv2.imshow(APP_NAME, frame)
        configure_linux_window_identity()

        # Escape may reach the preview before the overlay owns keyboard input.
        # During a warning it must dismiss, not terminate camera detection.
        key = cv2.waitKey(1) & 0xFF
        if key == 27:
            if blackout_active:
                screen_blackout_controller.dismiss()
            else:
                break
        if cv2.getWindowProperty(APP_NAME, cv2.WND_PROP_VISIBLE) < 1:
            break
finally:
    screen_blackout_controller.shutdown()
    stop_alarm()
    cap.release()
    cv2.destroyAllWindows()

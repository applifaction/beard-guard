#!/usr/bin/env python3
from pathlib import Path
import cv2
import mediapipe as mp
import time
import subprocess
import random
import sys
import shutil
from threading import Thread

# Import winsound only on Windows
use_winsound = sys.platform.startswith("win")
if use_winsound:
    import winsound

# Base directory of the script
BASE_DIR = Path(__file__).resolve().parent
# Directory containing alarm sound files
ALARMS_DIR = BASE_DIR / "alarms"

# Global reference to the current alarm process or flag
alarm_proc = None

# Determine audio player for Linux and macOS
if sys.platform.startswith("linux"):
    audio_player = "paplay" if shutil.which("paplay") else "aplay"
elif sys.platform == "darwin":
    audio_player = "afplay"
else:
    audio_player = None

# Function to play a random alarm sound, stoppable and with auto-timeout
def play_alarm():
    global alarm_proc
    # Skip if already playing
    if use_winsound and alarm_proc:
        return
    if not use_winsound and alarm_proc and alarm_proc.poll() is None:
        return

    # Select random sound file
    sounds = list(ALARMS_DIR.glob("*.wav"))
    alarm_path = random.choice(sounds) if sounds else BASE_DIR / "alarm.wav"

    # Play depending on platform
    if use_winsound:
        winsound.PlaySound(str(alarm_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        alarm_proc = True
        # Auto-stop after 2 seconds
        def _stop_win():
            time.sleep(2)
            winsound.PlaySound(None, winsound.SND_PURGE)
            global alarm_proc
            alarm_proc = None
        Thread(target=_stop_win, daemon=True).start()
    else:
        proc = subprocess.Popen([audio_player, str(alarm_path)])
        alarm_proc = proc
        # Auto-stop after 2 seconds
        def _stop_unix(p):
            time.sleep(2)
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

face_mesh = mp_face.FaceMesh(min_detection_confidence=0.5,
                             min_tracking_confidence=0.5)
hands = mp_hands.Hands(min_detection_confidence=0.5,
                       min_tracking_confidence=0.5)

# Open camera (0 = default webcam)
cap = cv2.VideoCapture(0)

# Timestamp of last alarm and cooldown in seconds
last_alarm = 0
alarm_cooldown = 2.0  # seconds

# Threshold factor for distance relative to face width
DISTANCE_THRESHOLD_FACTOR = 1.00  # 100%

while True:
    success, frame = cap.read()
    if not success:
        break

    # Flip image horizontally and convert to RGB
    frame = cv2.flip(frame, 1)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # Detect face and hand landmarks
    face_results = face_mesh.process(rgb)
    hand_results = hands.process(rgb)

    if face_results.multi_face_landmarks and hand_results.multi_hand_landmarks:
        face_landmarks = face_results.multi_face_landmarks[0]
        hand_landmarks = hand_results.multi_hand_landmarks[0]
        h, w, _ = frame.shape

        # Chin point: landmark 152
        chin = face_landmarks.landmark[152]
        chin_x, chin_y = int(chin.x * w), int(chin.y * h)

        # Index fingertip: landmark 8
        idx_tip = hand_landmarks.landmark[8]
        hand_x, hand_y = int(idx_tip.x * w), int(idx_tip.y * h)

        # Estimate face width
        left = face_landmarks.landmark[234]
        right = face_landmarks.landmark[454]
        face_width = abs(int((right.x - left.x) * w))

        # Calculate distance
        dist = ((chin_x - hand_x)**2 + (chin_y - hand_y)**2) ** 0.5
        threshold = face_width * DISTANCE_THRESHOLD_FACTOR

        # Draw visual markers
        cv2.circle(frame, (chin_x, chin_y), 5, (0, 255, 0), -1)
        cv2.circle(frame, (hand_x, hand_y), 5, (0, 0, 255), -1)
        cv2.line(frame, (chin_x, chin_y), (hand_x, hand_y), (255, 0, 0), 2)

        now = time.time()
        if dist < threshold and now - last_alarm > alarm_cooldown:
            play_alarm()
            last_alarm = now
        elif dist >= threshold:
            stop_alarm()

    # Display the annotated frame
    cv2.imshow('Beard Guard', frame)

    # Exit when window is closed or ESC pressed
    if cv2.getWindowProperty('Beard Guard', cv2.WND_PROP_VISIBLE) < 1:
        break
    if cv2.waitKey(1) & 0xFF == 27:
        break

# Cleanup
cap.release()
cv2.destroyAllWindows()
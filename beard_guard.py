#!/usr/bin/env python3
from pathlib import Path
import cv2
import mediapipe as mp
import time
import subprocess
from threading import Thread

# Base directory of the script
BASE_DIR = Path(__file__).resolve().parent

# Function to play the alarm non-blocking using paplay
def play_alarm(filename="alarm.wav"):
    alarm_path = BASE_DIR / filename
    def _play():
        # Use paplay (PulseAudio) to play the sound
        subprocess.run(["paplay", str(alarm_path)], check=False)
    Thread(target=_play, daemon=True).start()

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
alarm_cooldown = 3.0  # seconds

# Threshold factor for distance relative to face width
DISTANCE_THRESHOLD_FACTOR = 0.75  # 75%

while cap.isOpened():
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

        # Estimate face width using landmarks 234 (left) and 454 (right)
        left = face_landmarks.landmark[234]
        right = face_landmarks.landmark[454]
        face_width = abs(int((right.x - left.x) * w))

        # Calculate distance between chin and fingertip
        dist = ((chin_x - hand_x)**2 + (chin_y - hand_y)**2) ** 0.5
        threshold = face_width * DISTANCE_THRESHOLD_FACTOR

        # Visual markers (optional)
        cv2.circle(frame, (chin_x, chin_y), 5, (0, 255, 0), -1)
        cv2.circle(frame, (hand_x, hand_y), 5, (0, 0, 255), -1)
        cv2.line(frame, (chin_x, chin_y), (hand_x, hand_y), (255, 0, 0), 2)

        # Trigger alarm when distance falls below threshold
        now = time.time()
        if dist < threshold and now - last_alarm > alarm_cooldown:
            play_alarm()
            last_alarm = now

    # Display the annotated frame
    cv2.imshow('Beard Guard', frame)
    if cv2.waitKey(1) & 0xFF == 27:  # Esc key to exit
        break

# Release resources
cap.release()
cv2.destroyAllWindows()
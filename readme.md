# Beard Guard: Stop Beard-Pulling & Fingernail Biting Alarm

**Beard Guard** is designed for people with trichotillomania, beard-focused hair pulling, or nail biting who need a subtle, on-screen reminder to keep their hands away from their beard and nails while working at a computer.

## Who Is This For?

People suffering from trichotillomania, compulsive beard-pulling, chronic nail biting, or habitual fingernail chewing looking for a lightweight, privacy-focused tool to curb these behaviors without intrusive hardware.

## Installation & Setup: Prevent Beard-Pulling and Nail Biting

### Windows

1. Install Python 3.8+ and ensure `python` and `pip` are in your PATH.
2. Clone the repository:

   ```powershell
   git clone https://github.com/applifaction/beard-guard.git
   cd beard-guard
   ```
3. Create and activate a virtual environment:

   ```powershell
   python -m venv venv
   venv\Scripts\activate
   ```
4. Install dependencies:

   ```powershell
   pip install --upgrade pip
   pip install opencv-python mediapipe playsound
   ```
5. The `alarm.wav` file is already included in the project folder.
6. **Windows code tweak:** In `beard_guard.py`, replace the existing `play_alarm` function with:

   ```python
   from playsound import playsound
   from threading import Thread

   def play_alarm(path="alarm.wav"):
       Thread(target=playsound, args=(path,), daemon=True).start()
   ```
7. Launch the program:

   ```powershell
   python beard_guard.py
   ```

### Mac

1. Install Homebrew (if not already):

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
2. Use Homebrew to install Python:

   ```bash
   brew install python
   ```
3. Clone the repository:

   ```bash
   git clone https://github.com/applifaction/beard-guard.git
   cd beard-guard
   ```
4. Create and activate a virtual environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
5. Install required packages:

   ```bash
   pip install --upgrade pip
   pip install opencv-python mediapipe
   ```
6. The `alarm.wav` file is already included in the project folder.
7. **Mac code tweak:** In `beard_guard.py`, replace the `play_alarm` function with:

   ```python
   import subprocess
   from threading import Thread

   def play_alarm(path="alarm.wav"):
       def _play():
           subprocess.run(["afplay", path], check=False)
       Thread(target=_play, daemon=True).start()
   ```
8. Run the script:

   ```bash
   python beard_guard.py
   ```

### Ubuntu

1. Install system dependencies:

   ```bash
   sudo apt update
   sudo apt install python3-venv python3-full pulseaudio-utils
   ```
2. Clone the repository:

   ```bash
   git clone https://github.com/applifaction/beard-guard.git
   cd beard-guard
   ```
3. Create and activate a virtual environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
4. Install required Python packages:

   ```bash
   pip install --upgrade pip
   pip install opencv-python mediapipe
   ```
5. The `alarm.wav` file is already included in the project folder.
6. Run the alarm tool:

   ```bash
   python beard_guard.py
   ```

## Key Features

* Real-time face and hand landmark detection (MediaPipe) preventing beard-pulling and nail-biting
* Customizable distance threshold to control sensitivity
* Works on Windows (`playsound`), Mac (`afplay`), and Ubuntu (`paplay`)
* Lightweight, privacy-focused—everything runs locally without sending video data anywhere

## Configuration & Sensitivity

Adjust how early the alarm triggers by editing **`DISTANCE_THRESHOLD_FACTOR`** in `beard_guard.py`:

```python
# Default: 1.00 (100% of face width)
DISTANCE_THRESHOLD_FACTOR = 1.00
```

Increase or decrease this value to fine-tune when the alert fires.

## Autostart on Login

### Windows Startup

1. Create `run_beard_guard.bat`:

   ```bat
   @echo off
   cd /d C:\path\to\beard-guard
   venv\Scripts\activate
   python beard_guard.py
   ```
2. Place a shortcut to this batch file in the Windows Startup folder (`Win+R`, `shell:startup`).

### Mac Launch Agents

Create `~/Library/LaunchAgents/com.yourusername.beardguard.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.yourusername.beardguard</string>
    <key>ProgramArguments</key>
    <array>
      <string>/usr/local/bin/python3</string>
      <string>/full/path/to/beard-guard/beard_guard.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
  </dict>
</plist>
```

Load with:

```bash
launchctl load ~/Library/LaunchAgents/com.yourusername.beardguard.plist
```

### Ubuntu Auto-Launch

Create `~/.config/autostart/beard_guard.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Beard Guard
Exec=/full/path/to/venv/bin/python /full/path/to/beard_guard.py
X-GNOME-Autostart-enabled=true
```

## Requirements

* Python 3.8 or higher
* USB or integrated webcam

---

## License

This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.

**Contribute**: Issues and pull requests are welcome. Let’s help more people overcome trichotillomania and nail biting with Beard Guard!

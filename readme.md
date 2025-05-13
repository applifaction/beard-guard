# Beard Guard: Stop Beard-Pulling & Fingernail Biting Alarm

**Beard Guard** helps users with trichotillomania, compulsive beard-pulling, and chronic nail biting by sounding a random alarm whenever their hand approaches the beard or nails. It runs on Windows, macOS, and Ubuntu without any manual configuration.

## Who Is This For?

Anyone struggling with trichotillomania, beard-focused hair pulling, or habitual fingernail chewing, who wants a lightweight, privacy-focused reminder to keep their hands away while using the computer.

## Installation & Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/applifaction/beard-guard.git
   cd beard-guard
   ```
2. Create and activate a virtual environment:

   ```bash
   python3 -m venv venv          # or `python -m venv venv` on Windows
   source venv/bin/activate     # or `venv\\Scripts\\activate` on Windows
   ```
3. Install required Python packages:

   ```bash
   pip install --upgrade pip
   pip install opencv-python mediapipe
   ```
4. Run the application:

   ```bash
   python beard_guard.py
   ```

## Autostart on Login

### Windows

1. Create a batch file `run_beard_guard.bat` in the project root:

   ```bat
   @echo off
   cd /d C:\path\to\beard-guard
   venv\\Scripts\\activate
   python beard_guard.py
   ```
2. Place a shortcut to this file in your Startup folder (`Win+R`, enter `shell:startup`).

### macOS

1. Create a Launch Agent at `~/Library/LaunchAgents/com.applifaction.beardguard.plist`:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
     <dict>
       <key>Label</key>
       <string>com.applifaction.beardguard</string>
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
2. Load it:

   ```bash
   launchctl load ~/Library/LaunchAgents/com.applifaction.beardguard.plist
   ```

### Ubuntu

Create `~/.config/autostart/beard_guard.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Beard Guard
Exec=/full/path/to/venv/bin/python /full/path/to/beard-guard/beard_guard.py
X-GNOME-Autostart-enabled=true
```

## Key Features

* Real-time face and hand landmark detection (MediaPipe) to detect when your hand nears your beard or nails
* Randomized alarm playback to prevent habituation
* Alarm automatically stops after 2 seconds or as soon as you move your hand away
* Cross-platform audio support: no additional setup required
* Fully local processing—no video data leaves your machine

## Requirements

* Python 3.8 or higher
* USB or integrated webcam

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

**Contribute:** Issues and pull requests are welcome. Help more people curb unwanted habits with Beard Guard!

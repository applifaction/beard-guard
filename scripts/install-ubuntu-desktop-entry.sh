#!/usr/bin/env bash
set -euo pipefail

APP_ID="beardguard"
APP_NAME="Beard Guard"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_ICON="$PROJECT_DIR/assets/icons/beardguard.png"
PYTHON_BIN="$PROJECT_DIR/venv/bin/python"
APP_SCRIPT="$PROJECT_DIR/beard_guard.py"
APP_LAUNCHER="$PROJECT_DIR/beardguard.sh"
WATCHDOG_SCRIPT="$PROJECT_DIR/scripts/beardguard-camera-watchdog.sh"

if [[ ! -f "$SOURCE_ICON" ]]; then
  echo "Missing icon: $SOURCE_ICON" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing executable Python virtualenv: $PYTHON_BIN" >&2
  echo "Create it with: python3 -m venv venv && source venv/bin/activate && pip install opencv-python mediapipe" >&2
  exit 1
fi

if [[ ! -f "$APP_SCRIPT" ]]; then
  echo "Missing app script: $APP_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$APP_LAUNCHER" ]]; then
  echo "Missing launcher script: $APP_LAUNCHER" >&2
  exit 1
fi

if [[ ! -f "$WATCHDOG_SCRIPT" ]]; then
  echo "Missing watchdog script: $WATCHDOG_SCRIPT" >&2
  exit 1
fi

chmod +x "$APP_LAUNCHER" "$WATCHDOG_SCRIPT"

resize_tool="copy"
if command -v python3 >/dev/null 2>&1 && python3 - <<'PY' >/dev/null 2>&1
from PIL import Image
PY
then
  resize_tool="pillow"
elif command -v magick >/dev/null 2>&1; then
  resize_tool="magick"
elif command -v convert >/dev/null 2>&1; then
  resize_tool="convert"
fi

for size in 16 24 32 48 64 128 256 512; do
  target_dir="$HOME/.local/share/icons/hicolor/${size}x${size}/apps"
  target_file="$target_dir/$APP_ID.png"
  mkdir -p "$target_dir"

  case "$resize_tool" in
    pillow)
      SIZE="$size" SOURCE_ICON="$SOURCE_ICON" TARGET_FILE="$target_file" python3 - <<'PY'
import os
from pathlib import Path
from PIL import Image

size = int(os.environ["SIZE"])
source = Path(os.environ["SOURCE_ICON"])
target = Path(os.environ["TARGET_FILE"])
image = Image.open(source).convert("RGBA").resize((size, size), Image.LANCZOS)
image.save(target)
PY
      ;;
    magick)
      magick "$SOURCE_ICON" -resize "${size}x${size}" "$target_file"
      ;;
    convert)
      convert "$SOURCE_ICON" -resize "${size}x${size}" "$target_file"
      ;;
    *)
      cp "$SOURCE_ICON" "$target_file"
      ;;
  esac
done

applications_dir="$HOME/.local/share/applications"
autostart_dir="$HOME/.config/autostart"
mkdir -p "$applications_dir" "$autostart_dir"

desktop_file="$applications_dir/$APP_ID.desktop"
cat > "$desktop_file" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Local webcam reminder to keep hands away from beard and nails
Exec=/usr/bin/env BEARDGUARD_DESKTOP_LAUNCH=1 $APP_LAUNCHER
Path=$PROJECT_DIR
Icon=$APP_ID
StartupWMClass=$APP_ID
Terminal=false
Categories=Utility;
StartupNotify=false
X-GNOME-Autostart-enabled=true
DESKTOP

chmod 644 "$desktop_file"

watchdog_desktop_file="$autostart_dir/$APP_ID-watchdog.desktop"
watchdog_service_file="$HOME/.config/systemd/user/$APP_ID-watchdog.service"

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$(dirname "$watchdog_service_file")"
  cat > "$watchdog_service_file" <<SERVICE
[Unit]
Description=$APP_NAME camera-aware autostart watchdog
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$WATCHDOG_SCRIPT
Restart=always
RestartSec=5

[Install]
WantedBy=graphical-session.target
SERVICE
  chmod 644 "$watchdog_service_file"
  rm -f "$watchdog_desktop_file"
  systemctl --user daemon-reload
  systemctl --user enable --now "$APP_ID-watchdog.service"
else
  cat > "$watchdog_desktop_file" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$APP_NAME Camera Watchdog
Comment=Restart Beard Guard after other camera apps release the webcam
Exec=$WATCHDOG_SCRIPT
Path=$PROJECT_DIR
Icon=$APP_ID
Terminal=false
Categories=Utility;
StartupNotify=false
X-GNOME-Autostart-enabled=true
DESKTOP
  chmod 644 "$watchdog_desktop_file"
fi

# Remove both current and legacy direct-launch entries. The watchdog is the
# only autostart owner; direct entries bypass camera arbitration and locking.
rm -f "$autostart_dir/$APP_ID.desktop" "$autostart_dir/beard_guard.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1 && [[ -f "$HOME/.local/share/icons/hicolor/index.theme" ]]; then
  gtk-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "Installed $APP_NAME desktop entry: $desktop_file"
if [[ -f "$watchdog_service_file" ]]; then
  echo "Installed restartable watchdog user service: $watchdog_service_file"
else
  echo "Installed watchdog autostart entry: $watchdog_desktop_file"
fi
echo "Installed icons under: $HOME/.local/share/icons/hicolor/*/apps/$APP_ID.png"

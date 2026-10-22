#!/bin/bash
set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_FILE="$HOME/.local/share/applications/beardguard.desktop"

# When a user starts this script manually on Ubuntu/GNOME, route through the
# desktop entry so GNOME can associate the window with the Beard Guard icon.
# The desktop entry sets BEARDGUARD_DESKTOP_LAUNCH=1 to avoid recursion.
if [[ -z "${BEARDGUARD_DESKTOP_LAUNCH:-}" ]] && command -v gtk-launch >/dev/null 2>&1 && [[ -f "$DESKTOP_FILE" ]]; then
  gtk-launch beardguard >/dev/null 2>&1 &
  exit 0
fi

# One camera stream per login session. Repeated desktop clicks previously
# launched competing V4L2 readers, which destabilized the USB camera stream.
if command -v flock >/dev/null 2>&1; then
  lock_file="${XDG_RUNTIME_DIR:-/tmp}/beardguard-${UID}.lock"
  exec 9>"$lock_file"
  if ! flock -n 9; then
    exit 0
  fi
fi

cd "$SCRIPT_PATH" || exit

if [[ -z "${BEARDGUARD_DESKTOP_LAUNCH:-}" ]] && command -v title-spin-run >/dev/null 2>&1; then
  title-spin-run "Beardguard" ./venv/bin/python ./beard_guard.py
else
  exec ./venv/bin/python ./beard_guard.py
fi
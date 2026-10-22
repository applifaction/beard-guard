#!/usr/bin/env bash
set -euo pipefail

CAMERA_ID="5986:1193"
CONFIG_PATH="${BEARDGUARD_UVC_CONFIG_PATH:-/etc/modprobe.d/beardguard-webcam.conf}"
EFFECTIVE_UID="${BEARDGUARD_EFFECTIVE_UID:-$EUID}"
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"

if [[ "$EFFECTIVE_UID" != "0" ]]; then
  if ! command -v pkexec >/dev/null 2>&1; then
    echo "Administrator access is required. Run: sudo $SCRIPT_PATH" >&2
    exit 1
  fi
  exec pkexec "$SCRIPT_PATH" "$@"
fi

if [[ "${BEARDGUARD_SKIP_CAMERA_CHECK:-0}" != "1" ]] \
  && command -v lsusb >/dev/null 2>&1 \
  && ! lsusb -d "$CAMERA_ID" | grep -Fq "$CAMERA_ID"; then
  echo "Bison camera $CAMERA_ID was not found; refusing to install its workaround." >&2
  exit 1
fi

CONFIG_CONTENT="# Bison 5986:1193 overstates isochronous payload bandwidth on Linux 7.0.
# Force UVC to calculate the uncompressed stream bandwidth.
options uvcvideo nodrop=0 quirks=0x80"

if [[ -f "$CONFIG_PATH" ]] && [[ "$(cat "$CONFIG_PATH")" == "$CONFIG_CONTENT" ]]; then
  echo "UVC bandwidth workaround is already installed: $CONFIG_PATH"
  exit 0
fi

install -d -m 0755 "$(dirname "$CONFIG_PATH")"
if [[ -e "$CONFIG_PATH" ]]; then
  backup="$CONFIG_PATH.bak-$(date '+%Y%m%d-%H%M%S')"
  cp -a -- "$CONFIG_PATH" "$backup"
  echo "Backed up existing configuration: $backup"
fi

umask 022
printf '%s\n' "$CONFIG_CONTENT" > "$CONFIG_PATH"
chmod 0644 "$CONFIG_PATH"
echo "Installed UVC bandwidth workaround: $CONFIG_PATH"

if [[ -r /sys/module/uvcvideo/parameters/quirks ]] \
  && [[ "$(cat /sys/module/uvcvideo/parameters/quirks)" == "128" ]]; then
  echo "The workaround is already active in the running kernel."
else
  echo "Restart Ubuntu to activate the workaround."
fi

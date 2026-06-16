# Beard Guard Camera Watchdog Design

## Goal

Add a small Ubuntu/GNOME watchdog that restarts Beard Guard after a video call ends. The user will manually stop Beard Guard before joining a call. The watchdog must avoid immediately restarting Beard Guard in the short gap between stopping Beard Guard and the call taking the camera.

## Recommended approach

Use a stateful, call-aware watchdog script.

The watchdog runs at login and periodically checks whether Beard Guard is running and whether a non-Beard-Guard process is using the webcam. After Beard Guard has been stopped, the watchdog waits for evidence that another process used the camera. Only after that process releases the camera and the camera stays free for a short grace period does the watchdog restart Beard Guard.

This avoids the main failure mode of a simple “camera free means start” loop: restarting Beard Guard immediately after the user stops it but before the video call has acquired the camera.

## Components

### `scripts/beardguard-camera-watchdog.sh`

A long-running shell script for Ubuntu. It maintains a simple state machine:

1. `running`: Beard Guard is running. The watchdog sleeps and keeps monitoring.
2. `waiting_for_call`: Beard Guard is not running. The watchdog waits for a non-Beard-Guard process to open a camera device.
3. `in_call`: A non-Beard-Guard process is using the camera. The watchdog waits until the camera is released.
4. `resume_pending`: The camera is free. The watchdog waits for a configurable stability period.
5. `restart`: The watchdog starts Beard Guard via the desktop launcher, then returns to `running`.

The script should be configurable through environment variables:

- `BEARDGUARD_CAMERA_DEVICES`: optional space-separated camera devices; defaults to `/dev/video*`.
- `BEARDGUARD_WATCHDOG_POLL_SECONDS`: polling interval; default `5`.
- `BEARDGUARD_RESUME_DELAY_SECONDS`: free-camera stability period before restart; default `20`.
- `BEARDGUARD_START_ON_LOGIN`: whether to start Beard Guard at login if no camera is in use; default `1`.

### Ubuntu desktop/autostart integration

`beardguard.desktop` continues to launch Beard Guard itself with the app icon.

A separate user-level autostart file, `beardguard-watchdog.desktop`, starts the watchdog at login. This keeps responsibilities separate:

- `beardguard.desktop`: “start the app now”
- `beardguard-watchdog.desktop`: “keep Beard Guard coming back after calls”

The existing installer script should install both desktop entries.

### Launch behavior

The watchdog should start Beard Guard through `gtk-launch beardguard` when available. This preserves the Ubuntu/GNOME icon mapping already added for Beard Guard. If `gtk-launch` is not available, it can fall back to executing `beardguard.sh` directly.

## Camera-use detection

On Linux, the watchdog can detect webcam usage with `fuser` or `lsof` against `/dev/video*`.

The detection must ignore Beard Guard’s own Python process. Any other process holding a camera device counts as “call or camera use”. This includes browsers, Zoom, Teams, Slack, OBS, and other camera tools.

If neither `fuser` nor `lsof` is available, the watchdog should fail clearly with a helpful message instead of silently pretending to work.

## Edge cases

- If no `/dev/video*` device exists, the watchdog waits and retries.
- If Beard Guard is stopped and no call ever uses the camera, the watchdog stays in `waiting_for_call` instead of immediately restarting it.
- If a call briefly releases and reacquires the camera, the resume delay prevents premature restart.
- If Beard Guard crashes while it was supposed to be running, the watchdog may restart it after the normal checks, but it should not fight an intentional manual stop before a call.
- If multiple cameras exist, any non-Beard-Guard use of any configured camera counts as camera activity.

## Testing plan

1. Validate shell syntax for the watchdog and installer.
2. Install desktop entries.
3. Start the watchdog manually in a temporary test mode.
4. Confirm initial startup behavior when the camera is free.
5. Stop Beard Guard manually and confirm the watchdog does not immediately restart it.
6. Simulate non-Beard-Guard camera use with a short Python/OpenCV holder process.
7. Confirm the watchdog restarts Beard Guard only after the holder releases the camera and the resume delay passes.
8. Confirm the restarted Beard Guard has `WM_CLASS=beardguard`, so GNOME can use the app icon.

## Out of scope

- Automatically stopping Beard Guard when a video call begins.
- Per-application allowlists for Zoom, browser, Teams, etc.
- A graphical tray menu or pause button.
- Cross-platform watchdog support for Windows or macOS.

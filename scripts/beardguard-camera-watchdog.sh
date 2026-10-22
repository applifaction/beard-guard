#!/usr/bin/env bash
set -euo pipefail

APP_ID="beardguard"
APP_NAME="Beard Guard"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${BEARDGUARD_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
APP_SCRIPT="$PROJECT_DIR/beard_guard.py"
APP_LAUNCHER="$PROJECT_DIR/beardguard.sh"

POLL_SECONDS="${BEARDGUARD_WATCHDOG_POLL_SECONDS:-5}"
RESUME_DELAY_SECONDS="${BEARDGUARD_RESUME_DELAY_SECONDS:-20}"
STARTUP_GRACE_SECONDS="${BEARDGUARD_STARTUP_GRACE_SECONDS:-20}"
STARTUP_RETRY_SECONDS="${BEARDGUARD_STARTUP_RETRY_SECONDS:-10}"
STARTUP_FAILURE_WINDOW_SECONDS="${BEARDGUARD_STARTUP_FAILURE_WINDOW_SECONDS:-120}"
CLAIM_GUARD_SECONDS="${BEARDGUARD_CLAIM_GUARD_SECONDS:-2}"
NO_CALL_RESTART_SECONDS="${BEARDGUARD_NO_CALL_RESTART_SECONDS:-300}"
START_ON_LOGIN="${BEARDGUARD_START_ON_LOGIN:-1}"
LOG_FILE="${BEARDGUARD_WATCHDOG_LOG:-$HOME/.local/state/beardguard/watchdog.log}"
XHCI_WATCHDOG_UNIT="${BEARDGUARD_XHCI_WATCHDOG_UNIT:-xhci-input-watchdog.service}"
XHCI_FAULT_LATCH="${BEARDGUARD_XHCI_FAULT_LATCH:-$HOME/.local/state/beardguard/camera-fault-latched}"

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$XHCI_FAULT_LATCH")"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE" >&2
}

sleep_poll() {
  sleep "$POLL_SECONDS"
}

pid_cmdline() {
  local pid="$1"
  [[ -r "/proc/$pid/cmdline" ]] || return 0
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true
}

pid_cwd() {
  local pid="$1"
  readlink -f "/proc/$pid/cwd" 2>/dev/null || true
}

is_beardguard_identity() {
  local pid="$1" arg cwd candidate
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cwd="$(pid_cwd "$pid")"

  while IFS= read -r -d '' arg; do
    case "$arg" in
      beard_guard.py|*/beard_guard.py)
        if [[ "$arg" == /* ]]; then
          candidate="$(readlink -f -- "$arg" 2>/dev/null || true)"
        else
          candidate="$(readlink -f -- "$cwd/$arg" 2>/dev/null || true)"
        fi
        [[ "$candidate" == "$APP_SCRIPT" ]] && return 0
        ;;
    esac
  done < "/proc/$pid/cmdline"
  return 1
}

is_beardguard_pid() {
  local current_pid="$1" parent_pid depth
  for ((depth = 0; depth < 8 && current_pid > 1; depth++)); do
    is_beardguard_identity "$current_pid" && return 0
    parent_pid="$(awk '$1 == "PPid:" { print $2; exit }' "/proc/$current_pid/status" 2>/dev/null)" || return 1
    [[ "$parent_pid" =~ ^[0-9]+$ ]] || return 1
    current_pid="$parent_pid"
  done
  return 1
}

beardguard_pids() {
  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    is_beardguard_pid "$pid" && printf '%s\n' "$pid"
  done < <(pgrep -f '(^|[ /])beard_guard\.py([[:space:]]|$)' 2>/dev/null || true)
}

beardguard_running() {
  [[ -n "$(beardguard_pids)" ]]
}

descendant_pids() {
  local parent_pid="$1" child_pid
  while IFS= read -r child_pid; do
    [[ "$child_pid" =~ ^[0-9]+$ ]] || continue
    descendant_pids "$child_pid"
    printf '%s\n' "$child_pid"
  done < <(pgrep -P "$parent_pid" 2>/dev/null || true)
}

stop_beardguard() {
  local reason="$1" pids descendants targets alive pid
  pids="$(beardguard_pids)"
  [[ -n "$pids" ]] || return 0
  descendants=""
  for pid in $pids; do
    descendants+="$(descendant_pids "$pid") "
  done
  targets="$descendants $pids"
  log "Stopping $APP_NAME: $reason"
  # Stop camera-owning descendants before their parent can be reparented.
  kill -TERM $targets 2>/dev/null || true
  for _ in {1..20}; do
    alive=""
    for pid in $targets; do
      kill -0 "$pid" 2>/dev/null && alive+="$pid "
    done
    [[ -z "$alive" ]] && return 0
    sleep 0.1
  done
  log "Forcing remaining $APP_NAME camera processes to stop: $alive"
  kill -KILL $alive 2>/dev/null || true
}

camera_devices() {
  local device
  if [[ -n "${BEARDGUARD_CAMERA_DEVICES:-}" ]]; then
    for device in $BEARDGUARD_CAMERA_DEVICES; do
      [[ -e "$device" ]] && printf '%s\n' "$device"
    done
  else
    compgen -G '/dev/video*' | sort || true
  fi
}

camera_user_pids() {
  local device pid
  if command -v fuser >/dev/null 2>&1; then
    while IFS= read -r device; do
      [[ -e "$device" ]] || continue
      fuser "$device" 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+$' || true
    done < <(camera_devices)
  elif command -v lsof >/dev/null 2>&1; then
    while IFS= read -r device; do
      [[ -e "$device" ]] || continue
      lsof -t -- "$device" 2>/dev/null || true
    done < <(camera_devices)
  else
    log "ERROR: Need fuser or lsof to detect camera usage. Install psmisc or lsof."
    exit 1
  fi | sort -n -u
}

foreign_camera_pids() {
  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    is_beardguard_pid "$pid" || printf '%s\n' "$pid"
  done < <(camera_user_pids)
}

foreign_camera_in_use() {
  [[ -n "$(foreign_camera_pids)" ]]
}

xhci_camera_storm_since() {
  local since_epoch="$1"
  (( since_epoch > 0 )) || return 1
  # Do not use grep -q with pipefail: an early grep exit can turn journalctl's
  # SIGPIPE into a false negative when the journal window is large.
  journalctl -u "$XHCI_WATCHDOG_UNIT" --since "@$since_epoch" --no-pager -o cat 2>/dev/null \
    | grep -F "Recovery deferred: camera is active" >/dev/null
}

latch_camera_fault() {
  printf '%s\n' "$(date --iso-8601=seconds)" > "$XHCI_FAULT_LATCH"
}

clear_camera_fault() {
  rm -f "$XHCI_FAULT_LATCH"
}

xhci_controller_recovered_since_fault() {
  local fault_epoch
  [[ -e "$XHCI_FAULT_LATCH" ]] || return 1
  fault_epoch="$(stat -c %Y -- "$XHCI_FAULT_LATCH" 2>/dev/null)" || return 1
  [[ "$fault_epoch" =~ ^[0-9]+$ ]] || return 1
  journalctl -u "$XHCI_WATCHDOG_UNIT" --since "@$fault_epoch" --no-pager -o cat 2>/dev/null \
    | grep -F "recovered successfully" >/dev/null
}

clear_recovered_camera_fault() {
  xhci_controller_recovered_since_fault || return 1
  log "Confirmed xHCI controller recovery; clearing stale camera-fault latch and re-enabling automatic starts."
  clear_camera_fault
}

mark_running() {
  running_origin="${1:-observed}"
  state="running"
  running_since="$(date +%s)"
}

launch_beardguard() {
  if beardguard_running; then
    log "$APP_NAME is already running; not starting another instance."
    return 0
  fi
  if foreign_camera_in_use; then
    log "Not starting $APP_NAME; camera is in use: $(format_foreign_camera_users)"
    return 1
  fi

  # Close the check/start race: another camera client gets a short window to
  # claim the device before Beard Guard opens it.
  if [[ "$CLAIM_GUARD_SECONDS" != "0" ]]; then
    sleep "$CLAIM_GUARD_SECONDS"
  fi
  if foreign_camera_in_use; then
    log "Not starting $APP_NAME; camera became busy during claim guard: $(format_foreign_camera_users)"
    return 1
  fi

  log "Starting $APP_NAME."
  if command -v gtk-launch >/dev/null 2>&1 && [[ -f "$HOME/.local/share/applications/$APP_ID.desktop" ]]; then
    gtk-launch "$APP_ID" >/tmp/beardguard-launch.log 2>&1 &
  else
    BEARDGUARD_DESKTOP_LAUNCH=1 "$APP_LAUNCHER" >/tmp/beardguard-launch.log 2>&1 &
  fi
}

format_foreign_camera_users() {
  local pid cmdline output=""
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    cmdline="$(pid_cmdline "$pid")"
    output+="${pid}:${cmdline:-unknown} "
  done < <(foreign_camera_pids)
  printf '%s' "${output:-unknown}"
}

if [[ ! -f "$APP_SCRIPT" ]]; then
  log "ERROR: Missing app script: $APP_SCRIPT"
  exit 1
fi

if [[ ! -f "$APP_LAUNCHER" ]]; then
  log "ERROR: Missing launcher script: $APP_LAUNCHER"
  exit 1
fi

log "Watchdog started. poll=${POLL_SECONDS}s startup_grace=${STARTUP_GRACE_SECONDS}s startup_retry=${STARTUP_RETRY_SECONDS}s startup_failure_window=${STARTUP_FAILURE_WINDOW_SECONDS}s resume_delay=${RESUME_DELAY_SECONDS}s claim_guard=${CLAIM_GUARD_SECONDS}s no_call_restart=${NO_CALL_RESTART_SECONDS}s start_on_login=${START_ON_LOGIN}"

clear_recovered_camera_fault || true
if [[ -e "$XHCI_FAULT_LATCH" ]]; then
  state="fault_quarantine"
  log "Persistent camera-fault latch is active; automatic $APP_NAME starts are disabled until a manual start."
else
  state="initial"
fi
free_since=0
waiting_for_call_since=0
restart_after_seconds="$NO_CALL_RESTART_SECONDS"
running_since=0
running_origin="observed"
camera_seen=0

while true; do
  if [[ -z "$(camera_devices)" ]]; then
    if (( camera_seen )); then
      if [[ "$state" != "camera_fault" && "$state" != "fault_quarantine" ]]; then
        log "Camera device disappeared while state=$state; entering fault quarantine."
        [[ "$state" == "running" ]] && stop_beardguard "camera device disappeared"
        latch_camera_fault
      fi
      state="camera_fault"
    elif [[ "$state" != "fault_quarantine" ]]; then
      [[ "$state" == "startup_no_camera" ]] || log "No /dev/video* camera devices found at startup; waiting."
      state="startup_no_camera"
    fi
    sleep_poll
    continue
  fi

  if (( ! camera_seen )); then
    camera_seen=1
    [[ "$state" == "startup_no_camera" ]] && state="initial"
  elif [[ "$state" == "camera_fault" ]]; then
    if foreign_camera_in_use; then
      log "Camera returned and is in use by another process; keeping automatic $APP_NAME starts disabled."
    else
      log "Camera returned after an unexpected disconnect; withholding automatic $APP_NAME start until it is started manually."
    fi
    state="fault_quarantine"
  fi

  case "$state" in
    initial)
      if beardguard_running; then
        log "$APP_NAME is running."
        mark_running
      elif foreign_camera_in_use; then
        log "Camera already in use by another process: $(format_foreign_camera_users)"
        state="in_call"
      elif [[ "$START_ON_LOGIN" == "1" ]]; then
        free_since="$(date +%s)"
        log "Camera is free at startup. Waiting ${STARTUP_GRACE_SECONDS}s before starting $APP_NAME."
        state="startup_pending"
      else
        waiting_for_call_since="$(date +%s)"
        restart_after_seconds="$NO_CALL_RESTART_SECONDS"
        log "$APP_NAME is stopped; waiting up to ${restart_after_seconds}s for next camera use before auto-resume."
        state="waiting_for_call"
      fi
      ;;

    startup_pending)
      if beardguard_running; then
        log "$APP_NAME started manually during startup grace."
        mark_running
      elif foreign_camera_in_use; then
        log "Camera claimed during startup grace: $(format_foreign_camera_users)"
        state="in_call"
      elif (( $(date +%s) - free_since >= STARTUP_GRACE_SECONDS )); then
        if launch_beardguard; then mark_running "startup"; else state="in_call"; fi
      fi
      ;;

    running)
      if xhci_camera_storm_since "$running_since"; then
        log "xHCI camera error storm detected while $APP_NAME owns the camera; stopping capture and latching automatic restart."
        latch_camera_fault
        stop_beardguard "xHCI camera error storm"
        state="fault_quarantine"
      elif foreign_camera_in_use; then
        log "Another process is using the camera: $(format_foreign_camera_users)"
        stop_beardguard "yielding camera to another process"
        state="in_call"
      elif ! beardguard_running; then
        now="$(date +%s)"
        waiting_for_call_since="$now"
        if [[ "$running_origin" == "startup" ]] && (( now - running_since < STARTUP_FAILURE_WINDOW_SECONDS )); then
          restart_after_seconds="$STARTUP_RETRY_SECONDS"
          log "Early startup exit after $((now - running_since))s. Retrying $APP_NAME in ${restart_after_seconds}s."
        else
          restart_after_seconds="$NO_CALL_RESTART_SECONDS"
          log "$APP_NAME stopped. Waiting up to ${restart_after_seconds}s for another process to use the camera before fallback restart."
        fi
        state="waiting_for_call"
      fi
      ;;

    waiting_for_call)
      if beardguard_running; then
        log "$APP_NAME started manually."
        mark_running
      elif foreign_camera_in_use; then
        log "Detected camera use by another process: $(format_foreign_camera_users)"
        state="in_call"
      elif (( restart_after_seconds > 0 && $(date +%s) - waiting_for_call_since >= restart_after_seconds )); then
        log "No camera use detected for ${restart_after_seconds}s. Attempting to restart $APP_NAME."
        if launch_beardguard; then mark_running "fallback"; else state="in_call"; fi
      fi
      ;;

    in_call)
      if foreign_camera_in_use; then
        beardguard_running && stop_beardguard "camera is owned by another process"
      else
        free_since="$(date +%s)"
        log "Camera released cleanly. Waiting ${RESUME_DELAY_SECONDS}s before restarting $APP_NAME."
        state="resume_pending"
      fi
      ;;

    resume_pending)
      if beardguard_running; then
        log "$APP_NAME started manually during resume delay."
        mark_running
      elif foreign_camera_in_use; then
        log "Camera used again during resume delay: $(format_foreign_camera_users)"
        state="in_call"
      elif (( $(date +%s) - free_since >= RESUME_DELAY_SECONDS )); then
        if launch_beardguard; then mark_running; else state="in_call"; fi
      fi
      ;;

    fault_quarantine)
      if clear_recovered_camera_fault; then
        state="initial"
      elif beardguard_running; then
        log "$APP_NAME started manually after camera fault; clearing fault latch for this attempt."
        clear_camera_fault
        mark_running
      fi
      ;;

    *)
      log "Unknown state '$state'; resetting conservatively."
      state="fault_quarantine"
      ;;
  esac

  sleep_poll
done

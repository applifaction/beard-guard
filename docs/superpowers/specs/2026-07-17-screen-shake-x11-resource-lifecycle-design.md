# Screen-Shake X11 Resource Lifecycle Design

## Context

Beard Guard currently starts every desktop-wide screen-shake warning in a new daemon thread. Each thread creates and destroys its own `tkinter.Tk()` root. On GNOME Wayland, Tkinter uses XWayland for these overlay windows.

During the incident, the long-running Beard Guard process owned 220 of 253 active XWayland client connections. XWayland then rejected new clients with `Maximum number of clients reached`, which prevented Flameshot from opening after pressing PrintScreen.

An isolated Xvfb probe reproduced the leak deterministically:

- 60 threaded Tk root create/destroy cycles retained 60 additional X11 sockets.
- 60 OpenCV `namedWindow`/`destroyWindow` cycles retained no additional sockets after initial OpenCV setup.

The screen-shake Tk lifecycle is therefore the root cause. The OpenCV preview window is not part of this fix.

## Goal

Preserve the existing desktop-wide earthquake-style warning while keeping Beard Guard's X11/XWayland connection count bounded for the complete process lifetime.

## Non-goals

- Replacing Tkinter with another GUI toolkit.
- Changing hand or face detection behavior.
- Changing alarm sounds, cooldowns, or watchdog camera ownership behavior.
- Refactoring unrelated OpenCV application code.

## Architecture

Move the screen-shake implementation into a focused `screen_shake.py` module containing a `ScreenShakeController`.

The controller owns exactly one background thread. That thread owns all Tkinter objects and is the only thread allowed to call Tkinter APIs. It lazily creates one `tkinter.Tk()` root on the first trigger and keeps that root alive until controller shutdown.

The root and monitor overlay windows are reused between alarms. Finishing an animation hides the overlay windows instead of destroying the root. If the connected monitor layout changes, the controller updates geometries and may add or remove `Toplevel` children while retaining the same root and X11 display connection.

The existing Wayland behavior remains unchanged: Beard Guard does not request a desktop screenshot through the GNOME portal. It renders the immediate red fallback overlay instead.

## Components and data flow

### `ScreenShakeController`

The controller accepts the existing animation configuration: enabled state, duration, pixel amplitude, and step interval.

Its public lifecycle is:

- `trigger()`: starts the worker lazily and submits one animation request without blocking camera processing.
- `shutdown()`: asks the worker to destroy its windows and root, waits for it to stop, and is safe to call more than once.

A bounded queue with capacity one carries trigger requests from the OpenCV thread to the Tkinter worker. If a request is already queued or an animation is active, additional requests are coalesced. They do not create threads, roots, or unbounded queue entries.

The Tkinter thread uses `root.after()` callbacks for animation timing. It does not run a manual `root.update()` plus `time.sleep()` loop. Tkinter's event loop therefore remains owned by one stable thread.

### Beard Guard integration

`beard_guard.py` creates one controller with the current screen-shake constants. `play_alarm()` calls `controller.trigger()` instead of creating a new thread. The application cleanup path calls `controller.shutdown()` before final OpenCV teardown.

Extracting the controller into a separate module prevents importing it from opening the camera or entering Beard Guard's top-level processing loop. This gives the real GUI resource lifecycle a testable seam.

## Error handling

A visual-warning failure must never stop camera detection or alarm audio.

The worker reports initialization and animation failures to standard error with actionable context. It then destroys any Tk objects it successfully created and closes that worker lifecycle.

The controller permits one clean worker reinitialization on a later trigger. If initialization fails a second time, screen shake is disabled for the remainder of the process to prevent a restart or connection leak loop. Alarm audio and the OpenCV warning continue normally.

`shutdown()` remains safe after partial initialization, worker failure, or repeated calls.

## Verification

Add a real Tkinter integration test that runs under Xvfb and exercises the production controller:

1. Record the process socket count before controller initialization.
2. Trigger one short animation and wait until the worker is idle.
3. Record the stable socket count after initialization.
4. Trigger at least 100 additional short animations, including concurrent trigger calls.
5. Assert that exactly one worker exists and the socket count does not grow beyond the stable post-initialization count.
6. Call `shutdown()` and assert that the worker terminates.

Additional unit-level checks cover trigger coalescing, idempotent shutdown, and the one-retry failure policy.

The final manual regression check runs Beard Guard, triggers repeated warnings, confirms that its XWayland client count remains constant, and verifies that PrintScreen still opens Flameshot.

## Acceptance criteria

- The desktop-wide screen-shake effect remains available on all detected monitors.
- Repeated alarms reuse one Tk root and one worker thread.
- Beard Guard's X11/XWayland connection count remains bounded across at least 100 screen-shake cycles.
- Concurrent triggers cannot create additional Tk roots or worker threads.
- Worker failures cannot stop alarm audio or camera detection.
- Shutdown cleans up the screen-shake worker without hanging.
- Flameshot can still open after the stress test.

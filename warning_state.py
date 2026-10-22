"""Temporal hysteresis for Beard Guard's disruptive desktop warning."""

from __future__ import annotations


class WarningDebouncer:
    """Require sustained state changes while tolerating landmark jitter."""

    def __init__(self, *, activation_duration: float, clear_duration: float):
        if activation_duration < 0 or clear_duration < 0:
            raise ValueError("warning debounce durations must not be negative")
        self.activation_duration = activation_duration
        self.clear_duration = clear_duration
        self.active = False
        self._unsafe_since: float | None = None
        self._safe_since: float | None = None

    def update(self, unsafe: bool, *, now: float) -> bool:
        """Return the latched warning state for the latest observation."""
        if unsafe:
            self._safe_since = None
            if self.active:
                return True
            if self._unsafe_since is None:
                self._unsafe_since = now
            if now - self._unsafe_since >= self.activation_duration:
                self.active = True
                self._unsafe_since = None
            return self.active

        self._unsafe_since = None
        if not self.active:
            self._safe_since = None
            return False
        if self._safe_since is None:
            self._safe_since = now
        if now - self._safe_since >= self.clear_duration:
            self.active = False
            self._safe_since = None
        return self.active

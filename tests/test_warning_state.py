import unittest

from warning_state import WarningDebouncer


class WarningDebouncerTests(unittest.TestCase):
    def test_transient_unsafe_detection_does_not_activate_warning(self):
        state = WarningDebouncer(activation_duration=0.25, clear_duration=0.5)

        self.assertFalse(state.update(True, now=10.0))
        self.assertFalse(state.update(False, now=10.1))
        self.assertFalse(state.update(True, now=10.2))

    def test_sustained_unsafe_detection_activates_warning(self):
        state = WarningDebouncer(activation_duration=0.25, clear_duration=0.5)

        self.assertFalse(state.update(True, now=10.0))
        self.assertFalse(state.update(True, now=10.2))
        self.assertTrue(state.update(True, now=10.25))

    def test_brief_tracking_loss_does_not_clear_active_warning(self):
        state = WarningDebouncer(activation_duration=0.25, clear_duration=0.5)
        state.update(True, now=10.0)
        self.assertTrue(state.update(True, now=10.25))

        self.assertTrue(state.update(False, now=10.3))
        self.assertTrue(state.update(True, now=10.4))
        self.assertTrue(state.update(False, now=10.5))

    def test_sustained_safe_detection_clears_warning(self):
        state = WarningDebouncer(activation_duration=0.25, clear_duration=0.5)
        state.update(True, now=10.0)
        self.assertTrue(state.update(True, now=10.25))

        self.assertTrue(state.update(False, now=10.3))
        self.assertTrue(state.update(False, now=10.79))
        self.assertFalse(state.update(False, now=10.8))


if __name__ == "__main__":
    unittest.main()

"""Replay camera results through the actual app loop without a camera or GUI."""

import os
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "beard_guard.py"


def face_result(present=True):
    points = [types.SimpleNamespace(x=0.5, y=0.5) for _ in range(455)]
    points[152].y = 0.7  # chin
    points[1].y = 0.5  # nose
    points[168].y = 0.4  # eyes
    points[234].x = 0.3
    points[454].x = 0.7
    return types.SimpleNamespace(
        multi_face_landmarks=[types.SimpleNamespace(landmark=points)] if present else None
    )


def hand_result(state):
    points = [types.SimpleNamespace(x=0.5, y=0.5) for _ in range(21)]
    points[8].x = 0.4
    points[8].y = {
        "near": 0.45,
        "lower_beard": 0.82,
    }.get(state, 0.95)
    return types.SimpleNamespace(
        multi_hand_landmarks=[types.SimpleNamespace(landmark=points)] if state != "missing" else None
    )


class BeardGuardBlackoutTests(unittest.TestCase):
    def replay(self, states, times, faces=None, read_error=None, keys=None):
        cv2 = mock.MagicMock()
        cv2.COLOR_BGR2RGB = 1
        cv2.WND_PROP_VISIBLE = 2
        cv2.waitKey.return_value = -1
        if keys is not None:
            cv2.waitKey.side_effect = keys
        cv2.getWindowProperty.return_value = 1
        cv2.flip.side_effect = lambda frame, _: frame
        cv2.cvtColor.side_effect = lambda frame, _: frame
        capture_frame = types.SimpleNamespace(shape=(480, 640, 3))
        cv2.resize.return_value = capture_frame
        cap = mock.MagicMock()
        cap.isOpened.return_value = True
        cap.read.side_effect = [(True, capture_frame)] * len(states) + [read_error or (False, None)]
        camera_capture = types.SimpleNamespace(open_linux_camera=mock.Mock(return_value=cap))
        mp = mock.MagicMock()
        mp.solutions.face_mesh.FaceMesh.return_value.process.side_effect = [
            face_result(present) for present in (faces or [True] * len(states))
        ]
        mp.solutions.hands.Hands.return_value.process.side_effect = [hand_result(s) for s in states]
        controller = mock.MagicMock()
        module = types.SimpleNamespace(ScreenBlackoutController=mock.Mock(return_value=controller))
        with mock.patch.dict(
            sys.modules,
            {
                "cv2": cv2,
                "mediapipe": mp,
                "camera_capture": camera_capture,
                "screen_blackout": module,
            },
        ), \
             mock.patch.dict(os.environ, {"DISPLAY": ""}), \
             mock.patch("time.monotonic", side_effect=times), \
             mock.patch("pathlib.Path.glob", return_value=[]):
            if read_error:
                with self.assertRaisesRegex(RuntimeError, "camera failed"):
                    runpy.run_path(str(APP), run_name="__main__")
            else:
                runpy.run_path(str(APP), run_name="__main__")
        self.assertEqual(controller.shutdown.call_count, 1)
        camera_capture.open_linux_camera.assert_called_once_with(
            camera_index=0,
            width=352,
            height=288,
            fps=30,
            prefer_ir=False,
            primary_input_format="yuyv422",
            allow_ir_fallback=False,
        )
        cv2.resize.assert_not_called()
        cap.release.assert_called_once()
        cv2.destroyWindow.assert_not_called()
        cv2.namedWindow.assert_not_called()
        cv2.setWindowProperty.assert_not_called()
        if keys and 27 in keys and states[keys.index(27)] == "near":
            controller.dismiss.assert_called_once()
        return [call.args[0] for call in controller.set_active.call_args_list]

    def test_blackout_requires_sustained_too_close_detection(self):
        self.assertEqual(
            self.replay(["near", "near", "near"], [10, 10.2, 10.25]),
            [False, False, True],
        )

    def test_lower_beard_position_triggers_blackout(self):
        self.assertEqual(
            self.replay(
                ["lower_beard", "lower_beard", "lower_beard"],
                [10, 10.2, 10.25],
            ),
            [False, False, True],
        )

    def test_blackout_releases_after_hand_stays_away_for_grace_period(self):
        self.assertEqual(
            self.replay(
                ["near", "near", "near", "near", "far", "far"],
                [10, 10.1, 10.2, 10.3, 10.4, 11.0],
            ),
            [False, False, False, True, True, False],
        )

    def test_single_missing_hand_detection_does_not_flicker_blackout(self):
        self.assertEqual(
            self.replay(["near", "near", "missing", "near", "missing"], [10, 10.25, 10.3, 10.4, 10.5]),
            [False, True, True, True, True],
        )

    def test_blackout_releases_after_face_stays_missing_for_grace_period(self):
        self.assertEqual(
            self.replay(
                ["near"] * 4,
                [10, 10.25, 10.3, 10.8],
                faces=[True, True, False, False],
            ),
            [False, True, True, False],
        )

    def test_escape_in_preview_dismisses_active_warning_without_exiting(self):
        self.assertEqual(
            self.replay(["near"] * 3, [10, 10.25, 10.3], keys=[-1, 27, -1]),
            [False, True, True],
        )

    def test_escape_in_safe_preview_still_exits_the_app(self):
        self.assertEqual(
            self.replay(["far", "far"], [10, 10.01], keys=[27, -1]),
            [False],
        )

    def test_camera_error_cleans_up_active_blackout(self):
        self.assertEqual(
            self.replay(["near", "near"], [10, 10.6], read_error=RuntimeError("camera failed")),
            [False, True],
        )


if __name__ == "__main__":
    unittest.main()

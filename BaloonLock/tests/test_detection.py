"""Task 5: detection robustness - exposure lock, camera selection, presets,
red wrap-around, laser-dot rejection, and the HOLDING timeout."""

import unittest
from unittest import mock

import cv2
import numpy as np

from tests.fakes import patch_all, FRAME_DT, box_at, run_tracker, tt
import balloon_detect as bd
import camera


def hsv_to_bgr(h, s, v):
    return tuple(int(c) for c in cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0])


def detect_with_preset(img, name):
    presets = bd.load_presets()
    hsv = cv2.cvtColor(cv2.GaussianBlur(img, (bd.BLUR_SIZE, bd.BLUR_SIZE), 0), cv2.COLOR_BGR2HSV)
    p = presets[name]
    blob, area = bd.find_largest_blob(bd.make_mask(hsv, p), p["min_area"], hsv)
    if blob is None:
        return None
    x, y, w, h = cv2.boundingRect(blob)
    return x + w // 2, y + h // 2


class ExposureLock(unittest.TestCase):

    def test_locked_by_default_and_restored_on_exit(self):
        r = run_tracker(["--dry-run"], lambda i: box_at(322, 241, 30), frames=5)
        sets = r.cap.history
        self.assertIn((cv2.CAP_PROP_AUTO_EXPOSURE, camera.AUTO_EXPOSURE_MANUAL), sets)
        self.assertIn((cv2.CAP_PROP_AUTO_WB, 0), sets)
        # ...and switched back ON at the end, so the webcam isn't left stuck
        self.assertEqual(r.cap.props[cv2.CAP_PROP_AUTO_EXPOSURE], camera.AUTO_EXPOSURE_AUTO)
        self.assertEqual(r.cap.props[cv2.CAP_PROP_AUTO_WB], 1)
        self.assertIn("auto-exposure OFF", r.out)

    def test_auto_exposure_flag_leaves_camera_alone(self):
        r = run_tracker(["--dry-run", "--auto-exposure"], lambda i: box_at(322, 241, 30), frames=5)
        touched = {prop for prop, _ in r.cap.history}
        self.assertNotIn(cv2.CAP_PROP_AUTO_EXPOSURE, touched)
        self.assertNotIn(cv2.CAP_PROP_AUTO_WB, touched)


class CameraSelection(unittest.TestCase):

    def test_camera_flag(self):
        r = run_tracker(["--dry-run", "--camera", "2"], None, frames=2)
        self.assertEqual(r.camera_index, 2)

    def test_list_cameras_finds_working_ones(self):
        class Cam:
            def __init__(self, ok):
                self.ok = ok

            def read(self):
                return (True, np.zeros((720, 1280, 3), np.uint8)) if self.ok else (False, None)

            def release(self):
                pass
        fake = {0: Cam(True), 1: None, 2: Cam(True), 3: Cam(False)}
        with mock.patch.object(camera, "open_camera", lambda i: fake.get(i)):
            self.assertEqual(camera.list_cameras(4), [(0, 1280, 720), (2, 1280, 720)])

    def test_list_cameras_command_quits_without_opening_camera(self):
        with patch_all("print_camera_list", lambda: print("LISTED")):
            r = run_tracker(["--dry-run", "--list-cameras"], None, frames=2)
        self.assertIn("LISTED", r.out)
        self.assertIsNone(r.camera_index)


class ColorDetection(unittest.TestCase):

    def test_presets_file_has_the_colors(self):
        self.assertTrue({"red", "orange", "yellow", "green", "blue"} <= set(bd.load_presets()))

    def test_red_wraps_around_the_hue_circle(self):
        for hue in (175, 3):                     # both ends of OpenCV's 0-179 hue range
            with self.subTest(hue=hue):
                img = np.full((480, 640, 3), 90, np.uint8)
                cv2.circle(img, (400, 200), 50, hsv_to_bgr(hue, 200, 180), -1)
                self.assertEqual(detect_with_preset(img, "red"), (400, 200))
                self.assertIsNone(detect_with_preset(img, "green"))

    def test_laser_dot_is_ignored(self):
        dot = hsv_to_bgr(0, 190, 255)            # small and VERY bright
        img = np.full((480, 640, 3), 90, np.uint8)
        cv2.circle(img, (200, 200), 17, dot, -1)
        self.assertIsNone(detect_with_preset(img, "red"), "a lone laser dot isn't a balloon")
        cv2.circle(img, (200, 200), 20, dot, -1)                       # dot bigger than...
        cv2.circle(img, (450, 300), 17, hsv_to_bgr(0, 200, 170), -1)    # ...a small balloon
        self.assertEqual(detect_with_preset(img, "red"), (450, 300))


class Holding(unittest.TestCase):

    def test_holds_half_a_second_then_lost(self):
        r = run_tracker(["--dry-run"], lambda i: None if i >= 20 else box_at(322, 241, 30),
                        frames=50)
        hold_frames = int(tt.LOST_TIMEOUT_S / FRAME_DT)              # 15 frames
        self.assertIn("HOLDING", r.lines_at(20 + hold_frames - 2))
        self.assertIn("LOST", r.lines_at(20 + hold_frames + 2))


if __name__ == "__main__":
    unittest.main()

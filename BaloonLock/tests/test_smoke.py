"""
Smoke tests: run the whole dry-run pipeline headless (no webcam, no windows,
no serial) and make sure nothing crashes and the basics work end to end.
"""

import os
import unittest

import cv2
import numpy as np

from tests.fakes import REPO, run_tracker, tt


def red_balloon_frame(i):
    """Synthetic camera frame: a red balloon drifting slowly to the right."""
    img = np.full((480, 640, 3), 90, np.uint8)
    cv2.circle(img, (250 + i, 240), 45, (30, 30, 210), -1)
    return img


class DryRunSmoke(unittest.TestCase):

    def test_color_pipeline_tracks_and_locks(self):
        r = run_tracker(["--dry-run", "--color", "red"],
                        frames=90, frame_fn=red_balloon_frame)
        self.assertIn("would send:", r.out)
        self.assertTrue(r.laser_on_frames(), "should lock and (in AUTO, dry-run) fire")
        self.assertIn("Recentering to 90,90", r.out)

    def test_capture_thread_mode_starts_and_stops(self):
        # Threaded capture with a fake camera: just must not crash or hang.
        r = run_tracker(["--dry-run", "--color", "red"], frames=30, frame_fn=red_balloon_frame,
                        threaded=True)
        self.assertIn("Capture thread: ON", r.out)
        self.assertIn("Recentering to 90,90", r.out)

    @unittest.skipUnless(os.path.exists(os.path.join(REPO, "yolov8n-oiv7.pt")),
                         "AI model file not downloaded")
    def test_real_ai_model_loads_and_runs(self):
        detect = tt.make_ai_detector()
        found = detect(np.zeros((480, 640, 3), np.uint8))
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()

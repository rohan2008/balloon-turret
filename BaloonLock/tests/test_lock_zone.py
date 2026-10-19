"""Task 4: lock zone that scales with the balloon's apparent size, distance calibration."""

import contextlib
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np

from tests.fakes import patch_all, box_at, run_tracker, tt


def zone_numbers(text):
    """Pull 'lock zone 15.0px (exit 27.0)' out of the info-bar text."""
    m = re.search(r"lock zone ([\d.]+)px \(exit ([\d.]+)\)", text)
    return (float(m.group(1)), float(m.group(2))) if m else None


class ZoneSize(unittest.TestCase):

    def zone_for(self, radius, extra=()):
        r = run_tracker(["--dry-run", *extra], lambda i: box_at(322, 241, radius), frames=8)
        return zone_numbers(r.lines_at(7))

    def test_zone_is_fraction_of_radius_with_hysteresis(self):
        self.assertEqual(self.zone_for(30), (15.0, 27.0))           # 0.5 x 30, exit x1.8

    def test_zone_clamped_to_min_and_max(self):
        self.assertEqual(self.zone_for(200)[0], 60.0)               # 0.5 x 200 = 100 -> max 60
        self.assertEqual(self.zone_for(10)[0], 6.0)                 # 0.5 x 10 = 5 -> min 6

    def test_flags_change_fraction_and_limits(self):
        self.assertEqual(self.zone_for(30, ["--lock-fraction", "0.2"])[0], 6.0)
        self.assertEqual(self.zone_for(200, ["--lock-max", "80"])[0], 80.0)
        self.assertEqual(self.zone_for(10, ["--lock-min", "3"])[0], 5.0)

    def test_radius_is_smoothed(self):
        # Detector jitters 28 <-> 32 px: the shown radius should sit near 30
        r = run_tracker(["--dry-run"], lambda i: box_at(322, 241, 28 if i % 2 else 32), frames=12)
        shown = float(re.search(r"apparent radius ([\d.]+)px", r.lines_at(11)).group(1))
        self.assertAlmostEqual(shown, 30, delta=1)

    def test_color_mode_uses_min_enclosing_circle(self):
        def frame(i):
            img = np.full((480, 640, 3), 90, np.uint8)
            cv2.circle(img, (322, 241), 40, (30, 30, 210), -1)
            return img
        r = run_tracker(["--dry-run", "--color", "red"], frames=8, frame_fn=frame)
        shown = float(re.search(r"apparent radius ([\d.]+)px", r.lines_at(7)).group(1))
        self.assertAlmostEqual(shown, 40, delta=2)


class LockBehaviour(unittest.TestCase):

    def test_five_frames_and_hysteresis_with_dynamic_zone(self):
        # radius 50 -> enter 25, exit 45. Aim nearly frozen (tiny gain, no prediction).
        miss = [10] * 8 + [35] * 6 + [50] * 3 + [35] * 6 + [10] * 7
        r = run_tracker(["--dry-run", "--no-predict", "--gain", "0.01"],
                        lambda i: box_at(320 + miss[i - 1], 240, 50), frames=len(miss))
        locked = ["L" if "LOCKED" in r.lines_at(i) else "." for i in range(1, len(miss) + 1)]
        self.assertEqual("".join(locked), "....LLLLLLLLLL" + "." * 13 + "LLL")

    def test_deadzone_shrinks_so_small_zones_can_lock(self):
        # radius 14 -> zone 7 px. Balloon 9 px off on each axis: the old fixed 10 px
        # deadzone would never move the aim, and a 12.7 px miss could never lock.
        r = run_tracker(["--dry-run", "--no-predict"], lambda i: box_at(329, 249, 14), frames=40)
        self.assertIn("LOCKED", r.lines_at(39))

    def test_zone_colors(self):
        self.assertEqual(tt.lock_zone_state(True, True, False, 30, 15), "locked")
        self.assertEqual(tt.lock_zone_state(False, True, False, 10, 15), "inside")
        self.assertEqual(tt.lock_zone_state(False, True, False, 20, 15), "outside")
        self.assertEqual(tt.lock_zone_state(False, False, False, None, 15), "outside")
        self.assertEqual(tt.lock_zone_state(False, True, True, 1, 15), "outside")   # too far


class DistanceCalibration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config = os.path.join(self.tmp, "turret_config.json")
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch_all("CONFIG_FILE", self.config))

    def tearDown(self):
        self.stack.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_uncalibrated_then_calibrate_then_measure(self):
        r = run_tracker(["--dry-run"], lambda i: box_at(322, 241, 30), frames=5)
        self.assertIn("uncalibrated", r.lines_at(4))

        # Hold the balloon at 150 cm (radius 30 px) and press c
        r = run_tracker(["--dry-run", "--calibrate-distance", "150"],
                        lambda i: box_at(322, 241, 30), frames=20, keys={10: ord("c")})
        self.assertIn("Calibration saved", r.out)
        self.assertEqual(r.laser_on_frames(), set(), "laser must stay off while calibrating")
        with open(self.config, encoding="utf-8") as f:
            saved = json.load(f)["distance_calibration"]["ai"]
        self.assertAlmostEqual(saved["k"], 150 * 30, delta=1)

        # Balloon now looks 1.5x bigger -> 1.5x closer = 100 cm
        r = run_tracker(["--dry-run"], lambda i: box_at(322, 241, 45), frames=8)
        self.assertIn("distance ~100 cm", r.lines_at(7))

    def test_calibration_needs_a_steady_balloon(self):
        r = run_tracker(["--dry-run", "--calibrate-distance", "150"],
                        lambda i: box_at(322, 241, 30), frames=6, keys={2: ord("c")})
        self.assertIn("no steady balloon yet", r.out)
        self.assertFalse(os.path.exists(self.config))


class ScaleGain(unittest.TestCase):

    def gain_line(self, radius):
        r = run_tracker(["--dry-run", "--scale-gain"], lambda i: box_at(322, 241, radius), frames=6)
        return re.search(r"GAIN [\d.]+ -> ([\d.]+)", r.lines_at(5)).group(1)

    def test_off_by_default(self):
        r = run_tracker(["--dry-run"], lambda i: box_at(322, 241, 20), frames=4)
        self.assertNotIn("->", re.search(r"GAIN[^|]*", r.lines_at(3)).group(0))

    def test_scaled_and_clamped(self):
        self.assertEqual(self.gain_line(40), "0.20")     # reference radius: unchanged
        self.assertEqual(self.gain_line(20), "0.40")     # half size -> 2x
        self.assertEqual(self.gain_line(10), "0.40")     # would be 4x -> clamped to 2x
        self.assertEqual(self.gain_line(160), "0.10")    # would be 0.25x -> clamped to 0.5x


if __name__ == "__main__":
    unittest.main()

"""Task 1: lag features - hybrid AI + color tracker, feedforward, timing, camera buffer."""

import math
import unittest

import cv2
import numpy as np

from tests.fakes import box_at, run_tracker, tt
import balloon_detect as bd


def scene(x, y, color_bgr, radius=40, background=90):
    img = np.full((480, 640, 3), background, np.uint8)
    cv2.circle(img, (int(x), int(y)), radius, color_bgr, -1)
    return img


class HueAverage(unittest.TestCase):

    def test_red_wraps_around(self):
        self.assertIn(bd.circular_mean_hue([178, 2, 179, 1]), (0, 179))
        self.assertEqual(bd.circular_mean_hue([60, 62, 58]), 60)


class HybridDetector(unittest.TestCase):

    def run_hybrid(self, color, every=3, frames=30, hide_on=()):
        """Moving balloon; fake AI knows the truth. Returns (results, ai_calls)."""
        calls = []
        truth = {}

        def fake_ai(frame):
            calls.append(len(calls))
            x, y = truth["xy"]
            return box_at(x, y, 40)

        detect = tt.make_hybrid_detector(fake_ai, every)
        results = []
        for i in range(frames):
            x, y = 200 + 4 * i, 240 + i
            truth["xy"] = (x, y)
            frame = scene(x, y, color) if i not in hide_on else np.full((480, 640, 3), 90, np.uint8)
            found = detect(frame)
            results.append((i, detect.last_source, found, (x, y)))
        return results, len(calls)

    def test_ai_runs_every_third_frame_and_tracker_follows(self):
        results, ai_calls = self.run_hybrid((30, 30, 210))           # red balloon
        self.assertEqual(ai_calls, 10)                                # 30 frames / 3
        tracker = [r for r in results if r[1] == "color tracker"]
        self.assertEqual(len(tracker), 20)
        for i, _src, found, (x, y) in tracker:
            self.assertIsNotNone(found, f"tracker lost the balloon on frame {i}")
            self.assertLess(math.hypot(found[0] - x, found[1] - y), 3)
            self.assertAlmostEqual(found[3], 40, delta=3)            # radius

    def test_dark_balloon_tracked_by_brightness(self):
        results, _ = self.run_hybrid((20, 20, 20))                    # black balloon
        tracker = [r for r in results if r[1] == "color tracker"]
        self.assertTrue(all(r[2] is not None for r in tracker))

    def test_lost_balloon_hands_back_to_ai(self):
        results, _ = self.run_hybrid((30, 30, 210), hide_on={4})
        self.assertEqual(results[4][1], "color tracker")
        self.assertIsNone(results[4][2])
        self.assertEqual(results[5][1], "AI", "after a miss the next frame must use the AI")

    def test_every_1_means_ai_every_frame(self):
        _, ai_calls = self.run_hybrid((30, 30, 210), every=1, frames=12)
        self.assertEqual(ai_calls, 12)

    def test_full_pipeline_with_ai_every(self):
        def frame(i):
            return scene(325, 243, (30, 30, 210), radius=30)
        r = run_tracker(["--dry-run", "--ai-every", "3"], lambda i: box_at(325, 243, 30),
                        frames=40, frame_fn=frame)
        self.assertIn("color tracker", " ".join(f["text"] for f in r.frames))
        self.assertTrue(set(range(10, 40)) <= r.laser_on_frames(),
                        "lock should hold across tracker frames")


class ControlAndTiming(unittest.TestCase):

    def test_feedforward_converges_on_still_balloon(self):
        r = run_tracker(["--dry-run"], lambda i: box_at(500, 120, 40), frames=80)
        before_quit = r.out[:r.out.index("Recentering")]          # ignore the recenter glide
        sends = [line for line in before_quit.splitlines() if "would send:" in line and "," in line]
        pan, tilt = map(int, sends[-1].split()[-1].split(","))
        self.assertAlmostEqual(pan, 90 + 180 / (640 / 60), delta=2)
        self.assertAlmostEqual(tilt, 90 + 120 / (640 / 60), delta=2)

    def test_camera_buffer_size_requested(self):
        r = run_tracker(["--dry-run"], None, frames=3)
        self.assertIn("Camera buffer size 1", r.out)

    def test_timing_report_on_t(self):
        r = run_tracker(["--dry-run"], lambda i: box_at(325, 243, 30), frames=20,
                        keys={10: ord("t")})
        self.assertIn("----- Timing", r.out)
        for stage in ("capture wait", "detect", "control", "draw+show"):
            self.assertIn(stage, r.out)

    def test_default_servo_speed(self):
        self.assertEqual(tt.DEFAULT_SERVO_SPEED, 12.0)


if __name__ == "__main__":
    unittest.main()

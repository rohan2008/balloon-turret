"""Auto-tune branch: motion filter, latency-based lead, blur handling, dropouts,
hidden sliders, and proof that tuning can never loosen a safety rule."""

import inspect
import json
import math
import os
import re
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np

from tests.fakes import FakeSerial, REPO, box_at, patch_all, run_tracker, tt
import tests.fakes as fakes
import config
import control
import safety
import tests.test_laser_path as laser_path


def last_number(result, pattern):
    """The number captured by `pattern` on the last frame whose HUD matches it."""
    for frame in reversed(result.frames):
        m = re.search(pattern, frame["text"])
        if m:
            return float(m.group(1))
    raise AssertionError(f"{pattern!r} never shown on the HUD")


def write_tuning(values):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"parameters": values}, f)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# 1. Motion filter
# ---------------------------------------------------------------------------
class AccelerationFilter(unittest.TestCase):

    def prediction_error(self, mode, ahead_s=0.15, fps=30):
        """Feed a noisy free fall; average error predicting `ahead_s` into the future."""
        rng = np.random.default_rng(0)
        g = 9.81 * 277                                  # gravity in px/s^2 (2 m away)
        truth = lambda t: (320 + 40 * t, 40 + 0.5 * g * t * t)
        tracker = control.MotionTracker(dict(control.DEFAULT_TUNING), mode=mode)
        errors = []
        for k in range(int(0.6 * fps)):
            t = k / fps
            x, y = truth(t)
            tracker.update(t, (x + rng.normal(0, 1.5), y + rng.normal(0, 1.5)), 3.0)
            if t >= 0.2:                                # after it has settled
                px, py = tracker.predict_at(t + ahead_s)
                errors.append(math.dist((px, py), truth(t + ahead_s)))
        return float(np.mean(errors))

    def test_acceleration_model_beats_constant_velocity_on_free_fall(self):
        cv = self.prediction_error("cv")
        ca = self.prediction_error("ca")
        imm = self.prediction_error("imm")              # what the tracker actually uses
        self.assertLess(ca, cv * 0.5, f"CA {ca:.1f} px vs CV {cv:.1f} px")
        self.assertLess(imm, cv * 0.5, f"IMM {imm:.1f} px vs CV {cv:.1f} px")

    def test_acceleration_is_clamped(self):
        tracker = control.MotionTracker(dict(control.DEFAULT_TUNING))
        for k, y in enumerate([100, 100, 100, 400, 50, 450]):      # wild jumps
            state = tracker.update(k / 30, (320, y), 3.0)
        self.assertLessEqual(abs(state["ay"]), control.MAX_ACCEL_PX_S2 + 1e-6)


# ---------------------------------------------------------------------------
# 2. Latency-based lead
# ---------------------------------------------------------------------------
class LatencyLead(unittest.TestCase):

    def lead_at(self, fps, processing_s, extra=()):
        speed = 120.0                                    # px per second, whatever the fps
        with mock.patch.object(fakes, "FRAME_DT", 1.0 / fps):
            r = run_tracker(["--dry-run", *extra],
                            detector=lambda i: box_at(200 + speed * i / fps, 240, 30),
                            frames=int(2.5 * fps), processing_s=processing_s)
        return (last_number(r, r"LEAD (\d+) ms \(auto"),
                last_number(r, r"latency (\d+) ms"))

    def test_lead_is_latency_plus_servo_delay_in_seconds(self):
        lead, latency = self.lead_at(30, 0.020)
        self.assertAlmostEqual(latency, 20, delta=3)
        self.assertAlmostEqual(lead, 20 + 80, delta=4)

    def test_lead_in_seconds_stays_right_when_fps_changes(self):
        """Old LEAD was in FRAMES: 3 frames = 100 ms at 30 fps but only 50 ms at
        60 fps. Now it's time-based: the same real latency gives the same lead,
        whatever the camera's frame rate."""
        lead30, _ = self.lead_at(30, 0.020)
        lead60, _ = self.lead_at(60, 0.020)
        self.assertAlmostEqual(lead30, lead60, delta=4)

    def test_lead_follows_slower_processing(self):
        fast, _ = self.lead_at(30, 0.010)
        slow, _ = self.lead_at(15, 0.050)               # slow laptop: 15 fps, 50 ms per frame
        self.assertAlmostEqual(slow - fast, 40, delta=6)

    def test_servo_delay_flag(self):
        lead, _ = self.lead_at(30, 0.020, ["--servo-delay", "150"])
        self.assertAlmostEqual(lead, 20 + 150, delta=4)

    def test_gain_cap_shrinks_as_delay_grows(self):
        caps = [control.stable_gain_cap(d) for d in (0, 1, 2, 4)]
        self.assertEqual(caps, sorted(caps, reverse=True))
        tuning = dict(control.DEFAULT_TUNING, gain_max=0.8)
        self.assertLessEqual(control.auto_gain(1.0, tuning, 4), control.stable_gain_cap(4))


# ---------------------------------------------------------------------------
# 3. Motion blur
# ---------------------------------------------------------------------------
def streak_frame(x1, x2, y=240, radius=25, color=(40, 40, 210), background=110, steps=16):
    """A red ball smeared from x1 to x2 during the exposure (like a fast throw)."""
    acc = np.zeros((480, 640, 3), np.float32)
    for x in np.linspace(x1, x2, steps):
        layer = np.full((480, 640, 3), background, np.uint8)
        cv2.circle(layer, (int(round(x)), y), radius, color, -1, cv2.LINE_AA)
        acc += layer.astype(np.float32) / steps
    return cv2.convertScaleAbs(acc)


class BlurHandling(unittest.TestCase):

    def test_blurred_blob_is_still_detected_with_prediction(self):
        frame = streak_frame(260, 380)                  # 120 px streak, center (320, 240)
        detect = tt.make_color_detector("red")
        hint = {"predicted": (322.0, 238.0), "vx": 3600.0, "vy": 0.0, "speed": 3600.0,
                "radius": 25.0, "frame_dt": 1 / 30}
        self.assertIsNone(detect(frame, None))          # the plain search misses this streak...
        found = detect(frame, hint)                     # ...the prediction-guided search finds it
        self.assertIsNotNone(found, "blurred balloon missed")
        x, y, _box, radius = found
        self.assertLess(math.dist((x, y), (320, 240)), 8, "should aim at the streak's CENTER")
        self.assertLess(radius, 30, "the long streak must not inflate the size")

    def test_relaxed_thresholds_never_go_below_the_floors(self):
        settings = {"h_min": 170, "h_max": 10, "s_min": 60, "s_max": 255, "v_min": 50,
                    "v_max": 255, "min_area": 300}
        extreme = {k: hi for k, (lo, hi) in control.TUNING_RANGES.items()}
        relaxed = tt.relaxed_settings(settings, 1.0, extreme)
        self.assertGreaterEqual(relaxed["s_min"], min(settings["s_min"], control.RELAX_FLOOR_S))
        self.assertGreaterEqual(relaxed["v_min"], min(settings["v_min"], control.RELAX_FLOOR_V))


# ---------------------------------------------------------------------------
# 4. Dropouts: keep aiming, laser OFF
# ---------------------------------------------------------------------------
class Dropouts(unittest.TestCase):

    def run_live(self, detector, frames=70):
        """Live run with the laser allowed. Also records what the main loop told
        laser_should_fire() each time (result.decisions)."""
        decisions = []
        real_decide = tt.laser_should_fire

        def spy(*a, **k):
            decisions.append(k)
            return real_decide(*a, **k)
        with patch_all("LASER_ENABLED", True), patch_all("laser_should_fire", spy):
            result = run_tracker(["--port", "COM9"], detector=detector, frames=frames)
        result.decisions = decisions
        return result

    def test_laser_off_during_dropout_but_turret_keeps_aiming(self):
        missing = range(35, 45)                          # 10 frames = 0.33 s (< 0.5 s)
        det = lambda i: None if i in missing else box_at(320 + 0.5 * i, 240, 30)
        r = self.run_live(det)
        on = r.laser_on_frames()
        self.assertTrue(on & set(range(20, 35)), "should have locked and fired before the dropout")
        self.assertFalse(on & set(missing), "laser must be OFF without a real detection")
        # a PREDICTED position is never reported as a real detection (2 decisions per frame)
        no_detection = [k for k in r.decisions if not k["real_detection"]]
        self.assertGreaterEqual(len(no_detection), 2 * len(missing))
        holding = [f for f in r.frames if f["i"] in missing]
        self.assertTrue(all("HOLDING" in f["text"] for f in holding))
        self.assertTrue(any("predicting" in f["text"] for f in holding))

    def test_far_blob_during_dropout_does_not_fire(self):
        """A different red thing far from the prediction is ignored (gating) -
        and it certainly doesn't fire the laser."""
        far = range(35, 41)
        det = lambda i: box_at(560, 60, 30) if i in far else box_at(320, 240, 30)
        r = self.run_live(det)
        self.assertFalse(r.laser_on_frames() & set(far))
        self.assertTrue(any("ignored a far blob" in f["text"] for f in r.frames if f["i"] in far))


# ---------------------------------------------------------------------------
# 5. Sliders only matter with --manual-tuning
# ---------------------------------------------------------------------------
class Sliders(unittest.TestCase):

    def run_with_sliders(self, gain_pos, lead_ms, extra=()):
        positions = {tt.GAIN_TRACKBAR: gain_pos, tt.LEAD_TRACKBAR: lead_ms,
                     tt.LOCK_TRACKBAR: int(round(tt.DEFAULT_LOCK_FRACTION * 100))}
        created = []
        r = run_tracker(["--port", "COM9", *extra],
                        detector=lambda i: box_at(200 + 3 * i, 200 + 2 * i, 30), frames=50,
                        extra_patches=[
                            mock.patch.object(cv2, "getTrackbarPos", lambda n, w: positions[n]),
                            mock.patch.object(cv2, "createTrackbar",
                                              lambda name, *a: created.append(name))])
        angles = [l for l in r.serial_lines if l[:1].isdigit()]
        return angles, created

    def test_sliders_ignored_and_hidden_without_manual_tuning(self):
        a, created = self.run_with_sliders(5, 0)
        b, _ = self.run_with_sliders(90, 400)
        self.assertEqual(a, b, "GAIN/LEAD sliders changed the behavior without --manual-tuning")
        self.assertNotIn(tt.GAIN_TRACKBAR, created)
        self.assertNotIn(tt.LEAD_TRACKBAR, created)

    def test_sliders_work_with_manual_tuning(self):
        a, created = self.run_with_sliders(5, 0, ["--manual-tuning"])
        b, _ = self.run_with_sliders(90, 400, ["--manual-tuning"])
        self.assertNotEqual(a, b)
        self.assertIn(tt.GAIN_TRACKBAR, created)
        self.assertIn(tt.LEAD_TRACKBAR, created)


# ---------------------------------------------------------------------------
# 6. Tuning can never disable or loosen a safety rule
# ---------------------------------------------------------------------------
SAFETY_NAMES = ["LASER_ENABLED", "LOCK_FRAMES", "LOST_TIMEOUT_S", "laser_should_fire",
                "LOCK_ENTER", "lock_enter", "lock_exit", "min_radius", "MIN_LOCK_RADIUS_PX",
                "WATCHDOG_TIMEOUT_S", "laser_disabled", "first_run", "real_detection",
                "focused", "too_far", "link_ok", "button_held", "mode"]


class TuningCannotLoosenSafety(unittest.TestCase):

    def test_tunable_names_are_not_safety_settings(self):
        safety_words = set(vars(safety)) | set(inspect.signature(safety.laser_should_fire).parameters)
        overlap = set(control.TUNING_RANGES) & (safety_words | set(SAFETY_NAMES))
        self.assertEqual(overlap, set())
        source = inspect.getsource(safety)
        self.assertNotIn("TUNING", source, "safety.py must not depend on tuning values")

    def test_file_cannot_smuggle_in_safety_settings(self):
        path = write_tuning({**{name: True for name in SAFETY_NAMES},
                             "LOCK_FRAMES": 0, "gain_max": 50.0, "imm_stay": "yes",
                             "cv_accel_noise": 200.0})
        try:
            values, warnings = config.load_auto_tune(path)
            self.assertEqual(values, {"cv_accel_noise": 200.0})     # only the valid one
            self.assertEqual(len(warnings), len(SAFETY_NAMES) + 2)
            r = run_tracker(["--dry-run", "--tuning-file", path], detector=lambda i: box_at(320, 240, 30),
                            frames=10)
            self.assertIn("ignored 'LASER_ENABLED'", r.out)
            self.assertIs(safety.LASER_ENABLED, False)
            self.assertIs(tt.LASER_ENABLED, False)
            self.assertNotIn("L1", r.serial_lines)
        finally:
            os.unlink(path)

    def test_extreme_tuning_still_obeys_every_rule(self):
        """At every corner of the allowed ranges: LASER_ENABLED False means no L1
        ever, and with the laser allowed it only fires on a real detection, after
        LOCK_FRAMES locked frames, and goes off on a dropout or lost focus."""
        corners = [{k: lo for k, (lo, hi) in control.TUNING_RANGES.items()},
                   {k: hi for k, (lo, hi) in control.TUNING_RANGES.items()}]
        rng = np.random.default_rng(3)
        corners += [{k: float(rng.choice([lo, hi])) for k, (lo, hi) in control.TUNING_RANGES.items()}
                    for _ in range(3)]
        missing = set(range(30, 36))
        unfocused = set(range(45, 48))
        det = lambda i: None if i in missing else box_at(320 + 0.3 * i, 240, 30)
        for values in corners:
            path = write_tuning(values)
            try:
                argv = ["--port", "COM9", "--tuning-file", path]
                off = run_tracker(argv, detector=det, frames=60)
                self.assertNotIn("L1", off.serial_lines, "LASER_ENABLED False must win")
                with patch_all("LASER_ENABLED", True):
                    r = run_tracker(argv, detector=det, frames=60,
                                    focus_fn=lambda i: i not in unfocused)
                on = r.laser_on_frames()
                self.assertFalse(on & missing, f"fired without detection: {values}")
                self.assertFalse(on & unfocused, f"fired without focus: {values}")
                first = min(on) if on else None
                self.assertTrue(first is None or first >= tt.LOCK_FRAMES,
                                f"fired before {tt.LOCK_FRAMES} locked frames: {values}")
            finally:
                os.unlink(path)


class RuntimeProofWithTunedFile(laser_path.RuntimeProof):
    """The existing laser-path proof, re-run with the shipped config/auto_tune.json."""

    def run_checked(self, argv, **kwargs):
        return super().run_checked(list(argv) + ["--tuning-file", config.AUTO_TUNE_FILE], **kwargs)


if __name__ == "__main__":
    unittest.main()
